from v062b_metadata_audit import audit

def test_prospective_metadata_audit_preserves_source_and_excludes_runner_stat():
    historical=[{'id':'h1','race_date':'2026-10-01','track':'A',
                 'source_form_payload':{'race':{'distance':'1200m'}}}]
    prospective=[{'batch_id':'p1','race_date':'2026-11-15','track':'B',
                  'payload':{'form_response_raw':{
                      'race':{'distance':1400,'railPosition':'True'},
                      'runners':[{'name':'A','stats':{'distance':{'starts':5}}}]}}}]
    fields,coverage=audit(historical,prospective)
    race=fields[(fields.cohort=='PROSPECTIVE_AT_LOCK') & (fields.category=='distance')]
    assert len(race)==2
    assert race.candidate_race_level.sum()==1
    assert set(fields.cohort)=={'PROSPECTIVE_AT_LOCK','HISTORICAL_POST_RACE'}
    assert not coverage.empty

def test_missing_distance_is_reported_not_inferred():
    fields,coverage=audit([], [{'batch_id':'p','payload':{'form_response_raw':{'runners':[]}}}])
    assert len(fields)==1
    assert fields.iloc[0]['status']=='NO_MATCHING_METADATA_KEY'
