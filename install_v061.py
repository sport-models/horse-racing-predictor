"""Idempotent, narrowly scoped installer for v0.6.1 Streamlit integration.

Usage: python install_v061.py --check    (dry-run; validates anchor strings)
       python install_v061.py            (creates app.py.v061.bak once)
Never rewrites model scoring, bets, promotions, or settlement functions.
"""
import argparse
import ast
from pathlib import Path

SAVE_OLD = '''    if selected_date >= date.today():
        if st.button('🔒 Save & Lock All Race Predictions', type='primary'):
            try:
                batch = snapshot_race(
                    analysis['date'],
                    analysis['track'],
                    analysis['race_number'],
                    df,
                    analysis.get('bookmaker_quotes', {}),
                    analysis.get('odds_snapshot_at')
                )
                st.success(f'✅ {len(df)} predictions permanently saved to Supabase (batch {batch[:8]}). Open Bet Tracker to enter odds and stakes.')
            except Exception as e:
                st.error(f'Predictions were NOT confirmed saved: {e}')
'''
SAVE_NEW = '''    if selected_date >= date.today():
        if st.button('🔒 Save & Lock All Race Predictions', type='primary'):
            # v0.6.1: a fresh, uncached form snapshot generates the ACTUAL locked
            # rankings; cached display rankings are NEVER archived as new evidence.
            try:
                v061_prospective.ensure_new_race(
                    v061_prospective.archive_client(secret),
                    analysis['date'], analysis['track'], analysis['race_number'])
                prepared = v061_prospective.prepare_live_snapshot(
                    race_date=analysis['date'], track=analysis['track'],
                    track_slug=track_slug, race_number=analysis['race_number'],
                    formfav_key=api_key, puntersedge_key=secret('PUNTERSEDGE_API_KEY'),
                    get_items=get_items, get_value=get_value,
                    meeting_name=meeting_name, score_runner=score_runner,
                    recent_form_score=recent_form_score, win_rate=win_rate,
                    as_number=as_number, odds_find_race=odds_find_race,
                    odds_runner_quotes=odds_runner_quotes,
                    puntersedge_request=puntersedge_request)
            except Exception as evidence_error:
                st.error('New prospective lock BLOCKED: live evidence refresh failed: '
                         + str(evidence_error) + '. No predictions were saved.')
            else:
                try:
                    batch = snapshot_race(
                        analysis['date'], analysis['track'], analysis['race_number'],
                        prepared['rankings_df'], prepared['bookmaker_quotes'],
                        prepared['odds_snapshot_at'])
                except Exception as save_error:
                    st.error('Neither a new confirmed legacy prediction nor a v0.6.1 archive '
                             'is available; check Supabase before retrying: ' + str(save_error))
                else:
                    # Save the immutable evidence separately, and DO NOT pretend
                    # that two Supabase writes form a single database transaction.
                    try:
                        receipt = v061_prospective.archive_locked_batch(
                            v061_prospective.archive_client(secret), batch, prepared)
                    except Exception as archive_error:
                        st.error(f'IMPORTANT: legacy predictions batch {batch[:8]} WAS saved, '
                                 'but v0.6.1 independent evidence was NOT archived: '
                                 + str(archive_error) + '. This batch is UNVERIFIED. '
                                 'Check the migration and service-role credentials. '
                                 'Do not backfill this as a verified pre-race snapshot.')
                    else:
                        analysis['df'] = prepared['rankings_df']
                        analysis['race_data'] = prepared['payload']['form_response_raw']
                        st.success(f'✅ {len(prepared["rankings_df"])} freshly ranked runners saved '
                                   f'and archived in immutable v0.6.1 evidence (batch {batch[:8]}). '
                                   'Pre-scheduled only: official actual-off and source-as-of '
                                   'verification remain outstanding.')
'''
IMPORT_ANCHOR = 'import v060_validation_hub\n'
RENDER_ANCHOR = '    v060_validation_hub.render(db_rows)\n'
IMPORT = 'import v061_prospective\n'
RENDER = '    v061_prospective.render(secret)\n    st.divider()\n'


def patched(source):
    if IMPORT in source or RENDER in source or 'v061_prospective.prepare_live_snapshot(' in source:
        if all(x in source for x in (IMPORT, RENDER, 'v061_prospective.prepare_live_snapshot(')):
            ast.parse(source)
            return source, False
        raise RuntimeError('Partial v0.6.1 installation detected. Restore app.py from backup before retrying.')
    for title, anchor in [('v0.6.0 import', IMPORT_ANCHOR),
                          ('v0.6.0 render', RENDER_ANCHOR),
                          ('existing save block', SAVE_OLD)]:
        count = source.count(anchor)
        if count != 1:
            raise RuntimeError(f'Cannot patch safely: expected exactly one {title}, found {count}. '
                               'app.py was not modified.')
    original = source
    source = source.replace(IMPORT_ANCHOR, IMPORT + IMPORT_ANCHOR, 1)
    source = source.replace(RENDER_ANCHOR, RENDER + RENDER_ANCHOR, 1)
    source = source.replace(SAVE_OLD, SAVE_NEW, 1)
    ast.parse(source)
    # Model body/function preserved, old imports and existing tabs preserved.
    for existing_function in ('def score_runner(', 'def snapshot_race(', 'def bets_tab(',
                              'def results_tab(', 'def performance_tab('):
        if source.count(existing_function) != original.count(existing_function):
            raise RuntimeError('Unexpected function modification')
    return source, True


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--check', action='store_true', help='dry-run; do not write')
    parser.add_argument('--app', default='app.py', help='path to Streamlit app')
    args = parser.parse_args()
    app = Path(args.app)
    if not app.is_file():
        raise SystemExit(f'ERROR: {app} not found. Run from repository root.')
    original = app.read_text(encoding='utf-8')
    new, changed = patched(original)
    if args.check:
        print('v0.6.1 install preflight PASS' if changed else 'v0.6.1 already installed')
        return
    if changed:
        backup = app.with_name(app.name + '.v061.bak')
        if not backup.exists():
            backup.write_text(original, encoding='utf-8')
        app.write_text(new, encoding='utf-8')
        print('v0.6.1 installed. Backup:', backup)
    else:
        print('v0.6.1 already installed, no changes made')


if __name__ == '__main__':
    main()
