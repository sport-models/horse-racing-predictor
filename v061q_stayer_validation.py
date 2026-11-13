"""v0.6.1q: read-only, predetermined staying-race and class validation.

No recalculation of v0.2 ranks, no source updates, no model promotion.
Archive records are analysed using saved v0.6.1b runner factors.
"""
from __future__ import annotations
import math
import re
import numpy as np
import pandas as pd

VERSION = 'v0.6.1q'
CLASS_KEYS = ('raceClass', 'raceType', 'class', 'category', 'conditions')


def _num(s):
    return pd.to_numeric(s, errors='coerce').replace([np.inf, -np.inf], np.nan)


def class_label(batch):
    """Only explicitly typed race/class metadata, never from runner/result records."""
    p = (batch or {}).get('source_form_payload') or {}
    if not isinstance(p, dict):
        return ('CLASS_UNDETERMINED', 'NOT_DICT')
    found = []
    for scope, obj in [('root',p)] + [(k,p[k]) for k in ('race','raceInfo','raceDetails','event','meetingRace') if isinstance(p.get(k),dict)]:
        for k in CLASS_KEYS:
            value = obj.get(k)
            if isinstance(value,str) and value.strip():
                found.append((re.sub(r'\s+',' ',value.strip()).upper(),scope+'.'+k))
    unique = {v for v,_ in found}
    if len(unique)==1:
        v, src = found[0]
        return (v[:90], src)
    return ('CLASS_CONFLICT' if unique else 'CLASS_UNDETERMINED', 'CONFLICTING_EXPLICIT_CLASSES' if unique else 'NO_EXPLICIT_CLASS')


def _truth(s):
    return s.astype(str).str.casefold().eq('true')


def _day_interval(d, seed=611, reps=1000):
    """Paired race-day bootstrap for adjusted subgroup rate comparisons."""
    days = np.array(sorted(d['race_date'].astype(str).unique()))
    if len(days)<5 or d['staying'].sum()<20 or (~d['staying']).sum()<20:
        return (np.nan,np.nan)
    groups={day:d[d['race_date'].astype(str)==day] for day in days}
    rng=np.random.default_rng(seed);vals=[]
    for _ in range(reps):
        sample=pd.concat([groups[x] for x in rng.choice(days,size=len(days),replace=True)])
        a=sample[sample.staying];b=sample[~sample.staying]
        if len(a)<2 or len(b)<2:continue
        delta=(a.win-a.random_p).mean()-(b.win-b.random_p).mean()
        vals.append(100*float(delta))
    if len(vals)<100:return np.nan,np.nan
    return tuple(np.quantile(vals,[.025,.975]))


def _irls(X,y,lam=4.0):
    """Fixed-ridge logistic regression, intercept not penalised.
    This is an exploratory adjustment, not a causal test or selected winner model.
    """
    beta=np.zeros(X.shape[1]); penalty=np.eye(X.shape[1])*lam;penalty[0,0]=0
    for _ in range(100):
        z=np.clip(X@beta,-30,30);p=1/(1+np.exp(-z))
        w=np.clip(p*(1-p),1e-6,None)
        grad=X.T@(y-p)-penalty@beta
        hess=(X.T*w)@X+penalty
        try:change=np.linalg.solve(hess,grad)
        except np.linalg.LinAlgError:raise ValueError('Singular adjustment system')
        beta+=change
        if np.max(abs(change))<1e-9:break
    return beta


def _design(data, spec):
    cols=[np.ones(len(data)),np.log(data['actual_starters_num'].to_numpy(float)/10)]
    for name in ('any_unscored','explicit_maiden','top1_career_log','top1_distance_log', 'field_career_log'):
        if name in spec:cols.append(data[name].to_numpy(float))
    if 'staying' in spec:cols.append(data['staying'].to_numpy(float))
    return np.column_stack(cols)


