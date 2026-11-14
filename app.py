import v061s_feature_audit_ui
import v061r_class_validation
import v061q_stayer_validation
import v061p_race_type_lab
import v061o_weight_lab
import v061n_relative_lab
import v061m_capture
import v061l_confidence
import v061i_analysis
import v061j_integrity
import v061k_integrity
import v061k_stage2
import v061h_readiness
import v061h_strategy
import v057g_each_way_benchmark
# v0.5.8 PROMO & FIELD SIZE INSTALLED
import v061b_historical
import v061_prospective
import v060_validation_hub
import v059_score_separation
import v058_field_size
import v058_promotions
import v057f_repaired_benchmark
from v057f_odds_integrity import make_snapshot_quote
import v057e_strategy_benchmark
import v057c_corrected_benchmark
import v057b_coverage_diagnostics
import v057a_market_benchmark
import v045_result_lookup
import v044_bulk_results
import v043_full_field
import v042_results
import formfav_results_probe
import v04_validation
import v043_full_field
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
TARGET_TRACKS = {'flemington', 'caulfield', 'moonee valley', 'mooneevalley',
                 'geelong', 'sandown', 'ballarat', 'seymour', 'seymore', 'pakenham'}


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
                value=10.0 if kind != "Place" else 0.0,
                step=0.5,
                disabled=(kind == "Place")
            )

            place_stake = c4.number_input(
                "Place stake ($)",
                min_value=0.0,
                value=10.0 if kind != "Win" else 0.0,
                step=0.5,
                disabled=(kind == "Win")
            )

            st.caption(
                "Each Way: enter the win and place "
                "portions separately."
            )
            st.caption("Your rule: place odds below $2 → $10 Win; "
                       "$2 or higher → $10 Win + $10 Place (each-way). "
                       "Suggested from entered place odds: " +
                       ("Win" if place_odds < 2.0 else "Each Way") +
                       ". Bet type remains your manual choice.")

            promo_label = st.selectbox(
                "Promotion / stake funding",
                list(v058_promotions.DISPLAY),
                help=("A 'stake returned as bonus' offer is NOT cash. "
                      "Record any credited bonus only after bookmaker confirmation. "
                      "A bonus bet used normally does not return its bonus stake when it wins."),
                key="v058_promo_" + str(runner["id"])
            )
            promo_mode = v058_promotions.DISPLAY[promo_label]
            bonus_win_stake = 0.0
            bonus_place_stake = 0.0
            if promo_mode == v058_promotions.BONUS:
                st.caption("Enter the actual bonus-funded amount for each bet component. "
                           "The remainder is cash. For each-way, specify which portion used a bonus.")
                pc1, pc2 = st.columns(2)
                bonus_win_stake = pc1.number_input(
                    "Win portion funded by bonus ($)", min_value=0.0,
                    value=0.0, step=0.5,
                    disabled=(kind == "Place"),
                    key="v058_bonus_win_" + str(runner["id"])
                )
                bonus_place_stake = pc2.number_input(
                    "Place portion funded by bonus ($)", min_value=0.0,
                    value=0.0, step=0.5,
                    disabled=(kind == "Win"),
                    key="v058_bonus_place_" + str(runner["id"])
                )
            if promo_mode == v058_promotions.REFUND:
                st.info("Offer noted only. Actual bonus credit is recorded on settlement "
                        "after confirming the bookmaker's terms and credit.")

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

            promo_valid, promo_reason = v058_promotions.validate_promotion(
                kind, promo_mode, win_stake, place_stake,
                bonus_win_stake, bonus_place_stake
            )
            if not valid or not promo_valid:
                st.error("Invalid bet or promotion allocation: " +
                         (promo_reason if not promo_valid else "stake allocation does not match bet type"))
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
                        "promo_mode": promo_mode,
                        "bonus_win_stake": bonus_win_stake,
                        "bonus_place_stake": bonus_place_stake,
                        "bonus_credit_value": 0.0,
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
        show["Cash stake"] = [v058_promotions.cash_stake(b) for b in bets]
        show["Cash P/L"] = [
            v058_promotions.cash_profit(b) if b.get("status") == "Settled" else None
            for b in bets
        ]

        columns = [
            "race_date", "track", "race_number",
            "horse", "bet_type", "win_odds",
            "place_odds", "win_stake", "place_stake",
            "total_stake", "status",
            "total_return", "Cash stake", "Cash P/L",
            "promo_mode", "bonus_win_stake", "bonus_place_stake",
            "bonus_credit_value",
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
    try:
        imported_results = db_rows('runner_results', order=None)
        result_lookup = v045_result_lookup.result_index(imported_results)
    except Exception as exc:
        st.warning(f'Imported placings unavailable: {exc}. Manual entry remains available.')
        result_lookup = {}
    if pending:
        ready = sum(v045_result_lookup.lookup(b, result_lookup)['state'] == 'ready' for b in pending)
        st.info(f'{ready} of {len(pending)} pending bets have imported official placings awaiting your review. No automatic settlements.')
    if pending:
        bet = st.selectbox('Select pending bet', pending, format_func=lambda b: f"{b['race_date']} {b['track']} R{b['race_number']} — {b['horse']} ({b['bet_type']}, {money(b['total_stake'])})")
        st.markdown("### Review & Manually Confirm Settlement")
        official = v045_result_lookup.lookup(bet, result_lookup)
        if official['state'] == 'ready':
            label = 'SCRATCHED' if official['scratched'] else f"{official['position']}"
            st.success(f'Imported official result: {label}. Bet remains Pending until you confirm settlement.')
        elif official['state'] == 'ambiguous':
            st.error('Multiple imported results match this horse. Resolve duplicates before settling.')
        elif official['state'] == 'invalid':
            st.error('Imported result is invalid or not final. Verify manually before settling.')
        else:
            st.info('No imported result found for this bet. Manual entry remains available.')

        bet_type = bet["bet_type"]
        win_stake = float(bet.get("win_stake") or 0)
        place_stake = float(bet.get("place_stake") or 0)
        win_odds = float(bet.get("win_odds") or 0)
        place_odds = float(bet.get("place_odds") or 0)
        total_stake = float(bet["total_stake"])
        cash_outlay = v058_promotions.cash_stake(bet)
        promo_mode = bet.get("promo_mode") or v058_promotions.NONE
        bonus_total = total_stake - cash_outlay

        if promo_mode == v058_promotions.REFUND:
            st.info("Money-back-as-bonus offer recorded. A bonus credit is NOT cash "
                    "and is recorded only after it is actually awarded.")
        if promo_mode == v058_promotions.BONUS:
            st.info(f"Bonus stake used: ${bonus_total:,.2f}. Cash stake: "
                    f"${cash_outlay:,.2f}. Bonus stake is excluded from winning cash returns.")

        c1, c2, c3 = st.columns(3)

        with c1:
            st.metric("Bet Type", bet_type)
            st.metric("Total Stake", money(total_stake))

        with c2:
            st.metric("Win Odds", f"{win_odds:.2f}")
            st.metric("Win Stake", money(win_stake))

        with c3:
            st.metric("Place Odds", f"{place_odds:.2f}")
            st.metric("Place Stake", money(place_stake))

        position = st.number_input(
            "Finishing Position",
            min_value=0,
            max_value=99,
            value=official['position'] if official['state'] == 'ready' and not official['scratched'] else 0,
            key=f"v045_position_{bet['id']}",
            help="Pre-filled from official results when available; review before settlement."
        )

        scratched = st.checkbox(
            "Scratched / Fully Voided",
            value=official['scratched'] if official['state'] == 'ready' else False,
            key=f"v045_scratched_{bet['id']}"
        )

        paying_places = None

        if bet_type in ("Place", "Each Way") and not scratched:
            place_option = st.selectbox(
                "Number of Paying Places",
                ["Select", 2, 3, 4, 5, 6],
                help="Use the place terms applicable to this bet."
            )

            if place_option != "Select":
                paying_places = int(place_option)

        calculated_return = v058_promotions.calculated_cash_payout(
            bet, int(position), paying_places=paying_places, scratched=scratched
        )

        manual_override = st.checkbox(
            "Manually override calculated payout",
            help="Use for deductions, dead heats or other adjustments."
        )

        if manual_override:
            payout = st.number_input(
                "Actual Bookmaker Return ($)",
                min_value=0.0,
                value=float(calculated_return),
                step=0.01
            )
        else:
            payout = calculated_return

        bonus_credit_value = 0.0
        credit_verified = False
        if promo_mode == v058_promotions.REFUND:
            bonus_credit_value = st.number_input(
                "Bonus credit ACTUALLY awarded by bookmaker ($)",
                min_value=0.0,
                value=float(bet.get("bonus_credit_value") or 0),
                step=0.5,
                key="v058_bonus_credit_" + str(bet["id"]),
                help="Enter 0 until awarded. A promotional bonus is not cash return."
            )
            if bonus_credit_value > 0:
                credit_verified = st.checkbox(
                    "I checked that the bookmaker has issued this bonus credit",
                    key="v058_bonus_credit_verified_" + str(bet["id"])
                )

        profit_loss = round(payout - cash_outlay, 2)

        st.markdown("### Settlement Summary")

        s1, s2, s3 = st.columns(3)

        with s1:
            st.metric("Total Stake", money(total_stake))
            st.caption(f"Cash outlay: {money(cash_outlay)}; bonus used: {money(bonus_total)}")

        with s2:
            st.metric("Total Payout", money(payout))

        with s3:
            st.metric(
                "Cash Profit / Loss",
                money(profit_loss)
            )
            if bonus_credit_value:
                st.caption(f"Bonus credit separately recorded: {money(bonus_credit_value)}")

        if manual_override:
            st.info(
                "Manual payout override enabled. "
                "Check the bookmaker settlement before saving."
            )

        if scratched:
            st.info("Fully voided: cash-funded portion refunded. Any bonus stake "
                    "reissued by the bookmaker must be recorded separately; do not treat it as cash.")

        can_settle = official['state'] not in ('ambiguous', 'invalid')

        if not scratched and position == 0:
            st.warning("Enter the finishing position.")
            can_settle = False

        if (
            not scratched
            and bet_type in ("Place", "Each Way")
            and paying_places is None
        ):
            st.warning("Select the bookmaker's paying-place terms.")
            can_settle = False

        if scratched and abs(payout - cash_outlay) > 0.009:
            st.error("A fully voided bet must refund the cash-funded stake; "
                     "bonus-funded stakes are not cash.")
            can_settle = False
        if bonus_credit_value > 0 and not credit_verified:
            st.error("Confirm the bookmaker bonus credit before recording it.")
            can_settle = False
        if promo_mode == v058_promotions.REFUND and bonus_credit_value > cash_outlay:
            st.error("Bonus credit cannot exceed the original cash-funded stake "
                     "without a separately documented offer. Enter 0 and correct later if needed.")
            can_settle = False

        submitted = st.button(
            "💾 Save Result & Settle",
            type="primary",
            disabled=not can_settle
        )

        if submitted:
            now = datetime.now(timezone.utc).isoformat()

            status = "Void" if scratched else "Settled"

            change = {
                "status": status,
                "bonus_credit_value": round(bonus_credit_value, 2),
                "total_return": round(payout, 2),
                "settled_at": now,
                "updated_at": now
            }

            try:
                db().table("race_results").upsert(
                    {
                        "race_date": bet["race_date"],
                        "track": bet["track"],
                        "race_number": bet["race_number"],
                        "horse": bet["horse"],
                        "finishing_position": (
                            None if scratched else int(position)
                        ),
                        "scratched": scratched,
                        "updated_at": now
                    },
                    on_conflict="race_date,track,race_number,horse"
                ).execute()

                db().table("bets").update(change).eq(
                    "id", bet["id"]
                ).eq(
                    "status", "Pending"
                ).execute()

                try:
                    audit(
                        bet["id"],
                        "SETTLE",
                        {
                            "status": "Pending",
                            "total_return": None
                        },
                        change
                    )
                except Exception as audit_error:
                    st.warning(
                        f"Bet settled but audit log failed: {audit_error}"
                    )

                st.success(
                    "Settlement saved successfully. "
                    f"Profit/Loss: {money(profit_loss)}"
                )

                st.rerun()

            except Exception as e:
                st.error(
                    f"Settlement failed: {e}. "
                    "Check the saved records before retrying."
                )    
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
                if (old.get("promo_mode") or v058_promotions.NONE) == v058_promotions.REFUND:
                    new_bonus_credit = st.number_input(
                        "Correct verified bonus credit ($ — not cash)",
                        min_value=0.0, value=float(old.get("bonus_credit_value") or 0), step=0.5)
                else:
                    new_bonus_credit = float(old.get("bonus_credit_value") or 0)
                reason = st.text_input('Reason for correction (required)')
                save = st.form_submit_button('Save correction')
            if save:
                if not reason.strip():
                    st.error('Enter a reason for the correction.')
                else:
                    changes = {'status': new_status, 'total_return': round(new_return, 2),
                               'bonus_credit_value': round(new_bonus_credit, 2),
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
    stake = sum(v058_promotions.cash_stake(b) for b in settled)
    returns = sum(float(b['total_return'] or 0) for b in settled)
    profit = returns - stake
    c = st.columns(5)
    for col, label, value in zip(c, ['Settled Bets', 'Staked', 'Returned', 'Net P/L', 'ROI'],
                                 [str(len(settled)), money(stake), money(returns), money(profit), f'{profit/stake*100:.2f}%' if stake else 'N/A']):
        col.metric(label, value)
    promo_totals = v058_promotions.cash_stake_metrics(bets)
    pc1, pc2 = st.columns(2)
    pc1.metric("Bonus stakes used (not cash)", money(promo_totals["bonus_staked"]))
    pc2.metric("Confirmed bonus credits (not cash)", money(promo_totals["bonus_credits_confirmed"]))
    st.caption(f'{len(pending)} pending bets excluded from settled ROI. Void bets excluded. '
               'Staked = actual cash outlay; bonus stakes and bonus credits shown separately. '
               'The existing Supabase profit_loss column may still reflect gross stake; cash-P/L is recalculated here.')
    if settled:
        wins = sum(float(b['total_return'] or 0) > v058_promotions.cash_stake(b) for b in settled)
        st.metric('Profitable bet rate', f'{wins/len(settled)*100:.1f}%')
        st.caption('Profitable bet rate is not the same as horse win/place strike rate; each-way and partial returns can differ.')
        frame = pd.DataFrame(settled)
        frame['Cash stake'] = [v058_promotions.cash_stake(b) for b in settled]
        frame['P/L'] = [v058_promotions.cash_profit(b) for b in settled]
        frame['Cumulative P/L'] = frame['P/L'].cumsum()
        st.line_chart(frame.set_index('placed_at')['Cumulative P/L'])
        st.subheader('By bet type')
        group = frame.groupby('bet_type', as_index=False).agg(Bets=('id', 'count'), Stake=('Cash stake', lambda x: sum(map(float, x))), Returns=('total_return', lambda x: sum(map(float, x))))
        group['P/L'] = group['Returns'] - group['Stake']
        group['ROI %'] = group.apply(lambda x: 100*x['P/L']/x['Stake'] if x['Stake'] else 0, axis=1)
        st.dataframe(group, hide_index=True, use_container_width=True)
        rank_lookup = {p['id']: p['model_rank'] for p in predictions}
        frame['Model Rank'] = frame['prediction_id'].map(rank_lookup)
        st.subheader('By saved model rank')
        ranked = frame.dropna(subset=['Model Rank'])
        if not ranked.empty:
            ranks = ranked.groupby('Model Rank', as_index=False).agg(Bets=('id', 'count'), Stake=('Cash stake', lambda x: sum(map(float, x))), Returns=('total_return', lambda x: sum(map(float, x))))
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
        st.warning('No selected Victorian racecourses returned for this date. Check API Meeting Diagnostics for venues returned by FormFav. Your saved bets and results remain accessible in the other tabs.')
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
    # v0.6.1s: combined rankings and odds appear below; avoid duplicate tables.

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

    st.subheader("📊 Full race rankings & bookmaker odds")
    st.dataframe(
        display_df,
        hide_index=True,
        use_container_width=True
    )

    v061s_feature_audit_ui.render_analysis_explanations(analysis['race_data'], df)

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
                    "Last Updated": quote["last_update"],
                    "Book Places Paid": quote.get("places_paid"),
                    "Race Places Reference (not book terms)": quote.get("race_places_paid_reference")
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

    # v0.6.1s: top five are already rows 1–5 of the consolidated full-field table.
    with st.expander('How rankings are calculated'):
        st.dataframe(pd.DataFrame({'Factor': ['Recent Form', 'Career Performance', 'Track Performance',
            'Distance Performance', 'Track & Distance', 'Barrier & Weight'],
            'Weight': ['35%', '15%', '15%', '15%', '10%', '10%']}), hide_index=True)
        st.info('Bookmaker odds are not included in the ranking algorithm.')
    if selected_date < date.today():
        st.warning('Historical API statistics may include post-race information. Do not treat these rankings as verified pre-race predictions.')
    if selected_date >= date.today():
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

        quotes.append(make_snapshot_quote(book, event))

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

t1, t2, t3, t4, t5, t6 = st.tabs([
    '🏇 Race Analysis',
    '💰 Bet Tracker',
    '🏁 Race Results',
    '📊 Performance',
    '🧪 Model Validation',
    '🔌 API Test'
])
with t1:
    analysis_tab()
with t2:
    bets_tab()
with t3:
    results_tab()
    st.divider()
    v042_results.render(db_rows, db, secret)
    st.divider()
    v044_bulk_results.render(db_rows, db, secret)
with t4:
    performance_tab()
with t5:
    v043_full_field.render(db_rows)
    st.divider()
    v061_prospective.render(secret)
    st.divider()
    v061b_historical.render(secret, score_runner)
    st.divider()
    v061h_readiness.render(secret)
    st.divider()
    v061h_strategy.render(secret)
    st.divider()
    v061i_analysis.render(secret)
    st.divider()
    v061j_integrity.render(secret)
    st.divider()
    v061k_integrity.render(secret)
    st.divider()
    v061k_stage2.render(secret)
    st.divider()
    v061l_confidence.render(secret)
    st.divider()
    v061m_capture.render(secret)
    st.divider()
    v061n_relative_lab.render(secret)
    st.divider()
    v061o_weight_lab.render(secret)
    st.divider()
    v061p_race_type_lab.render(secret)
    st.divider()
    v061q_stayer_validation.render(secret)
    st.divider()
    v061s_feature_audit_ui.render_feature_audit(secret)
    st.divider()
    v061r_class_validation.render(secret)
    st.divider()
    v060_validation_hub.render(db_rows)
    st.divider()
    v059_score_separation.render(db_rows)
    st.divider()
    v058_field_size.render(db_rows)
    st.divider()
    v057c_corrected_benchmark.render(db_rows)
    st.divider()
    v057e_strategy_benchmark.render(db_rows)
    st.divider()
    v057f_repaired_benchmark.render(db_rows)
    st.divider()
    v057g_each_way_benchmark.render(db_rows)
    st.divider()
    v057a_market_benchmark.render(db_rows)
    st.divider()
    v057b_coverage_diagnostics.render(db_rows)
with t6:
    puntersedge_tab()
    st.divider()
    formfav_results_probe.render(db_rows, secret)
st.divider()
st.caption('Horse Racing Predictor | Model v0.2 | Odds Integration v0.3 | Database-backed tracking')
