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



def snapshot_race(race_date, track, number, rankings,
                  bookmaker_quotes=None, odds_snapshot_at=None):
    batch_id = str(uuid.uuid4())
    rows = []
    bookmaker_quotes = bookmaker_quotes or {}

    for row in rankings.to_dict("records"):
        horse = str(row["Horse"])

        rows.append({
            "race_date": str(race_date),
            "track": track,
            "race_number": int(number),
            "horse": horse,
            "model_rank": int(row["Rank"]),
            "form_score": (
                None if pd.isna(row["Form Score"])
                else float(row["Form Score"])
            ),
            "model_version": MODEL_VERSION,
            "prediction_batch_id": batch_id,
            "bookmaker_quotes": bookmaker_quotes.get(horse, []),
            "odds_snapshot_at": odds_snapshot_at
        })

    db().table("race_predictions").insert(rows).execute()
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
    st.subheader("💰 Bet Tracker")

    try:
        snapshots = latest_snapshots()
        bets = db_rows("bets", order="placed_at")
    except Exception as e:
        st.error(f"Database read failed: {e}")
        return

    if not snapshots:
        st.info(
            "Save a race prediction in Race Analysis "
            "before recording a bet."
        )
    else:
        races = sorted(
            set(
                (r["race_date"], r["track"], r["race_number"])
                for r in snapshots
            ),
            reverse=True
        )

        chosen = st.selectbox(
            "Saved race",
            races,
            format_func=lambda x:
                f"{x[0]} | {x[1]} | Race {x[2]}"
        )

        runners = sorted(
            (
                r for r in snapshots
                if (
                    r["race_date"],
                    r["track"],
                    r["race_number"]
                ) == chosen
            ),
            key=lambda r: r["model_rank"]
        )

        # Runner selection is deliberately outside the form,
        # so changing horses refreshes the reference odds.
        runner_index = st.selectbox(
            "Horse (saved model ranking)",
            range(len(runners)),
            format_func=lambda i:
                f"#{runners[i]['model_rank']} "
                f"{runners[i]['horse']} — "
                f"Score {runners[i]['form_score']}"
        )

        runner = runners[runner_index]
        quotes = runner.get("bookmaker_quotes") or []

        current_quotes = [
            q for q in quotes
            if isinstance(q, dict)
            and not q.get("stale", True)
        ]

        available_books = sorted(
            set(
                q.get("bookmaker")
                for q in current_quotes
                if q.get("bookmaker")
            )
        )

        options = ["Best available", "Manual entry"]
        options += available_books

        default_option = (
            "sportsbet" if "sportsbet" in available_books
            else "Best available"
        )

        bookmaker = st.selectbox(
            "Reference bookmaker",
            options,
            index=options.index(default_option)
        )

        if bookmaker == "Best available":
            win_quote = odds_best(current_quotes, "win")
            place_quote = odds_best(current_quotes, "place")

        elif bookmaker == "Manual entry":
            win_quote = None
            place_quote = None

        else:
            selected_quote = next(
                (
                    q for q in current_quotes
                    if q["bookmaker"] == bookmaker
                ),
                None
            )

            win_quote = (
                selected_quote
                if selected_quote
                and selected_quote.get("win") is not None
                else None
            )

            place_quote = (
                selected_quote
                if selected_quote
                and selected_quote.get("place") is not None
                else None
            )

        reference_win = (
            win_quote.get("win") if win_quote else None
        )
        reference_place = (
            place_quote.get("place") if place_quote else None
        )

        win_default = float(reference_win or 2.0)
        place_default = float(reference_place or 1.5)

        st.caption(
            "Odds below are pre-filled from the saved "
            "prediction snapshot, not refreshed live. "
            "You can override them before saving."
        )

        if not quotes:
            st.warning(
                "No bookmaker snapshot was saved for this horse. "
                "Enter the actual odds manually."
            )

        
        kind = st.selectbox(
            "Bet type",
            ["Win", "Place", "Each Way"],
            key="bet_type_" + str(runner["id"])
        )

        with st.form(
            "new_bet_" + str(runner["id"]) + "_" +
            bookmaker.replace(" ", "_") + "_" +
            kind.replace(" ", "_"),
            clear_on_submit=False
        ):

            c1, c2 = st.columns(2)

            win_odds = c1.number_input(
                "Win odds (decimal)",
                min_value=1.01,
                value=max(1.01, win_default),
                step=0.1
            )

            place_odds = c2.number_input(
                "Place odds (decimal)",
                min_value=1.01,
                value=max(1.01, place_default),
                step=0.1
            )

            c3, c4 = st.columns(2)

            
            win_stake = c3.number_input(
                "Win stake ($)",
                min_value=0.0,
                value=5.0 if kind != "Place" else 0.0,
                step=0.5,
                disabled=(kind == "Place")
            )

            place_stake = c4.number_input(
                "Place stake ($)",
                min_value=0.0,
                value=5.0 if kind != "Win" else 0.0,
                step=0.5,
                disabled=(kind == "Win")
            )

            st.caption(
                "Each Way: enter the win and place "
                "portions separately."
            )

            submitted = st.form_submit_button(
                "💾 Save Bet",
                type="primary"
            )

        if submitted:
            valid = (
                (
                    kind == "Win"
                    and win_stake > 0
                    and place_stake == 0
                )
                or (
                    kind == "Place"
                    and place_stake > 0
                    and win_stake == 0
                )
                or (
                    kind == "Each Way"
                    and win_stake > 0
                    and place_stake > 0
                )
            )

            if not valid:
                st.error(
                    "Stake allocation does not match bet type."
                )
            else:
                duplicate = any(
                    b["race_date"] == chosen[0]
                    and b["track"] == chosen[1]
                    and int(b["race_number"]) == chosen[2]
                    and b["horse"] == runner["horse"]
                    and b["bet_type"] == kind
                    and b["status"] == "Pending"
                    for b in bets
                )

                if duplicate:
                    st.error(
                        "A pending bet already exists for "
                        "this horse and bet type."
                    )
                else:
                    record = {
                        "prediction_id": runner["id"],
                        "race_date": chosen[0],
                        "track": chosen[1],
                        "race_number": chosen[2],
                        "horse": runner["horse"],
                        "bet_type": kind,
                        "win_odds": (
                            win_odds
                            if kind != "Place" else None
                        ),
                        "place_odds": (
                            place_odds
                            if kind != "Win" else None
                        ),
                        "win_stake": (
                            win_stake
                            if kind != "Place" else 0
                        ),
                        "place_stake": (
                            place_stake
                            if kind != "Win" else 0
                        ),
                        "status": "Pending",
                        "reference_bookmaker": bookmaker,
                        "reference_win_bookmaker": (
                            win_quote.get("bookmaker")
                            if win_quote else None
                        ),
                        "reference_place_bookmaker": (
                            place_quote.get("bookmaker")
                            if place_quote else None
                        ),
                        "reference_win_odds": reference_win,
                        "reference_place_odds": reference_place,
                        "reference_win_updated_at": (
                            win_quote.get("last_update")
                            if win_quote else None
                        ),
                        "reference_place_updated_at": (
                            place_quote.get("last_update")
                            if place_quote else None
                        )
                    }

                    try:
                        saved = (
                            db().table("bets")
                            .insert(record).execute().data
                        )

                        if not saved:
                            raise RuntimeError(
                                "No saved row was returned"
                            )

                        st.success(
                            "✅ Bet saved permanently to Supabase: "
                            + runner["horse"]
                        )

                    except Exception as e:
                        st.error(
                            f"Bet was NOT confirmed saved: {e}"
                        )

    if bets:
        st.subheader("Saved bets")

        show = pd.DataFrame(bets)

        columns = [
            "race_date", "track", "race_number",
            "horse", "bet_type", "win_odds",
            "place_odds", "win_stake", "place_stake",
            "total_stake", "status",
            "total_return", "profit_loss",
            "reference_bookmaker",
            "reference_win_bookmaker",
            "reference_place_bookmaker",
            "reference_win_odds",
            "reference_place_odds"
        ]

        st.dataframe(
            show[[c for c in columns if c in show.columns]],
            hide_index=True,
            use_container_width=True
        )



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

    # v0.3 bookmaker prices are displayed separately from
    # the model's independent form-based rankings.
    quotes_by_horse = {}
    odds_rows = []

    pe_key = secret("PUNTERSEDGE_API_KEY")

    if pe_key:
        try:
            with st.spinner("Retrieving bookmaker prices..."):
                odds_payload = fetch_live_racing_odds(
                    pe_key, analysis["track"]
                )

            matched_event = odds_find_race(
                odds_payload,
                analysis["date"],
                analysis["track"],
                analysis["race_number"]
            )

            if matched_event is None:
                st.warning(
                    "No unique matching Australian thoroughbred "
                    "race found in the live odds feed."
                )
            else:
                for horse in df["Horse"]:
                    quotes = odds_runner_quotes(
                        matched_event, horse
                    )
                    quotes_by_horse[str(horse)] = quotes

        except Exception as odds_error:
            st.warning(
                f"Bookmaker odds unavailable: {odds_error}"
            )
    else:
        st.info(
            "PUNTERSEDGE_API_KEY is not configured. "
            "Model rankings remain available."
        )

    for _, model_row in df.iterrows():
        horse = str(model_row["Horse"])
        quotes = quotes_by_horse.get(horse, [])
        summary = odds_summary(quotes)

        odds_rows.append({
            "Horse": horse,
            "Sportsbet Win": summary["sportsbet_win"],
            "Sportsbet Place": summary["sportsbet_place"],
            "Best Win": summary["best_win"],
            "Best Win Book": summary["best_win_bookmaker"],
            "Best Place": summary["best_place"],
            "Best Place Book": summary["best_place_bookmaker"]
        })

    odds_df = pd.DataFrame(odds_rows)

    display_df = df.merge(
        odds_df,
        on="Horse",
        how="left",
        validate="one_to_one"
    )

    st.subheader("💰 Sportsbet & Best Available Odds")
    st.dataframe(
        display_df,
        hide_index=True,
        use_container_width=True
    )

    with st.expander("Compare all bookmakers"):
        comparison = []

        for horse, quotes in quotes_by_horse.items():
            for quote in quotes:
                comparison.append({
                    "Horse": horse,
                    "Bookmaker": quote["bookmaker"],
                    "Win": quote["win"],
                    "Place": quote["place"],
                    "Stale": quote["stale"],
                    "Last Updated": quote["last_update"]
                })

        if comparison:
            st.dataframe(
                pd.DataFrame(comparison),
                hide_index=True,
                use_container_width=True
            )
        else:
            st.info("No matching bookmaker quotes available.")

    analysis["bookmaker_quotes"] = quotes_by_horse
    analysis["odds_snapshot_at"] = (
        datetime.now(timezone.utc).isoformat()
    )

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



