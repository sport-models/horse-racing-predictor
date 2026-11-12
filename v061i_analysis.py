"""v0.6.1i: READ-ONLY prediction accuracy, betting evidence, distance and score audits.

No schema writes, no production scoring changes, no odds reconstruction.
Prospective predictions are pre-SCHEDULE only, not actual-off/input-as-of certified.
Historical research is after-race retrieved, not point-in-time performance.
"""
from __future__ import annotations
from collections import Counter, defaultdict
import math
import re
import pandas as pd


def _canon(value):
    return re.sub(r'[^a-z0-9]', '', str(value or '').casefold())


def _is_true(value):
    if isinstance(value, bool): return value
    return str(value).casefold() == 'true'


def _pos(value):
    if value is None or isinstance(value, bool): return None
    try:
        number = float(value)
        if math.isfinite(number) and number >= 1 and number.is_integer(): return int(number)
    except (TypeError, ValueError, OverflowError): pass
    return None


def _race_id(item):
    return (str(item.get('race_date') or '')[:10], _canon(item.get('track')),
            _pos(item.get('race_number')))


def _valid_hash(archive):
    # Refuse to grade an archive without an integrity hash.
    import v061_prospective as p
    raw = archive.get('payload')
    return bool(isinstance(raw,dict) and archive.get('payload_sha256') and
                p.digest(raw) == archive['payload_sha256'])


def prospective_accuracy(archives, runner_results):
    """Independent of all win/place odds: uses original locked ranks and final results.

    A race is eligible only when all official starter identities and positions are
    valid and present in the original snapshot. No post-scratch substitutions.
    """
    from v057f_odds_integrity import iso_timestamp
    index=defaultdict(list)
    for result in runner_results:
        k=_race_id(result)
        if k[2] is not None: index[k].append(result)
    out=[]
    seen=set()
    for a in sorted(archives, key=lambda x: str(x.get('created_at') or '')):
        key=_race_id(a)
        p=a.get('payload') or {}
        ranks=p.get('rankings') or []
        row={'batch_id':a.get('batch_id'), 'race_date':key[0], 'track':a.get('track'),
             'race_number':key[2], 'status':'UNASSESSED', 'top1_won':None,
             'top3_hit':None,'top2_hit':None,'top1_selection':None,'field_size':None,
             'winner_count':None,'top1_scratched':None,'top3_scratched':None,
             'research_only':True,'actual_off_verified':False,
             'input_asof_verified':False}
        if key in seen:
            row['status']='DUPLICATE_PROSPECTIVE_RACE';out.append(row);continue
        seen.add(key)
        if not _valid_hash(a):row['status']='MISSING_OR_INVALID_ARCHIVE_HASH';out.append(row);continue
        if p.get('model_version')!='v0.2':row['status']='MODEL_VERSION_MISMATCH';out.append(row);continue
        scheduled=iso_timestamp(a.get('scheduled_start_utc'))
        archived=iso_timestamp(a.get('created_at'))
        times=p.get('source_times') or {}
        form_at=iso_timestamp(times.get('form_response_received_utc'))
        rank_at=iso_timestamp(times.get('ranking_generated_utc'))
        if not (scheduled and archived and form_at and rank_at and form_at<=rank_at<=archived<scheduled):
            row['status']='PRE_SCHEDULE_EVIDENCE_INCOMPLETE';out.append(row);continue
        try:
            rankmap={int(r['rank']):_canon(r['horse']) for r in ranks}
            name_by_rank={int(r['rank']):str(r['horse']) for r in ranks}
            if (not ranks or len(rankmap)!=len(ranks) or
                sorted(rankmap)!=list(range(1,len(ranks)+1)) or
                len(set(rankmap.values()))!=len(rankmap) or not all(rankmap.values())):
                raise ValueError('invalid rankings')
        except (TypeError,ValueError,KeyError):
            row['status']='INVALID_LOCKED_RANKINGS';out.append(row);continue
        row['top1_selection']=name_by_rank[1]
        result=index.get(key,[])
        if not result:
            row['status']='NO_IMPORTED_FINAL_RESULTS';out.append(row);continue
        if any(x.get('result_status')!='final' for x in result):
            row['status']='NONFINAL_RESULT';out.append(row);continue
        observed={}; scratched=set(); invalid=False
        for x in result:
            name=_canon(x.get('horse_key') or x.get('horse'))
            if not name or name in observed or name in scratched:
                invalid=True;break
            if x.get('scratched') is True:
                if x.get('finishing_position') is not None:invalid=True;break
                scratched.add(name)
            elif x.get('scratched') is False:
                pos=_pos(x.get('finishing_position'))
                if pos is None:invalid=True;break
                observed[name]=pos
            else:
                invalid=True;break
        if invalid or not observed or not any(v==1 for v in observed.values()):
            row['status']='INVALID_OR_INCOMPLETE_OFFICIAL_POSITIONS';out.append(row);continue
        if not set(observed).issubset(set(rankmap.values())):
            row['status']='ACTUAL_STARTER_MISSING_FROM_LOCKED_FIELD';out.append(row);continue
        # Missing result horses are permissible only if explicitly scratched; we
        # must not use result count as implicit proof of a complete field.
        if set(rankmap.values()) != set(observed)|scratched:
            row['status']='OFFICIAL_RESULT_FIELD_INCOMPLETE';out.append(row);continue
        row['field_size']=len(observed)
        row['winner_count']=sum(v==1 for v in observed.values())
        row['top1_scratched']=rankmap[1] in scratched
        row['top3_scratched']=any(rankmap[i] in scratched for i in range(1,min(3,len(rankmap))+1))
        row['top1_won']=bool(observed.get(rankmap[1])==1) if not row['top1_scratched'] else None
        row['top2_hit']=any(observed.get(rankmap[i])==1 for i in range(1,min(2,len(rankmap))+1))
        row['top3_hit']=any(observed.get(rankmap[i])==1 for i in range(1,min(3,len(rankmap))+1))
        row['status']='EVALUABLE_PRE_SCHEDULE_PROVISIONAL' if not row['top1_scratched'] else 'TOP1_SCRATCHED_NO_SUBSTITUTION'
        out.append(row)
    return pd.DataFrame(out, columns=list(row) if out else [
        'batch_id','race_date','track','race_number','status','top1_won','top2_hit','top3_hit'])


