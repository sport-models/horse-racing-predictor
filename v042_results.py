"""v0.4.2 controlled FormFav full-field result import.
Does not touch the bets or race_results tables. Does not settle bets.
"""
import re
import unicodedata
from datetime import date, timedelta

import pandas as pd
import requests
import streamlit as st

URL = 'https://api.formfav.com/v1/results'


def canonical(name):
    s = unicodedata.normalize('NFKD', str(name or '')).encode('ascii', 'ignore').decode().casefold()
    return re.sub(r'[^a-z0-9]+', '', s)


def normalize_track(track):
    return re.sub(r'\s+', ' ', str(track or '').casefold().strip())


def parse(payload):
    if not isinstance(payload, dict):
        raise ValueError('Unexpected response type')
    status = str(payload.get('resultStatus', '')).casefold()
    if status != 'final':
        raise ValueError(f'Result not final (resultStatus={status or "missing"})')
    if payload.get('abandoned') is True:
        raise ValueError('Abandoned race cannot be imported')
    placings = payload.get('placings')
    if not isinstance(placings, list) or not placings:
        raise ValueError('Missing placings list')
    rows, seen, positions = [], set(), set()
    for runner in placings:
        if not isinstance(runner, dict):
            raise ValueError('Invalid runner object')
        name = str(runner.get('name') or '').strip()
        key = canonical(name)
        if not key or key in seen:
            raise ValueError(f'Missing/duplicate runner name: {name!r}')
        seen.add(key)
        scratched = runner.get('scratched') is True
        raw_pos = runner.get('position')
        pos = int(raw_pos) if raw_pos not in (None, '') else None
        if pos is not None and pos < 1:
            raise ValueError(f'Invalid finishing position for {name}')
        if not scratched and pos is None:
            raise ValueError(f'Missing finishing position for {name}')
        if scratched and pos is not None:
            raise ValueError(f'Scratched runner has finishing position: {name}')
        if pos is not None:
            positions.add(pos)
        price = runner.get('startingPrice')
        price = float(price) if price not in (None, '') else None
        if price is not None and price <= 1:
            raise ValueError(f'Invalid starting price for {name}')
        rows.append({
            'horse': name, 'horse_key': key, 'runner_number': runner.get('number'),
            'finishing_position': pos, 'scratched': scratched,
            'starting_price': price, 'margin': runner.get('margin'),
            'jockey': runner.get('jockey'), 'trainer': runner.get('trainer'),
            'carried_weight': runner.get('weight'), 'barrier': runner.get('barrier'),
            'result_status': status, 'result_version': payload.get('resultVersion'),
            'result_updated_at': payload.get('resultUpdatedAt'), 'source': 'FormFav',
        })
    if 1 not in positions:
        raise ValueError('No winning horse (position 1) in results')
    if sum(r['finishing_position'] == 1 for r in rows) != 1:
        raise ValueError('Unexpected multiple winners; dead heats require manual review')
    count = payload.get('numberOfRunners')
    # Providers may count starters rather than all entrants, so flag discrepancy for review.
    warning = None
    if count is not None and int(count) not in (len(rows), sum(not r['scratched'] for r in rows)):
        warning = f'API numberOfRunners={count} differs from returned {len(rows)} placings.'
    return rows, warning


def render(db_rows, db, secret):
    st.subheader('🏁 v0.4.2 — Import Full-Field Results')
    st.caption('Preview → verify → save. Bet settlements and model predictions are never changed.')
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
                    for p in predictions if cutoff <= date.fromisoformat(str(p['race_date'])[:10]) <= today}, reverse=True)
    if not races:
        st.info('No locked races within the 30-day FormFav results window.')
        return
    race = st.selectbox('Saved race', races, format_func=lambda x: f'{x[0]} — {x[1]} — R{x[2]}', key='v042_race')
    if st.button('Fetch and preview results', key='v042_fetch'):
        st.session_state.pop('v042_preview', None)
        params = {'date': race[0], 'track': race[1], 'race': race[2], 'race_code': 'gallops', 'country': 'au'}
        try:
            response = requests.get(URL, headers={'X-API-Key': str(api_key)}, params=params, timeout=30)
            response.raise_for_status()
            payload = response.json()
            rows, warning = parse(payload)
            if str(payload.get('date'))[:10] != race[0] or normalize_track(payload.get('track')) != normalize_track(race[1]) or int(payload.get('raceNumber', -1)) != race[2]:
                raise ValueError('API race identity does not match selected locked race')
            saved = [p for p in predictions if (str(p['race_date'])[:10], str(p['track']), int(p['race_number'])) == race]
            pred_keys = {canonical(p['horse']) for p in saved}
            result_keys = {r['horse_key'] for r in rows}
            if len(pred_keys) != len(saved):
                raise ValueError('Duplicate horse identities in saved predictions')
            missing = sorted(pred_keys - result_keys)
            extra = sorted(result_keys - pred_keys)
            if missing or extra:
                raise ValueError(f'Runner mismatch. Missing from results: {missing}; extra results: {extra}. Nothing saved.')
            if warning:
                raise ValueError(warning + ' Nothing saved until verified.')
            for r in rows:
                r.update({'race_date': race[0], 'track': race[1], 'race_number': race[2]})
            st.session_state['v042_preview'] = {'race': race, 'rows': rows}
        except (requests.RequestException, ValueError, TypeError, KeyError) as exc:
            st.error(f'Preview blocked: {exc}')
    preview = st.session_state.get('v042_preview')
    if not preview or tuple(preview['race']) != tuple(race):
        return
    rows = preview['rows']
    ranks = {canonical(p['horse']): p['model_rank'] for p in predictions if (str(p['race_date'])[:10], str(p['track']), int(p['race_number'])) == race}
    st.success(f'{len(rows)} runners matched to saved predictions. Final results verified structurally.')
    frame = pd.DataFrame(rows)
    frame['model_rank'] = frame['horse_key'].map(ranks)
    st.dataframe(frame[['horse', 'model_rank', 'finishing_position', 'scratched', 'starting_price', 'margin', 'jockey', 'trainer']], hide_index=True, use_container_width=True)
    st.warning('Review the entire field before saving. Existing imported races are never overwritten by this version.')
    confirmed = st.checkbox('I verified the race and all runner results', key='v042_confirm')
    if st.button('Save full-field results to Supabase', type='primary', disabled=not confirmed, key='v042_save'):
        try:
            # Preflight: table existence and no existing rows. A unique constraint also guards duplicates.
            existing = db().table('runner_results').select('id').eq('race_date', race[0]).eq('track', race[1]).eq('race_number', race[2]).limit(1).execute().data
            if existing:
                st.error('This race already has imported results. No changes made.')
                return
            db().table('runner_results').insert(rows).execute()
            st.success(f'Saved {len(rows)} full-field results. No bets were settled.')
            st.session_state.pop('v042_preview', None)
        except Exception as exc:
            st.error(f'Save failed: {exc}. Check runner_results before retrying; partial writes may exist.')
