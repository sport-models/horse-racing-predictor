"""v0.6.1t — historical missing-data diagnostics and in-row v0.2 explanations.

Read-only research and presentation. No changes to scoring, locks or databases.
"""
from __future__ import annotations
import math
import re
from collections import Counter
import pandas as pd
import numpy as np
from v061s_feature_audit_ui import score_breakdown, WEIGHTS

VERSION = 'v0.6.1t'
METRICS = [('career','overall'),('track','track'),('distance','distance'),('track_distance','trackDistance')]


def number(value):
    try:
        if isinstance(value,bool): return None
        x=float(value)
        return x if math.isfinite(x) else None
    except (TypeError,ValueError,OverflowError):return None


def norm(name):
    return re.sub('[^a-z0-9]', '', str(name or '').casefold())


def source_runners(form):
    if isinstance(form,list):return form
    if not isinstance(form,dict):return []
    if isinstance(form.get('runners'),list):return form['runners']
    d=form.get('data')
    if isinstance(d,list):return d
    if isinstance(d,dict) and isinstance(d.get('runners'),list):return d['runners']
    return []


def details_for_rankings(form, frame):
    """Verify each explanation independently; detect ambiguous duplicate horse names."""
    runners=source_runners(form)
    names={};duplicates=set()
    for raw in runners:
        if not isinstance(raw,dict):continue
        key=norm(raw.get('name') or raw.get('runnerName') or raw.get('horseName'))
        if not key:continue
        if key in names:duplicates.add(key)
        names[key]=raw
    out=[]
    for d in frame.to_dict('records'):
        horse=str(d['Horse']);key=norm(horse);actual=number(d.get('Form Score'))
        source=names.get(key) if key not in duplicates else None
        factor=score_breakdown(source) if source else None
        status=('UNSCORED' if actual is None else 'UNVERIFIED_SOURCE' if factor is None
                else 'VERIFIED' if abs(factor['score']-actual)<=0.011 else 'SCORE_PARITY_FAILED')
        out.append({'rank':int(d['Rank']),'horse':horse,'score':actual,'status':status,
                    'breakdown':factor if status=='VERIFIED' else None})
    return out


def explanation_for_runner(row, allrows):
    """Plain-language, factual comparative explanation; no causal claims."""
    if row['status']=='UNSCORED':
        return 'No v0.2 form score is available. Its source data do not meet the frozen scorer’s minimum recent-form and career-start requirements; its position among unscored runners is not a confidence ranking.'
    if row['status']!='VERIFIED':
        return 'The original runner data could not be matched to the displayed v0.2 score. No factor explanation is shown until score parity is verified.'
    contributions=row['breakdown']['contributions']
    best=sorted(contributions,key=contributions.get,reverse=True)[:2]
    text=(f"Ranked #{row['rank']} with a frozen v0.2 score of {row['score']:.2f}. "
          f"Its largest weighted score contributions are {best[0]} ({contributions[best[0]]:.2f} points) "
          f"and {best[1]} ({contributions[best[1]]:.2f} points).")
    above=next((x for x in allrows if x['rank']==row['rank']-1),None)
    below=next((x for x in allrows if x['rank']==row['rank']+1),None)
    if above and above['score'] is not None:
        text+=f" It trails rank #{above['rank']} by {above['score']-row['score']:.2f} score points."
    elif below and below['score'] is not None:
        text+=f" It leads rank #{below['rank']} by {row['score']-below['score']:.2f} score points."
    compare=above if above and above['status']=='VERIFIED' else below if below and below['status']=='VERIFIED' else None
    if compare:
        other=compare['breakdown']['contributions']
        direction=1 if compare is below else -1
        differences={name:direction*(contributions[name]-other[name]) for name in WEIGHTS}
        strongest=max(differences,key=differences.get)
        if differences[strongest]>.005:
            if compare is below:
                text+=f" Its biggest score advantage over rank #{compare['rank']} is {strongest} (+{differences[strongest]:.2f} points)."
            else:
                text+=f" Its largest weighted-component shortfall versus rank #{compare['rank']} is {strongest} ({differences[strongest]:.2f} points)."
    text+=' These are weighted model inputs—not a win probability or a statement about bookmaker value.'
    return text


def _odds(x):
    n=number(x)
    return f'${n:.2f}' if n is not None and n>1 else '—'


