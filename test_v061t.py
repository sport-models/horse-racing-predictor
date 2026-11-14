import unittest
from copy import deepcopy
from install_v061t import install
import v061t_missing_data_ui as m
import v061s_feature_audit_ui as s
import pandas as pd


def runner(name,form='12345',career=10,track=2,distance=None,td=None):
    stats={'overall':{'starts':career,'wins':3},'track':{'starts':track,'wins':1}}
    if distance is not None:stats['distance']={'starts':distance,'wins':0}
    if td is not None:stats['trackDistance']={'starts':td,'wins':0}
    return {'name':name,'form':form,'stats':stats,'barrier':4,'weight':56}


class Tests(unittest.TestCase):
    def test_score_parity_and_explanation(self):
        a=runner('A');b=runner('B',form='43211')
        fa=s.score_breakdown(a)['score'];fb=s.score_breakdown(b)['score']
        tab=pd.DataFrame([{'Rank':1,'Horse':'A','Form Score':fa},{'Rank':2,'Horse':'B','Form Score':fb}])
        rows=m.details_for_rankings({'runners':[a,b]},tab)
        self.assertEqual([x['status'] for x in rows],['VERIFIED','VERIFIED'])
        why=m.explanation_for_runner(rows[0],rows)
        self.assertIn('Ranked #1',why)
        self.assertIn('score',why.lower())
        self.assertIn('weighted',why)
    def test_unscored_reason(self):
        r=runner('Debut',form='',career=0)
        frame=pd.DataFrame([{'Rank':1,'Horse':'Debut','Form Score':None}])
        rows=m.details_for_rankings({'data':{'runners':[r]}},frame)
        self.assertEqual(rows[0]['status'],'UNSCORED')
        self.assertIn('No v0.2 form score',m.explanation_for_runner(rows[0],rows))

    def test_parity_fail_closed(self):
        a=runner('A');frame=pd.DataFrame([{'Rank':1,'Horse':'A','Form Score':0}])
        rows=m.details_for_rankings({'runners':[a]},frame)
        self.assertEqual(rows[0]['status'],'SCORE_PARITY_FAILED')
        self.assertIsNone(rows[0]['breakdown'])

    def test_duplicate_names_fail_closed(self):
        a=runner('A');score=s.score_breakdown(a)['score']
        frame=pd.DataFrame([{'Rank':1,'Horse':'A','Form Score':score}])
        self.assertEqual(m.details_for_rankings({'runners':[a,a]},frame)[0]['status'],'UNVERIFIED_SOURCE')

    def test_missing_separate_from_zero(self):
        r=runner('A')
        self.assertEqual(m._stat(r,'distance')[2],'MISSING')
        r['stats']['distance']={'starts':0,'wins':0}
        self.assertEqual(m._stat(r,'distance')[2],'EXPLICIT_ZERO')

    def test_scratches_and_fallback(self):
        a=runner('A'); b=runner('B',form='')
        a_score=s.score_breakdown(a)['score']
        rows=[{'batch_id':'1','horse':'A','model_score':a_score,'feature_payload':a,'scratched_in_historical_response':False},
              {'batch_id':'1','horse':'B','model_score':None,'feature_payload':b,'scratched_in_historical_response':False},
              {'batch_id':'1','horse':'C','model_score':None,'feature_payload':{'name':'C','scratched':True},'scratched_in_historical_response':True}]
        reports=m.build_missing_data_reports(rows,[{'id':'1','race_date':'2026-11-01'}])
        summary=reports['summary'].iloc[0]
        self.assertEqual(summary['score_parity_failures'],0)
        self.assertEqual(summary['unscored_non_scratched'],1)
        reasons=dict(zip(reports['unscored_reasons']['unscored_reason'],reports['unscored_reasons']['runner_rows']))
        self.assertEqual(reasons['SCRATCHED'],1)
        self.assertEqual(reasons['RECENT_FORM_MISSING'],1)
        self.assertEqual(reports['fallback_exposure'].iloc[0]['career_fallback_components'],2)
        self.assertFalse(reports['summary'].iloc[0]['opposition_rating_ready'])

    def test_no_mutation(self):
        r=runner('A');rows=[{'batch_id':'1','horse':'A','model_score':s.score_breakdown(r)['score'],'feature_payload':r}]
        initial=deepcopy(rows);m.build_missing_data_reports(rows,[{'id':'1','race_date':'2026-11-01'}]);self.assertEqual(initial,rows)

    def test_inconsistent_stats(self):
        r=runner('A');r['stats']['track']['wins']=20
        x=m.build_missing_data_reports([{'batch_id':'1','horse':'A','model_score':None,'feature_payload':r}],[{'id':'1'}])
        self.assertEqual(x['invalid_stat_records'].iloc[0]['metric'],'track')

    def test_installer_idempotence(self):
        src=("import v061s_feature_audit_ui\n"
          "    st.subheader(\"📊 Full race rankings & bookmaker odds\")\n"
          "    st.dataframe(\n        display_df,\n        hide_index=True,\n        use_container_width=True\n    )\n\n"
          "    v061s_feature_audit_ui.render_analysis_explanations(analysis['race_data'], df)\n"
          "    with st.expander(\"Compare all bookmakers\"):\n        pass\n"
          "    v061s_feature_audit_ui.render_feature_audit(secret)\n")
        # Installer only alters view and research panel anchors.
        once=install(src)
        self.assertEqual(once,install(once))
        self.assertIn('render_ranking_table(display_df',once)
        self.assertNotIn('render_analysis_explanations(analysis',once)
        self.assertIn('render_data_audit(secret)',once)

    def test_installer_unknown_layout_blocks(self):
        with self.assertRaises(RuntimeError):install('def score_runner(x):\n    return x\n')

    def test_no_score_model_changers(self):
        import inspect
        source=inspect.getsource(m)
        self.assertNotIn('table(\'race_predictions\').update',source)
        self.assertNotIn('table(\'bets\').update',source)

if __name__=='__main__':unittest.main()
