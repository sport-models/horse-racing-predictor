"""v0.6.1p: read-only race-type performance research on archived v0.2 outputs.

All subgroup thresholds predetermined in code. Never refit or modify rankings.
Race attributes are extracted ONLY from stored FormFav pre-result race metadata.
"""
from __future__ import annotations
import re
import math
from collections import Counter
import numpy as np
import pandas as pd

VERSION = 'v0.6.1p'
DISTANCE_BANDS = ('SPRINT_LT_1400','MIDDLE_1400_1999','STAYING_GE_2000','DISTANCE_UNAVAILABLE')
FIELD_BANDS = ('SMALL_2_7','MEDIUM_8_11','LARGE_12_PLUS','FIELD_UNAVAILABLE')
SCORE_BANDS = ('NONE_UNSCORED','ONE_UNSCORED','TWO_PLUS_UNSCORED','SCORE_COVERAGE_UNKNOWN')
MAIDEN_BANDS = ('EXPLICIT_MAIDEN','EXPLICIT_NON_MAIDEN','MAIDEN_UNDETERMINED')


def number(v):
    try:
        if isinstance(v, bool):return None
        n=float(v)
        return n if math.isfinite(n) else None
    except (ValueError, TypeError, OverflowError):return None


def race_metadata(batch):
    """Never derive race-wide metadata by scanning runner objects."""
    raw=batch.get('source_form_payload') or {}
    if not isinstance(raw,dict):return {'distance_m':None,'distance_source':'MISSING_PAYLOAD','maiden_group':'MAIDEN_UNDETERMINED','maiden_source':'MISSING_PAYLOAD'}
    scopes=[('root',raw)]
    for k in ('race','raceInfo','raceDetails','event','meetingRace'):
        if isinstance(raw.get(k),dict):scopes.append((k,raw[k]))
    distances=[]
    for scope,obj in scopes:
        for key in ('distance','distanceMeters','distanceMetres','raceDistance','distance_m','distanceM'):
            if key not in obj:continue
            val=obj[key]
            if isinstance(val,dict):
                unit=str(val.get('unit','m')).lower()
                val=val.get('meters',val.get('metres',val.get('value')))
                if unit not in ('m','meter','meters','metre','metres'):continue
            if isinstance(val,str):
                match=re.fullmatch(r'\s*(\d{3,4})(?:\s*(m|metres|meters))?\s*',val,re.I)
                if not match:continue
                val=match.group(1)
            dist=number(val)
            if dist is not None and dist.is_integer() and 600 <= dist <= 5000:distances.append((int(dist),scope+'.'+key))
    unique={x for x,_ in distances}
    distance,source=(next(iter(unique)),distances[0][1]) if len(unique)==1 else (None,'CONFLICTING_DISTANCES' if unique else 'DISTANCE_MISSING')
    # No inference that unscored runners imply maiden race. Maiden only from an explicit label.
    labels=[]
    for scope,obj in scopes:
        for key in ('raceName','name','raceType','raceClass','class','title','description','category','conditions'):
            v=obj.get(key)
            if isinstance(v,str) and v.strip():labels.append((scope+'.'+key,v))
    explicit_flags=[]
    for scope,obj in scopes:
        for key in ('isMaiden','maiden'):
            if isinstance(obj.get(key),bool):explicit_flags.append((scope+'.'+key,obj[key]))
    has_maiden=any(bool(re.search(r'\bmaiden\b',v,re.I)) for _,v in labels)
    if has_maiden or any(flag for _,flag in explicit_flags):maiden='EXPLICIT_MAIDEN'; maiden_source=';'.join(k for k,v in labels if re.search(r'\bmaiden\b',v,re.I)) or ';'.join(k for k,v in explicit_flags if v)
    elif explicit_flags and all(not flag for _,flag in explicit_flags):maiden='EXPLICIT_NON_MAIDEN';maiden_source=';'.join(k for k,_ in explicit_flags)
    else:maiden='MAIDEN_UNDETERMINED';maiden_source='NO_EXPLICIT_MAIDEN_FLAG'
    return {'distance_m':distance,'distance_source':source,'maiden_group':maiden,'maiden_source':maiden_source}


