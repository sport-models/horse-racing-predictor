"""v0.6.2B Stage 2: read-only normalized barrier and barrier-deconfounded score-conditioned research.

Descriptive conditional randomisation NULL, not a model forecast. Conditioning on
observed winner's stored barrier-free score *tier* uses the outcome to define the comparison
stratum. It must NEVER be interpreted as a calibrated pre-race win probability
or as verified barrier predictive lift. Historical FormFav ranks were retrieved
after each race; those data are discovery only, not as-of verified evidence.

No side effects, no production scorer changes, no tuning on prospective rows.
"""
from __future__ import annotations

import math
import pandas as pd

HISTORICAL = 'HISTORICAL_RESEARCH_POST_RACE_RETRIEVAL'
PROSPECTIVE = 'PROSPECTIVE_LOCKED_SCHEDULE_ONLY'
ALLOWED = (HISTORICAL, PROSPECTIVE)
MIN_STARTERS = 100
MIN_RACE_DATES = 15


def _int(value):
    if isinstance(value, bool):
        return None
    try:
        n = float(value)
        return int(n) if math.isfinite(n) and n.is_integer() else None
    except (TypeError, ValueError, OverflowError):
        return None


def _zone(position):
    return ('INSIDE' if position < 1 / 3 else
            'MIDDLE' if position < 2 / 3 else 'OUTSIDE')


def _field_band(size):
    return 'SMALL_2_7' if size <= 7 else 'MEDIUM_8_11' if size <= 11 else 'LARGE_12_PLUS'


def _distance_band(value):
    try:
        d = float(value)
    except (TypeError, ValueError, OverflowError):
        return 'UNKNOWN_DISTANCE'
    if not math.isfinite(d) or not 600 <= d <= 5000:
        return 'UNKNOWN_DISTANCE'
    return 'SPRINT_LE_1200M' if d <= 1200 else 'MIDDLE_1201_1600M' if d <= 1600 else 'LONG_GT_1600M'


def _summarise(rows, segment, label, cohort):
    frame = rows.copy()
    frame['segment_type'] = label
    frame['segment_value'] = frame[segment].astype(str)
    dims = ['segment_type', 'segment_value', 'draw_zone']
    frame['matched_starter'] = frame['quality_matched_expected'].notna().astype(int)
    frame['matched_win'] = frame['won'] * frame['matched_starter']
    frame['expected_matched'] = frame['quality_matched_expected'].fillna(0.0)
    report = (frame.groupby(dims, dropna=False, sort=True)
              .agg(starters=('won', 'size'), wins=('won', 'sum'),
                   races=('batch_id', 'nunique'), race_dates=('race_date', 'nunique'),
                   uniform_expected_wins=('uniform_expected_win', 'sum'),
                   quality_matched_starters=('matched_starter', 'sum'),
                   quality_matched_wins=('matched_win', 'sum'),
                   quality_matched_expected_wins=('expected_matched', 'sum'))
              .reset_index())
    report['win_rate'] = report['wins'] / report['starters']
    report['excess_vs_uniform'] = report['wins'] - report['uniform_expected_wins']
    report['quality_matched_coverage_pct'] = 100 * report['quality_matched_starters'] / report['starters']
    report['excess_vs_quality_matched_null'] = (report['quality_matched_wins'] - report['quality_matched_expected_wins'])
    report.loc[report['quality_matched_starters'].eq(0),
               ['quality_matched_expected_wins', 'excess_vs_quality_matched_null']] = float('nan')
    report['cohort'] = cohort
    report['gate'] = report.apply(
        lambda row: ('EXPLORATORY_ONLY_NOT_VALIDATED' if
                     row.quality_matched_starters >= MIN_STARTERS and row.race_dates >= MIN_RACE_DATES
                     else 'INSUFFICIENT_INDEPENDENT_EXPOSURE'), axis=1)
    return report


