from datetime import timedelta
import pytest
from v045_abandoned import validate_manual_abandonment, race_id, melbourne_today
# Bind real function at collection time before legacy tests stub sys.modules.
from v061_prospective import evaluate_archives


def _race():
    day=melbourne_today().isoformat()
    race=(day,'Donald',5)
    key=race_id({'race_date':day,'track':'Donald','race_number':5})
    return race,key


def test_valid_single_confirmation_without_placings_or_fabricated_evidence():
    race,key=_race()
    row=validate_manual_abandonment(race,{key},set(),set())
    assert row['outcome']=='ABANDONED'
    assert row['reviewed_by']=='USER_CONFIRMED_OFFICIAL_ABANDONMENT'
    assert row['official_evidence_url'] is None
    assert row['evidence_note'] is None
    assert 'finishing_position' not in row
    assert 'horse' not in row
    assert row['track_key']=='donald'


def test_no_prediction_cannot_manually_create_race():
    race,key=_race()
    with pytest.raises(ValueError,match='prediction'):
        validate_manual_abandonment(race,set(),set(),set())


def test_existing_results_or_status_block_duplicate():
    race,key=_race()
    for finals,prior in [({key},set()),(set(),{key})]:
        with pytest.raises(ValueError):
            validate_manual_abandonment(race,{key},finals,prior)


def test_future_race_cannot_be_marked_abandoned():
    race,key=_race()
    future=((melbourne_today()+timedelta(days=1)).isoformat(),race[1],race[2])
    next_key=race_id({'race_date':future[0],'track':future[1],'race_number':future[2]})
    with pytest.raises(ValueError,match='future'):
        validate_manual_abandonment(future,{next_key},set(),set())


def test_prospective_abandoned_race_has_no_win_loss_or_roi():
    from test_dead_heat_results import _archive
    archive=_archive()
    marker={'race_date':archive['race_date'],'track':archive['track'],
            'race_number':archive['race_number'],'outcome':'ABANDONED',
            'official_evidence_url':'https://official.example/notice'}
    report=evaluate_archives([archive],[],[marker])
    race=report['races'].iloc[0]
    summary=report['summary'].iloc[0]
    assert race.status=='ABANDONED_OFFICIAL_MANUAL_VERIFICATION'
    assert race.top1_won is None
    assert race.top3_hit is None
    assert race.abandonment_evidence_url=='https://official.example/notice'
    assert int(summary.abandoned_races)==1
    assert int(summary.result_complete_provisional)==0
    assert not report['runner_audit'].shape[0]
    assert 'ABANDONED_EXCLUDED_FROM_WIN_AND_ROI_METRICS' in set(report['missing_evidence'].reason)


def test_conflict_between_abandoned_race_and_final_results_blocks_metrics():
    from test_dead_heat_results import _archive,_results
    archive=_archive()
    marker={'race_date':archive['race_date'],'track':archive['track'],
            'race_number':archive['race_number'],'outcome':'ABANDONED',
            'official_evidence_url':'https://official.example/notice'}
    official=_results(winners=('Horse A',))
    for row in official:
        row.update(race_date=archive['race_date'],track=archive['track'],
                   race_number=archive['race_number'])
    report=evaluate_archives([archive],official,[marker])
    race=report['races'].iloc[0]
    assert race.status=='CONFLICT_ABANDONED_AND_RUNNER_RESULTS'
    assert race.top1_won is None
    assert report['summary'].iloc[0].abandoned_result_conflicts==1
    assert report['summary'].iloc[0].result_complete_provisional==0
