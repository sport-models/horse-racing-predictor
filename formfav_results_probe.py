"""v0.4.1 read-only FormFav Pro race-results API probe.

No Supabase writes. No changes to the prediction model or bet settlements.
"""
from datetime import date, timedelta
import pandas as pd
import requests
import streamlit as st

BASE_URL = 'https://api.formfav.com'


def render(db_rows, secret):
    st.subheader('🧪 FormFav Pro — Results API Test (Read Only)')
    st.caption('Preview a completed race. This test does not save results or settle bets.')
    key = secret('FORMFAV_API_KEY')
    if not key:
        st.error('FORMFAV_API_KEY is not configured in Streamlit secrets.')
        return

    try:
        predictions = db_rows('race_predictions', order='predicted_at')
    except Exception as exc:
        st.error(f'Unable to read locked predictions: {exc}')
        return

    today = date.today()
    cutoff = today - timedelta(days=30)
    races = {}
    for row in predictions:
        try:
            race_day = date.fromisoformat(str(row['race_date'])[:10])
            race_number = int(row['race_number'])
            track = str(row['track']).strip()
        except (ValueError, TypeError, KeyError):
            continue
        if cutoff <= race_day < today and track:
            races[(race_day.isoformat(), track, race_number)] = True

    options = sorted(races, reverse=True)
    if options:
        selection = st.selectbox(
            'Saved race (within documented 30-day history)', options,
            format_func=lambda r: f'{r[0]} — {r[1]} — Race {r[2]}',
            key='ff_results_race_choice',
        )
        selected_day, selected_track, selected_number = selection
    else:
        st.info('No saved races found in the previous 30 days. You can test a completed race manually below.')
        selected_day = (today - timedelta(days=1)).isoformat()
        selected_track = 'Flemington'
        selected_number = 1

    manual = st.checkbox('Enter race details manually', value=not bool(options), key='ff_results_manual')
    if manual:
        selected_day = st.date_input(
            'Race date', value=date.fromisoformat(selected_day),
            min_value=cutoff, max_value=today - timedelta(days=1),
            key='ff_results_date',
        ).isoformat()
        selected_track = st.text_input('Track name / slug', value=selected_track, key='ff_results_track').strip()
        selected_number = st.number_input('Race number', min_value=1, max_value=20,
                                          value=int(selected_number), step=1, key='ff_results_number')

    st.info('FormFav Pro results may lag by 2–3 hours. A successful HTTP response can still have resultsStatus = pending.')
    if st.button('🔎 Fetch FormFav Results — Read Only', key='ff_results_fetch', type='primary'):
        if not selected_track:
            st.error('Enter a track name.')
            return
        params = {
            'date': selected_day,
            'track': selected_track,
            'race': int(selected_number),
            'race_code': 'gallops',
            'country': 'au',
        }
        try:
            with st.spinner('Requesting results from FormFav…'):
                response = requests.get(
                    BASE_URL + '/v1/results',
                    headers={'X-API-Key': str(key)},
                    params=params, timeout=30,
                )
            st.write('HTTP status:', response.status_code)
            if response.status_code == 403:
                st.error('403 Forbidden: check whether this API key has Pro results access.')
            response.raise_for_status()
            payload = response.json()
        except requests.HTTPError as exc:
            st.error(f'FormFav HTTP error: {exc}')
            return
        except (requests.RequestException, ValueError) as exc:
            st.error(f'FormFav request/JSON error: {exc}')
            return

        st.write('Race-level resultsStatus:', payload.get('resultsStatus', 'Not found at top level') if isinstance(payload, dict) else 'Response is not an object')
        st.warning('Read-only test: no race results, bet records, or predictions were changed.')
        st.subheader('Actual API response (inspect field names)')
        st.json(payload, expanded=False)
        st.download_button(
            'Download API response JSON',
            data=__import__('json').dumps(payload, indent=2, ensure_ascii=False).encode('utf-8'),
            file_name=f'formfav_results_{selected_day}_{selected_number}.json',
            mime='application/json',
            key='ff_results_download',
        )
        st.caption('Before sharing JSON publicly, inspect it for any private or account-specific information.')