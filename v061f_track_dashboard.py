"""v0.6.1f | Read-only historical track performance dashboard.

Uses reports already produced by v061b_historical.build_reports; NEVER reads or
modifies the betting ledger, frozen v0.2 scorer or Supabase tables. Only graded
historical research races enter W/L, Top-3 and starting-price ROI calculations.
"""
from __future__ import annotations

from datetime import date
from math import sqrt
import re
import unicodedata

import pandas as pd

VERSION = 'v0.6.1f'
EVALUABLE = 'EVALUABLE_RETROSPECTIVE_RESEARCH'
TOTAL = 'ALL SELECTED TRACKS'


def _key(value):
    v = unicodedata.normalize('NFKD', str(value or '')).encode('ascii', 'ignore').decode().casefold()
    return re.sub(r'[^a-z0-9]+', '', v)


def venue_name(raw_track):
    """Merge recognized venue variants only; preserve anything unfamiliar as-is."""
    t = str(raw_track or '').strip()
    if not t:
        return 'Unknown'
    k = _key(t)
    if k.startswith('mooneevalley'):
        return 'Moonee Valley'
    known = {'flemington':'Flemington', 'caulfield':'Caulfield',
             'sandown':'Sandown','geelong':'Geelong','ballarat':'Ballarat',
             'seymour':'Seymour','seymore':'Seymour','pakenham':'Pakenham'}
    for key, label in known.items():
        if k.startswith(key):
            return label
    return t


def _bool_win(x):
    return x is True or (isinstance(x, (int, float)) and x == 1) or str(x).lower() == 'true'


def _wilson_interval(successes, attempts, z=1.959963984540054):
    """Approximate binomial 95% Wilson interval, for descriptive context only."""
    if not attempts:
        return None, None
    p = successes / attempts
    d = 1 + z * z / attempts
    center = (p + z * z / (2 * attempts)) / d
    offset = z * sqrt(p * (1-p) / attempts + z*z / (4*attempts*attempts)) / d
    return max(0.0, (center - offset) * 100), min(100.0, (center + offset) * 100)


def _safe_date(value):
    try:
        return date.fromisoformat(str(value)[:10])
    except (TypeError, ValueError):
        return None


def _sum_numeric(series):
    return float(pd.to_numeric(series, errors='coerce').fillna(0).sum())


