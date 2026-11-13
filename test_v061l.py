"""Offline unit checks. python test_v061l.py"""
import unittest
import numpy as np
import pandas as pd
import v061l_confidence as x

class TestConfidence(unittest.TestCase):
    def synthetic(self):
        rng=np.random.default_rng(42)
        dates=pd.date_range('2026-09-01',periods=25)
        rows=[]
        for j in range(500):
            n=int(rng.integers(5,15));gap=float(rng.uniform(0,15))
            p=float(x._sigmoid(np.array([-.85-.18*np.log(n/10)+.07*gap]))[0]);y=bool(rng.random()<p)
            rows.append(dict(batch_id=str(j),race_date=str(dates[j%25].date()),status='EVALUABLE_RETROSPECTIVE_RESEARCH',
                 actual_starters=n,gap=gap,top1_won=y,original_top1_scratched=False,duplicate_top_rank=False))
        return pd.DataFrame(rows)
    def test_stable_split(self):
        r=x.build_historical(self.synthetic())
        c=r['coverage'].iloc[0]
        self.assertTrue(c.train_through<c.holdout_from)
        self.assertEqual(int(c.training_races)+int(c.holdout_races),500)
    def test_design(self):
        self.assertEqual(x._design([10],[5],'FIELD_BASELINE').shape,(1,2))
        self.assertEqual(x._design([10],[5],'GAP_ONLY_CHALLENGER').shape,(1,3))
        self.assertEqual(x._design([10],[5],'FIELD_GAP_INTERACTION_CHALLENGER').shape,(1,4))
    def test_no_missing_scores_as_zero(self):
        f=self.synthetic();f.loc[0,'gap']=float('nan');r=x.build_historical(f)
        self.assertEqual(int(r['coverage'].iloc[0].eligible_scored_rows),499)
    def test_no_negative_gaps(self):
        f=self.synthetic();f.loc[0,'gap']=-1;r=x.build_historical(f)
        self.assertEqual(int(r['coverage'].iloc[0].eligible_scored_rows),499)
    def test_no_duplicate_archives(self):
        f=self.synthetic();f.loc[0,'batch_id']=f.loc[1,'batch_id'];r=x.build_historical(f)
        self.assertEqual(int(r['coverage'].iloc[0].eligible_scored_rows),498)
    def test_train_outcomes_do_not_use_holdout(self):
        f=self.synthetic();r1=x.build_historical(f)
        holdout_start=r1['coverage'].iloc[0].holdout_from
        f.loc[f.race_date>=holdout_start,'top1_won']=~f.loc[f.race_date>=holdout_start,'top1_won'].astype(bool)
        r2=x.build_historical(f)
        self.assertEqual(r1['config']['models'],r2['config']['models'])
    def test_shadow_not_prelabeled(self):
        c=x.build_historical(self.synthetic())['config']
        ar=[{'batch_id':'a','payload':{'rankings':[{'rank':1,'horse':'x','score':65},{'rank':2,'horse':'y','score':60}]}}]
        rr=pd.DataFrame([{'batch_id':'a','race_date':'2026-11-13','track':'Pakenham','race_number':1,'strict_status':'PASS','strict_ok':True,'top1_won':False}])
        row=x._shadow_rows(rr,ar,c).iloc[0]
        self.assertEqual(row.shadow_status,'RETROACTIVE_SHADOW_NOT_PRE_REGISTERED')
        self.assertFalse(row.actual_off_verified)
    def test_no_data_writes_in_module(self):
        import inspect
        code=inspect.getsource(x)
        self.assertNotIn('.insert(',code)
        self.assertNotIn('.upsert(',code)
        self.assertNotIn('.update(',code)

if __name__=='__main__':unittest.main(verbosity=2)
