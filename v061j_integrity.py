"""v0.6.1j -- read-only, strict prospective results integrity reconciliation.

No database mutations, no reranking, no changes to v0.2 or existing settlement.
The same race-level integrity gate governs both prediction and financial reports.
Snapshot complete vs result finality are independently assessed and explained.
"""
from __future__ import annotations

from collections import Counter, defaultdict
import math

import pandas as pd

VERSION = 'v0.6.1j'
PASS = 'STRICT_FIELD_RECONCILED'


def _canonical(s):
    from v042_results import canonical
    return canonical(s)


def _pos(x):
    try:
        if x is None or isinstance(x, bool):return None
        n=int(x)
        return n if n > 0 and float(x)==n and math.isfinite(float(x)) else None
    except (ValueError,TypeError,OverflowError):return None


def _race_key(x):
    from v061_prospective import race_key
    return race_key(x)


def _add_issue(issues, arc, code, name='', detail='', category='INTEGRITY'):
    issues.append({'batch_id':arc.get('batch_id'), 'race_date':arc.get('race_date'),
       'track':arc.get('track'),'race_number':arc.get('race_number'),
       'category':category,'issue':code,'horse':name,'detail':detail})


def reconcile(archives, official_rows):
    """Diagnose every original locked rank against the *full* imported result field.

    Fails closed on discrepancies. No inferred scratched horses or betting returns.
    A provider's omission is a coverage issue, not permission to presume a scratch.
    """
    from v061_prospective import digest
    from v057f_odds_integrity import iso_timestamp
    index=defaultdict(list)
    for r in official_rows:
        index[_race_key(r)].append(r)
    race_rows=[]; issues=[]; member_rows=[]; seen=set()
    for a in sorted(archives,key=lambda row:str(row.get('created_at') or '')):
        key=_race_key(a); batch=a.get('batch_id'); payload=a.get('payload') or {}
        rankrows=payload.get('rankings') or []
        imported=index.get(key,[])
        row={'batch_id':batch,'race_date':a.get('race_date'),'track':a.get('track'),
             'race_number':a.get('race_number'),'snapshot_declared':len(rankrows),
             'official_rows':len(imported),'official_starters':0,'official_scratches':0,
             'missing_from_official':0,'extra_in_official':0,'duplicate_official':0,
             'strict_status':'UNASSESSED','strict_ok':False,
             'top1_won':None,'top2_hit':None,'top3_hit':None,
             'winner_count':None,'top1_scratched':None,'reason_codes':'',
             'scheduled_only_not_actual_off_certified':True,
             'research_only':True}
        found=[]
        def fail(code,horse='',detail=''):
            found.append(code);_add_issue(issues,a,code,horse,detail)
        if key in seen:fail('DUPLICATE_ARCHIVED_RACE')
        seen.add(key)
        if not isinstance(payload,dict) or not a.get('payload_sha256') or digest(payload)!=a.get('payload_sha256'):
            fail('ARCHIVE_HASH_UNVERIFIED')
        if payload.get('model_version')!='v0.2' or a.get('model_version')!='v0.2':
            fail('MODEL_VERSION_NOT_FROZEN_V02')
        times=payload.get('source_times') or {}
        sched=iso_timestamp(a.get('scheduled_start_utc'))
        arch=iso_timestamp(a.get('created_at'))
        src=iso_timestamp(times.get('form_response_received_utc'))
        generated=iso_timestamp(times.get('ranking_generated_utc'))
        if not(sched and arch and src and generated and src<=generated<=arch<sched):
            fail('TIMING_OR_PROVENANCE_INCOMPLETE')
        ranked={}; key_to_rank={}
        for r in rankrows:
            try:
                rank=_pos(r['rank']); name=_canonical(r['horse'])
                if rank is None or not name or rank in ranked or name in key_to_rank:raise ValueError()
                ranked[rank]=name; key_to_rank[name]=rank
            except (KeyError,TypeError,ValueError):fail('INVALID_OR_DUPLICATE_LOCKED_RANK')
        if not ranked or sorted(ranked)!=list(range(1,len(rankrows)+1)):
            fail('INCOMPLETE_LOCKED_RANK_SEQUENCE')
        if not imported:fail('NO_IMPORTED_OFFICIAL_RESULTS')
        if any(r.get('result_status')!='final' for r in imported):fail('NON_FINAL_OFFICIAL_RESULT')
        official={}; scratched=set()
        for r in imported:
            name=_canonical(r.get('horse_key') or r.get('horse'))
            if not name:
                fail('EMPTY_OFFICIAL_RUNNER_ID');continue
            if name in official:
                row['duplicate_official']+=1
                fail('DUPLICATE_OFFICIAL_RUNNER',name);continue
            scratch=r.get('scratched')
            pos=r.get('finishing_position')
            if scratch is True:
                if pos is not None:fail('SCRATCHED_WITH_FINISH_POSITION',name)
                scratched.add(name); official[name]=None
            elif scratch is False:
                place=_pos(pos)
                if place is None:fail('INVALID_OFFICIAL_FINISH_POSITION',name)
                official[name]=place
            else:
                fail('UNKNOWN_SCRATCH_STATUS',name)
                official[name]=None
        starters={n:v for n,v in official.items() if n not in scratched and v is not None}
        row['official_starters']=len(starters);row['official_scratches']=len(scratched)
        missing=sorted(set(key_to_rank)-set(official))
        extra=sorted(set(official)-set(key_to_rank))
        row['missing_from_official']=len(missing);row['extra_in_official']=len(extra)
        for n in missing:fail('LOCKED_RUNNER_NOT_IN_OFFICIAL_RESULT',n,
                              'Unknown whether late scratch, omission, or naming mismatch; no inference')
        for n in extra:fail('OFFICIAL_RUNNER_NOT_IN_LOCKED_SNAPSHOT',n,
                            'Possible late entry / identity mismatch; do not remap automatically')
        for name,rank in sorted(key_to_rank.items(),key=lambda x:x[1]):
            member_rows.append({'batch_id':batch,'race_date':a.get('race_date'),'track':a.get('track'),
                'race_number':a.get('race_number'),'model_rank':rank,'locked_horse_key':name,
                'official_present':name in official,'official_scratched':name in scratched if name in official else None,
                'official_finishing_position':starters.get(name),
                'issue':'MISSING_FROM_OFFICIAL' if name in missing else ('OFFICIALLY_SCRATCHED' if name in scratched else 'MATCHED_STARTER')})
        wins={n for n,pos in starters.items() if pos==1}
        if not wins:fail('NO_OFFICIAL_FIRST_PLACE_WINNER')
        # Do not infer official result completeness from field size alone.
        if found:
            row['strict_status']='|'.join(sorted(set(found)))
            row['reason_codes']=row['strict_status']
        else:
            row.update(strict_status=PASS,strict_ok=True,winner_count=len(wins),
                top1_scratched=ranked[1] in scratched,
                top1_won=ranked[1] in wins if ranked[1] not in scratched else None,
                top2_hit=any(ranked[i] in wins for i in range(1,min(2,len(ranked))+1)),
                top3_hit=any(ranked[i] in wins for i in range(1,min(3,len(ranked))+1)))
            if ranked[1] in scratched:
                row.update(strict_ok=False,strict_status='TOP1_SCRATCHED_NO_REPLACEMENT',reason_codes='TOP1_SCRATCHED_NO_REPLACEMENT')
                _add_issue(issues,a,'TOP1_SCRATCHED_NO_REPLACEMENT')
        race_rows.append(row)
    return pd.DataFrame(race_rows),pd.DataFrame(issues,columns=['batch_id','race_date','track','race_number','category','issue','horse','detail']),pd.DataFrame(member_rows)