def build_track_dashboard(reports, *, start_date=None, end_date=None,
                          selected_tracks=None, group_variants=True):
    """Compute per-track reports without touching any persisted source data.

    Args:
        reports: dict returned from v061b_historical.build_reports.
        selected_tracks: None for all, otherwise selected display track names.
        group_variants: True combines Sandown/Hillside/Lakeside etc.

    Returns dict with DataFrames: track_performance, top3_by_track,
    race_details, winner_details, coverage. The summary ALWAYS excludes all
    races except status == EVALUABLE_RETROSPECTIVE_RESEARCH from W/L and ROI.
    """
    raw_races = reports.get('races') if reports else None
    raw_runner = reports.get('runner_outcomes') if reports else None
    if raw_races is None or raw_races.empty:
        return {k: pd.DataFrame() for k in
                ('track_performance','top3_by_track','race_details','winner_details','coverage')}

    races = raw_races.copy()
    required = {'batch_id','race_date','track','race_number','status','top1_won',
                'model_selection_sp','model_win_sp_profit_10'}
    missing = required.difference(races.columns)
    if missing:
        raise ValueError('Historical race report missing fields: ' + ', '.join(sorted(missing)))
    if races['batch_id'].astype(str).duplicated().any():
        raise ValueError('Historical races report contains duplicate batch IDs')
    races['batch_id'] = races['batch_id'].astype(str)
    races['race_date'] = races['race_date'].astype(str).str[:10]
    races['venue'] = races['track'].map(venue_name)
    races['Track'] = races['venue'] if group_variants else races['track'].astype(str).str.strip()
    races['Track'] = races['Track'].where(races['Track'].ne(''), 'Unknown')
    races['_date'] = races['race_date'].map(_safe_date)
    if start_date is not None:
        sd = _safe_date(start_date)
        if sd is None:
            raise ValueError('Invalid start date')
        races = races.loc[races['_date'].notna() & (races['_date'] >= sd)]
    if end_date is not None:
        ed = _safe_date(end_date)
        if ed is None:
            raise ValueError('Invalid end date')
        races = races.loc[races['_date'].notna() & (races['_date'] <= ed)]
    if selected_tracks is not None:
        races = races.loc[races['Track'].isin(set(selected_tracks))]
    races = races.copy()
    if races.empty:
        return {k: pd.DataFrame() for k in
                ('track_performance','top3_by_track','race_details','winner_details','coverage')}
    graded = races.loc[races['status'] == EVALUABLE].copy()
    if not graded.empty:
        if graded['top1_won'].isna().any():
            raise ValueError('Evaluable race missing rank-1 win/loss flag')
        graded['top1_won'] = graded['top1_won'].map(_bool_win)
    graded_ids = set(graded['batch_id'])

    # Count winner ranks from *runner* outcomes. In a first-place dead heat,
    # rank 1 and rank 2 may BOTH have won the same race. Top-3 hit counts that
    # race once, rather than adding each winner as an extra race.
    if graded_ids and (raw_runner is None or raw_runner.empty):
        raise ValueError('Missing runner-outcomes report; rerun the historical research backtest')
    runners = raw_runner.copy() if raw_runner is not None else pd.DataFrame()
    winners = pd.DataFrame(columns=['batch_id','model_rank','horse','Track','race_date','win'])
    if graded_ids:
        runner_fields = {'batch_id','model_rank','win','horse'}
        lack = runner_fields.difference(runners.columns)
        if lack:
            raise ValueError('Runner outcomes missing fields: ' + ', '.join(sorted(lack)))
        runners['batch_id'] = runners['batch_id'].astype(str)
        runners = runners.loc[runners['batch_id'].isin(graded_ids)].copy()
        runners['model_rank'] = pd.to_numeric(runners['model_rank'], errors='coerce')
        runners['win'] = runners['win'].map(_bool_win)
        # Race results with no winners in runner outcome are incomplete; do
        # not silently report zero wins for top 3.
        if not runners.groupby('batch_id')['win'].any().reindex(sorted(graded_ids), fill_value=False).all():
            raise ValueError('At least one graded race has no first-place winner in runner outcomes')
        winners = runners.loc[runners['win']].copy()
        # Runner reports already carry race_date; retain the canonical race
        # metadata from the graded race table, avoiding merge suffixes.
        winners = winners.drop(columns=['race_date','Track'],errors='ignore')
        winners = winners.merge(graded[['batch_id','Track','race_date']],on='batch_id',how='left',validate='many_to_one')
        ranked = winners.loc[winners['model_rank'].isin([1,2,3])]
        # Unique winning ranks within each race: do not double-count malformed
        # duplicate horse rows when tallying rank achievements.
        flags = ranked[['batch_id','model_rank']].drop_duplicates()
        rank_hits = {1:set(flags.loc[flags['model_rank']==1,'batch_id']),
                     2:set(flags.loc[flags['model_rank']==2,'batch_id']),
                     3:set(flags.loc[flags['model_rank']==3,'batch_id'])}
        top3_ids = set().union(*rank_hits.values())
    else:
        rank_hits = {1:set(),2:set(),3:set()}
        top3_ids = set()

    result_rows=[];top3_rows=[];coverage_rows=[]
    def build_one(label, all_races, scored):
        n_all, n = len(all_races), len(scored)
        wins = int(scored['top1_won'].sum()) if n else 0
        losses = n-wins
        scored_ids = set(scored['batch_id'])
        r1=sum(bid in rank_hits[1] for bid in scored_ids)
        r2=sum(bid in rank_hits[2] for bid in scored_ids)
        r3=sum(bid in rank_hits[3] for bid in scored_ids)
        top3=sum(bid in top3_ids for bid in scored_ids)
        # Under the frozen scorer, each rank is unique and rank1 wins must
        # equal top1 W/L. Surface inconsistencies rather than hiding them.
        if r1 != wins:
            raise ValueError(f'Rank-1 results disagree with historical W/L for {label}: {r1} vs {wins}')
        sp_odds = pd.to_numeric(scored['model_selection_sp'],errors='coerce') if n else pd.Series(dtype=float)
        profits = pd.to_numeric(scored['model_win_sp_profit_10'],errors='coerce') if n else pd.Series(dtype=float)
        priced = (sp_odds > 1) & profits.notna()
        priced_n = int(priced.sum())
        stake = 10 * priced_n
        profit = float(profits.loc[priced].sum()) if priced_n else None
        roi = 100 * profit / stake if stake else None
        wlo, whi = _wilson_interval(wins,n)
        tlo, thi = _wilson_interval(top3,n)
        sample = 'Low (<30)' if n < 30 else ('Developing (30-99)' if n < 100 else '100+ races')
        result_rows.append({
            'Track':label,'Archived races':n_all,'Graded races':n,
            'Ungraded races':n_all-n,'W':wins,'L':losses,
            'Win rate %':100*wins/n if n else None,'95% win low %':wlo,
            '95% win high %':whi,'SP-priced races':priced_n,
            'SP coverage %':100*priced_n/n if n else None,
            'SP stake $':stake,'SP net P/L $':profit,'SP ROI %':roi,
            'Dead-heat races':int(scored['dead_heat_first'].map(_bool_win).sum())
                               if n and 'dead_heat_first' in scored else None,
            'Sample':sample,
        })
        top3_rows.append({
            'Track':label,'Graded races':n,'Rank 1 winners':r1,
            'Rank 2 winners':r2,'Rank 3 winners':r3,
            'Top 3 winning races':top3,'Top 3 hit %':100*top3/n if n else None,
            '95% top3 low %':tlo,'95% top3 high %':thi,
            'Top 3 misses':n-top3,
            'Rank 1-3 winning horses':r1+r2+r3,
            'Random top3 expected hits':_sum_numeric(scored['random_top3_expectation'])
                                         if n and 'random_top3_expectation' in scored else None,
            'Sample':sample,
        })
        counts = all_races['status'].value_counts()
        for status,count in counts.items():
            coverage_rows.append({'Track':label,'Status':str(status),'Races':int(count)})

    for label, group in races.groupby('Track',sort=True):
        scored = graded.loc[graded['Track']==label]
        build_one(str(label),group,scored)
    build_one(TOTAL,races,graded)

    # Race-level CSV preserves raw racetrack, grouping, source status and
    # which first-place ranks were present. No rows are removed from archive.
    details = races.drop(columns=['_date']).copy()
    details['Top 3 hit'] = details['batch_id'].isin(top3_ids).where(details['status']==EVALUABLE,other=pd.NA)
    details['Winning rank 1'] = details['batch_id'].isin(rank_hits[1]).where(details['status']==EVALUABLE,other=pd.NA)
    details['Winning rank 2'] = details['batch_id'].isin(rank_hits[2]).where(details['status']==EVALUABLE,other=pd.NA)
    details['Winning rank 3'] = details['batch_id'].isin(rank_hits[3]).where(details['status']==EVALUABLE,other=pd.NA)
    if not winners.empty:
        winners = winners.sort_values(['race_date','Track','batch_id','model_rank'],na_position='last')
    return {'track_performance':pd.DataFrame(result_rows),
            'top3_by_track':pd.DataFrame(top3_rows),
            'race_details':details,
            'winner_details':winners,
            'coverage':pd.DataFrame(coverage_rows)}


