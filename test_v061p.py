import unittest
import pandas as pd
from v061p_race_type_lab import make_reports,race_metadata

class V061PTests(unittest.TestCase):
    def test_distance_and_maiden_from_race_only(self):
        b={'source_form_payload':{'distance':'1200m','raceName':'Maiden Plate','runners':[{'distance':2200}]}}
        m=race_metadata(b)
        self.assertEqual(m['distance_m'],1200)
        self.assertEqual(m['maiden_group'],'EXPLICIT_MAIDEN')
    def test_unscored_not_maiden(self):
        b={'source_form_payload':{'distance':1600,'runners':[{'starts':0}]}}
        self.assertEqual(race_metadata(b)['maiden_group'],'MAIDEN_UNDETERMINED')
    def test_conflicting_distance_rejected(self):
        b={'source_form_payload':{'distance':1200,'race':{'distance':1400}}}
        self.assertIsNone(race_metadata(b)['distance_m'])
    def test_no_outcome_in_metadata(self):
        b={'source_form_payload':{'distance':2000,'result':{'distance':1300,'winningHorse':'A'}}}
        self.assertEqual(race_metadata(b)['distance_m'],2000)
    def sample(self):
        rows=[];feats=[];batches=[]
        for day in range(1,11):
            bid=f'id-{day}'
            rows.append({'batch_id':bid,'race_date':f'2026-10-{day:02d}','track':'Test', 'race_number':1,'status':'EVALUABLE_RETROSPECTIVE_RESEARCH','top1_won':day%2==0,'winner_in_top3':True,'actual_starters':6,'original_top1_scratched':False,'winner_count':1})
            feats.extend([{'batch_id':bid,'model_rank':1,'model_score':60},{'batch_id':bid,'model_rank':2,'model_score':None}])
            batches.append({'id':bid,'source_form_payload':{'distance':1200 if day<5 else 2100,'raceName':'Maiden' if day==1 else 'Class 2'}})
        return pd.DataFrame(rows),pd.DataFrame(feats),batches
    def test_reports_and_no_imputation(self):
        r,f,b=self.sample();reports=make_reports(r,f,b)
        self.assertEqual(reports['summary'].iloc[0]['eligible_races'],10)
        self.assertEqual(reports['summary'].iloc[0]['unscored_field_eligible'],10)
        self.assertEqual(reports['summary'].iloc[0]['maiden_unknown_eligible'],9)
        self.assertTrue((reports['eligible_race_categories']['score_band']=='ONE_UNSCORED').all())
        self.assertEqual(reports['summary'].iloc[0]['holdout_races'],3)
        self.assertEqual(reports['summary'].iloc[0]['development_races'],7)
    def test_duplicate_batch_stops(self):
        r,f,b=self.sample();r=pd.concat([r,r.iloc[:1]])
        with self.assertRaisesRegex(ValueError,'Duplicate race'):make_reports(r,f,b)
    def test_race_field_missing_not_invented(self):
        r,f,b=self.sample();b[0]['source_form_payload'].pop('distance')
        d=make_reports(r,f,b)['integrity']
        self.assertEqual(d.iloc[0]['distance_band'],'DISTANCE_UNAVAILABLE')
    def test_production_untouched(self):
        r,f,b=self.sample();s=make_reports(r,f,b)['summary'].iloc[0]
        self.assertFalse(s['production_changed'])
        self.assertFalse(s['new_predictions_generated'])
if __name__=='__main__':unittest.main()
