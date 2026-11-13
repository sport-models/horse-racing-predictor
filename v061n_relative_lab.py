"""v0.6.1n | Race-relative research and feature-evidence audit.

Read-only. No ranking changes. Historical FormFav feature availability is
user-validated as pre-race; the archive's *retrieval* timestamps are later.
Those facts are recorded separately rather than relabelling old archives.
"""
from __future__ import annotations

import hashlib
import json
import math
import numpy as np
import pandas as pd

VERSION = 'v0.6.1n'
FIELD_FEATURES = ('rank1_vs_mean','rank1_vs_median','rank1_z','score_sd',
                  'score_range','top3_share','top1_share','score_cv')


def _numbers(series):
    return pd.to_numeric(series, errors='coerce').replace([np.inf,-np.inf],np.nan)


def _true(series):
    return series.astype(str).str.strip().str.casefold().eq('true')


def _valid_ids(df, name):
    if 'batch_id' not in df.columns:
        raise ValueError(f'{name}: missing batch_id')
    if df['batch_id'].isna().any():
        raise ValueError(f'{name}: blank batch_id')


def build_reports(races: pd.DataFrame, features: pd.DataFrame) -> dict[str,pd.DataFrame]:
    """One observation per immutable historical batch; no outcome in feature construction."""
    required_races={'batch_id','race_date','status','actual_starters','top1_won','winner_in_top3',
                    'original_top1_scratched','rank1_score','rank2_score'}
    required_features={'batch_id','model_rank','model_score','horse'}
    for name, frame, required in [('races',races,required_races),('features',features,required_features)]:
        miss=required-set(frame.columns)
        if miss: raise ValueError(f'{name} missing columns: {sorted(miss)}')
        _valid_ids(frame,name)
    rf=races.copy(); ft=features.copy()
    rf['batch_id']=rf['batch_id'].astype(str); ft['batch_id']=ft['batch_id'].astype(str)
    # Duplicate batches (even exact duplicates) are excluded, never silently chosen.
    bad_race_ids=set(rf.loc[rf.duplicated('batch_id',keep=False),'batch_id'])
    bad_rank_ids=set(ft.loc[ft.duplicated(['batch_id','model_rank'],keep=False) & ft['model_rank'].notna(),'batch_id'])
    ft['rank_num']=_numbers(ft['model_rank'])
    ft['score_num']=_numbers(ft['model_score'])
    ft['horse_name']=ft['horse'].astype(str).str.strip().str.casefold()
    bad_horse_ids=set(ft.loc[ft.duplicated(['batch_id','horse_name'],keep=False),'batch_id'])
    grouped=ft.groupby('batch_id',sort=False)
    rows=[]
    for bid,g in grouped:
        issues=[]
        if bid in bad_rank_ids:issues.append('DUPLICATE_RANK')
        if bid in bad_horse_ids:issues.append('DUPLICATE_HORSE')
        rankvals=g['rank_num'].dropna()
        if (rankvals%1!=0).any() or sorted(rankvals.tolist())!=list(range(1,len(rankvals)+1)):
            issues.append('INVALID_OR_MISSING_RANK_SEQUENCE')
        sorted_g=g[g['rank_num'].notna()].sort_values('rank_num',kind='mergesort')
        scored=sorted_g['score_num'].dropna().to_numpy(dtype=float)
        top=sorted_g.loc[sorted_g['rank_num'].eq(1),'score_num']
        second=sorted_g.loc[sorted_g['rank_num'].eq(2),'score_num']
        s1=float(top.iloc[0]) if len(top)==1 and pd.notna(top.iloc[0]) else np.nan
        s2=float(second.iloc[0]) if len(second)==1 and pd.notna(second.iloc[0]) else np.nan
        # Ranking engine sorts null scores last. Never substitute a later ranked horse.
        n=len(sorted_g); ns=len(scored)
        if n<2:issues.append('TOO_FEW_LOCKED_RUNNERS')
        if ns<3:issues.append('FEWER_THAN_THREE_SCORED_RUNNERS')
        if not np.isfinite(s1) or not np.isfinite(s2): issues.append('MISSING_TOP_TWO_SCORE')
        if ns and (np.diff(scored)>1e-7).any(): issues.append('RANK_SCORE_ORDER_CONFLICT')
        mean=float(np.mean(scored)) if ns else np.nan
        median=float(np.median(scored)) if ns else np.nan
        sd=float(np.std(scored,ddof=0)) if ns else np.nan
        score_min=float(np.min(scored)) if ns else np.nan
        # Shares only for positive scores, and are reported as uninterpretable if
        # any score is missing (incomplete field) or scores can be negative.
        complete=ns==n and ns>=3 and np.all(scored>=0) and np.sum(scored)>0
        top3_share=float(sum(scored[:3])/sum(scored)) if complete else np.nan
        top1_share=float(s1/sum(scored)) if complete and np.isfinite(s1) else np.nan
        rows.append({'batch_id':bid,'feature_runner_rows':len(g),'ranked_runner_rows':n,'scored_runner_rows':ns,
          'score_coverage_pct':100*ns/n if n else np.nan,
          'rank1_score_feature':s1,'rank2_score_feature':s2,
          'rank1_gap_feature':s1-s2 if np.isfinite(s1) and np.isfinite(s2) else np.nan,
          'field_mean_score':mean,'field_median_score':median,'score_sd':sd,
          'score_range':float(np.max(scored)-score_min) if ns else np.nan,
          'rank1_vs_mean':s1-mean if np.isfinite(s1) and np.isfinite(mean) else np.nan,
          'rank1_vs_median':s1-median if np.isfinite(s1) and np.isfinite(median) else np.nan,
          'rank1_z':(s1-mean)/sd if np.isfinite(s1) and np.isfinite(sd) and sd>0 else np.nan,
          'top3_share':top3_share,'top1_share':top1_share,
          'score_cv':sd/mean if np.isfinite(sd) and np.isfinite(mean) and mean>0 else np.nan,
          'field_coverage_complete':bool(complete),'feature_integrity_issues':'|'.join(issues)})
    ff=pd.DataFrame(rows)
    out=rf.merge(ff,on='batch_id',how='left',validate='many_to_one',indicator='feature_join')
    out['date']=pd.to_datetime(out['race_date'],errors='coerce')
    out['actual_starters_num']=_numbers(out['actual_starters'])
    out['historical_rank1_score']=_numbers(out['rank1_score'])
    out['historical_rank2_score']=_numbers(out['rank2_score'])
    out['score_parity_ok']=(np.isclose(out['historical_rank1_score'],out['rank1_score_feature'],rtol=0,atol=1e-6,equal_nan=True)&
                            np.isclose(out['historical_rank2_score'],out['rank2_score_feature'],rtol=0,atol=1e-6,equal_nan=True))
    out['historical_retrieval_class']=out.get('point_in_time_class',pd.Series('NOT_RECORDED',index=out.index))
    # Pre-race availability was verified by user separately; timestamp-in-archive
    # is NOT automatically a pre-race retrieval proof. Do not forge a certification.
    out['form_availability_assessment']='USER_CONFIRMED_PRE_RACE_AVAILABILITY'
    out['archive_asof_certified']=_true(out['certified_pre_race']) if 'certified_pre_race' in out else False
    out['is_evaluable']=(out['status'].eq('EVALUABLE_RETROSPECTIVE_RESEARCH')&
      ~_true(out['original_top1_scratched'])&out['actual_starters_num'].between(2,30)&
      out['date'].notna()&~out['batch_id'].isin(bad_race_ids)&
      out['feature_integrity_issues'].fillna('').eq('')&out['score_parity_ok']&
      out['field_coverage_complete'].eq(True)&out['feature_join'].eq('both'))
    # Unscored fields are kept in the integrity audit; not used for relative
    # feature comparisons, which would otherwise compare different populations.
    out['is_win']=_true(out['top1_won']).astype(int)
    out['top3_hit']=_true(out['winner_in_top3']).astype(int)
    audit=out[['batch_id','race_date','track','race_number','status','historical_retrieval_class',
       'form_availability_assessment','archive_asof_certified','feature_join',
       'feature_runner_rows','ranked_runner_rows','scored_runner_rows','score_coverage_pct','field_coverage_complete',
       'feature_integrity_issues','score_parity_ok','is_evaluable']].copy()
    audit['exclusion_reason']=np.where(out['is_evaluable'],'',
        np.where(out['feature_join'].ne('both'),'MISSING_FEATURES',
        np.where(out['batch_id'].isin(bad_race_ids),'DUPLICATE_RACE',
        np.where(out['feature_integrity_issues'].fillna('').ne(''),'FEATURE_INTEGRITY',
        np.where(~out['score_parity_ok'],'SCORE_PARITY',
        np.where(~out['field_coverage_complete'].eq(True),'INCOMPLETE_SCORE_FIELD',
        'RACE_STATUS_OR_OUTCOME'))))))
    eligible=out[out['is_evaluable']].copy().sort_values(['date','batch_id']).reset_index(drop=True)
    report_cols=['batch_id','race_date','track','race_number','actual_starters_num',
        'rank1_score_feature','rank2_score_feature','rank1_gap_feature','field_mean_score',
        'field_median_score']+list(FIELD_FEATURES)+['is_win','top3_hit']
    # Chronological holdout by WHOLE race day (no interleaving).
    holdout=eligible.iloc[0:0].copy();dev=eligible.iloc[0:0].copy()
    dates=sorted(eligible['date'].dt.date.unique())
    if len(dates)>=5:
        cut=max(1,min(len(dates)-1,int(math.floor(.7*len(dates)))))
        dev=eligible[eligible['date'].dt.date.isin(dates[:cut])].copy()
        holdout=eligible[eligible['date'].dt.date.isin(dates[cut:])].copy()
    eligible['research_split']='NONE'
    eligible.loc[eligible['batch_id'].isin(dev['batch_id']),'research_split']='DEVELOPMENT'
    eligible.loc[eligible['batch_id'].isin(holdout['batch_id']),'research_split']='CHRONOLOGICAL_HOLDOUT'
    # Quartile boundaries from DEVELOPMENT ONLY; no tuning using outcomes.
    strata=[];correlation=[];quantiles=[]
    for feature in FIELD_FEATURES:
        d=_numbers(dev[feature]).dropna();h=_numbers(holdout[feature]).dropna()
        if len(d)<40 or len(h)<15:continue
        edges=np.quantile(d,[0,.25,.50,.75,1]).astype(float)
        unique=np.unique(edges)
        quantiles.append({'feature':feature,'development_rows':len(d),'holdout_rows':len(h),
          'development_q25':edges[1],'development_q50':edges[2],'development_q75':edges[3],
          'distinct_edges':len(unique),'rule':'QUARTILES_FIXED_ON_DEVELOPMENT_ONLY'})
        for split,part in [('DEVELOPMENT',dev),('CHRONOLOGICAL_HOLDOUT',holdout)]:
            vals=_numbers(part[feature])
            mask=vals.notna()
            if int(mask.sum())<15:continue
            dat=part.loc[mask].copy()
            # Within a split, correlations are descriptive only; no rank shifts.
            rho=dat[feature].corr(dat['is_win'],method='spearman') if dat[feature].nunique()>1 else np.nan
            correlation.append({'feature':feature,'split':split,'races':len(dat),
              'spearman_vs_rank1_win':rho,'rank1_wins':int(dat['is_win'].sum()),
              'rank1_win_rate_pct':100*dat['is_win'].mean(),
              'causal_or_prospective_claim':False})
            if len(unique)<3:continue
            # Using threshold values deduplicates tied quantiles gracefully.
            bucket=np.searchsorted(unique[1:-1],dat[feature].to_numpy(float),side='right')+1
            dat['bucket']=bucket
            for bn,g in dat.groupby('bucket'):
                expected=float(_numbers(g['actual_starters_num']).rdiv(1).sum())
                strata.append({'feature':feature,'split':split,'bucket':int(bn),
                  'bucket_lower_bound':float(unique[int(bn)-1]),
                  'bucket_upper_bound':float(unique[int(bn)]) if int(bn)<len(unique) else float(unique[-1]),
                  'races':len(g),'rank1_wins':int(g['is_win'].sum()),
                  'rank1_win_pct':100*g['is_win'].mean(),
                  'top3_hit_pct':100*g['top3_hit'].mean(),
                  'random_rank1_expected_wins':expected,
                  'excess_vs_random_pp':100*(g['is_win'].sum()-expected)/len(g),
                  'under_30_races':len(g)<30})
    parity=pd.DataFrame([{'transform':'score - field_mean','rank_order_preserved':True,
       'reason':'subtracting a race-wide constant cannot alter within-race ranks'},
       {'transform':'(score - mean) / positive_sd','rank_order_preserved':True,
       'reason':'division by strictly positive common SD preserves rank order'},
       {'transform':'score - field_median','rank_order_preserved':True,
       'reason':'subtracting a race-wide constant cannot alter within-race ranks'}])
    summary=pd.DataFrame([{'version':VERSION,'archived_races':len(out),'feature_batches':len(ff),
      'evaluable_full_score_races':len(eligible),'development_races':len(dev),
      'holdout_races':len(holdout),'duplicate_race_batches':len(bad_race_ids),
      'score_parity_failures':int((~out['score_parity_ok']).sum()),
      'archive_asof_certified_count':int(out['archive_asof_certified'].sum()),
      'provenance_statement':'User confirmed FormFav features were available before racing; archive retrieval classification and independent as-of evidence remain separately reported.',
      'production_v02_changed':False,'new_rankings_generated':False,'research_only':True,
      'no_model_promotion':True}])
    return {'summary':summary,'integrity_audit':audit,
      'race_features':eligible[report_cols+['research_split']].copy(),
      'train_quantile_thresholds':pd.DataFrame(quantiles),
      'rank1_associations':pd.DataFrame(correlation),'train_holdout_strata':pd.DataFrame(strata),
      'ranking_invariance':parity}