def analyse(detail: pd.DataFrame, cohort: str):
    """Returns starter evidence, segment summaries and excluded-race diagnostics.

    Quality is only the *original stored v0.2 model score after removing its explicit barrier term*, not starting price,
    winning odds, finishing position, or post-result re-ranking. Prospective
    archive scores are unchanged; historical scores are retrospective proxies.
    All report strata are computed separately for one explicit cohort.
    """
    if cohort not in ALLOWED:
        raise ValueError('Explicit historical or prospective research cohort required')
    columns = ['batch_id', 'race_date', 'track', 'race_number', 'horse',
               'barrier', 'actual_starters', 'won', 'distance_m']
    if detail is None or detail.empty:
        return pd.DataFrame(), pd.DataFrame(), pd.DataFrame(columns=['batch_id', 'issue'])
    missing = set(columns).difference(detail.columns)
    if missing:
        raise ValueError('Missing original field(s): ' + ', '.join(sorted(missing)))
    if 'cohort' in detail.columns and cohort == HISTORICAL:
        if not detail['cohort'].eq(HISTORICAL).all():
            raise ValueError('Historical research cohort contains another source; no pooling')
    if 'evidence_status' in detail.columns:
        expected = ('POST_RACE_RETRIEVAL_NOT_POINT_IN_TIME' if cohort == HISTORICAL else
                    'SCHEDULE_ONLY_NOT_CERTIFIED')
        if not detail['evidence_status'].eq(expected).all():
            raise ValueError('Unexpected evidence class; historical and prospective data cannot be pooled')
    score_field = 'research_model_score' if cohort == HISTORICAL else 'locked_score'
    rows, issues = [], []
    for batch_id, g in detail.groupby('batch_id', dropna=False, sort=False):
        r = g.copy()
        n = len(r)
        barriers = r['barrier'].map(_int)
        counts = r['actual_starters'].map(_int)
        winners = r['won'].map(_int)
        bad = []
        if n < 2 or counts.nunique() != 1 or (counts != n).any():
            bad.append('INCOMPLETE_ACTUAL_STARTER_FIELD')
        if barriers.isna().any() or (barriers < 1).any() or (barriers > 30).any() or barriers.nunique() != n:
            bad.append('INVALID_OR_DUPLICATE_STARTER_BARRIERS')
        if winners.isna().any() or (~winners.isin([0, 1])).any() or winners.sum() != 1:
            bad.append('NO_UNIQUE_FINISHING_WINNER')
        if r[['race_date', 'track', 'race_number']].drop_duplicates().shape[0] != 1:
            bad.append('INCONSISTENT_RACE_IDENTITY')
        if len(r['horse'].dropna().astype(str).str.casefold().unique()) != n:
            bad.append('DUPLICATE_OR_MISSING_HORSE_IDENTITY')
        if bad:
            issues.extend({'batch_id': batch_id, 'issue': item} for item in bad)
            continue
        r['won'] = winners.to_numpy()
        r['barrier'] = barriers.to_numpy()
        # Draw position of each *actual starter*, not raw gate numbers. This
        # handles scratched gates and different field sizes consistently.
        r['draw_order'] = r['barrier'].rank(method='first').astype(int)
        r['draw_percentile'] = (r['draw_order'] - .5) / n
        r['draw_zone'] = r['draw_percentile'].map(_zone)
        r['field_band'] = _field_band(n)
        r['distance_band'] = r['distance_m'].map(_distance_band)
        r['uniform_expected_win'] = 1 / n
        r['quality_matched_expected'] = float('nan')
        r['quality_tier'] = None
        r['barrier_free_quality_score'] = float('nan')
        r['score_source'] = ('HISTORICAL_POST_RACE_RETRIEVED_MODEL_SCORE' if cohort == HISTORICAL
                             else 'IMMUTABLE_PRE_SCHEDULE_LOCKED_MODEL_SCORE')
        # Frozen v0.2 score contains barrier: 0.10 * context,
        # context=(barrier_score+weight_score)/2, multiplied by 100.
        # Subtract exactly 5 * barrier_score points to avoid adjusting for
        # the very effect being studied. No outcome-based score calculation.
        if score_field not in r:
            issues.append({'batch_id': batch_id, 'issue': 'NO_STORED_MODEL_SCORE_COLUMN'})
        else:
            scores = pd.to_numeric(r[score_field], errors='coerce')
            barrier_scores = (1 - (barriers - 1) / 16).clip(0, 1)
            quality = scores - 5.0 * barrier_scores
            if quality.isna().any() or not quality.map(math.isfinite).all():
                issues.append({'batch_id': batch_id, 'issue': 'INVALID_OR_MISSING_STORED_MODEL_SCORE'})
            else:
                r['barrier_free_quality_score'] = quality
                # Stable order independent of stored row position.
                # Ties that span terciles are ambiguous and excluded.
                order = r.assign(_score=quality, _name=r['horse'].astype(str).str.casefold())
                order = order.sort_values(['_score','_name'], ascending=[False,True], kind='stable')
                tier_values = [_zone((i + .5) / n) for i in range(n)]
                ties_split = any((order['_score'].iloc[i] == order['_score'].iloc[i-1]
                                  and tier_values[i] != tier_values[i-1])
                                 for i in range(1, n))
                if ties_split:
                    issues.append({'batch_id': batch_id, 'issue': 'QUALITY_SCORE_TIE_SPANS_TERCILES'})
                else:
                    tiers = pd.Series(tier_values, index=order.index)
                    r['quality_tier'] = tiers.reindex(r.index)
                    winning_tier = r.loc[r['won'].eq(1), 'quality_tier'].iloc[0]
                    in_tier = r['quality_tier'].eq(winning_tier)
                    r.loc[in_tier, 'quality_matched_expected'] = 1 / int(in_tier.sum())
                    r.loc[~in_tier, 'quality_matched_expected'] = 0.0
        rows.append(r)
    if not rows:
        return pd.DataFrame(), pd.DataFrame(), pd.DataFrame(issues, columns=['batch_id', 'issue'])
    evidence = pd.concat(rows, ignore_index=True)
    reports = []
    evidence['all_tracks'] = 'ALL_TRACKS'
    evidence['track_distance'] = evidence['track'].astype(str) + ' | ' + evidence['distance_band']
    for dimension, label in [('all_tracks', 'OVERALL'), ('field_band', 'FIELD_SIZE'),
                             ('distance_band', 'DISTANCE'), ('track', 'TRACK'),
                             ('track_distance', 'TRACK_DISTANCE')]:
        reports.append(_summarise(evidence, dimension, label, cohort))
    summary = pd.concat(reports, ignore_index=True)
    return evidence.drop(columns=['all_tracks', 'track_distance']), summary, pd.DataFrame(issues, columns=['batch_id', 'issue'])


