"""Stage 2.2 regression cases for point-in-time evidence and official source gate."""
import pandas as pd
import pytest
from v062b_stage22 import (
    archived_start_evidence,inspect_score_provenance,
    historical_raw_index,meeting_evidence,prospective_raw_index,
)
from v062b_stage2 import HISTORICAL,PROSPECTIVE


def test_positive_zero_and_unavailable_career_starts():
    assert archived_start_evidence({'stats':{'overall':{'starts':0}}})[:2]==(0,'PROVIDER_ZERO_STARTS')
    assert archived_start_evidence({'careerStarts':5})[:2]==(5,'PROVIDER_POSITIVE_STARTS')
    assert archived_start_evidence({'stats':{'overall':{'starts':0}},'careerStarts':3})[:2]==(None,'SOURCE_CONFLICT')
    assert archived_start_evidence({'stats':{'overall':{}}})[:2]==(None,'SOURCE_MISSING')
    assert archived_start_evidence({'starts':-1})[:2]==(None,'SOURCE_INVALID')
    assert archived_start_evidence(None)[:2]==(None,'SOURCE_MISSING')
    assert archived_start_evidence({'starts':True})[:2]==(None,'SOURCE_INVALID')


def test_missing_score_requires_original_archived_starts_not_assumed_debutant():
    evidence=pd.DataFrame([
        {'batch_id':'b','horse':'A','race_date':'2026-11-15','track':'Donald',
         'score_present':False},
        {'batch_id':'b','horse':'B','race_date':'2026-11-15','track':'Donald',
         'score_present':False},
        {'batch_id':'b','horse':'C','race_date':'2026-11-15','track':'Donald',
         'score_present':True}])
    raw={
        ('b','a'):{'stats':{'overall':{'starts':0}}},
        ('b','b'):{'stats':{'overall':{'starts':4}}},
        ('b','c'):{'stats':{'overall':{'starts':0}}},
    }
    audited=inspect_score_provenance(evidence,raw,PROSPECTIVE)
    assert audited.score_provenance_resolution.tolist()==[
        'UNSCORED_PROVIDER_ZERO_STARTS','UNSCORED_PREVIOUSLY_RACED','SCORE_PRESENT']
    assert audited.archived_career_starts.tolist()==[0,4,0]
    assert audited.archived_source_time_class.str.contains('UNCERTIFIED').all()
    assert 'POST_RACE_HISTORICAL_RETRIEVAL_NOT_PRE_RACE' in set(
        inspect_score_provenance(evidence,raw,HISTORICAL).archived_source_time_class)
    with pytest.raises(ValueError):
        inspect_score_provenance(evidence,raw,'UNSPECIFIED')


def test_duplicated_historical_source_not_attested():
    raw,duplicates=historical_raw_index([
        {'batch_id':'b','horse':'A','horse_key':'a','feature_payload':{'careerStarts':0}},
        {'batch_id':'b','horse':'A','horse_key':'a','feature_payload':{'careerStarts':5}}])
    assert ('b','a') in duplicates
    assert ('b','a') not in raw


def test_without_fixture_source_no_meeting_has_verified_class():
    frame=pd.DataFrame([{'race_date':'2026-11-15','track':'Sandown','batch_id':'b'}])
    result,problems=meeting_evidence(frame,pd.DataFrame())
    assert problems.empty
    assert result.iloc[0].meeting_class_evidence_status=='NO_MEETING_CLASS_SOURCE'
    assert pd.isna(result.iloc[0].meeting_class)
    assert result.iloc[0].classification_used_for_predictive_testing==False


def test_user_official_fixture_attestation_remains_uncertified():
    frame=pd.DataFrame([{'race_date':'2026-11-15','track':'Sandown','batch_id':'b'}])
    manifest=pd.DataFrame([{
        'race_date':'2026-11-15','track':'Sandown','meeting_class':'METRO',
        'official_source_url':'https://racing.example/meeting/2026-11-15',
        'official_source_statement':'Explicit metropolitan meeting designation',
        'reviewer_verified':'YES'}])
    result,problems=meeting_evidence(frame,manifest)
    assert problems.iloc[0]['issues']==''
    assert result.iloc[0].meeting_class=='METRO'
    assert result.iloc[0].meeting_class_evidence_status==(
        'USER_ATTESTED_OFFICIAL_SOURCE_NOT_INDEPENDENTLY_VALIDATED')
    assert not result.iloc[0].classification_used_for_predictive_testing


def test_invalid_or_conflicting_fixture_evidence_rejected():
    frame=pd.DataFrame([{'race_date':'2026-11-15','track':'Sandown','batch_id':'b'}])
    manifest=pd.DataFrame([{
        'race_date':'2026-11-15','track':'Sandown','meeting_class':'METRO',
        'official_source_url':'http://unknown.example',
        'official_source_statement':'Only a weak assertion',
        'reviewer_verified':'NO'}])
    result,issues=meeting_evidence(frame,manifest)
    assert pd.isna(result.iloc[0].meeting_class)
    assert result.iloc[0].meeting_class_evidence_status=='INCOMPLETE_OR_CONFLICTING_EVIDENCE'
    assert 'NO_OFFICIAL_HTTPS_CITATION' in issues.iloc[0]['issues']
    manifest['official_source_url']='https://racing.example/meeting'
    manifest['reviewer_verified']='YES'
    double=pd.concat([manifest,manifest],ignore_index=True)
    result,issues=meeting_evidence(frame,double)
    assert pd.isna(result.iloc[0].meeting_class)
    assert 'DUPLICATE_MEETING_ROW' in set('|'.join(issues.issues).split('|'))


def test_prospective_bad_archive_hash_rejects_metadata(monkeypatch):
    import v061_prospective as p
    monkeypatch.setattr(p,'digest',lambda data:'good-hash',raising=False)
    index,issues=prospective_raw_index([{
        'batch_id':'a','payload_sha256':'bad-hash',
        'payload':{'form_response_raw':{'runners':[{'name':'A','careerStarts':0}]}}}])
    assert index=={}
    assert set(issues.issue)=={'ARCHIVE_HASH_MISMATCH_NO_SOURCE_ACCESS'}
