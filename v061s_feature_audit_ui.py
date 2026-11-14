"""v0.6.1s: read-only historical feature coverage and factual v0.2 explanations.

No prediction/scoring/settlement/database writes. The live scorer remains app.score_runner.
"""
from __future__ import annotations
import json
import math
import re
from collections import Counter
import pandas as pd

VERSION = 'v0.6.1s'
STAT_GROUPS = ('overall', 'track', 'distance', 'trackDistance')
WEIGHTS = {'Recent form': .35, 'Career': .15, 'Track': .15,
           'Distance': .15, 'Track & distance': .10, 'Barrier & weight': .10}


def _num(v):
    try:
        if isinstance(v, bool): return None
        x = float(v)
        return x if math.isfinite(x) else None
    except (ValueError, TypeError, OverflowError): return None


def _rate(rec):
    """Mirrors frozen v0.2 win_rate, including intentional defaults."""
    if not isinstance(rec, dict): return 0., 0
    starts = max(0, int(_num(rec.get('starts')) or 0))
    wins = max(0., _num(rec.get('wins')) or 0.)
    return (min(wins / starts, 1.), starts) if starts else (0., 0)


def _recent(form):
    digits = re.findall(r'[0-9]', str(form or ''))[-5:]
    if not digits: return None
    points = {'1': 1., '2': .7, '3': .5, '4': .3, '5': .2}
    return sum(i * points.get(d, 0.) for i, d in enumerate(digits, 1)) / sum(range(1, len(digits) + 1))


def score_breakdown(runner):
    """Independent explanation replay of exact v0.2 formula; fails closed on parity mismatch."""
    stats = runner.get('stats') or {}
    if not isinstance(stats, dict): stats = {}
    career, ncareer = _rate(stats.get('overall'))
    track, nt = _rate(stats.get('track'))
    distance, nd = _rate(stats.get('distance'))
    td, ntd = _rate(stats.get('trackDistance'))
    recent = _recent(runner.get('form'))
    if recent is None or ncareer == 0: return None
    smooth = lambda rate, starts: (starts * rate + 5 * career) / (starts + 5)
    barrier = _num(runner.get('barrier'))
    weight = _num(runner.get('weight'))
    barrier = barrier if barrier is not None else 8.
    weight = weight if weight is not None else 56.
    context = (max(0., min(1., 1 - (barrier - 1) / 16)) +
               max(0., min(1., 1 - (weight - 48) / 16))) / 2
    factors = {'Recent form': recent, 'Career': career, 'Track': smooth(track, nt),
               'Distance': smooth(distance, nd), 'Track & distance': smooth(td, ntd),
               'Barrier & weight': context}
    contributions = {k: 100 * v * WEIGHTS[k] for k, v in factors.items()}
    score = round(sum(contributions.values()), 2)
    return {'score': score, 'factors': factors, 'contributions': contributions,
            'starts': {'Career': ncareer, 'Track': nt, 'Distance': nd, 'Track & distance': ntd},
            'note': 'Track/distance scores use v0.2 career-rate smoothing; zeros or missing source stats are not independently certified as actual zero starts.'}


def _runner_name(runner):
    return str(runner.get('name') or runner.get('runnerName') or runner.get('horseName') or '').strip()


def _norm(s): return re.sub(r'[^a-z0-9]', '', str(s or '').casefold())


def explanation_rows(race_data, rankings):
    """Explain all ranked runners, with fail-closed agreement against displayed scores."""
    raw = race_data.get('runners') if isinstance(race_data, dict) else None
    if not isinstance(raw, list):
        data = race_data.get('data') if isinstance(race_data, dict) else None
        raw = data.get('runners') if isinstance(data, dict) else []
    by_name = {_norm(_runner_name(r)): r for r in raw if isinstance(r, dict) and _runner_name(r)}
    result = []
    for r in rankings.to_dict('records'):
        horse = str(r['Horse'])
        raw_runner = by_name.get(_norm(horse))
        stored = _num(r['Form Score'])
        breakdown = score_breakdown(raw_runner) if raw_runner else None
        status = 'UNSCORED' if stored is None else 'INPUT_UNAVAILABLE' if not breakdown else 'VERIFIED' if abs(breakdown['score'] - stored) <= .011 else 'SCORE_MISMATCH'
        contribution = breakdown['contributions'] if status == 'VERIFIED' else {}
        strengths = sorted(contribution, key=contribution.get, reverse=True)
        result.append({'Rank': r['Rank'], 'Horse': horse, 'Score': stored,
                       'Explanation status': status,
                       'Largest weighted factor': strengths[0] if strengths else None,
                       'Second factor': strengths[1] if len(strengths)>1 else None,
                       'Replayed score': breakdown['score'] if breakdown else None,
                       'Contributions': contribution,
                       'breakdown': breakdown})
    return result


