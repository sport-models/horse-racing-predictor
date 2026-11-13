import unittest
import pandas as pd
from v061q_stayer_validation import make_reports,class_label

class QTests(unittest.TestCase):
    def synthetic(self):
        races=[];features=[];batches=[]
        for d in range(1,21):
            bid='demo_'+str(d)
            races.append({'batch_id':bid,'race_date':f'2026-10-{d:02d}','track':'Test','race_number':1,
              'status':'EVALUABLE_RETROSPECTIVE_RESEARCH','top1_won':d%4==0,'winner_in_top3':d%2==0,
              'actual_starters':8,'original_top1_scratched':False,'winner_count':1,'ranked_count':3})
            for n in range(1,4):
                features.append({'batch_id':bid,'model_rank':n,'model_score':65-n,
                    'career_starts':d+n,'distance_starts':n,'track_starts':2})
            batches.append({'id':bid,'source_form_payload':{'distance':2200 if d%2 else 1200,'raceClass':'BM 64','raceName':'Maiden' if d==1 else 'Handicap'}})
        return pd.DataFrame(races),pd.DataFrame(features),batches
    def test_class_from_explicit(self):
        self.assertEqual(class_label({'source_form_payload':{'raceClass':' BM 64 ','result':{'raceClass':'BM99'}}})[0],'BM 64')
    def test_no_class_inference(self):
        self.assertEqual(class_label({'source_form_payload':{'raceName':'Some Race'}})[0],'CLASS_UNDETERMINED')
    def test_reports(self):
        a,b,c=self.synthetic();r=make_reports(a,b,c)
        self.assertEqual(len(r['experience_integrity']),20)
        self.assertEqual(int(r['summary']['holdout_races'].iloc[0]),6)
        self.assertTrue((r['experience_integrity']['reconstructed_score_coverage_ok']).all())
        self.assertEqual(r['summary']['model_promotion'].iloc[0],'NOT_APPROVED')
        self.assertEqual(len(r['adjusted_holdout']),3)
    def test_missing_experience_not_zero(self):
        a,b,c=self.synthetic();b.loc[b.model_rank.eq(1),'distance_starts']=None
        out=make_reports(a,b,c)
        self.assertEqual(int(out['summary']['complete_experience_races'].iloc[0]),0)
        self.assertTrue((out['adjusted_holdout']['status']=='INSUFFICIENT_COMPLETE_EXPERIENCE').all())
    def test_unscored_count_preserved(self):
        a,b,c=self.synthetic();b.loc[b.model_rank.eq(3),'model_score']=None
        result=make_reports(a,b,c)
        self.assertTrue((result['experience_integrity']['unscored_count']==1).all())
    def test_no_class_conflict_silent(self):
        self.assertEqual(class_label({'source_form_payload':{'raceClass':'BM 64','race':{'raceClass':'BM 70'}}})[0],'CLASS_CONFLICT')
    def test_duplicate_ranks(self):
        a,b,c=self.synthetic();b.loc[b.model_rank.eq(3),'model_rank']=2
        with self.assertRaisesRegex(ValueError,'Duplicated'):make_reports(a,b,c)
    def test_split_holdout_fixed_day(self):
        a,b,c=self.synthetic();r=make_reports(a,b,c)
        z=r['experience_integrity'];self.assertEqual(set(z[z.split=='CHRONOLOGICAL_HOLDOUT'].race_date),set('2026-10-%02d'%d for d in range(15,21)))
if __name__=='__main__':unittest.main()
