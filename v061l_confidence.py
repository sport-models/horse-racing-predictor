"""v0.6.1l: Read-only confidence challenger for frozen v0.2 horse rankings.

Retrospective historical form may incorporate later information: RESEARCH ONLY.
Development and validation are chronological; never fit to validation results.
No Supabase writes, no changes to production rankings or betting records.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd

VERSION = 'v0.6.1l'
FROZEN_CONFIG = Path(__file__).with_name('v061l_frozen_config.json')
MIN_TRAIN = 80
MIN_VALIDATION = 40


def _numeric(value):
    try:
        v = float(value)
        return v if np.isfinite(v) else None
    except (ValueError, TypeError, OverflowError):
        return None


def _sigmoid(x):
    return 1.0 / (1.0 + np.exp(-np.clip(x, -35, 35)))


def _design(field, gap, kind, scale=None):
    """Fixed, prespecified feature design. No outcome-informed transformations."""
    f = np.asarray(field, dtype=float)
    g = np.asarray(gap, dtype=float)
    if scale is None:
        scale = 10.0
    log_field = np.log(f / 10.0)
    gap_unit = np.clip(g / scale, -20.0, 20.0)
    if kind == 'FIELD_BASELINE':
        return np.column_stack([np.ones(len(f)), log_field])
    if kind == 'GAP_ONLY_CHALLENGER':
        return np.column_stack([np.ones(len(f)), log_field, gap_unit])
    if kind == 'FIELD_GAP_INTERACTION_CHALLENGER':
        return np.column_stack([np.ones(len(f)), log_field, gap_unit, log_field * gap_unit])
    raise ValueError('Unknown design: ' + str(kind))


def _fit_logistic(X, y, regularization=1.0):
    """Deterministic ridge-penalised logistic regression, intercept unpenalised."""
    if len(y) < MIN_TRAIN or len(np.unique(y)) != 2:
        raise ValueError('Insufficient diverse training outcomes')
    beta = np.zeros(X.shape[1], dtype=float)
    beta[0] = np.log(np.clip(y.mean(), 1e-5, 1-1e-5) / (1-np.clip(y.mean(),1e-5,1-1e-5)))
    penalty = np.eye(X.shape[1]) * regularization
    penalty[0, 0] = 0.0
    for _ in range(120):
        probs = np.clip(_sigmoid(X @ beta), 1e-8, 1-1e-8)
        weights = probs * (1-probs)
        grad = X.T @ (y-probs) - penalty @ beta
        hess = X.T @ (weights[:, None] * X) + penalty + np.eye(X.shape[1]) * 1e-9
        step = np.linalg.solve(hess, grad)
        beta = beta + step
        if np.max(np.abs(step)) < 1e-9:
            break
    return beta


def _metrics(y, p):
    if len(y) == 0:
        return {'n': 0, 'wins': 0, 'observed_win_pct': None, 'mean_prediction_pct': None,
                'brier': None, 'log_loss': None, 'ece_5bin': None}
    p = np.clip(np.asarray(p, dtype=float), 1e-8, 1-1e-8)
    y = np.asarray(y, dtype=float)
    bins = np.minimum((p * 5).astype(int), 4)
    ece = sum(float((bins==i).mean()) * abs(float(y[bins==i].mean()) - float(p[bins==i].mean()))
              for i in range(5) if (bins==i).any())
    return {'n':int(len(y)), 'wins':int(y.sum()), 'observed_win_pct':float(y.mean()*100),
            'mean_prediction_pct':float(p.mean()*100),
            'brier':float(np.mean((p-y)**2)),
            'log_loss':float(-np.mean(y*np.log(p) + (1-y)*np.log(1-p))),
            'ece_5bin':float(ece)}


def _clean_history(stage2_races):
    needed={'batch_id','race_date','status','actual_starters','gap','top1_won',
            'original_top1_scratched','duplicate_top_rank'}
    missing = needed - set(stage2_races.columns)
    if missing:
        raise ValueError('Stage 2 race export missing columns: ' + ', '.join(sorted(missing)))
    df=stage2_races.copy()
    df['race_date']=pd.to_datetime(df['race_date'],errors='coerce')
    df['actual_starters']=pd.to_numeric(df['actual_starters'],errors='coerce')
    df['gap']=pd.to_numeric(df['gap'],errors='coerce')
    df['is_win']=df['top1_won'].astype(str).str.lower().eq('true').astype(int)
    valid=(df['status'].eq('EVALUABLE_RETROSPECTIVE_RESEARCH') &
           df['race_date'].notna() & df['actual_starters'].between(2,30) &
           np.isfinite(df['gap']) & df['gap'].ge(0) &
           ~df['original_top1_scratched'].astype(str).str.lower().eq('true') &
           ~df['duplicate_top_rank'].astype(str).str.lower().eq('true'))
    # Exclude duplicate archival IDs rather than silently picking one.
    valid &= ~df['batch_id'].astype(str).duplicated(keep=False)
    out=df.loc[valid].sort_values(['race_date','batch_id']).copy().reset_index(drop=True)
    out['date_only']=out['race_date'].dt.strftime('%Y-%m-%d')
    return out


def _chronological_split(df):
    dates=np.sort(df['date_only'].unique())
    if len(dates)<5:raise ValueError('Need at least five distinct historical dates')
    boundary=int(np.floor(len(dates)*.70))
    boundary=max(1,min(boundary,len(dates)-1))
    train=df[df['date_only'].isin(dates[:boundary])].copy()
    validation=df[df['date_only'].isin(dates[boundary:])].copy()
    if len(train)<MIN_TRAIN or len(validation)<MIN_VALIDATION:
        raise ValueError('Not enough development / holdout races')
    assert train['date_only'].max()<validation['date_only'].min()
    return train,validation


def _bootstrap_by_day(holdout, baseline_p, challenger_p, iterations=500):
    """Day-cluster paired Brier delta CI, fixed random seed; exploratory only."""
    dayids=holdout['date_only'].to_numpy()
    unique=np.unique(dayids)
    if len(unique)<5:return {'ci_low':None,'ci_high':None,'p_better':None,'bootstrap_days':len(unique)}
    by_day={d:np.flatnonzero(dayids==d) for d in unique}
    y=holdout['is_win'].to_numpy(dtype=float)
    rng=np.random.default_rng(611)
    values=[]
    for _ in range(iterations):
        dates=rng.choice(unique,size=len(unique),replace=True)
        ix=np.concatenate([by_day[d] for d in dates])
        delta=np.mean((baseline_p[ix]-y[ix])**2-(challenger_p[ix]-y[ix])**2)
        values.append(delta)
    return {'ci_low':float(np.quantile(values,.025)),'ci_high':float(np.quantile(values,.975)),
            'p_better':float(np.mean(np.asarray(values)>0)),'bootstrap_days':len(unique)}


def build_historical(stage2_races):
    clean=_clean_history(stage2_races)
    train,holdout=_chronological_split(clean)
    models=('FIELD_BASELINE','GAP_ONLY_CHALLENGER','FIELD_GAP_INTERACTION_CHALLENGER')
    results=[]; pred=holdout[['batch_id','race_date','date_only','actual_starters','gap','is_win']].copy()
    registry={'version':VERSION,'frozen_production_model':'v0.2','created_by':'historical retrospective research',
              'training_start':train['date_only'].min(),'training_end':train['date_only'].max(),
              'holdout_start':holdout['date_only'].min(),'holdout_end':holdout['date_only'].max(),
              'training_races':len(train),'holdout_races':len(holdout),'models':{},
              'WARNING':'Not an independent point-in-time model. Predictions on already-run races are retroactively calculated; never label as pre-race shadow evidence.'}
    for kind in models:
        tX=_design(train['actual_starters'],train['gap'],kind)
        hX=_design(holdout['actual_starters'],holdout['gap'],kind)
        coef=_fit_logistic(tX,train['is_win'].to_numpy(dtype=float))
        tp=_sigmoid(tX@coef);hp=_sigmoid(hX@coef)
        for split,part,predictions in [('DEVELOPMENT',train,tp),('CHRONOLOGICAL_HOLDOUT',holdout,hp)]:
            results.append({'model':kind,'split':split,**_metrics(part['is_win'],predictions)})
        pred[kind+'_prob']=hp
        registry['models'][kind]={'coefficients':[float(x) for x in coef], 'features':'intercept,log(field_size/10),gap/10,[interaction if applicable]','l2_penalty':1.0}
    base=pred[models[0]+'_prob'].to_numpy()
    y=pred['is_win'].to_numpy(dtype=float)
    comparison=[]
    for kind in models[1:]:
        p=pred[kind+'_prob'].to_numpy()
        boot=_bootstrap_by_day(holdout,base,p)
        comparison.append({'challenger':kind,'holdout_races':len(holdout),
                           'brier_baseline':_metrics(y,base)['brier'],
                           'brier_challenger':_metrics(y,p)['brier'],
                           'brier_improvement_positive_is_better':float(np.mean((base-y)**2)-np.mean((p-y)**2)),
                           'logloss_improvement_positive_is_better':float(_metrics(y,base)['log_loss']-_metrics(y,p)['log_loss']),
                           'day_cluster_brier_improvement_ci_low':boot['ci_low'],
                           'day_cluster_brier_improvement_ci_high':boot['ci_high'],
                           'bootstrap_fraction_positive_NOT_a_pvalue':boot['p_better'],
                           'number_of_holdout_dates':boot['bootstrap_days'],
                           'promotion':'BLOCKED_PROSPECTIVE_NOT_CERTIFIED'})
    registry['training_dataset_sha256']=hashlib.sha256(
        train[['batch_id','date_only','actual_starters','gap','is_win']].to_csv(index=False).encode()).hexdigest()
    registry['config_sha256']=hashlib.sha256(json.dumps(registry,sort_keys=True).encode()).hexdigest()
    excluded=pd.DataFrame([{'original_archived_rows':len(stage2_races),'eligible_scored_rows':len(clean),
         'excluded_or_missing_gap_rows':len(stage2_races)-len(clean),'training_races':len(train),
         'holdout_races':len(holdout),'train_through':train['date_only'].max(),
         'holdout_from':holdout['date_only'].min(),
         'warning':'Historical after-race form: outcomes may leak into features. This is an exploratory split only.'}])
    return {'metrics':pd.DataFrame(results),'holdout_predictions':pred,
            'holdout_comparison':pd.DataFrame(comparison),'coverage':excluded,
            'config':registry}


def _shadow_rows(reconciled_races, archives, config):
    """Retrospective application to immutable pre-schedule ranks; NOT a preregistered shadow bet."""
    if not isinstance(config,dict) or config.get('version')!=VERSION:
        return pd.DataFrame()
    archive_by_batch={str(a.get('batch_id')):a for a in archives}
    rows=[]
    for r in reconciled_races.to_dict('records'):
        batch=str(r.get('batch_id'))
        a=archive_by_batch.get(batch)
        if not a:continue
        ranks=(a.get('payload') or {}).get('rankings') or []
        byrank={x.get('rank'):x for x in ranks if isinstance(x,dict)}
        one=byrank.get(1);two=byrank.get(2)
        # Use ONLY the locked pre-schedule field size. Official final field
        # sizes can reveal later scratches and MUST NOT enter a shadow input.
        n=float(len(ranks)) if ranks else None
        s1=_numeric(one.get('score')) if one else None
        s2=_numeric(two.get('score')) if two else None
        gap=s1-s2 if s1 is not None and s2 is not None else None
        row={'batch_id':batch,'race_date':r.get('race_date'),'track':r.get('track'),
             'race_number':r.get('race_number'),'strict_status':r.get('strict_status'),
             'strict_ok':r.get('strict_ok'),'locked_field_size_used':n,'rank1_gap':gap,
             'top1_won':r.get('top1_won'),'config_sha256':config.get('config_sha256'),
             'shadow_status':'RETROACTIVE_SHADOW_NOT_PRE_REGISTERED',
             'actual_off_verified':False,'promotion':'BLOCKED'}
        if n is None or n<2 or n>30 or gap is None or gap<0:
            row['shadow_status']='MISSING_OR_INVALID_LOCKED_INPUTS'
        elif r.get('strict_ok') is not True:
            row['shadow_status']='RACE_NOT_STRICTLY_RECONCILED'
        else:
            for kind,detail in config.get('models',{}).items():
                X=_design([n],[gap],kind)
                coeff=np.asarray(detail['coefficients'],dtype=float)
                if len(coeff)!=X.shape[1]:raise ValueError('Bad frozen coefficient length')
                row[kind+'_p']=float(_sigmoid(X@coeff)[0])
        rows.append(row)
    return pd.DataFrame(rows)


def render(secret):
    import streamlit as st
    import v061k_stage2 as stage2
    import v061k_integrity as integrity
    import v061_prospective as prospective
    import v061b_historical as history
    st.subheader('🧪 v0.6.1l — Score Confidence & Field-Size Validation')
    st.warning('READ ONLY. v0.2 scorer is unchanged. Historical FormFav snapshots were retrieved AFTER racing; chronological holdout is NOT a certified out-of-sample point-in-time test.')
    if st.button('Run v0.6.1l chronological confidence challengers',key='v061l_run'):
        try:
            client=history.server_db(secret)
            races=history.read_all(client,history.RACES,'id,race_date,track,track_key,track_slug,race_number,runner_count,predicted_count,source_form_sha256,scorer_sha256,cohort','race_date')
            feats=history.read_all(client,history.FEATURES,'batch_id,horse,horse_key,model_rank,model_score')
            results=history.read_all(client,history.RESULTS,'batch_id,result_payload,result_sha256,actual_starters')
            hist=history.build_reports(races,feats,results)
            stage=stage2.reports_from_frames(hist['races'],pd.DataFrame(feats))
            rep=build_historical(stage['stage2_races'])
            archives=prospective._read_all(client,prospective.TABLE,order='created_at')
            official=prospective._read_all(client,'runner_results')
            strict=integrity.build_reports(archives,official)
            rep['shadow_replay']=_shadow_rows(strict['race_reconciliation'],archives,rep['config'])
            st.session_state['v061l_reports']=rep
        except Exception as exc:
            st.error('v0.6.1l audit stopped without writes: '+str(exc));return
    reports=st.session_state.get('v061l_reports')
    if not reports:return
    st.info('Pakenham/Geelong locks can be collected immediately. This dashboard only retrospectively applies the research rule to saved scores; it does not claim rules were preregistered at race lock or calculate betting ROI.')
    for key in ('coverage','metrics','holdout_comparison','holdout_predictions','shadow_replay'):
        frame=reports[key]
        with st.expander(key.replace('_',' ').title(),expanded=key in ('coverage','metrics','holdout_comparison')):
            st.dataframe(frame,hide_index=True,use_container_width=True) if not frame.empty else st.info('No rows yet.')
            st.download_button('Download '+key+' CSV',frame.to_csv(index=False).encode('utf-8'),
                file_name='v061l_'+key+'.csv',mime='text/csv',key='v061l_'+key+'_csv')
    st.download_button('Download research calibration JSON (NOT a live approved model)',
        json.dumps(reports['config'],indent=2,sort_keys=True).encode(),
        file_name='v061l_research_calibration_NOT_APPROVED.json',mime='application/json',key='v061l_json')
