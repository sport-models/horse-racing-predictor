"""Stage 2 initial field-size/score-gap research, frozen v0.2 comparison only.

All historical form fetched post-race, therefore exploratory retrospective
associations only. No model recomputation using outcomes and no weight changes.
"""
from __future__ import annotations
import math
import pandas as pd


def field_band(value):
    try:
        n=int(value)
    except (ValueError,TypeError,OverflowError):return 'UNKNOWN'
    if n<=0:return 'UNKNOWN'
    if n<=7:return 'SMALL_1_7'
    if n<=10:return 'MEDIUM_8_10'
    if n<=13:return 'LARGE_11_13'
    return 'VERY_LARGE_14_PLUS'


def score_gap(first,second):
    try:
        a,b=float(first),float(second)
        if not(math.isfinite(a) and math.isfinite(b)):return None
        return a-b
    except (TypeError,ValueError,OverflowError):return None


def reports_from_frames(races,runner_features):
    if races.empty:return {'stage2_summary':pd.DataFrame(),'stage2_races':pd.DataFrame(),'stage2_field_size':pd.DataFrame(),'stage2_score_gap':pd.DataFrame(),'stage2_interaction':pd.DataFrame()}
    frame=races.copy()
    features=runner_features.copy()
    # Do not use outcomes to choose predictor values. Read immutable archived rank+score.
    features['model_rank_numeric']=pd.to_numeric(features['model_rank'],errors='coerce')
    features['model_score_numeric']=pd.to_numeric(features['model_score'],errors='coerce')
    rank1=features[features['model_rank_numeric'].eq(1)][['batch_id','model_score_numeric']].rename(columns={'model_score_numeric':'rank1_score'})
    rank2=features[features['model_rank_numeric'].eq(2)][['batch_id','model_score_numeric']].rename(columns={'model_score_numeric':'rank2_score'})
    # Multiple rank-1/2 values must not multiply observations or be silently selected.
    ambiguous=set(rank1.loc[rank1.duplicated('batch_id',keep=False),'batch_id']) | set(rank2.loc[rank2.duplicated('batch_id',keep=False),'batch_id'])
    rank1=rank1.drop_duplicates('batch_id',keep=False)
    rank2=rank2.drop_duplicates('batch_id',keep=False)
    frame=frame.merge(rank1,on='batch_id',how='left',validate='one_to_one')
    frame=frame.merge(rank2,on='batch_id',how='left',validate='one_to_one')
    frame['duplicate_top_rank']=frame['batch_id'].isin(ambiguous)
    frame['gap']=frame.apply(lambda r:score_gap(r['rank1_score'],r['rank2_score']) if not r['duplicate_top_rank'] else None,axis=1)
    frame['evaluable']=frame['status'].eq('EVALUABLE_RETROSPECTIVE_RESEARCH')
    frame['field_band']=frame['actual_starters'].map(field_band)
    elig=frame[frame['evaluable']].copy()
    gap_known=elig[elig['gap'].notna()].copy()
    # Predeclared absolute bands, not tuned on win outcomes. Uncalibrated score units.
    def band(x):
        if pd.isna(x):return 'UNKNOWN'
        if x<0:return 'NEGATIVE_GAP_CHECK_RANKING'
        if x<2:return 'GAP_LT_2'
        if x<5:return 'GAP_2_TO_LT5'
        if x<10:return 'GAP_5_TO_LT10'
        return 'GAP_10_PLUS'
    frame['gap_band']=frame['gap'].map(band)
    elig['gap_band']=elig['gap'].map(band)
    def summarize(group_by):
        rows=[]
        for label,part in elig.groupby(group_by,dropna=False):
            n=len(part); top1=int(part['top1_won'].eq(True).sum());top3=int(part['winner_in_top3'].eq(True).sum())
            random1=float(pd.to_numeric(part['random_top1_expectation'],errors='coerce').sum())
            random3=float(pd.to_numeric(part['random_top3_expectation'],errors='coerce').sum())
            sp=pd.to_numeric(part['model_win_sp_profit_10'],errors='coerce').dropna()
            stake=10*len(sp); net=float(sp.sum()) if stake else None
            rows.append({group_by:label,'races':n,'top1_wins':top1,'top1_win_pct':100*top1/n,
                         'top3_hit_races':top3,'top3_hit_pct':100*top3/n,
                         'random_top1_expected':random1,'rank1_excess_vs_random_pp':100*(top1-random1)/n,
                         'random_top3_expected':random3,'top3_excess_vs_random_pp':100*(top3-random3)/n,
                         'retrospective_sp_priced_races':len(sp),'retrospective_sp_stake_$':stake,
                         'retrospective_sp_profit_$':net,
                         'retrospective_sp_roi_pct':100*net/stake if stake else None,
                         'research_only':True})
        return pd.DataFrame(rows)
    inter=elig.groupby(['field_band','gap_band'],dropna=False).agg(
        races=('batch_id','size'),top1_wins=('top1_won',lambda x:int(x.eq(True).sum())),
        top3_hits=('winner_in_top3',lambda x:int(x.eq(True).sum()))).reset_index()
    if not inter.empty:
        inter['top1_pct']=100*inter['top1_wins']/inter['races']
        inter['top3_pct']=100*inter['top3_hits']/inter['races']
        inter['small_sample_under_30']=inter['races']<30
    summary=pd.DataFrame([{'version':'v0.6.1k-stage2','archived_races':len(frame),
      'evaluable_retrospective_races':len(elig),'rank1_rank2_gap_available':len(gap_known),
      'missing_rank1_rank2_gap':len(elig)-len(gap_known),
      'duplicate_rank_identifiers':int(frame['duplicate_top_rank'].sum()),
      'scorer_modified':False,'research_only':True,
      'warning':'Post-race retrieved features; uncalibrated gap bins; no independence or causal effect claims; no promotion gate open.'}])
    return {'stage2_summary':summary,'stage2_races':frame,'stage2_field_size':summarize('field_band'),
            'stage2_score_gap':summarize('gap_band'),'stage2_interaction':inter}


