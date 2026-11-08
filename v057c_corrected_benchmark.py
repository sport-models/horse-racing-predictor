"""v0.5.7c corrected scratch-aware read-only SP market benchmark."""
from collections import defaultdict
import math
from datetime import datetime, timezone
import re
import unicodedata
import pandas as pd
import streamlit as st


def horse_key(s):
    s = unicodedata.normalize('NFKD', str(s or '')).encode('ascii', 'ignore').decode().casefold()
    return re.sub(r'[^a-z0-9]', '', s)


def track_key(s):
    return re.sub(r'[^a-z0-9]', '', str(s or '').casefold())


def race_key(r):
    return (str(r['race_date'])[:10], track_key(r['track']), int(r['race_number']))


def timestamp(s):
    if not s:
        return None
    try:
        d = datetime.fromisoformat(str(s).replace('Z', '+00:00'))
        return d if d.tzinfo else None
    except (ValueError, TypeError):
        return None


def benchmark(predictions, results):
    """Return matched per-race rows and exclusion records. No mutating inputs."""
    p_by_race, r_by_race = defaultdict(list), defaultdict(list)
    issues = []
    for source, rows, dest in [('prediction', predictions, p_by_race), ('result', results, r_by_race)]:
        for row in rows:
            try:
                dest[race_key(row)].append(row)
            except (ValueError, KeyError, TypeError):
                issues.append({'race': str(row.get('id', '?')), 'reason': 'INVALID_' + source.upper() + '_RACE_KEY'})
    output = []
    for race in sorted(set(p_by_race) | set(r_by_race)):
        label = f'{race[0]} {race[1]} R{race[2]}'
        ps, rs = p_by_race.get(race, []), r_by_race.get(race, [])
        if not ps or not rs:
            issues.append({'race': label, 'reason': 'MISSING_PREDICTIONS' if not ps else 'MISSING_RESULTS'})
            continue
        batches = defaultdict(list)
        for p in ps:
            batch = p.get('prediction_batch_id')
            if not batch:
                batches[f'no_batch_{p.get("id", id(p))}'].append(p)
            else:
                batches[str(batch)].append(p)
        # Use the earliest complete saved batch, never the most recent post-result rerun.
        def batch_order(item):
            stamps = [timestamp(p.get('predicted_at')) for p in item[1]]
            return (min(stamps) if stamps and all(stamps) else datetime.max.replace(tzinfo=timezone.utc), item[0])
        batch_id, chosen = min(batches.items(), key=batch_order)
        if any(timestamp(p.get('predicted_at')) is None for p in chosen):
            issues.append({'race': label, 'reason': 'MISSING_OR_UNZONED_PREDICTION_TIMESTAMP'})
            continue
        pkeys = [horse_key(p.get('horse')) for p in chosen]
        rkeys = [horse_key(r.get('horse')) for r in rs]
        if not all(pkeys) or len(set(pkeys)) != len(pkeys):
            issues.append({'race': label, 'reason': 'DUPLICATE_OR_EMPTY_PREDICTION_HORSE'})
            continue
        if not all(rkeys) or len(set(rkeys)) != len(rkeys):
            issues.append({'race': label, 'reason': 'DUPLICATE_OR_EMPTY_RESULT_HORSE'})
            continue
        if any(r.get('result_status') != 'final' for r in rs):
            issues.append({'race': label, 'reason': 'NONFINAL_RESULTS'})
            continue
        if any(r.get('scratched') not in (True, False) for r in rs):
            issues.append({'race': label, 'reason': 'INVALID_SCRATCH_STATUS'})
            continue
        starters = [r for r in rs if r.get('scratched') is False]
        starter_keys = {horse_key(r['horse']) for r in starters}
        missing_starters = starter_keys - set(pkeys)
        if missing_starters:
            issues.append({'race': label, 'reason': 'MISSING_ACTUAL_STARTERS', 'detail': ', '.join(sorted(missing_starters))})
            continue
        unexpected_predictions = set(pkeys) - set(rkeys)
        if unexpected_predictions:
            issues.append({'race': label, 'reason': 'PREDICTED_HORSE_ABSENT_FROM_RESULTS', 'detail': ', '.join(sorted(unexpected_predictions))})
            continue
        if not starters:
            issues.append({'race': label, 'reason': 'NO_STARTERS'})
            continue
        try:
            positions = [int(r['finishing_position']) for r in starters]
        except (ValueError, TypeError, KeyError):
            issues.append({'race': label, 'reason': 'INVALID_FINISHING_POSITIONS'})
            continue
        if positions.count(1) != 1 or min(positions) != 1:
            issues.append({'race': label, 'reason': 'WINNER_AMBIGUOUS'})
            continue
        if any(r.get('scratched') is True and r.get('finishing_position') is not None for r in rs):
            issues.append({'race': label, 'reason': 'SCRATCHED_RUNNER_HAS_POSITION'})
            continue
        try:
            prices = {horse_key(r['horse']): float(r['starting_price']) for r in starters}
        except (ValueError, TypeError, KeyError):
            issues.append({'race': label, 'reason': 'MISSING_OR_INVALID_STARTING_PRICE'})
            continue
        if any(not (math.isfinite(x) and 1 < x < 100000) for x in prices.values()):
            issues.append({'race': label, 'reason': 'INVALID_STARTING_PRICE'})
            continue
        # Preserve original rank #1 and transparently promote only the highest-ranked starter.
        try:
            ranked = [(int(p['model_rank']), horse_key(p['horse']), p) for p in chosen]
        except (ValueError, TypeError, KeyError):
            issues.append({'race': label, 'reason': 'INVALID_MODEL_RANK'})
            continue
        ranks = [rank for rank, _, _ in ranked]
        if any(rank < 1 for rank in ranks) or len(set(ranks)) != len(ranks):
            issues.append({'race': label, 'reason': 'DUPLICATE_OR_INVALID_MODEL_RANK'})
            continue
        rank1 = [item for item in ranked if item[0] == 1]
        if len(rank1) != 1:
            issues.append({'race': label, 'reason': 'MODEL_TOP_RANK_AMBIGUOUS'})
            continue
        original_top = rank1[0]
        original_scratched = original_top[1] not in starter_keys
        active_ranked = sorted((item for item in ranked if item[1] in starter_keys), key=lambda x: x[0])
        if not active_ranked:
            issues.append({'race': label, 'reason': 'NO_PREDICTED_STARTERS'})
            continue
        selected_rank, model_key, selected_prediction = active_ranked[0]
        lowest = min(prices.values())
        favourites = [k for k, v in prices.items() if abs(v - lowest) < 1e-9]
        if len(favourites) != 1:
            issues.append({'race': label, 'reason': 'JOINT_MARKET_FAVOURITES'})
            continue
        fav_key = favourites[0]
        winner_key = horse_key(next(r['horse'] for r in starters if int(r['finishing_position']) == 1))
        ptime = min(timestamp(p['predicted_at']) for p in chosen)
        # Prediction timestamp alone cannot verify pre-off availability without official start times.
        output.append({'race_date': race[0], 'track': str(rs[0]['track']), 'race_number': race[2],
                       'prediction_batch_id': batch_id, 'prediction_at_utc': ptime.isoformat(),
                       'time_validation': 'UNVERIFIED_OFFICIAL_START_TIME',
                       'original_model_horse': original_top[2]['horse'],
                       'original_model_rank_1_scratched': original_scratched,
                       'active_model_rank': selected_rank, 'selection_promoted_after_scratch': original_scratched,
                       'predicted_scratches': sum(k in set(pkeys) for k in (set(rkeys) - starter_keys)),
                       'model_horse': selected_prediction['horse'], 'model_sp': prices[model_key],
                       'model_won': int(model_key == winner_key),
                       'model_profit_1u': prices[model_key] * int(model_key == winner_key) - 1,
                       'favourite_horse': next(r['horse'] for r in starters if horse_key(r['horse']) == fav_key),
                       'favourite_sp': prices[fav_key], 'favourite_won': int(fav_key == winner_key),
                       'favourite_profit_1u': prices[fav_key] * int(fav_key == winner_key) - 1,
                       'winner': next(r['horse'] for r in starters if horse_key(r['horse']) == winner_key),
                       'field_size': len(starters), 'declared_field_size': len(rs),
                       'predicted_field_size': len(chosen)})
    return pd.DataFrame(output), pd.DataFrame(issues, columns=['race', 'reason', 'detail'])


