"""v0.6.2B: read-only venue/distance/barrier exploratory audit.

Never estimates barrier advantage from wins alone: each row is an actual starter,
not an archived nonstarter. No backfill, no adjustment to frozen v0.2.
"""
from __future__ import annotations
import math
import re
from collections import defaultdict
import pandas as pd

MIN_STARTERS = 100
MIN_RACE_DATES = 15
DISTANCE_KEYS = ('distance','distanceMeters','distanceMetres','raceDistance','distance_m','distanceM')
RAIL_KEYS = ('railPosition','rail','rail_position')
GOING_KEYS = ('trackCondition','track_condition','going')


def _scopes(raw):
    if not isinstance(raw, dict):
        return []
    return [raw] + [raw[k] for k in ('race','raceInfo','raceDetails','event','meetingRace')
                    if isinstance(raw.get(k), dict)]


def _unique_field(raw, keys, parse):
    found = set()
    for scope in _scopes(raw):
        for key in keys:
            if key in scope:
                value = parse(scope[key])
                if value is not None:
                    found.add(value)
    return next(iter(found)) if len(found) == 1 else None


def _distance(value):
    if isinstance(value, dict):
        value = value.get('meters', value.get('metres', value.get('value')))
    if isinstance(value, str):
        import re
        match = re.fullmatch(r'\s*(\d{3,4})(?:\s*(?:m|metres|meters))?\s*', value, re.I)
        if not match:
            return None
        value = match.group(1)
    try:
        n = float(value)
        return int(n) if math.isfinite(n) and n.is_integer() and 600 <= n <= 5000 else None
    except (TypeError, ValueError, OverflowError):
        return None


def _label(value):
    if isinstance(value, (dict, list)) or value is None:
        return None
    s = str(value).strip()
    return s.upper()[:80] if s else None


def _barrier(value):
    if isinstance(value, bool):
        return None
    try:
        n = float(value)
        return int(n) if math.isfinite(n) and n.is_integer() and 1 <= n <= 30 else None
    except (TypeError, ValueError, OverflowError):
        return None


