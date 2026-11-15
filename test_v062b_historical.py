import pandas as pd
from v062b_historical import build_historical, COHORT


def test_historical_cohort_separated_and_missing_distance_preserved(monkeypatch):
    import v061b_historical as h
    monkeypatch.setattr(h,'build_reports',lambda b,f,r:{
        'races':pd.DataFrame([{'batch_id':'a','status':'EVALUABLE_RETROSPECTIVE_RESEARCH','actual_starters':2}])})
    monkeypatch.setattr(h,'digest',lambda p:'ok')
    batches=[{'id':'a','race_date':'2026-10-01','track':'Seymour','race_number':1,'source_form_payload':{'distance':'1200m'}}]
    features=[{'batch_id':'a','horse':'A','horse_key':'a','barrier':1},
              {'batch_id':'a','horse':'B','horse_key':'b','barrier':2}]
    results=[{'batch_id':'a','result_sha256':'ok','result_payload':{
        'placings':[{'name':'A','position':1,'scratched':False},
                    {'name':'B','position':2,'scratched':False}]}}]
    detail,summary,issues,_=build_historical(batches,features,results)
    assert len(detail)==2
    assert (detail.distance_m==1200).all()
    assert set(detail.cohort)=={COHORT}
    assert summary.expected_wins_uniform.sum()==1
    assert set(summary.gate)=={'INSUFFICIENT_HISTORICAL_EXPOSURE'}
    features[1]['barrier']=1
    detail,_,issues,_=build_historical(batches,features,results)
    assert detail.empty
    assert 'INCOMPLETE_OR_DUPLICATE_BARRIER_EXPOSURE' in set(issues.issue)