def render_ranking_table(frame, form):
    """Row-level Why popovers: st.dataframe itself cannot embed expandable widgets."""
    import streamlit as st
    details=details_for_rankings(form,frame)
    if not len(details):
        st.info('No ranked runners to show.')
        return
    st.caption('Rankings are calculated without bookmaker odds. Choose **Why?** on any row for its verified v0.2 explanation.')
    widths=[.52,2.4,.77,1.05,1.05,1.27,1.27,.85]
    headers=['#','Horse','Score','Sportsbet win','Sportsbet place','Best win / book','Best place / book','Explain']
    cells=st.columns(widths,vertical_alignment='center')
    for col,label in zip(cells,headers): col.markdown('**'+label+'**')
    for d,record in zip(frame.to_dict('records'),details):
        col=st.columns(widths,vertical_alignment='center')
        col[0].write(str(record['Rank']))
        col[1].write(str(record['Horse']))
        col[2].write('—' if record['score'] is None else f"{record['score']:.2f}")
        col[3].write(_odds(record.get('Sportsbet Win')))
        col[4].write(_odds(record.get('Sportsbet Place')))
        win=_odds(record.get('Best Win'));win_book=record.get('Best Win Book')
        col[5].write(win+(f" · {win_book}" if win!='—' and pd.notna(win_book) and str(win_book).strip() else ''))
        place=_odds(record.get('Best Place'));place_book=record.get('Best Place Book')
        col[6].write(place+(f" · {place_book}" if place!='—' and pd.notna(place_book) and str(place_book).strip() else ''))
        with col[7].popover('Why?',use_container_width=True):
            st.markdown(f"**#{record['rank']} {record['horse']}**")
            st.write(explanation_for_runner(record,details))
            if record['status']=='VERIFIED':
                b=record['breakdown']
                factors=pd.DataFrame([{'Factor':f,'Weight':f'{WEIGHTS[f]:.0%}',
                                        'Raw component':round(b['factors'][f],4),
                                        'Weighted score points':round(b['contributions'][f],3)} for f in WEIGHTS])
                st.dataframe(factors,hide_index=True,use_container_width=True)
                st.caption(b['note'])
            else:
                st.caption('No score was fabricated or substituted.')
    st.caption('All additional bookmaker quotes and timestamp details remain available under **Compare all bookmakers** below.')


def _stat(raw,key):
    group=(raw.get('stats') or {}).get(key)
    if not isinstance(group,dict): group={}
    starts=number(group.get('starts'));wins=number(group.get('wins'))
    status=('MISSING' if starts is None or wins is None else 'EXPLICIT_ZERO' if starts==0
            else 'VALID' if starts>0 and wins>=0 and wins<=starts and starts.is_integer() and wins.is_integer()
            else 'INCONSISTENT')
    return starts,wins,status


