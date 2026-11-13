import unittest
from datetime import datetime, timedelta, timezone
import v061m_capture as m

NOW = datetime.now(timezone.utc)

def example(scores):
    return {'model_version':'v0.2',
            'scheduled_start_utc':(NOW+timedelta(hours=2)).isoformat(),
            'source_times':{'ranking_generated_utc':(NOW-timedelta(seconds=5)).isoformat()},
            'rankings':[{'rank':i+1,'horse':f'Runner {i+1}','score':v}
                        for i,v in enumerate(scores)]}

class FixTests(unittest.TestCase):
    def test_full_scored_field_unchanged(self):
        result=m.calculate(example([67.0,50.0,40.0]))
        self.assertEqual(result['status'],'PREREGISTERED_RESEARCH_ONLY_UNCERTIFIED')
        self.assertEqual(len(result['probabilities']),3)
        self.assertEqual(result['rank1_rank2_gap'],17)
    def test_unscored_rank_1_preserved(self):
        result=m.attach_shadow(example([None,50,40]))['confidence_shadow_v061m']
        self.assertEqual(result['field_size'],3)
        self.assertEqual(result['rank1_score'],None)
        self.assertEqual(result['unscored_count'],1)
        self.assertEqual(result['unscored_runners'],[{'rank':1,'horse':'Runner 1'}])
        self.assertEqual(result['probabilities'],{})
    def test_unscored_outside_top2_skips_probabilities(self):
        result=m.calculate(example([70,65,None]))
        self.assertEqual(result['rank1_rank2_gap'],5)
        self.assertEqual(result['status'],'PREREGISTERED_CONFIDENCE_UNAVAILABLE_UNSCORED')
        self.assertEqual(result['probabilities'],{})
    def test_all_first_timers(self):
        result=m.calculate(example([None,None,None]))
        self.assertEqual(result['unscored_count'],3)
        self.assertIsNone(result['rank1_rank2_gap'])
    def test_nonfinite_score_fails_closed(self):
        with self.assertRaises(ValueError): m.calculate(example([50,float('nan'),None]))
    def test_duplicate_rank_still_rejected(self):
        p=example([65,None,10]);p['rankings'][1]['rank']=1
        with self.assertRaises(ValueError):m.calculate(p)
    def test_original_payload_not_mutated(self):
        p=example([65,None,10]);before=[r.copy() for r in p['rankings']]
        m.attach_shadow(p)
        self.assertEqual(p['rankings'],before)
        self.assertNotIn('confidence_shadow_v061m',p)
    def test_late_lock_still_rejected(self):
        p=example([65,None,10]);p['scheduled_start_utc']=(NOW-timedelta(seconds=2)).isoformat()
        with self.assertRaises(ValueError):m.calculate(p)
    def test_no_double_attach(self):
        p=m.attach_shadow(example([65,None,10]))
        with self.assertRaises(ValueError):m.attach_shadow(p)

if __name__ == '__main__': unittest.main()
