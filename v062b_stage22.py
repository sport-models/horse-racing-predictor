"""Stage 2.2: immutable-archive career-start provenance and official meeting evidence gate.

RESEARCH ONLY. Venue proxies NEVER become verified classifications through inference.
No model scores imputed or overwritten, no results or databases modified.
"""
from __future__ import annotations
import math
import re
from urllib.parse import urlparse
import pandas as pd
from v062b_stage21 import venue_class, venue_key
# Match the core archive hash and runner index without depending on
# legacy test modules that stub sys.modules['v061_prospective'].
from v042_results import canonical
from hashlib import sha256
import json


def digest(value):
    return sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                             separators=(',', ':'), allow_nan=False).encode('utf-8')).hexdigest()


def _locked_runner_metadata(form_response):
    candidates=[]
    def visit(node):
        if isinstance(node,dict):
            for key,value in node.items():
                if key=='runners' and isinstance(value,list):
                    candidates.extend(v for v in value if isinstance(v,dict))
                elif isinstance(value,(dict,list)):
                    visit(value)
        elif isinstance(node,list):
            for item in node:
                visit(item)
    visit(form_response)
    indexed,ambiguous={},set()
    for runner in candidates:
        key=canonical(runner.get('name') or runner.get('runnerName') or runner.get('horseName'))
        if not key:
            continue
        if key in indexed:
            ambiguous.add(key)
        else:
            indexed[key]=runner
    return indexed,ambiguous

CLASS_TYPES={'METRO','PROVINCIAL','COUNTRY'}
MEETING_COLUMNS=['race_date','track','meeting_class','official_source_url',
                 'official_source_statement','reviewer_verified']
STATUS=['PROVIDER_ZERO_STARTS','PROVIDER_POSITIVE_STARTS',
        'SOURCE_CONFLICT','SOURCE_MISSING','SOURCE_INVALID']

def number_of_starts(v):
    if isinstance(v,bool):
        return None
    try:
        n=float(v)
        return int(n) if math.isfinite(n) and n.is_integer() and 0<=n<=1000 else None
    except (ValueError,TypeError,OverflowError):
        return None

def archived_start_evidence(raw):
    """Read only explicit archived starts; absence and conflicts are visible."""
    if not isinstance(raw,dict):
        return None,'SOURCE_MISSING','NO_ARCHIVED_RUNNER_METADATA'
    observed=[]
    for field in ('careerStarts','starts'):
        if field in raw and raw[field] not in ('',None):
            observed.append((field,number_of_starts(raw[field])))
    stats=raw.get('stats')
    if isinstance(stats,dict):
        overall=stats.get('overall')
        if isinstance(overall,dict):
            for field in ('starts','careerStarts'):
                if field in overall and overall[field] not in ('',None):
                    observed.append(('stats.overall.'+field,number_of_starts(overall[field])))
    if not observed:
        return None,'SOURCE_MISSING','NO_EXPLICIT_CAREER_STARTS_AT_CAPTURE'
    if any(n is None for field,n in observed):
        return None,'SOURCE_INVALID','INVALID_ARCHIVED_CAREER_START_COUNT'
    values={n for field,n in observed}
    if len(values)!=1:
        return None,'SOURCE_CONFLICT','CONFLICTING_ARCHIVED_CAREER_START_COUNTS'
    n=next(iter(values))
    return n,('PROVIDER_ZERO_STARTS' if n==0 else 'PROVIDER_POSITIVE_STARTS'),(
        '|'.join(field for field,_ in observed))


def inspect_score_provenance(evidence, raw_index, cohort):
    """Attach source-status to Stage 2.1 starter observations, never impute."""
    from v062b_stage2 import ALLOWED,HISTORICAL,PROSPECTIVE
    if cohort not in ALLOWED:
        raise ValueError('Must specify historical or prospective cohort')
    if evidence is None or evidence.empty:
        return pd.DataFrame()
    r=evidence.copy()
    if not {'batch_id','horse','race_date','track','score_present'}.issubset(r.columns):
        raise ValueError('Stage 2.1 starter evidence required')
    rows=[]
    for record in r.to_dict('records'):
        key=(str(record['batch_id']),canonical(record['horse']))
        source=raw_index.get(key)
        value,status,fields=archived_start_evidence(source)
        record['archived_career_starts']=value
        record['archived_career_start_status']=status
        record['archived_career_start_fields']=fields
        record['archived_source_time_class']=(
            'POST_RACE_HISTORICAL_RETRIEVAL_NOT_PRE_RACE' if cohort==HISTORICAL else
            'ORIGINAL_AT_LOCK_PROVIDER_RESPONSE_UNCERTIFIED_PROVIDER_AS_OF')
        record['score_provenance_resolution']=(
            'SCORE_PRESENT' if record['score_present']
            else 'UNSCORED_PROVIDER_ZERO_STARTS' if status=='PROVIDER_ZERO_STARTS'
            else 'UNSCORED_PREVIOUSLY_RACED' if status=='PROVIDER_POSITIVE_STARTS'
            else 'UNSCORED_PROVENANCE_UNVERIFIED')
        rows.append(record)
    return pd.DataFrame(rows)