def summary(df):
    n = len(df)
    if not n:
        return pd.DataFrame(columns=['Selection', 'Races', 'Wins', 'Win %', 'Avg SP', 'Profit $10', 'ROI %', 'Max drawdown $10'])
    out = []
    for prefix, label in [('model', 'Model #1'), ('favourite', 'SP favourite')]:
        profits = df.sort_values(['race_date', 'track', 'race_number'])[f'{prefix}_profit_1u'] * 10
        equity = profits.cumsum()
        peak = equity.cummax().clip(lower=0)
        out.append({'Selection': label, 'Races': n, 'Wins': int(df[f'{prefix}_won'].sum()),
                    'Win %': 100 * df[f'{prefix}_won'].mean(),
                    'Avg SP': df[f'{prefix}_sp'].mean(),
                    'Profit $10': profits.sum(), 'ROI %': 100 * profits.sum() / (10 * n),
                    'Max drawdown $10': (peak - equity).max()})
    return pd.DataFrame(out)


def render(db_rows):
    st.subheader('📈 v0.5.7c — Corrected Scratch-Aware Market Benchmark')
    st.caption('Read-only research: earliest saved prediction batch; top active starter versus lowest-SP favourite. '
               'No bet settlement, database writes or model changes.')
    st.warning('Starting prices are an SP benchmark, not verified executable pre-race odds. '
               'Official start times and feature provenance are not verified; do not treat this as confirmed forward performance.')
    if not st.button('Run read-only benchmark', key='v057c_run'):
        return
    try:
        with st.spinner('Loading predictions and official results...'):
            predictions = db_rows('race_predictions', order='predicted_at')
            results = db_rows('runner_results', order=None)
            matched, excluded = benchmark(predictions, results)
    except Exception as exc:
        st.error(f'Benchmark could not be completed: {exc}')
        return
    st.metric('Matched benchmark races', len(matched))
    if not matched.empty:
        st.metric('Original #1 scratched / active selection promoted', int(matched['original_model_rank_1_scratched'].sum()))
    st.metric('Excluded or unmatched races/records', len(excluded))
    if not matched.empty:
        st.dataframe(summary(matched).round(2), hide_index=True, use_container_width=True)
        st.dataframe(matched, hide_index=True, use_container_width=True)
        st.caption('Active-runner model selections may differ from the original #1 after a scratch. This is a retrospective active-runner comparison, not an executable pre-race strategy.')
        st.download_button('Download matched race benchmark CSV', matched.to_csv(index=False).encode(),
                           file_name='v057c_market_benchmark.csv', mime='text/csv')
    if not excluded.empty:
        st.subheader('Coverage and exclusions')
        counts = excluded['reason'].value_counts().rename_axis('Reason').reset_index(name='Count')
        st.dataframe(counts, hide_index=True, use_container_width=True)
        st.download_button('Download exclusions CSV', excluded.to_csv(index=False).encode(),
                           file_name='v057c_exclusions.csv', mime='text/csv')
