"""Standalone tests — no network, Supabase, or app mutation."""
import datetime as dt, hashlib, json, sys, types, unittest
import pandas as pd
p=types.ModuleType('v061_prospective')
p.digest=lambda v:hashlib.sha256(json.dumps(v,ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False).encode()).hexdigest()
p.race_key=lambda x:(str(x.get('race_date'))[:10],str(x.get('track')).casefold(),int(x.get('race_number')))
sys.modules['v061_prospective']=p
f=types.ModuleType('v042_results'); f.canonical=lambda x:''.join(c for c in str(x or '').casefold() if c.isalnum());sys.modules['v042_results']=f
od=types.ModuleType('v057f_odds_integrity');od.iso_timestamp=lambda s:dt.datetime.fromisoformat(str(s).replace('Z','+00:00')) if s else None
sys.modules['v057f_odds_integrity']=od
import v061j_integrity as prior
import v061k_integrity as k
import v061k_stage2 as stage

def archive():
 payload={'model_version':'v0.2','rankings':[{'rank':i,'horse':name,'score':3-i} for i,name in enumerate(('Alpha','Bravo','Charlie'),1)],
 'source_times':{'form_response_received_utc':'2026-11-12T00:00:00+00:00','ranking_generated_utc':'2026-11-12T00:01:00+00:00'}}
 return {'batch_id':'one','race_date':'2026-11-12','track':'Seymour','race_number':1,
  'created_at':'2026-11-12T00:02:00+00:00','scheduled_start_utc':'2026-11-12T01:00:00+00:00',
  'model_version':'v0.2','payload':payload,'payload_sha256':p.digest(payload)}

def result(horse,pos=None,scratched=False):
 return {'race_date':'2026-11-12','track':'Seymour','race_number':1,'horse':horse,
         'finishing_position':pos,'scratched':scratched,'result_status':'final'}

class ScratchTests(unittest.TestCase):
 def test_three_verified_extra_scratches_r1(self):
  arr=[result('Alpha',2),result('Bravo',1),result('Charlie',3),
       result('Fiorente Charm',scratched=True),result('A Rus In Grinzing',scratched=True),result('Lady of Alsace',scratched=True)]
  r,i,m=k.reconcile([archive()],arr)
  self.assertTrue(bool(r.iloc[0]['strict_ok']))
  self.assertEqual(int(r.iloc[0]['verified_prelock_excluded_scratches']),3)
  self.assertTrue(bool(r.iloc[0]['top2_hit']))
  self.assertEqual(len(m),6)
  self.assertEqual(len(i[i['category']=='INTEGRITY']),0)
  r_old,_,_=prior.reconcile([archive()],arr)
  self.assertFalse(bool(r_old.iloc[0]['strict_ok']))
 def test_extra_starter_still_blocks(self):
  arr=[result('Alpha',2),result('Bravo',1),result('Charlie',3),result('Delta',4)]
  r,i,_=k.reconcile([archive()],arr)
  self.assertFalse(bool(r.iloc[0]['strict_ok']))
  self.assertEqual(int(r.iloc[0]['unmatched_official_starters']),1)
 def test_extra_scratch_with_position_still_blocks(self):
  arr=[result('Alpha',2),result('Bravo',1),result('Charlie',3),result('Delta',4,scratched=True)]
  r,i,_=k.reconcile([archive()],arr)
  self.assertFalse(bool(r.iloc[0]['strict_ok']))
  self.assertIn('SCRATCHED_WITH_FINISH_POSITION',set(i['issue']))
 def test_missing_locked_remains_blocked(self):
  r,i,_=k.reconcile([archive()],[result('Alpha',2),result('Bravo',1)])
  self.assertFalse(bool(r.iloc[0]['strict_ok']))
 def test_late_top1_scratch_no_replacement(self):
  r,i,_=k.reconcile([archive()],[result('Alpha',scratched=True),result('Bravo',1),result('Charlie',2)])
  self.assertEqual(r.iloc[0]['strict_status'],'TOP1_SCRATCHED_NO_REPLACEMENT')
 def test_late_rank3_scratch_accepted(self):
  r,i,_=k.reconcile([archive()],[result('Alpha',2),result('Bravo',1),result('Charlie',scratched=True)])
  self.assertTrue(bool(r.iloc[0]['strict_ok']))
  self.assertEqual(r.iloc[0]['late_locked_scratches'],1)
 def test_tampered_snapshot_blocked(self):
  a=archive();a['payload']['rankings'][0]['horse']='Other'
  r,i,_=k.reconcile([a],[result('Alpha',2),result('Bravo',1),result('Charlie',3)])
  self.assertFalse(bool(r.iloc[0]['strict_ok']))
 def test_betting_gate_untouched(self):
  a=archive();r,_,_=k.reconcile([a],[result('Alpha',2),result('Bravo',1)])
  raw=pd.DataFrame([{'batch_id':'one','settlement_status':'GRADED_RESEARCH_ONLY','profit':10.,'total_return':20.,'win_hit':True,'place_hit':False}])
  gated=k.gate_tickets(raw,r)
  self.assertEqual(gated.iloc[0]['settlement_status'],'BLOCKED_BY_V061J_STRICT_INTEGRITY')
  self.assertEqual(raw.iloc[0]['profit'],10.)

class StageTests(unittest.TestCase):
 def test_score_band(self):
  self.assertEqual(stage.field_band(7),'SMALL_1_7')
  self.assertEqual(stage.field_band(14),'VERY_LARGE_14_PLUS')
  self.assertIsNone(stage.score_gap(None,4))
 def test_descriptive_summary_not_model_change(self):
  races=pd.DataFrame([{'batch_id':'a','status':'EVALUABLE_RETROSPECTIVE_RESEARCH',
    'top1_won':True,'winner_in_top3':True,'actual_starters':9,
    'random_top1_expectation':1/9,'random_top3_expectation':1/3,'model_win_sp_profit_10':20},
    {'batch_id':'b','status':'NO_IMPORTED_RESULTS','top1_won':None,'winner_in_top3':None,
    'actual_starters':None,'random_top1_expectation':None,'random_top3_expectation':None,'model_win_sp_profit_10':None}])
  features=pd.DataFrame([{'batch_id':'a','model_rank':1,'model_score':12},
                          {'batch_id':'a','model_rank':2,'model_score':10}])
  reports=stage.reports_from_frames(races,features)
  self.assertEqual(reports['stage2_summary'].iloc[0]['evaluable_retrospective_races'],1)
  self.assertEqual(reports['stage2_summary'].iloc[0]['rank1_rank2_gap_available'],1)
  self.assertEqual(reports['stage2_field_size'].iloc[0]['top1_win_pct'],100)
 def test_missing_gap_kept_unknown(self):
  races=pd.DataFrame([{'batch_id':'a','status':'EVALUABLE_RETROSPECTIVE_RESEARCH',
   'top1_won':False,'winner_in_top3':False,'actual_starters':6,
   'random_top1_expectation':1/6,'random_top3_expectation':.5,'model_win_sp_profit_10':-10}])
  feats=pd.DataFrame([{'batch_id':'a','model_rank':1,'model_score':None},
                      {'batch_id':'a','model_rank':2,'model_score':15}])
  out=stage.reports_from_frames(races,feats)
  self.assertEqual(out['stage2_races'].iloc[0]['gap_band'],'UNKNOWN')

if __name__=='__main__':unittest.main(verbosity=2)
