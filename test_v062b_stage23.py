"""Synthetic, DB-free tests of Stage 2.3 read-only score-gate audit."""
import copy
import pytest
import pandas as pd

from v062b_stage2 import HISTORICAL,PROSPECTIVE
from v062b_stage22 import digest
from v062b_stage23 import (
    v02_score_gates,audit_runners,summaries,strict_prospective_index,
)


def raw(starts=5,form='4312',**other):
    return {'stats':{'overall':{'starts':starts}},'form':form,**other}


def runners():
    return pd.DataFrame([
        {'batch_id':'b','race_date':'2026-11-15','track':'Donald',
         'race_number':5,'horse':'Horse A','score_present':True,'won':0,
         'locked_rank':1},
        {'batch_id':'b','race_date':'2026-11-15','track':'Donald',
         'race_number':5,'horse':'Horse B','score_present':False,'won':1,
         'locked_rank':8},
        {'batch_id':'b','race_date':'2026-11-15','track':'Donald',
         'race_number':5,'horse':'Horse C','score_present':False,'won':0,
         'locked_rank':9},
    ])


def test_gates_are_exactly_two_v02_early_return_conditions():
    gates=v02_score_gates(raw(starts=4,form='12x3'))
    assert gates['v02_gate_could_score']
    assert gates['effective_v02_career_starts']==4
    assert gates['form_last5_digits']=='123'
    assert gates['v02_null_gate_reason']=='V02_GATES_PASS'


@pytest.mark.parametrize('source,reason',[
    (raw(starts=0,form='12'),'NO_V02_OVERALL_CAREER_STARTS'),
    (raw(starts=5,form='-'),'NO_V02_FORM_DIGITS'),
    (raw(starts=0,form='-'),'BOTH_V02_GATES_BLOCK'),
    ({'stats':{},'form':'123'},'NO_V02_OVERALL_CAREER_STARTS'),
    ({'form':None},'BOTH_V02_GATES_BLOCK'),
])
def test_explicit_form_and_qualified_career_starts_required(source,reason):
    assert v02_score_gates(source)['v02_null_gate_reason']==reason


def test_alt_provider_fields_are_not_silently_used_by_v02():
    gates=v02_score_gates({'careerStarts':5,'recentForm':'321','form':None,'stats':{}})
    assert gates['alternate_form_digits']
    assert gates['alternate_root_starts_positive']
    assert not gates['v02_gate_could_score']
    assert gates['v02_null_gate_reason']=='BOTH_V02_GATES_BLOCK'


def test_missing_source_not_proof_of_debutant():
    gates=v02_score_gates(None)
    assert gates['v02_null_gate_reason']=='ARCHIVED_RUNNER_MISSING'
    assert not gates['source_runner_found']
    assert not gates['v02_gate_could_score']


def test_invalid_or_subunit_starts_not_assumed_debutant():
    for starts in ('unknown',None,float('nan'),False,0.5,-1):
        g=v02_score_gates(raw(starts=starts))
        assert not g['v02_start_gate_pass']
        assert g['effective_v02_career_starts']==0


def test_actual_missing_score_winner_preserved_and_not_reranked():
    source={('b','horsea'):raw(5,'4321'),
            ('b','horseb'):raw(0,''),
            ('b','horsec'):raw(3,'')}
    evidence=audit_runners(runners(),source,PROSPECTIVE)
    unscored,gates,races,problems=summaries(evidence)
    assert len(evidence)==3 and len(unscored)==2
    assert races.iloc[0]['unscored_winners']==1
    assert races.iloc[0]['winners']==1
    assert races.iloc[0]['unscored']==2
    assert races.iloc[0]['full_quality_score_coverage']==False
    assert evidence.loc[evidence.horse.eq('Horse B'),'locked_rank'].iloc[0]==8
    assert all(evidence.independent_debutant_verification.eq(False))
    assert problems.empty


def test_contradictory_original_source_and_saved_scores_flagged_not_changed():
    source={('b','horsea'):raw(0,''),
            ('b','horseb'):raw(3,'123'),
            ('b','horsec'):raw(0,'')}
    audited=audit_runners(runners(),source,PROSPECTIVE)
    _,_,_,problems=summaries(audited)
    assert set(problems.v02_gate_parity)=={
        'UNEXPECTED_NULL_BOTH_V02_GATES_PASSED',
        'SCORE_PRESENT_BUT_V02_GATE_BLOCKED'}
    assert len(problems)==2
    assert audited.loc[audited.horse.eq('Horse B'),'score_present'].iloc[0]==False


def test_missing_source_explicitly_reported():
    audited=audit_runners(runners(),{},PROSPECTIVE)
    assert set(audited.v02_gate_parity)=={'RAW_ARCHIVED_RUNNER_NOT_AVAILABLE'}
    assert not audited.provider_reported_zero_at_capture.any()


def test_cohort_is_required():
    with pytest.raises(ValueError):
        audit_runners(runners(),{},'UNKNOWN')
    h=audit_runners(runners(),{},HISTORICAL)
    assert h.source_time_certification.str.contains('POST_RACE').all()


def _archive(form):
    p={'model_version':'v0.2','form_response_raw':form,
       'form_response_sha256':digest(form)}
    return {'batch_id':'b','payload':p,'payload_sha256':digest(p),
            'model_version':'v0.2'}


def test_strict_at_lock_index_rejects_tampered_form_hash():
    a=_archive({'runners':[{'name':'Horse A','stats':{'overall':{'starts':1}},
                            'form':'23'}]})
    sources,issues=strict_prospective_index([a])
    assert len(sources)==1
    assert issues.empty
    tampered=copy.deepcopy(a)
    tampered['payload']['form_response_raw']['runners'][0]['form']='111'
    tampered['payload_sha256']=digest(tampered['payload'])
    sources,issues=strict_prospective_index([tampered])
    assert not sources
    assert 'HASH_OR_VERSION_MISMATCH' in set(issues.issue)


def test_strict_at_lock_index_rejects_wrong_model_version():
    a=_archive({'runners':[]})
    a['model_version']='v0.3'
    sources,issues=strict_prospective_index([a])
    assert not sources
    assert len(issues)==1


def test_unscored_provider_zero_is_not_independently_certified_debutant():
    one=runners().iloc[[1]].copy()
    audited=audit_runners(one,{('b','horseb'):raw(0,'')},PROSPECTIVE)
    assert audited.provider_reported_zero_at_capture.iloc[0]
    assert not audited.independent_debutant_verification.iloc[0]
    assert audited.explicit_starts_status.iloc[0]=='PROVIDER_ZERO_STARTS'


def test_empty_outputs_and_no_numeric_imputation():
    assert audit_runners(pd.DataFrame(),{},PROSPECTIVE).empty
    assert all(df.empty for df in summaries(pd.DataFrame()))
    audited=audit_runners(runners(),{},PROSPECTIVE)
    assert 'imputed_score' not in audited.columns
