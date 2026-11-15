"""Stage 2.1 regression tests: separate cohorts, venue proxies, missing scores."""
import pandas as pd
import pytest
from v062b_stage2 import HISTORICAL, PROSPECTIVE, analyse
from v062b_stage21 import (
    venue_class, missing_score_reason, build_stage21, dead_heat_manifest,
)


def _race(batch='a',cohort=PROSPECTIVE,venue='Sandown',day='2026-11-15',n=6):
    data=[]
    for i in range(n):
        barrier=i+1
        raw=55.0-i*4+5*max(0,min(1,1-i/16))
        row={'batch_id':batch,'race_date':day,'track':venue,'race_number':1,
             'horse':f'Horse {i}','barrier':barrier,'distance_m':1200,
             'actual_starters':n,'won':int(i==0),
             'evidence_status':'POST_RACE_RETRIEVAL_NOT_POINT_IN_TIME' if cohort==HISTORICAL
                              else 'SCHEDULE_ONLY_NOT_CERTIFIED'}
        if cohort==HISTORICAL:
            row.update(cohort=HISTORICAL,research_rank=i+1,
                       research_model_score=raw,research_career_starts=5)
        else:
            row.update(locked_rank=i+1,locked_score=raw,locked_scoring_status='SCORED')
        data.append(row)
    return pd.DataFrame(data)


def _analyse(frame,cohort):
    evidence,_,issues=analyse(frame,cohort)
    assert issues.empty
    return build_stage21(evidence,cohort)


def test_venue_mapping_is_conservative():
    assert venue_class('Sandown')=='METRO_VENUE_PROXY'
    assert venue_class('Royal Randwick')=='METRO_VENUE_PROXY'
    assert venue_class('Donald')=='COUNTRY_VENUE_PROXY'
    assert venue_class('Moe')=='COUNTRY_VENUE_PROXY'
    assert venue_class('Wyong')=='PROVINCIAL_VENUE_PROXY'
    assert venue_class('Toowoomba')=='PROVINCIAL_VENUE_PROXY'
    assert venue_class('Imaginary Racetrack')=='UNCLASSIFIED_VENUE'
    assert venue_class('Mornington')=='UNCLASSIFIED_VENUE'


def test_separate_metro_country_provincial_unknown_and_gates():
    source=pd.concat([_race('metro',venue='Sandown'),
                      _race('country',venue='Donald'),
                      _race('provincial',venue='Wyong'),
                      _race('unknown',venue='Unknown Test Course')],ignore_index=True)
    runners,seg,venues,reason,races,hyp=_analyse(source,PROSPECTIVE)
    assert len(runners)==24
    assert len(races)==4 and len(hyp)==4
    assert set(runners.venue_class)=={
        'METRO_VENUE_PROXY','COUNTRY_VENUE_PROXY',
        'PROVINCIAL_VENUE_PROXY','UNCLASSIFIED_VENUE'}
    assert not runners.venue_class_is_meeting_certified.any()
    assert set(seg.gate)=={'INSUFFICIENT_INDEPENDENT_EXPOSURE'}
    overall=seg[seg.segment_type.eq('VENUE_CLASS')]
    assert (overall.groupby('venue_class').wins.sum()==1).all()
    assert (overall.groupby('venue_class').expected_wins_uniform.sum().round(9)==1).all()
    assert set(hyp.gate)=={'NO_PREDICTIVE_INFERENCE'}


def test_null_score_with_zero_starts_kept_in_uniform_not_quality():
    source=_race(cohort=HISTORICAL)
    source.loc[2,'research_model_score']=None
    source.loc[2,'research_career_starts']=0
    evidence,_,issues=analyse(source,HISTORICAL)
    assert 'INVALID_OR_MISSING_STORED_MODEL_SCORE' in set(issues.issue)
    runners,seg,venues,reasons,races,hyp=build_stage21(evidence,HISTORICAL)
    assert len(runners)==6
    assert runners.loc[runners.horse.eq('Horse 2'),'null_score_reason'].iloc[0] == 'PROVIDER_REPORTED_ZERO_CAREER_STARTS_POST_RACE'
    assert runners.uniform_expected_win.sum()==pytest.approx(1)
    assert runners.quality_matched_expected.isna().all()
    assert races.unscored_starters.iloc[0]==1
    assert not races.score_complete.iloc[0]
    assert not races.quality_comparison_complete.iloc[0]