ODDS_INTEGRATION_VERSION = 'v0.3'

def odds_horse_name(name):
    import re
    return re.sub(
        r"[^a-z0-9]",
        "",
        str(name or "").casefold()
    )


def odds_track_name(name):
    import re
    return re.sub(
        r"[^a-z0-9]",
        "",
        str(name or "").casefold()
    )


def odds_price(value):
    try:
        price = float(value)
        return price if price > 1.0 else None
    except (TypeError, ValueError):
        return None


def odds_race_date(event):
    from datetime import datetime
    value = event.get("start_time")
    if not value:
        return None

    try:
        from zoneinfo import ZoneInfo
        dt = datetime.fromisoformat(
            str(value).replace("Z", "+00:00")
        )
        return dt.astimezone(
            ZoneInfo("Australia/Melbourne")
        ).date().isoformat()
    except (ValueError, TypeError):
        return None


def odds_events(payload):
    if isinstance(payload, list):
        return payload

    if not isinstance(payload, dict):
        return []

    for key in ("races", "events", "data"):
        value = payload.get(key)

        if isinstance(value, list):
            return value

        if isinstance(value, dict):
            for nested_key in ("races", "events"):
                nested = value.get(nested_key)
                if isinstance(nested, list):
                    return nested

    return []


def odds_find_race(payload, race_date, track, race_number):
    matches = []

    for event in odds_events(payload):
        if not isinstance(event, dict):
            continue

        if str(event.get("country", "")).upper() != "AU":
            continue

        if str(event.get("category", "")).lower() != "horse":
            continue

        if odds_track_name(event.get("venue")) != odds_track_name(track):
            continue

        try:
            number = int(event.get("race_number"))
        except (TypeError, ValueError):
            continue

        if number != int(race_number):
            continue

        if odds_race_date(event) != str(race_date):
            continue

        matches.append(event)

    # Ambiguous matching must not silently select a race.
    return matches[0] if len(matches) == 1 else None


