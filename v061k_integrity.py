"""v0.6.1k: scratch-aware, read-only prospective integrity.

Preserves immutable archived predictions, original v0.6.1j outputs and frozen v0.2.
Official scratched outsiders are recognized *only* with explicit scratched=True
and finishing_position=None. No retroactive replacement or re-ranking.
"""
from __future__ import annotations
from collections import Counter, defaultdict
import pandas as pd
from v061j_integrity import (_canonical, _pos, _race_key, _add_issue,
                             gate_tickets, prediction_accuracy, compare_reports)

VERSION = 'v0.6.1k'
PASS = 'SCRATCH_AWARE_FIELD_RECONCILED'


def reconcile(archives, official_rows):
    from v061_prospective import digest
    from v057f_odds_integrity import iso_timestamp
    index=defaultdict(list)
    for item in official_rows:
        index[_race_key(item)].append(item)
    races=[]; issues=[]; members=[]; seen=set()
    for a in sorted(archives,key=lambda x:str(x.get('created_at') or '')):
        key=_race_key(a); batch=a.get('batch_id'); payload=a.get('payload') or {}
        rankrows=payload.get('rankings') or []; imported=index.get(key,[])
        row={'batch_id':batch,'race_date':a.get('race_date'),'track':a.get('track'),
             'race_number':a.get('race_number'),'snapshot_declared':len(rankrows),
             'official_rows':len(imported),'official_starters':0,'official_scratches':0,
             'verified_prelock_excluded_scratches':0,'late_locked_scratches':0,
             'unmatched_official_starters':0,'missing_locked_runners':0,
             'scratch_only_reconciliation':False,
             'strict_status':'UNASSESSED','strict_ok':False,
             'top1_won':None,'top2_hit':None,'top3_hit':None,
             'winner_count':None,'top1_scratched':None,'reason_codes':'',
             'scheduled_only_not_actual_off_certified':True,'research_only':True}
        found=[]
        def fail(code, horse='', detail=''):
            found.append(code); _add_issue(issues,a,code,horse,detail)
        if key in seen:fail('DUPLICATE_ARCHIVED_RACE')
        seen.add(key)
        if not isinstance(payload,dict) or not a.get('payload_sha256') or digest(payload)!=a.get('payload_sha256'):
            fail('ARCHIVE_HASH_UNVERIFIED')
        if payload.get('model_version')!='v0.2' or a.get('model_version')!='v0.2':fail('MODEL_VERSION_NOT_FROZEN_V02')
        times=payload.get('source_times') or {}
        sched=iso_timestamp(a.get('scheduled_start_utc')); arch=iso_timestamp(a.get('created_at'))
        src=iso_timestamp(times.get('form_response_received_utc'))
        generated=iso_timestamp(times.get('ranking_generated_utc'))
        if not(sched and arch and src and generated and src<=generated<=arch<sched):
            fail('TIMING_OR_PROVENANCE_INCOMPLETE')
        ranked={}; key_to_rank={}
        for r in rankrows:
            try:
                rank=_pos(r['rank']); name=_canonical(r['horse'])
                if rank is None or not name or rank in ranked or name in key_to_rank:raise ValueError()
                ranked[rank]=name;key_to_rank[name]=rank
            except (KeyError,TypeError,ValueError):fail('INVALID_OR_DUPLICATE_LOCKED_RANK')
        if not ranked or sorted(ranked)!=list(range(1,len(rankrows)+1)):
            fail('INCOMPLETE_LOCKED_RANK_SEQUENCE')
        if not imported:fail('NO_IMPORTED_OFFICIAL_RESULTS')
        if any(r.get('result_status')!='final' for r in imported):fail('NON_FINAL_OFFICIAL_RESULT')
        official={}; scratched=set(); original_names={}
        for r in imported:
            name=_canonical(r.get('horse_key') or r.get('horse'))
            if not name:
                fail('EMPTY_OFFICIAL_RUNNER_ID');continue
            if name in official:
                fail('DUPLICATE_OFFICIAL_RUNNER',name);continue
            raw=r.get('scratched'); pos=r.get('finishing_position')
            original_names[name]=r.get('horse') or r.get('horse_key')
            if raw is True:
                if pos is not None:fail('SCRATCHED_WITH_FINISH_POSITION',name)
                scratched.add(name);official[name]=None
            elif raw is False:
                place=_pos(pos)
                if place is None:fail('INVALID_OFFICIAL_FINISH_POSITION',name)
                official[name]=place
            else:
                fail('UNKNOWN_SCRATCH_STATUS',name);official[name]=None
        starters={name:v for name,v in official.items() if name not in scratched and v is not None}
        row['official_starters']=len(starters);row['official_scratches']=len(scratched)
        missing=sorted(set(key_to_rank)-set(official))
        extra=sorted(set(official)-set(key_to_rank))
        for name in missing:
            fail('LOCKED_RUNNER_NOT_IN_OFFICIAL_RESULT',name,'A locked runner must have an explicit official starter or scratched entry')
        for name in extra:
            if name in scratched and official[name] is None:
                row['verified_prelock_excluded_scratches']+=1
                _add_issue(issues,a,'VERIFIED_OFFICIAL_SCRATCH_OUTSIDE_LOCK',original_names.get(name) or name,
                           'Excluded from original ranked active field; official scratched=True and no finish position', 'INFORMATION')
            else:
                row['unmatched_official_starters']+=1
                fail('OFFICIAL_STARTER_NOT_IN_LOCKED_SNAPSHOT',name,
                     'Actual starter not present in original immutable rankings')
        row['missing_locked_runners']=len(missing)
        row['late_locked_scratches']=sum(n in scratched for n in key_to_rank)
        row['scratch_only_reconciliation']=bool(extra) and row['unmatched_official_starters']==0 and not missing
        for name,rank in sorted(key_to_rank.items(),key=lambda x:x[1]):
            members.append({'batch_id':batch,'race_date':a.get('race_date'),'track':a.get('track'),
              'race_number':a.get('race_number'),'model_rank':rank,'horse_key':name,
              'official_present':name in official,'official_scratched':name in scratched if name in official else None,
              'official_finishing_position':starters.get(name),
              'identity_status':'MISSING_FROM_OFFICIAL' if name in missing else
                    ('LOCKED_HORSE_LATER_SCRATCHED' if name in scratched else 'MATCHED_STARTER')})
        for name in extra:
            members.append({'batch_id':batch,'race_date':a.get('race_date'),'track':a.get('track'),
              'race_number':a.get('race_number'),'model_rank':None,'horse_key':name,
              'official_present':True,'official_scratched':name in scratched,
              'official_finishing_position':starters.get(name),
              'identity_status':'OFFICIAL_SCRATCH_OUTSIDE_LOCK' if name in scratched else 'OFFICIAL_STARTER_OUTSIDE_LOCK'})
        wins={n for n,v in starters.items() if v==1}
        if not wins:fail('NO_OFFICIAL_FIRST_PLACE_WINNER')
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
        races.append(row)
    return pd.DataFrame(races),pd.DataFrame(issues,columns=['batch_id','race_date','track','race_number','category','issue','horse','detail']),pd.DataFrame(members)