def render(secret):
    import streamlit as st
    import v061b_historical as h
    st.subheader('🧪 Stage 2 — Field Size × Score Separation (Exploratory)')
    st.warning('Historical form was retrieved AFTER racing: results are hypothesis-generating ONLY. No live odds, no prospective claims, no scorer modifications.')
    if st.button('Run Stage 2 baseline stratification',key='v061k_stage2_run'):
        try:
            client=h.server_db(secret)
            races=h.read_all(client,h.RACES,'id,race_date,track,track_key,track_slug,race_number,runner_count,predicted_count,source_form_sha256,scorer_sha256,cohort','race_date')
            features=h.read_all(client,h.FEATURES,'batch_id,horse,horse_key,model_rank,model_score')
            results=h.read_all(client,h.RESULTS,'batch_id,result_payload,result_sha256,actual_starters')
            history=h.build_reports(races,features,results)
            st.session_state['v061k_stage2_reports']=reports_from_frames(history['races'],pd.DataFrame(features))
        except Exception as exc:
            st.error('Stage 2 safely stopped; nothing modified: '+str(exc));return
    reports=st.session_state.get('v061k_stage2_reports')
    if not reports:return
    for name,df in reports.items():
        with st.expander(name.replace('_',' ').title(),expanded=name in ('stage2_summary','stage2_field_size','stage2_score_gap')):
            if df.empty:st.info('No observations')
            else:st.dataframe(df,hide_index=True,use_container_width=True)
            st.download_button('Download '+name+' CSV',df.to_csv(index=False).encode(),
                file_name='v061k_'+name+'.csv',mime='text/csv',key='v061k_stage2_csv_'+name)
