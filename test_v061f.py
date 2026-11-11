"""Offline regression tests for v0.6.1f read-only historical track dashboard.

Tests the current v0.6.1d dead-heat-aware research report outputs and the
v0.6.1f installer. No live API, Supabase, GitHub or betting-ledger calls.
"""
import copy
from datetime import datetime, timezone
from pathlib import Path
import unittest

import pandas as pd

import v061b_historical as historical
import v061f_track_dashboard as f
import install_v061f as installer
import test_v061b as fixtures


def make_example_reports():
    races=[]
    features=[]
    results=[]
    cases=[
        ('Flemington','2026-11-01',1,[1,2,3,None],False,False),
        ('Flemington','2026-11-02',2,[2,1,3,None],False,False),
        ('Sandown Hillside','2026-11-03',3,[1,1,3,None],False,False),
        ('Sandown Lakeside','2026-11-04',4,[3,1,1,None],False,False),
        ('Geelong','2026-11-05',5,None,True,False),
        ('Caulfield','2026-11-06',6,[None,1,3,None],False,True),
    ]
    for idx,(track,day,num,places,missing_result,top1_scratched) in enumerate(cases):
        race={**fixtures.RACE,'track':track,'track_key':historical.track_key(track),
              'track_slug':track.lower().replace(' ','-'),
              'race_date':day,'race_number':num}
        form=copy.deepcopy(fixtures.FORM)
        form.update({'track':track,'date':day,'raceNumber':num})
        stamp=datetime(2026,11,9,tzinfo=timezone.utc)
        batch,feat=historical.make_capture(race,form,stamp,stamp,fixtures.scorer_fixture)
        races.append(batch)
        features.extend([dict(x,batch_id=batch['id']) for x in feat])
        if missing_result:
            continue
        payload=copy.deepcopy(fixtures.RESULTS)
        payload.update({'track':track,'date':day,'raceNumber':num})
        for row,place in zip(payload['placings'],places):
            row['position']=place
        if top1_scratched:
            payload['placings'][0]['scratched']=True
        results.append({'batch_id':batch['id'],'result_payload':payload,
                        'result_sha256':historical.digest(payload)})
    return historical.build_reports(races,features,results)


class TrackDashboardTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.reports=make_example_reports()

    def full(self,**kwargs):
        return f.build_track_dashboard(self.reports,**kwargs)

    def test_archived_graded_wl_excludes_missing_results_and_scratches(self):
        result=self.full()
        df=result['track_performance'].set_index('Track')
        overall=df.loc[f.TOTAL]
        self.assertEqual(int(overall['Archived races']),6)
        self.assertEqual(int(overall['Graded races']),4)
        self.assertEqual(int(overall['Ungraded races']),2)
        self.assertEqual(int(overall['W']),2)
        self.assertEqual(int(overall['L']),2)
        self.assertEqual(int(df.loc['Caulfield','Graded races']),0)
        self.assertEqual(int(df.loc['Geelong','Graded races']),0)
        self.assertTrue(pd.isna(df.loc['Geelong','SP ROI %']))

    def test_profit_roi_and_sp_coverage(self):
        result=self.full()['track_performance'].set_index('Track')
        overall=result.loc[f.TOTAL]
        self.assertEqual(int(overall['SP-priced races']),4)
        self.assertEqual(int(overall['SP stake $']),40)
        self.assertAlmostEqual(overall['SP net P/L $'],12.50)
        self.assertAlmostEqual(overall['SP ROI %'],31.25)
        self.assertAlmostEqual(result.loc['Sandown','SP net P/L $'],-2.50)
        self.assertAlmostEqual(result.loc['Flemington','SP net P/L $'],15.00)
        self.assertAlmostEqual(result.loc['Flemington','Win rate %'],50)
        self.assertEqual(result.loc[f.TOTAL,'Sample'],'Low (<30)')

    def test_dead_heat_rank_hits_not_double_counted_as_races(self):
        result=self.full()['top3_by_track'].set_index('Track')
        total=result.loc[f.TOTAL]
        self.assertEqual(int(total['Rank 1 winners']),2)
        self.assertEqual(int(total['Rank 2 winners']),3)
        self.assertEqual(int(total['Rank 3 winners']),1)
        self.assertEqual(int(total['Rank 1-3 winning horses']),6)
        self.assertEqual(int(total['Top 3 winning races']),4)
        self.assertEqual(int(total['Top 3 misses']),0)
        self.assertAlmostEqual(total['Top 3 hit %'],100)
        sand=result.loc['Sandown']
        self.assertEqual(int(sand['Rank 1 winners']),1)
        self.assertEqual(int(sand['Rank 2 winners']),2)
        self.assertEqual(int(sand['Rank 3 winners']),1)
        self.assertEqual(int(sand['Top 3 winning races']),2)

    def test_track_grouping_and_variants(self):
        grouped=self.full()['track_performance']['Track'].to_list()
        self.assertIn('Sandown',grouped)
        self.assertNotIn('Sandown Hillside',grouped)
        distinct=self.full(group_variants=False)['track_performance']['Track'].to_list()
        self.assertIn('Sandown Hillside',distinct)
        self.assertIn('Sandown Lakeside',distinct)
        self.assertNotIn('Sandown',distinct)
        self.assertEqual(f.venue_name('Ballarat Synthetic'),'Ballarat')
        self.assertEqual(f.venue_name('Pakenham Synthetic'),'Pakenham')
        self.assertEqual(f.venue_name('Geelong Synthetic'),'Geelong')
        self.assertEqual(f.venue_name('Seymore'),'Seymour')
        self.assertEqual(f.venue_name('Seymour'),'Seymour')
        self.assertEqual(f.venue_name('Sandown Lakeside'),'Sandown')

    def test_date_and_track_filters(self):
        only=self.full(selected_tracks=['Sandown'])
        total=only['track_performance'].set_index('Track').loc[f.TOTAL]
        self.assertEqual(int(total['Archived races']),2)
        self.assertEqual(int(total['Graded races']),2)
        day=self.full(start_date='2026-11-04',end_date='2026-11-05')
        total2=day['track_performance'].set_index('Track').loc[f.TOTAL]
        self.assertEqual(int(total2['Archived races']),2)
        self.assertEqual(int(total2['Graded races']),1)

    def test_sp_missing_does_not_get_assumed_a_loss_or_enter_roi(self):
        rep=dict(self.reports)
        rep['races']=rep['races'].copy()
        idx=rep['races'].index[rep['races']['track']=='Sandown Lakeside'][0]
        rep['races'].loc[idx,'model_selection_sp']=None
        rep['races'].loc[idx,'model_win_sp_profit_10']=None
        total=f.build_track_dashboard(rep)['track_performance'].set_index('Track').loc[f.TOTAL]
        self.assertEqual(int(total['Graded races']),4)
        self.assertEqual(int(total['L']),2)
        self.assertEqual(int(total['SP-priced races']),3)
        self.assertAlmostEqual(total['SP coverage %'],75)
        self.assertAlmostEqual(total['SP net P/L $'],22.5)
        self.assertAlmostEqual(total['SP ROI %'],75.0)

    def test_race_audit_and_coverage_preserve_missing_result(self):
        output=self.full()
        details=output['race_details']
        self.assertEqual(len(details),6)
        geelong=details.loc[details['Track']=='Geelong'].iloc[0]
        self.assertEqual(geelong['status'],'NO_IMPORTED_RESULTS')
        self.assertTrue(pd.isna(geelong['Top 3 hit']))
        coverage=output['coverage']
        self.assertTrue(((coverage['Track']=='Geelong') & (coverage['Status']=='NO_IMPORTED_RESULTS')).any())

    def test_missing_runner_outcomes_rejected_instead_of_fake_zero(self):
        rep=dict(self.reports)
        rep['runner_outcomes']=pd.DataFrame()
        with self.assertRaisesRegex(ValueError,'Missing runner-outcomes'):
            f.build_track_dashboard(rep)

    def test_mismatch_between_top1_and_rank1_rejected(self):
        rep=dict(self.reports)
        rep['races']=rep['races'].copy()
        ix=rep['races'].index[rep['races']['track']=='Flemington'][0]
        rep['races'].loc[ix,'top1_won']=False
        with self.assertRaisesRegex(ValueError,'Rank-1 results disagree'):
            f.build_track_dashboard(rep)

    def test_install_marker_and_idempotence(self):
        src=Path('v061b_historical.py').read_text()
        self.assertEqual(installer.updated(src),src)
        self.assertIn(installer.ADDED,src)
        self.assertIn('def parse_final_results',src)
        self.assertIn('V061D_DEAD_HEAT_MULTIPLE_WINNERS',src)

    def test_empty_reports_do_not_throw(self):
        self.assertTrue(f.build_track_dashboard({'races':pd.DataFrame()})['track_performance'].empty)
        self.assertTrue(f.build_track_dashboard(self.reports,selected_tracks=['Unknown Track'])['track_performance'].empty)

    def test_intervals_and_no_invented_precision(self):
        low,hi=f._wilson_interval(2,4)
        self.assertTrue(low<50<hi)
        self.assertEqual(f._wilson_interval(0,0),(None,None))


if __name__=='__main__':
    unittest.main(verbosity=2)