def prediction_summary(predictions):
    rows=[]
    if predictions.empty:return pd.DataFrame()
    for label,field in [('Rank 1','top1_won'),('Top 2','top2_hit'),('Top 3','top3_hit')]:
        eligible=predictions[predictions['status']=='EVALUABLE_PRE_SCHEDULE_PROVISIONAL']
        eligible=eligible[eligible[field].notna()]
        wins=int(eligible[field].map(_is_true).sum())
        n=len(eligible)
        rows.append({'prediction':label,'evaluable_races':n,'races_with_winner':wins,
                     'hit_rate_pct':100*wins/n if n else None,
                     'odds_required':False,'certified_point_in_time':False})
    return pd.DataFrame(rows)


def odds_coverage(tickets, graded):
    if tickets.empty:return pd.DataFrame(),pd.DataFrame()
    group=tickets.copy()
    counts=Counter(str(x) for x in group['status'])
    reason=pd.DataFrame([{'reason':k,'selections':v} for k,v in sorted(counts.items())])
    by=[]
    for depth in (1,2,3):
        part=group[group['rank']<=depth]
        for batch_id, g in part.groupby('batch_id'):
            complete=len(g)==depth and bool((g['status']=='ELIGIBLE').all())
            by.append({'batch_id':batch_id,'race_date':g.iloc[0]['race_date'],
                       'track':g.iloc[0]['track'],'race_number':g.iloc[0]['race_number'],
                       'strategy':f'Top {depth}', 'expected_tickets':depth,
                       'available_tickets':int((g['status']=='ELIGIBLE').sum()),
                       'quotes_complete':complete,
                       'missing_or_unverified':'; '.join(f"Rank {r['rank']}: {r['status']} ({r['horse']})"
                                    for r in g.to_dict('records') if r['status']!='ELIGIBLE'),
                       'fully_graded':False})
    coverage=pd.DataFrame(by)
    if not graded.empty:
        complete=graded.groupby(['batch_id']).size()
        for i,r in coverage.iterrows():
            g=graded[(graded['batch_id']==r['batch_id']) & (graded['rank']<=int(r['strategy'][-1]))]
            coverage.at[i,'fully_graded']=len(g)==int(r['strategy'][-1]) and bool((g['settlement_status']=='GRADED_RESEARCH_ONLY').all())
    return coverage,reason