def render_analysis_explanations(race_data, rankings):
    import streamlit as st
    rows = explanation_rows(race_data, rankings)
    if not rows: return
    lead = rows[0]
    st.subheader('Why the model selected these horses')
    if lead['Explanation status'] == 'VERIFIED':
        next_row = rows[1] if len(rows)>1 else None
        gap = lead['Score'] - next_row['Score'] if next_row and next_row['Score'] is not None else None
        text = (f"**{lead['Horse']}** ranks first with a v0.2 score of **{lead['Score']:.2f}**. "
                f"Its largest weighted contributions are **{lead['Largest weighted factor']}** and "
                f"**{lead['Second factor']}**.")
        if gap is not None: text += f' It leads the second-ranked runner by **{gap:.2f} score points**.'
        st.info(text)
    else:
        st.info('Rank 1 explanation unavailable: original source statistics could not be safely reconciled with the displayed score.')
    with st.expander('Explain each runner — actual v0.2 factor contributions'):
        for row in rows:
            st.markdown(f"**#{row['Rank']} {row['Horse']}** — {row['Explanation status']}")
            if row['Explanation status'] == 'VERIFIED':
                detail = pd.DataFrame([{'Factor': k, 'Fixed weight': f'{WEIGHTS[k]:.0%}',
                                        'Component': round(row['breakdown']['factors'][k], 4),
                                        'Score contribution': round(v, 4)}
                                       for k,v in row['Contributions'].items()])
                st.dataframe(detail, hide_index=True, use_container_width=True)
                st.caption(row['breakdown']['note'])
            elif row['Explanation status']=='UNSCORED':
                st.caption('No v0.2 score: insufficient recent-form or career-start evidence. No replacement score is invented.')
            else:
                st.warning('Explanation hidden to avoid asserting unverified factor contributions.')
    st.caption('Explanations describe the frozen score, not probabilities, bookmaker value, or causal drivers of winning.')


def _history_rows_from_source(raw):
    """Generic examination of archived runner's original FormFav payload."""
    return raw if isinstance(raw, dict) else {}


def opponent_schema(raw):
    """Find explicit race-level past-performances structures, not form digits/aggregate starts."""
    found=[]
    def visit(obj, prefix='', depth=0):
        if depth>4: return
        if isinstance(obj, dict):
            for k,v in obj.items():
                low=str(k).casefold()
                if any(word in low for word in ('pastperformance','past_performance','formhistory','form_history','previousrace','previous_race','racehistory','race_history','pastresults','past_results')):
                    found.append({'path':(prefix+'.'+str(k)).strip('.'), 'value_type':type(v).__name__,
                                  'nonempty':bool(v)})
                if k not in ('stats','jockeyStats','trainerStats'):
                    visit(v, (prefix+'.'+str(k)).strip('.'),depth+1)
        elif isinstance(obj, list) and depth<3:
            for o in obj[:3]: visit(o,prefix+'[]',depth+1)
    visit(raw)
    return found


