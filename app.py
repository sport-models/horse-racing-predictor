import os
import re
import hmac
import uuid
from datetime import date, datetime, timezone
from decimal import Decimal

import pandas as pd
import requests
import streamlit as st
from supabase import create_client

st.set_page_config(page_title='Horse Racing Predictor', page_icon='🏇', layout='wide')
BASE_URL = 'https://api.formfav.com'
MODEL_VERSION = 'v0.2'
TARGET_TRACKS = {'flemington', 'caulfield', 'moonee valley', 'mooneevalley'}


def secret(name):
    try:
        return st.secrets.get(name) or os.getenv(name)
    except Exception:
        return os.getenv(name)


def login():
    password = secret('APP_PASSWORD')
    if not password:
        st.error('APP_PASSWORD is missing from Streamlit Secrets. Database access is disabled.')
        st.stop()
    if not st.session_state.get('authenticated', False):
        st.title('🔒 Horse Racing Predictor')
        with st.form('login'):
            attempt = st.text_input('Dashboard password', type='password')
            submitted = st.form_submit_button('Sign in')
        if submitted:
            if hmac.compare_digest(attempt, str(password)):
                st.session_state.authenticated = True
                st.rerun()
            else:
                st.error('Incorrect password.')
        st.stop()


login()
with st.sidebar:
    if st.button('Sign out'):
        st.session_state.authenticated = False
        st.rerun()


@st.cache_resource
def db_client(url, key):
    return create_client(url, key)


def db():
    url, key = secret('SUPABASE_URL'), secret('SUPABASE_KEY')
    if not url or not key:
        st.error('Supabase secrets are missing. Configure SUPABASE_URL and SUPABASE_KEY.')
        st.stop()
    return db_client(url, key)


def db_rows(table, columns='*', order='created_at', descending=True):
    # Pagination avoids the standard 1,000-row response limit.
    out, offset = [], 0
    while True:
        q = db().table(table).select(columns)
        if order:
            q = q.order(order, desc=descending)
        batch = q.range(offset, offset + 999).execute().data or []
        out.extend(batch)
        if len(batch) < 1000:
            return out
        offset += len(batch)


def money(value):
    return f'${float(value or 0):,.2f}'


def get_items(data, key):
    if isinstance(data, list):
        return data
    if not isinstance(data, dict):
        return []
    if isinstance(data.get(key), list):
        return data[key]
    nested = data.get('data')
    if isinstance(nested, list):
        return nested
    if isinstance(nested, dict) and isinstance(nested.get(key), list):
        return nested[key]
    return []


def get_value(obj, *keys, default=None):
    if not isinstance(obj, dict):
        return default
    for key in keys:
        if obj.get(key) is not None and obj.get(key) != '':
            return obj[key]
    return default


def as_number(value, default=0.0):
    try:
        return float(value)
    except (ValueError, TypeError):
        return default


def win_rate(record):
    if not isinstance(record, dict):
        return 0.0, 0
    starts = max(0, int(as_number(record.get('starts'))))
    wins = max(0, as_number(record.get('wins')))
    if starts == 0:
        return 0.0, 0
    return min(wins / starts, 1.0), starts


def recent_form_score(form):
    digits = re.findall(r'[0-9]', str(form or ''))[-5:]
    if not digits:
        return None
    points = {'1': 1.0, '2': 0.7, '3': 0.5, '4': 0.3, '5': 0.2}
    weights = range(1, len(digits) + 1)
    return sum(w * points.get(position, 0) for w, position in zip(weights, digits)) / sum(weights)


def score_runner(runner):
    # Preserved exactly from existing v0.2 model.
    stats = runner.get('stats') or {}
    if not isinstance(stats, dict):
        stats = {}
    career, career_starts = win_rate(stats.get('overall', {}))
    track, track_starts = win_rate(stats.get('track', {}))
    distance, distance_starts = win_rate(stats.get('distance', {}))
    track_distance, td_starts = win_rate(stats.get('trackDistance', {}))
    recent = recent_form_score(runner.get('form'))
    if recent is None or career_starts == 0:
        return None

    def smooth(rate, starts):
        return (starts * rate + 5 * career) / (starts + 5)

    track = smooth(track, track_starts)
    distance = smooth(distance, distance_starts)
    track_distance = smooth(track_distance, td_starts)
    barrier = as_number(runner.get('barrier'), 8)
    weight = as_number(runner.get('weight'), 56)
    barrier_score = max(0.0, min(1.0, 1 - (barrier - 1) / 16))
    weight_score = max(0.0, min(1.0, 1 - (weight - 48) / 16))
    context = (barrier_score + weight_score) / 2
    score = (0.35 * recent + 0.15 * career + 0.15 * track +
             0.15 * distance + 0.10 * track_distance + 0.10 * context)
    return round(score * 100, 2)