def individual_strategy(graded):
    if graded.empty:return pd.DataFrame()
    valid=graded[graded['settlement_status']=='GRADED_RESEARCH_ONLY'].copy()
    out=[]
    for label, frame in [('Rank 1',valid[valid['rank']==1]),('Rank 2',valid[valid['rank']==2]),
                         ('Rank 3',valid[valid['rank']==3]),('All eligible Top 3 tickets',valid[valid['rank']<=3])]:
        stake=float(frame['stake'].sum()) if len(frame) else 0
        profit=float(frame['profit'].sum()) if len(frame) else 0
        out.append({'selection_group':label,'individually_graded_tickets':len(frame),
                    'races_represented':frame['batch_id'].nunique(),
                    'win_legs_hit':int(frame['win_hit'].map(_is_true).sum()) if len(frame) else 0,
                    'place_legs_hit':int(frame['place_hit'].map(_is_true).sum()) if len(frame) else 0,
                    'stake_$':stake,'net_profit_$':profit if stake else None,
                    'roi_%':100*profit/stake if stake else None,
                    'matched_portfolio_comparison':False})
    return pd.DataFrame(out)


def distance_meters(value):
    """Accept explicit metric distances only; unknown units are not guessed."""
    if value is None or (isinstance(value,float) and math.isnan(value)):return None
    # FormFav race.distance is a numeric metres field; record that this is a
    # provider-unit assumption, not an independently measured distance.
    match=re.fullmatch(r'\s*(\d{3,4})(?:\.0)?\s*(?:m|metres|meters)?\s*',str(value).casefold())
    if not match:return None
    n=int(match.group(1))
    return n if 600<=n<=5000 else None


def distance_band(value):
    d=distance_meters(value)
    if d is None:return 'UNKNOWN'
    if d<=1200:return 'SPRINT_600_1200M'
    if d<=1600:return 'SHORT_MIDDLE_1201_1600M'
    if d<=2000:return 'MIDDLE_1601_2000M'
    return 'STAYING_2001_5000M'


def historical_distance(race_coverage,backtest_races):
    if race_coverage.empty or backtest_races.empty:return pd.DataFrame(),pd.DataFrame()
    coverage=race_coverage[['batch_id','source_race_distance']].drop_duplicates('batch_id')
    research=backtest_races.merge(coverage,on='batch_id',how='left',validate='one_to_one')
    research['distance_m']=research['source_race_distance'].map(distance_meters)
    research['distance_band']=research['source_race_distance'].map(distance_band)
    research['research_evaluable']=research['status'].eq('EVALUABLE_RETROSPECTIVE_RESEARCH')
    rows=[]
    for band, part in research.groupby('distance_band'):
        good=part[part['research_evaluable']]
        n=len(good)
        top1=int(good['top1_won'].map(_is_true).sum()) if n else 0
        top3=int(good['winner_in_top3'].map(_is_true).sum()) if n else 0
        sp=good[pd.to_numeric(good['model_win_sp_profit_10'],errors='coerce').notna()]
        stake=10*len(sp)
        profit=float(pd.to_numeric(sp['model_win_sp_profit_10']).sum()) if stake else 0
        rows.append({'distance_band':band,'archived_races':len(part),
                     'evaluable_retrospective_races':n,'rank1_wins':top1,
                     'rank1_win_pct':100*top1/n if n else None,
                     'top3_winner_races':top3,'top3_hit_pct':100*top3/n if n else None,
                     'sp_proxy_priced_races':len(sp),'sp_proxy_$10_stake':stake,
                     'sp_proxy_net_$':profit if stake else None,
                     'sp_proxy_roi_pct':100*profit/stake if stake else None,
                     'research_only':True,'source':'POST_RACE_FORM_AND_HISTORICAL_SP'})
    return pd.DataFrame(rows),research


def unscored_diagnostic(features):
    """Identify missing-score patterns; never manufacture a score or re-rank."""
    if not features:return pd.DataFrame(),pd.DataFrame(),pd.DataFrame()
    rows=[]
    for x in features:
        rank=_pos(x.get('model_rank'))
        if rank is None:continue
        score=x.get('model_score')
        missing=score is None
        try:
            if score is not None and not math.isfinite(float(score)):missing=True
        except (ValueError,TypeError):missing=True
        checks={k:x.get(k) is None or (isinstance(x.get(k),str) and not x[k].strip())
                for k in ('recent_form','career_starts','track_starts','distance_starts','track_distance_starts')}
        rows.append({'batch_id':x.get('batch_id'),'horse':x.get('horse'),'model_rank':rank,
                     'unscored':missing,'in_top3':rank<=3,
                     **{'missing_'+k:v for k,v in checks.items()},
                     'potential_input_gap':'; '.join(k for k,miss in checks.items() if miss),
                     'diagnosis':'INPUT_AVAILABILITY_OBSERVATION_NOT_PROVEN_ROOT_CAUSE'})
    df=pd.DataFrame(rows)
    summary=df.groupby(['in_top3','unscored'],dropna=False).size().reset_index(name='runners')
    rank=df.groupby('model_rank').agg(total=('unscored','size'),unscored=('unscored','sum')).reset_index()
    rank['unscored_pct']=100*rank['unscored']/rank['total']
    return df[df['unscored']].copy(),summary,rank


