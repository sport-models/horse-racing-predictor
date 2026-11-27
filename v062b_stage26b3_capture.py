"""Stage 2.6B3: production capture-only sidecar for prospective research.

Runs strictly after a successful original archive, before scheduled start.
Read-only against that archived evidence; one append-only sidecar insert.
No odds/results, experimental scores, dashboard, or model changes.
"""
from __future__ import annotations
import hashlib
import json
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any
import re
import unicodedata

VERSION = '2.6B3-PROD-CAPTURE-ONLY'
TABLE = 'v062b_stage26b3_shadow_captures'


def canon(s):
    v=unicodedata.normalize('NFKD',str(s or '')).encode('ascii','ignore').decode().casefold()
    return re.sub(r'[^a-z0-9]+','',v)


def serialized(v):
    return json.dumps(v,sort_keys=True,separators=(',', ':'),ensure_ascii=False,allow_nan=False)


def sha(v):
    return hashlib.sha256(serialized(v).encode()).hexdigest()


def timestamp(v):
    if not isinstance(v,str) or not v.strip():return None
    try:
        x=datetime.fromisoformat(v.replace('Z','+00:00'))
        return x.astimezone(timezone.utc) if x.tzinfo else None
    except ValueError:return None


def starts_count(v):
    if isinstance(v,bool) or v is None:return None
    try:
        d=Decimal(str(v))
        if not d.is_finite() or d<0 or d!=d.to_integral_value():return None
        return int(d)
    except (ValueError,TypeError):return None


def classification(r):
    sources=[]
    for key in ('careerStarts','career_starts','starts'):
        if key in r:
            v=starts_count(r[key]);
            if v is not None:sources.append((key,v))
    stats=r.get('stats')
    if isinstance(stats,dict) and isinstance(stats.get('overall'),dict):
        v=starts_count(stats['overall'].get('starts'))
        if v is not None:sources.append(('stats.overall.starts',v))
    if len({n for _,n in sources})>1:return ('CONFLICTING_COUNTS',None,[p for p,n in sources])
    if not sources:return ('UNKNOWN_STARTS',None,[])
    n=sources[0][1]
    return ('PROVIDER_REPORTED_ZERO_NOT_INDEPENDENTLY_CERTIFIED' if n==0 else 'EXPERIENCED',n,[p for p,_ in sources])


def _barrier(r,ranking,field_size):
    v=r.get('barrier',ranking.get('barrier'))
    n=starts_count(v)
    if n is None or not (1<=n<=40):return None,None
    return n,(n-1)/max(field_size-1,1)


def build(archived:dict, now:datetime|None=None)->dict:
    """Fail closed: timestamp, SHA and archived identity checks before shadow construction."""
    now=now or datetime.now(timezone.utc)
    if now.tzinfo is None:raise ValueError('Capture time must be timezone-aware')
    now=now.astimezone(timezone.utc)
    p=archived.get('payload')
    if not isinstance(p,dict):raise ValueError('Missing immutable original payload')
    if sha(p)!=archived.get('payload_sha256'):raise ValueError('Original archive digest mismatch')
    raw=p.get('form_response_raw')
    if not isinstance(raw,dict) or not isinstance(raw.get('runners'),list):raise ValueError('Missing archived at-lock runners')
    if sha(raw)!=p.get('form_response_sha256'):raise ValueError('Original FormFav response digest mismatch')
    rankings=p.get('rankings')
    if not isinstance(rankings,list) or not rankings:raise ValueError('Missing original locked rankings')
    schedule=timestamp(archived.get('scheduled_start_utc') or p.get('scheduled_start_utc'))
    received=timestamp((p.get('source_times') or {}).get('form_response_received_utc'))
    generated=timestamp((p.get('source_times') or {}).get('ranking_generated_utc'))
    archived_at=timestamp(archived.get('created_at'))
    if not all((schedule,received,generated,archived_at)):raise ValueError('Missing archival timestamps')
    if not (received<=generated<=archived_at<=now<schedule):
        raise ValueError('Cannot create a new prospective capture after scheduled start or from inconsistent recorded times')
    if str(archived.get('batch_id')) != str(p.get('original_prediction_batch_id')):
        raise ValueError('Original prediction batch identity mismatch')
    if archived.get('model_version')!='v0.2' or p.get('model_version')!='v0.2':
        raise ValueError('Only frozen v0.2 archives are supported')
    src={}; dup=set()
    for r in raw['runners']:
        if not isinstance(r,dict):continue
        key=canon(r.get('name') or r.get('runnerName') or r.get('horseName'))
        if not key:continue
        if key in src:dup.add(key)
        else:src[key]=r
    keys=[canon(r.get('horse')) for r in rankings if isinstance(r,dict)]
    if len(keys)!=len(rankings) or len(set(keys))!=len(keys) or not all(keys):
        raise ValueError('Invalid or duplicate ranked horse identities')
    if dup.intersection(keys) or any(k not in src for k in keys):
        raise ValueError('Ambiguous or missing horse match to original form')
    rows=[]; field_size=len(keys)
    for ranking,key in zip(rankings,keys):
        original=src[key]
        status,n,evidence=classification(original)
        barrier,fraction=_barrier(original,ranking,field_size)
        rows.append({'horse':ranking['horse'],'original_rank':ranking.get('rank'),
                     'original_score':ranking.get('score'),'identity_status':status,
                     'career_starts_at_lock':n,'career_starts_source_keys':evidence,
                     'jockey_at_lock':original.get('jockey') or ranking.get('jockey'),
                     'trainer_at_lock':original.get('trainer') or ranking.get('trainer'),
                     'barrier_at_lock':barrier,'barrier_position_fraction':fraction,
                     'weight_at_lock':original.get('weight') or ranking.get('weight'),
                     'independent_identity_certified':False,'trial_evidence_certified':False,
                     'production_prediction_eligible':False})
    body={'version':VERSION,'mode':'PROSPECTIVE_SHADOW_CAPTURE_RESEARCH_ONLY',
          'original_batch_id':str(archived['batch_id']),
          'original_archive_sha256':archived['payload_sha256'],
          'race_date':archived.get('race_date'),'track':archived.get('track'),
          'race_number':archived.get('race_number'),
          'scheduled_start_utc':schedule.isoformat(),
          'original_form_response_received_utc':received.isoformat(),
          'original_rank_generated_utc':generated.isoformat(),
          'original_archive_created_utc':archived_at.isoformat(),
          'shadow_created_utc':now.isoformat(),
          'actual_off_certified':False,'input_asof_certified':False,
          'race_class_at_lock':raw.get('raceClass'),'distance_at_lock':raw.get('distance'),
          'condition_at_lock':raw.get('condition'),'active_field_size':field_size,
          'score_policy':'NO_SCORING_CAPTURE_ONLY',
          'runners':rows,'no_odds_or_results_used':True,'v02_predictions_unchanged':True}
    return body


def persist(db_client,archive,now=None):
    """Single append-only write. Unique source batch prevents reruns/backfill."""
    body=build(archive,now)
    record={'original_batch_id':body['original_batch_id'],
            'race_date':body['race_date'],'track':body['track'],
            'race_number':body['race_number'],
            'scheduled_start_utc':body['scheduled_start_utc'],
            'source_archive_sha256':body['original_archive_sha256'],
            'capture_sha256':sha(body),'payload':body}
    result=db_client.table(TABLE).insert(record).execute()
    if not result.data or str(result.data[0].get('original_batch_id'))!=body['original_batch_id']:
        raise RuntimeError('Shadow research capture INSERT not confirmed')
    return body