def render(secret):
    """Streamlit on-demand; never writes research rows to Supabase."""
    import streamlit as st
    st.subheader('🔬 v0.6.2B Stage 2 — Barrier draw / barrier-free score comparison')
    st.caption('Read-only: field-size-normalised actual starter draw terciles. '
               'A quality-score-conditioned permutation null compares winners within the '
               'same debarriered v0.2 score tercile. This is an association diagnostic, '
               'NOT calibrated win probabilities or demonstrated predictive lift.')
    source = st.radio('Research cohort (never pooled)',
                      ['Historical discovery (post-race FormFav)',
                       'Prospective archived (schedule-only evidence)'],
                      key='v062b_stage2_source')
    if not st.button('Run Stage 2 read-only analysis', key='v062b_stage2_run'):
        return
    try:
        if source.startswith('Historical'):
            from v061b_historical import server_db, read_all, RACES, FEATURES, RESULTS
            from v062b_historical import build_historical
            db = server_db(secret)
            history, _, prior_issues, _ = build_historical(
                read_all(db, RACES, '*', 'race_date'),
                read_all(db, FEATURES, 'batch_id,horse,horse_key,model_rank,model_score,barrier'),
                read_all(db, RESULTS, 'batch_id,result_payload,result_sha256,actual_starters'))
            cohort = HISTORICAL
            detail = history
        else:
            from v061_prospective import archive_client, _read_all, TABLE
            from v062b_barrier_lab import build
            client = archive_client(secret)
            detail, _, prior_issues = build(
                _read_all(client, TABLE, order='created_at'),
                _read_all(client, 'runner_results'))
            cohort = PROSPECTIVE
        evidence, summary, issues = analyse(detail, cohort)
    except Exception as exc:
        st.error('Stage 2 read-only audit failed: ' + str(exc))
        st.exception(exc)
        return
    c1, c2, c3 = st.columns(3)
    c1.metric('Valid starter observations', len(evidence))
    c2.metric('Evaluable races', evidence.batch_id.nunique() if not evidence.empty else 0)
    c3.metric('Quality-matched starter observations',
              int(evidence.quality_matched_expected.notna().sum()) if not evidence.empty else 0)
    if not evidence.empty:
        st.markdown('**Aggregate draw terciles — unadjusted vs quality-matched null**')
        st.dataframe(summary[summary.segment_type.eq('OVERALL')], hide_index=True,
                     use_container_width=True)
        st.markdown('**Segments: field size, distance, venue**')
        st.dataframe(summary[~summary.segment_type.eq('OVERALL')], hide_index=True,
                     use_container_width=True)
    st.metric('Earlier Stage 1 exclusions / issue rows', len(prior_issues))
    if not prior_issues.empty:
        with st.expander('Stage 1 eligibility exclusions — retained separately'):
            st.dataframe(prior_issues, hide_index=True, use_container_width=True)
            st.download_button('Export Stage 1 exclusions for this cohort',
                               prior_issues.to_csv(index=False).encode('utf-8'),
                               file_name='v062b_stage2_prior_exclusions_' +
                               ('historical' if cohort == HISTORICAL else 'prospective') + '.csv',
                               mime='text/csv', key='v062b_stage2_prior_exclusions')
    if not issues.empty:
        st.warning('Some races excluded or lack valid original scores for quality matching.')
        st.dataframe(issues, hide_index=True, use_container_width=True)
    for name, data in [('stage2_starters', evidence), ('stage2_segments', summary),
                       ('stage2_issues', issues)]:
        st.download_button('Export ' + name, data.to_csv(index=False).encode('utf-8'),
                           file_name='v062b_' + name + '_' +
                           ('historical' if cohort == HISTORICAL else 'prospective') + '.csv',
                           mime='text/csv', key='v062b_' + name)
    st.warning('Not approved for production: historical score inputs were retrieved '
               'AFTER racing; prospective actual-off and provider-as-of timing remain '
               'uncertified. Quality-matched null conditions on the winning quality tier and must '
               'not be interpreted as a pre-race forecast. No p-values, betting '
               'recommendations, or feature promotions are produced.')