def render(reports):
    """Streamlit UI called inside historical research dashboard tab."""
    import streamlit as st
    st.subheader('🏟️ v0.6.1f — Performance by Track')
    st.caption('Frozen v0.2 historical research only • $10 hypothetical win bet at official SP • '
               'NOT settled Bet Tracker ROI. Excluded, missing-result, scratched-top-1 and '
               'incomplete races do not count as losses.')
    raw = reports.get('races') if reports else None
    if raw is None or raw.empty:
        st.info('Archive races, import official results and run the historical research backtest first.')
        return
    frame = raw.copy()
    frame['race_date'] = frame['race_date'].astype(str).str[:10]
    date_values = sorted(v for v in (frame['race_date'].map(_safe_date)) if v is not None)
    if not date_values:
        st.warning('Historical report contains no valid race dates.')
        return
    grouped = st.toggle('Group track variants under the same venue (e.g. Sandown Hillside/Lakeside)',
                        value=True,key='v061f_group_variants')
    track_names = sorted(set(frame['track'].map(venue_name) if grouped else frame['track'].astype(str)))
    col1,col2=st.columns(2)
    from_day = col1.date_input('From date',value=date_values[0],min_value=date_values[0],max_value=date_values[-1],key='v061f_from')
    to_day = col2.date_input('To date',value=date_values[-1],min_value=date_values[0],max_value=date_values[-1],key='v061f_to')
    chosen = st.multiselect('Tracks (leave empty for all)',track_names,default=[],key='v061f_track_filter')
    if from_day>to_day:
        st.warning('From date must be before or equal to To date.')
        return
    try:
        data=build_track_dashboard(reports,start_date=from_day,end_date=to_day,
                                   selected_tracks=chosen or None,group_variants=grouped)
    except ValueError as exc:
        st.error('Track dashboard cannot calculate reliably: '+str(exc))
        return
    by_track=data['track_performance']
    if by_track.empty:
        st.info('No archived races match these filters.')
        return
    detail=by_track.loc[by_track['Track']==TOTAL].iloc[0]
    top=data['top3_by_track'].loc[lambda d:d['Track']==TOTAL].iloc[0]
    box=st.columns(5)
    box[0].metric('Graded races',f"{int(detail['Graded races']):,}")
    box[1].metric('Rank 1 W / L',f"{int(detail['W'])} / {int(detail['L'])}")
    box[2].metric('Rank 1 win rate',f"{detail['Win rate %']:.1f}%" if pd.notna(detail['Win rate %']) else '—')
    box[3].metric('Historical SP ROI',f"{detail['SP ROI %']:+.1f}%" if pd.notna(detail['SP ROI %']) else '—')
    box[4].metric('Top 3 winner found',f"{top['Top 3 hit %']:.1f}%" if pd.notna(top['Top 3 hit %']) else '—')
    st.markdown('**Rank 1: W/L and historical starting-price ROI by track**')
    view=by_track.loc[by_track['Track']!=TOTAL].sort_values(['Graded races','Track'],ascending=[False,True])
    st.dataframe(view.style.format({
        'Win rate %':'{:.1f}','95% win low %':'{:.1f}','95% win high %':'{:.1f}',
        'SP coverage %':'{:.1f}','SP stake $':'${:,.2f}',
        'SP net P/L $':'${:,.2f}','SP ROI %':'{:+.1f}'},na_rep='—'),
        hide_index=True,use_container_width=True)
    if len(view):
        st.bar_chart(view.set_index('Track')[['Win rate %']],y_label='Rank 1 win rate (%)')
    st.caption('ROI = sum($10 × SP dead-heat-adjusted return − $10) / total $10 stakes, '
               'for races with a valid historical selection SP only. Missing SP means ROI unavailable '
               'for that race. SP coverage is shown; compare sample sizes before interpreting differences.')
    st.markdown('**Which of our top three rankings contained the winner?**')
    top_view=data['top3_by_track'].loc[lambda d:d['Track']!=TOTAL].sort_values(
        ['Graded races','Track'],ascending=[False,True])
    st.dataframe(top_view.style.format({
        'Top 3 hit %':'{:.1f}','95% top3 low %':'{:.1f}','95% top3 high %':'{:.1f}',
        'Random top3 expected hits':'{:.1f}'},na_rep='—'),
        hide_index=True,use_container_width=True)
    if len(top_view):
        chart=top_view.set_index('Track')[['Top 3 hit %']]
        st.bar_chart(chart,y_label='Top 3 winning-race rate (%)')
    st.caption('Rank 1 / 2 / 3 winner counts are winning horses at each rank. '
               'Top 3 winning races counts a race once, even if two or more runners dead-heat for first. '
               'The Wilson 95% intervals are descriptive, not proof of a track advantage.')
    if (by_track['Graded races']<30).any():
        st.info('Some track samples contain fewer than 30 graded races. Treat their rates and ROI as exploratory.')
    with st.expander('Race-by-race audit, exclusions and downloads',expanded=False):
        st.dataframe(data['coverage'],hide_index=True,use_container_width=True)
        st.dataframe(data['race_details'],hide_index=True,use_container_width=True)
        for name,label in [('track_performance','W_L_and_ROI_by_track'),
                           ('top3_by_track','Top3_winners_by_track'),
                           ('race_details','Track_race_audit'),
                           ('winner_details','Individual_winning_runners'),
                           ('coverage','Track_result_coverage')]:
            table=data[name]
            st.download_button('Download '+label+' CSV',
                               data=table.to_csv(index=False).encode('utf-8'),
                               file_name='v061f_'+label+'.csv',mime='text/csv',
                               key='v061f_export_'+name)