def make_reports(races:pd.DataFrame, features:pd.DataFrame, batches:list[dict]):
    required_r={'batch_id','race_date','status','actual_starters','top1_won','winner_in_top3','original_top1_scratched','winner_count'}
    required_f={'batch_id','model_rank','model_score','career_starts','distance_starts','track_starts'}
    for name,df,need in [('races',races,required_r),('features',features,required_f)]:
        missing=need-set(df.columns)
        if missing:raise ValueError(f'{name}: required columns absent: {sorted(missing)}')
        if df.batch_id.isna().any():raise ValueError(f'{name}: null batch IDs')
    import v061p_race_type_lab as p
    # v0.6.1p remains the single source of eligibility and fixed subgroup boundaries.
    base=p.make_reports(races,features,batches)
    r=base['eligible_race_categories'].copy()
    if not len(r):raise ValueError('No eligible races')
    ids=r.batch_id.astype(str)
    feats=features.copy();feats['batch_id']=feats.batch_id.astype(str)
    feats['rank_numeric']=_num(feats.model_rank)
    feats=feats.loc[feats.rank_numeric.notna()].copy()
    for k in ('career_starts','distance_starts','track_starts'):
        feats[k+'_num']=_num(feats[k])
        # A negative number is invalid and never interpreted as negative experience.
        feats.loc[feats[k+'_num']<0,k+'_num']=np.nan
    if feats.duplicated(['batch_id','rank_numeric']).any():
        raise ValueError('Duplicated historical ranked runners: cannot attribute experience')
    top=feats[feats.rank_numeric.eq(1)].set_index('batch_id')
    grouped=feats.groupby('batch_id')
    experience=grouped.agg(field_career_mean=('career_starts_num','mean'),
                           field_career_observed=('career_starts_num','count'),
                           field_distance_mean=('distance_starts_num','mean'),
                           field_distance_observed=('distance_starts_num','count'),
                           scored_count=('model_score','count'),runner_count=('rank_numeric','size'))
    for k in ('career_starts_num','distance_starts_num','track_starts_num'):
        experience['top1_'+k]=top[k]
    r['batch_id']=ids
    r=r.merge(experience,left_on='batch_id',right_index=True,how='left',validate='one_to_one')
    batchmap={}
    for b in batches:
        key=str(b.get('id') or b.get('batch_id'))
        if key in batchmap:raise ValueError('Duplicate source batches')
        batchmap[key]=b
    classes=[class_label(batchmap.get(bid)) for bid in r.batch_id]
    r['explicit_race_class']=[x[0] for x in classes]
    r['race_class_source']=[x[1] for x in classes]
    r['win']=_num(r.top1_win)
    r['top3']=_num(r.top3_win)
    r['random_p']=_num(r.baseline_random_win_prob)
    r['staying']=r.distance_band.eq('STAYING_GE_2000')
    r['any_unscored']=r.unscored_count.gt(0).astype(int)
    r['explicit_maiden']=r.maiden_group.eq('EXPLICIT_MAIDEN').astype(int)
    r['top1_career_log']=np.log1p(_num(r.top1_career_starts_num).clip(lower=0).fillna(0))
    r['top1_distance_log']=np.log1p(_num(r.top1_distance_starts_num).clip(lower=0).fillna(0))
    r['field_career_log']=np.log1p(_num(r.field_career_mean).clip(lower=0).fillna(0))
    r['top1_career_missing']=r.top1_career_starts_num.isna()
    r['top1_distance_missing']=r.top1_distance_starts_num.isna()
    r['field_career_missing']=r.field_career_observed.lt(r.runner_count)
    # These missingness dimensions remain visible. Do not silently assume zero starts.
    r['experience_complete']=(~r.top1_career_missing & ~r.top1_distance_missing &
                              ~r.field_career_missing & r.field_distance_observed.eq(r.runner_count))
    r['top1_career_band']=pd.cut(_num(r.top1_career_starts_num),[-.1,4,19,float('inf')],labels=['0_TO_4','5_TO_19','20_PLUS']).astype('object').fillna('UNKNOWN')
    r['top1_distance_band']=pd.cut(_num(r.top1_distance_starts_num),[-.1,0,2,float('inf')],labels=['ZERO','1_TO_2','3_PLUS']).astype('object').fillna('UNKNOWN')
    r['field_experience_band']=pd.cut(_num(r.field_career_mean),[-.1,4,14,float('inf')],labels=['LOW_0_TO_4','MID_5_TO_14','HIGH_15_PLUS']).astype('object').fillna('UNKNOWN')
    r['reconstructed_score_coverage_ok']=r.scored_count.add(r.unscored_count).eq(r.runner_count)
    if not r.reconstructed_score_coverage_ok.all():
        raise ValueError('Historical score coverage and runner count disagree')
    train=r[r.split=='DEVELOPMENT'].copy();hold=r[r.split=='CHRONOLOGICAL_HOLDOUT'].copy()
    if train.empty or hold.empty:raise ValueError('Missing chronological development or holdout cohort')
    # Fixed, descriptive breakdowns; unknown class is not treated as confirmed non-maiden.
    tables=[]
    for dim in ['distance_band','field_band','maiden_group','explicit_race_class',
                'top1_career_band','top1_distance_band','field_experience_band','score_band']:
        for (split,cat),g in r.groupby(['split',dim],dropna=False):
            n=len(g);w=int(g.win.sum());exp=float(g.random_p.sum())
            tables.append({'dimension':dim,'category':str(cat),'split':split,'races':n,
                           'days':g.race_date.nunique(),'rank1_wins':w,'rank1_win_pct':100*w/n,
                           'expected_random_wins':exp,'excess_vs_random_pp':100*(w-exp)/n,
                           'top3_hit_pct':100*g.top3.mean(),
                           'staying_races':int(g.staying.sum()),'maiden_races':int(g.explicit_maiden.sum()),
                           'incomplete_experience_races':int((~g.experience_complete).sum()),
                           'under_30':n<30,'descriptive_only':True})
    strata=pd.DataFrame(tables)
    cross=[]
    for dim in ['field_band','maiden_group','score_band','top1_career_band','top1_distance_band','field_experience_band']:
        for (split,stay,category),g in r.groupby(['split','staying',dim]):
            cross.append({'dimension':dim,'category':str(category),'split':split,'staying':bool(stay),
                          'races':len(g),'days':g.race_date.nunique(),
                          'winners':int(g.win.sum()),'win_pct':100*g.win.mean(),
                          'excess_vs_random_pp':100*(g.win-g.random_p).mean(),
                          'under_30':len(g)<30})
    ci_l,ci_h=_day_interval(hold)
    aggregate=[]
    for split,part in [('DEVELOPMENT',train),('CHRONOLOGICAL_HOLDOUT',hold)]:
        for stay,g in part.groupby('staying'):
            aggregate.append({'split':split,'staying':bool(stay),'races':len(g),
                              'days':g.race_date.nunique(),'win_pct':100*g.win.mean(),
                              'excess_vs_random_pp':100*(g.win-g.random_p).mean(),
                              'mean_field_size':g.actual_starters_num.mean(),
                              'any_unscored_pct':100*g.any_unscored.mean(),
                              'explicit_maiden_pct':100*g.explicit_maiden.mean(),
                              'mean_top1_career_starts':g.top1_career_starts_num.mean(),
                              'mean_top1_distance_starts':g.top1_distance_starts_num.mean(),
                              'experience_missing_pct':100*(~g.experience_complete).mean(),
                              'holdout_day_ci_staying_minus_other_low_pp':ci_l if split=='CHRONOLOGICAL_HOLDOUT' else np.nan,
                              'holdout_day_ci_staying_minus_other_high_pp':ci_h if split=='CHRONOLOGICAL_HOLDOUT' else np.nan})
    # Exploratory adjustment: trained on earlier races only, evaluated once on later races.
    # No refit, feature selection or threshold search on holdout.
    fits=[]
    model_specs=[('FIELD_ONLY',()),('FIELD_EXPERIENCE_MAIDEN',('any_unscored','explicit_maiden','top1_career_log','top1_distance_log','field_career_log')),
                 ('FIELD_EXPERIENCE_MAIDEN_STAYING',('any_unscored','explicit_maiden','top1_career_log','top1_distance_log','field_career_log','staying'))]
    # Complete experience is required to avoid treating unknown values as known zero.
    complete_train=train[train.experience_complete].copy()
    complete_hold=hold[hold.experience_complete].copy()
    for name,variables in model_specs:
        if len(complete_train)<100 or len(complete_hold)<50:
            fits.append({'model':name,'status':'INSUFFICIENT_COMPLETE_EXPERIENCE','train_races':len(complete_train),'holdout_races':len(complete_hold)})
            continue
        X=_design(complete_train,variables);Y=complete_train.win.to_numpy(float)
        beta=_irls(X,Y)
        Xt=_design(complete_hold,variables);prob=np.clip(1/(1+np.exp(-np.clip(Xt@beta,-30,30))),1e-7,1-1e-7)
        y=complete_hold.win.to_numpy(float)
        fits.append({'model':name,'status':'EXPLORATORY_COMPLETE_CASE','train_races':len(complete_train),
                     'holdout_races':len(complete_hold),'holdout_staying_races':int(complete_hold.staying.sum()),
                     'holdout_brier':float(np.mean((y-prob)**2)),
                     'holdout_logloss':float(np.mean(-y*np.log(prob)-(1-y)*np.log(1-prob))),
                     'holdout_staying_brier':float(np.mean((y[complete_hold.staying]-prob[complete_hold.staying])**2)) if complete_hold.staying.sum() else np.nan,
                     'coeff_staying_logodds':float(beta[-1]) if 'staying' in variables else np.nan,
                     'note':'Exploratory probability association only; no ranking or wagering simulation'})
    integrity=r[['batch_id','race_date','track','race_number','split','distance_m','staying','maiden_group','explicit_race_class',
                 'race_class_source','actual_starters_num','scored_count','unscored_count',
                 'top1_career_starts_num','top1_distance_starts_num','top1_track_starts_num',
                 'field_career_mean','field_distance_mean','field_career_observed','field_distance_observed',
                 'runner_count','experience_complete','reconstructed_score_coverage_ok']].copy()
    summary=pd.DataFrame([{'version':VERSION,'historical_races':len(races),'eligible_races':len(r),
       'development_races':len(train),'holdout_races':len(hold),'holdout_staying_races':int(hold.staying.sum()),
       'explicit_class_races':int((~r.explicit_race_class.isin(['CLASS_UNDETERMINED','CLASS_CONFLICT'])).sum()),
       'explicit_maiden_races':int(r.explicit_maiden.sum()),
       'complete_experience_races':int(r.experience_complete.sum()),
       'holdout_complete_experience_races':len(complete_hold),
       'holdout_staying_complete_experience_races':int(complete_hold.staying.sum()),
       'pre_race_form_validation':'USER_PREVIOUSLY_VALIDATED_FORMFAV_INPUT_AVAILABILITY',
       'freeze':'v0.2 unchanged','model_promotion':'NOT_APPROVED', 'new_rankings':False,
       'statement':'Post-hoc exploratory subgroup research; no independent validation of a new strategy.'}])
    return {'summary':summary,'experience_integrity':integrity,'staying_vs_other':pd.DataFrame(aggregate),
            'race_class_and_experience':strata,'staying_x_factors':pd.DataFrame(cross),
            'adjusted_holdout':pd.DataFrame(fits)}


