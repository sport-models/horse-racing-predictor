"""Run: python test_v061.py (offline; no Supabase credentials or network access)."""
import ast
from datetime import datetime, timedelta, timezone
from pathlib import Path
import sys
import types
import unittest
import uuid
from unittest.mock import patch

# This file's tests use real integration modules if installed in Codespaces.
# In standalone test environments without Streamlit, stub only its UI import.
try:
    import streamlit
except ImportError:
    sys.modules['streamlit'] = types.ModuleType('streamlit')

import v061_prospective as v
import install_v061


def stub_scorer(row):
    return row.get('test_score')


def stub_recent(row):
    return 0


def stub_win(row):
    return 0, 0


def stub_num(x, default):
    return default


def items(payload, key):
    if isinstance(payload, dict):
        return payload.get(key, [])
    return []


def val(row, *keys, default=None):
    for k in keys:
        if k in row and row[k] is not None:
            return row[k]
    return default


def meeting_name(row):
    return row.get('track')


def quote_match(odds_payload, date, track, number):
    return odds_payload['event']


def quotes(event, horse):
    return event.get('quotes', {}).get(horse, [])


class FakeQuery:
    def __init__(self, client, table):
        self.client, self.table_name, self.batch_id = client, table, None
    def select(self, *args):
        return self
    def eq(self, key, value):
        assert key == 'prediction_batch_id'
        self.batch_id = value
        return self
    def execute(self):
        if self.table_name == 'race_predictions':
            self.data = self.client.saved.get(self.batch_id, [])
        else:
            self.data = self.client.archived
        return self
    def insert(self, record):
        if self.table_name != v.TABLE:
            raise AssertionError('test unexpectedly wrote to other table')
        record = dict(record)
        record['created_at'] = datetime.now(timezone.utc).isoformat()
        self.client.archived.append(record)
        return self


class FakeClient:
    def __init__(self):
        self.saved = {}
        self.archived = []
    def table(self, key):
        return FakeQuery(self, key)


