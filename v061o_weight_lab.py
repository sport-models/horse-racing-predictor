"""v0.6.1o: read-only, fixed-candidate distance/track weight experiments.
Uses archived runner-level inputs, preserves production v0.2, never writes data.
A holdout is a chronological research split, not an untouched prospective sample.
"""
from __future__ import annotations
import re
import numpy as np
import pandas as pd

VERSION='v0.6.1o'
FEATURES=('recent','career','track','distance','track_distance','context')
WEIGHTS={
 'V02_FROZEN':(.35,.15,.15,.15,.10,.10),
 'TRACK_PLUS_05':(.35,.15,.20,.10,.10,.10),
 'DISTANCE_PLUS_05':(.35,.15,.10,.20,.10,.10),
 'TRACK_DISTANCE_PLUS_05':(.35,.15,.125,.125,.15,.10),
 'RECENT_PLUS_05':(.40,.15,.125,.125,.10,.10),
 'CONTEXT_MINUS_05':(.35,.15,.175,.175,.10,.05),
}
REQUIRED={'batch_id','horse','model_rank','model_score','recent_form','career_starts','career_wins',
          'track_starts','track_wins','distance_starts','distance_wins',
          'track_distance_starts','track_distance_wins','barrier','weight_kg'}
RACE_REQ={'batch_id','race_date','status','original_top1_scratched','winning_horses','winner_count',
          'actual_starters'}

def namekey(s):return re.sub(r'[^a-z0-9]','',str(s).casefold())
def num(s):
 try:
  v=float(s)
  return v if np.isfinite(v) else None
 except (ValueError,TypeError,OverflowError):return None

def rate(row,prefix):
 starts=num(row.get(prefix+'_starts'));wins=num(row.get(prefix+'_wins'))
 if starts is None or starts<=0:return 0.,0
 return min(max(wins / starts,0.),1.) if wins is not None else 0.,int(starts)

def components(row):
 """Recreate frozen v0.2 score_runner components, not an alternative scorer."""
 career,n=rate(row,'career')
 if n==0:return None,'NO_CAREER_STARTS'
 digits=re.findall(r'[0-9]',str(row.get('recent_form') or ''))[-5:]
 if not digits:return None,'NO_RECENT_FORM'
 lookup={'1':1.,'2':.7,'3':.5,'4':.3,'5':.2}
 recent=sum((i+1)*lookup.get(v,0.) for i,v in enumerate(digits))/sum(range(1,len(digits)+1))
 values=[]
 for prefix in ['track','distance','track_distance']:
  v,starts=rate(row,prefix)
  values.append((starts*v+5*career)/(starts+5))
 barrier=num(row.get('barrier'));weight=num(row.get('weight_kg'))
 barrier=8. if barrier is None else barrier
 weight=56. if weight is None else weight
 b=max(0.,min(1.,1-(barrier-1)/16))
 w=max(0.,min(1.,1-(weight-48)/16))
 return (recent,career,*values,(b+w)/2),None

def score(parts,w):return round(sum(x*y for x,y in zip(parts,w))*100,2)

def prepare(races,features):
 if RACE_REQ-set(races.columns):raise ValueError('Missing race columns: '+str(sorted(RACE_REQ-set(races.columns))))
 if REQUIRED-set(features.columns):raise ValueError('Missing runner columns: '+str(sorted(REQUIRED-set(features.columns))))
 if races.batch_id.astype(str).duplicated().any():raise ValueError('Duplicate race batch IDs')
 f=features.copy();f['batch_id']=f.batch_id.astype(str)
 r=races.copy();r['batch_id']=r.batch_id.astype(str)
 if f.duplicated(['batch_id','horse']).any():raise ValueError('Duplicate runner identities')
 rows=[]
 for item in f.to_dict('records'):
  scratched=str(item.get('scratched_in_historical_response')).lower()=='true'
  comp,reason=(None,'SCRATCHED') if scratched else components(item)
  orig=num(item['model_score']);rank=num(item['model_rank'])
  rebuilt=score(comp,WEIGHTS['V02_FROZEN']) if comp is not None else None
  parity=((orig is None and rebuilt is None) or
          (orig is not None and rebuilt is not None and abs(orig-rebuilt)<.011))
  o={'batch_id':item['batch_id'],'horse':item['horse'],'horse_key':namekey(item['horse']),
     'original_rank':int(rank) if rank is not None else None,'original_score':orig,
     'rebuilt_v02_score':rebuilt,'score_parity':parity,'missing_reason':reason,
     'scratched_at_capture':scratched}
  for k,v in zip(FEATURES,comp or [None]*6):o[k]=v
  if comp is not None:
   for candidate,ws in WEIGHTS.items():o[candidate]=score(comp,ws)
  rows.append(o)
 runner=pd.DataFrame(rows)
 count=runner.groupby('batch_id').agg(saved_runners=('horse','size'),scored_runners=('original_score','count'),
              parity_failures=('score_parity',lambda s:int((~s).sum())),duplicate_keys=('horse_key',lambda s:s.duplicated().sum()))
 # No data from results are used in runner components. Result names join only after scores have been rebuilt.
 return r,runner,count

