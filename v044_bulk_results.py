"""v0.4.4 opt-in bulk FormFav results importer. No betting or prediction writes."""
from datetime import date, timedelta, datetime
from zoneinfo import ZoneInfo
import time
import requests
import pandas as pd
import streamlit as st
from v042_results import URL, parse, earliest_batch, canonical, normalize_track


def _race(p):
    """Preserve the original track name for provider requests/display."""
    return (str(p['race_date'])[:10], str(p['track']), int(p['race_number']))


def _race_key(row):
    """Same identity across original prediction, runner result and abandoned ledger."""
    if isinstance(row, tuple):
        day, track, number = row
    else:
        day, track, number = _race(row)
    return str(day)[:10], normalize_track(track), int(number)


def _pending_races(predictions, result_rows, abandoned_rows, today):
    """Pure, case-independent list of saved races awaiting results."""
    cutoff = today - timedelta(days=30)
    existing_keys = {_race_key(r) for r in result_rows}
    abandoned_keys = {_race_key(r) for r in abandoned_rows}
    by_key = {}
    for prediction in predictions:
        race = _race(prediction)
        if cutoff <= date.fromisoformat(race[0]) <= today:
            by_key.setdefault(_race_key(race), race)
    pending = sorted((race for key, race in by_key.items()
                      if key not in existing_keys and key not in abandoned_keys),
                     key=lambda r: (r[0], normalize_track(r[1]), r[2]))
    return pending, abandoned_keys


def _fetch(race, api_key, predictions):
    response = requests.get(URL, headers={'X-API-Key': str(api_key)}, params={
        'date': race[0], 'track': race[1], 'race': race[2],
        'race_code': 'gallops', 'country': 'au'}, timeout=30)
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, dict):
        raise ValueError('Unexpected non-object API response')
    if (str(payload.get('date'))[:10] != race[0] or
            normalize_track(payload.get('track')) != normalize_track(race[1]) or
            int(payload.get('raceNumber', -1)) != race[2]):
        raise ValueError('FormFav race identity mismatch')
    rows = parse(payload)
    declared = payload.get('numberOfRunners')
    if declared is not None and int(declared) != len(rows):
        raise ValueError(f'Declared runners {declared} != returned runners {len(rows)}')
    batch, chosen = earliest_batch(predictions, race)
    predicted = {canonical(p['horse']) for p in chosen}
    returned = {r['horse_key'] for r in rows}
    missing_results = predicted - returned
    if missing_results:
        raise ValueError(f'Predicted horses absent from official field: {sorted(missing_results)}')
    missing_starters = sorted(r['horse'] for r in rows if not r['scratched'] and r['horse_key'] not in predicted)
    for row in rows:
        row.update(race_date=race[0], track=race[1], race_number=race[2])
    joint_winners = [r['horse'] for r in rows
                     if r['scratched'] is False and r['finishing_position'] == 1]
    return {'race': race, 'rows': rows, 'batch': batch, 'missing_starters': missing_starters,
            'starters': sum(not r['scratched'] for r in rows),
            'scratches': sum(bool(r['scratched']) for r in rows),
            'joint_winners': joint_winners, 'dead_heat_first': len(joint_winners) == 2}


