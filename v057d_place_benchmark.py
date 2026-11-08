"""v0.5.7d: read-only placement / each-way benchmark based on saved bookmaker quotes.

No database writes. No fabricated place odds. Production v0.2 engine untouched.
"""
from collections import defaultdict
from datetime import timezone
import math
import re

import pandas as pd
import streamlit as st

from v057c_corrected_benchmark import benchmark, horse_key, race_key, timestamp


BOOKMAKER_ORDER = ('sportsbet', 'ladbrokes_au', 'tabtouch', 'bet365',
                   'betright', 'unibet', 'neds', 'pointsbetau')


def positive_price(value):
    try:
        number = float(value)
    except (ValueError, TypeError):
        return None
    return number if math.isfinite(number) and 1 < number < 100000 else None


def place_terms(value, n_starters):
    # Explicit bookmaker-supplied terms ONLY. Never infer from the race field.
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, str) and not re.fullmatch(r'\s*\d+\s*', value):
        return None
    try:
        n = int(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return n if 1 <= n <= n_starters else None


def quotes_for_runner(prediction, n_starters, snapshot_at):
    """Return eligible quote variants and rejected-reason detail; not settlement odds."""
    qs = prediction.get('bookmaker_quotes') or []
    if not isinstance(qs, list):
        return {}, 'MALFORMED_BOOKMAKER_QUOTES'
    valid = {}
    reasons = []
    for q in qs:
        if not isinstance(q, dict):
            reasons.append('INVALID_QUOTE_ROW'); continue
        book = str(q.get('key') or '').strip().casefold()
        if not book:
            reasons.append('MISSING_BOOKMAKER_ID'); continue
        if q.get('stale') is not False:
            reasons.append('STALE_OR_UNKNOWN_QUOTE'); continue
        win, place = positive_price(q.get('win')), positive_price(q.get('place'))
        if win is None or place is None:
            reasons.append('MISSING_WIN_OR_PLACE_ODDS'); continue
        paid = place_terms(q.get('places_paid'), n_starters)
        if paid is None:
            reasons.append('MISSING_OR_INVALID_PLACES_PAID'); continue
        raw_update = q.get('last_update')
        updated = timestamp(raw_update) if raw_update else None
        if raw_update and updated is None:
            reasons.append('INVALID_QUOTE_TIMESTAMP'); continue
        if updated and updated.astimezone(timezone.utc) > snapshot_at.astimezone(timezone.utc):
            reasons.append('QUOTE_UPDATED_AFTER_PREDICTION'); continue
        valid[book] = {
            'bookmaker': str(q.get('bookmaker') or book), 'bookmaker_key': book,
            'win': win, 'place': place, 'places_paid': paid,
            'odds_updated_at': updated.isoformat() if updated else '',
            'quote_time_verified': updated is not None,
        }
    return valid, ';'.join(sorted(set(reasons))) if reasons else ('NO_SAVED_BOOKMAKER_QUOTES' if not qs else '')


def common_quote(model_row, fav_row, n_starters, predicted_at):
    m, mr = quotes_for_runner(model_row, n_starters, predicted_at)
    f, fr = quotes_for_runner(fav_row, n_starters, predicted_at)
    common = set(m) & set(f)
    viable = [b for b in common if m[b]['places_paid'] == f[b]['places_paid']]
    viable.sort(key=lambda b: ((BOOKMAKER_ORDER.index(b) if b in BOOKMAKER_ORDER else len(BOOKMAKER_ORDER)), b))
    if not viable:
        if not m or not f:
            return None, f'MODEL:{mr or "NO_USABLE_QUOTE"} | FAVOURITE:{fr or "NO_USABLE_QUOTE"}'
        return None, 'NO_SHARED_BOOKMAKER_WITH_IDENTICAL_PLACE_TERMS'
    book = viable[0]
    return (m[book], f[book]), ''


def compute_place_benchmark(predictions, results):
    """Benchmark existing eligible races; return all, priced pair, missing and summary.

    SP win comparison uses all v0.5.7c eligible races. Place and each-way
    require actual saved nonstale quoted odds and explicit bookmaker place terms.
    """
    eligible, original_exclusions = benchmark(predictions, results)
    if eligible.empty:
        return eligible, pd.DataFrame(), pd.DataFrame(), pd.DataFrame(), original_exclusions
    pred_by_batch = defaultdict(dict)
    for p in predictions:
        batch = str(p.get('prediction_batch_id') or '')
        if not batch:
            continue
        try:
            pred_by_batch[(race_key(p), batch)][horse_key(p['horse'])] = p
        except (ValueError, TypeError, KeyError):
            continue
    result_by_race = defaultdict(dict)
    for r in results:
        try:
            result_by_race[race_key(r)][horse_key(r['horse'])] = r
        except (ValueError, TypeError, KeyError):
            continue
    records = []
    missing = []
    for item in eligible.to_dict('records'):
        key = race_key(item)
        batch = str(item['prediction_batch_id'])
        preds = pred_by_batch.get((key, batch), {})
        res = result_by_race.get(key, {})
        modelkey, favkey = horse_key(item['model_horse']), horse_key(item['favourite_horse'])
        model_result, fav_result = res.get(modelkey), res.get(favkey)
        if not model_result or not fav_result:
            # Defensive: should already be impossible after v0.5.7c eligibility.
            missing.append({'race': f'{key[0]} {key[1]} R{key[2]}', 'reason': 'SELECTED_RESULT_MISSING'})
            continue
        model_pos = int(model_result['finishing_position'])
        fav_pos = int(fav_result['finishing_position'])
        row = dict(item)
        row['model_finish'] = model_pos
        row['favourite_finish'] = fav_pos
        row['model_top_3'] = int(model_pos <= 3)
        row['favourite_top_3'] = int(fav_pos <= 3)
        row['pricing_status'] = 'NOT_PRICED'
        row['pricing_reason'] = ''
        # Never use later re-run prices: quotes must come from original selected batch.
        pair, reason = common_quote(preds.get(modelkey, {}), preds.get(favkey, {}),
                                    int(row['field_size']), timestamp(row['prediction_at_utc']))
        if pair is None:
            row['pricing_reason'] = reason
            missing.append({'race': f'{key[0]} {key[1]} R{key[2]}', 'reason': reason})
        else:
            m, f = pair
            row['pricing_status'] = ('SAVED_QUOTES_WITH_TIMESTAMP' if
                                     m['quote_time_verified'] and f['quote_time_verified'] else
                                     'SAVED_QUOTES_UNVERIFIED_UPDATE_TIME')
            row['bookmaker'] = m['bookmaker']
            row['bookmaker_key'] = m['bookmaker_key']
            row['places_paid'] = m['places_paid']
            row['model_quote_updated_at'] = m['odds_updated_at']
            row['favourite_quote_updated_at'] = f['odds_updated_at']
            for prefix, q, pos in [('model', m, model_pos), ('favourite', f, fav_pos)]:
                hit_win = pos == 1
                hit_place = pos <= q['places_paid']
                row[f'{prefix}_placed_paid'] = int(hit_place)
                row[f'{prefix}_quoted_win_odds'] = q['win']
                row[f'{prefix}_quoted_place_odds'] = q['place']
                row[f'{prefix}_quoted_win_return_10'] = round(10 * q['win'], 4) if hit_win else 0.0
                row[f'{prefix}_quoted_win_profit_10'] = row[f'{prefix}_quoted_win_return_10'] - 10
                row[f'{prefix}_place_return_10'] = round(10 * q['place'], 4) if hit_place else 0.0
                row[f'{prefix}_place_profit_10'] = row[f'{prefix}_place_return_10'] - 10
                row[f'{prefix}_eachway_return_10'] = round(
                    (5 * q['win'] if hit_win else 0) + (5 * q['place'] if hit_place else 0), 4)
                row[f'{prefix}_eachway_profit_10'] = row[f'{prefix}_eachway_return_10'] - 10
        records.append(row)
    all_df = pd.DataFrame(records)
    if all_df.empty:
        return all_df, pd.DataFrame(), pd.DataFrame(missing), pd.DataFrame(), original_exclusions
    priced = all_df[all_df['pricing_status'] != 'NOT_PRICED'].copy()
    missing_df = pd.DataFrame(missing, columns=['race', 'reason'])
    summaries = []
    def add(name, df, pcol, wincol, source):
        n = len(df)
        summaries.append({
            'strategy': name, 'price_source': source, 'races': n,
            'wins_or_paid_places': int(df[wincol].sum()) if n and wincol else None,
            'stake_total': 10 * n,
            'total_return': round(10*n + float(df[pcol].sum()), 2) if n else None,
            'profit': round(float(df[pcol].sum()), 2) if n else None,
            'roi_pct': round(100 * float(df[pcol].sum()) / (10*n), 2) if n else None,
        })
    for prefix, desc in [('model', 'Model top active'), ('favourite', 'SP favourite')]:
        all_df[f'{prefix}_sp_win_profit_10'] = 10 * all_df[f'{prefix}_profit_1u']
        add(desc + ' — $10 win', all_df, f'{prefix}_sp_win_profit_10',
            f'{prefix}_won', 'Official win starting price (all matched races)')
    if not priced.empty:
        for prefix, desc in [('model', 'Model top active'), ('favourite', 'SP favourite')]:
            add(desc + ' — $10 win', priced, f'{prefix}_quoted_win_profit_10',
                f'{prefix}_won', 'Saved bookmaker win quote (matched pricing subset)')
            add(desc + ' — $10 place', priced, f'{prefix}_place_profit_10',
                f'{prefix}_placed_paid', 'Saved bookmaker place quote (matched pricing subset)')
            add(desc + ' — $10 each-way ($5+$5)', priced, f'{prefix}_eachway_profit_10',
                f'{prefix}_placed_paid', 'Saved bookmaker win/place quotes (matched pricing subset)')
    return all_df, priced, missing_df, pd.DataFrame(summaries), original_exclusions


def render(db_rows):
    st.subheader('📊 v0.5.7d — Placings & Each-Way Returns')
    st.caption('Research only. Compare $10 win, $10 place, and $10 total each-way ($5 win + $5 place). '
               'No prediction, bets, settlement or scoring changes.')
    st.warning('Win SP is NOT place odds. A horse finishing 2nd/3rd returns $0 on a win-only bet. '
               'Place/e-w returns require actual saved bookmaker place prices and explicit places-paid terms. '
               'Never assume one-quarter SP, universal top-3 payouts or historical executable prices.')
    st.caption('Market favourite is selected retrospectively by SP; pre-off prices, official race start times, '
               'and input point-in-time validity are not fully verified. Quotes without timestamps remain flagged.')
    if not st.button('Run placement & each-way benchmark', key='v057d_run'):
        return
    try:
        with st.spinner('Reading saved predictions, odds snapshots, and official results...'):
            predictions = db_rows('race_predictions', order='predicted_at')
            results = db_rows('runner_results', order=None)
            all_df, priced, missing, summary, excluded = compute_place_benchmark(predictions, results)
    except Exception as exc:
        st.error(f'Placement benchmark failed: {exc}')
        return
    st.metric('Eligible races (win-SP benchmark)', len(all_df))
    st.metric('Races with comparable saved win/place quotes for both selections', len(priced))
    if all_df.empty:
        st.info('No eligible matched races. Use v0.5.7c exclusions to investigate.')
        return
    st.write('**Outcome analysis (does not imply place payouts)**')
    counts = pd.DataFrame([
        {'Selection': label, 'Top 3': int(all_df[f'{prefix}_top_3'].sum()),
         'Top 3 %': round(100 * all_df[f'{prefix}_top_3'].mean(), 2),
         'Wins': int(all_df[f'{prefix}_won'].sum()), 'Races': len(all_df)}
        for prefix, label in [('model', 'Model'), ('favourite', 'SP favourite')]])
    st.dataframe(counts, hide_index=True, use_container_width=True)
    st.write('**Actual quoted-price returns (when available)**')
    st.dataframe(summary, hide_index=True, use_container_width=True)
    if priced.empty:
        st.info('No races have complete comparable saved win/place bookmaker quotes and explicit place terms. '
                'Place and each-way ROI are unavailable; $0 is NOT imputed. '
                'We need recorded place dividends or timestamped bookmaker snapshots with places-paid terms.')
    else:
        st.caption('Win SP rows use all eligible races; quoted returns use only same-race/same-bookmaker '
                   'pairs with saved prices and place terms. Do not mix the ROI denominators.')
        st.dataframe(priced, hide_index=True, use_container_width=True)
        st.download_button('Download priced race comparison CSV', priced.to_csv(index=False).encode(),
                           file_name='v057d_priced_comparison.csv', mime='text/csv')
    st.download_button('Download all finishing positions & coverage CSV', all_df.to_csv(index=False).encode(),
                       file_name='v057d_placement_details.csv', mime='text/csv')
    st.download_button('Download summary CSV', summary.to_csv(index=False).encode(),
                       file_name='v057d_summary.csv', mime='text/csv')
    if not missing.empty:
        st.write('**Missing pricing / terms diagnostics**')
        st.dataframe(missing, hide_index=True, use_container_width=True)
        st.download_button('Download missing pricing CSV', missing.to_csv(index=False).encode(),
                           file_name='v057d_missing_odds.csv', mime='text/csv')
    if not excluded.empty:
        st.info(f'{len(excluded)} races remain excluded by the existing v0.5.7c eligibility rules.')