def run(races,features):
 r,runner,count=prepare(races,features)
 audit=[];pred=[]
 by_batch={bid:g for bid,g in runner.groupby('batch_id',sort=False)}
 for race in r.to_dict('records'):
  bid=race['batch_id'];g=by_batch.get(bid)
  problems=[]
  if g is None or g.empty:problems.append('NO_FEATURE_ROWS')
  else:
   if int(count.loc[bid,'parity_failures']):problems.append('V02_PARITY_FAIL')
   if int(count.loc[bid,'duplicate_keys']):problems.append('DUPLICATE_HORSE_KEYS')
   ranks=g.original_rank.dropna().tolist()
   if sorted(ranks)!=list(range(1,len(ranks)+1)):problems.append('RANK_SEQUENCE')
   expected=int(num(race.get('runner_count')) or len(g))
   if len(g)!=expected:problems.append('RUNNER_COUNT_MISMATCH')
  if race['status']!='EVALUABLE_RETROSPECTIVE_RESEARCH':problems.append('RACE_NOT_EVALUABLE')
  if str(race['original_top1_scratched']).lower()=='true':problems.append('ORIGINAL_TOP1_SCRATCHED')
  names=[namekey(s.strip()) for s in str(race.get('winning_horses') or '').split('|') if s.strip()]
  if not names or len(names)!=int(num(race['winner_count']) or 0):problems.append('WINNER_IDENTITY_MISSING')
  if g is not None and names and (len(set(names))!=len(names) or not set(names).issubset(set(g.horse_key))):problems.append('WINNER_NOT_MATCHED')
  if not problems:
   # Only originally ranked active participants are candidates; missing v0.2 scores remain unscored.
   active=g[g.original_rank.notna()].copy()
   if len(active)<2:problems.append('TOO_FEW_RANKED')
   elif active.original_score.notna().sum()<2:problems.append('TOO_FEW_SCORED')
   elif active.loc[active.original_rank.eq(1),'original_score'].isna().any():problems.append('TOP1_UNSCORED')
  record={'batch_id':bid,'race_date':race['race_date'],'track':race.get('track'),'race_number':race.get('race_number'),
          'saved_runners':0 if g is None else len(g),'unscored_runners':0 if g is None else int(g.loc[g.original_rank.notna(),'original_score'].isna().sum()),
          'parity_failures':0 if g is None else int((~g.score_parity).sum()),'eligible':not problems,
          'reasons':'|'.join(problems)}
  audit.append(record)
  if problems:continue
  # Sorting is stable for explicit ties. Exclude races with a tied top score for any candidate,
  # rather than arbitrarily selecting based on array order or horse name.
  for candidate in WEIGHTS:
   scored=active.dropna(subset=[candidate]).sort_values([candidate],ascending=False,kind='mergesort')
   maxval=scored[candidate].iloc[0]
   ties=int(np.isclose(scored[candidate].to_numpy(float),maxval,rtol=0,atol=1e-10).sum())
   top=scored.iloc[0]
   pred.append({'batch_id':bid,'race_date':race['race_date'],'candidate':candidate,
      'horse':top['horse'],'selected_key':top['horse_key'],'rank1_won':int(top['horse_key'] in names),
      'top3_hit':int(bool(set(scored.head(3).horse_key)&set(names))) if len(scored)>=3 else np.nan,
      'tie_for_first':ties>1,'scored_horses':len(scored),'unscored_horses':int(active.original_score.isna().sum()),
      'actual_starters':race.get('actual_starters')})
 audit=pd.DataFrame(audit);pred=pd.DataFrame(pred)
 if pred.empty:return {'integrity':audit,'race_predictions':pred,'summary':pd.DataFrame(),'comparisons':pd.DataFrame(),'selection_changes':pd.DataFrame(),'paired_holdout':pd.DataFrame()}
 tie_ids=set(pred.loc[pred.tie_for_first,'batch_id'])
 pred['comparison_eligible']=~pred.batch_id.isin(tie_ids)
 valid=pred[pred.comparison_eligible].copy()
 dates=sorted(valid.race_date.astype(str).unique())
 boundary=max(1,min(len(dates)-1,int(np.floor(.7*len(dates))))) if len(dates)>1 else 1
 train=set(dates[:boundary]);holdout=set(dates[boundary:])
 valid['split']=np.where(valid.race_date.isin(train),'DEVELOPMENT','CHRONOLOGICAL_HOLDOUT')
 # Design fixed before outcomes; no search, selection, or tuning using holdout labels.
 metrics=[]
 for (split,candidate),g in valid.groupby(['split','candidate']):
  metrics.append({'split':split,'candidate':candidate,'races':len(g),'rank1_wins':int(g.rank1_won.sum()),
                  'rank1_win_rate_pct':float(g.rank1_won.mean()*100),
                  'top3_hit_pct':float(g.top3_hit.mean()*100) if g.top3_hit.notna().any() else None,
                  'top3_evaluable_races':int(g.top3_hit.notna().sum())})
 metrics=pd.DataFrame(metrics)
 base=valid[valid.candidate=='V02_FROZEN'][['batch_id','split','rank1_won','horse','selected_key']].rename(columns={'rank1_won':'base_won','horse':'base_horse','selected_key':'base_key'})
 comparison=valid.merge(base,on=['batch_id','split'],validate='many_to_one')
 comparison['changed_selection']=comparison.selected_key!=comparison.base_key
 changes=comparison[comparison.candidate!='V02_FROZEN'][['batch_id','race_date','candidate','split','base_horse','horse','base_won','rank1_won','changed_selection']].copy()
 # Paired winner-count improvements, preserving race-date clustering. Exploratory CIs only.
 paired_rows=[]
 for candidate,g in comparison[comparison.candidate!='V02_FROZEN'].groupby('candidate'):
  for split,part in g.groupby('split'):
   delta=(part.rank1_won-part.base_won).to_numpy(float)
   dates=part.race_date.astype(str).to_numpy();unique=np.unique(dates)
   lower=upper=None
   if len(unique)>=5:
    rng=np.random.default_rng(610)
    lookup={d:np.flatnonzero(dates==d) for d in unique};bs=[]
    for _ in range(1000):
     ix=np.concatenate([lookup[d] for d in rng.choice(unique,size=len(unique),replace=True)])
     bs.append(float(np.mean(delta[ix])*100))
    lower,upper=map(float,np.quantile(bs,[.025,.975]))
   paired_rows.append({'candidate':candidate,'split':split,'races':len(part),
    'baseline_wins':int(part.base_won.sum()),'challenger_wins':int(part.rank1_won.sum()),
    'changed_selections':int(part.changed_selection.sum()),
    'baseline_only_wins':int(((part.base_won==1)&(part.rank1_won==0)).sum()),
    'challenger_only_wins':int(((part.base_won==0)&(part.rank1_won==1)).sum()),
    'win_rate_delta_pp':float(delta.mean()*100),'day_bootstrap_95pct_ci_low_pp':lower,
    'day_bootstrap_95pct_ci_high_pp':upper,'number_of_dates':len(unique),
    'promotion':'BLOCKED_PROSPECTIVE_NOT_VALIDATED'})
 paired=pd.DataFrame(paired_rows)
 summary=pd.DataFrame([{'version':VERSION,'historical_races':len(r),'runner_feature_rows':len(runner),
   'races_parity_clean':int((audit.parity_failures==0).sum()),'races_eligible_before_ties':int(audit.eligible.sum()),
   'races_all_models_comparable':int(valid.batch_id.nunique()),
   'races_with_tied_top_candidate':len(tie_ids),
   'development_last_date':max(train) if train else None,'holdout_first_date':min(holdout) if holdout else None,
   'research_only':True,'production_v02_changed':False,'candidates_fixed_in_code':True,
   'note':'Reported features pre-race available per user validation. Historical archive retrieval times and independent source-as-of certification remain separate evidence.'}])
 return {'integrity':audit,'race_predictions':pred,'summary':summary,'comparisons':metrics,'selection_changes':changes,'paired_holdout':paired}

