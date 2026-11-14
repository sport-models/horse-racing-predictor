"""v0.6.1u: read-only evidence check for same-day PRE-SCHEDULE race locks.

Distinguishes *scheduled* start from independently verified actual off.
Does not turn same-day evidence into independently certified before-off evidence.
"""
from __future__ import annotations
from datetime import datetime,timezone
from hashlib import sha256
import json
import re
import unicodedata


def digest(value):
    """Identical canonical JSON encoding to v061_prospective.digest."""
    raw=json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',', ':'),allow_nan=False)
    return sha256(raw.encode('utf-8')).hexdigest()


def canonical(value):
    raw=unicodedata.normalize('NFKD',str(value or '')).encode('ascii','ignore').decode().casefold()
    return re.sub(r'[^a-z0-9]+','',raw)


def normalize_track(value):
    return re.sub(r'\s+',' ',str(value or '').casefold().strip())


def timestamp(value):
    if not value:return None
    try:
        t=datetime.fromisoformat(str(value).replace('Z','+00:00'))
        if t.tzinfo is None or t.utcoffset() is None:return None
        return t.astimezone(timezone.utc)
    except (ValueError,TypeError,OverflowError):return None


def make_archive_index(rows):
    result={}
    for a in rows:
        batch=str(a.get('batch_id') or '')
        if not batch:continue
        result.setdefault(batch,[]).append(a)
    return result


def proof_for_batch(batch_id, predictions, archive_index):
    """Confirm the exact earliest legacy batch matches its immutable pre-schedule archive.

    Returns labelled classification, not official actual-off certification.
    Missing/ambiguous/bad archive stays unverified.
    """
    archives=archive_index.get(str(batch_id),[])
    if len(archives)!=1:return 'NO_UNIQUE_ARCHIVE'
    a=archives[0];p=a.get('payload') or {}
    try:
        if a.get('model_version')!='v0.2' or p.get('model_version')!='v0.2':
            return 'WRONG_MODEL'
        if p.get('original_prediction_batch_id')!=str(batch_id):
            return 'BATCH_MISMATCH'
        if digest(p)!=a.get('payload_sha256'):
            return 'ARCHIVE_HASH_MISMATCH'
        if p.get('form_response_sha256')!=digest(p.get('form_response_raw')):
            return 'FORM_HASH_MISMATCH'
        schedule=timestamp(a.get('scheduled_start_utc'))
        saved=timestamp(a.get('created_at'))
        generated=timestamp(p.get('source_times',{}).get('ranking_generated_utc'))
        form_received=timestamp(p.get('source_times',{}).get('form_response_received_utc'))
        if not all((schedule,saved,generated,form_received)) or not form_received<=generated<=saved<schedule:
            return 'TIMING_UNVERIFIED'
        if (str(a.get('race_date'))[:10],normalize_track(a.get('track')),int(a.get('race_number'))) != (
                str(p.get('race_date'))[:10],normalize_track(p.get('track')),int(p.get('race_number'))):
            return 'RACE_ID_MISMATCH'
        if len(predictions)!=len(p.get('rankings') or []):
            return 'RANKING_COUNT_MISMATCH'
        left={canonical(x.get('horse')):x for x in predictions}
        right={canonical(x.get('horse')):x for x in p['rankings']}
        if not all(left) or set(left)!=set(right) or len(left)!=len(predictions) or len(right)!=len(predictions):
            return 'RUNNER_ID_MISMATCH'
        for h,x in left.items():
            y=right[h]
            if int(x['model_rank'])!=int(y['rank']):return 'RANK_MISMATCH'
            a_score=x.get('form_score');b_score=y.get('score')
            if (a_score is None)!=(b_score is None):return 'SCORE_MISMATCH'
            if a_score is not None and abs(float(a_score)-float(b_score))>1e-6:return 'SCORE_MISMATCH'
        return 'BEFORE_SCHEDULE_ARCHIVED_NOT_ACTUAL_OFF_CERTIFIED'
    except (ValueError,TypeError,KeyError,OverflowError):
        return 'ARCHIVE_INCOMPLETE'