@st.cache_data(ttl=600, show_spinner=False)
def fetch_formfav(endpoint, params, api_key):
    response = requests.get(BASE_URL + endpoint, headers={'X-API-Key': api_key}, params=params, timeout=30)
    response.raise_for_status()
    return response.json()


def meeting_name(meeting):
    return str(get_value(meeting, 'track', 'trackName', 'venue', 'name', default='Unknown'))


def norm_track(name):
    return re.sub(r'\s+', ' ', str(name).lower().replace('-', ' ').replace('_', ' ')).strip()


def meeting_country(meeting):
    return str(get_value(meeting, 'country', 'countryCode', default='')).lower()


def race_label(race):
    return f"Race {get_value(race, 'raceNumber', 'number', default='?')} — {get_value(race, 'raceName', 'name', default='')} {get_value(race, 'distance', default='')}"


def snapshot_race(race_date, track, number, rankings):
    # An immutable snapshot; one batch per race per day is allowed to preserve genuine revisions.
    batch_id = str(uuid.uuid4())
    rows = []
    for row in rankings.to_dict('records'):
        rows.append({'race_date': str(race_date), 'track': track, 'race_number': int(number),
                     'horse': str(row['Horse']), 'model_rank': int(row['Rank']),
                     'form_score': None if pd.isna(row['Form Score']) else float(row['Form Score']),
                     'model_version': MODEL_VERSION, 'prediction_batch_id': batch_id})
    db().table('race_predictions').insert(rows).execute()
    return batch_id


def latest_snapshots():
    rows = db_rows('race_predictions', order='predicted_at')
    latest = {}
    for row in rows:
        key = (row['race_date'], row['track'], row['race_number'])
        if key not in latest:
            latest[key] = row['prediction_batch_id']
    return [r for r in rows if latest[(r['race_date'], r['track'], r['race_number'])] == r['prediction_batch_id']]


def audit(bet_id, action, old, new):
    db().table('bet_audit_log').insert({'bet_id': bet_id, 'action': action,
                                        'previous_values': old, 'new_values': new}).execute()


