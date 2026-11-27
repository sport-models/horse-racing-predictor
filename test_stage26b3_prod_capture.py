from datetime import datetime, timezone, timedelta
import hashlib
import json
from unittest.mock import patch
import pytest
import v062b_stage26b3_capture as capture
import v062b_stage26b3_lock_hook as hook


def example(now=None, starts=0):
    t=now or datetime(2026,12,1,0,0,tzinfo=timezone.utc)
    def iso(dt): return dt.isoformat()
    raw={'runners':[{'name':'Debutant','stats':{'overall':{'starts':starts}},'barrier':3,'jockey':'J','trainer':'T'}, {'name':'Other','barrier':5,'form':'123','stats':{'overall':{'starts':3}}}]}
    payload={'form_response_raw':raw,'form_response_sha256':capture.sha(raw),'rankings':[{'horse':'Debutant','rank':2,'score':None},{'horse':'Other','rank':1,'score':3.0}], 'model_version':'v0.2', 'original_prediction_batch_id':'00000000-0000-4000-8000-000000000001', 'source_times':{'form_response_received_utc':iso(t-timedelta(minutes=5)),'ranking_generated_utc':iso(t-timedelta(minutes=4))}}
    archive={'payload':payload,'payload_sha256':capture.sha(payload),'batch_id':payload['original_prediction_batch_id'], 'race_date':'2026-12-01','track':'Example','race_number':1, 'scheduled_start_utc':iso(t+timedelta(hours=2)), 'created_at':iso(t-timedelta(minutes=3)), 'model_version':'v0.2'}
    return archive,t

class Client:
    def __init__(self):self.records=[]
    def table(self,name):
        assert name == 'v062b_stage26b3_shadow_captures';return self
    def insert(self,record):self.records.append(record);return self
    def execute(self):return type('Response',(),{'data':[self.records[-1]]})()

def test_valid_capture_no_model_scores_or_results():
    a,t=example()
    body=capture.build(a,now=t)
    assert body['score_policy']=='NO_SCORING_CAPTURE_ONLY'
    assert body['no_odds_or_results_used']
    assert all('experimental_barrier_proxy_not_probability' not in x for x in body['runners'])
    assert body['runners'][0]['identity_status'].startswith('PROVIDER_REPORTED_ZERO')
    assert not body['runners'][0]['production_prediction_eligible']

def test_unknown_does_not_become_debutant():
    a,t=example();a['payload']['form_response_raw']['runners'][0].pop('stats')
    a['payload']['form_response_sha256']=capture.sha(a['payload']['form_response_raw']);a['payload_sha256']=capture.sha(a['payload'])
    assert capture.build(a,now=t)['runners'][0]['identity_status']=='UNKNOWN_STARTS'

def test_fail_closed_expired_schedule():
    a,t=example()
    with pytest.raises(ValueError,match='after scheduled start'):
        capture.build(a,now=t+timedelta(hours=3))

def test_fail_closed_tampered_payload():
    a,t=example();a['payload']['rankings'][0]['score']=77
    with pytest.raises(ValueError,match='digest mismatch'):capture.build(a,now=t)

def test_insert_once_and_evidence_hash():
    a,t=example();client=Client();status,message=hook.attempt(client,a,now=t)
    assert status=='CAPTURED'
    assert len(client.records)==1
    assert client.records[0]['capture_sha256']==capture.sha(client.records[0]['payload'])
    assert client.records[0]['source_archive_sha256']==a['payload_sha256']

def test_failure_does_not_raise_or_modify_original():
    a,t=example();orig=json.dumps(a,sort_keys=True)
    status,detail=hook.attempt(Client(),a,now=t+timedelta(days=1))
    assert status=='FAILED' and 'ValueError' in detail
    assert json.dumps(a,sort_keys=True)==orig

def test_both_lock_paths_and_no_research_dashboard_in_production():
    from pathlib import Path
    app=Path('app.py').read_text();bulk=Path('v061v_bulk_capture.py').read_text()
    assert 'v062b_stage26b3_lock_hook.attempt' in app
    assert 'v062b_stage26b3_lock_hook.attempt' in bulk
    assert 'receipt = prospective.archive_locked_batch(' in bulk
    assert "'Shadow': shadow_status" in bulk
    assert "v062b_stage26b3.render(" not in app
    assert 'v062b_stage26b3_capture' not in app
