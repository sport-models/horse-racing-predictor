"""v0.6.2B Stage 2.1: venue-tier hypothesis and missing-score integrity.

RESEARCH ONLY, read-only. Venue-based *proxy* classification, not verified
race-meeting status. No production model changes or debutant-score imputation.
"""
from __future__ import annotations
import math
import re
import pandas as pd

METRO = {
    'flemington', 'caulfield', 'caulfield heath', 'moonee valley', 'sandown',
    'sandown hillside', 'sandown lakeside',
    'randwick', 'royal randwick', 'rosehill', 'canterbury', 'warwick farm',
    'eagle farm', 'doomben', 'ascot', 'belmont',
    'morphettville', 'morphettville parks',
}
PROVINCIAL = {
    'wyong', 'hawkesbury', 'gosford', 'newcastle', 'kembla grange',
    'gold coast', 'gold coast polytrack', 'ipswich', 'sunshine coast', 'toowoomba',
}
# Explicit shortlist only: unknown non-metro venues are NOT assumed country.
COUNTRY = {
    'donald', 'moe', 'seymour', 'geelong', 'pakenham', 'ballarat', 'bendigo',
    'bairnsdale', 'echuca', 'hamilton', 'horsham', 'kilmore', 'kyneton',
    'mildura', 'sale', 'swan hill', 'wangaratta', 'warrnambool', 'st arnaud',
    'avoca', 'ararat', 'wycheproof', 'yarra valley', 'traralgon',
    'albury', 'ballina', 'casino', 'coffs harbour', 'goulburn', 'grafton',
    'lismore', 'muswellbrook', 'nowra', 'port macquarie', 'scone', 'tamworth',
    'taree', 'wagga', 'wellington', 'orange', 'dubbo', 'bathurst',
}
assert not (METRO & PROVINCIAL or METRO & COUNTRY or PROVINCIAL & COUNTRY)

VENUE_SOURCES = {
    'METRO_VENUE_PROXY': 'Official venue references; meeting class still unverified',
    'PROVINCIAL_VENUE_PROXY': 'NSW provincial and Queensland non-metro venue references',
    'COUNTRY_VENUE_PROXY': 'Selected Victorian and NSW regional venue references',
    'UNCLASSIFIED_VENUE': 'No safe venue classification in this frozen lookup',
}

def venue_key(track):
    return re.sub(r'\s+', ' ', str(track or '').lower().replace('-', ' ').replace('_', ' ')).strip()

def venue_class(track):
    key = venue_key(track)
    if key in METRO:
        return 'METRO_VENUE_PROXY'
    if key in PROVINCIAL:
        return 'PROVINCIAL_VENUE_PROXY'
    if key in COUNTRY:
        return 'COUNTRY_VENUE_PROXY'
    return 'UNCLASSIFIED_VENUE'

def _number(value):
    try:
        n = float(value)
        return n if math.isfinite(n) else None
    except (ValueError, TypeError, OverflowError):
        return None

def missing_score_reason(row, cohort):
    """Never equate all missing scores to verified first-time starters."""
    from v062b_stage2 import HISTORICAL, PROSPECTIVE
    source = 'research_model_score' if cohort == HISTORICAL else 'locked_score'
    if _number(row.get(source)) is not None:
        return 'SCORED'
    if cohort == PROSPECTIVE:
        status = str(row.get('locked_scoring_status') or '')
        if status == 'NULL_SCORE_ZERO_CAREER_STARTS':
            return 'PROVIDER_REPORTED_ZERO_CAREER_STARTS'
        if status == 'NULL_SCORE_NO_RECORDED_RECENT_FORM':
            return 'NO_RECORDED_RECENT_FORM_NOT_PROVEN_DEBUTANT'
        if status == 'NULL_SCORE_FORM_RUNNER_NOT_FOUND':
            return 'FORM_RUNNER_NOT_FOUND'
        return 'NULL_SCORE_REASON_UNVERIFIED'
    starts = _number(row.get('research_career_starts'))
    if starts == 0:
        return 'PROVIDER_REPORTED_ZERO_CAREER_STARTS_POST_RACE'
    if starts is not None and starts > 0:
        return 'HAS_RECORDED_CAREER_STARTS_BUT_UNSCORED'
    return 'NULL_SCORE_REASON_UNVERIFIED_POST_RACE'

