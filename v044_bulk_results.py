"""v0.4.4 opt-in bulk FormFav results importer. No betting or prediction writes."""
from datetime import date, timedelta
import time
import requests
import pandas as pd
import streamlit as st
from v042_results import URL, parse, earliest_batch, canonical, normalize_track


def _race(p):
    return (str(p['race_date'])[:10], str(p['track']), int(p['race_number']))


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
    return {'race': race, 'rows': rows, 'batch': batch, 'missing_starters': missing_starters,
            'starters': sum(not r['scratched'] for r in rows),
            'scratches': sum(bool(r['scratched']) for r in rows)}


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
    cutoff = date.today() - timedelta(days=30)
    existing = {_race(r) for r in existing_rows}
    races = sorted({_race(p) for p in predictions if cutoff <= date.fromisoformat(str(p['race_date'])[:10]) <= date.today()})
    pending = [r for r in races if r not in existing]
    st.metric('Saved races not yet imported (within 30 days)', len(pending))
    if not pending:
        st.success('No eligible unimported races found.'); return
    selected = st.multiselect('Races to fetch (maximum 20 per batch)', pending,
                              default=pending[:min(10, len(pending))],
                              format_func=lambda r: f'{r[0]} — {r[1]} — R{r[2]}', key='v044_selected')
    if st.button('Fetch selected races — preview only', key='v044_fetch'):
        st.session_state.pop('v044_preview', None)
        if not selected or len(selected) > 20:
            st.error('Select between 1 and 20 races.'); return
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
    items = preview['items']
    if not items:
        st.info('No valid final race results to save.'); return
    overview = []
    for item in items:
        race = item['race']
        overview.append({'Date': race[0], 'Track': race[1], 'Race': race[2],
                         'Starters': item['starters'], 'Scratches': item['scratches'],
                         'Total runners': len(item['rows']),
                         'Missing predicted starters': len(item['missing_starters'])})
    st.dataframe(pd.DataFrame(overview), hide_index=True, use_container_width=True)
    for item in items:
        race = item['race']
        with st.expander(f'{race[0]} — {race[1]} R{race[2]}: full field'):
            if item['missing_starters']:
                st.warning(f"Unpredicted starters: {item['missing_starters']}")
            st.dataframe(pd.DataFrame(item['rows'])[['horse', 'finishing_position', 'scratched', 'starting_price']],
                         hide_index=True, use_container_width=True)
    st.warning('Review all races and scratches before saving. Failed/pending races are excluded.')
    confirmed = st.checkbox('I have reviewed the official results for EVERY race shown and accept any coverage gaps',
                            key='v044_confirm')
    if st.button('Save reviewed races to Supabase', type='primary', disabled=not confirmed, key='v044_save'):
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