def build_reports(archives, official_rows):
    import v061h_strategy as strategy
    import v061j_integrity as prior
    races,issues,members=reconcile(archives,official_rows)
    old_races,_,_=prior.reconcile(archives,official_rows)
    tickets=strategy.build_tickets(archives)
    graded=strategy.grade(tickets,official_rows) if not tickets.empty else pd.DataFrame()
    gated=gate_tickets(graded,races)
    portfolios,matched,compare=compare_reports(gated,graded,races)
    reason_counts=(issues.groupby(['category','issue'],dropna=False).size().reset_index(name='occurrences')
                   if not issues.empty else pd.DataFrame(columns=['category','issue','occurrences']))
    old_by_batch=old_races.set_index('batch_id')['strict_status'].to_dict() if not old_races.empty else {}
    delta=races[['batch_id','race_date','track','race_number','strict_ok','strict_status',
                 'verified_prelock_excluded_scratches','late_locked_scratches','unmatched_official_starters']].copy()
    delta['v061j_status']=delta['batch_id'].map(old_by_batch)
    delta['newly_eligible']=delta['strict_ok'] & ~delta['batch_id'].map(
        old_races.set_index('batch_id')['strict_ok'].to_dict() if not old_races.empty else {}).fillna(False)
    summary=pd.DataFrame([{'version':VERSION,'archived_races':len(races),
        'reconciled_races':int(races['strict_ok'].sum()) if not races.empty else 0,
        'still_unreconciled_races':int((~races['strict_ok']).sum()) if not races.empty else 0,
        'verified_official_scratches_excluded_from_lock':int(races['verified_prelock_excluded_scratches'].sum()) if not races.empty else 0,
        'unmatched_official_starters':int(races['unmatched_official_starters'].sum()) if not races.empty else 0,
        'missing_locked_runners':int(races['missing_locked_runners'].sum()) if not races.empty else 0,
        'newly_eligible_vs_v061j':int(delta['newly_eligible'].sum()) if not delta.empty else 0,
        'certified_actual_off_races':0,'research_only':True,
        'note':'Official scratches absent from locked active field accepted only with scratched=True and null finish; immutable ranks unchanged.'}])
    return {'integrity_summary':summary,'race_reconciliation':races,'runner_identity_audit':members,
            'issues':issues,'issue_counts':reason_counts,'j_vs_k_comparison':delta,
            'prediction_accuracy':prediction_accuracy(races),'ticket_audit':gated,
            'portfolios':portfolios,'matched_top3':matched,'previous_vs_scratch_aware':compare}


def render(secret):
    import streamlit as st
    import v061_prospective as p
    st.subheader('🛡️ v0.6.1k — Scratch-Aware Prospective Results Integrity')
    st.caption('Reads the saved v0.6.1 snapshots and final results; never writes to Supabase. v0.2 unchanged.')
    st.warning('Pre-schedule only: independent actual-off and input-as-of evidence not certified. Official scratches outside the locked active field are accepted ONLY when explicitly scratched and without finishing positions.')
    if st.button('Run v0.6.1k scratch-aware reconciliation',key='v061k_run'):
        try:
            client=p.archive_client(secret)
            archives=p._read_all(client,p.TABLE,order='created_at')
            official=p._read_all(client,'runner_results')
            st.session_state['v061k_reports']=build_reports(archives,official)
        except Exception as exc:
            st.error('v0.6.1k safely stopped; nothing modified: '+str(exc));return
    reports=st.session_state.get('v061k_reports')
    if not reports:return
    for name,frame in reports.items():
        with st.expander(name.replace('_',' ').title(),expanded=name in ('integrity_summary','race_reconciliation','prediction_accuracy','portfolios')):
            if frame.empty:st.info('No rows in this report.')
            else:st.dataframe(frame,hide_index=True,use_container_width=True)
            st.download_button('Download '+name+' CSV',data=frame.to_csv(index=False).encode(),
                               file_name='v061k_'+name+'.csv',mime='text/csv',key='v061k_csv_'+name)