def build(archives, results):
    from v061_prospective import evaluate_archives, _result_index, race_key, canonical, _locked_runner_metadata, digest
    report = evaluate_archives(archives, results)
    eligible = {str(r.batch_id): r for r in report['races'].itertuples(index=False)
                if r.status == 'EVALUABLE_PROVISIONAL'}
    # Two-way dead heats remain legitimate in model performance but cannot
    # enter this single-winner barrier hypothesis, whose baseline assumes
    # exactly one winner per race. Retain an explicit exclusion reason.
    dead_heats = {str(r.batch_id) for r in report['races'].itertuples(index=False)
                  if getattr(r, 'joint_first_count', None) is not None
                  and getattr(r, 'joint_first_count', 0) > 1}
    indexed = _result_index(results)
    rows, issues = [], []
    issues.extend({'batch_id':bid,
                   'issue':'EXCLUDED_JOINT_FIRST_FROM_SINGLE_WINNER_BARRIER_RESEARCH'}
                  for bid in sorted(dead_heats))
    seen = set()
    for archive in archives:
        bid = str(archive.get('batch_id'))
        if bid not in eligible or bid in dead_heats:
            continue
        if bid in seen:
            issues.append({'batch_id':bid,'issue':'DUPLICATE_BATCH'})
            continue
        seen.add(bid)
        p = archive.get('payload') or {}
        if digest(p) != archive.get('payload_sha256'):
            issues.append({'batch_id':bid,'issue':'HASH_MISMATCH'})
            continue
        form = p.get('form_response_raw')
        raw_runners, ambiguous = _locked_runner_metadata(form)
        distance = _unique_field(form, DISTANCE_KEYS, _distance)
        rail = _unique_field(form, RAIL_KEYS, _label)
        going = _unique_field(form, GOING_KEYS, _label)
        ranked = {canonical(x.get('horse')): x for x in p.get('rankings', [])
                  if isinstance(x, dict)}
        finishers = indexed.get(race_key(archive), [])
        active = [x for x in finishers if x.get('scratched') is False]
        if not active or len(active) != int(eligible[bid].field_size):
            issues.append({'batch_id':bid,'issue':'STARTER_COUNT_MISMATCH'})
            continue
        for result in active:
            horse = canonical(result.get('horse_key') or result.get('horse'))
            locked = ranked.get(horse)
            raw = raw_runners.get(horse) if horse not in ambiguous else None
            if not locked:
                issues.append({'batch_id':bid,'issue':'MISSING_LOCKED_RUNNER'})
                continue
            barrier = _barrier(locked.get('barrier', (raw or {}).get('barrier')))
            if barrier is None:
                issues.append({'batch_id':bid,'issue':'BARRIER_UNKNOWN_OR_INVALID'})
                continue
            rows.append({
                'batch_id':bid,'race_date':archive.get('race_date'),
                'track':archive.get('track'),'race_number':archive.get('race_number'),
                'horse':locked.get('horse'),'barrier':barrier,'distance_m':distance,
                'rail':rail,'track_condition':going,'actual_starters':len(active),
                'locked_rank':locked.get('rank'),
                'locked_score':locked.get('score'),
                'locked_scoring_status':locked.get('scoring_status'),
                'won':int(result.get('finishing_position') == 1),
                'uniform_expected_win':1/len(active),
                'evidence_status':'SCHEDULE_ONLY_NOT_CERTIFIED',
            })
    detail = pd.DataFrame(rows)
    if detail.empty:
        return detail, pd.DataFrame(), pd.DataFrame(issues)
    # A complete starter barrier exposure is essential: exclude any race with
    # missing or duplicate barriers, rather than biasing denominators.
    counts = detail.groupby('batch_id').agg(observed=('horse','size'),
                                             expected=('actual_starters','first'),
                                             unique_barriers=('barrier','nunique'))
    valid = counts.index[(counts.observed == counts.expected) &
                         (counts.unique_barriers == counts.expected)]
    for bid in counts.index.difference(valid):
        issues.append({'batch_id':bid,'issue':'INCOMPLETE_OR_DUPLICATE_BARRIER_EXPOSURE'})
    detail = detail[detail.batch_id.isin(valid)].copy()
    if detail.empty:
        return detail, pd.DataFrame(), pd.DataFrame(issues)
    # Distance is exact, not arbitrarily pooled. Missing conditions are visible
    # in detail, never assumed to mean a standard rail/going.
    summary = (detail.groupby(['track','distance_m','barrier'],dropna=False)
               .agg(starters=('won','size'),wins=('won','sum'),
                    expected_wins_uniform=('uniform_expected_win','sum'),
                    race_dates=('race_date','nunique'),
                    distinct_races=('batch_id','nunique')).reset_index())
    summary['win_rate'] = summary.wins / summary.starters
    summary['excess_wins_vs_uniform'] = summary.wins - summary.expected_wins_uniform
    summary['gate'] = summary.apply(
        lambda r: 'EXPLORATORY_ONLY_NO_QUALITY_ADJUSTMENT' if
        r.starters >= MIN_STARTERS and r.race_dates >= MIN_RACE_DATES
        else 'INSUFFICIENT_EXPOSURE', axis=1)
    return detail, summary, pd.DataFrame(issues, columns=['batch_id','issue'])


def render(secret):
    import streamlit as st
    from v061_prospective import archive_client, _read_all, TABLE
    st.subheader('🔬 v0.6.2B — Track, distance & barrier bias (standalone)')
    st.caption('Read-only actual-starter exposure by venue, exact distance and barrier. '
               'Uniform field-size expectation is a descriptive reference, NOT a '
               'horse-quality-adjusted effect. Rail and track condition are captured '
               'when present in original locked provider data.')
    if not st.button('Audit archived barrier exposure', key='v062b_run'):
        return
    try:
        client = archive_client(secret)
        detail, summary, issues = build(
            _read_all(client,TABLE,order='created_at'),
            _read_all(client,'runner_results'))
    except Exception as exc:
        st.error('Read-only barrier audit failed: '+str(exc))
        return
    st.metric('Complete-field starter observations',len(detail))
    if detail.empty:
        st.info('No eligible complete-field barrier outcomes yet.')
    else:
        st.dataframe(summary,hide_index=True,use_container_width=True)
        st.caption('No barrier recommendation or production adjustment is generated. '
                   'Horse-quality-adjusted modelling requires separately frozen, '
                   'forward-validated estimates and sufficient data.')
    if not issues.empty:
        st.dataframe(issues,hide_index=True,use_container_width=True)
    for name,frame in [('starters',detail),('summary',summary),('issues',issues)]:
        st.download_button('Export barrier '+name,frame.to_csv(index=False).encode(),
                           file_name='v062b_'+name+'.csv',mime='text/csv',key='v062b_'+name)
    st.warning('Promotion blocked: uncertified actual-off and input-as-of timing; '
               'no validated horse-quality adjustment or independent holdout.')