def build_all(*,archives,runner_results,historical_races,historical_features,historical_results,
              historical_exclusions):
    """Use existing read-only v061h engine and original v061b backtest logic."""
    import v061h_strategy as strategy
    import v061h_readiness as readiness
    import v061b_historical as history
    snapshots=prospective_accuracy(archives,runner_results)
    tickets=strategy.build_tickets(archives)
    evaluated=strategy.grade(tickets,runner_results) if not tickets.empty else pd.DataFrame()
    cover,reasons=odds_coverage(tickets,evaluated)
    readiness_reports=readiness.summarize(historical_races,historical_features,historical_results,historical_exclusions)
    hist_batches=[{k:v for k,v in a.items() if k!='source_form_payload'} for a in historical_races]
    hist_reports=history.build_reports(hist_batches,historical_features,historical_results)
    distance_summary,distance_races=historical_distance(readiness_reports['race_coverage'],hist_reports['races'])
    unscored,unscored_summary,rank_gaps=unscored_diagnostic(historical_features)
    return {
        'prediction_accuracy':prediction_summary(snapshots),
        'prediction_race_audit':snapshots,
        'betting_portfolios':strategy.aggregate(evaluated),
        'betting_matched_top3':strategy.matched_portfolios(evaluated),
        'betting_individual_eligible':individual_strategy(evaluated),
        'betting_ticket_audit':evaluated,
        'odds_coverage_by_race':cover,
        'odds_missing_reasons':reasons,
        'distance_summary_RESEARCH_ONLY':distance_summary,
        'distance_races_RESEARCH_ONLY':distance_races,
        'unscored_runner_details':unscored,
        'unscored_rank_summary':rank_gaps,
        'unscored_top3_summary':unscored_summary,
    }


def render(secret):
    import streamlit as st
    import v061_prospective as prospective
    import v061b_historical as historical
    st.subheader('🧪 v0.6.1i — Prediction Integrity & Distance Research')
    st.caption('Read-only; frozen v0.2, Bet Tracker, official results and historical archives unchanged.')
    st.warning('Prediction accuracy uses saved pre-scheduled rankings, regardless of odds. Not independently certified actual-off or source-as-of. Distance research is retrospective only; SP ROI is NOT executable odds.')
    if st.button('Run v0.6.1i complete audit',key='v061i_run_all'):
        try:
            db=prospective.archive_client(secret)
            hdb=historical.server_db(secret)
            archives=prospective._read_all(db,prospective.TABLE,order='created_at')
            official=prospective._read_all(db,'runner_results')
            hr=historical.read_all(hdb,historical.RACES,'*','race_date')
            hf=historical.read_all(hdb,historical.FEATURES,'*')
            results=historical.read_all(hdb,historical.RESULTS,'batch_id,result_payload,result_sha256,actual_starters')
            exclusions=historical.read_all(hdb,'v061b_result_exclusions','batch_id,reason,note')
            st.session_state['v061i_reports']=build_all(archives=archives,runner_results=official,
                historical_races=hr,historical_features=hf,historical_results=results,historical_exclusions=exclusions)
        except Exception as exc:
            st.error('v0.6.1i audit stopped safely (no data written): '+str(exc));return
    reports=st.session_state.get('v061i_reports')
    if not reports:return
    for key,frame in reports.items():
        with st.expander(key.replace('_',' ').title(),expanded=key in ('prediction_accuracy','betting_portfolios','distance_summary_RESEARCH_ONLY','unscored_top3_summary')):
            if frame.empty:st.info('No eligible observations in this category yet.')
            else:st.dataframe(frame,hide_index=True,use_container_width=True)
            st.download_button('Download '+key+' CSV',frame.to_csv(index=False).encode('utf-8'),
                               file_name='v061i_'+key+'.csv',mime='text/csv',key='v061i_download_'+key)