def bets_tab():
    st.subheader('💰 Bet Tracker')
    try:
        snapshots = latest_snapshots()
        bets = db_rows('bets', order='placed_at')
    except Exception as e:
        st.error(f'Database read failed: {e}')
        return
    if not snapshots:
        st.info('Save a race prediction in Race Analysis before recording a bet. Existing bets remain accessible here.')
    else:
        races = sorted(set((r['race_date'], r['track'], r['race_number']) for r in snapshots), reverse=True)
        chosen = st.selectbox('Saved race', races, format_func=lambda x: f'{x[0]} | {x[1]} | Race {x[2]}')
        runners = sorted((r for r in snapshots if (r['race_date'], r['track'], r['race_number']) == chosen), key=lambda r: r['model_rank'])
        with st.form('new_bet', clear_on_submit=False):
            runner = st.selectbox('Horse (saved model ranking)', runners, format_func=lambda r: f"#{r['model_rank']} {r['horse']} — Score {r['form_score']}")
            kind = st.selectbox('Bet type', ['Win', 'Place', 'Each Way'])
            c1, c2 = st.columns(2)
            win_odds = c1.number_input('Win odds (decimal)', min_value=1.01, value=2.0, step=0.1)
            place_odds = c2.number_input('Place odds (decimal)', min_value=1.01, value=1.5, step=0.1)
            c3, c4 = st.columns(2)
            win_stake = c3.number_input('Win stake ($)', min_value=0.0, value=5.0, step=0.5)
            place_stake = c4.number_input('Place stake ($)', min_value=0.0, value=5.0, step=0.5)
            st.caption('Each Way: enter the win and place portions separately. A $5 each-way bet costs $10 total.')
            submitted = st.form_submit_button('💾 Save Bet', type='primary')
        if submitted:
            valid = ((kind == 'Win' and win_stake > 0 and place_stake == 0) or
                     (kind == 'Place' and place_stake > 0 and win_stake == 0) or
                     (kind == 'Each Way' and win_stake > 0 and place_stake > 0))
            if not valid:
                st.error('Stake allocation does not match bet type. Win requires win stake only; Place requires place stake only; Each Way requires both.')
            else:
                duplicate = any(b['race_date'] == chosen[0] and b['track'] == chosen[1] and
                                int(b['race_number']) == chosen[2] and b['horse'] == runner['horse'] and
                                b['bet_type'] == kind and b['status'] == 'Pending' for b in bets)
                if duplicate:
                    st.error('A pending bet already exists for this horse and bet type. No duplicate was saved.')
                else:
                    record = {'prediction_id': runner['id'], 'race_date': chosen[0], 'track': chosen[1],
                              'race_number': chosen[2], 'horse': runner['horse'], 'bet_type': kind,
                              'win_odds': win_odds if kind != 'Place' else None,
                              'place_odds': place_odds if kind != 'Win' else None,
                              'win_stake': win_stake if kind != 'Place' else 0,
                              'place_stake': place_stake if kind != 'Win' else 0, 'status': 'Pending'}
                    try:
                        saved = db().table('bets').insert(record).execute().data
                        if not saved:
                            raise RuntimeError('No saved row was returned')
                        st.success(f"✅ Bet saved permanently to Supabase: {runner['horse']} — {money(record['win_stake'] + record['place_stake'])}")
                    except Exception as e:
                        st.error(f'Bet was NOT confirmed saved: {e}')
    if bets:
        st.subheader('Saved bets')
        show = pd.DataFrame(bets)
        columns = ['race_date', 'track', 'race_number', 'horse', 'bet_type', 'win_odds', 'place_odds',
                   'win_stake', 'place_stake', 'total_stake', 'status', 'total_return', 'profit_loss']
        st.dataframe(show[columns], hide_index=True, use_container_width=True)


