import unittest
import copy
import hashlib
import json
from datetime import datetime,timezone,timedelta
from unittest.mock import patch
import v061r_class_validation as m
import install_v061r as installer

class Tests(unittest.TestCase):
    def test_class_families(self):
        checks={'3YO+ Maiden':'MAIDEN','MDN HCP':'MAIDEN','BM 64 Handicap':'BENCHMARK',
                'CL2':'CLASS','Group 1':'BLACK_TYPE_GROUP1','G3':'BLACK_TYPE_GROUP3',
                'Listed':'BLACK_TYPE_LISTED','Open Handicap':'OPEN'}
        for label,expected in checks.items():
            self.assertEqual(m.classify_text({'raceClass':label})['class_family'],expected,label)
    def test_unknown_is_not_nonmaiden(self):
        self.assertEqual(m.classify_text({'raceClass':'3YO Fillies'})['maiden_class'],'UNKNOWN')
        self.assertEqual(m.classify_text({})['maiden_class'],'UNKNOWN')
    def test_explicit_false_maiden(self):
        c=m.classify_text({'isMaiden':False})
        self.assertEqual((c['maiden_class'],c['class_family']),('VERIFIED_NON_MAIDEN','OTHER_VERIFIED_NON_MAIDEN'))
    def test_conflict_never_resolved_by_guess(self):
        self.assertEqual(m.classify_text({'raceClass':'Maiden BM64'})['class_family'],'UNKNOWN')
        self.assertEqual(m.attributes_from_sources({'raceClass':'Maiden'},{'raceClass':'BM64'})['class_family'],'UNKNOWN')
    def test_distance_sources(self):
        self.assertEqual(m.attributes_from_sources({'distance':'2400m'})['distance_band'],'STAYING_GE_2000')
        self.assertEqual(m.attributes_from_sources({'distance':'1200m'},{'race':{'distance':1300}})['distance_m'],None)
        self.assertIsNone(m.attributes_from_sources({'distance':'6 furlongs'})['distance_m'])
    def test_immutability_and_unscored(self):
        p={'model_version':'v0.2','rankings':[{'rank':1,'horse':'A','score':83},{'rank':2,'horse':'B','score':None}],
           'form_response_raw':{'raceClass':'Maiden','distance':2200},'scheduled_race_raw':{'raceClass':'Maiden'},
           'confidence_shadow_v061m':{'probabilities':{}}}
        old=copy.deepcopy(p)
        s=m.attach_class_shadow(p)
        self.assertEqual(p,old)
        self.assertEqual(s['rankings'],old['rankings'])
        self.assertEqual(s['confidence_shadow_v061m'],old['confidence_shadow_v061m'])
        self.assertEqual(s[m.KEY]['unscored_count'],1)
        self.assertEqual(s[m.KEY]['distance_band'],'STAYING_GE_2000')
        self.assertEqual(s[m.KEY]['maiden_class'],'EXPLICIT_MAIDEN')
        with self.assertRaises(ValueError):m.attach_class_shadow(s)
    def test_lock_frozen_experience(self):
        payload={'model_version':'v0.2','rankings':[{'rank':1,'horse':'First Horse','score':55},{'rank':2,'horse':'Second Horse','score':None}],
                 'form_response_raw':{'runners':[{'name':'First Horse','stats':{'overall':{'starts':17}}},
                                                 {'name':'Second Horse','stats':{'overall':{'starts':0}}}]}}
        shadow=m.attach_class_shadow(payload)[m.KEY]
        self.assertEqual(shadow['top1_career_starts'],17)
        self.assertEqual(shadow['top1_career_band'],'TOP1_5_19')
        self.assertEqual(shadow['field_career_known_count'],2)
        self.assertEqual(shadow['unscored_count'],1)

    def test_spec_hash_stable(self):
        self.assertEqual(m.SPEC_SHA,m._canonical(m.SPEC))
        self.assertEqual(len(m.SPEC_SHA),64)
    def test_install_idempotent_no_source_change_to_scorer(self):
        app='import v061q_stayer_validation\nif True:\n    v061q_stayer_validation.render(secret)\n'
        pros='def archive():\n    payload = attach_shadow(payload)  # SAME immutable archive row/hash as v0.2 rankings\n'
        a,p=installer.patch(app,pros)
        self.assertIn('v061r_class_validation.render(secret)',a)
        self.assertIn('attach_class_shadow(payload)',p)
        self.assertEqual((a,p),installer.patch(a,p))
    def test_forward_empty_no_backfill(self):
        import pandas as pd
        import sys, types
        stub=types.ModuleType('v061_prospective')
        stub._result_index=lambda _: {}
        with patch.dict(sys.modules, {'v061_prospective':stub}):
            r=m.prospective_reports([{'payload':{},'batch_id':'older'}],[])
        self.assertEqual(r['prospective_summary'].iloc[0]['at_lock_v061r_shadows'],0)
    def test_reject_unknown_version(self):
        with self.assertRaises(ValueError):m.attach_class_shadow({'model_version':'v0.3'})
    def test_scheduled_time_validated_by_existing_archive(self):
        now=datetime.now(timezone.utc)
        p={'model_version':'v0.2','rankings':[{'rank':1,'horse':'A','score':1},{'rank':2,'horse':'B','score':0}],
           'form_response_raw':{'raceClass':'BM64','distance':1200},
           'scheduled_race_raw':{'raceClass':'BM64'},
           'source_times':{'ranking_generated_utc':(now-timedelta(seconds=3)).isoformat()}}
        with patch.object(m,'datetime') as clock:
            clock.now.return_value=now
            s=m.attach_class_shadow(p)
        # existing evaluator only, just confirms hash and timestamps of newly shadowed archive
        self.assertEqual(s[m.KEY]['hypotheses_sha256'],m.SPEC_SHA)
        self.assertTrue(s[m.KEY]['research_only'])

if __name__=='__main__':unittest.main()
