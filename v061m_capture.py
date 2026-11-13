"""v0.6.1m — pre-race confidence shadow, embedded in immutable v0.6.1 evidence.

Research-only confidence estimates. Never rerank horses, change bets, or refit
models using observed results. No retroactive backfilling.
"""
import hashlib
import json
import math
from pathlib import Path
from datetime import datetime, timezone

VERSION = 'v0.6.1m'
EXPECTED_CONFIG_HASH = 'd1c05d1e838cce3a4b2c3336e89cfaf26f0c1810b8831e5b93122a03c62428b2'
FROZEN = Path(__file__).with_name('v061m_frozen_config.json')


def canonical_sha(obj):
    return hashlib.sha256(json.dumps(obj, sort_keys=True, ensure_ascii=False,
                                     separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def utc_time(value):
    if not isinstance(value, str):
        raise ValueError('Missing timestamp')
    t = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if t.tzinfo is None or t.utcoffset() is None:
        raise ValueError('Naive timestamp')
    return t.astimezone(timezone.utc)


def frozen_config():
    conf = json.loads(FROZEN.read_text(encoding='utf-8'))
    if conf.get('config_sha256') != EXPECTED_CONFIG_HASH or conf.get('frozen_production_model') != 'v0.2':
        raise ValueError('Unexpected challenger research configuration; lock blocked')
    kinds = {'FIELD_BASELINE': 2, 'GAP_ONLY_CHALLENGER': 3, 'FIELD_GAP_INTERACTION_CHALLENGER': 4}
    if {k: len(v['coefficients']) for k, v in conf['models'].items()} != kinds:
        raise ValueError('Unexpected challenger coefficients; lock blocked')
    return conf


def logistic(z):
    return 1 / (1 + math.exp(-max(-35, min(35, z))))


def calculate(payload, timestamp=None):
    """Calculate immutable at-lock probabilities using only ranked score and field size."""
    cfg = frozen_config()
    if payload.get('model_version') != 'v0.2':
        raise ValueError('Production scorer is not v0.2')
    if payload.get('confidence_shadow_v061m'):
        raise ValueError('Confidence shadow already exists; do not rewrite')
    ranks = payload.get('rankings')
    if not isinstance(ranks, list) or len(ranks) < 2:
        raise ValueError('Require at least two complete locked runners')
    ids, scores = [], []
    for item in ranks:
        try:
            ids.append(int(item['rank']))
            val = float(item['score'])
        except (ValueError, TypeError, KeyError):
            raise ValueError('Invalid rank or missing score; shadow not recorded')
        if not math.isfinite(val):
            raise ValueError('Non-finite ranking score')
        scores.append(val)
    if sorted(ids) != list(range(1, len(ranks) + 1)):
        raise ValueError('Incomplete or duplicate ranks')
    lookup = dict(zip(ids, scores))
    gap = lookup[1] - lookup[2]
    if gap < -1e-8:
        raise ValueError('Locked rankings and score order conflict')
    now = utc_time(timestamp or datetime.now(timezone.utc).isoformat())
    schedule = utc_time(payload['scheduled_start_utc'])
    generated = utc_time(payload['source_times']['ranking_generated_utc'])
    if not generated <= now < schedule:
        raise ValueError('Shadow calculation must follow ranking and precede scheduled start')
    field = len(ranks)
    lf = math.log(field / 10)
    gu = min(20, max(-20, gap / 10))
    vectors = {
        'FIELD_BASELINE': [1, lf],
        'GAP_ONLY_CHALLENGER': [1, lf, gu],
        'FIELD_GAP_INTERACTION_CHALLENGER': [1, lf, gu, lf * gu],
    }
    probabilities = {
        name: logistic(sum(c * v for c, v in zip(cfg['models'][name]['coefficients'], vector)))
        for name, vector in vectors.items()
    }
    return {
        'version': VERSION, 'status': 'PREREGISTERED_RESEARCH_ONLY_UNCERTIFIED',
        'source': 'At-lock frozen v0.2 ranking scores, no result data',
        'registered_at_utc': now.isoformat(),
        'research_config_sha256': EXPECTED_CONFIG_HASH,
        'research_training_dataset_sha256': cfg['training_dataset_sha256'],
        'research_training_end': cfg['training_end'],
        'research_holdout_end': cfg['holdout_end'],
        'production_model': 'v0.2', 'field_size': field,
        'rank1_score': lookup[1], 'rank2_score': lookup[2], 'rank1_rank2_gap': gap,
        'probabilities': probabilities,
        'actual_off_verified': False, 'source_asof_verified': False,
        'promotion_approved': False,
    }


def attach_shadow(payload):
    """Called INSIDE archive_locked_batch before archive payload hash and INSERT.

    Postgres archived payload is a single immutable record containing both the
    v0.2 ranks and shadow probabilities. A failed calculation blocks archive;
    caller must mark already-written legacy batch unverified, as before.
    """
    updated = dict(payload)
    updated['confidence_shadow_v061m'] = calculate(updated)
    return updated


def audit_archives(archives):
    """Read-only export. No recomputation of probabilities for older races."""
    records = []
    for a in archives:
        p = a.get('payload') or {}
        shadow = p.get('confidence_shadow_v061m')
        if not shadow:
            continue
        issues = []
        try:
            if canonical_sha(p) != a.get('payload_sha256'):
                issues.append('ARCHIVE_HASH_MISMATCH')
            if shadow.get('research_config_sha256') != EXPECTED_CONFIG_HASH:
                issues.append('CONFIG_HASH_MISMATCH')
            if shadow.get('production_model') != 'v0.2':
                issues.append('PRODUCTION_MODEL_MISMATCH')
            if shadow.get('status') != 'PREREGISTERED_RESEARCH_ONLY_UNCERTIFIED':
                issues.append('INVALID_STATUS')
            t = utc_time(shadow['registered_at_utc'])
            scheduled = utc_time(a['scheduled_start_utc'])
            server = utc_time(a['created_at'])
            generated = utc_time(p['source_times']['ranking_generated_utc'])
            if not generated <= t <= server < scheduled:
                issues.append('TIME_OR_ARCHIVE_ORDER_INVALID')
            if shadow['field_size'] != len(p['rankings']):
                issues.append('FIELD_MISMATCH')
            if shadow['rank1_rank2_gap'] < 0:
                issues.append('NEGATIVE_GAP')
            if any(not (0 < float(v) < 1) for v in shadow['probabilities'].values()):
                issues.append('INVALID_PROBABILITY')
        except (KeyError, TypeError, ValueError, OverflowError):
            issues.append('INCOMPLETE_OR_INVALID_SHADOW')
        probabilities = shadow.get('probabilities') or {}
        records.append({
            'batch_id': a.get('batch_id'), 'race_date': a.get('race_date'),
            'track': a.get('track'), 'race_number': a.get('race_number'),
            'registered_at_utc': shadow.get('registered_at_utc'),
            'server_archived_at': a.get('created_at'),
            'research_config_sha256': shadow.get('research_config_sha256'),
            'field_size': shadow.get('field_size'), 'score_gap': shadow.get('rank1_rank2_gap'),
            'field_baseline_p': probabilities.get('FIELD_BASELINE'),
            'score_gap_p': probabilities.get('GAP_ONLY_CHALLENGER'),
            'field_gap_interaction_p': probabilities.get('FIELD_GAP_INTERACTION_CHALLENGER'),
            'status': 'CAPTURED_SCHEDULE_ONLY_NOT_CERTIFIED' if not issues else 'INVALID',
            'issues': ';'.join(issues),
        })
    return records


def render(secret):
    import pandas as pd
    import streamlit as st
    from v061_prospective import archive_client, _read_all, TABLE
    st.subheader('🔬 v0.6.1m — Immutable prospective confidence shadows')
    st.caption('Only future newly locked races contain at-lock challenger estimates. '
               'Prior locked races are not backfilled. Research only; no ranking or betting changes.')
    if not st.button('Audit archived v0.6.1m confidence snapshots', key='v061m_audit'):
        return
    try:
        client = archive_client(secret)
        archives = _read_all(client, TABLE, order='created_at')
        rows = audit_archives(archives)
    except Exception as exc:
        st.error('Read-only audit could not run: ' + str(exc))
        return
    st.metric('Recorded confidence shadows', len(rows))
    if not rows:
        st.info('No v0.6.1m shadows registered yet. Lock a future race after installing this version.')
        return
    frame = pd.DataFrame(rows)
    st.dataframe(frame, use_container_width=True, hide_index=True)
    st.download_button('Export registered v0.6.1m shadow records', frame.to_csv(index=False).encode(),
                       file_name='v061m_preregistered_shadows.csv', mime='text/csv')
    st.warning('This export is an at-lock evidence audit, NOT an outcome test. Actual-off and provider '
               'input-as-of certification are still outstanding; never backfill past shadows.')