def results_tab():
    st.subheader('🏁 Race Results & Settlement')
    try:
        bets = db_rows('bets', order='placed_at')
    except Exception as e:
        st.error(f'Database read failed: {e}')
        return
    if not bets:
        st.info('No saved bets yet.')
        return
    pending = [b for b in bets if b['status'] == 'Pending']
    if pending:
        bet = st.selectbox('Select pending bet', pending, format_func=lambda b: f"{b['race_date']} {b['track']} R{b['race_number']} — {b['horse']} ({b['bet_type']}, {money(b['total_stake'])})")
        with st.form('settle_bet'):
            position = st.number_input('Finishing position (0 = unknown or scratched)', min_value=0, max_value=99, value=0)
            scratched = st.checkbox('Scratched / voided bet')
            payout = st.number_input('Actual total bookmaker return ($)', min_value=0.0, value=0.0, step=0.5,
                                     help='Enter the actual total payout, including returned stake. For a loss, enter $0. For a void, enter the refunded stake.')
            st.caption('Enter the actual Sportsbet return to account for deductions, dead heats and place terms. Check the amount before saving.')
            submitted = st.form_submit_button('💾 Save Result & Settle', type='primary')
        if submitted:
            if scratched and abs(payout - float(bet['total_stake'])) > 0.009:
                st.error('For a void bet, the return should equal the full refunded stake. Please check the amount.')
            else:
                now = datetime.now(timezone.utc).isoformat()
                status = 'Void' if scratched else 'Settled'
                change = {'status': status, 'total_return': round(payout, 2), 'settled_at': now, 'updated_at': now}
                try:
                    # Save result first; if a later write fails, report it explicitly.
                    db().table('race_results').upsert({'race_date': bet['race_date'], 'track': bet['track'],
                        'race_number': bet['race_number'], 'horse': bet['horse'],
                        'finishing_position': None if position == 0 else int(position),
                        'scratched': scratched, 'updated_at': now},
                        on_conflict='race_date,track,race_number,horse').execute()
                    db().table('bets').update(change).eq('id', bet['id']).eq('status', 'Pending').execute()
                    try:
                        audit(bet['id'], 'SETTLE', {'status': 'Pending', 'total_return': None}, change)
                    except Exception as audit_error:
                        st.warning(f'Bet settled but audit log failed: {audit_error}')
                    st.success(f"✅ Settlement saved to Supabase. P/L: {money(payout - float(bet['total_stake']))}")
                except Exception as e:
                    st.error(f'Settlement could not be fully saved: {e}. Verify saved records before retrying.')
    else:
        st.info('No pending bets.')
    settled = [b for b in bets if b['status'] != 'Pending']
    if settled:
        st.subheader('Correct a settlement')
        with st.expander('Edit a settled bet (audit logged)'):
            old = st.selectbox('Bet to correct', settled, format_func=lambda b: f"{b['race_date']} {b['horse']} — {b['status']} — {money(b['total_return'])}")
            with st.form('correct_settlement'):
                new_status = st.selectbox('Correct status', ['Settled', 'Void'], index=0 if old['status'] == 'Settled' else 1)
                new_return = st.number_input('Correct total return ($)', min_value=0.0, value=float(old['total_return'] or 0), step=0.5)
                reason = st.text_input('Reason for correction (required)')
                save = st.form_submit_button('Save correction')
            if save:
                if not reason.strip():
                    st.error('Enter a reason for the correction.')
                else:
                    changes = {'status': new_status, 'total_return': round(new_return, 2),
                               'updated_at': datetime.now(timezone.utc).isoformat()}
                    try:
                        db().table('bets').update(changes).eq('id', old['id']).execute()
                        audit(old['id'], 'CORRECT: ' + reason.strip(),
                              {'status': old['status'], 'total_return': old['total_return']}, changes)
                        st.success('Correction saved and audit logged.')
                    except Exception as e:
                        st.error(f'Correction or audit failed: {e}. Check database before retrying.')


def performance_tab():
    st.subheader('📊 Performance')
    try:
        bets = db_rows('bets', order='placed_at')
        predictions = db_rows('race_predictions', order='predicted_at')
        results = db_rows('race_results', order='recorded_at')
        audits = db_rows('bet_audit_log', order='changed_at')
    except Exception as e:
        st.error(f'Database read failed: {e}')
        return
    settled = [b for b in bets if b['status'] == 'Settled']
    pending = [b for b in bets if b['status'] == 'Pending']
    stake = sum(float(b['total_stake']) for b in settled)
    returns = sum(float(b['total_return'] or 0) for b in settled)
    profit = returns - stake
    c = st.columns(5)
    for col, label, value in zip(c, ['Settled Bets', 'Staked', 'Returned', 'Net P/L', 'ROI'],
                                 [str(len(settled)), money(stake), money(returns), money(profit), f'{profit/stake*100:.2f}%' if stake else 'N/A']):
        col.metric(label, value)
    st.caption(f'{len(pending)} pending bets excluded from settled ROI. Void bets excluded from staked amount and ROI.')
    if settled:
        wins = sum(float(b['total_return'] or 0) > float(b['total_stake']) for b in settled)
        st.metric('Profitable bet rate', f'{wins/len(settled)*100:.1f}%')
        st.caption('Profitable bet rate is not the same as horse win/place strike rate; each-way and partial returns can differ.')
        frame = pd.DataFrame(settled)
        frame['P/L'] = frame['profit_loss'].astype(float)
        frame['Cumulative P/L'] = frame['P/L'].cumsum()
        st.line_chart(frame.set_index('placed_at')['Cumulative P/L'])
        st.subheader('By bet type')
        group = frame.groupby('bet_type', as_index=False).agg(Bets=('id', 'count'), Stake=('total_stake', lambda x: sum(map(float, x))), Returns=('total_return', lambda x: sum(map(float, x))))
        group['P/L'] = group['Returns'] - group['Stake']
        group['ROI %'] = group.apply(lambda x: 100*x['P/L']/x['Stake'] if x['Stake'] else 0, axis=1)
        st.dataframe(group, hide_index=True, use_container_width=True)
        rank_lookup = {p['id']: p['model_rank'] for p in predictions}
        frame['Model Rank'] = frame['prediction_id'].map(rank_lookup)
        st.subheader('By saved model rank')
        ranked = frame.dropna(subset=['Model Rank'])
        if not ranked.empty:
            ranks = ranked.groupby('Model Rank', as_index=False).agg(Bets=('id', 'count'), Stake=('total_stake', lambda x: sum(map(float, x))), Returns=('total_return', lambda x: sum(map(float, x))))
            ranks['P/L'] = ranks['Returns'] - ranks['Stake']
            ranks['ROI %'] = ranks.apply(lambda x: 100*x['P/L']/x['Stake'] if x['Stake'] else 0, axis=1)
            st.dataframe(ranks, hide_index=True, use_container_width=True)
    st.subheader('📥 Backup exports')
    for name, data in [('bets', bets), ('race_predictions', predictions), ('race_results', results), ('bet_audit_log', audits)]:
        st.download_button(f'Download {name}.csv', data=pd.DataFrame(data).to_csv(index=False).encode('utf-8'),
                           file_name=f'{name}_{date.today().isoformat()}.csv', mime='text/csv', key=f'export_{name}')
    st.caption('Backups are additional protection. Export periodically and store copies securely.')


