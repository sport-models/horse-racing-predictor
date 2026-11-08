"""Offline tests: no actual provider, Supabase, or betting ledger writes."""
import json
import tempfile
import unittest
from pathlib import Path
from datetime import date, datetime, timezone
from types import SimpleNamespace

import v061b_historical as m
import install_v061b as installer


def scorer_fixture(r):
    # A deterministic fixture; live app passes its unchanged production scorer.
    return r.get('fixture_score')


RACE={'race_date':'2026-11-07','track':'Flemington','track_key':'flemington',
      'track_slug':'flemington','race_number':4,'scheduled_start':'2026-11-07T03:00:00Z'}
RUNNERS=[
    {'name':'Alpha','number':1,'form':'X3212','fixture_score':91.25,
     'jockey':'Jockey One','trainer':'Trainer One',
     'jockeyStats':{'starts':10,'wins':2},'trainerStats':{'starts':20,'wins':5},'barrier':5,'weight':56.5,
     'stats':{'overall':{'starts':10,'wins':3,'places':5},'track':{'starts':3,'wins':1},
              'distance':{'starts':5,'wins':2},'trackDistance':{'starts':2,'wins':1}}},
    {'name':'Beta','number':2,'form':'25844','fixture_score':70,'jockey':'Jockey Two',
     'trainer':'Trainer Two','barrier':1,'weight':54.5,'stats':{'overall':{'starts':0,'wins':0}}},
    {'name':'Gamma','number':3,'form':None,'fixture_score':None,'jockey':{'name':'Jockey Three'},
     'trainer':{'name':'Trainer Three'},'stats':{}},
    {'name':'Delta','number':4,'form':'3124','fixture_score':99,
     'scratched':True,'jockey':'Jockey Four','trainer':'Trainer Four'},
]
FORM={'date':'2026-11-07','track':'Flemington','raceNumber':4,'runners':RUNNERS}
RESULTS={'date':'2026-11-07','track':'Flemington','raceNumber':4,'resultStatus':'final',
         'numberOfRunners':4,'placings':[
             {'name':'Alpha','position':1,'scratched':False,'startingPrice':3.5},
             {'name':'Beta','position':2,'scratched':False,'startingPrice':2.1},
             {'name':'Gamma','position':3,'scratched':False,'startingPrice':7.5},
             {'name':'Delta','position':None,'scratched':True,'startingPrice':None}]}


class FakeTable:
    def __init__(self, parent, name):
        self.parent=parent;self.name=name;self.predicates=[];self.mode=None;self.value=None
    def select(self, _):self.mode='select';return self
    def eq(self, col, val):self.predicates.append((col,val));return self
    def limit(self, _):return self
    def insert(self, value):self.mode='insert';self.value=value;return self
    def execute(self):
        if self.mode=='select':
            filtered=[x for x in self.parent.rows.get(self.name,[]) if all(str(x.get(k))==str(v) for k,v in self.predicates)]
            return SimpleNamespace(data=filtered)
        if self.mode=='insert':
            self.parent.rows.setdefault(self.name,[]).append(self.value)
            return SimpleNamespace(data=[self.value])
        raise AssertionError('Bad fake mode')


class FakeDB:
    def __init__(self):self.rows={};self.rpc_count=0
    def table(self,name):return FakeTable(self,name)
    def rpc(self,name,payload):
        assert name=='v061b_capture_historical'
        race=payload['p_batch'];features=payload['p_runners']
        if any(x['race_date']==race['race_date'] and x['track_key']==race['track_key'] and x['race_number']==race['race_number']
               for x in self.rows.get(m.RACES,[])):
            raise ValueError('unique_violation')
        assert race['runner_count']==len(features)
        self.rows.setdefault(m.RACES,[]).append(race)
        for feature in features:
            self.rows.setdefault(m.FEATURES,[]).append(dict(feature,batch_id=race['id']))
        self.rpc_count+=1
        return SimpleNamespace(execute=lambda:SimpleNamespace(data=race['id']))


class Response:
    def __init__(self,data):self.data=data
    def raise_for_status(self):pass
    def json(self):return self.data


