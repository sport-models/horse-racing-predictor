import unittest
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from unittest.mock import patch
import v061m_capture as m

NOW = datetime.now(timezone.utc)

def sample():
    return {'model_version':'v0.2','scheduled_start_utc':(NOW+timedelta(hours=2)).isoformat(),
            'source_times':{'ranking_generated_utc':(NOW-timedelta(seconds=5)).isoformat()},
            'rankings':[{'rank':1,'horse':'A','score':65},{'rank':2,'horse':'B','score':45},
                        {'rank':3,'horse':'C','score':20}]}

class Tests(unittest.TestCase):
    def test_config(self): self.assertEqual(m.frozen_config()['config_sha256'],m.EXPECTED_CONFIG_HASH)
    def test_probs(self):
        s=m.calculate(sample());self.assertEqual(s['field_size'],3);self.assertEqual(s['rank1_rank2_gap'],20)
        self.assertEqual(len(s['probabilities']),3)
        self.assertTrue(all(0<x<1 for x in s['probabilities'].values()))
    def test_no_mutation(self):
        p=sample(); before=deepcopy(p);m.attach_shadow(p);self.assertEqual(p,before)
    def test_no_overwrite(self):
        p=m.attach_shadow(sample())
        with self.assertRaisesRegex(ValueError,'already exists'):m.attach_shadow(p)
    def test_bad_model(self):
        p=sample();p['model_version']='v0.3'
        with self.assertRaises(ValueError):m.calculate(p)
    def test_invalid_score(self):
        p=sample();p['rankings'][0]['score']=None
        result=m.calculate(p)
        self.assertEqual(result['status'],'PREREGISTERED_CONFIDENCE_UNAVAILABLE_UNSCORED')
        self.assertEqual(result['probabilities'],{})
        self.assertEqual(result['unscored_count'],1)
    def test_negative_gap(self):
        p=sample();p['rankings'][1]['score']=70
        with self.assertRaises(ValueError):m.calculate(p)
    def test_duplicate_ranks(self):
        p=sample();p['rankings'][1]['rank']=1
        with self.assertRaises(ValueError):m.calculate(p)
    def test_late_registration(self):
        p=sample();p['scheduled_start_utc']=(NOW-timedelta(seconds=1)).isoformat()
        with self.assertRaises(ValueError):m.calculate(p)
    def test_missing_shadows_not_backfilled(self):
        self.assertEqual(m.audit_archives([{'payload':sample()}]),[])
    def test_bad_archive_hash_flags(self):
        p=m.attach_shadow(sample());s=m.audit_archives([{'payload':p,'payload_sha256':'wrong',
            'scheduled_start_utc':p['scheduled_start_utc'],'created_at':(NOW+timedelta(seconds=10)).isoformat()}]);
        self.assertEqual(s[0]['status'],'INVALID');self.assertIn('ARCHIVE_HASH_MISMATCH',s[0]['issues'])

if __name__=='__main__':unittest.main()