def analysis_tab():
    api_key = secret('FORMFAV_API_KEY')
    if not api_key:
        st.error('FORMFAV_API_KEY is not configured.')
        return
    st.warning('Experimental prediction model. Form scores are not calibrated win probabilities.')
    selected_date = st.date_input('Racing date', value=date.today())
    if st.button('🔄 Refresh Race Meetings'):
        fetch_formfav.clear()
        st.rerun()
    try:
        with st.spinner('Retrieving race meetings...'):
            meetings_data = fetch_formfav('/v1/form/meetings',
                {'date': selected_date.isoformat(), 'race_code': 'gallops', 'country': 'au'}, api_key)
            meetings = get_items(meetings_data, 'meetings')
    except Exception as e:
        st.error(f'FormFav meeting request failed: {e}')
        return
    with st.expander('🔍 API Meeting Diagnostics'):
        st.write('Requested date:', str(selected_date))
        st.write('Meetings returned:', len(meetings))
        st.dataframe(pd.DataFrame([{'Track': meeting_name(m), 'Country': meeting_country(m),
            'Slug': m.get('slug'), 'Race Count': len(get_items(m, 'races')),
            'First Race UTC': (get_items(m, 'races') or [{}])[0].get('startTime')}
            for m in meetings if isinstance(m, dict)]), hide_index=True)
        with st.expander('Raw API response'):
            st.json(meetings_data)
    filtered = [m for m in meetings if isinstance(m, dict) and meeting_country(m) in ('au','aus','australia')
                and any(norm_track(meeting_name(m)) == t or norm_track(meeting_name(m)).startswith(t + ' ') for t in TARGET_TRACKS)]
    if not filtered:
        st.warning('No Flemington, Caulfield or Moonee Valley meeting returned for this date. Your saved bets and results remain accessible in the other tabs.')
        return
    meeting = st.selectbox('Select Racecourse', filtered, format_func=meeting_name)
    races = get_items(meeting, 'races')
    if not races:
        st.warning('No races returned for this meeting.')
        return
    race = st.selectbox('Select Race', races, format_func=race_label)
    track_slug = get_value(meeting, 'slug', 'trackSlug', default=meeting_name(meeting).lower().replace(' ', '-'))
    race_number = get_value(race, 'raceNumber', 'number')
    race_key = (str(selected_date), track_slug, str(race_number))
    if st.button('Analyse Race', type='primary'):
        try:
            with st.spinner('Analysing race form...'):
                race_data = fetch_formfav('/v1/form', {'date': selected_date.isoformat(), 'track': track_slug,
                    'race': race_number, 'race_code': 'gallops', 'country': 'au'}, api_key)
                runners = get_items(race_data, 'runners')
            rows = []
            for runner in runners:
                if not isinstance(runner, dict) or runner.get('scratched') is True:
                    continue
                try:
                    score = score_runner(runner)
                except (TypeError, ValueError, AttributeError):
                    score = None
                rows.append({'Horse': get_value(runner, 'name', 'runnerName', 'horseName', default='Unknown'),
                             'Form Score': score, 'Recent Form': runner.get('form', ''),
                             'Barrier': runner.get('barrier'), 'Weight': runner.get('weight'),
                             'Jockey': runner.get('jockey'), 'Trainer': runner.get('trainer')})
            if not rows:
                st.warning('No eligible runners returned.')
                return
            df = pd.DataFrame(rows).sort_values('Form Score', ascending=False, na_position='last').reset_index(drop=True)
            df.insert(0, 'Rank', range(1, len(df)+1))
            st.session_state['analysis'] = {'key': race_key, 'date': str(selected_date),
                'track': meeting_name(meeting), 'race_number': int(race_number),
                'df': df, 'race_data': race_data}
        except Exception as e:
            st.error(f'Race analysis failed: {e}')
            return
    analysis = st.session_state.get('analysis')
    if not analysis or analysis['key'] != race_key:
        return
    df = analysis['df']
    st.subheader(f"🏇 {analysis['track']} — Race {race_number}")
    rated = df.dropna(subset=['Form Score'])
    if not rated.empty:
        top = rated.iloc[0]
        st.success(f"🏆 Best Chance: {top['Horse']} — Form Score {top['Form Score']:.2f}")
    st.subheader('📊 Full Race Rankings')
    st.dataframe(df, hide_index=True, use_container_width=True)
    st.subheader('⭐ Top Five Model Selections')
    st.dataframe(df.head(5), hide_index=True, use_container_width=True)
    with st.expander('How rankings are calculated'):
        st.dataframe(pd.DataFrame({'Factor': ['Recent Form', 'Career Performance', 'Track Performance',
            'Distance Performance', 'Track & Distance', 'Barrier & Weight'],
            'Weight': ['35%', '15%', '15%', '15%', '10%', '10%']}), hide_index=True)
        st.info('Bookmaker odds are not included in the ranking algorithm.')
    if selected_date < date.today():
        st.warning('Historical API statistics may include post-race information. Do not treat these rankings as verified pre-race predictions.')
    if selected_date >= date.today():
        if st.button('🔒 Save & Lock All Race Predictions', type='primary'):
            try:
                batch = snapshot_race(analysis['date'], analysis['track'], analysis['race_number'], df)
                st.success(f'✅ {len(df)} predictions permanently saved to Supabase (batch {batch[:8]}). Open Bet Tracker to enter odds and stakes.')
            except Exception as e:
                st.error(f'Predictions were NOT confirmed saved: {e}')
    else:
        st.info('Saving historical rankings as pre-race predictions is disabled to prevent post-race data leakage.')
    with st.expander('🔍 Raw Race Data'):
        st.json(analysis['race_data'])