def render(db_rows, db, secret):
    st.subheader('📥 v0.4.4 — Bulk Full-Field Results Import')
    st.caption('Explicit fetch → review → confirm → save. Only runner_results is written. No bet settlements or prediction changes.')
    api_key = secret('FORMFAV_API_KEY')
    if not api_key:
        st.error('FORMFAV_API_KEY is missing.'); return
    try:
        predictions = db_rows('race_predictions', order='predicted_at')
        existing_rows = db_rows('runner_results', columns='race_date,track,race_number', order=None)
    except Exception as exc:
        st.error(f'Could not read race records: {exc}'); return
    try:
        abandoned_rows = db_rows('abandoned_races',
                                 columns='race_date,track,track_key,race_number', order=None)
    except Exception as exc:
        st.error('Abandonment registry is missing or unreadable. Run migrate_v045_abandoned.sql '
                 'in Supabase SQL Editor before using the bulk importer; no writes performed. '+str(exc))
        return
    today = datetime.now(ZoneInfo('Australia/Melbourne')).date()
    pending, abandoned_keys = _pending_races(
        predictions, existing_rows, abandoned_rows, today)
    st.metric('Saved races not yet imported (within 30 days)', len(pending))
    if abandoned_keys:
        st.caption(f'{len(abandoned_keys)} confirmed abandoned race(s) excluded '
                   'from the pending-final-results list.')
    if not pending:
        st.success('No eligible unimported races found.'); return
    # Drop any selection retained from an earlier render that is now
    # excluded. This happens BEFORE the multiselect widget is instantiated.
    retained = st.session_state.get('v044_selected')
    if isinstance(retained, list):
        eligible_keys = {_race_key(r) for r in pending}
        cleaned = [r for r in retained if _race_key(r) in eligible_keys]
        if len(cleaned) != len(retained):
            st.session_state['v044_selected'] = cleaned
            st.info('Previously selected processed/abandoned races were removed.')
    selected = st.multiselect('Races to fetch (maximum 20 per batch)', pending,
                              default=pending[:min(10, len(pending))],
                              format_func=lambda r: f'{r[0]} — {r[1]} — R{r[2]}', key='v044_selected')
    if st.button('Fetch selected races — preview only', key='v044_fetch'):
        st.session_state.pop('v044_preview', None)
        if not selected or len(selected) > 20:
            st.error('Select between 1 and 20 races.'); return
        # Protect against stale multiselect values from before abandonment.
        current_pending_keys = {_race_key(r) for r in pending}
        if any(_race_key(r) not in current_pending_keys for r in selected):
            st.error('A selected race was already processed or marked abandoned. '
                     'Refresh the list before fetching.'); return
        previews, errors = [], []
        progress = st.progress(0)
        for i, race in enumerate(selected):
            try:
                previews.append(_fetch(race, api_key, predictions))
            except Exception as exc:
                errors.append({'Race': f'{race[0]} {race[1]} R{race[2]}', 'Issue': str(exc)})
            progress.progress((i + 1) / len(selected))
        st.session_state['v044_preview'] = {'items': previews, 'errors': errors}
    preview = st.session_state.get('v044_preview')
    if not preview:
        return
    if preview['errors']:
        st.warning(f"{len(preview['errors'])} race(s) blocked or unavailable. They will NOT be saved.")
        st.dataframe(pd.DataFrame(preview['errors']), hide_index=True, use_container_width=True)
    # A preview can survive a rerun after a separate abandonment save.
    # It is never eligible to write once any race is no longer pending.
    pending_keys = {_race_key(r) for r in pending}
    stale = [item for item in preview['items']
             if _race_key(item['race']) not in pending_keys]
    if stale:
        st.session_state.pop('v044_preview', None)
        st.warning('Results preview cleared because a race is now abandoned or '
                   'already imported. Fetch the remaining eligible races again.')
        return
    items = preview['items']
    if not items:
        st.info('No valid final race results to save.'); return
    overview = []
    for item in items:
        race = item['race']
        overview.append({'Date': race[0], 'Track': race[1], 'Race': race[2],
                         'Starters': item['starters'], 'Scratches': item['scratches'],
                         'Total runners': len(item['rows']),
                         'Winners (joint first)': len(item.get('joint_winners', [])),
                         'Two-way dead heat': bool(item.get('dead_heat_first')),
                         'Missing predicted starters': len(item['missing_starters'])})
    st.dataframe(pd.DataFrame(overview), hide_index=True, use_container_width=True)
    for item in items:
        race = item['race']
        with st.expander(f'{race[0]} — {race[1]} R{race[2]}: full field'):
            if item['missing_starters']:
                st.warning(f"Unpredicted starters: {item['missing_starters']}")
            st.dataframe(pd.DataFrame(item['rows'])[['horse', 'finishing_position', 'scratched', 'starting_price']],
                         hide_index=True, use_container_width=True)
    dead_heats = [item for item in items if item.get('dead_heat_first')]
    dead_heat_verified = True
    if dead_heats:
        labels = [f"{x['race'][0]} {x['race'][1]} R{x['race'][2]}: " +
                  ', '.join(x['joint_winners']) for x in dead_heats]
        st.warning('OFFICIAL TWO-WAY DEAD HEAT(S) FOR FIRST: ' + '; '.join(labels) +
                   '. Both winning horses must retain finishing position 1.')
        dead_heat_verified = st.checkbox(
            'I independently verified BOTH joint-first finishers for EVERY dead heat listed',
            key='v044_dead_heat_verified')
    st.warning('Review all races and scratches before saving. Failed/pending races are excluded.')
    confirmed = st.checkbox('I have reviewed the official results for EVERY race shown and accept any coverage gaps',
                            key='v044_confirm')
    if st.button('Save reviewed races to Supabase', type='primary',
                 disabled=not (confirmed and dead_heat_verified), key='v044_save'):
        saved, skipped, failed = [], [], []
        for item in items:
            race = item['race']
            label = f'{race[0]} {race[1]} R{race[2]}'
            try:
                # Recheck immediately before insert. No upsert: never overwrite results.
                exists = (db().table('runner_results').select('id').eq('race_date', race[0])
                          .eq('track', race[1]).eq('race_number', race[2]).limit(1).execute().data)
                if exists:
                    skipped.append(label)
                    continue
                matching_abandoned = (db().table('abandoned_races').select('id')
                                      .eq('race_date',race[0]).eq('track_key',normalize_track(race[1]))
                                      .eq('race_number',race[2]).limit(1).execute().data)
                if matching_abandoned:
                    skipped.append(label+' (abandoned)')
                    continue
                db().table('runner_results').insert(item['rows']).execute()
                saved.append(label)
            except Exception as exc:
                failed.append(f'{label}: {exc}')
        st.session_state.pop('v044_preview', None)
        st.success(f'Saved {len(saved)} race(s); skipped {len(skipped)} existing race(s); {len(failed)} failed.')
        if failed:
            st.error('Check the database before retrying: ' + '; '.join(failed))
        if skipped:
            st.info('Already imported: ' + ', '.join(skipped))