def build_missing_data_reports(features,batches,race_outcomes=None):
    """No result joins and no scoring modifications. Report fallback mechanics precisely."""
    if not isinstance(features,list) or not isinstance(batches,list):
        raise ValueError('Expected existing archived runner and race lists')
    parent={str(b.get('id')):b for b in batches}
    out=[];invalid=[];candidate=[]
    for f in features:
        bid=str(f.get('batch_id'));raw=f.get('feature_payload') or {}
        if not isinstance(raw,dict):raw={}
        scores=score_breakdown(raw)
        archived=number(f.get('model_score'))
        statuses={};stats={}
        for name,key in METRICS:
            starts,wins,status=_stat(raw,key)
            statuses[name]=status;stats[name]=(starts,wins)
            if status=='INCONSISTENT': invalid.append({'batch_id':bid,'horse':f.get('horse'),'metric':name,'problem':'INCONSISTENT_STARTS_OR_WINS'})
        career_starts=stats['career'][0]
        form=raw.get('form')
        has_recent=bool(re.findall('[0-9]',str(form or '')))
        scratched=raw.get('scratched') is True or f.get('scratched_in_historical_response') is True
        if scratched:reason='SCRATCHED'
        elif archived is not None:reason='SCORED'
        elif not has_recent and (career_starts is None or career_starts==0):reason='NO_RECENT_AND_NO_CAREER'
        elif not has_recent:reason='RECENT_FORM_MISSING'
        elif career_starts is None:reason='CAREER_STARTS_MISSING'
        elif career_starts==0:reason='CAREER_STARTS_ZERO'
        else:reason='UNSCORED_WITH_POTENTIAL_INPUTS_CHECK'
        parity=('NOT_SCORED' if archived is None else 'PARITY_OK' if scores and abs(archived-scores['score'])<=.011 else 'PARITY_FAIL')
        starts=stats['career'][0]
        # Frozen score uses zero starts when the relevant group is omitted,
        # which makes its smoothed component equal career win rate.
        fallback=[n for n in ('track','distance','track_distance') if statuses[n] in ('MISSING','EXPLICIT_ZERO')]
        out.append({'batch_id':bid,'race_date':str(parent.get(bid,{}).get('race_date') or '')[:10],
                    'horse':f.get('horse'),'model_rank':f.get('model_rank'),
                    'scratched':scratched,'scored':archived is not None,
                    'score':archived,'score_parity':parity,'unscored_reason':reason,
                    'career_starts':starts,'recent_form_available':has_recent,
                    **{f'{n}_source':statuses[n] for n,_ in METRICS},
                    'career_fallback_components':len(fallback),
                    'track_fallback': 'track' in fallback,
                    'distance_fallback':'distance' in fallback,
                    'track_distance_fallback':'track_distance' in fallback,
                    'archive_parent_found':bid in parent})
        # Formal known schema paths only; do not infer or invent past meetings.
        for key in ('pastPerformances','past_performances','previousRaces','previous_races','raceHistory','race_history','formHistory','history'):
            if key in raw and raw[key]:
                candidate.append({'batch_id':bid,'horse':f.get('horse'),
                                  'candidate_field':key,'value_type':type(raw[key]).__name__,
                                  'validation':'NOT_VALIDATED_AS_DATED_OPPONENT_RECORDS'})
    df=pd.DataFrame(out)
    if df.empty:raise ValueError('No historical runner records available')
    scored=df[df.scored & ~df.scratched]
    diag=df.groupby('unscored_reason',dropna=False).agg(runner_rows=('horse','size'),
                    scored=('scored','sum')).reset_index()
    fallback=(scored.groupby('career_fallback_components')
              .agg(runners=('horse','size'),mean_score=('score','mean'),
                   mean_career_starts=('career_starts','mean')).reset_index())
    missing=(pd.DataFrame([{'statistic':name,'missing_runners':int((df[f'{name}_source']=='MISSING').sum()),
                            'explicit_zero_runners':int((df[f'{name}_source']=='EXPLICIT_ZERO').sum()),
                            'valid_runners':int((df[f'{name}_source']=='VALID').sum()),
                            'inconsistent_runners':int((df[f'{name}_source']=='INCONSISTENT').sum())}
                           for name,_ in METRICS]))
    summary=pd.DataFrame([{'version':VERSION,'historical_batches':len(batches),'runner_rows':len(df),
        'scored_non_scratched':len(scored),'unscored_non_scratched':int((~df.scored & ~df.scratched).sum()),
        'missing_track_fallback_scored':int(scored.track_fallback.sum()),
        'missing_distance_fallback_scored':int(scored.distance_fallback.sum()),
        'missing_track_distance_fallback_scored':int(scored.track_distance_fallback.sum()),
        'score_parity_failures':int((df.score_parity=='PARITY_FAIL').sum()),
        'unscored_potential_input_errors':int((df.unscored_reason=='UNSCORED_WITH_POTENTIAL_INPUTS_CHECK').sum()),
        'opponent_candidate_records':len(candidate),'opposition_rating_ready':False,
        'model_changed':False,'data_changed':False}])
    # Optional outcome JOIN only after constructing historical runner features;
    # descriptive coverage stratification, not a fitted challenger.
    outcome_strata=pd.DataFrame(columns=['period','rank1_fallback_components','races','rank1_wins',
        'rank1_win_pct','field_adjusted_random_expected_wins','excess_vs_random_pp',
        'scope_warning'])
    if race_outcomes is not None and len(race_outcomes):
        races=pd.DataFrame(race_outcomes).copy()
        needed={'batch_id','race_date','status','top1_won','actual_starters','original_top1_scratched','winner_count'}
        if needed.issubset(races.columns):
            top=df[pd.to_numeric(df.model_rank,errors='coerce').eq(1)].copy()
            if top.batch_id.duplicated().any():
                raise ValueError('Duplicate top-ranked horse in archived features')
            merged=races.merge(top[['batch_id','career_fallback_components','score_parity']],on='batch_id',how='inner',validate='one_to_one')
            n=pd.to_numeric(merged.actual_starters,errors='coerce')
            winners=pd.to_numeric(merged.winner_count,errors='coerce')
            eligible=(merged.status.eq('EVALUABLE_RETROSPECTIVE_RESEARCH') &
                      merged.original_top1_scratched.astype(str).str.casefold().ne('true') &
                      merged.score_parity.eq('PARITY_OK') & n.between(2,30) &
                      winners.ge(1) & winners.le(n))
            merged=merged[eligible].copy()
            merged['random_p']=pd.to_numeric(merged.winner_count)/pd.to_numeric(merged.actual_starters)
            merged['win']=merged.top1_won.astype(str).str.casefold().eq('true').astype(int)
            merged['period']=merged.race_date.astype(str).str[:10].apply(
                lambda d:'EARLIER_RESEARCH_THROUGH_2026_10_30' if d<'2026-10-31' else
                'REUSED_HOLDOUT_2026_10_31_TO_11_08' if d<='2026-11-08' else
                'LATER_RETROSPECTIVE_RESEARCH')
            aggregate=[]
            for (period,count),g in merged.groupby(['period','career_fallback_components']):
                n=len(g);wins=int(g.win.sum());expected=g.random_p.sum()
                aggregate.append({'period':period,'rank1_fallback_components':int(count),
                    'races':n,'rank1_wins':wins,'rank1_win_pct':100*wins/n,
                    'field_adjusted_random_expected_wins':float(expected),
                    'excess_vs_random_pp':100*(wins-expected)/n,
                    'scope_warning':'Exploratory description only; missingness, race class, distance and field size may confound results'})
            outcome_strata=pd.DataFrame(aggregate,columns=outcome_strata.columns)
    feasibility=pd.DataFrame([
        {'required_input':'Horse stable identifier','status':'VERIFY_EXTERNALLY','research_note':'Names alone may be ambiguous across providers'},
        {'required_input':'Dated previous race IDs','status':'NOT_VALIDATED','research_note':'Audit source-form candidate fields; no race-ID linkage assumed'},
        {'required_input':'Previous full fields and finishing positions','status':'NOT_VALIDATED','research_note':'Current archive does not certify opponent identities'},
        {'required_input':'Pre-race opponent ratings','status':'NOT_BUILT','research_note':'Requires strict chronological replay of dated results'},
        {'required_input':'Provider access/usage rights','status':'CHECK_PROVIDER','research_note':'Verify FormFav or licensed historical-run source; no API endpoint invented'},
    ])
    return {'summary':summary,'missing_stat_groups':missing,'unscored_reasons':diag,
            'fallback_exposure':fallback,'runner_diagnostics':df,
            'invalid_stat_records':pd.DataFrame(invalid,columns=['batch_id','horse','metric','problem']),
            'fallback_outcome_strata':outcome_strata,
            'opposition_source_feasibility':feasibility,
            'opposition_candidate_paths':pd.DataFrame(candidate,columns=['batch_id','horse','candidate_field','value_type','validation'])}