# ============================================================
# PUNTERSEDGE API INTEGRATION
# Read-only diagnostics
# Model v0.2 and Supabase data remain unchanged
# ============================================================

PUNTERSEDGE_BASE_URL = "https://api.puntersedge.online/v1"


def puntersedge_request(endpoint, api_key, params=None):
    """
    Make a read-only request to the PuntersEdge API.
    API credentials are never displayed.
    """

    response = requests.get(
        PUNTERSEDGE_BASE_URL + endpoint,
        headers={
            "X-API-Key": api_key,
            "Accept": "application/json"
        },
        params=params or {},
        timeout=20
    )

    if not response.ok:
        raise RuntimeError(
            f"PuntersEdge returned HTTP {response.status_code} "
            f"for {endpoint}"
        )

    return response.json()


def puntersedge_tab():

    st.subheader("🔌 PuntersEdge API Test")

    st.caption(
        "Read-only API testing. Existing predictions, "
        "bets and results remain unchanged."
    )

    api_key = secret("PUNTERSEDGE_API_KEY")

    if not api_key:
        st.error(
            "PUNTERSEDGE_API_KEY is missing from Streamlit Secrets."
        )
        return

    st.success("PuntersEdge API key found in Secrets.")

    st.info(
        "These are exploratory API checks. Endpoint availability, "
        "parameters and credit charges must be verified against "
        "your PuntersEdge account documentation."
    )

    # --------------------------------------------------------
    # TEST 1: ACCOUNT USAGE
    # --------------------------------------------------------

    with st.expander("🔑 Check API Account / Usage"):

        st.write("Endpoint: /usage")

        if st.button("Check API Connection", key="pe_usage"):

            try:

                with st.spinner("Connecting to PuntersEdge..."):

                    data = puntersedge_request(
                        "/usage",
                        api_key
                    )

                st.success("API request successful!")

                st.json(data, expanded=False)

            except Exception as e:

                st.error(f"API request failed: {e}")

    # --------------------------------------------------------
    # TEST 2: UPCOMING RACING EVENTS
    # --------------------------------------------------------

    with st.expander("🏇 Upcoming Racing Events"):

        st.write("Endpoint: /racing/events")

        if st.button("Fetch Racing Events", key="pe_events"):

            try:

                with st.spinner("Retrieving racing events..."):

                                        data = puntersedge_request(
                        "/racing/events",
                        api_key,
                        {
                            "country": "AU",
                            "categories": "horse",
                            "include_unresolved": "true",
                            "date": "2026-11-05"
                        }
                    )

                st.success("Racing API request successful!")

                if isinstance(data, list):

                    st.write(
                        f"Records returned: {len(data)}"
                    )

                elif isinstance(data, dict):

                    st.write(
                        "Response fields:",
                        list(data.keys())
                    )

                st.json(data, expanded=False)

            except Exception as e:

                st.error(f"Racing request failed: {e}")

    # --------------------------------------------------------
    # TEST 3: NEXT-TO-GO RACING ODDS
    # --------------------------------------------------------

    with st.expander("💰 Next-to-Go Racing Odds"):

        st.write("Endpoint: /racing/next-to-go")

        st.caption(
            "Checks whether upcoming races and bookmaker "
            "prices are available."
        )

        if st.button("Fetch Racing Odds", key="pe_odds"):

            try:

                with st.spinner("Retrieving racing odds..."):

                    data = puntersedge_request(
                        "/racing/next-to-go",
                        api_key,
                        {
                            "country": "AU",
                            "categories": "horse",
                            "include_unresolved": "true",
                            "venue": "Flemington",
                            "num_races": 20
                        }
                    )

                st.success("Racing odds request successful!")

                if isinstance(data, list):

                    st.write(
                        f"Records returned: {len(data)}"
                    )

                elif isinstance(data, dict):

                    st.write(
                        "Response fields:",
                        list(data.keys())
                    )

                st.json(data, expanded=False)

            except Exception as e:

                st.error(f"Odds request failed: {e}")

    # --------------------------------------------------------
    # TEST 4: AFL ODDS
    # --------------------------------------------------------

    with st.expander("🏉 AFL Odds"):

        st.write("Endpoint: /sports/afl/odds")

        st.caption(
            "Tests availability of AFL bookmaker markets."
        )

        if st.button("Fetch AFL Odds", key="pe_afl"):

            try:

                with st.spinner("Retrieving AFL odds..."):

                    data = puntersedge_request(
                        "/sports/afl/odds",
                        api_key
                    )

                st.success("AFL API request successful!")

                st.json(data, expanded=False)

            except Exception as e:

                st.error(f"AFL request failed: {e}")

    st.divider()

    st.caption(
        "No automatic odds saving is enabled yet. "
        "First we need to verify the API response format "
        "and match runners against FormFav."
    )
st.title('🏇 Horse Racing Predictor')
st.caption('Australian Thoroughbred Racing | Independent Form-Based Rankings | Persistent Bet Ledger')
try:
    db().table('bets').select('id').limit(1).execute()
except Exception as e:
    st.error(f'Supabase connection failed. No records can be saved until resolved: {e}')
    st.stop()

t1, t2, t3, t4, t5 = st.tabs([
    '🏇 Race Analysis',
    '💰 Bet Tracker',
    '🏁 Race Results',
    '📊 Performance',
    '🔌 API Test'
])
with t1:
    analysis_tab()
with t2:
    bets_tab()
with t3:
    results_tab()
with t4:
    performance_tab()
with t5:
    puntersedge_tab()
st.divider()
st.caption('Horse Racing Predictor | Model v0.2 unchanged | Database-backed tracking')