def test_null_score_without_evidence_not_assumed_debutant():
    assert missing_score_reason({
        'locked_score':None,'locked_scoring_status':'NULL_SCORE_NO_RECORDED_RECENT_FORM'},
        PROSPECTIVE)=='NO_RECORDED_RECENT_FORM_NOT_PROVEN_DEBUTANT'
    assert missing_score_reason({
        'locked_score':None,'locked_scoring_status':None},
        PROSPECTIVE)=='NULL_SCORE_REASON_UNVERIFIED'
    assert missing_score_reason({
        'research_model_score':None,'research_career_starts':4},
        HISTORICAL)=='HAS_RECORDED_CAREER_STARTS_BUT_UNSCORED'
    assert missing_score_reason({
        'research_model_score':None,'research_career_starts':None},
        HISTORICAL)=='NULL_SCORE_REASON_UNVERIFIED_POST_RACE'


def test_prospective_provenance_for_zero_starts():
    source=_race()
    source.loc[0,'locked_score']=None
    source.loc[0,'locked_scoring_status']='NULL_SCORE_ZERO_CAREER_STARTS'
    evidence,_,issues=analyse(source,PROSPECTIVE)
    assert 'INVALID_OR_MISSING_STORED_MODEL_SCORE' in set(issues.issue)
    runners,*_=build_stage21(evidence,PROSPECTIVE)
    assert runners.loc[0,'null_score_reason']=='PROVIDER_REPORTED_ZERO_CAREER_STARTS'


def test_cohort_mix_rejected():
    evidence,_,_=analyse(_race(cohort=HISTORICAL),HISTORICAL)
    with pytest.raises(ValueError,match='Mixed/unknown evidence'):
        build_stage21(evidence,PROSPECTIVE)
    with pytest.raises(ValueError,match='explicitly'):
        build_stage21(evidence,'UNCONFIRMED')


def test_dead_heat_manifest_lists_two_winners_without_overwriting():
    source=pd.DataFrame([
        {'batch_id':'donald5','race_date':'2026-11-15','track':'Donald',
         'race_number':5,'joint_first_count':2,
         'winning_horses':['Horse A','Horse B']},
        {'batch_id':'donald6','race_date':'2026-11-15','track':'Donald',
         'race_number':6,'joint_first_count':1,'winning_horses':['Horse C']}])
    out=dead_heat_manifest(source,PROSPECTIVE)
    assert len(out)==1
    assert out.iloc[0].race_number==5
    assert out.iloc[0].winning_horses==['Horse A','Horse B']
    assert out.iloc[0].issue=='EXCLUDED_JOINT_FIRST_FROM_SINGLE_WINNER_BARRIER_RESEARCH'
    assert len(dead_heat_manifest(source.rename(columns={'joint_first_count':'winner_count'}),HISTORICAL))==1
    assert dead_heat_manifest(pd.DataFrame(),PROSPECTIVE).empty


def test_multiple_winners_rejected_from_single_winner_study():
    source=_race()
    source.loc[1,'won']=1
    evidence,_,issues=analyse(source,PROSPECTIVE)
    assert evidence.empty
    assert 'NO_UNIQUE_FINISHING_WINNER' in set(issues.issue)


def test_no_fake_significance_or_promotion():
    runners,seg,venues,reasons,coverage,hyp=_analyse(_race(),PROSPECTIVE)
    assert not any('p_value' in c for c in seg.columns)
    assert 'inside_minus_middle_excess' in hyp.columns
    assert not hyp.gate.eq('APPROVED').any()
    assert (runners.cohort==PROSPECTIVE).all()


def test_empty_does_not_invent_results():
    out=build_stage21(pd.DataFrame(),PROSPECTIVE)
    assert len(out)==6 and all(x.empty for x in out)