def historical_raw_index(features):
    index={}
    duplicate=set()
    for f in features:
        key=(str(f.get('batch_id')),canonical(f.get('horse_key') or f.get('horse')))
        if key in index:
            duplicate.add(key)
        else:
            index[key]=f.get('feature_payload')
    for key in duplicate:
        index.pop(key,None)
    return index,duplicate


def prospective_raw_index(archives):
    index={}
    issues=[]
    for archive in archives:
        bid=str(archive.get('batch_id'))
        p=archive.get('payload') or {}
        if not isinstance(p,dict) or digest(p)!=archive.get('payload_sha256'):
            issues.append({'batch_id':bid,'issue':'ARCHIVE_HASH_MISMATCH_NO_SOURCE_ACCESS'})
            continue
        form=p.get('form_response_raw')
        runners,dupes=_locked_runner_metadata(form)
        for name in dupes:
            issues.append({'batch_id':bid,'issue':'DUPLICATE_ARCHIVED_HORSE_'+name})
        for key,raw in runners.items():
            if key not in dupes:
                index[(bid,canonical(key))]=raw
    return index,pd.DataFrame(issues,columns=['batch_id','issue'])


def meeting_evidence(rows, uploaded):
    """Review user-provided official source citations per *meeting and date*.

    No URL is fetched or independently checked; no unsupported verified claim.
    """
    if rows is None or rows.empty:
        return pd.DataFrame(),pd.DataFrame()
    races=rows[['race_date','track','batch_id']].drop_duplicates().copy()
    races['track_key']=races.track.map(venue_key)
    races['venue_proxy']=races.track.map(venue_class)
    reports=[]
    manifest={}
    for m in (uploaded.to_dict('records') if isinstance(uploaded,pd.DataFrame) else []):
        day=str(m.get('race_date') or '')[:10]
        track=venue_key(m.get('track'))
        class_name=str(m.get('meeting_class') or '').strip().upper()
        uri=str(m.get('official_source_url') or '').strip()
        statement=str(m.get('official_source_statement') or '').strip()
        parsed=urlparse(uri)
        flag=str(m.get('reviewer_verified') or '').strip().upper() in ('TRUE','YES','Y','1')
        reasons=[]
        if not re.fullmatch(r'\d{4}-\d{2}-\d{2}',day):reasons.append('INVALID_DATE')
        if not track:reasons.append('EMPTY_TRACK')
        if class_name not in CLASS_TYPES:reasons.append('INVALID_CLASS')
        if parsed.scheme!='https' or not parsed.netloc or parsed.username or parsed.password:
            reasons.append('NO_OFFICIAL_HTTPS_CITATION')
        if len(statement)<20:reasons.append('INSUFFICIENT_OFFICIAL_EXCERPT')
        if not flag:reasons.append('REVIEWER_NOT_CONFIRMED')
        key=(day,track)
        if key in manifest:
            reasons.append('DUPLICATE_MEETING_ROW')
            manifest[key]['issues'].append('DUPLICATE_MEETING_ROW')
        rec={'race_date':day,'track_key':track,'proposed_class':class_name,
             'official_source_url':uri,'official_source_statement':statement,
             'reviewer_verified':flag,'issues':reasons}
        manifest[key]=rec
        reports.append(rec.copy())
    output=[]
    for r in races.to_dict('records'):
        rec=manifest.get((str(r['race_date'])[:10],r['track_key']))
        out=dict(r)
        out['meeting_class']=rec['proposed_class'] if rec and not rec['issues'] else None
        out['meeting_class_evidence_status']=(
            'USER_ATTESTED_OFFICIAL_SOURCE_NOT_INDEPENDENTLY_VALIDATED'
            if rec and not rec['issues'] else 'INCOMPLETE_OR_CONFLICTING_EVIDENCE'
            if rec else 'NO_MEETING_CLASS_SOURCE')
        out['classification_used_for_predictive_testing']=False
        out['official_source_url']=rec['official_source_url'] if rec else None
        output.append(out)
    for p in reports:
        p['issues']='|'.join(sorted(set(p['issues'])))
    return pd.DataFrame(output),pd.DataFrame(reports)


def audit_summary(provenance,meeting):
    if provenance is None or provenance.empty:
        return pd.DataFrame(),pd.DataFrame()
    unscored=provenance[~provenance.score_present].copy()
    status=(unscored.groupby(['score_provenance_resolution','archived_career_start_status'],
                              dropna=False)
                  .agg(starters=('horse','size'),races=('batch_id','nunique'),
                       racing_dates=('race_date','nunique')).reset_index())
    coverage=(meeting.groupby('meeting_class_evidence_status')
              .agg(races=('batch_id','size'),tracks=('track','nunique'),
                   racing_dates=('race_date','nunique')).reset_index())
    return status,coverage