def prediction_accuracy(races):
    rows=[]
    for label,column in [('Top 1','top1_won'),('Top 2','top2_hit'),('Top 3','top3_hit')]:
        valid=races[races['strict_ok'].eq(True) & races[column].notna()] if not races.empty else races
        n=len(valid); hit=int(valid[column].eq(True).sum()) if n else 0
        rows.append({'strategy':label,'fully_reconciled_races':n,'winning_races':hit,
            'hit_rate_pct':100*hit/n if n else None,'requires_betting_odds':False,
            'certified_actual_off':False,'research_only':True})
    return pd.DataFrame(rows)


def gate_tickets(graded, races):
    """Retain original rows and money, but prohibit un-reconciled financial grades.

    The original v061h portfolio stays untouched; this is an independently
    named, stricter view. An invalid race cannot contribute to any ROI.
    """
    if graded.empty:return graded.copy()
    rights=dict(zip(races['batch_id'],races['strict_ok']))
    reasons=dict(zip(races['batch_id'],races['strict_status']))
    out=graded.copy()
    out['field_reconciled']=out['batch_id'].map(rights).fillna(False).astype(bool)
    out['integrity_status']=out['batch_id'].map(reasons).fillna('ARCHIVE_MISSING_FROM_RECONCILIATION')
    out['original_settlement_status']=out['settlement_status']
    rejected=~out['field_reconciled']
    out.loc[rejected,'settlement_status']='BLOCKED_BY_V061J_STRICT_INTEGRITY'
    for col in ['total_return','profit','win_hit','place_hit']:
        if col in out.columns:
            out[col]=out[col].astype(object)
            out.loc[rejected,col]=None
    return out


def _matched(strategy, evaluated):
    import v061h_strategy
    return v061h_strategy.matched_portfolios(evaluated) if strategy=='matched' else v061h_strategy.aggregate(evaluated)


