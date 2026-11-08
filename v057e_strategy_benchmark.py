"""v0.5.7e — read-only user betting-strategy benchmark.

Based on v0.5.7c eligible race selection and v0.5.7d contemporaneous
bookmaker pair validation. No database writes or changes to production scoring.

Strategy: saved place odds < $2 => $10 win; >= $2 => $10 win + $10 place.
"""
from __future__ import annotations

import math

import pandas as pd
import streamlit as st

from v057d_place_benchmark import compute_place_benchmark

THRESHOLD = 2.00
COMPONENT_STAKE = 10.00


def strategy_settlement(*, win_odds, place_odds, finishing_position, places_paid):
    """Compute decimal-odds return inclusive of successful component stakes.

    Raise on missing or impossible odds/terms rather than substituting fake values.
    """
    win = float(win_odds)
    place = float(place_odds)
    pos = int(finishing_position)
    paid = int(places_paid)
    if not all(math.isfinite(v) and 1 < v < 100000 for v in (win, place)):
        raise ValueError('Both win and place odds must be valid decimal prices > 1.')
    if pos < 1 or paid < 1:
        raise ValueError('Finishing position and places-paid must be positive integers.')
    bet_type = 'WIN' if place < THRESHOLD else 'EACH_WAY'
    stake_win = COMPONENT_STAKE
    stake_place = 0.0 if bet_type == 'WIN' else COMPONENT_STAKE
    win_hit = pos == 1
    place_hit = pos <= paid
    win_return = stake_win * win if win_hit else 0.0
    place_return = stake_place * place if place_hit else 0.0
    stake_total = stake_win + stake_place
    total_return = win_return + place_return
    return {
        'bet_type': bet_type,
        'stake_win': stake_win,
        'stake_place': stake_place,
        'stake_total': stake_total,
        'win_hit': int(win_hit),
        'paid_place_hit': int(place_hit),
        'win_return': win_return,
        'place_return': place_return,
        'total_return': total_return,
        'profit': total_return - stake_total,
    }


def evaluate_strategy(predictions, results):
    """Return full coverage, accurately-priced race comparisons, missing and summaries.

    Uses 0.5.7d's shared-bookmaker quote and places-paid checks, so both model
    and SP favourite are compared on identical races/odds source. No results
    are backfilled with SP or guessed place prices.
    """
    all_df, priced, missing, _prior_summary, exclusions = compute_place_benchmark(predictions, results)
    if priced.empty:
        return all_df, pd.DataFrame(), missing, pd.DataFrame(), exclusions
    rows = []
    invalid = []
    for item in priced.to_dict('records'):
        output = dict(item)
        try:
            paid = int(item['places_paid'])
            for prefix in ('model', 'favourite'):
                result = strategy_settlement(
                    win_odds=item[f'{prefix}_quoted_win_odds'],
                    place_odds=item[f'{prefix}_quoted_place_odds'],
                    finishing_position=item[f'{prefix}_finish'],
                    places_paid=paid,
                )
                for key, value in result.items():
                    output[f'{prefix}_strategy_{key}'] = value
        except (ValueError, TypeError, OverflowError, KeyError) as e:
            invalid.append({'race_date': item.get('race_date'), 'track': item.get('track'),
                            'race_number': item.get('race_number'),
                            'reason': f'INVALID_STRATEGY_INPUT: {e}'})
            continue
        rows.append(output)
    evaluated = pd.DataFrame(rows)
    if invalid:
        missing = pd.concat([missing, pd.DataFrame(invalid)], ignore_index=True)
    if evaluated.empty:
        return all_df, evaluated, missing, pd.DataFrame(), exclusions
    summaries = []
    for prefix, name in [('model', 'Model top active'), ('favourite', 'SP favourite')]:
        stake = float(evaluated[f'{prefix}_strategy_stake_total'].sum())
        payout = float(evaluated[f'{prefix}_strategy_total_return'].sum())
        wins = int(evaluated[f'{prefix}_strategy_win_hit'].sum())
        paid_places = int(evaluated[f'{prefix}_strategy_paid_place_hit'].sum())
        each_way = int((evaluated[f'{prefix}_strategy_bet_type'] == 'EACH_WAY').sum())
        summaries.append({
            'selection': name,
            'races': len(evaluated),
            'win_only_bets': len(evaluated) - each_way,
            'each_way_bets': each_way,
            'win_hits': wins,
            'paid_place_finishes': paid_places,
            'win_strike_rate_pct': 100 * wins / len(evaluated),
            'paid_place_finish_rate_pct': 100 * paid_places / len(evaluated),
            'total_staked': stake,
            'total_return': payout,
            'net_profit': payout - stake,
            'roi_pct': 100 * (payout - stake) / stake if stake else None,
            'price_basis': 'Saved same-bookmaker quote, historical/non-executable validation',
        })
    return all_df, evaluated, missing, pd.DataFrame(summaries), exclusions