def build_stage21(stage2_starters: pd.DataFrame, cohort: str):
    """Per-starter evidence, venue segments, venue inventory, null-score reasons,
    race coverage and hypothesis contrasts. Cohorts never combined.
    """
    from v062b_stage2 import HISTORICAL, PROSPECTIVE, ALLOWED
    if cohort not in ALLOWED:
        raise ValueError('Research cohort must be explicitly historical or prospective')
    if stage2_starters is None or stage2_starters.empty:
        return tuple(pd.DataFrame() for _ in range(6))
    r = stage2_starters.copy()
    required = ['batch_id','race_date','track','race_number','horse',
                'barrier','draw_zone','actual_starters','won',
                'uniform_expected_win','quality_matched_expected','evidence_status']
    absent = sorted(set(required) - set(r.columns))
    if absent:
        raise ValueError('Stage 2 starter input missing: ' + ', '.join(absent))
    expected = ('POST_RACE_RETRIEVAL_NOT_POINT_IN_TIME' if cohort == HISTORICAL else
                'SCHEDULE_ONLY_NOT_CERTIFIED')
    if not r['evidence_status'].eq(expected).all():
        raise ValueError('Mixed/unknown evidence types: cohort pooling is forbidden')
    r['venue_class'] = r['track'].map(venue_class)
    r['venue_source_note'] = r['venue_class'].map(VENUE_SOURCES)
    r['venue_class_is_meeting_certified'] = False
    r['null_score_reason'] = r.apply(lambda row: missing_score_reason(row, cohort), axis=1)
    r['score_present'] = r['null_score_reason'].eq('SCORED')
    r['quality_match_eligible'] = r['quality_matched_expected'].notna()
    r['cohort'] = cohort
    r['outcome_label'] = 'ORIGINAL_RESULT_SINGLE_WINNER_ONLY'
    keys = ['batch_id','race_date','track','race_number','venue_class']
    race = (r.groupby(keys, dropna=False)
            .agg(starters=('horse','size'), winners=('won','sum'),
                 unscored_starters=('score_present',lambda s:int((~s).sum())),
                 score_complete=('score_present','all'),
                 quality_comparison_complete=('quality_match_eligible','all'))
            .reset_index())
    if not race['winners'].eq(1).all():
        raise ValueError('Stage 2.1 rejects multiwinner races; provide excluded-race manifest separately')
    if not race['starters'].eq(race['batch_id'].map(r.groupby('batch_id').size())).all():
        raise ValueError('Inconsistent starter membership')
    race['cohort'] = cohort
    race['coverage_policy'] = 'DO_NOT_IMPUTE_UNSCORED_STARTERS'
    venue = (r.groupby(['venue_class','track'], dropna=False)
             .agg(starters=('horse','size'), races=('batch_id','nunique'),
                  race_dates=('race_date','nunique'), winner_rows=('won','sum'))
             .reset_index())
    venue['cohort'] = cohort
    venue['meeting_class_verified'] = False
    venue['source_note'] = venue['venue_class'].map(VENUE_SOURCES)
    summary = []
    for segment, label in [(['venue_class'],'VENUE_CLASS'),
                           (['venue_class','field_band'],'VENUE_BY_FIELD_SIZE'),
                           (['venue_class','distance_band'],'VENUE_BY_DISTANCE')]:
        if any(c not in r.columns for c in segment):
            continue
        dim = segment + ['draw_zone']
        counts = (r.groupby(dim,dropna=False)
                  .agg(starters=('horse','size'),wins=('won','sum'),
                       expected_wins_uniform=('uniform_expected_win','sum'),
                       quality_matched_starters=('quality_match_eligible','sum'),
                       race_dates=('race_date','nunique'),races=('batch_id','nunique'))
                  .reset_index())
        match = (r[r['quality_match_eligible']].groupby(dim,dropna=False)
                 .agg(quality_matched_wins=('won','sum'),
                      expected_wins_quality_null=('quality_matched_expected','sum'))
                 .reset_index())
        merged = counts.merge(match,how='left',on=dim,validate='one_to_one')
        merged['segment_type'] = label
        merged['field_band'] = merged.get('field_band',pd.Series([None]*len(merged))).values
        merged['distance_band'] = merged.get('distance_band',pd.Series([None]*len(merged))).values
        merged['excess_vs_uniform'] = merged['wins']-merged['expected_wins_uniform']
        merged['excess_vs_quality_matched_null'] = (merged['quality_matched_wins'] -
                                                    merged['expected_wins_quality_null'])
        merged['quality_matched_coverage_pct'] = (100*merged['quality_matched_starters']/merged['starters'])
        merged['cohort'] = cohort
        merged['gate'] = merged.apply(lambda x:
            'EXPLORATORY_ONLY_NOT_VALIDATED' if
            x.quality_matched_starters >= 100 and x.race_dates >= 15
            else 'INSUFFICIENT_INDEPENDENT_EXPOSURE',axis=1)
        merged['classification_warning'] = 'VENUE_PROXY_NOT_MEETING_CLASSIFICATION'
        summary.append(merged)
    summary = pd.concat(summary,ignore_index=True)
    reasons = (r.groupby(['venue_class','null_score_reason'],dropna=False)
               .agg(starters=('horse','size'),races=('batch_id','nunique'),
                    dates=('race_date','nunique'),winners=('won','sum'))
               .reset_index())
    reasons['cohort'] = cohort
    coverage = (race.groupby('venue_class')
                .agg(races=('batch_id','nunique'),
                     races_with_unscored=('score_complete',lambda v:int((~v).sum())),
                     races_quality_complete=('quality_comparison_complete','sum'),
                     unscored_starters=('unscored_starters','sum'),
                     racing_dates=('race_date','nunique')).reset_index())
    coverage['cohort'] = cohort
    comp = summary[summary['segment_type'].eq('VENUE_CLASS')]
    pivot = comp.pivot(index='venue_class',columns='draw_zone',values='excess_vs_uniform')
    hypothesis = coverage.copy()
    hypothesis['inside_excess_wins'] = (hypothesis['venue_class'].map(pivot['INSIDE'])
                                       if 'INSIDE' in pivot else float('nan'))
    hypothesis['middle_excess_wins'] = (hypothesis['venue_class'].map(pivot['MIDDLE'])
                                       if 'MIDDLE' in pivot else float('nan'))
    hypothesis['inside_minus_middle_excess'] = (hypothesis['inside_excess_wins'] -
                                               hypothesis['middle_excess_wins'])
    hypothesis['gate'] = hypothesis.apply(
        lambda x:'NO_PREDICTIVE_INFERENCE' if x.racing_dates<15
        else 'EXPLORATORY_DESCRIPTIVE_ONLY',axis=1)
    hypothesis['classification_warning'] = 'VENUE_PROXY_NOT_MEETING_CLASSIFICATION'
    return r,summary,venue,reasons,race,hypothesis

