"""Offline v0.6.1d regression tests: joint winners / dead heats.

No live FormFav or Supabase calls. Uses the existing v061b test fixtures.
"""
import copy
import math
import unittest
from pathlib import Path

import v061b_historical as m
import test_v061b as baseline
import install_v061d as installer


class DeadHeatTests(unittest.TestCase):
    def setup_result(self, positions, form=None, result=None):
        db = baseline.FakeDB()
        form = copy.deepcopy(form or baseline.FORM)
        result = copy.deepcopy(result or baseline.RESULTS)
        for i, p in enumerate(positions):
            result['placings'][i]['position'] = p
        m.capture_one(db, baseline.RACE, 'TEST', baseline.scorer_fixture,
                      requester=lambda *_args, **_kwargs: baseline.Response(form))
        race = db.rows[m.RACES][0]
        preview = m.fetch_results_preview(race, 'TEST',
                     requester=lambda *_args, **_kwargs: baseline.Response(result))
        m.persist_results(db, preview)
        report = m.build_reports(db.rows[m.RACES], db.rows[m.FEATURES], db.rows[m.RESULTS])
        return db, preview, report

    def test_standard_one_winner_unchanged(self):
        _, preview, report = self.setup_result([1, 2, 3, None])
        self.assertEqual(preview['winners'], 1)
        self.assertFalse(preview['dead_heat_first'])
        r=report['races'].iloc[0]
        self.assertTrue(bool(r['top1_won']))
        self.assertAlmostEqual(r['model_win_sp_profit_10'], 25)
        self.assertEqual(r['winner_count'], 1)
        self.assertAlmostEqual(r['random_top1_expectation'],1/3)

    def test_two_first_place_winners_import_and_backtest(self):
        db, preview, report = self.setup_result([1, 1, 3, None])
        self.assertEqual(preview['winners'], 2)
        self.assertTrue(preview['dead_heat_first'])
        self.assertEqual(len(db.rows[m.RESULTS]), 1)
        self.assertEqual(len(db.rows[m.FEATURES]), 4)
        self.assertEqual(report['summary'].iloc[0]['first_place_dead_heats_imported'],1)
        self.assertEqual(report['summary'].iloc[0]['first_place_dead_heats_evaluable'],1)
        row=report['races'].iloc[0]
        self.assertEqual(row['status'],'EVALUABLE_RETROSPECTIVE_RESEARCH')
        self.assertEqual(row['winning_horses'],'Alpha | Beta')
        self.assertEqual(row['winner_count'],2)
        self.assertEqual(row['winner_rank'],1)
        self.assertEqual(row['winner_ranks'],'1,2')
        self.assertTrue(bool(row['top1_won']))
        self.assertTrue(bool(row['winner_in_top3']))
        self.assertTrue(bool(row['market_sp_favourite_won']))
        self.assertAlmostEqual(row['random_top1_expectation'],2/3)
        self.assertAlmostEqual(row['random_top3_expectation'],1)
        self.assertAlmostEqual(row['model_win_sp_profit_10'],7.50)
        self.assertAlmostEqual(report['summary'].iloc[0]['sp_research_net_profit_10'],7.50)
        self.assertIn('PRO_RATA_DEAD_HEAT',report['summary'].iloc[0]['sp_return_basis'])

    def test_two_winners_but_top_ranked_loses(self):
        _, _, report = self.setup_result([3, 1, 1, None])
        row=report['races'].iloc[0]
        self.assertEqual(row['winner_rank'],2)
        self.assertEqual(row['winner_ranks'],'2,3')
        self.assertFalse(bool(row['top1_won']))
        self.assertTrue(bool(row['winner_in_top3']))
        self.assertAlmostEqual(row['model_win_sp_profit_10'],-10)
        self.assertEqual(report['summary'].iloc[0]['top1_wins'],0)
        self.assertEqual(report['summary'].iloc[0]['winner_in_top3'],1)

    def test_three_first_place_winners_works(self):
        _, preview, report = self.setup_result([1, 1, 1, None])
        self.assertEqual(preview['winners'],3)
        self.assertTrue(preview['dead_heat_first'])
        row=report['races'].iloc[0]
        self.assertEqual(row['winner_ranks'],'1,2,3')
        self.assertAlmostEqual(row['model_win_sp_profit_10'],35/3-10)

    def test_five_starters_two_winners_probability_baseline(self):
        form=copy.deepcopy(baseline.FORM)
        result=copy.deepcopy(baseline.RESULTS)
        extra=[{'name':'Epsilon','number':5,'fixture_score':50,'form':'321'},
               {'name':'Zeta','number':6,'fixture_score':40,'form':'312'}]
        form['runners'].extend(extra)
        result['placings'].extend([
            {'name':'Epsilon','position':4,'scratched':False,'startingPrice':11},
            {'name':'Zeta','position':5,'scratched':False,'startingPrice':15}])
        result['numberOfRunners']=6
        _, preview, report=self.setup_result([1,1,3,None,4,5],form=form,result=result)
        self.assertEqual(preview['starters'],5)
        row=report['races'].iloc[0]
        self.assertAlmostEqual(row['random_top1_expectation'],2/5)
        self.assertAlmostEqual(row['random_top3_expectation'],1-math.comb(3,3)/math.comb(5,3))

    def test_rejects_no_first_place_or_nonfinal(self):
        no_winner=copy.deepcopy(baseline.RESULTS)
        no_winner['placings'][0]['position']=2
        with self.assertRaisesRegex(ValueError,'no first-place winner'):
            m.parse_final_results(no_winner, baseline.RACE)
        not_final=copy.deepcopy(baseline.RESULTS)
        not_final['resultStatus']='pending'
        with self.assertRaises(ValueError):
            m.parse_final_results(not_final, baseline.RACE)

    def test_new_install_is_idempotent(self):
        current=Path('v061b_historical.py').read_text(encoding='utf-8')
        self.assertEqual(installer.update_historical(current),current)
        tests=Path('test_v061b.py').read_text(encoding='utf-8')
        self.assertEqual(installer.update_tests(tests),tests)


if __name__=='__main__':
    unittest.main(verbosity=2)