def render(db_rows):
    st.subheader('🎯 v0.5.7e — Your $2 Place-Odds Betting Strategy')
    st.caption('Read-only research. Place quote < $2.00: $10 WIN; otherwise $10 WIN + $10 PLACE ($20 total). '
               'Exactly $2.00 is each-way. No production scoring or bet-settlement changes.')
    st.warning('Only saved bookmaker quotes with valid place terms are eligible. '
               'Historical quotes are NOT proof of executable prices or verified pre-off features. '
               'The market comparator is chosen retrospectively using win SP, not a verified live favourite.')
    if not st.button('Run custom betting strategy benchmark', key='v057e_run'):
        return
    try:
        with st.spinner('Comparing saved prices and official finishing positions...'):
            predictions = db_rows('race_predictions', order='predicted_at')
            results = db_rows('runner_results', order=None)
            all_df, evaluated, missing, summaries, exclusions = evaluate_strategy(predictions, results)
    except Exception as exc:
        st.error(f'Strategy benchmark failed: {exc}')
        return
    a, b, c = st.columns(3)
    a.metric('SP-benchmark eligible races', len(all_df))
    b.metric('Races with complete matching price evidence', len(evaluated))
    c.metric('Races lacking strategy price evidence', len(missing))
    if evaluated.empty:
        st.info('No comparable races with usable saved win/place odds AND explicit places-paid terms. '
                'We cannot calculate a trustworthy historical strategy ROI yet. '
                'The missing-odds export identifies why.')
    else:
        st.write('**Your strategy: matched-race comparison**')
        st.dataframe(summaries, hide_index=True, use_container_width=True)
        st.caption("Each row's strategy uses its own selection's place quote; model and market favourite "
                   'may have different bet types. ROI = net profit ÷ total actual stakes.')
        st.dataframe(evaluated, hide_index=True, use_container_width=True)
        st.download_button('Download strategy race results CSV', evaluated.to_csv(index=False).encode(),
                           file_name='v057e_strategy_races.csv', mime='text/csv', key='v057e_races_csv')
        st.download_button('Download strategy summary CSV', summaries.to_csv(index=False).encode(),
                           file_name='v057e_strategy_summary.csv', mime='text/csv', key='v057e_summary_csv')
    if not missing.empty:
        st.write('**Unavailable or invalid bookmaker quotes / place terms**')
        st.dataframe(missing, hide_index=True, use_container_width=True)
        st.download_button('Download missing-price diagnostics CSV', missing.to_csv(index=False).encode(),
                           file_name='v057e_missing_prices.csv', mime='text/csv', key='v057e_missing_csv')
    if not exclusions.empty:
        st.caption(f'{len(exclusions)} race(s) failed the earlier v0.5.7c race eligibility checks.')
        st.download_button('Download base benchmark exclusions CSV', exclusions.to_csv(index=False).encode(),
                           file_name='v057e_base_exclusions.csv', mime='text/csv', key='v057e_exclusions_csv')
    if not all_df.empty:
        st.download_button('Download complete race eligibility CSV', all_df.to_csv(index=False).encode(),
                           file_name='v057e_all_races.csv', mime='text/csv', key='v057e_all_csv')
