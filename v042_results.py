"""v0.4.2.1 scratch-aware full-field result importer.
Only inserts into runner_results; never changes bets, predictions, or race_results.
"""
import re
import unicodedata
from datetime import date, timedelta

import pandas as pd
import requests
import streamlit as st

URL = 'https://api.formfav.com/v1/results'


def canonical(value):
    text = unicodedata.normalize('NFKD', str(value or '')).encode('ascii', 'ignore').decode().casefold()
    return re.sub(r'[^a-z0-9]+', '', text)


def normalize_track(value):
    return re.sub(r'\s+', ' ', str(value or '').casefold().strip())


def parse(payload):
    if not isinstance(payload, dict) or payload.get('resultStatus') != 'final':
        raise ValueError('Results are not final; import is blocked.')
    if payload.get('abandoned') is True:
        raise ValueError('Abandoned race; import is blocked.')
    placings = payload.get('placings')
    if not isinstance(placings, list) or not placings:
        raise ValueError('Missing placings array.')
    rows, seen, winners = [], set(), 0
    for runner in placings:
        if not isinstance(runner, dict):
            raise ValueError('Invalid runner in placings.')
        horse = str(runner.get('name') or '').strip()
        key = canonical(horse)
        if not key or key in seen:
            raise ValueError(f'Missing or duplicate horse identity: {horse!r}')
        seen.add(key)
        scratched = runner.get('scratched') is True
        raw_position = runner.get('position')
        try:
            position = int(raw_position) if raw_position not in (None, '') else None
        except (ValueError, TypeError):
            raise ValueError(f'Invalid position for {horse}')
        if scratched and position is not None:
            raise ValueError(f'Scratched horse has a position: {horse}')
        if not scratched and (position is None or position < 1):
            raise ValueError(f'Starter has no valid finishing position: {horse}')
        winners += int(position == 1)
        raw_price = runner.get('startingPrice')
        try:
            price = float(raw_price) if raw_price not in (None, '') else None
        except (ValueError, TypeError):
            raise ValueError(f'Invalid starting price for {horse}')
        if price is not None and price <= 1:
            raise ValueError(f'Invalid starting price for {horse}')
        rows.append({
            'horse': horse, 'horse_key': key, 'runner_number': runner.get('number'),
            'finishing_position': position, 'scratched': scratched,
            'starting_price': price, 'margin': runner.get('margin'),
            'jockey': runner.get('jockey'), 'trainer': runner.get('trainer'),
            'carried_weight': runner.get('weight'), 'barrier': runner.get('barrier'),
            'result_status': 'final', 'result_version': payload.get('resultVersion'),
            'result_updated_at': payload.get('resultUpdatedAt'), 'source': 'FormFav',
        })
    if winners != 1:
        raise ValueError(f'Expected exactly one winner, found {winners}. Manual review required.')
    return rows


def earliest_batch(predictions, race):
    matching = [p for p in predictions if
                (str(p['race_date'])[:10], str(p['track']), int(p['race_number'])) == race]
    batches = {}
    for p in matching:
        batches.setdefault(str(p['prediction_batch_id']), []).append(p)
    if not batches:
        raise ValueError('No prediction batches for race')
    batch_id = min(batches, key=lambda b: min(str(p.get('predicted_at') or '') for p in batches[b]))
    chosen = batches[batch_id]
    keys = [canonical(p['horse']) for p in chosen]
    if len(set(keys)) != len(keys):
        raise ValueError('Duplicate horses within earliest prediction batch')
    return batch_id, chosen


