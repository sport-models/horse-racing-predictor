"""v0.6.2B historical discovery cohort; never pooled with prospective validation."""
from __future__ import annotations
import pandas as pd
from v062b_barrier_lab import _barrier, _unique_field, _distance, DISTANCE_KEYS, MIN_STARTERS, MIN_RACE_DATES

COHORT='HISTORICAL_RESEARCH_POST_RACE_RETRIEVAL'


def build_historical(batches, features, results):
    from v061b_historical import build_reports, canonical, digest, integer
    reports=build_reports(batches,features,results)
    race_table=reports['races']
    if not isinstance(race_table,pd.DataFrame):
        raise TypeError('Historical build_reports races must be a DataFrame')
    eligible={str(row['batch_id']):row for row in race_table.to_dict('records')
              if row['status']=='EVALUABLE_RETROSPECTIVE_RESEARCH'}
    by_batch={}
    for f in features:
        by_batch.setdefault(str(f.get('batch_id')),[]).append(f)
    res={str(r.get('batch_id')):r for r in results}
    rows=[]; issues=[]
    for b in batches:
        bid=str(b['id'])
        if bid not in eligible:continue
        f=by_batch.get(bid,[])
        rr=res.get(bid) or {}
        payload=rr.get('result_payload') or {}
        if digest(payload)!=rr.get('result_sha256'):
            issues.append({'batch_id':bid,'issue':'RESULT_HASH_MISMATCH'})
            continue
        placings=payload.get('placings')
        if not isinstance(placings,list):
            issues.append({'batch_id':bid,'issue':'MISSING_PLACINGS'})
            continue
        starters={canonical(p.get('name')):p for p in placings
                  if isinstance(p,dict) and p.get('scratched') is not True}
        indexed={canonical(x.get('horse_key') or x.get('horse')):x for x in f}
        if not starters or len(starters)!=eligible[bid]['actual_starters'] or set(starters)-set(indexed):
            issues.append({'batch_id':bid,'issue':'STARTER_COVERAGE_MISMATCH'})
            continue
        # Do not invent a distance: use explicit race-level metadata only.
        distance=_unique_field(b.get('source_form_payload'),DISTANCE_KEYS,_distance)
        # Only archived race-level FormFav metadata is admissible. Results
        # payload is deliberately not used to infer pre-race race distance.
        race=[]
        for horse, result in starters.items():
            feature=indexed[horse]
            barrier=_barrier(feature.get('barrier'))
            if barrier is None:
                issues.append({'batch_id':bid,'issue':'MISSING_BARRIER'})
                break
            race.append({
                'cohort':COHORT,'batch_id':bid,'race_date':str(b.get('race_date'))[:10],
                'track':b.get('track'),'race_number':b.get('race_number'),
                'horse':feature.get('horse'),'barrier':barrier,'distance_m':distance,
                'research_rank':feature.get('model_rank'),
                'research_model_score':feature.get('model_score'),
                'research_career_starts':feature.get('career_starts'),
                'actual_starters':len(starters),
                'won':int(integer(result.get('position'))==1),
                'uniform_expected_win':1/len(starters),
                'evidence_status':'POST_RACE_RETRIEVAL_NOT_POINT_IN_TIME',
            })
        if len(race)!=len(starters) or len({x['barrier'] for x in race})!=len(starters):
            issues.append({'batch_id':bid,'issue':'INCOMPLETE_OR_DUPLICATE_BARRIER_EXPOSURE'})
            continue
        rows.extend(race)
    detail=pd.DataFrame(rows)
    if detail.empty:
        return detail,pd.DataFrame(),pd.DataFrame(issues,columns=['batch_id','issue']),pd.DataFrame(reports['races'])
    summary=(detail.groupby(['track','distance_m','barrier'],dropna=False)
             .agg(starters=('won','size'),wins=('won','sum'),
                  expected_wins_uniform=('uniform_expected_win','sum'),
                  race_dates=('race_date','nunique'),
                  races=('batch_id','nunique')).reset_index())
    summary['win_rate']=summary.wins/summary.starters
    summary['excess_wins_vs_uniform']=summary.wins-summary.expected_wins_uniform
    summary['gate']=summary.apply(lambda x:
        'HISTORICAL_EXPLORATION_ONLY' if x.starters>=MIN_STARTERS
        and x.race_dates>=MIN_RACE_DATES else 'INSUFFICIENT_HISTORICAL_EXPOSURE',axis=1)
    return detail,summary,pd.DataFrame(issues,columns=['batch_id','issue']),pd.DataFrame(reports['races'])


def render(secret):
    import streamlit as st
    from v061b_historical import server_db,read_all,RACES,FEATURES,RESULTS
    st.subheader('📚 v0.6.2B — Historical barrier discovery (separate cohort)')
    st.caption('Archived FormFav runner barriers were retrieved after the races. '
               'Results and source timing are not certified pre-race. '
               'Historical findings must not be presented as forward validation.')
    if not st.button('Audit historical barrier coverage',key='v062b_hist_run'):
        return
    try:
        db=server_db(secret)
        batches=read_all(db,RACES,'*','race_date')
        features=read_all(db,FEATURES,'batch_id,horse,horse_key,model_rank,model_score,barrier')
        results=read_all(db,RESULTS,'batch_id,result_payload,result_sha256,actual_starters')
        detail,summary,issues,races=build_historical(batches,features,results)
    except Exception as exc:
        st.error('Historical read-only audit failed: '+str(exc))
        st.exception(exc)
        return
    st.metric('Historical archive races',len(batches))
    st.metric('Complete barrier starter observations',len(detail))
    st.metric('Historical race dates',detail.race_date.nunique() if not detail.empty else 0)
    st.metric('Known-distance starter coverage',int(detail.distance_m.notna().sum()) if not detail.empty else 0)
    if not summary.empty:st.dataframe(summary,hide_index=True,use_container_width=True)
    if not issues.empty:st.dataframe(issues,hide_index=True,use_container_width=True)
    for name,frame in [('historical_starters',detail),('historical_summary',summary),
                       ('historical_issues',issues),('historical_race_coverage',races)]:
        st.download_button('Export '+name,frame.to_csv(index=False).encode(),
                           file_name='v062b_'+name+'.csv',mime='text/csv',key='v062b_'+name)
    st.warning('Discovery cohort only. Missing race distances remain unknown. '
               'No retrospective outcome-based tuning is applied to forward cohorts; '
               'no horse-quality adjustment or production promotion.')