def render_data_audit(secret):
    import streamlit as st
    import v061b_historical as hist
    st.subheader('🧪 v0.6.1t — Missing-data mechanics & opposition-source readiness')
    st.caption('Read-only: audits stored original FormFav runner payloads; does not rescore races, alter bets or fetch new provider data.')
    if st.button('Run v0.6.1t source integrity audit',key='v061t_run'):
        try:
            client=hist.server_db(secret)
            batches=hist.read_all(client,hist.RACES,'*','race_date')
            features=hist.read_all(client,hist.FEATURES,'*')
            results=hist.read_all(client,hist.RESULTS,'*')
            historical=hist.build_reports(batches,features,results)
            st.session_state['v061t_reports']=build_missing_data_reports(features,batches,historical['races'])
        except Exception as exc:
            st.error('v0.6.1t audit failed; no data changed: '+str(exc));return
    reports=st.session_state.get('v061t_reports')
    if not reports:return
    for name,frame in reports.items():
        with st.expander(name.replace('_',' ').title(),expanded=name in ('summary','missing_stat_groups','unscored_reasons')):
            st.dataframe(frame,hide_index=True,use_container_width=True)
            st.download_button('Download '+name,frame.to_csv(index=False).encode(),
                               file_name=f'v061t_{name}.csv',mime='text/csv',key='v061t_dl_'+name)
    st.warning('Missing statistics are not independently proven to mean zero starts. Career-rate fallback is how frozen v0.2 calculates its smoothed components, NOT evidence of actual race-specific experience. Opposition strength remains unimplemented.')