def render(secret):
 import streamlit as st
 import v061b_historical as history
 st.subheader('🧪 v0.6.1o — Track & distance weight research (v0.2 frozen)')
 st.caption('Rebuilds scores from archived feature components; stops candidate evaluation on any v0.2 parity failure. Research-only: no prediction/bet writes, no weight promotion.')
 if st.button('Run v0.6.1o fixed-weight challengers',key='v061o_run'):
  try:
   client=history.server_db(secret)
   races=history.read_all(client,history.RACES,'id,race_date,track,track_key,track_slug,race_number,runner_count,predicted_count,source_form_sha256,scorer_sha256,cohort','race_date')
   feats=history.read_all(client,history.FEATURES,'*')
   results=history.read_all(client,history.RESULTS,'batch_id,result_payload,result_sha256,actual_starters')
   hist=history.build_reports(races,feats,results)
   reports=run(hist['races'],pd.DataFrame(feats))
   st.session_state['v061o_reports']=reports
  except Exception as exc:
   st.error('v0.6.1o failed without database writes: '+str(exc));return
 report=st.session_state.get('v061o_reports')
 if not report:return
 for key,frame in report.items():
  with st.expander(key.replace('_',' ').title(),expanded=key in ('summary','comparisons')):
   st.dataframe(frame,use_container_width=True,hide_index=True)
   st.download_button('Download '+key,frame.to_csv(index=False).encode(),file_name='v061o_'+key+'.csv',mime='text/csv',key='v061o_export_'+key)
 st.warning('Exploratory comparisons, not an independent prospective test. No changes to v0.2 rankings or betting decisions.')
