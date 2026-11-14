"""Offline regression suite includes real Streamlit render stub and archival timing checks."""
from datetime import datetime,timedelta,timezone
from types import SimpleNamespace
from unittest import TestCase,main,mock
import sys
import pandas as pd
import v061u_full_field as timing

T=datetime(2026,11,14,1,0,tzinfo=timezone.utc)

def mock_batch(*,same_day=True):
    predictions=[{'horse':'A','model_rank':1,'form_score':50.0},
                 {'horse':'B','model_rank':2,'form_score':None}]
    payload={'model_version':'v0.2','race_date':'2026-11-14','track':'Sandown',
             'race_number':2,'original_prediction_batch_id':'BATCH',
             'form_response_raw':{'runners':[]},
             'rankings':[{'horse':'A','rank':1,'score':50.0},{'horse':'B','rank':2,'score':None}],
             'source_times':{'form_response_received_utc':(T-timedelta(minutes=5)).isoformat(),
                             'ranking_generated_utc':(T-timedelta(minutes=4)).isoformat()}}
    payload['form_response_sha256']=timing.digest(payload['form_response_raw'])
    archive={'batch_id':'BATCH','race_date':'2026-11-14','track':'Sandown',
             'race_number':2,'model_version':'v0.2',
             'created_at':(T-timedelta(minutes=3)).isoformat(),
             'scheduled_start_utc':(T+timedelta(minutes=10)).isoformat(),
             'payload':payload,'payload_sha256':timing.digest(payload)}
    return predictions,archive

class ProofTests(TestCase):
    def test_same_day_prior_to_scheduled_start_is_included(self):
        predictions,a=mock_batch()
        self.assertEqual(timing.proof_for_batch('BATCH',predictions,timing.make_archive_index([a])),
                         'BEFORE_SCHEDULE_ARCHIVED_NOT_ACTUAL_OFF_CERTIFIED')
    def test_after_scheduled_start_is_excluded(self):
        p,a=mock_batch();a['created_at']=(T+timedelta(minutes=11)).isoformat()
        self.assertEqual(timing.proof_for_batch('BATCH',p,timing.make_archive_index([a])),'TIMING_UNVERIFIED')
    def test_hash_tamper_fails(self):
        p,a=mock_batch();a['payload']['rankings'][0]['score']=90
        self.assertEqual(timing.proof_for_batch('BATCH',p,timing.make_archive_index([a])),'ARCHIVE_HASH_MISMATCH')
    def test_mismatching_rank_fails(self):
        p,a=mock_batch();p[0]['model_rank']=2
        self.assertEqual(timing.proof_for_batch('BATCH',p,timing.make_archive_index([a])),'RANK_MISMATCH')
    def test_missing_unscored_horse_prohibits(self):
        p,a=mock_batch();p.pop()
        self.assertEqual(timing.proof_for_batch('BATCH',p,timing.make_archive_index([a])),'RANKING_COUNT_MISMATCH')
    def test_no_legacy_relabel(self):
        p,a=mock_batch();self.assertEqual(timing.proof_for_batch('BATCH',p,{}),'NO_UNIQUE_ARCHIVE')
    def test_duplicate_archives_excluded(self):
        p,a=mock_batch();self.assertEqual(timing.proof_for_batch('BATCH',p,timing.make_archive_index([a,a])),'NO_UNIQUE_ARCHIVE')
    def test_timing_nan_fails(self):
        p,a=mock_batch();a['created_at']='bad'
        self.assertEqual(timing.proof_for_batch('BATCH',p,timing.make_archive_index([a])),'TIMING_UNVERIFIED')

class TableTests(TestCase):
    @classmethod
    def setUpClass(cls):
        # The new UI uses v061t's explanation helpers; make a lightweight
        # module stub to exercise the real rendering function in isolation.
        cls.stubs={name:sys.modules.get(name) for name in ('v061t_missing_data_ui','v061s_feature_audit_ui')}
        sys.modules['v061t_missing_data_ui']=SimpleNamespace(details_for_rankings=lambda f,source:[
            {'rank':1,'horse':'Horse A','score':52.5,'status':'UNSCORED','breakdown':None}],
            explanation_for_runner=lambda row,allrows:'Verified source explanation')
        sys.modules['v061s_feature_audit_ui']=SimpleNamespace(WEIGHTS={'Recent form':.35})
        import v061u_analysis_ui
        cls.ui=v061u_analysis_ui
    @classmethod
    def tearDownClass(cls):
        for name,old in cls.stubs.items():
            if old is None:sys.modules.pop(name,None)
            else:sys.modules[name]=old
        sys.modules.pop('v061u_analysis_ui',None)
    def frame(self):
        return pd.DataFrame([{'Rank':1,'Horse':'Horse A','Form Score':52.5,'Sportsbet Win':2.5,
          'Sportsbet Place':1.5,'Best Win':2.6,'Best Win Book':'tab','Best Place':1.7,
          'Best Place Book':'tab'}])
    def test_highlight_top3_and_others(self):
        f=self.frame()
        self.assertIn('#e5f5ea',self.ui.row_highlight(f.iloc[0])[0])
        f.loc[0,'Rank']=2;self.assertIn('#e9f2ff',self.ui.row_highlight(f.iloc[0])[0])
        f.loc[0,'Rank']=3;self.assertIn('#fff7dc',self.ui.row_highlight(f.iloc[0])[0])
        f.loc[0,'Rank']=4;self.assertEqual(self.ui.row_highlight(f.iloc[0])[0],'')
    def test_sorted_complete_rank_required(self):
        f=pd.concat([self.frame(),self.frame()],ignore_index=True)
        with self.assertRaises(ValueError):self.ui.formatted_rankings(f)
    def test_original_bookmakers_preserved(self):
        f=self.ui.formatted_rankings(self.frame())
        self.assertEqual(f.at[0,'Best Win Book'],'tab')
    def test_streamlit_renders_selectable_table(self):
        fake=SimpleNamespace(caption=mock.Mock(),markdown=mock.Mock(),info=mock.Mock(),
          dataframe=mock.Mock(return_value=SimpleNamespace(selection=SimpleNamespace(rows=[0]))))
        sys.modules['streamlit']=fake
        try:
            self.ui.render(self.frame(),{},('2026-11-14','sandown',2))
            self.assertEqual(fake.dataframe.call_count,1)
            self.assertEqual(fake.dataframe.call_args.kwargs['selection_mode'],'single-row')
            self.assertEqual(fake.dataframe.call_args.kwargs['on_select'],'rerun')
        finally:sys.modules.pop('streamlit',None)

if __name__=='__main__':main()