def make_reports(races: pd.DataFrame, features: pd.DataFrame, batches: list[dict]) -> dict[str,pd.DataFrame]:
    for required,frame,name in [({'batch_id','race_date','status','top1_won','winner_in_top3','actual_starters','original_top1_scratched','winner_count'},races,'races'),
                                 ({'batch_id','model_rank','model_score'},features,'features')]:
        missing=required-set(frame.columns)
        if missing:raise ValueError(f'{name}: missing {sorted(missing)}')
    r=races.copy();f=features.copy()
    r['batch_id']=r.batch_id.astype(str);f['batch_id']=f.batch_id.astype(str)
    if r.batch_id.duplicated().any():raise ValueError('Duplicate race batch IDs: abort analysis')
    if f.batch_id.isna().any():raise ValueError('Missing feature batch IDs')
    # Batches are already archived; no refreshed provider queries, no results as predictors.
    batch_map={}
    for b in batches:
        bid=str(b.get('id') or b.get('batch_id'))
        if bid in batch_map:raise ValueError('Duplicate source-form batch IDs')
        batch_map[bid]=b
    meta=[]
    for bid in r.batch_id:
        obj=batch_map.get(bid)
        vals=race_metadata(obj) if obj is not None else {'distance_m':None,'distance_source':'BATCH_MISSING','maiden_group':'MAIDEN_UNDETERMINED','maiden_source':'BATCH_MISSING'}
        meta.append({'batch_id':bid,**vals})
    m=pd.DataFrame(meta)
    f['rank_numeric']=pd.to_numeric(f.model_rank,errors='coerce')
    f['score_numeric']=pd.to_numeric(f.model_score,errors='coerce').replace([np.inf,-np.inf],np.nan)
    f=f[f.rank_numeric.notna()].copy()
    # Historical reports already contain ranked_count. Use namespaced aggregation
    # columns so pandas never creates ranked_count_x / ranked_count_y after merge.
    cnt=f.groupby('batch_id').agg(_v061p_ranked=('rank_numeric','size'),
                                  _v061p_scored=('score_numeric','count'))
    cnt['_v061p_unscored']=cnt['_v061p_ranked']-cnt['_v061p_scored']
    r=r.merge(m,on='batch_id',validate='one_to_one').merge(cnt,on='batch_id',how='left',validate='one_to_one')
    if 'ranked_count' in r:
        r['archived_ranked_count']=pd.to_numeric(r['ranked_count'],errors='coerce')
    r['ranked_count']=r['_v061p_ranked']
    r['scored_count']=r['_v061p_scored']
    r['unscored_count']=r['_v061p_unscored']
    r=r.drop(columns=['_v061p_ranked','_v061p_scored','_v061p_unscored'])
    r['actual_starters_num']=pd.to_numeric(r.actual_starters,errors='coerce')
    r['winner_count_num']=pd.to_numeric(r.winner_count,errors='coerce')
    r['race_day']=pd.to_datetime(r.race_date,errors='coerce')
    def truth(v):return isinstance(v,(bool,np.bool_)) and v or str(v).lower()=='true'
    r['top1_win']=r.top1_won.apply(truth).astype(int)
    r['top3_win']=r.winner_in_top3.apply(truth).astype(int)
    r['distance_band']=r.distance_m.apply(lambda x:'DISTANCE_UNAVAILABLE' if pd.isna(x) else 'SPRINT_LT_1400' if x<1400 else 'MIDDLE_1400_1999' if x<2000 else 'STAYING_GE_2000')
    r['field_band']=r.actual_starters_num.apply(lambda x:'FIELD_UNAVAILABLE' if pd.isna(x) else 'SMALL_2_7' if x<=7 else 'MEDIUM_8_11' if x<=11 else 'LARGE_12_PLUS')
    r['score_band']=r.unscored_count.apply(lambda x:'SCORE_COVERAGE_UNKNOWN' if pd.isna(x) else 'NONE_UNSCORED' if x==0 else 'ONE_UNSCORED' if x==1 else 'TWO_PLUS_UNSCORED')
    r['baseline_random_win_prob']=r.winner_count_num/r.actual_starters_num
    r['baseline_random_top3_prob']=r.apply(lambda x: (1-math.comb(int(x.actual_starters_num-x.winner_count_num),min(3,int(x.actual_starters_num)))/math.comb(int(x.actual_starters_num),min(3,int(x.actual_starters_num)))) if pd.notna(x.actual_starters_num) and pd.notna(x.winner_count_num) and x.actual_starters_num>=2 and 0<x.winner_count_num<=x.actual_starters_num and x.actual_starters_num-x.winner_count_num>=min(3,int(x.actual_starters_num)) else (1. if pd.notna(x.actual_starters_num) and pd.notna(x.winner_count_num) and x.actual_starters_num>=2 and 0<x.winner_count_num<=x.actual_starters_num else np.nan),axis=1)
    r['eligible']=r.status.eq('EVALUABLE_RETROSPECTIVE_RESEARCH') & ~r.original_top1_scratched.apply(truth) & r.race_day.notna() & r.actual_starters_num.ge(2) & r.actual_starters_num.le(30) & r.winner_count_num.ge(1) & r.winner_count_num.le(r.actual_starters_num) & r.ranked_count.ge(2)
    r['exclusion_reason']=np.where(r.eligible,'',np.where(~r.status.eq('EVALUABLE_RETROSPECTIVE_RESEARCH'),'RACE_STATUS',np.where(r.original_top1_scratched.apply(truth),'SCRATCHED_TOP1','INVALID_OUTCOME_OR_RANK_COVERAGE')))
    valid=r[r.eligible].copy()
    dates=sorted(valid.race_day.dt.date.unique())
    if len(dates)<5:raise ValueError('At least five distinct race days required for chronology')
    split=max(1,min(len(dates)-1,int(math.floor(len(dates)*.7))))
    train_dates=set(dates[:split]);hold_dates=set(dates[split:])
    valid['split']=np.where(valid.race_day.dt.date.isin(train_dates),'DEVELOPMENT','CHRONOLOGICAL_HOLDOUT')
    strata=[]
    for dimension in ('distance_band','field_band','score_band','maiden_group'):
        for (period,category),g in valid.groupby(['split',dimension],dropna=False):
            n=len(g);days=g.race_day.dt.date.nunique()
            exp=float(g.baseline_random_win_prob.sum())
            wins=int(g.top1_win.sum())
            expected_top3=float(g.baseline_random_top3_prob.sum())
            strata.append({'dimension':dimension,'category':category,'split':period,'races':n,'race_days':days,
                'rank1_wins':wins,'rank1_win_pct':wins/n*100,'rank1_random_expected_wins':exp,
                'excess_over_random_pp':(wins-exp)/n*100,'top3_hits':int(g.top3_win.sum()),
                'top3_hit_pct':float(g.top3_win.mean()*100),'top3_random_expected_hits':expected_top3,
                'unscored_races':int((g.unscored_count>0).sum()),'under_30_races':n<30,'exploratory_only':True})
    strata=pd.DataFrame(strata)
    # For inspection: fixed distance x score-coverage segmentation; do not cherry pick winners.
    cross=[]
    for (splitname,dist,coverage),g in valid.groupby(['split','distance_band','score_band']):
        cross.append({'split':splitname,'distance_band':dist,'score_band':coverage,'races':len(g),
          'rank1_wins':int(g.top1_win.sum()),'rank1_win_pct':100*g.top1_win.mean(),
          'random_expected_wins':g.baseline_random_win_prob.sum(),'under_30_races':len(g)<30})
    cross=pd.DataFrame(cross)
    # Date-clustered intervals around descriptive excess vs field-size random; not a test of a new strategy.
    uncertainty=[]
    for (dimension,category),g in valid[valid.split=='CHRONOLOGICAL_HOLDOUT'].assign(dummy=1).pipe(lambda d:pd.concat([d.assign(dimension=dim,category=d[dim]) for dim in ('distance_band','field_band','score_band','maiden_group')],ignore_index=True)).groupby(['dimension','category']):
        delta=(g.top1_win-g.baseline_random_win_prob).to_numpy(float)
        day=g.race_day.dt.date.astype(str).to_numpy();distinct=np.unique(day)
        low=high=np.nan
        if len(distinct)>=5 and len(g)>=30:
            rng=np.random.default_rng(611)
            lookup={d:np.flatnonzero(day==d) for d in distinct}
            samples=[100*float(delta[np.concatenate([lookup[x] for x in rng.choice(distinct,size=len(distinct),replace=True)])].mean()) for _ in range(1000)]
            low,high=np.quantile(samples,[.025,.975])
        uncertainty.append({'dimension':dimension,'category':category,'races':len(g),'days':len(distinct),
            'excess_vs_random_pp':100*delta.mean(),'day_bootstrap_ci_low_pp':low,'day_bootstrap_ci_high_pp':high,
            'note':'Descriptive historical subgroup only; many comparisons, not a prospective signal'})
    uncertainty=pd.DataFrame(uncertainty)
    integrity_cols=['batch_id','race_date','track','race_number','distance_m','distance_source','maiden_group','maiden_source','ranked_count','scored_count','unscored_count','actual_starters_num','status','eligible','exclusion_reason','distance_band','field_band','score_band']
    if 'archived_ranked_count' in r.columns:
        integrity_cols.append('archived_ranked_count')
    integrity=r[integrity_cols].copy()
    coverage=pd.DataFrame([{'version':VERSION,'archived_races':len(r),'eligible_races':len(valid),
        'distance_available_eligible':int(valid.distance_m.notna().sum()),'explicit_maiden_eligible':int((valid.maiden_group=='EXPLICIT_MAIDEN').sum()),
        'explicit_non_maiden_eligible':int((valid.maiden_group=='EXPLICIT_NON_MAIDEN').sum()),
        'maiden_unknown_eligible':int((valid.maiden_group=='MAIDEN_UNDETERMINED').sum()),
        'unscored_field_eligible':int((valid.unscored_count>0).sum()),
        'development_races':int((valid.split=='DEVELOPMENT').sum()),'holdout_races':int((valid.split=='CHRONOLOGICAL_HOLDOUT').sum()),
        'development_last_day':str(dates[split-1]),'holdout_first_day':str(dates[split]),
        'frozen_model':'v0.2','production_changed':False,'new_predictions_generated':False,
        'data_availability':'FormFav features validated as available pre-race per user; archived capture times separately retained',
        'decision':'DESCRIPTIVE_RESEARCH_ONLY_NO_MODEL_PROMOTION'}])
    return {'summary':coverage,'integrity':integrity,'category_performance':strata,'distance_x_score_coverage':cross,
            'holdout_day_bootstrap':uncertainty,'eligible_race_categories':valid[['batch_id','race_date','track','race_number','distance_m','distance_band','field_band','score_band','maiden_group','unscored_count','actual_starters_num','top1_win','top3_win','baseline_random_win_prob','split']].copy()}