class V061bTests(unittest.TestCase):
    def test_recent_form_jockey_and_trainer_separate_and_full_raw(self):
        start=datetime(2026,11,8,tzinfo=timezone.utc)
        batch,rows=m.make_capture(RACE,FORM,start,start,scorer_fixture)
        self.assertEqual(batch['model_version'],'v0.2')
        self.assertEqual(batch['cohort'],m.COHORT)
        self.assertEqual([r['model_rank'] for r in rows],[1,2,3,None])
        self.assertEqual(rows[0]['jockey'],'Jockey One')
        self.assertEqual(rows[0]['trainer'],'Trainer One')
        self.assertEqual(rows[0]['recent_form_last5'],'3212')
        self.assertEqual(rows[0]['career_starts'],10)
        self.assertEqual(rows[0]['career_wins'],3)
        self.assertAlmostEqual(rows[0]['career_win_rate'],.3)
        self.assertEqual(rows[0]['track_distance_wins'],1)
        self.assertEqual(rows[0]['jockey_starts'],10)
        self.assertEqual(rows[0]['jockey_wins'],2)
        self.assertAlmostEqual(rows[0]['trainer_win_rate'],.25)
        self.assertEqual(rows[0]['barrier'],5)
        self.assertEqual(rows[0]['weight_kg'],56.5)
        self.assertEqual(rows[2]['jockey'],'Jockey Three')
        self.assertEqual(rows[2]['trainer'],'Trainer Three')
        self.assertIsNone(rows[2]['career_starts'])
        self.assertEqual(rows[3]['scratched_in_historical_response'],True)
        self.assertEqual(rows[3]['feature_payload']['name'],'Delta')
        self.assertEqual(rows[0]['bookmaker_quotes'],[])
        self.assertEqual(batch['source_form_sha256'],m.digest(FORM))

    def test_historical_scan_uses_only_meetings(self):
        visited=[]
        def requester(url,**kwargs):
            visited.append(url)
            return Response({'meetings':[{'country':'AU','track':'Flemington','slug':'flemington',
                     'races':[{'raceNumber':4,'startTime':RACE['scheduled_start']}]}]})
        plan,errors=m.scan_meetings([date(2026,11,7)],'TEST',requester=requester)
        self.assertEqual(len(plan),1)
        self.assertEqual(len(errors),0)
        self.assertEqual(visited,[m.MEETINGS_URL])

    def test_capture_only_form_then_idempotently_skip(self):
        db=FakeDB();visited=[]
        def requester(url,**kwargs):
            visited.append(url)
            if url!=m.FORM_URL:raise AssertionError('Result API used during prediction!')
            return Response(FORM)
        a=m.capture_one(db,RACE,'TEST',scorer_fixture,requester=requester)
        b=m.capture_one(db,RACE,'TEST',scorer_fixture,requester=requester)
        self.assertEqual(a['status'],'ARCHIVED_RESEARCH')
        self.assertEqual(b['status'],'SKIPPED_ALREADY_ARCHIVED')
        self.assertEqual(visited,[m.FORM_URL])
        self.assertEqual(db.rpc_count,1)
        self.assertEqual(len(db.rows[m.FEATURES]),4)

    def test_official_results_are_separate_and_checked(self):
        db=FakeDB()
        capture=m.capture_one(db,RACE,'TEST',scorer_fixture,requester=lambda *_args,**_kwargs:Response(FORM))
        row=db.rows[m.RACES][0]
        preview=m.fetch_results_preview(row,'TEST',requester=lambda *_args,**_kwargs:Response(RESULTS))
        self.assertEqual(preview['starters'],3)
        m.persist_results(db,preview)
        self.assertEqual(len(db.rows[m.RESULTS]),1)
        self.assertEqual(len(db.rows[m.FEATURES]),4)
        self.assertEqual(db.rows[m.RESULTS][0]['batch_id'],capture['batch_id'])
        output=m.build_reports(db.rows[m.RACES],db.rows[m.FEATURES],db.rows[m.RESULTS])
        self.assertEqual(output['summary'].iloc[0]['top1_wins'],1)
        self.assertEqual(output['summary'].iloc[0]['winner_in_top3'],1)
        self.assertAlmostEqual(output['summary'].iloc[0]['sp_research_net_profit_10'],25)
        self.assertEqual(output['races'].iloc[0]['status'],'EVALUABLE_RETROSPECTIVE_RESEARCH')
        self.assertEqual(output['summary'].iloc[0]['pre_race_certified_count'],0)

    def test_result_parser_rejects_leaked_or_incomplete_results(self):
        copy=json.loads(json.dumps(RESULTS));copy['resultStatus']='pending'
        with self.assertRaises(ValueError):m.parse_final_results(copy,RACE)
        copy=json.loads(json.dumps(RESULTS));copy['placings'][1]['position']=1
        with self.assertRaises(ValueError):m.parse_final_results(copy,RACE)
        copy=json.loads(json.dumps(RESULTS));copy['numberOfRunners']=5
        with self.assertRaises(ValueError):m.parse_final_results(copy,RACE)
        copy=json.loads(json.dumps(RESULTS));copy['date']='2026-11-06'
        with self.assertRaises(ValueError):m.parse_final_results(copy,RACE)

    def test_missing_actual_starter_blocks_backtest(self):
        db=FakeDB();m.capture_one(db,RACE,'TEST',scorer_fixture,requester=lambda *_a,**_kw:Response(FORM))
        payload=json.loads(json.dumps(RESULTS));payload['placings'][2]['name']='Unknown Starter'
        data={'batch_id':db.rows[m.RACES][0]['id'],'result_payload':payload,'result_sha256':m.digest(payload)}
        report=m.build_reports(db.rows[m.RACES],db.rows[m.FEATURES],[data])
        self.assertEqual(report['races'].iloc[0]['status'],'INCOMPLETE_PREDICTIONS')
        self.assertEqual(report['summary'].iloc[0]['evaluable_research_races'],0)

    def test_scratched_rank1_not_replaced_after_results(self):
        db=FakeDB();m.capture_one(db,RACE,'TEST',scorer_fixture,requester=lambda *_a,**_kw:Response(FORM))
        payload=json.loads(json.dumps(RESULTS))
        # The originally top rated Alpha was a starter in /form, then scratched.
        payload['placings'][0]['position']=None;payload['placings'][0]['scratched']=True
        payload['placings'][1]['position']=1
        # Delta remains scratched; no unpredicted new starter is introduced.
        result={'batch_id':db.rows[m.RACES][0]['id'],'result_payload':payload,'result_sha256':m.digest(payload)}
        report=m.build_reports(db.rows[m.RACES],db.rows[m.FEATURES],[result])
        self.assertEqual(report['races'].iloc[0]['status'],'ORIGINAL_TOP1_SCRATCHED')
        self.assertEqual(report['summary'].iloc[0]['evaluable_research_races'],0)

    def test_strict_rolling_window(self):
        x=m.race_date_window(date(2026,10,10),date(2026,11,7),date(2026,11,8))
        self.assertEqual(len(x),29)
        with self.assertRaises(ValueError):m.race_date_window(date(2026,10,8),date(2026,11,7),date(2026,11,8))
        with self.assertRaises(ValueError):m.race_date_window(date(2026,11,7),date(2026,11,8),date(2026,11,8))

    def test_missing_stats_are_null_not_fabricated(self):
        row=m.feature_row({'name':'Test','form':'','jockey':'One','trainer':'Two','stats':{}},None,1)
        self.assertIsNone(row['career_wins'])
        self.assertIsNone(row['career_win_rate'])
        self.assertIsNone(row['track_starts'])
        self.assertIsNone(row['weight_kg'])
        self.assertIsNone(row['barrier'])

    def test_scoring_does_not_read_result_fields(self):
        def scorer_other(r):
            return 72.5 if r.get('name')=='Alpha' else None
        with_result=json.loads(json.dumps(FORM));with_result['resultStatus']='final'
        for i,r in enumerate(with_result['runners']):
            r['position']=i+1
        a,rows=m.make_capture(RACE,FORM,m.now_utc(),m.now_utc(),scorer_other)
        b,rows2=m.make_capture(RACE,with_result,m.now_utc(),m.now_utc(),scorer_other)
        self.assertEqual([(x['horse'],x['model_score'],x['model_rank']) for x in rows],
                         [(x['horse'],x['model_score'],x['model_rank']) for x in rows2])

    def test_installer_is_idempotent_and_preserves_score(self):
        src="""import v061_prospective
import streamlit as st

def score_runner(x):
    return x['score']

with t5:
    v061_prospective.render(secret)
"""
        new=installer.updated(src)
        self.assertIn('import v061b_historical',new)
        self.assertIn('v061b_historical.render(secret, score_runner)',new)
        self.assertEqual(new,installer.updated(new))
        self.assertEqual(installer.score_source(src),installer.score_source(new))

    def test_sql_append_only_and_atomic(self):
        sql=Path(__file__).with_name('migrate_v061b.sql').read_text()
        self.assertIn('v061b_capture_historical',sql)
        self.assertIn('SECURITY INVOKER',sql)
        self.assertIn('ENABLE ROW LEVEL SECURITY',sql)
        self.assertIn('BEFORE UPDATE OR DELETE',sql)
        self.assertIn('GRANT EXECUTE ON FUNCTION',sql)
        self.assertNotIn('DROP TABLE',sql)

if __name__=='__main__':
    unittest.main(verbosity=2)