def render(secret):
    import streamlit as st
    import v061b_historical as history
    import v061k_stage2 as stage2
    st.subheader('🧪 v0.6.1n — Race-relative score & integrity lab')
    st.caption('Research-only, reads historical archives. FormFav pre-race feature availability: user-validated. Original archive retrieval timestamps / provenance labels are preserved.')
    st.info('Mean-centering and standardising scores DO NOT change horse rankings. This lab tests whether race-relative measures explain Rank 1 success; it does not create a new ranking challenger or promise betting profit.')
    if st.button('Run v0.6.1n field-relative research',key='v061n_run'):
        try:
            client=history.server_db(secret)
            races=history.read_all(client,history.RACES,'id,race_date,track,track_key,track_slug,race_number,runner_count,predicted_count,source_form_sha256,scorer_sha256,cohort','race_date')
            feats=history.read_all(client,history.FEATURES,'batch_id,horse,horse_key,model_rank,model_score')
            results=history.read_all(client,history.RESULTS,'batch_id,result_payload,result_sha256,actual_starters')
            history_reports=history.build_reports(races,feats,results)
            stage=stage2.reports_from_frames(history_reports['races'],pd.DataFrame(feats))
            st.session_state['v061n_reports']=build_reports(stage['stage2_races'],pd.DataFrame(feats))
        except Exception as exc:
            st.error('Research halted without modifying Supabase: '+str(exc));return
    rep=st.session_state.get('v061n_reports')
    if not rep:return
    for name,frame in rep.items():
        with st.expander(name.replace('_',' ').title(),expanded=name in ('summary','rank1_associations','train_holdout_strata')):
            if frame.empty:st.info('No eligible observations in this section')
            else:st.dataframe(frame,use_container_width=True,hide_index=True)
            st.download_button('Export '+name,frame.to_csv(index=False).encode('utf-8'),
               file_name='v061n_'+name+'.csv',mime='text/csv',key='v061n_'+name+'_download')
    st.warning('Historical association is not independent prospective proof. Frozen v0.2 remains unchanged. No Supabase writes or new betting rules.')
