import pandas as pd
from v062a_relative_calibration import evaluate


def _data():
    shadows = [
        {'batch_id': 'a', 'race_date': '2026-11-15', 'track': 'Moe',
         'status': 'CAPTURED_SCHEDULE_ONLY_NOT_CERTIFIED',
         'field_size': 8, 'field_baseline_p': .2, 'score_gap_p': .6,
         'field_gap_interaction_p': .7},
        {'batch_id': 'b', 'race_date': '2026-11-16', 'track': 'Seymour',
         'status': 'CAPTURED_NO_CONFIDENCE_UNSCORED',
         'field_size': 9, 'field_baseline_p': None, 'score_gap_p': None,
         'field_gap_interaction_p': None},
    ]
    races = [
        {'batch_id': 'a', 'status': 'EVALUABLE_PROVISIONAL', 'top1_won': True},
        {'batch_id': 'b', 'status': 'EVALUABLE_PROVISIONAL', 'top1_won': False},
    ]
    return shadows, races


def test_prospective_only_and_null_exclusion():
    s, r = _data()
    detail, metrics, periods = evaluate(s, r)
    assert len(detail) == 1
    assert detail.iloc[0]['batch_id'] == 'a'
    assert len(metrics) == 3
    assert set(metrics['gate']) == {'INSUFFICIENT_FORWARD_SAMPLE'}


def test_brier_and_logloss_favour_better_probability():
    s, r = _data()
    _, metrics, _ = evaluate(s, r)
    b = metrics.set_index('candidate')
    assert b.loc['score_gap_p', 'brier_mean'] < b.loc['field_baseline_p', 'brier_mean']
    assert b.loc['score_gap_p', 'logloss_mean'] < b.loc['field_baseline_p', 'logloss_mean']


def test_duplicate_batch_excluded():
    s, r = _data()
    detail, _, _ = evaluate(s + [dict(s[0])], r)
    assert detail.empty


def test_incomplete_outcomes_excluded():
    s, r = _data()
    r[0]['status'] = 'INCOMPLETE'
    detail, _, _ = evaluate(s, r)
    assert detail.empty


def test_invalid_probabilities_excluded():
    s, r = _data()
    s[0]['score_gap_p'] = 1.0
    detail, _, _ = evaluate(s, r)
    assert detail.empty