def render(db_rows, db, secret):
    st.subheader('🏁 v0.4.2.1 — Scratch-Aware Full-Field Results')
    st.caption('Fetch → compare earliest saved prediction batch → verify → save official results. No bet settlements.')
    api_key = secret('FORMFAV_API_KEY')
    if not api_key:
        st.error('FORMFAV_API_KEY missing')
        return
    try:
        predictions = db_rows('race_predictions', order='predicted_at')
    except Exception as exc:
        st.error(f'Cannot load predictions: {exc}')
        return
    today = date.today()
    cutoff = today - timedelta(days=30)
    races = sorted({(str(p['race_date'])[:10], str(p['track']), int(p['race_number']))
                    for p in predictions if cutoff <= date.fromisoformat(str(p['race_date'])[:10]) <= today},
                   reverse=True)
    if not races:
        st.info('No saved races within the 30-day results window.')
        return
    race = st.selectbox('Saved race', races,
                        format_func=lambda r: f'{r[0]} — {r[1]} — R{r[2]}', key='v0421_race')
    if st.button('Fetch and preview results', key='v0421_fetch'):
        st.session_state.pop('v0421_preview', None)
        try:
            response = requests.get(URL, headers={'X-API-Key': str(api_key)}, params={
                'date': race[0], 'track': race[1], 'race': race[2],
                'race_code': 'gallops', 'country': 'au'}, timeout=30)
            response.raise_for_status()
            payload = response.json()
            if (str(payload.get('date'))[:10] != race[0] or
                    normalize_track(payload.get('track')) != normalize_track(race[1]) or
                    int(payload.get('raceNumber', -1)) != race[2]):
                raise ValueError('API race identity differs from selected race.')
            rows = parse(payload)
            batch_id, chosen = earliest_batch(predictions, race)
            pred_keys = {canonical(p['horse']) for p in chosen}
            result_keys = {r['horse_key'] for r in rows}
            starter_keys = {r['horse_key'] for r in rows if not r['scratched']}
            missing_starters = sorted(starter_keys - pred_keys)
            missing_results = sorted(pred_keys - result_keys)
            predicted_scratches = sorted(pred_keys & {r['horse_key'] for r in rows if r['scratched']})
            count = payload.get('numberOfRunners')
            # For this import, require the complete declared field, not just starters.
            if count is not None and int(count) != len(rows):
                raise ValueError(
                    f'FormFav reports {count} declared runners but placings contains {len(rows)}. '
                    'Cannot confirm complete scratches; import blocked pending review.')
            if missing_results:
                raise ValueError(f'Predicted horses missing from official result: {missing_results}')
            for row in rows:
                row.update({'race_date': race[0], 'track': race[1], 'race_number': race[2]})
            st.session_state['v0421_preview'] = {
                'race': race, 'rows': rows, 'batch': batch_id, 'chosen': chosen,
                'missing_starters': missing_starters, 'predicted_scratches': predicted_scratches,
            }
        except (requests.RequestException, ValueError, TypeError, KeyError) as exc:
            st.error(f'Preview blocked: {exc}')
    preview = st.session_state.get('v0421_preview')
    if not preview or tuple(preview['race']) != tuple(race):
        return
    rows = preview['rows']
    rank_lookup = {canonical(p['horse']): p['model_rank'] for p in preview['chosen']}
    frame = pd.DataFrame(rows)
    frame['model_rank'] = frame['horse_key'].map(rank_lookup)
    st.success(f'Final results retrieved: {len(rows)} declared runners, '
               f'{int((~frame["scratched"]).sum())} starters, {int(frame["scratched"].sum())} scratches.')
    st.caption(f'Earliest prediction batch: {preview["batch"]} ({len(preview["chosen"])} predicted horses).')
    st.dataframe(frame[['horse', 'model_rank', 'finishing_position', 'scratched',
                        'starting_price', 'margin', 'jockey', 'trainer']],
                 hide_index=True, use_container_width=True)
    if preview['predicted_scratches']:
        st.info(f'Predicted horses subsequently scratched: {preview["predicted_scratches"]}')
    if preview['missing_starters']:
        st.warning('Prediction coverage is incomplete: these actual starters were not in the '
                   f'earliest batch: {preview["missing_starters"]}. Full official results can still '
                   'be stored, but do not treat the batch as a complete-field backtest.')
    st.warning('Review all results. Existing imported races will not be overwritten.')
    confirmed = st.checkbox('I verified the entire official field and acknowledge any prediction coverage gaps',
                            key='v0421_confirm')
    if st.button('Save full-field results to Supabase', key='v0421_save', type='primary',
                 disabled=not confirmed):
        try:
            existing = (db().table('runner_results').select('id')
                        .eq('race_date', race[0]).eq('track', race[1])
                        .eq('race_number', race[2]).limit(1).execute().data)
            if existing:
                st.error('Race already imported. No changes made.')
                return
            db().table('runner_results').insert(rows).execute()
            st.success(f'Saved {len(rows)} official runner results. Bets and predictions unchanged.')
            st.session_state.pop('v0421_preview', None)
        except Exception as exc:
            st.error(f'Import failed: {exc}. Check runner_results before retrying.')