def build_audit(feature_rows, batch_rows):
    """Read-only batch+runner integrity summaries. No outcome joins or model changes."""
    if not isinstance(feature_rows,list) or not isinstance(batch_rows,list): raise ValueError('Expected historical archived lists')
    batch_ids={str(b.get('id')) for b in batch_rows}
    metrics=[]; opponents=[]; inconsistencies=[]
    by_batch=Counter()
    for f in feature_rows:
        bid=str(f.get('batch_id'))
        by_batch[bid]+=1
        raw=_history_rows_from_source(f.get('feature_payload'))
        stats=raw.get('stats') if isinstance(raw.get('stats'),dict) else {}
        row={'batch_id':bid,'horse':f.get('horse'),'scored':f.get('model_score') is not None,
             'has_recent_form':_recent(raw.get('form')) is not None}
        for name,key in (('career','overall'),('track','track'),('distance','distance'),('track_distance','trackDistance')):
            grp=stats.get(key) if isinstance(stats.get(key),dict) else {}
            s=_num(grp.get('starts'));w=_num(grp.get('wins'));p=_num(grp.get('places'))
            row[f'{name}_starts_present']=s is not None
            row[f'{name}_wins_present']=w is not None
            row[f'{name}_both_present']=s is not None and w is not None
            row[f'{name}_zero_starts_explicit']=s==0
            if any(v is not None and (v<0 or v!=int(v)) for v in (s,w,p)) or (s is not None and w is not None and w>s):
                inconsistencies.append({'batch_id':bid,'horse':f.get('horse'),'factor':name,'issue':'INVALID_COUNTS'})
        for kind in ('jockeyStats','trainerStats'):
            row[kind+'_present']=isinstance(raw.get(kind),dict) and bool(raw[kind])
        row['barrier_present']=_num(raw.get('barrier')) is not None
        row['weight_present']=_num(raw.get('weight')) is not None
        paths=opponent_schema(raw)
        row['opponent_candidate_present']=any(x['nonempty'] for x in paths)
        for item in paths: opponents.append({'batch_id':bid,'horse':f.get('horse'),**item})
        metrics.append(row)
    d=pd.DataFrame(metrics)
    if d.empty: raise ValueError('No archived runner features found')
    columns=[c for c in d if c not in ('batch_id','horse')]
    coverage=pd.DataFrame([{'metric':c,'runners':len(d),'available':int(d[c].sum()),'percent':round(100*d[c].mean(),2)} for c in columns])
    completeness=d.groupby('batch_id',sort=False).agg(runners=('horse','size'),scored=('scored','sum'),
         complete_career=('career_both_present','all'),complete_track=('track_both_present','all'),
         complete_distance=('distance_both_present','all'),complete_track_distance=('track_distance_both_present','all'),
         any_opponent_candidate=('opponent_candidate_present','any')).reset_index()
    completeness['missing_archive_parent']=~completeness.batch_id.isin(batch_ids)
    summary=pd.DataFrame([{'version':VERSION,'historical_batches':len(batch_rows),'runner_rows':len(d),
         'races_all_four_groups_complete':int((completeness[['complete_career','complete_track','complete_distance','complete_track_distance']].all(axis=1)).sum()),
         'opponent_candidate_runners':int(d.opponent_candidate_present.sum()),
         'opponent_candidate_races':int(completeness.any_opponent_candidate.sum()),
         'opponent_rating_ready':False,
         'opponent_note':'Schema/path presence is not proof of historical opponent identities, dated race links, or point-in-time opponent ratings.',
         'research_only':True,'production_changed':False}])
    return {'summary':summary,'field_coverage':coverage,'race_completeness':completeness,
            'runner_coverage':d,'stat_inconsistencies':pd.DataFrame(inconsistencies,columns=['batch_id','horse','factor','issue']),
            'opponent_source_candidates':pd.DataFrame(opponents,columns=['batch_id','horse','path','value_type','nonempty'])}


def render_feature_audit(secret):
    import streamlit as st
    import v061b_historical as hist
    st.subheader('🧪 v0.6.1s — Feature completeness & opposition-data feasibility')
    st.caption('Research-only audit of archived original FormFav runner payloads. No model refit, source refresh or database writes.')
    if st.button('Run v0.6.1s feature coverage audit',key='v061s_run'):
        try:
            client=hist.server_db(secret)
            batches=hist.read_all(client,hist.RACES,'id,race_date,track,race_number,runner_count','race_date')
            features=hist.read_all(client,hist.FEATURES,'batch_id,horse,model_score,feature_payload')
            st.session_state['v061s_reports']=build_audit(features,batches)
        except Exception as exc:
            st.error('Feature audit failed; no data changed: '+str(exc))
            return
    reports=st.session_state.get('v061s_reports')
    if not reports: return
    for name,df in reports.items():
        with st.expander(name.replace('_',' ').title(),expanded=name in ('summary','field_coverage')):
            st.dataframe(df,hide_index=True,use_container_width=True)
            st.download_button('Export '+name,df.to_csv(index=False).encode(),
                               file_name=f'v061s_{name}.csv',mime='text/csv',key='v061s_dl_'+name)
    st.warning('Opposition strength is NOT implemented as a predictor. Candidate archive paths require verification of dated opponents and pre-race availability before challenger testing.')