def render(secret):
    import streamlit as st
    from io import BytesIO
    from zipfile import ZipFile,ZIP_DEFLATED
    from v062b_stage2 import analyse,HISTORICAL,PROSPECTIVE
    from v062b_stage21 import build_stage21
    st.subheader('🔬 v0.6.2B Stage 2.2 — Meeting class & debutant provenance')
    st.caption('Read-only source audit; meeting class requires dated official fixture citation '
               'and reviewer attestation. Source URLs are NOT automatically verified. '
               'Only archived FormFav career starts may be used, never later/current form.')
    source=st.radio('Source cohort',['Historical discovery (post-race FormFav)',
                                   'Prospective archived (scheduled evidence only)'],
                    key='v062b_stage22_source')
    st.caption('Optional official meeting manifest CSV columns: '+', '.join(MEETING_COLUMNS)+
               '. One row per date+track; mark reviewer_verified=YES only after '
               'verifying the named meeting against an official racing authority notice.')
    up=st.file_uploader('Optional official meeting manifest (CSV)',type=['csv'],
                        key='v062b_stage22_manifest')
    if not st.button('Run Stage 2.2 source audit — no database writes',
                     key='v062b_stage22_run'):
        return
    try:
        if source.startswith('Historical'):
            from v061b_historical import server_db,read_all,RACES,FEATURES,RESULTS
            from v062b_historical import build_historical
            client=server_db(secret)
            features=read_all(client,FEATURES,
                              'batch_id,horse,horse_key,model_rank,model_score,barrier,career_starts,feature_payload')
            raw,duplicate=historical_raw_index(features)
            detail,_,prev_issues,races=build_historical(
                read_all(client,RACES,'*','race_date'),features,
                read_all(client,RESULTS,'batch_id,result_payload,result_sha256,actual_starters'))
            cohort=HISTORICAL
            raw_issues=pd.DataFrame([{'batch_id':k[0],'issue':'DUPLICATE_HISTORICAL_RAW_RUNNER'}
                                     for k in sorted(duplicate)])
        else:
            from v061_prospective import archive_client,_read_all,TABLE
            from v062b_barrier_lab import build
            client=archive_client(secret)
            archives=_read_all(client,TABLE,order='created_at')
            raw,raw_issues=prospective_raw_index(archives)
            detail,_,prev_issues=build(archives,_read_all(client,'runner_results'))
            cohort=PROSPECTIVE
        stage2,_,stage2_issues=analyse(detail,cohort)
        starters,*_=build_stage21(stage2,cohort)
        provenance=inspect_score_provenance(starters,raw,cohort)
        upload=pd.read_csv(up,dtype=str,keep_default_na=False) if up is not None else pd.DataFrame(columns=MEETING_COLUMNS)
        missing=set(MEETING_COLUMNS)-set(upload.columns)
        if missing and len(upload):
            raise ValueError('Manifest missing columns: '+', '.join(sorted(missing)))
        meetings,manifest_check=meeting_evidence(starters,upload)
        unscored,coverage=audit_summary(provenance,meetings)
    except Exception as exc:
        st.error('Stage 2.2 read-only audit blocked: '+str(exc))
        st.exception(exc)
        return
    a,b,c=st.columns(3)
    a.metric('Eligible source-audited starters',len(provenance))
    b.metric('Source-confirmed zero career starts among unscored',
             int(provenance.score_provenance_resolution.eq('UNSCORED_PROVIDER_ZERO_STARTS').sum()) if len(provenance) else 0)
    c.metric('Meeting-class sources awaiting independent validation',
             int(meetings.meeting_class.notna().sum()) if not meetings.empty else 0)
    st.dataframe(unscored,hide_index=True,use_container_width=True)
    st.dataframe(coverage,hide_index=True,use_container_width=True)
    st.warning('No official meeting class accepted as independently certified in this audit. '
               'All supplied citations require an external source verification step. '
               'No use in predictive scoring or frozen-forward evaluation.')
    label='historical' if cohort==HISTORICAL else 'prospective'
    files=[('career_starts_provenance',provenance),('unscored_explanations',unscored),
           ('meeting_class_evidence',meetings),('official_manifest_validation',manifest_check),
           ('meeting_class_coverage',coverage),('raw_archive_issues',raw_issues),
           ('stage2_issues',stage2_issues),('prior_issues',prev_issues)]
    out=BytesIO()
    with ZipFile(out,'w',compression=ZIP_DEFLATED) as z:
        for name,df in files:
            z.writestr(f'v062b_stage22_{name}_{label}.csv',df.to_csv(index=False))
    st.download_button('Download Stage 2.2 full read-only audit ZIP',out.getvalue(),
                       file_name=f'v062b_stage22_{label}_audit_bundle.zip',
                       mime='application/zip',key='v062b_stage22_zip_'+label)