def dead_heat_manifest(rows, cohort):
    """Show joint-first race exclusions without selecting a fictitious sole winner."""
    from v062b_stage2 import HISTORICAL,PROSPECTIVE,ALLOWED
    if cohort not in ALLOWED:
        raise ValueError('Explicit cohort required')
    fields = ['batch_id','race_date','track','race_number','joint_first_count',
              'winning_horses','issue','cohort']
    if rows is None or rows.empty:
        return pd.DataFrame(columns=fields)
    count_name = 'winner_count' if cohort == HISTORICAL else 'joint_first_count'
    if count_name not in rows:
        return pd.DataFrame(columns=fields)
    df=rows.copy()
    df[count_name]=pd.to_numeric(df[count_name],errors='coerce')
    df=df[df[count_name].gt(1)].copy()
    if df.empty:
        return pd.DataFrame(columns=fields)
    df['joint_first_count']=df[count_name]
    df['winning_horses']=df.get('winning_horses',pd.Series([None]*len(df),index=df.index))
    df['issue']='EXCLUDED_JOINT_FIRST_FROM_SINGLE_WINNER_BARRIER_RESEARCH'
    df['cohort']=cohort
    return df.reindex(columns=fields).reset_index(drop=True)

def render(secret):
    import streamlit as st
    from v062b_stage2 import analyse,HISTORICAL,PROSPECTIVE
    st.subheader('🔬 v0.6.2B Stage 2.1 — Country / metro venue hypothesis & unscored horses')
    st.caption('Read-only exploratory venue-based proxies, not confirmed meeting class. '
               'Country, provincial and metro are distinct. Dead heats are explicitly '
               'reported and excluded from the one-winner comparison. Missing scores '
               'are never imputed.')
    source=st.radio('Source cohort (never pooled)',
                    ['Historical discovery (post-race FormFav)',
                     'Prospective archived (schedule-only evidence)'],
                    key='v062b_stage21_source')
    if not st.button('Run Stage 2.1 venue hypothesis and missing-score audit',
                     key='v062b_stage21_run'):
        return
    try:
        if source.startswith('Historical'):
            from v061b_historical import server_db,read_all,RACES,FEATURES,RESULTS
            from v062b_historical import build_historical
            client=server_db(secret)
            detail,_,prior_issues,races=build_historical(
                read_all(client,RACES,'*','race_date'),
                read_all(client,FEATURES,
                         'batch_id,horse,horse_key,model_rank,model_score,barrier,career_starts'),
                read_all(client,RESULTS,'batch_id,result_payload,result_sha256,actual_starters'))
            cohort=HISTORICAL
            manifest=dead_heat_manifest(races,cohort)
        else:
            from v061_prospective import archive_client,_read_all,TABLE,evaluate_archives
            from v062b_barrier_lab import build
            client=archive_client(secret)
            archived=_read_all(client,TABLE,order='created_at')
            final=_read_all(client,'runner_results')
            report=evaluate_archives(archived,final)
            detail,_,prior_issues=build(archived,final)
            cohort=PROSPECTIVE
            manifest=dead_heat_manifest(report['races'],cohort)
        evidence,_,issues=analyse(detail,cohort)
        starter,segments,inventory,reasons,race_coverage,hypotheses=build_stage21(evidence,cohort)
    except Exception as exc:
        st.error('Stage 2.1 read-only audit failed: '+str(exc))
        st.exception(exc)
        return
    a,b,c=st.columns(3)
    a.metric('Single-winner races in Stage 2.1',starter.batch_id.nunique() if not starter.empty else 0)
    b.metric('Races containing unscored starters',
             int(race_coverage.unscored_starters.gt(0).sum()) if not race_coverage.empty else 0)
    c.metric('Explicit dead-heat exclusions',len(manifest))
    st.markdown('**Metro vs country venue hypothesis (PROXY, not meeting classification)**')
    st.dataframe(hypotheses,hide_index=True,use_container_width=True)
    st.dataframe(segments[segments.segment_type.eq('VENUE_CLASS')] if not segments.empty else segments,
                 hide_index=True,use_container_width=True)
    st.markdown('**Missing model-score explanations; debutants only where provider evidence supports zero starts**')
    st.dataframe(reasons,hide_index=True,use_container_width=True)
    st.markdown('**Unscored races remain in uniform-draw analysis but not complete quality matching**')
    st.dataframe(race_coverage[race_coverage.unscored_starters.gt(0)]
                 if not race_coverage.empty else race_coverage,hide_index=True,use_container_width=True)
    if not manifest.empty:
        st.warning(f'{len(manifest)} genuine joint-first races listed as exclusions, not assigned a sole winner.')
        st.dataframe(manifest,hide_index=True,use_container_width=True)
    with st.expander('All venue/field-size/distance segments, unclassified venues, and exclusions'):
        st.dataframe(segments,hide_index=True,use_container_width=True)
        st.dataframe(inventory,hide_index=True,use_container_width=True)
        if not prior_issues.empty:
            st.dataframe(prior_issues,hide_index=True,use_container_width=True)
        if not issues.empty:
            st.dataframe(issues,hide_index=True,use_container_width=True)
    label='historical' if cohort==HISTORICAL else 'prospective'
    exports = [('starters',starter),('venue_segments',segments),
               ('venue_inventory',inventory),('missing_score_reasons',reasons),
               ('race_score_coverage',race_coverage),('venue_hypothesis',hypotheses),
               ('dead_heat_exclusions',manifest),('stage2_issues',issues)]
    from io import BytesIO
    from zipfile import ZipFile, ZIP_DEFLATED
    out = BytesIO()
    with ZipFile(out, mode='w', compression=ZIP_DEFLATED) as archive:
        for name,data in exports:
            archive.writestr('v062b_stage21_'+name+'_'+label+'.csv',
                             data.to_csv(index=False))
    st.download_button('Download ALL Stage 2.1 exports for this cohort (ZIP)',
                       data=out.getvalue(),
                       file_name='v062b_stage21_'+label+'_audit_bundle.zip',
                       mime='application/zip',key='v062b_stage21_bundle_'+label)
    with st.expander('Optional: Download individual CSVs'):
        for name,data in exports:
            st.download_button('Export Stage 2.1 '+name,
                               data.to_csv(index=False).encode('utf-8'),
                               file_name='v062b_stage21_'+name+'_'+label+'.csv',
                               mime='text/csv',key='v062b_stage21_'+label+'_'+name)
    st.warning('RESEARCH ONLY: venue is NOT certified race-meeting class; historical '
               'inputs were retrieved after racing, and prospective dates are few. '
               'The quality comparison conditions on the winner tier and is NOT a '
               'prospective win probability. No feature promotion.')
