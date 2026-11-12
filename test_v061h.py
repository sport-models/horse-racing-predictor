"""Pure unit tests. python test_v061h.py; no database/network calls."""
import unittest,sys,types,math
from datetime import datetime,timezone
from pathlib import Path
# Existing production modules may not be installed in a standalone copy of package;
# in Codespaces they are real. Keep this test import local and dependency-light.
import re
# Minimal offline stand-ins for existing repository helpers. Live Codespaces imports
# the actual modules; this harness never calls external APIs or Streamlit.
if 'v042_results' not in sys.modules:
    m=types.ModuleType('v042_results')
    m.canonical=lambda x: re.sub('[^a-z0-9]','',str(x or '').lower())
    m.normalize_track=lambda x: re.sub(' +',' ',str(x or '').lower().strip())
    sys.modules['v042_results']=m
if 'v057f_odds_integrity' not in sys.modules:
    m=types.ModuleType('v057f_odds_integrity')
    def price(v):
        try:return float(v) if v is not None and float(v)>1 else None
        except (TypeError,ValueError):return None
    def places(v,field=None):
        try:return int(v) if v is not None and int(v)>0 and (field is None or int(v)<=field) else None
        except (TypeError,ValueError):return None
    m.bookmaker_id=lambda q: (q.get('key') or q.get('bookmaker'),None)
    m.valid_price=price
    m.valid_places=places
    m.iso_timestamp=lambda v: datetime.fromisoformat(str(v).replace('Z','+00:00')) if v else None
    m.preferred_key=lambda x: (0 if x=='sportsbet' else 1,x)
    sys.modules['v057f_odds_integrity']=m
import v061h_readiness as audit
import v061h_strategy as strat
import install_v061h as installer