def render(secret):
    import streamlit as st
    import v061b_historical as hist
    st.subheader('🧪 v0.6.1q — Staying-race and class validation')
    st.caption('Read-only; fixed categories. Compares staying vs other races with field size, experience, maiden designation and missingness audits.')
    if st.button('Run v0.6.1q staying-race validation',key='v061q_run'):
        try:
            client=hist.server_db(secret)
            batches=hist.read_all(client,hist.RACES,'*','race_date')
            features=hist.read_all(client,hist.FEATURES,'*')
            results=hist.read_all(client,hist.RESULTS,'batch_id,result_payload,result_sha256,actual_starters')
            original=hist.build_reports(batches,features,results)
            reports=make_reports(original['races'],pd.DataFrame(features),batches)
            st.session_state['v061q_reports']=reports
        except Exception as exc:
            st.error('v0.6.1q research could not run; no database writes: '+str(exc));return
    reports=st.session_state.get('v061q_reports')
    if reports is None:return
    for name,frame in reports.items():
        with st.expander(name.replace('_',' ').title(),expanded=name in ('summary','staying_vs_other','adjusted_holdout')):
            st.dataframe(frame,hide_index=True,use_container_width=True)
            st.download_button('Download '+name,frame.to_csv(index=False).encode(),file_name='v061q_'+name+'.csv',mime='text/csv',key='v061q_dl_'+name)
    st.warning('Exploratory historical validation only. Do not change production ranks, confidence, bets or staking based on these results.')
