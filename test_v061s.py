import unittest
from unittest.mock import patch
import pandas as pd
from v061s_feature_audit_ui import score_breakdown, explanation_rows, build_audit, opponent_schema
from install_v061s import install


def horse():
    return {'name':'Example','form':'12543','stats':{
        'overall':{'starts':10,'wins':3},'track':{'starts':2,'wins':1},
        'distance':{'starts':4,'wins':1},'trackDistance':{'starts':0,'wins':0}},
        'barrier':3,'weight':55}

class Tests(unittest.TestCase):
    def test_breakdown_components_sum(self):
        b=score_breakdown(horse());self.assertEqual(round(sum(b['contributions'].values()),2),b['score'])
    def test_career_zero_unscored(self):
        h=horse();h['stats']['overall']['starts']=0;self.assertIsNone(score_breakdown(h))
    def test_recent_missing_unscored(self):
        h=horse();h['form']='';self.assertIsNone(score_breakdown(h))
    def test_display_parity(self):
        h=horse();score=score_breakdown(h)['score'];d=pd.DataFrame([{'Rank':1,'Horse':'Example','Form Score':score}]);self.assertEqual(explanation_rows({'runners':[h]},d)[0]['Explanation status'],'VERIFIED')
    def test_display_mismatch_fails_closed(self):
        h=horse();d=pd.DataFrame([{'Rank':1,'Horse':'Example','Form Score':999}]);self.assertEqual(explanation_rows({'runners':[h]},d)[0]['Explanation status'],'SCORE_MISMATCH')
    def test_opponent_no_invention(self):self.assertEqual(opponent_schema(horse()),[])
    def test_opponent_candidate(self):self.assertTrue(opponent_schema({'pastPerformances':[{'raceId':'x'}]}))
    def test_missing_vs_zero(self):
        h=horse();h['stats']['distance']={};h['stats']['track']={'starts':0,'wins':0}
        b=build_audit([{'batch_id':'1','horse':'Example','model_score':1,'feature_payload':h}],[{'id':'1'}])
        d=b['runner_coverage'].iloc[0];self.assertFalse(bool(d['distance_starts_present']));self.assertTrue(bool(d['track_zero_starts_explicit']))
    def test_invalid_counts(self):
        h=horse();h['stats']['overall']={'starts':3,'wins':5}
        b=build_audit([{'batch_id':'1','horse':'Example','model_score':1,'feature_payload':h}],[{'id':'1'}]);self.assertEqual(len(b['stat_inconsistencies']),1)
    def test_installer_idempotence_and_safety(self):
        original='''import v061q_stayer_validation

def score_runner(runner):
    return 999

def analysis_tab():
    st.subheader('📊 Full Race Rankings')
    st.dataframe(df, hide_index=True, use_container_width=True)
    st.subheader("💰 Sportsbet & Best Available Odds")
    with st.expander("Compare all bookmakers"):
        pass
    st.subheader('⭐ Top Five Model Selections')
    st.dataframe(df.head(5), hide_index=True, use_container_width=True)

def validation_tab():
    v061q_stayer_validation.render(secret)
'''
        first=install(original);self.assertEqual(first,install(first));self.assertIn('def score_runner(runner):\n    return 999',first)
        self.assertEqual(first.count('render_analysis_explanations'),1);self.assertEqual(first.count('render_feature_audit'),1)
        self.assertNotIn("st.dataframe(df.head(5)",first)
    def test_installer_rejects_unknown(self):
        with self.assertRaises(RuntimeError):install('def score_runner(r): return 1')

if __name__=='__main__':unittest.main()
