"""Run offline: python test_v061j.py (no Supabase/Streamlit required)."""
import datetime as dt
import hashlib
import json
import sys
import types
import unittest

# External app modules replaced only inside this standalone test process.
p=types.ModuleType('v061_prospective')
p.digest=lambda x: hashlib.sha256(json.dumps(x,ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False).encode()).hexdigest()
p.race_key=lambda x:(str(x.get('race_date'))[:10],str(x.get('track')).casefold(),int(x.get('race_number')))
sys.modules['v061_prospective']=p
f=types.ModuleType('v042_results')
f.canonical=lambda x:''.join(c for c in str(x or '').casefold() if c.isalnum())
f.normalize_track=lambda x:str(x or '').casefold()
sys.modules['v042_results']=f
od=types.ModuleType('v057f_odds_integrity')
od.iso_timestamp=lambda s:dt.datetime.fromisoformat(str(s).replace('Z','+00:00')) if s else None
od.bookmaker_id=lambda q:(q.get('bookmaker'),None)
od.valid_price=lambda v:float(v) if v and float(v)>1 else None
od.valid_places=lambda v,n=None:int(v) if v else None
od.preferred_key=lambda b:(0,b)
sys.modules['v057f_odds_integrity']=od

import v061j_integrity as j

def archive():
 t={'form_response_received_utc':'2026-11-12T00:00:00+00:00','ranking_generated_utc':'2026-11-12T00:01:00+00:00'}
 ranks=[{'rank':i,'horse':name,'score':i} for i,name in enumerate(['Alpha','Bravo','Charlie'],1)]
 payload={'model_version':'v0.2','rankings':ranks,'source_times':t}
 return {'batch_id':'one','race_date':'2026-11-12','track':'Seymour','race_number':1,
   'created_at':'2026-11-12T00:02:00+00:00','scheduled_start_utc':'2026-11-12T01:00:00+00:00',
   'model_version':'v0.2','payload':payload,'payload_sha256':p.digest(payload)}

def result(horse,pos=None,scratched=False):
 return {'race_date':'2026-11-12','track':'Seymour','race_number':1,'horse':horse,
         'finishing_position':pos,'scratched':scratched,'result_status':'final'}

class TestIntegrity(unittest.TestCase):
 def test_all_identical(self):
  a=archive(); races,issues,members=j.reconcile([a],[result('Alpha',2),result('Bravo',1),result('Charlie',3)])
  self.assertEqual(races.iloc[0]['strict_status'],j.PASS)
  self.assertTrue(bool(races.iloc[0]['strict_ok']))
  self.assertTrue(bool(races.iloc[0]['top2_hit']))
  self.assertEqual(len(issues),0)
  self.assertEqual(len(members),3)
 def test_missing_locked_runner_blocks(self):
  a=archive();r,i,m=j.reconcile([a],[result('Alpha',2),result('Bravo',1)])
  self.assertFalse(bool(r.iloc[0]['strict_ok']))
  self.assertIn('LOCKED_RUNNER_NOT_IN_OFFICIAL_RESULT',set(i['issue']))
  self.assertEqual(r.iloc[0]['missing_from_official'],1)
  self.assertIsNone(r.iloc[0]['top3_hit'])
 def test_explicit_scratch_ok_except_top1(self):
  a=archive();r,i,_=j.reconcile([a],[result('Alpha',2),result('Bravo',1),result('Charlie',scratched=True)])
  self.assertTrue(bool(r.iloc[0]['strict_ok']))
  b=archive();r,i,_=j.reconcile([b],[result('Alpha',scratched=True),result('Bravo',1),result('Charlie',2)])
  self.assertFalse(bool(r.iloc[0]['strict_ok']))
  self.assertEqual(r.iloc[0]['strict_status'],'TOP1_SCRATCHED_NO_REPLACEMENT')
 def test_extra_and_name_collision(self):
  a=archive();r,i,_=j.reconcile([a],[result('Alpha',2),result('Bravo',1),result('Charlie',3),result('Delta',4)])
  self.assertFalse(bool(r.iloc[0]['strict_ok']))
  self.assertIn('OFFICIAL_RUNNER_NOT_IN_LOCKED_SNAPSHOT',set(i['issue']))
 def test_tampered_hash_blocks(self):
  a=archive();a['payload']['rankings'][0]['horse']='Changed'
  r,i,_=j.reconcile([a],[result('Alpha',2),result('Bravo',1),result('Charlie',3)])
  self.assertFalse(bool(r.iloc[0]['strict_ok']))
  self.assertIn('ARCHIVE_HASH_UNVERIFIED',set(i['issue']))
 def test_block_hypothetical_roi_when_race_unreconciled(self):
  import pandas as pd
  a=archive();r,_,_=j.reconcile([a],[result('Alpha',2),result('Bravo',1)])
  old=pd.DataFrame([{'batch_id':'one','rank':1,'settlement_status':'GRADED_RESEARCH_ONLY',
      'stake':10.,'profit':25.,'total_return':35.,'win_hit':True,'place_hit':False}])
  new=j.gate_tickets(old,r)
  self.assertEqual(new.iloc[0]['settlement_status'],'BLOCKED_BY_V061J_STRICT_INTEGRITY')
  self.assertTrue(pd.isna(new.iloc[0]['profit']))
  self.assertEqual(old.iloc[0]['profit'],25.)
 def test_incomplete_official_not_assumed_scratch(self):
  a=archive();r,i,_=j.reconcile([a],[result('Alpha',2),result('Bravo',1)])
  self.assertTrue((i['issue']=='LOCKED_RUNNER_NOT_IN_OFFICIAL_RESULT').any())
  self.assertFalse((i['issue']=='TOP1_SCRATCHED_NO_REPLACEMENT').any())

if __name__=='__main__':unittest.main(verbosity=2)