def odds_runner_quotes(event, horse):
    matches = [
        runner for runner in event.get("runners", [])
        if isinstance(runner, dict)
        and odds_horse_name(runner.get("name")) == odds_horse_name(horse)
    ]

    if len(matches) != 1:
        return []

    quotes = []

    for book in matches[0].get("bookmakers", []):
        if not isinstance(book, dict):
            continue

        quotes.append({
            "bookmaker": str(book.get("key", "")).lower(),
            "win": odds_price(book.get("win_price")),
            "place": odds_price(book.get("place_price")),
            "stale": book.get("stale") is not False,
            "last_update": book.get("last_update"),
            "source_url": book.get("source_url"),
        })

    return quotes


def odds_best(quotes, market):
    eligible = [
        quote for quote in quotes
        if not quote["stale"]
        and quote.get(market) is not None
    ]

    if not eligible:
        return None

    return max(
        eligible,
        key=lambda quote: quote[market]
    )


def odds_sportsbet(quotes):
    for quote in quotes:
        if quote["bookmaker"] == "sportsbet" and not quote["stale"]:
            return quote

    return None


def odds_summary(quotes):
    sportsbet = odds_sportsbet(quotes)
    best_win = odds_best(quotes, "win")
    best_place = odds_best(quotes, "place")

    return {
        "sportsbet_win": sportsbet["win"] if sportsbet else None,
        "sportsbet_place": sportsbet["place"] if sportsbet else None,
        "best_win": best_win["win"] if best_win else None,
        "best_win_bookmaker": best_win["bookmaker"] if best_win else None,
        "best_place": best_place["place"] if best_place else None,
        "best_place_bookmaker": best_place["bookmaker"] if best_place else None,
    }


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



@st.cache_data(ttl=30, show_spinner=False)
def fetch_live_racing_odds(api_key, track):
    return puntersedge_request(
        "/racing/next-to-go",
        api_key,
        {
            "country": "AU",
            "categories": "horse",
            "include_unresolved": "true",
            "venue": track,
            "num_races": 20
        }
    )


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
st.caption('Horse Racing Predictor | Model v0.2 | Odds Integration v0.3 | Database-backed tracking')