def compare_reports(strict_graded, old_graded, races):
    import v061h_strategy
    s=v061h_strategy.aggregate(strict_graded)
    m=v061h_strategy.matched_portfolios(strict_graded)
    o=v061h_strategy.aggregate(old_graded)
    rows=[]
    for new in s.to_dict('records'):
        old=next((r for r in o.to_dict('records') if r['strategy']==new['strategy']),{})
        rows.append({'strategy':new['strategy'],'previous_provisional_races':old.get('fully_evaluable_races',0),
            'strict_reconciled_races':new['fully_evaluable_races'],
            'excluded_from_previous':old.get('fully_evaluable_races',0)-new['fully_evaluable_races'],
            'previous_provisional_roi_pct':old.get('roi_%'),
            'strict_roi_pct':new['roi_%'],
            'strict_net_profit_$':new['net_profit_$'],
            'CAUTION':'Historical/current snapshots NOT independently certified actual-off time; research only'})
    return s,m,pd.DataFrame(rows)


def build_reports(archives,official_rows):
    import v061h_strategy as strategy
    races,issues,members=reconcile(archives,official_rows)
    tickets=strategy.build_tickets(archives)
    graded=strategy.grade(tickets,official_rows) if not tickets.empty else pd.DataFrame()
    gated=gate_tickets(graded,races)
    portfolios,matched,compare=compare_reports(gated,graded,races)
    reason_counts=(issues.groupby('issue',dropna=False).size().reset_index(name='occurrences')
                   .sort_values('occurrences',ascending=False) if not issues.empty else
                   pd.DataFrame(columns=['issue','occurrences']))
    summary=pd.DataFrame([{'version':VERSION,'archived_races':len(races),
        'strictly_reconciled_races':int(races['strict_ok'].sum()) if not races.empty else 0,
        'unreconciled_races':int((~races['strict_ok']).sum()) if not races.empty else 0,
        'official_name_missing_issues':int((issues['issue']=='LOCKED_RUNNER_NOT_IN_OFFICIAL_RESULT').sum()),
        'official_name_extra_issues':int((issues['issue']=='OFFICIAL_RUNNER_NOT_IN_LOCKED_SNAPSHOT').sum()),
        'certified_actual_off_races':0,'prediction_and_roi_share_strict_gate':True,
        'note':'Read-only; incomplete race fields BLOCK both prediction accuracy and strategy ROI. No inferred scratches.'}])
    return {'integrity_summary':summary,'race_reconciliation':races,
            'runner_identity_audit':members,'issues':issues,'issue_counts':reason_counts,
            'strict_prediction_accuracy':prediction_accuracy(races),
            'strict_ticket_audit':gated,'strict_portfolios':portfolios,
            'strict_matched_top3':matched,'previous_vs_strict':compare}


def render(secret):
    import streamlit as st
    import v061_prospective as prospective
    st.subheader('🛡️ v0.6.1j — Prospective Results Integrity')
    st.caption('Read-only shared race-integrity gate for prediction and ROI. The earlier v0.6.1h/i exploratory reports are not overwritten.')
    st.warning('Results are provisional: pre-schedule captures do not independently verify actual race-off or source-as-of. Unmatched locked horses are NOT automatically marked scratched.')
    if st.button('Run v0.6.1j strict results reconciliation',key='v061j_run'):
        try:
            client=prospective.archive_client(secret)
            archives=prospective._read_all(client,prospective.TABLE,order='created_at')
            official=prospective._read_all(client,'runner_results')
            st.session_state['v061j_reports']=build_reports(archives,official)
        except Exception as exc:
            st.error('Integrity report failed safely, no records modified: '+str(exc));return
    reports=st.session_state.get('v061j_reports')
    if not reports:return
    st.dataframe(reports['integrity_summary'],use_container_width=True,hide_index=True)
    st.markdown('**Why each race passed or failed**')
    st.dataframe(reports['race_reconciliation'],use_container_width=True,hide_index=True)
    st.markdown('**Mismatch types and individual horse names**')
    st.dataframe(reports['issue_counts'],use_container_width=True,hide_index=True)
    st.dataframe(reports['issues'],use_container_width=True,hide_index=True)
    st.markdown('**Odds-independent prediction accuracy — strict cohort**')
    st.dataframe(reports['strict_prediction_accuracy'],use_container_width=True,hide_index=True)
    st.markdown('**Betting ROI — same strict field-integrity gate, plus independently verified bookmaker terms**')
    st.dataframe(reports['strict_portfolios'],use_container_width=True,hide_index=True)
    st.dataframe(reports['previous_vs_strict'],use_container_width=True,hide_index=True)
    for name,frame in reports.items():
        st.download_button('Download v0.6.1j '+name+' CSV',
            data=frame.to_csv(index=False).encode('utf-8'),file_name='v061j_'+name+'.csv',
            mime='text/csv',key='v061j_export_'+name)