class V061hTests(unittest.TestCase):
    def test_readiness_reconciliation(self):
        races=[dict(id='1',race_date='2026-11-01',track='Flemington',track_key='flemington',race_number=1,runner_count=1,source_form_payload={'trackCondition':'Soft 5','distance':1200}),
               dict(id='2',race_date='2026-11-01',track='Ascot',track_key='ascot',race_number=2,runner_count=1,source_form_payload={})]
        feats=[dict(batch_id='1',horse='A',model_rank=1,model_score=None,jockey='Joe')]
        reports=audit.summarize(races,feats,[dict(batch_id='1')],[dict(batch_id='2',reason='RESULT_UNAVAILABLE',note='404')])
        row=reports['readiness_summary'].iloc[0]
        self.assertEqual(row['without_results'],1)
        self.assertEqual(row['excluded_without_results'],1)
        self.assertEqual(row['unresolved_without_results'],0)
        self.assertEqual(row['runner_count_mismatch_races'],1)
        self.assertEqual(row['ranked_but_unscored'],1)
        self.assertEqual(reports['race_metadata_keys'].set_index('source_key').loc['trackCondition','races_with_value'],1)
    def test_rule_threshold(self):
        self.assertEqual(strat.bet_rule(5,1.99,None,1,'sportsbet')['bet_type'],'WIN_ONLY')
        self.assertEqual(strat.bet_rule(5,1.99,None,1,'sportsbet')['win_stake'],10)
        self.assertEqual(strat.bet_rule(5,2.0,3,1,'sportsbet')['bet_type'],'EACH_WAY')
        self.assertEqual(strat.bet_rule(5,2.0,3,1,'sportsbet')['place_stake'],5)
    def test_quotes_require_timestamp_book_terms(self):
        cutoff=datetime(2026,11,12,3,tzinfo=timezone.utc)
        q=[{'key':'sportsbet','bookmaker':'sportsbet','stale':False,'win':5,'place':2.1,'places_paid':3,'places_paid_source':'bookmaker_quote','last_update':'2026-11-12T02:00:00Z'}]
        self.assertEqual(strat.best_quote(q,cutoff,10)['places_paid'],3)
        self.assertIsNone(strat.best_quote([{**q[0],'last_update':None}],cutoff,10))
        self.assertIsNone(strat.best_quote([{**q[0],'stale':True}],cutoff,10))
        self.assertIsNone(strat.best_quote([{**q[0],'last_update':'2026-11-12T04:00:00Z'}],cutoff,10))
        self.assertIsNone(strat.best_quote([{**q[0],'place':None}],cutoff,10))
    def fixture_prospective_snapshot(self):
        arc={'batch_id':'b1','race_date':'2026-11-12','track':'Flemington','race_number':1,
             'created_at':'2026-11-12T02:30:00Z','payload':{'model_version':'v0.2',
             'scheduled_start_utc':'2026-11-12T04:00:00Z',
             'source_times':{'odds_response_received_utc':'2026-11-12T02:25:00Z'},
             'rankings':[{'horse':'Alpha','rank':1},{'horse':'Beta','rank':2}],
             'odds_quotes':{'Alpha':[{'key':'sportsbet','bookmaker':'sportsbet','stale':False,'win':5,'place':2,'places_paid':2,'places_paid_source':'bookmaker_quote','last_update':'2026-11-12T02:00:00Z'}],
                            'Beta':[{'key':'sportsbet','bookmaker':'sportsbet','stale':False,'win':6,'place':1.8,'last_update':'2026-11-12T02:00:00Z'}]}}}
        return arc
    def test_win_place_and_portfolios(self):
        arc=self.fixture_prospective_snapshot()
        tickets=strat.build_tickets([arc])
        self.assertEqual(len(tickets),2)
        self.assertEqual(tickets.iloc[0]['bet_type'],'EACH_WAY')
        self.assertEqual(tickets.iloc[1]['bet_type'],'WIN_ONLY')
        result=[dict(race_date='2026-11-12',track='Flemington',race_number=1,horse='Alpha',result_status='final',scratched=False,finishing_position=1),
                dict(race_date='2026-11-12',track='Flemington',race_number=1,horse='Beta',result_status='final',scratched=False,finishing_position=2)]
        settled=strat.grade(tickets,result)
        self.assertEqual(settled.iloc[0]['profit'],25) # 5*5 + 5*2 = 35 return, $10 stake
        self.assertEqual(settled.iloc[1]['profit'],-10)
        summary=strat.aggregate(settled)
        self.assertEqual(summary.iloc[0]['fully_evaluable_races'],1)
        self.assertEqual(summary.iloc[1]['fully_evaluable_races'],1)
        self.assertEqual(summary.iloc[1]['stake_$'],20)
        self.assertEqual(summary.iloc[1]['net_profit_$'],15)
        self.assertTrue(strat.matched_portfolios(settled).empty)
    def test_missing_terms_are_not_settled(self):
        arc=self.fixture_prospective_snapshot()
        arc['payload']['odds_quotes']['Alpha'][0]['places_paid']=None
        t=strat.build_tickets([arc])
        self.assertEqual(t.iloc[0]['status'],'MISSING_BOOKMAKER_PLACE_TERMS')
    def test_dead_heat_excluded(self):
        arc=self.fixture_prospective_snapshot()
        tickets=strat.build_tickets([arc])
        result=[dict(race_date='2026-11-12',track='Flemington',race_number=1,horse=h,result_status='final',scratched=False,finishing_position=1) for h in ('Alpha','Beta')]
        settled=strat.grade(tickets,result)
        self.assertTrue(all(settled['settlement_status']=='DEAD_HEAT_SETTLEMENT_UNVERIFIED'))
    def test_track_report_and_payload_hash(self):
        arc=self.fixture_prospective_snapshot()
        tickets=strat.build_tickets([arc])
        result=[dict(race_date='2026-11-12',track='Flemington',race_number=1,horse='Alpha',result_status='final',scratched=False,finishing_position=1),
                dict(race_date='2026-11-12',track='Flemington',race_number=1,horse='Beta',result_status='final',scratched=False,finishing_position=2)]
        summary=strat.aggregate_by_track(strat.grade(tickets,result))
        self.assertEqual(set(summary['Track']),{'Flemington'})
        arc['payload_sha256']='0'*64
        self.assertTrue(all(strat.build_tickets([arc])['status']=='ARCHIVE_HASH_MISMATCH'))
    def test_installer_score_preservation(self):
        src='import streamlit as st\ndef score_runner(runner):\n    return runner["score"]\nwith t5:\n    v060_validation_hub.render(db_rows)\n'
        new=installer.updated(src)
        self.assertEqual(new,installer.updated(new))
        self.assertEqual(installer.score_source(src),installer.score_source(new))
        self.assertIn('v061h_strategy.render(secret)',new)

if __name__=='__main__':unittest.main(verbosity=2)
