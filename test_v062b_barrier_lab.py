import pandas as pd
from v062b_barrier_lab import _barrier, _distance, _unique_field, build


def test_barrier_rejects_invalid_and_boolean():
    assert _barrier(3) == 3
    assert _barrier('7') == 7
    assert _barrier(True) is None
    assert _barrier(0) is None
    assert _barrier(2.5) is None


def test_distance_requires_valid_unambiguous_metadata():
    assert _distance('1200') == 1200
    assert _distance('1200m') == 1200
    assert _distance('1,200m') is None
    assert _unique_field({'race':{'distance':1200}}, ('distance',), _distance) == 1200
    assert _unique_field({'distance':1200,'race':{'distance':1400}}, ('distance',), _distance) is None


def test_exposure_requires_complete_unique_starter_barriers(monkeypatch):
    import v061_prospective as core
    monkeypatch.setattr(core,'evaluate_archives',lambda a,r:{
        'races':pd.DataFrame([{'batch_id':'a','status':'EVALUABLE_PROVISIONAL','field_size':2}])}, raising=False)
    monkeypatch.setattr(core,'digest',lambda payload:'verified', raising=False)
    monkeypatch.setattr(core,'canonical',lambda name: str(name or '').strip().lower(), raising=False)
    monkeypatch.setattr(core,'_locked_runner_metadata',lambda raw:({},set()), raising=False)
    monkeypatch.setattr(core,'race_key',lambda row:('race',), raising=False)
    monkeypatch.setattr(core,'_result_index',lambda rows:{('race',):rows}, raising=False)
    archives=[{'batch_id':'a','payload_sha256':'verified','race_date':'2026-11-15',
               'track':'Seymour','race_number':1,'payload':{
                   'form_response_raw':{'distance':1200},
                   'rankings':[{'horse':'A','rank':1,'score':74.6,'barrier':1},
                               {'horse':'B','rank':2,'score':68.4,'barrier':2}]}}]
    results=[{'horse':'A','scratched':False,'finishing_position':1},
             {'horse':'B','scratched':False,'finishing_position':2}]
    detail, summary, issues=build(archives,results)
    assert len(detail)==2 and len(summary)==2
    assert set(detail.locked_score)=={74.6,68.4}
    assert summary.wins.sum()==1
    assert summary.expected_wins_uniform.sum()==1
    assert set(summary.gate)=={'INSUFFICIENT_EXPOSURE'}
    archives[0]['payload']['rankings'][1]['barrier']=1
    detail, summary, issues=build(archives,results)
    assert detail.empty
    assert 'INCOMPLETE_OR_DUPLICATE_BARRIER_EXPOSURE' in set(issues.issue)


def test_prospective_joint_first_not_counted_as_one_winner_barrier_race(monkeypatch):
    import v061_prospective as core
    monkeypatch.setattr(core,'evaluate_archives',lambda a,r:{
        'races':pd.DataFrame([{'batch_id':'a','status':'EVALUABLE_PROVISIONAL',
                               'field_size':2,'joint_first_count':2}])},raising=False)
    monkeypatch.setattr(core,'digest',lambda p:'verified',raising=False)
    monkeypatch.setattr(core,'canonical',lambda v:str(v or '').lower(),raising=False)
    monkeypatch.setattr(core,'_locked_runner_metadata',lambda raw:({},set()),raising=False)
    monkeypatch.setattr(core,'race_key',lambda row:('race',),raising=False)
    monkeypatch.setattr(core,'_result_index',lambda rows:{('race',):rows},raising=False)
    archives=[{'batch_id':'a','race_date':'2026-11-15','track':'Donald',
               'race_number':5,'payload_sha256':'verified','payload':{
               'form_response_raw':{'distance':'1200m'},
               'rankings':[{'horse':'A','rank':1,'barrier':1},
                           {'horse':'B','rank':2,'barrier':2}]}}]
    results=[{'horse':'A','scratched':False,'finishing_position':1},
             {'horse':'B','scratched':False,'finishing_position':1}]
    starters,summary,issues=build(archives,results)
    assert starters.empty
    assert summary.empty
    assert 'EXCLUDED_JOINT_FIRST_FROM_SINGLE_WINNER_BARRIER_RESEARCH' in set(issues.issue)
