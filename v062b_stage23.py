"""Stage 2.3 — frozen v0.2 null-score gate audit; RESEARCH ONLY.

Read archived runner source values, never re-fetch form, score/rerank horses,
edit historical evidence, or write a database row.
"""
from __future__ import annotations
from datetime import date
from io import BytesIO
from zipfile import ZipFile, ZIP_DEFLATED
import math
import re
import pandas as pd

from v042_results import canonical
from v062b_stage2 import HISTORICAL, PROSPECTIVE, ALLOWED
from v062b_stage22 import (
    archived_start_evidence, historical_raw_index, prospective_raw_index, digest,
)


def _finite(value):
    if isinstance(value,bool) or value is None:
        return None
    try:
        n=float(value)
        return n if math.isfinite(n) else None
    except (ValueError,TypeError,OverflowError):
        return None


def v02_score_gates(raw):
    """Replicate only v0.2 early-return requirements, not its numeric score.

    app.py score_runner returns None if recent_form_score(form) is None
    OR career_starts (from stats.overall.starts) == 0. Other provider aliases
    are NOT read by this function. Missing starts defaults to 0 in v0.2 but
    must never be called a confirmed debutant.
    """
    state=dict(source_runner_found=isinstance(raw,dict),
               stats_overall_starts_present=False,
               stats_overall_starts_numeric=None,
               effective_v02_career_starts=0,
               form_field_present=False,form_digit_count=0,
               form_last5_digits=None,alternate_form_digits=False,
               alternate_root_starts_positive=False,
               v02_start_gate_pass=False,v02_form_gate_pass=False,
               v02_gate_could_score=False,
               v02_null_gate_reason='ARCHIVED_RUNNER_MISSING')
    if not isinstance(raw,dict):
        return state
    stats=raw.get('stats')
    overall=(stats.get('overall') if isinstance(stats,dict) else None)
    overall=overall if isinstance(overall,dict) else {}
    raw_starts=overall.get('starts')
    present='starts' in overall and raw_starts not in (None,'')
    parsed=_finite(raw_starts) if present else None
    starts=max(0,int(parsed)) if parsed is not None else 0
    form=raw.get('form')
    digits=re.findall(r'[0-9]',str(form or ''))
    form_ok=bool(digits)
    start_ok=starts>0
    alt_form=any(bool(re.findall(r'[0-9]',str(raw.get(k) or '')))
                 for k in ('recentForm','recent_form') if k in raw)
    alt_starts=any((_finite(raw.get(k)) or 0)>0 for k in ('careerStarts','starts'))
    if not form_ok and not start_ok:
        reason='BOTH_V02_GATES_BLOCK'
    elif not form_ok:
        reason='NO_V02_FORM_DIGITS'
    elif not start_ok:
        reason='NO_V02_OVERALL_CAREER_STARTS'
    else:
        reason='V02_GATES_PASS'
    state.update(stats_overall_starts_present=present,
                 stats_overall_starts_numeric=parsed,
                 effective_v02_career_starts=starts,
                 form_field_present=('form' in raw and form is not None),
                 form_digit_count=len(digits),
                 form_last5_digits=''.join(digits[-5:]) or None,
                 alternate_form_digits=alt_form,
                 alternate_root_starts_positive=alt_starts,
                 v02_start_gate_pass=start_ok,
                 v02_form_gate_pass=form_ok,
                 v02_gate_could_score=start_ok and form_ok,
                 v02_null_gate_reason=reason)
    return state


