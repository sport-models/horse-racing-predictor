import unittest
from copy import deepcopy
import pandas as pd
import v061o_weight_lab as m

def runner(horse,rank,form='11111',score=None):
 d={'batch_id':'r1','horse':horse,'model_rank':rank,'recent_form':form,
 'career_starts':10,'career_wins':3,'track_starts':5,'track_wins':1,
 'distance_starts':4,'distance_wins':1,'track_distance_starts':2,
 'track_distance_wins':1,'barrier':8,'weight_kg':56,'scratched_in_historical_response':False}
 parts,_=m.components(d)
 d['model_score']=m.score(parts,m.WEIGHTS['V02_FROZEN']) if score is None else score
 return d

def race():
 return {'batch_id':'r1','race_date':'2026-10-10','track':'X','race_number':1,
 'status':'EVALUABLE_RETROSPECTIVE_RESEARCH','original_top1_scratched':False,
 'winning_horses':'A','winner_count':1,'actual_starters':3,'runner_count':3}
class Tests(unittest.TestCase):
 def test_weights_sum(self):
  for w in m.WEIGHTS.values():self.assertAlmostEqual(sum(w),1.)
 def test_parity_and_readonly(self):
  rr=pd.DataFrame([race()]);ff=pd.DataFrame([runner('A',1),runner('B',2,'22222'),runner('C',3,'55555')]);before=deepcopy(ff)
  out=m.run(rr,ff)
  self.assertEqual(out['summary'].iloc[0]['races_eligible_before_ties'],1)
  pd.testing.assert_frame_equal(ff,before)
 def test_parity_fail_closed(self):
  rr=pd.DataFrame([race()]);ff=pd.DataFrame([runner('A',1),runner('B',2,'22222'),runner('C',3,'55555')]);ff.loc[0,'model_score']+=1
  out=m.run(rr,ff)
  self.assertFalse(bool(out['integrity'].iloc[0]['eligible']))
  self.assertIn('V02_PARITY_FAIL',out['integrity'].iloc[0]['reasons'])
 def test_missing_unscored(self):
  rr=pd.DataFrame([race()]);ff=pd.DataFrame([runner('A',1),runner('B',2,'22222'),runner('C',3,'55555')]);ff.loc[2,'recent_form']='';ff.loc[2,'model_score']=None
  self.assertTrue(bool(m.run(rr,ff)['integrity'].iloc[0]['eligible']))
 def test_scratched_not_fabricated(self):
  f=runner('Scratch',None);f['scratched_in_historical_response']=True;f['model_score']=None
  _,r,_=m.prepare(pd.DataFrame([race()]),pd.DataFrame([f]));self.assertTrue(bool(r.iloc[0]['score_parity']))
 def test_bad_columns(self):
  with self.assertRaises(ValueError):m.run(pd.DataFrame([race()]),pd.DataFrame([{'batch_id':'r1'}]))
 def test_no_output_leakage_into_components(self):
  x=runner('A',1);base=m.components(x)
  x['winning_horses']='NOT_A';x['finishing_position']=20
  self.assertEqual(base,m.components(x))
 def test_fractional_lap(self):
  a=runner('A',1);a['barrier']=1;a['weight_kg']=48
  self.assertEqual(m.components(a)[1],None)
if __name__=='__main__':unittest.main()
