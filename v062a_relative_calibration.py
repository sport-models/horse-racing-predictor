"""v0.6.2A — read-only prospective race-relative probability comparison.

Uses only v0.6.1m probabilities stored before scheduled race starts. No refitting,
no retroactive prediction generation, no alteration of frozen v0.2 rankings.
"""
from __future__ import annotations

import math
import pandas as pd

CANDIDATES = ('field_baseline_p', 'score_gap_p', 'field_gap_interaction_p')
MIN_RACES = 100
MIN_DATES = 15


def evaluate(shadows, races):
    """Strict one-to-one join of valid archived shadows to complete prospective outcomes."""
    s = pd.DataFrame(shadows).copy()
    r = pd.DataFrame(races).copy()
    columns = ['batch_id', 'race_date', 'track', 'field_size', 'top1_won',
               *CANDIDATES, 'brier_field_baseline_p', 'brier_score_gap_p',
               'brier_field_gap_interaction_p', 'logloss_field_baseline_p',
               'logloss_score_gap_p', 'logloss_field_gap_interaction_p']
    if s.empty or r.empty:
        return pd.DataFrame(columns=columns), pd.DataFrame(), pd.DataFrame()
    required_s = {'batch_id', 'race_date', 'status', *CANDIDATES}
    required_r = {'batch_id', 'status', 'top1_won'}
    if required_s - set(s.columns) or required_r - set(r.columns):
        raise ValueError('Missing required immutable shadow or result fields')
    # Duplicate identifiers are excluded rather than arbitrarily selected.
    s = s[~s['batch_id'].duplicated(keep=False)].copy()
    r = r[~r['batch_id'].duplicated(keep=False)].copy()
    s = s[s['status'].eq('CAPTURED_SCHEDULE_ONLY_NOT_CERTIFIED')].copy()
    r = r[r['status'].eq('EVALUABLE_PROVISIONAL')].copy()
    merged = s.merge(r[['batch_id', 'top1_won']], on='batch_id', how='inner', validate='one_to_one')
    if merged.empty:
        return pd.DataFrame(columns=columns), pd.DataFrame(), pd.DataFrame()
    y = pd.to_numeric(merged['top1_won'], errors='coerce')
    valid = y.isin([0, 1])
    for col in CANDIDATES:
        merged[col] = pd.to_numeric(merged[col], errors='coerce')
        valid &= merged[col].gt(0) & merged[col].lt(1)
    merged = merged.loc[valid].copy()
    if merged.empty:
        return pd.DataFrame(columns=columns), pd.DataFrame(), pd.DataFrame()
    y = pd.to_numeric(merged['top1_won'])
    for col in CANDIDATES:
        p = merged[col]
        merged['brier_' + col] = (p - y) ** 2
        merged['logloss_' + col] = -(y * p.map(math.log) + (1 - y) * p.map(lambda value: math.log1p(-value)))
    metrics = []
    for col in CANDIDATES:
        metrics.append({
            'candidate': col, 'races': len(merged),
            'distinct_race_dates': merged['race_date'].nunique(),
            'brier_mean': merged['brier_' + col].mean(),
            'logloss_mean': merged['logloss_' + col].mean(),
            'brier_delta_vs_field_baseline':
                merged['brier_' + col].mean() - merged['brier_field_baseline_p'].mean(),
            'gate': 'RESEARCH_ONLY_UNCERTIFIED' if
                len(merged) >= MIN_RACES and merged['race_date'].nunique() >= MIN_DATES
                else 'INSUFFICIENT_FORWARD_SAMPLE',
        })
    # Chronological date blocks, not random row splits. Descriptive only;
    # candidates were frozen before these outcomes and are never refitted here.
    days = sorted(merged['race_date'].astype(str).unique())
    cut = max(1, int(len(days) * 0.7))
    merged['evaluation_period'] = merged['race_date'].astype(str).apply(
        lambda d: 'earlier_forward' if d in set(days[:cut]) else 'later_forward')
    periods = []
    for period, group in merged.groupby('evaluation_period'):
        for col in CANDIDATES:
            periods.append({'period': period, 'candidate': col,
                            'races': len(group), 'dates': group['race_date'].nunique(),
                            'brier_mean': group['brier_' + col].mean(),
                            'logloss_mean': group['logloss_' + col].mean()})
    return merged[columns + ['evaluation_period']], pd.DataFrame(metrics), pd.DataFrame(periods)


def render(secret):
    import streamlit as st
    from v061_prospective import archive_client, _read_all, TABLE, evaluate_archives
    from v061m_capture import audit_archives

    st.subheader('🔬 v0.6.2A — Race-relative probability calibration (research only)')
    st.caption('Compares existing immutable at-lock v0.6.1m field, score-gap and '
               'field-gap probability estimates against completed forward outcomes. '
               'No fitting, reranking, betting changes or retrospective backfill.')
    if not st.button('Evaluate frozen forward calibration candidates', key='v062a_run'):
        return
    try:
        client = archive_client(secret)
        archives = _read_all(client, TABLE, order='created_at')
        shadows = audit_archives(archives)
        results = _read_all(client, 'runner_results')
        races = evaluate_archives(archives, results)['races']
        detail, summary, periods = evaluate(shadows, races)
    except Exception as exc:
        st.error('Read-only calibration audit failed: ' + str(exc))
        return
    st.metric('Eligible paired prospective races', len(detail))
    if detail.empty:
        st.info('No eligible fully scored forward shadows with complete outcomes yet. '
                'Unscored fields remain excluded; do not backfill.')
        return
    st.dataframe(summary, hide_index=True, use_container_width=True)
    st.dataframe(periods, hide_index=True, use_container_width=True)
    for label, frame in [('paired_races', detail), ('metrics', summary), ('date_periods', periods)]:
        st.download_button('Download v0.6.2A ' + label, frame.to_csv(index=False).encode(),
                           file_name='v062a_' + label + '.csv', mime='text/csv',
                           key='v062a_export_' + label)
    st.warning('PROMOTION GATE CLOSED: actual-off times and provider input-as-of '
               'evidence remain uncertified. Descriptive comparisons are not proof '
               'of improvement or permission to change frozen v0.2.')