def render(secret):
    import streamlit as st
    import v061b_historical as history
    st.subheader('🧪 v0.6.1p — Race type, distance & unscored-runner performance')
    st.caption('Fixed thresholds: sprint <1400m, middle 1400–1999m, staying >=2000m; fields 2–7 / 8–11 / 12+; 0 / 1 / 2+ unscored. Explicit maiden only. Read-only.')
    if st.button('Run v0.6.1p race-type research',key='v061p_run'):
        try:
            client=history.server_db(secret)
            batches=history.read_all(client,history.RACES,'*','race_date')
            features=history.read_all(client,history.FEATURES,'*')
            results=history.read_all(client,history.RESULTS,'batch_id,result_payload,result_sha256,actual_starters')
            historical=history.build_reports(batches,features,results)
            reports=make_reports(historical['races'],pd.DataFrame(features),batches)
            st.session_state['v061p_reports']=reports
        except Exception as exc:
            st.error('v0.6.1p research failed; no database changes made: '+str(exc));return
    reports=st.session_state.get('v061p_reports')
    if not reports:return
    for name,frame in reports.items():
        with st.expander(name.replace('_',' ').title(),expanded=name in ('summary','category_performance')):
            st.dataframe(frame,use_container_width=True,hide_index=True)
            st.download_button('Download '+name,frame.to_csv(index=False).encode(),file_name='v061p_'+name+'.csv',mime='text/csv',key='v061p_dl_'+name)
    st.warning('Historical subgroup descriptions are exploratory, not evidence that a category-specific model improves predictions. Frozen v0.2 unchanged.')
