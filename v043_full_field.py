"""v0.4.3 read-only full-field validation. Never changes prediction or result records."""
import re
import unicodedata
from datetime import datetime
from zoneinfo import ZoneInfo
import pandas as pd
import streamlit as st

LOCAL = ZoneInfo('Australia/Melbourne')


def norm(value):
    return re.sub(r'[^a-z0-9]+', '', unicodedata.normalize('NFKD', str(value or '')).encode('ascii', 'ignore').decode().casefold())


def race_key(row):
    return (str(row['race_date'])[:10], norm(row['track']), int(row['race_number']))


def render(db_rows, secret):
    st.divider()
    st.subheader('🏇 v0.4.3 — Full-field validation')
    st.caption('Read-only. Uses the earliest recorded prediction batch per race; preserves original ranks and excludes scratches from starter metrics. Same-day locks remain unverified without race start-time evidence.')
    try:
        predictions = db_rows('race_predictions', order='predicted_at')
        results = db_rows('runner_results', order=None)
        import v061_prospective
        from v061u_full_field import make_archive_index, proof_for_batch
        try:
            archived = v061_prospective._read_all(v061_prospective.archive_client(secret), v061_prospective.TABLE)
            archive_index = make_archive_index(archived)
        except Exception as archive_error:
            archive_index = {}
            st.warning('Pre-schedule archive evidence unavailable; older metrics remain visible. '+type(archive_error).__name__)
    except Exception as exc:
        st.error(f'Unable to read full-field validation data: {exc}')
        return
    if not predictions or not results:
        st.info('Both locked predictions and imported runner_results are required.')
        return

    grouped = {}
    for p in predictions:
        try:
            key = (race_key(p), str(p['prediction_batch_id']))
            locked = pd.to_datetime(p['predicted_at'], utc=True, errors='coerce')
            if pd.isna(locked):
                continue
            grouped.setdefault(key, []).append((locked, p))
        except (ValueError, TypeError, KeyError):
            continue
    earliest = {}
    for (race, batch), items in grouped.items():
        first = min(t for t, _ in items)
        if race not in earliest or first < earliest[race][0]:
            earliest[race] = (first, batch, [p for _, p in items])

    by_race = {}
    for r in results:
        try:
            by_race.setdefault(race_key(r), []).append(r)
        except (ValueError, TypeError, KeyError):
            continue

    details, summary = [], []
    for race, official in sorted(by_race.items()):
        if race not in earliest:
            continue
        lock, batch, selected = earliest[race]
        race_day = pd.Timestamp(race[0]).date()
        local_lock_day = lock.tz_convert(LOCAL).date()
        timing = ('PRIOR_DAY' if local_lock_day < race_day else
                  'SAME_DAY_UNVERIFIED' if local_lock_day == race_day else 'AFTER_RACE_DAY_EXCLUDE')
        if timing == 'AFTER_RACE_DAY_EXCLUDE':
            continue
        preds = {}
        duplicate_predictions = False
        for p in selected:
            h = norm(p.get('horse'))
            if not h or h in preds:
                duplicate_predictions = True
            preds[h] = p
        official_keys = [norm(r.get('horse')) for r in official]
        duplicate_results = len(official_keys) != len(set(official_keys))
        starters = [r for r in official if r.get('scratched') is False]
        winners = [r for r in starters if pd.to_numeric(r.get('finishing_position'), errors='coerce') == 1]
        matched = sum(norm(r.get('horse')) in preds for r in starters)
        valid = (not duplicate_predictions and not duplicate_results and len(winners) == 1 and
                 len(starters) > 0 and all(pd.notna(pd.to_numeric(r.get('finishing_position'), errors='coerce')) for r in starters))
        top = [r for r in starters if norm(r.get('horse')) in preds and pd.to_numeric(preds[norm(r.get('horse'))].get('model_rank'), errors='coerce') == 1]
        winner_rank = (pd.to_numeric(preds[norm(winners[0]['horse'])].get('model_rank'), errors='coerce')
                       if winners and norm(winners[0]['horse']) in preds else None)
        top_finish = pd.to_numeric(top[0].get('finishing_position'), errors='coerce') if len(top) == 1 else None
        complete = matched == len(starters) and valid
        evidence = proof_for_batch(batch, selected, archive_index)
        summary.append({'Date': race[0], 'Track': official[0]['track'], 'Race': race[2],
                        'Schedule evidence': evidence,
                        'Lock (Melbourne)': lock.tz_convert(LOCAL).strftime('%Y-%m-%d %H:%M'),
                        'Timing': timing, 'Batch': batch, 'Starters': len(starters),
                        'Predicted starters': matched, 'Coverage %': round(100 * matched / len(starters), 1) if starters else None,
                        'Complete field': complete, 'Data valid': valid,
                        'Top pick': top[0]['horse'] if len(top) == 1 else None,
                        'Top pick finish': top_finish, 'Winner': winners[0]['horse'] if len(winners) == 1 else None,
                        'Winner model rank': winner_rank,
                        'Top 3 covered': bool(winner_rank <= 3) if pd.notna(winner_rank) else None})
        for r in official:
            p = preds.get(norm(r.get('horse')))
            details.append({'Date': race[0], 'Track': r['track'], 'Race': race[2],
                            'Horse': r['horse'], 'Model rank': p.get('model_rank') if p else None,
                            'Finish': r.get('finishing_position'), 'Scratched': r.get('scratched'),
                            'Starting price': r.get('starting_price'), 'Margin': r.get('margin'),
                            'Jockey': r.get('jockey'), 'Trainer': r.get('trainer'),
                            'Predicted': p is not None, 'Timing': timing, 'Complete field': complete})

    if not summary:
        st.info('No imported races could be matched to eligible saved prediction batches.')
        return
    s = pd.DataFrame(summary)
    st.write(f'**{len(s)} imported race(s) matched to locked prediction batches.**')
    st.warning('Pre-race timing is not independently proven for same-day locks. Race-level metrics below are descriptive; incomplete fields and invalid races are excluded from the strict baseline.')
    strict = s[(s['Complete field']) & (s['Data valid']) & (s['Timing'] == 'PRIOR_DAY')]
    c1, c2, c3, c4 = st.columns(4)
    c1.metric('Imported matched races', len(s))
    c2.metric('Complete-field races', int(s['Complete field'].sum()))
    c3.metric('Strict baseline races', len(strict))
    c4.metric('Starter coverage', f"{sum(s['Predicted starters'])}/{sum(s['Starters'])}")
    if not strict.empty:
        top_known = strict[strict['Top pick finish'].notna()]
        win_count = int((top_known['Top pick finish'] == 1).sum())
        place_count = int((top_known['Top pick finish'] <= 3).sum())
        cover_count = int((strict['Top 3 covered'] == True).sum())
        st.markdown('**Strict baseline — complete field, prior-day lock only**')
        x, y, z = st.columns(3)
        x.metric('Top pick winners', f'{win_count}/{len(top_known)}')
        y.metric('Top pick top-3 finishes', f'{place_count}/{len(top_known)}')
        z.metric('Winner ranked top 3', f'{cover_count}/{len(strict)}')
    else:
        st.info('No races meet the strict complete-field, prior-day-lock criteria yet. Other races remain visible below.')
    # Live descriptive scoreboard for every valid imported race, regardless of
    # prediction timing. Never treat this as an approved prospective baseline.
    all_complete=s[(s['Complete field']) & (s['Data valid']) & (s['Top pick finish'].notna())]
    st.markdown('**All complete imported races — descriptive, including same-day locks**')
    st.caption('Updates when additional official race results are imported. Settling bets does '
               'not change race winners or these counters. This includes locks without independently '
               'verified timing and is NOT the strict prospective cohort.')
    a,b,c,d=st.columns(4)
    a.metric('Races with valid top pick',len(all_complete))
    b.metric('Top pick winners',f"{int((all_complete['Top pick finish']==1).sum())}/{len(all_complete)}")
    c.metric('Top pick top-3 finishes',f"{int((all_complete['Top pick finish']<=3).sum())}/{len(all_complete)}")
    d.metric('Winner ranked top 3',f"{int((all_complete['Top 3 covered']==True).sum())}/{len(all_complete)}")
    # New same-day-eligible cohort: only exact archived, before-scheduled-start
    # snapshots qualify. NO assertion that actual-off timing was independently verified.
    prestart=s[(s['Complete field']) & (s['Data valid']) &
               (s['Schedule evidence']=='BEFORE_SCHEDULE_ARCHIVED_NOT_ACTUAL_OFF_CERTIFIED') &
               (s['Top pick finish'].notna())]
    st.markdown('**Before-scheduled-start archive cohort — includes eligible same-day locks**')
    st.caption('Matches each earliest saved prediction batch to its immutable, hash-verified archive, '
               'with fresh form, ranking and server archive timestamps before the scheduled start. '
               'This is NOT independently certified actual race-off timing. Bets do not alter these counts.')
    x,y,z,w=st.columns(4)
    x.metric('Matched pre-schedule races',len(prestart))
    y.metric('Top pick winners', f"{int((prestart['Top pick finish']==1).sum())}/{len(prestart)}")
    z.metric('Top pick top-3 finishes', f"{int((prestart['Top pick finish']<=3).sum())}/{len(prestart)}")
    w.metric('Winner ranked top 3', f"{int((prestart['Top 3 covered']==True).sum())}/{len(prestart)}")
    other=s[(s['Complete field']) & (s['Data valid']) &
            (s['Schedule evidence']!='BEFORE_SCHEDULE_ARCHIVED_NOT_ACTUAL_OFF_CERTIFIED')]
    if not other.empty:
        st.info(f'{len(other)} complete imported race(s) lack a matching before-schedule archive. '
                'They remain visible in the race-by-race table and are excluded from the new cohort.')
    st.subheader('Race-by-race performance')
    st.dataframe(s.drop(columns=['Batch']), hide_index=True, use_container_width=True)
    st.download_button('Download race validation CSV', s.to_csv(index=False).encode('utf-8'), 'v043_races.csv', 'text/csv')
    st.subheader('Imported results viewer')
    labels = {(row['Date'], norm(row['Track']), int(row['Race'])): f"{row['Date']} — {row['Track']} — R{row['Race']}" for _, row in s.iterrows()}
    selected = st.selectbox('Race to inspect', list(labels), format_func=lambda k: labels[k], key='v043_view_race')
    d = pd.DataFrame(details)
    subset = d[(d['Date'] == selected[0]) & (d['Track'].map(norm) == selected[1]) & (d['Race'] == selected[2])]
    st.dataframe(subset.drop(columns=['Date', 'Track', 'Race', 'Timing', 'Complete field']), hide_index=True, use_container_width=True)
    st.download_button('Download all matched runner results CSV', d.to_csv(index=False).encode('utf-8'), 'v043_runner_validation.csv', 'text/csv')