def audit_runners(starters, raw_index, cohort):
    if cohort not in ALLOWED:
        raise ValueError('Explicit historic or prospective cohort required')
    if starters is None or starters.empty:
        return pd.DataFrame()
    needed={'batch_id','race_date','track','race_number','horse','score_present','won'}
    if needed-set(starters.columns):
        raise ValueError('Missing Stage 2.1 columns: '+str(sorted(needed-set(starters.columns))))
    rows=[]
    for row in starters.to_dict('records'):
        raw=raw_index.get((str(row['batch_id']),canonical(row['horse'])))
        gates=v02_score_gates(raw)
        count,status,fields=archived_start_evidence(raw)
        if not gates['source_runner_found']:
            parity='RAW_ARCHIVED_RUNNER_NOT_AVAILABLE'
        elif gates['v02_gate_could_score'] and not row['score_present']:
            parity='UNEXPECTED_NULL_BOTH_V02_GATES_PASSED'
        elif not gates['v02_gate_could_score'] and row['score_present']:
            parity='SCORE_PRESENT_BUT_V02_GATE_BLOCKED'
        elif row['score_present']:
            parity='SCORE_PRESENT_GATE_CONSISTENT'
        else:
            parity='NULL_SCORE_GATE_CONSISTENT'
        rows.append({**row,**gates,
                     'explicit_archived_career_starts':count,
                     'explicit_starts_status':status,
                     'explicit_starts_fields':fields,
                     'v02_gate_parity':parity,
                     'provider_reported_zero_at_capture':status=='PROVIDER_ZERO_STARTS',
                     'independent_debutant_verification':False,
                     'source_time_certification':'HISTORICAL_POST_RACE_NOT_PRE_RACE'
                         if cohort==HISTORICAL else
                         'PROSPECTIVE_LOCKED_SCHEDULE_ONLY_ASOF_UNVERIFIED',
                     'cohort':cohort})
    return pd.DataFrame(rows)


def summaries(detail):
    if detail is None or detail.empty:
        return tuple(pd.DataFrame() for _ in range(4))
    unscored=detail[~detail.score_present].copy()
    breakdown=(unscored.groupby(['v02_null_gate_reason','explicit_starts_status','v02_gate_parity'],dropna=False)
               .agg(starters=('horse','size'),winner_rows=('won','sum'),
                    races=('batch_id','nunique'),dates=('race_date','nunique')).reset_index())
    race=(detail.groupby(['batch_id','race_date','track','race_number'],dropna=False)
          .agg(starters=('horse','size'),scored=('score_present','sum'),
               unscored=('score_present',lambda s:int((~s).sum())),
               winners=('won','sum'),
               parity_conflicts=('v02_gate_parity',lambda s:int(s.isin([
                   'UNEXPECTED_NULL_BOTH_V02_GATES_PASSED',
                   'SCORE_PRESENT_BUT_V02_GATE_BLOCKED']).sum())))
          .reset_index())
    wins=unscored.groupby('batch_id')['won'].sum().to_dict()
    race['unscored_winners']=race.batch_id.map(wins).fillna(0).astype(int)
    race['full_quality_score_coverage']=race.unscored.eq(0)
    issues=detail[detail.v02_gate_parity.isin([
        'UNEXPECTED_NULL_BOTH_V02_GATES_PASSED','SCORE_PRESENT_BUT_V02_GATE_BLOCKED',
        'RAW_ARCHIVED_RUNNER_NOT_AVAILABLE'])].copy()
    return unscored,breakdown,race,issues


def strict_prospective_index(archives):
    """Validate archive AND raw-form digests before consuming any runner source."""
    rejected=[]
    eligible=[]
    for a in archives:
        bid=str(a.get('batch_id'))
        p=a.get('payload')
        if not isinstance(p,dict):
            rejected.append({'batch_id':bid,'issue':'INVALID_ARCHIVE_PAYLOAD'})
            continue
        try:
            good=(digest(p)==a.get('payload_sha256') and
                  digest(p.get('form_response_raw'))==p.get('form_response_sha256')
                  and p.get('model_version')=='v0.2' and a.get('model_version')=='v0.2')
        except (TypeError,ValueError,OverflowError):
            good=False
        if not good:
            rejected.append({'batch_id':bid,'issue':'HASH_OR_VERSION_MISMATCH'})
            continue
        eligible.append(a)
    sources,issues=prospective_raw_index(eligible)
    rejected=pd.DataFrame(rejected,columns=['batch_id','issue'])
    return sources,pd.concat([rejected,issues],ignore_index=True)