class ProspectiveTests(unittest.TestCase):
    def setUp(self):
        self.current = datetime.now(timezone.utc)
        self.schedule = (self.current + timedelta(hours=2)).isoformat()
        self.updated = (self.current - timedelta(minutes=5)).isoformat()
        self.data = [
            {'name': 'A Mare', 'test_score': 90, 'scratched': False},
            {'name': 'Bee Horse', 'test_score': 80, 'scratched': False},
            {'name': 'C Runner', 'test_score': 70, 'scratched': False},
        ]
        self.form = {'runners': self.data}
        self.meets = {'meetings': [{'track': 'Flemington', 'races': [
            {'raceNumber': 1, 'startTime': self.schedule}]}]}
        self.book = {'quotes': {
            'A Mare': [{'bookmaker':'sportsbet','win':3.1,'place':1.3,
                        'stale':False,'last_update':self.updated}],
            'Bee Horse': [{'bookmaker':'sportsbet','win':2.8,'place':1.4,
                           'stale':False,'last_update':self.updated}],
            'C Runner': [{'bookmaker':'sportsbet','win':5.0,'place':1.8,
                          'stale':False,'last_update':self.updated}],
        }}

    def prepare(self):
        def pretend_get(url, key, params):
            return self.meets if url.endswith('meetings') else self.form
        with patch.object(v, '_request_json', side_effect=pretend_get):
            return v.prepare_live_snapshot(
                race_date=self.current.astimezone(__import__('zoneinfo').ZoneInfo(v.DATE_ZONE)).date().isoformat(),
                track='Flemington', track_slug='flemington', race_number=1,
                formfav_key='fake', puntersedge_key='fake',
                get_items=items, get_value=val, meeting_name=meeting_name,
                score_runner=stub_scorer, recent_form_score=stub_recent,
                win_rate=stub_win, as_number=stub_num,
                odds_find_race=quote_match, odds_runner_quotes=quotes,
                puntersedge_request=lambda *args, **kwargs: {'event': self.book},
            )

    def results(self, prepared):
        p = prepared['payload']
        return [{'race_date':p['race_date'],'track':p['track'],'race_number':1,
                 'horse':horse,'finishing_position':pos,'scratched':False,
                 'result_status':'final'} for horse,pos in
                [('A Mare',2),('Bee Horse',1),('C Runner',3)]]

    def test_at_lock_refresh_and_correct_rank_hash(self):
        p = self.prepare()['payload']
        self.assertEqual([r['horse'] for r in p['rankings']], ['A Mare','Bee Horse','C Runner'])
        self.assertEqual(p['form_response_sha256'], v.digest(self.form))
        self.assertEqual(p['model_version'], 'v0.2')
        self.assertFalse(p['official_actual_off_verified'])

    def test_lock_rejects_start_passed(self):
        self.schedule = (self.current - timedelta(minutes=1)).isoformat()
        self.meets['meetings'][0]['races'][0]['startTime'] = self.schedule
        with self.assertRaises(ValueError):
            self.prepare()

    def test_duplicate_names_blocked(self):
        self.data.append({'name':'A-Mare','test_score':55,'scratched':False})
        with self.assertRaisesRegex(ValueError, 'duplicate'):
            self.prepare()

    def test_archive_and_market_comparison(self):
        prepared = self.prepare()
        db = FakeClient()
        batch = str(uuid.uuid4())
        db.saved[batch] = [{'horse':r['horse'],'model_rank':r['rank'],'form_score':r['score']}
                           for r in prepared['payload']['rankings']]
        receipt = v.archive_locked_batch(db,batch,prepared)
        self.assertEqual(receipt['batch_id'],batch)
        race, issues, rows = v.evaluate_one(receipt, self.results(prepared))
        self.assertEqual(issues, [])
        self.assertEqual(race['timing'], 'BEFORE_SCHEDULE_UNVERIFIED_ACTUAL_OFF')
        self.assertEqual(race['top1_won'], False)
        self.assertEqual(race['top3_hit'], True)
        self.assertEqual(race['market_favourite'], 'Bee Horse')
        self.assertEqual(race['market_favourite_won'], True)
        self.assertFalse(race['certified_point_in_time'])
        self.assertEqual(len(rows),3)
        report = v.evaluate_archives(db.archived,self.results(prepared))
        self.assertEqual(report['summary'].iloc[0]['paired_saved_market_races'],1)
        self.assertEqual(report['summary'].iloc[0]['certified_point_in_time_races'],0)

    def test_market_no_missing_bookmaker_runner_prices(self):
        prepared=self.prepare()
        prepared['payload']['odds_quotes']['C Runner'] = []
        db=FakeClient(); batch=str(uuid.uuid4())
        db.saved[batch] = [{'horse':r['horse'],'model_rank':r['rank'],'form_score':r['score']}
                           for r in prepared['payload']['rankings']]
        rec=v.archive_locked_batch(db,batch,prepared)
        race,issues,_=v.evaluate_one(rec,self.results(prepared))
        self.assertEqual(issues,[])
        self.assertIn('NO_COMPLETE_',race['market_issue'])
        self.assertIsNone(race['market_favourite'])

    def test_market_rejects_quote_after_capture(self):
        prepared=self.prepare()
        late=(self.current+timedelta(minutes=3)).isoformat()
        for q in prepared['payload']['odds_quotes'].values():
            q[0]['last_update']=late
        db=FakeClient(); batch=str(uuid.uuid4())
        db.saved[batch] = [{'horse':r['horse'],'model_rank':r['rank'],'form_score':r['score']}
                           for r in prepared['payload']['rankings']]
        rec=v.archive_locked_batch(db,batch,prepared)
        race,_,_=v.evaluate_one(rec,self.results(prepared))
        self.assertIsNone(race['market_favourite'])

    def test_unmatched_starter_blocks_race(self):
        prepared=self.prepare()
        db=FakeClient(); batch=str(uuid.uuid4())
        db.saved[batch] = [{'horse':r['horse'],'model_rank':r['rank'],'form_score':r['score']}
                           for r in prepared['payload']['rankings']]
        rec=v.archive_locked_batch(db,batch,prepared)
        results=self.results(prepared)
        results.append({'race_date':prepared['payload']['race_date'], 'track':'Flemington',
                        'race_number':1,'horse':'New Starter','finishing_position':4,
                        'scratched':False,'result_status':'final'})
        race,issues,_=v.evaluate_one(rec, results)
        self.assertIn('ACTUAL_STARTER_MISSING_FROM_SNAPSHOT',issues)
        self.assertIsNone(race['top1_won'])

    def test_immutable_payload_hash_tampering_detected(self):
        prepared=self.prepare()
        db=FakeClient(); batch=str(uuid.uuid4())
        db.saved[batch] = [{'horse':r['horse'],'model_rank':r['rank'],'form_score':r['score']}
                           for r in prepared['payload']['rankings']]
        rec=v.archive_locked_batch(db,batch,prepared)
        rec['payload']['rankings'][0]['score'] = 123
        race,issues,_=v.evaluate_one(rec,self.results(prepared))
        self.assertIn('PAYLOAD_HASH_MISMATCH',issues)

    def test_scratch_promotion_is_retrospective(self):
        prepared=self.prepare()
        db=FakeClient(); batch=str(uuid.uuid4())
        db.saved[batch] = [{'horse':r['horse'],'model_rank':r['rank'],'form_score':r['score']}
                           for r in prepared['payload']['rankings']]
        rec=v.archive_locked_batch(db,batch,prepared)
        results=self.results(prepared)
        results[0]['scratched']=True
        results[0]['finishing_position']=None
        race,issues,_=v.evaluate_one(rec,results)
        self.assertIn('ORIGINAL_TOP1_SCRATCHED_RETROSPECTIVE_SUBSTITUTION', issues)
        self.assertEqual(race['model_top1'],'Bee Horse')
        self.assertTrue(race['original_rank1_scratched'])
        self.assertTrue(race['active_runner_selection_retrospective'])

    def test_legacy_batch_mismatch_refused(self):
        prepared=self.prepare()
        db=FakeClient(); batch=str(uuid.uuid4())
        db.saved[batch] = [{'horse':'A Mare','model_rank':3,'form_score':90}]
        with self.assertRaises(ValueError):
            v.archive_locked_batch(db,batch,prepared)

    def test_installer_idempotent(self):
        skeleton = ('import v060_validation_hub\n'
                    'def score_runner(x): pass\n'
                    'def snapshot_race(x): pass\n'
                    'def bets_tab(): pass\n'
                    'def results_tab(): pass\n'
                    'def performance_tab(): pass\n'
                    'def analysis_tab():\n'
                    + install_v061.SAVE_OLD + '\n'
                    + 'with t5:\n    v060_validation_hub.render(db_rows)\n')
        patched, changed = install_v061.patched(skeleton)
        self.assertTrue(changed)
        again, changed = install_v061.patched(patched)
        self.assertFalse(changed)
        self.assertEqual(again, patched)
        ast.parse(patched)


if __name__ == '__main__':
    unittest.main(verbosity=2)
