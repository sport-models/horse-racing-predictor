import math
import pandas as pd
import pytest
from v062b_stage2 import analyse, HISTORICAL, PROSPECTIVE


def race(bid='a', cohort=PROSPECTIVE, n=6, date='2026-11-12', track='Seymour', winner=1, ranks=None, barriers=None):
    ranks = ranks or list(range(1, n + 1))
    barriers = barriers or list(range(1, n + 1))
    rows = []
    for idx in range(n):
        row = {
            'batch_id': bid, 'race_date': date, 'track': track,
            'race_number': 1, 'horse': 'Horse %d' % idx,
            'barrier': barriers[idx], 'distance_m': 1200,
            'actual_starters': n, 'won': int(idx == winner),
            'evidence_status': ('POST_RACE_RETRIEVAL_NOT_POINT_IN_TIME'
                                if cohort == HISTORICAL else 'SCHEDULE_ONLY_NOT_CERTIFIED'),
        }
        if cohort == PROSPECTIVE:
            row['locked_rank'] = ranks[idx]
        else:
            row['cohort'] = HISTORICAL
            row['research_rank'] = ranks[idx]
        rows.append(row)
    return pd.DataFrame(rows)


def test_normalised_draw_and_matched_null_have_one_winner_per_race():
    evidence, summary, issues = analyse(race(), PROSPECTIVE)
    assert issues.empty
    assert evidence['draw_zone'].tolist() == ['INSIDE', 'INSIDE', 'MIDDLE',
                                              'MIDDLE', 'OUTSIDE', 'OUTSIDE']
    assert math.isclose(evidence['uniform_expected_win'].sum(), 1)
    assert math.isclose(evidence['rank_matched_expected'].sum(), 1)
    assert evidence.loc[evidence.draw_zone.eq('INSIDE'), 'rank_matched_expected'].sum() == 1
    assert summary.loc[summary.segment_type.eq('OVERALL'), 'wins'].sum() == 1
    assert set(summary.gate) == {'INSUFFICIENT_INDEPENDENT_EXPOSURE'}


def test_rank_control_changes_null_when_good_horses_draw_outside():
    evidence, summary, issues = analyse(
        race(barriers=[1, 5, 2, 3, 4, 6], winner=0), PROSPECTIVE)
    assert issues.empty
    top = evidence[evidence['quality_tier'].eq('INSIDE')]
    assert math.isclose(top.rank_matched_expected.sum(), 1)
    assert math.isclose(top[top.draw_zone.eq('INSIDE')].rank_matched_expected.sum(), 0.5)
    assert math.isclose(top[top.draw_zone.eq('OUTSIDE')].rank_matched_expected.sum(), 0.5)
    inside = summary[(summary.segment_type == 'OVERALL') & (summary.draw_zone == 'INSIDE')].iloc[0]
    assert math.isclose(inside.excess_vs_rank_matched_null, 0.5)


def test_no_unverified_quality_imputation_or_hindsight_rank_replacement():
    source = race()
    source.loc[2, 'locked_rank'] = None
    evidence, summary, issues = analyse(source, PROSPECTIVE)
    assert len(evidence) == 6
    assert evidence['rank_matched_expected'].isna().all()
    assert math.isclose(evidence.uniform_expected_win.sum(), 1)
    assert 'INVALID_OR_MISSING_STORED_MODEL_RANK' in set(issues.issue)
    assert (summary.rank_matched_coverage_pct == 0).all()
    assert summary.excess_vs_rank_matched_null.isna().all()


def test_historical_kept_separate_and_never_certified_as_at_lock():
    source = race(cohort=HISTORICAL)
    detail, summary, issues = analyse(source, HISTORICAL)
    assert issues.empty
    assert set(detail.rank_source) == {'HISTORICAL_POST_RACE_RETRIEVED_MODEL_RANK'}
    assert set(summary.cohort) == {HISTORICAL}
    with pytest.raises(ValueError, match='evidence class'):
        analyse(source, PROSPECTIVE)


def test_mixed_evidence_rejected_even_with_cohort_parameter():
    future = race(bid='future')
    old = race(bid='hist', cohort=HISTORICAL)
    with pytest.raises(ValueError, match='evidence class'):
        analyse(pd.concat([future, old], ignore_index=True), PROSPECTIVE)


def test_winner_and_barrier_validation_excludes_entire_bad_races():
    dup = race(bid='dup')
    dup.loc[1, 'barrier'] = 1
    badwinner = race(bid='no_winner')
    badwinner['won'] = 0
    healthy = race(bid='healthy')
    ev, _, issues = analyse(pd.concat([dup, badwinner, healthy]), PROSPECTIVE)
    assert set(ev.batch_id) == {'healthy'}
    assert {'INVALID_OR_DUPLICATE_STARTER_BARRIERS', 'NO_UNIQUE_FINISHING_WINNER'} <= set(issues.issue)


def test_actual_starter_draws_handle_gaps_from_scratches():
    ev, _, issues = analyse(race(barriers=[2, 3, 7, 8, 12, 15]), PROSPECTIVE)
    assert issues.empty
    assert ev.draw_order.tolist() == [1, 2, 3, 4, 5, 6]
    assert (ev.draw_percentile.between(0, 1)).all()


def test_distance_bands_and_field_bands_include_all_valid_entries():
    small = race('small', n=6)
    large = race('large', n=12)
    large['distance_m'] = 2000
    ev, summary, issues = analyse(pd.concat([small, large]), PROSPECTIVE)
    assert issues.empty
    assert set(ev.field_band) == {'SMALL_2_7', 'LARGE_12_PLUS'}
    assert set(ev.distance_band) == {'SPRINT_LE_1200M', 'LONG_GT_1600M'}
    for group in ['OVERALL', 'FIELD_SIZE', 'DISTANCE', 'TRACK', 'TRACK_DISTANCE']:
        s = summary[summary.segment_type.eq(group)]
        assert s.starters.sum() == 18
        assert math.isclose(s.uniform_expected_wins.sum(), 2)
        assert math.isclose(s.rank_matched_expected_wins.sum(), 2)


def test_none_or_empty_inputs_are_safe():
    ev, summary, issues = analyse(pd.DataFrame(), PROSPECTIVE)
    assert ev.empty and summary.empty and issues.empty
    with pytest.raises(ValueError, match='research cohort'):
        analyse(race(), 'UNKNOWN')


def test_segments_do_not_claim_significance_from_three_dates():
    samples = []
    for k in range(50):
        samples.append(race(str(k), date=f'2026-11-{12 + (k % 3):02}'))
    _, summary, _ = analyse(pd.concat(samples), PROSPECTIVE)
    assert (summary.gate == 'INSUFFICIENT_INDEPENDENT_EXPOSURE').all()
