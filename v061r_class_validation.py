"""v0.6.1r — frozen race-class ontology and at-lock research-only shadows.

Historical class normalisation is exploratory. Prospective records are captured
inside newly locked, immutable v0.6.1 snapshots, BEFORE result imports. The
existing v0.2 rankings and v0.6.1m probabilities are never edited.
"""
from __future__ import annotations
import hashlib
import json
import math
import re
from datetime import datetime, timezone
from collections import Counter
import pandas as pd

VERSION = 'v0.6.1r'
KEY = 'race_class_shadow_v061r'
# Fixed hypotheses: alternatives chosen from repeated exploration of 2026-10-10 .. 2026-11-08.
# Any historical results are exploratory; new shadows are the only forward cohort.
SPEC = {
  'version': VERSION, 'frozen_model': 'v0.2',
  'research_only': True,
  'class_rules': 'EXPLICIT_CLASS_LABEL_REGEX_V1_STRICT_NO_RUNNER_INFERENCE',
  'distance_rule': 'STAYING_GE_2000M_V1',
  'field_rule': 'ACTUAL_STARTERS_IN_OUTCOME_RANDOM_BASELINE',
  'experience_rule': 'RANK1_CAREER_STARTS_0_4_5_19_20PLUS_UNKNOWN_FROZEN_AT_LOCK',
  'hypotheses': [
    {'id':'H1_STAYERS', 'direction':'greater_excess_over_field_adjusted_random',
     'compare':'distance >= 2000m versus known distance < 2000m',
     'minimum_per_group':100, 'minimum_race_days':15},
    {'id':'H2_MAIDENS', 'direction':'greater_excess_over_field_adjusted_random',
     'compare':'explicit maiden versus verified explicit non-maiden; unknown excluded',
     'minimum_per_group':100, 'minimum_race_days':15},
    {'id':'H3_EXPERIENCE', 'direction':'greater_excess_over_random_for_stayers_after_experience_stratification',
     'compare':'staying vs non-staying in career-start coverage/experience bands',
     'minimum_per_group':100, 'minimum_race_days':15},
  ],
  'prospective_scope':'ONLY_NEW_AT_LOCK_SHADOWS_NO_RETROACTIVE_BACKFILL',
  'no_bet_or_ranking_changes': True,
}
SPEC_SHA = hashlib.sha256(json.dumps(SPEC, sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode()).hexdigest()

# A positive non-maiden class MUST be explicitly recognisable. All other
# labels remain unknown (never assume non-maiden from lack of MAIDEN token).
CLASS_PATTERNS = (
 ('MAIDEN',r'\b(?:MAIDEN|MDN)\b'),
 ('BLACK_TYPE_GROUP1',r'\b(?:GROUP\s*1|GR\s*1|G\s*1|G1)\b'),
 ('BLACK_TYPE_GROUP2',r'\b(?:GROUP\s*2|GR\s*2|G\s*2|G2)\b'),
 ('BLACK_TYPE_GROUP3',r'\b(?:GROUP\s*3|GR\s*3|G\s*3|G3)\b'),
 ('BLACK_TYPE_LISTED',r'\b(?:LISTED|LR)\b'),
 ('BENCHMARK',r'\b(?:BM\s*\d{2,3}|BENCHMARK\s*\d{2,3})\b'),
 ('CLASS',r'\b(?:CLASS\s*[1-6]|CL\s*[1-6])\b'),
 ('OPEN',r'\b(?:OPEN(?:\s+HANDICAP)?|OPEN\s+HCP)\b'),
)
LABEL_KEYS = ('raceClass','raceType','class','category','conditions','raceName','title','name','description')
SCOPE_KEYS = ('race','raceInfo','raceDetails','event','meetingRace')
DIST_KEYS = ('distance','distanceMeters','distanceMetres','raceDistance','distance_m','distanceM')


def _canonical(obj):
    return hashlib.sha256(json.dumps(obj, sort_keys=True, separators=(',', ':'), ensure_ascii=False, allow_nan=False).encode()).hexdigest()


def _scopes(raw):
    if not isinstance(raw,dict):return []
    return [('root',raw)]+[(k,raw[k]) for k in SCOPE_KEYS if isinstance(raw.get(k),dict)]


def _labels(raw, *, allow_name=True):
    out=[]
    for scope,obj in _scopes(raw):
        for key in LABEL_KEYS:
            if not allow_name and key in ('name','raceName','title','description'):continue
            if isinstance(obj.get(key),str) and obj[key].strip():
                out.append((scope+'.'+key,re.sub(r'\s+',' ',obj[key].strip()).upper()))
    return out


def classify_text(raw):
    """Returns conservative family classification from explicit metadata only."""
    labels=_labels(raw)
    found=set()
    for _,value in labels:
        for family,pattern in CLASS_PATTERNS:
            if re.search(pattern,value,re.I):found.add(family)
    flags=[]
    for scope,obj in _scopes(raw):
        for key in ('isMaiden','maiden'):
            if type(obj.get(key)) is bool:flags.append((scope+'.'+key,obj[key]))
    if any(v for _,v in flags):found.add('MAIDEN')
    # conflicting explicit clues => not classifiable; avoid optimistic adjudication
    conflict=(len(found)>1 or ('MAIDEN' in found and any(not flag for _,flag in flags))
              or (found and any(flag for _,flag in flags) and 'MAIDEN' not in found))
    if conflict:return {'class_family':'UNKNOWN','maiden_class':'UNKNOWN','class_evidence':'CONFLICTING_EXPLICIT_CLASS', 'class_raw_labels':' | '.join(v for _,v in labels)[:400]}
    family=next(iter(found)) if found else 'UNKNOWN'
    if family=='MAIDEN': maiden='EXPLICIT_MAIDEN'
    elif family!='UNKNOWN':maiden='VERIFIED_NON_MAIDEN'
    elif flags and all(not v for _,v in flags):maiden='VERIFIED_NON_MAIDEN';family='OTHER_VERIFIED_NON_MAIDEN'
    else:maiden='UNKNOWN'
    return {'class_family':family,'maiden_class':maiden,
            'class_evidence':'EXPLICIT_CLASS_METADATA' if family!='UNKNOWN' else 'NO_RECOGNISED_EXPLICIT_CLASS',
            'class_raw_labels':' | '.join(v for _,v in labels)[:400]}


def _distance(raw):
    vals=[]
    for scope,obj in _scopes(raw):
        for k in DIST_KEYS:
            if k not in obj:continue
            val=obj[k]
            if isinstance(val,dict):
                unit=str(val.get('unit','m')).lower()
                if unit not in ('m','metres','meters','meter','metre'):continue
                val=val.get('meters',val.get('metres',val.get('value')))
            if isinstance(val,str):
                m=re.fullmatch(r'\s*(\d{3,4})(?:\s*(?:m|metres|meters))?\s*',val,re.I)
                if not m:continue
                val=m.group(1)
            if isinstance(val,bool):continue
            try: n=float(val)
            except (TypeError,ValueError,OverflowError):continue
            if math.isfinite(n) and n.is_integer() and 600<=n<=5000:vals.append(int(n))
    return next(iter(set(vals))) if len(set(vals))==1 else None


def attributes_from_sources(*sources):
    """Race metadata only, never runner histories/results used to infer class."""
    classified=[classify_text(s) for s in sources if isinstance(s,dict)]
    known=[c for c in classified if c['class_family']!='UNKNOWN']
    if known and len({(c['class_family'],c['maiden_class']) for c in known})==1:
        cls=known[0]
    elif known:
        cls={'class_family':'UNKNOWN','maiden_class':'UNKNOWN','class_evidence':'CONFLICT_ACROSS_SOURCES','class_raw_labels':''}
    else:cls=classified[0] if classified else classify_text({})
    distances=[_distance(s) for s in sources if isinstance(s,dict)]
    ds=set(x for x in distances if x is not None)
    d=next(iter(ds)) if len(ds)==1 else None
    return {**cls,'distance_m':d,'distance_band':('STAYING_GE_2000' if d is not None and d>=2000
                                                 else 'NON_STAYING_LT_2000' if d is not None
                                                 else 'DISTANCE_UNKNOWN')}


def _horse_key(name):
    return re.sub(r'[^a-z0-9]', '', str(name or '').casefold())


def _career_starts(runner):
    stats=runner.get('stats') or {}
    overall=stats.get('overall') if isinstance(stats,dict) else None
    if not isinstance(overall,dict):return None
    val=overall.get('starts')
    if isinstance(val,bool):return None
    try: n=float(val)
    except (TypeError,ValueError,OverflowError):return None
    return int(n) if math.isfinite(n) and n>=0 and n.is_integer() else None


def experience_at_lock(payload):
    raw=payload.get('form_response_raw') or {}
    values=raw.get('runners') if isinstance(raw,dict) else None
    if not isinstance(values,list):values=[]
    runners=payload.get('rankings') or []
    indexes={}
    duplicates=set()
    for entry in values:
        if not isinstance(entry,dict):continue
        name=_horse_key(entry.get('name') or entry.get('runnerName') or entry.get('horseName'))
        if not name:continue
        if name in indexes:duplicates.add(name)
        indexes[name]=entry
    obs=[]
    for row in runners:
        key=_horse_key(row.get('horse'))
        val=_career_starts(indexes[key]) if key in indexes and key not in duplicates else None
        obs.append(val)
    ranked=sorted(runners,key=lambda x:int(x.get('rank') or 9999))
    top_key=_horse_key(ranked[0].get('horse')) if ranked else ''
    top_starts=(_career_starts(indexes[top_key]) if top_key in indexes and top_key not in duplicates else None)
    band=('TOP1_CAREER_UNKNOWN' if top_starts is None else 'TOP1_0_4' if top_starts<=4
          else 'TOP1_5_19' if top_starts<=19 else 'TOP1_20PLUS')
    return {'top1_career_starts':top_starts,'top1_career_band':band,
            'field_career_known_count':sum(v is not None for v in obs),
            'field_career_missing_count':sum(v is None for v in obs)}


def attach_class_shadow(payload):
    """Pure function invoked after v0.6.1m, before the immutable archive hash."""
    if payload.get('model_version')!='v0.2':raise ValueError('v0.6.1r requires frozen v0.2')
    if KEY in payload:raise ValueError('v0.6.1r shadow already exists')
    # Never fetch results/provider refresh, or mutate rankings or earlier shadows.
    result=attributes_from_sources(payload.get('scheduled_race_raw'),payload.get('form_response_raw'))
    runners=payload.get('rankings') or []
    unscored=sum(r.get('score') is None for r in runners)
    registered_at=datetime.now(timezone.utc).isoformat()
    shadow={
      'version':VERSION,'hypotheses_sha256':SPEC_SHA,
      'registered_at_utc':registered_at,
      'hypothesis_ids':[x['id'] for x in SPEC['hypotheses']],
      'model_version':'v0.2',**result,**experience_at_lock(payload),
      'declared_active_field_size':len(runners),'unscored_count':unscored,
      'research_only':True,'actual_off_certified':False,
    }
    out=dict(payload);out[KEY]=shadow
    return out


def historical_reports(races, features, batches):
    """Historical descriptive comparison only: never claim new independent holdout."""
    import v061p_race_type_lab as p
    base=p.make_reports(races,features,batches)
    row=base['eligible_race_categories'].copy()
    by_id={str(b['id']):b for b in batches}
    data=[]
    for item in row.to_dict('records'):
        batch=by_id.get(str(item['batch_id']),{})
        src=batch.get('source_form_payload') or {}
        metadata=attributes_from_sources(src)
        data.append({**item, **metadata, 'historical_only':True})
    data=pd.DataFrame(data)
    if data.empty:return {'historical_summary':pd.DataFrame([{'races':0}]),'historical_class_performance':pd.DataFrame(),'historical_races':data}
    results=[]
    for (split,family),g in data.groupby(['split','class_family'],dropna=False):
        n=len(g)
        results.append({'split':split,'class_family':family,'races':n,'days':g.race_date.nunique(),
            'rank1_wins':int(g.top1_win.sum()),'rank1_win_pct':100*g.top1_win.mean(),
            'random_expected_wins':float(g.baseline_random_win_prob.sum()),
            'excess_vs_random_pp':100*(g.top1_win-g.baseline_random_win_prob).mean(),
            'staying_races':int((g.distance_band=='STAYING_GE_2000').sum()),
            'under_30_races':n<30,'sample_previously_explored':True})
    summary=pd.DataFrame([{'version':VERSION,'eligible_historical_races':len(data),
        'historical_explicit_maiden':int((data.maiden_class=='EXPLICIT_MAIDEN').sum()),
        'historical_verified_non_maiden':int((data.maiden_class=='VERIFIED_NON_MAIDEN').sum()),
        'historical_unknown_class':int((data.maiden_class=='UNKNOWN').sum()),
        'note':'Reuses previously examined historical outcomes; descriptive only, not independent confirmation'}])
    return {'historical_summary':summary,'historical_class_performance':pd.DataFrame(results),'historical_races':data}


def prospective_reports(archives, result_rows):
    """Evaluate ONLY existing at-lock v0.6.1r shadows, not old archive backfills."""
    import v061_prospective as pros
    index=pros._result_index(result_rows)
    records=[];seen=set()
    for a in sorted(archives,key=lambda r:str(r.get('created_at') or '')):
        p=a.get('payload') or {}; shadow=p.get(KEY)
        if not isinstance(shadow,dict):continue
        bid=str(a.get('batch_id'))
        key=pros.race_key(a)
        errors=[]
        if key in seen:errors.append('DUPLICATE_RACE_ARCHIVE')
        seen.add(key)
        try:
            if pros.digest(p)!=a.get('payload_sha256'):errors.append('ARCHIVE_HASH_MISMATCH')
            if shadow.get('hypotheses_sha256')!=SPEC_SHA:errors.append('HYPOTHESIS_VERSION_MISMATCH')
            if shadow.get('model_version')!='v0.2':errors.append('MODEL_MISMATCH')
            ts=pros.iso_timestamp(shadow.get('registered_at_utc'))
            generated=pros.iso_timestamp((p.get('source_times') or {}).get('ranking_generated_utc'))
            created=pros.iso_timestamp(a.get('created_at'))
            scheduled=pros.iso_timestamp(a.get('scheduled_start_utc'))
            if not all((ts,created,scheduled,generated)) or not generated<=ts<=created<scheduled:
                errors.append('NOT_REGISTERED_BEFORE_SCHEDULED_START')
            if shadow.get('declared_active_field_size')!=len(p.get('rankings') or []):errors.append('FIELD_MISMATCH')
            if shadow.get('unscored_count')!=sum(r.get('score') is None for r in p.get('rankings') or []):errors.append('UNSCORED_COUNT_MISMATCH')
            # Recheck only against archived metadata, not updated race information.
            expected=attributes_from_sources(p.get('scheduled_race_raw'),p.get('form_response_raw'))
            if any(shadow.get(k)!=v for k,v in expected.items()):errors.append('CLASSIFICATION_PARITY_MISMATCH')
            if any(shadow.get(k)!=v for k,v in experience_at_lock(p).items()):errors.append('EXPERIENCE_PARITY_MISMATCH')
        except Exception:
            errors.append('INVALID_ARCHIVE_OR_SHADOW')
        report, eval_errors, _=pros.evaluate_one(a,index.get(key,[]))
        if eval_errors and report.get('status')=='EVALUABLE_PROVISIONAL':errors.append('EVALUATION_INTEGRITY_ISSUE')
        eligible=(not errors and report.get('status')=='EVALUABLE_PROVISIONAL'
                  and report.get('original_rank1_scratched') is False)
        records.append({'batch_id':bid,'race_date':a.get('race_date'),'track':a.get('track'),
            'race_number':a.get('race_number'),'registered_at_utc':shadow.get('registered_at_utc'),
            'status':report.get('status'),'evidence_valid':not errors,'evidence_issues':';'.join(errors),
            'eligible_for_provisional_analysis':eligible,'class_family':shadow.get('class_family'),
            'maiden_class':shadow.get('maiden_class'),'distance_m':shadow.get('distance_m'),
            'distance_band':shadow.get('distance_band'),'unscored_count':shadow.get('unscored_count'),
            'top1_career_band':shadow.get('top1_career_band'),
            'top1_career_starts':shadow.get('top1_career_starts'),
            'field_size':report.get('field_size'), 'top1_won':report.get('top1_won') if eligible else None,
            'random_top1_expected':report.get('random_top1_expected') if eligible else None,
            'source_asof_certified':False,'actual_off_certified':False,
            'independent_confirmation':'NO_CERTIFIED_POINT_IN_TIME_EVIDENCE'})
    df=pd.DataFrame(records)
    if df.empty:
        return {'prospective_summary':pd.DataFrame([{'at_lock_v061r_shadows':0,'provisional_evaluable':0,'promotion_approved':False}]),
                'prospective_evidence_audit':df,'prospective_hypothesis_metrics':pd.DataFrame()}
    eligible=df[df.eligible_for_provisional_analysis].copy()
    metrics=[]
    for dimension in ('distance_band','maiden_class','top1_career_band'):
        for category,g in eligible.groupby(dimension,dropna=False):
            n=len(g)
            metrics.append({'dimension':dimension,'category':category,'races':n,'race_days':g.race_date.nunique(),
                'rank1_wins':int(g.top1_won.sum()),'rank1_win_pct':100*g.top1_won.mean(),
                'random_expected_wins':g.random_top1_expected.sum(),
                'excess_over_random_pp':100*(g.top1_won.astype(float)-g.random_top1_expected).mean(),
                'minimum_sample_met':n>=100 and g.race_date.nunique()>=15,
                'status':'PROVISIONAL_ONLY_NO_PROMOTION'})
    # H3 checks both distance groups WITHIN a pre-registered experience band.
    for (band, distance),g in eligible.groupby(['top1_career_band','distance_band'],dropna=False):
        if distance not in ('STAYING_GE_2000','NON_STAYING_LT_2000'):continue
        metrics.append({'dimension':'H3_DISTANCE_WITHIN_CAREER_BAND',
            'category':str(band)+' / '+str(distance),'races':len(g),
            'race_days':g.race_date.nunique(),
            'rank1_wins':int(g.top1_won.sum()),'rank1_win_pct':100*g.top1_won.mean(),
            'random_expected_wins':g.random_top1_expected.sum(),
            'excess_over_random_pp':100*(g.top1_won.astype(float)-g.random_top1_expected).mean(),
            'minimum_sample_met':len(g)>=100 and g.race_date.nunique()>=15,
            'status':'PROVISIONAL_ONLY_NO_PROMOTION'})
    summary=pd.DataFrame([{'at_lock_v061r_shadows':len(df),'valid_evidence':int(df.evidence_valid.sum()),
        'provisional_evaluable':len(eligible),'excluded_old_unshadowed_records':'Yes',
        'promotion_approved':False,'hypotheses_sha256':SPEC_SHA,
        'note':'Only original at-lock shadows. Unknown maiden classifications never treated as verified non-maiden.'}])
    return {'prospective_summary':summary,'prospective_evidence_audit':df,
            'prospective_hypothesis_metrics':pd.DataFrame(metrics)}


def render(secret):
    import streamlit as st
    import v061b_historical as hist
    import v061_prospective as pros
    st.subheader('🧪 v0.6.1r — Normalised class & prospectively registered hypotheses')
    st.caption('Research only. Fixed rules and hypotheses are embedded automatically when saving a NEW race; no extra manual locking step.')
    st.code('Frozen hypothesis SHA-256: '+SPEC_SHA)
    st.warning('Historical outcomes have been inspected in previous experiments: historical summaries are exploratory. '
               'A new v0.6.1r shadow is registered within the original prospective archive only at Save & Lock; old races are not backfilled.')
    tab1,tab2=st.tabs(['Historical class audit','New at-lock prospective evidence'])
    with tab1:
        if st.button('Run historical class normalisation audit',key='v061r_historical'):
            try:
                cli=hist.server_db(secret)
                batches=hist.read_all(cli,hist.RACES,'*','race_date')
                features=hist.read_all(cli,hist.FEATURES,'*')
                results=hist.read_all(cli,hist.RESULTS,'batch_id,result_payload,result_sha256,actual_starters')
                historical=hist.build_reports(batches,features,results)
                st.session_state['v061r_historical_reports']=historical_reports(historical['races'],pd.DataFrame(features),batches)
            except Exception as exc:st.error('Historical audit failed without DB changes: '+str(exc))
        for name,frame in st.session_state.get('v061r_historical_reports',{}).items():
            with st.expander(name,expanded=name.endswith('summary')):
                st.dataframe(frame,hide_index=True,use_container_width=True)
                st.download_button('Download '+name,frame.to_csv(index=False).encode(),file_name='v061r_'+name+'.csv',key='v061r_'+name)
    with tab2:
        if st.button('Audit v0.6.1r new at-lock prospective classifications',key='v061r_prospective'):
            try:
                cli=pros.archive_client(secret)
                archives=pros._read_all(cli,pros.TABLE,order='created_at')
                results=pros._read_all(cli,'runner_results')
                st.session_state['v061r_prospective_reports']=prospective_reports(archives,results)
            except Exception as exc:st.error('Read-only prospective audit failed: '+str(exc))
        for name,frame in st.session_state.get('v061r_prospective_reports',{}).items():
            with st.expander(name,expanded=name.endswith('summary')):
                st.dataframe(frame,hide_index=True,use_container_width=True)
                st.download_button('Download '+name,frame.to_csv(index=False).encode(),file_name='v061r_'+name+'.csv',key='v061r_'+name)
    st.info('No betting, ranking, confidence or staking adjustments are authorised by this research.')