def render(secret):
    import streamlit as st
    from v062b_stage2 import analyse
    from v062b_stage21 import build_stage21
    st.subheader('🔬 v0.6.2B Stage 2.3 — Unscored horse root-cause audit')
    st.caption('Read-only original archived FormFav. Investigates the exact two v0.2 '
               'scoring prerequisites without reranking or changing any predictions.')
    choice=st.radio('Research cohort (run both separately)',
        ['Historical discovery (post-race FormFav)',
         'Prospective archived (schedule-only evidence)'],
        key='v062b_stage23_cohort')
    if not st.button('Run Stage 2.3 read-only root-cause audit',
                     key='v062b_stage23_run'):
        return
    try:
        if choice.startswith('Historical'):
            from v061b_historical import server_db,read_all,RACES,FEATURES,RESULTS
            from v062b_historical import build_historical
            client=server_db(secret)
            features=read_all(client,FEATURES,
                'batch_id,horse,horse_key,model_rank,model_score,barrier,career_starts,feature_payload')
            src,duplicates=historical_raw_index(features)
            source_issues=pd.DataFrame([
                {'batch_id':b,'issue':'DUPLICATE_HISTORICAL_RUNNER'}
                for b,k in sorted(duplicates)],columns=['batch_id','issue'])
            prior,_,prior_issues,_=build_historical(
                read_all(client,RACES,'*','race_date'),features,
                read_all(client,RESULTS,'batch_id,result_payload,result_sha256,actual_starters'))
            cohort=HISTORICAL
        else:
            from v061_prospective import archive_client,_read_all,TABLE
            from v062b_barrier_lab import build
            client=archive_client(secret)
            archived=_read_all(client,TABLE,order='created_at')
            src,source_issues=strict_prospective_index(archived)
            prior,_,prior_issues=build(archived,_read_all(client,'runner_results'))
            cohort=PROSPECTIVE
        analysed,_,stage2_issues=analyse(prior,cohort)
        stage21,*_=build_stage21(analysed,cohort)
        audited=audit_runners(stage21,src,cohort)
        unscored,breakdown,race,conflicts=summaries(audited)
    except Exception as exc:
        st.error('Stage 2.3 read-only audit blocked: '+str(exc))
        st.exception(exc)
        return
    a,b,c=st.columns(3)
    a.metric('Audited starters',len(audited))
    b.metric('Unscored horses',len(unscored))
    c.metric('Score-gate parity conflicts',
             int(conflicts.v02_gate_parity.isin([
                 'UNEXPECTED_NULL_BOTH_V02_GATES_PASSED',
                 'SCORE_PRESENT_BUT_V02_GATE_BLOCKED']).sum())
             if not conflicts.empty else 0)
    st.markdown('**Why saved scores are NULL**')
    st.dataframe(breakdown,hide_index=True,use_container_width=True)
    st.markdown('**Affected races, including unscored winners**')
    st.dataframe(race[race.unscored.gt(0)] if not race.empty else race,
                 hide_index=True,use_container_width=True)
    st.info('Frozen v0.2 returns NULL when its form field contains no digits '
            'or stats.overall.starts is not positive. It ignores alternative '
            'root starts/form aliases. A default zero is NOT proof of no prior starts.')
    if not conflicts.empty:
        st.warning(f'{len(conflicts)} raw-source/parity cases require inspection.')
    label='historical' if cohort==HISTORICAL else 'prospective'
    files=[
        ('runner_gate_evidence',audited),('unscored_horses',unscored),
        ('gate_reason_summary',breakdown),('affected_races',race),
        ('source_parity_issues',conflicts),('raw_archive_issues',source_issues),
        ('stage2_issues',stage2_issues),('prior_issues',prior_issues),
    ]
    bundle=BytesIO()
    with ZipFile(bundle,'w',ZIP_DEFLATED) as z:
        for name,frame in files:
            z.writestr(f'v062b_stage23_{name}_{label}.csv',frame.to_csv(index=False))
    st.download_button('Download entire Stage 2.3 audit ZIP',
        data=bundle.getvalue(),mime='application/zip',
        file_name=f'v062b_stage23_{label}_audit_bundle.zip',
        key='v062b_stage23_bundle_'+label)
    st.warning('RESEARCH ONLY. Historic inputs were fetched after racing; '
        'prospective actual-off and provider as-of remain uncertified. '
        'No scoring fix is approved.')
