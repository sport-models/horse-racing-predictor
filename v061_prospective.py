"""v0.6.1 — prospective race evidence capture and read-only validation.

No changes to production v0.2 ranking algorithm.  New archives are append-only,
using server-side creation time and the complete provider response supplied at
lock.  Schedule times are NOT actual race-off times; rankings remain provisional
until a separately verifiable actual-off and input-as-of audit is available.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, timezone, timedelta
from hashlib import sha256
import inspect
import json
import math
import re
import uuid

import pandas as pd
import requests
import streamlit as st

from v042_results import canonical, normalize_track
from v057f_odds_integrity import bookmaker_id, iso_timestamp, valid_price

VERSION = 'v0.6.1-prospective-evidence'
TABLE = 'v061_prospective_snapshots'
FROZEN_MODEL = 'v0.2'
BOOK_PREFERENCE = ('sportsbet', 'ladbrokes_au', 'tabtouch', 'tab', 'bet365', 'unibet', 'neds')
DATE_ZONE = 'Australia/Melbourne'


def utc_now():
    return datetime.now(timezone.utc)


def utc_string(value):
    return value.astimezone(timezone.utc).isoformat() if isinstance(value, datetime) else None


def js(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)


def digest(value):
    return sha256(js(value).encode('utf-8')).hexdigest()


def started_before_schedule(source_time, scheduled):
    t = iso_timestamp(source_time)
    s = iso_timestamp(scheduled)
    return t is not None and s is not None and t < s


def safe_race_number(value):
    try:
        n = int(value)
        return n if 1 <= n <= 30 and float(value) == n else None
    except (ValueError, TypeError, OverflowError):
        return None


def _request_json(url, api_key, params):
    result = requests.get(url, headers={'X-API-Key': api_key}, params=params, timeout=30)
    result.raise_for_status()
    response = result.json()
    if not isinstance(response, (dict, list)):
        raise ValueError('FormFav returned an unsupported payload')
    return response


def _race_schedule(meetings_payload, track, number, *, get_items, get_value, meeting_name):
    normalized_track = normalize_track(str(track).replace('-', ' ').replace('_', ' '))
    matches = []
    for meeting in get_items(meetings_payload, 'meetings'):
        if not isinstance(meeting, dict):
            continue
        venue = normalize_track(str(meeting_name(meeting)).replace('-', ' ').replace('_', ' '))
        if venue != normalized_track:
            continue
        for race in get_items(meeting, 'races'):
            if not isinstance(race, dict):
                continue
            if safe_race_number(get_value(race, 'raceNumber', 'number')) == number:
                matches.append(race)
    if len(matches) != 1:
        raise ValueError(f'Expected one matching live scheduled race, found {len(matches)}; saving blocked')
    schedule_raw = matches[0].get('startTime')
    schedule = iso_timestamp(schedule_raw)
    if schedule is None:
        raise ValueError('Live meeting did not provide a timezone-aware scheduled start; saving blocked')
    return matches[0], schedule


def _scorer_hash(*functions):
    code = []
    for fn in functions:
        code.append(inspect.getsource(fn))
    return sha256('\n'.join(code).encode('utf-8')).hexdigest()


def prepare_live_snapshot(*, race_date, track, track_slug, race_number, formfav_key, puntersedge_key,
                          get_items, get_value, meeting_name, score_runner, recent_form_score,
                          win_rate, as_number, odds_find_race, odds_runner_quotes,
                          puntersedge_request):
    """Fresh uncached FormFav and PuntersEdge reads, recomputing unmodified v0.2 ranks.

    All data gathered at lock time, not cached analysis-time data.  If the form
    or schedule cannot be refreshed, the original save is BLOCKED (fail closed).
    Odds are optional but missing/uncertain price evidence remains explicitly flagged.
    """
    number = safe_race_number(race_number)
    if number is None:
        raise ValueError('Invalid race number')
    # Do not allow historical snapshots to be dressed up as prospective.
    from zoneinfo import ZoneInfo
    if date.fromisoformat(str(race_date)[:10]) < utc_now().astimezone(ZoneInfo(DATE_ZONE)).date():
        raise ValueError('Historical date: prospective capture is blocked')

    meeting_started = utc_now()
    meeting_payload = _request_json('https://api.formfav.com/v1/form/meetings', formfav_key,
                                    {'date': str(race_date)[:10], 'race_code': 'gallops', 'country': 'au'})
    meeting_completed = utc_now()
    live_race, scheduled = _race_schedule(meeting_payload, track, number, get_items=get_items,
                                         get_value=get_value, meeting_name=meeting_name)
    if meeting_completed >= scheduled:
        raise ValueError('Scheduled start time has passed. Prospective locking blocked.')

    form_started = utc_now()
    form_response = _request_json('https://api.formfav.com/v1/form', formfav_key,
                                  {'date': str(race_date)[:10], 'track': track_slug,
                                   'race': number, 'race_code': 'gallops', 'country': 'au'})
    form_completed = utc_now()
    if form_completed >= scheduled:
        raise ValueError('Form retrieval finished at/after scheduled start. Prospective locking blocked.')
    runners = get_items(form_response, 'runners')
    if not runners:
        raise ValueError('No runners returned by fresh FormFav response')
    rows = []
    for runner in runners:
        if not isinstance(runner, dict) or runner.get('scratched') is True:
            continue
        try:
            score = score_runner(runner)
        except (TypeError, ValueError, AttributeError):
            score = None
        rows.append({'Horse': get_value(runner, 'name', 'runnerName', 'horseName', default='Unknown'),
                     'Form Score': score, 'Recent Form': runner.get('form', ''),
                     'Barrier': runner.get('barrier'), 'Weight': runner.get('weight'),
                     'Jockey': runner.get('jockey'), 'Trainer': runner.get('trainer')})
    if not rows:
        raise ValueError('No active runners to rank')
    df = pd.DataFrame(rows).sort_values('Form Score', ascending=False, na_position='last').reset_index(drop=True)
    names = [canonical(x) for x in df['Horse']]
    if not all(names) or len(names) != len(set(names)):
        raise ValueError('Blank or duplicate runner names; prospective save blocked')
    df.insert(0, 'Rank', range(1, len(df) + 1))
    generated_at = utc_now()
    if generated_at >= scheduled:
        raise ValueError('Ranking generated at/after scheduled start. Prospective save blocked.')

    quotes, odds_completed, odds_status = {}, None, 'NO_ODDS_KEY'
    if puntersedge_key:
        try:
            odds_data = puntersedge_request('/racing/next-to-go', puntersedge_key,
                                            {'country': 'AU', 'categories': 'horse',
                                             'include_unresolved': 'true', 'venue': track,
                                             'num_races': 20})
            odds_completed = utc_now()
            event = odds_find_race(odds_data, str(race_date)[:10], track, number)
            if event is None:
                odds_status = 'NO_UNIQUE_MATCHED_LIVE_EVENT'
            else:
                for horse in df['Horse']:
                    quotes[str(horse)] = odds_runner_quotes(event, str(horse))
                odds_status = 'CAPTURED' if odds_completed < scheduled else 'ODDS_AFTER_SCHEDULE'
        except Exception as exc:
            odds_status = f'ODDS_FETCH_FAILED:{type(exc).__name__}'  # No API secrets in archive.
    if utc_now() >= scheduled:
        raise ValueError('Scheduled start elapsed before snapshot could be saved; no lock permitted.')
    engine_hash = _scorer_hash(score_runner, recent_form_score, win_rate, as_number)
    rankings = [{'horse': str(r['Horse']), 'rank': int(r['Rank']),
                 'score': None if pd.isna(r['Form Score']) else float(r['Form Score'])}
                for r in df.to_dict('records')]
    # Provider data, not credentials, is archived to allow future replay checks.
    payload = {
        'version': VERSION, 'model_version': FROZEN_MODEL,
        'race_date': str(race_date)[:10], 'track': str(track), 'race_number': number,
        'scheduled_start_utc': utc_string(scheduled),
        'scheduled_start_source': 'FormFav /v1/form/meetings startTime (SCHEDULED ONLY)',
        'scheduled_race_raw': live_race,
        'form_request': {'source': 'FormFav', 'endpoint': '/v1/form',
                         'params': {'date': str(race_date)[:10], 'track': str(track_slug),
                                    'race': number, 'race_code': 'gallops', 'country': 'au'}},
        'meeting_request': {'source': 'FormFav', 'endpoint': '/v1/form/meetings',
                            'params': {'date': str(race_date)[:10], 'race_code': 'gallops',
                                       'country': 'au'}},
        'odds_request': {'source': 'PuntersEdge', 'endpoint': '/racing/next-to-go',
                         'params': {'country': 'AU', 'categories': 'horse', 'venue': str(track),
                                    'num_races': 20}},
        'form_response_raw': form_response,
        'form_response_sha256': digest(form_response), 'rankings': rankings,
        'odds_quotes': quotes,
        'source_times': {'meeting_request_started_utc': utc_string(meeting_started),
                         'meeting_response_received_utc': utc_string(meeting_completed),
                         'form_request_started_utc': utc_string(form_started),
                         'form_response_received_utc': utc_string(form_completed),
                         'ranking_generated_utc': utc_string(generated_at),
                         'odds_response_received_utc': utc_string(odds_completed)},
        'bookmaker_capture_status': odds_status,
        'model_engine_source_sha256': engine_hash,
        'input_asof_provenance_verified': False,
        'official_actual_off_verified': False,
        'capture_class': 'PROSPECTIVE_SCHEDULE_ONLY_NOT_CERTIFIED',
    }
    js(payload)  # Validate JSON before ANY prediction snapshot writes.
    return {'rankings_df': df, 'bookmaker_quotes': quotes,
            'odds_snapshot_at': utc_string(odds_completed),
            'scheduled_start_utc': utc_string(scheduled), 'payload': payload}


def validate_saved_batch(saved, rankings):
    if len(saved) != len(rankings):
        raise ValueError('Legacy snapshot count does not match fresh locked rankings')
    left = {}
    for row in saved:
        key = canonical(row.get('horse'))
        if not key or key in left:
            raise ValueError('Duplicate/empty legacy saved horse key')
        left[key] = row
    for row in rankings:
        key = canonical(row['horse'])
        if key not in left or int(left[key]['model_rank']) != int(row['rank']):
            raise ValueError('Legacy snapshot rankings do not match archive')
        a, b = left[key].get('form_score'), row.get('score')
        if (a is None) != (b is None):
            raise ValueError('Legacy snapshot score missing')
        if a is not None and abs(float(a) - float(b)) > 1e-6:
            raise ValueError('Legacy snapshot score mismatch')
    return True


def ensure_new_race(db_client, race_date, track, race_number):
    """Fail BEFORE saving the legacy batch if schema is missing or race archived."""
    old = (db_client.table(TABLE).select('batch_id')
           .eq('race_date', str(race_date)[:10])
           .eq('track', str(track))
           .eq('race_number', safe_race_number(race_number))
           .limit(1).execute().data or [])
    if old:
        raise ValueError('This race already has an immutable prospective archive; no second lock allowed')
    return True


def archive_locked_batch(db_client, batch_id, prepared):
    """Single immutable Postgres INSERT for evidence; checks existing saved batch."""
    uuid.UUID(str(batch_id))
    payload = dict(prepared['payload'])
    from v061m_capture import attach_shadow
    payload = attach_shadow(payload)  # SAME immutable archive row/hash as v0.2 rankings
    if payload.get('model_version') != FROZEN_MODEL:
        raise ValueError('Incorrect model version')
    saved = db_client.table('race_predictions').select('horse,model_rank,form_score').eq(
        'prediction_batch_id', str(batch_id)).execute().data or []
    validate_saved_batch(saved, payload['rankings'])
    payload['original_prediction_batch_id'] = str(batch_id)
    payload['original_prediction_count_verified'] = len(saved)
    if not started_before_schedule(payload['source_times']['ranking_generated_utc'],
                                   payload['scheduled_start_utc']):
        raise ValueError('Rank generation is not before scheduled start')
    record = {
        'batch_id': str(batch_id), 'race_date': payload['race_date'],
        'track': payload['track'], 'race_number': payload['race_number'],
        'model_version': FROZEN_MODEL,
        'scheduled_start_utc': payload['scheduled_start_utc'],
        'form_response_received_utc': payload['source_times']['form_response_received_utc'],
        'ranking_generated_utc': payload['source_times']['ranking_generated_utc'],
        'payload_sha256': digest(payload), 'payload': payload,
    }
    response = db_client.table(TABLE).insert(record).execute()
    if not response.data or str(response.data[0].get('batch_id')) != str(batch_id):
        raise RuntimeError('Prospective evidence archive INSERT not confirmed')
    return response.data[0]


def _canonical_market_quotes(payload, starters, cutoff):
    """One consistent bookmaker with a valid pre-schedule quote for every starter."""
    candidate = defaultdict(dict)
    for horse, quotes in (payload.get('odds_quotes') or {}).items():
        hkey = canonical(horse)
        if hkey not in starters:
            continue
        for quote in (quotes or []):
            if not isinstance(quote, dict) or quote.get('stale') is not False:
                continue
            book, error = bookmaker_id(quote)
            price = valid_price(quote.get('win'))
            update = iso_timestamp(quote.get('last_update'))
            if error or price is None or update is None or not cutoff or update > cutoff:
                continue
            if book in candidate and hkey in candidate[book]:
                candidate[book][hkey] = None  # Ambiguous duplicate quote, reject this book.
            else:
                candidate[book][hkey] = price
    books = [b for b, m in candidate.items() if len(m) == len(starters) and all(p is not None for p in m.values())]
    books.sort(key=lambda b: (BOOK_PREFERENCE.index(b) if b in BOOK_PREFERENCE else 99, b))
    if not books:
        return None, {}, 'NO_COMPLETE_VERIFIABLY_TIMESTAMPED_BOOKMAKER_FIELD'
    book = books[0]
    market = candidate[book]
    min_price = min(market.values())
    favorites = [key for key, price in market.items() if price == min_price]
    if len(favorites) > 1:
        return book, market, 'TIED_PRE_RACE_MARKET_FAVOURITE'
    return book, market, None


def race_key(row):
    return (str(row.get('race_date'))[:10], normalize_track(row.get('track')), safe_race_number(row.get('race_number')))


def _result_index(rows):
    by_race = defaultdict(list)
    for row in rows:
        key = race_key(row)
        if key[-1] is not None:
            by_race[key].append(row)
    return by_race


def evaluate_one(archive, results):
    """Evaluate an immutable archive without changing predictions or results.

    Status is provisional even if recorded before the scheduled start.  Cannot
    certify an independently verified actual-off or pre-race source provenance.
    """
    p = archive.get('payload') or {}
    source_times = p.get('source_times') or {}
    output = {'batch_id': archive.get('batch_id'), 'race_date': archive.get('race_date'),
              'track': archive.get('track'), 'race_number': archive.get('race_number'),
              'model_version': archive.get('model_version'),
              'server_archived_at': archive.get('created_at'),
              'scheduled_start_utc': archive.get('scheduled_start_utc'),
              'top1_won': None, 'top3_hit': None,
              'field_size': None, 'model_top1': None, 'market_favourite': None,
              'model_vs_market_same_horse': None, 'market_favourite_won': None,
              'market_bookmaker': None, 'random_top1_expected': None,
              'random_top3_expected': None, 'status': 'UNASSESSED',
              'timing': 'UNVERIFIED', 'market_issue': None,
              'official_actual_off_verified': False, 'input_asof_verified': False,
              'certified_point_in_time': False, 'research_only': True}
    issues = []
    if not isinstance(p, dict) or not p:
        return output | {'status': 'MISSING_ARCHIVE_PAYLOAD'}, ['MISSING_ARCHIVE_PAYLOAD'], []
    try:
        if digest(p) != str(archive.get('payload_sha256')):
            issues.append('PAYLOAD_HASH_MISMATCH')
        if p.get('model_version') != FROZEN_MODEL or archive.get('model_version') != FROZEN_MODEL:
            issues.append('MODEL_VERSION_MISMATCH')
        if digest(p.get('form_response_raw')) != p.get('form_response_sha256'):
            issues.append('FORM_HASH_MISMATCH')
    except (TypeError, ValueError, OverflowError):
        issues.append('NONCANONICAL_PAYLOAD')
    scheduled = iso_timestamp(archive.get('scheduled_start_utc'))
    received = iso_timestamp(archive.get('created_at'))
    form_received = iso_timestamp(source_times.get('form_response_received_utc'))
    ranking_time = iso_timestamp(source_times.get('ranking_generated_utc'))
    odds_received = iso_timestamp(source_times.get('odds_response_received_utc'))
    if scheduled and received and form_received and ranking_time and (
        form_received <= ranking_time <= received < scheduled
        and form_received < scheduled
    ):
        output['timing'] = 'BEFORE_SCHEDULE_UNVERIFIED_ACTUAL_OFF'
    else:
        output['timing'] = 'INCOMPLETE_OR_AFTER_SCHEDULE'
        issues.append('INCOMPLETE_OR_AFTER_SCHEDULE_TIMESTAMPS')
    ranks = p.get('rankings') or []
    valid_ranks = isinstance(ranks, list) and bool(ranks)
    if valid_ranks:
        try:
            keyed = {canonical(r['horse']): r for r in ranks}
            rank_numbers = [int(r['rank']) for r in ranks]
            valid_ranks = (len(keyed) == len(ranks) and all(keyed)
                           and sorted(rank_numbers) == list(range(1, len(ranks)+1)))
        except (ValueError, KeyError, TypeError):
            valid_ranks = False
    if not valid_ranks:
        issues.append('INVALID_OR_DUPLICATE_PREDICTION_RANKINGS')
    if not isinstance(results, list) or not results:
        issues.append('NO_FINAL_RESULTS')
    positions, scratched, incomplete = {}, set(), False
    if results:
        for r in results:
            if r.get('result_status') != 'final':
                incomplete = True
            h = canonical(r.get('horse_key') or r.get('horse'))
            if not h or h in positions or h in scratched:
                incomplete = True
                continue
            if r.get('scratched') is True:
                if r.get('finishing_position') is not None:
                    incomplete = True
                scratched.add(h)
            elif r.get('scratched') is False:
                try:
                    pos = int(r['finishing_position'])
                    if pos < 1 or float(r['finishing_position']) != pos:
                        incomplete = True
                    else:
                        positions[h] = pos
                except (TypeError, ValueError):
                    incomplete = True
            else:
                incomplete = True
    if results and (incomplete or sum(p == 1 for p in positions.values()) != 1):
        issues.append('INVALID_OR_INCOMPLETE_OFFICIAL_RESULTS')
    runner_rows = []
    if valid_ranks and results:
        if not set(positions).issubset(keyed.keys()):
            issues.append('ACTUAL_STARTER_MISSING_FROM_SNAPSHOT')
        if not positions:
            issues.append('NO_ACTUAL_STARTERS')
        if not issues:
            active = sorted((r for r in ranks if canonical(r['horse']) in positions), key=lambda r: int(r['rank']))
            top = active[0]
            topkey = canonical(top['horse'])
            winner = next(k for k, pos in positions.items() if pos == 1)
            output.update(field_size=len(positions), model_top1=top['horse'],
                          top1_won=topkey == winner,
                          top3_hit=any(canonical(r['horse']) == winner for r in ranks
                                       if int(r['rank']) <= 3),
                          random_top1_expected=1/len(positions),
                          random_top3_expected=min(3, len(positions))/len(positions))
            # Capture original rank #1 scratch separately; do not claim replacement was a live bet.
            raw_top = min(ranks, key=lambda r: int(r['rank']))
            output['original_rank1_horse'] = raw_top['horse']
            output['original_rank1_scratched'] = canonical(raw_top['horse']) in scratched
            output['active_runner_selection_retrospective'] = bool(output['original_rank1_scratched'])
            if output['original_rank1_scratched']:
                issues.append('ORIGINAL_TOP1_SCRATCHED_RETROSPECTIVE_SUBSTITUTION')
            market_cutoff = min([d for d in (scheduled, received, odds_received) if d is not None], default=None)
            if p.get('bookmaker_capture_status') != 'CAPTURED' or odds_received is None:
                market_book, prices, m_issue = None, {}, 'NO_FRESH_PROVIDER_ODDS_CAPTURE'
            else:
                market_book, prices, m_issue = _canonical_market_quotes(p, set(positions), market_cutoff)
            output['market_bookmaker'] = market_book
            output['market_issue'] = m_issue
            if m_issue is None:
                fav_key = min(prices, key=prices.get)
                output['market_favourite'] = next(r['horse'] for r in active if canonical(r['horse']) == fav_key)
                output['market_favourite_won'] = fav_key == winner
                output['model_vs_market_same_horse'] = topkey == fav_key
            for r in ranks:
                h = canonical(r['horse'])
                runner_rows.append({
                    'batch_id': archive.get('batch_id'), 'race_date': archive.get('race_date'),
                    'track': archive.get('track'), 'race_number': archive.get('race_number'),
                    'horse': r['horse'], 'saved_rank': int(r['rank']), 'saved_score': r.get('score'),
                    'scratched': h in scratched, 'finished_position': positions.get(h),
                    'won': positions.get(h) == 1, 'active': h in positions,
                    'selected_market_bookmaker': market_book,
                    'market_win_odds': prices.get(h) if prices else None,
                    'selection_is_retroactive_if_top1_scratched': output['original_rank1_scratched'],
                })
    output['status'] = 'EVALUABLE_PROVISIONAL' if not issues else '|'.join(sorted(set(issues)))
    return output, sorted(set(issues)), runner_rows


def evaluate_archives(archives, runner_results):
    """Return race, runner, market, evidence, summary tables. Never modify DB."""
    results = _result_index(runner_results)
    race_rows, runner_rows, evidence_rows = [], [], []
    seen = set()
    for archive in sorted(archives, key=lambda row: str(row.get('created_at') or '')):
        k = race_key(archive)
        if k in seen:
            evidence_rows.append({'batch_id': archive.get('batch_id'), 'reason': 'DUPLICATE_PROSPECTIVE_RACE_ARCHIVE'})
            continue
        seen.add(k)
        row, issues, runners = evaluate_one(archive, results.get(k, []))
        race_rows.append(row)
        runner_rows.extend(runners)
        for issue in issues:
            evidence_rows.append({'batch_id': row['batch_id'], 'reason': issue})
        if row['timing'] != 'BEFORE_SCHEDULE_UNVERIFIED_ACTUAL_OFF':
            evidence_rows.append({'batch_id': row['batch_id'], 'reason': 'SCHEDULE_TIMING_NOT_PASSED'})
        evidence_rows.append({'batch_id': row['batch_id'], 'reason': 'OFFICIAL_ACTUAL_OFF_TIME_NOT_VERIFIED'})
        evidence_rows.append({'batch_id': row['batch_id'], 'reason': 'FORM_INPUT_AS_OF_NOT_INDEPENDENTLY_VERIFIED'})
        if row['market_issue']:
            evidence_rows.append({'batch_id': row['batch_id'], 'reason': row['market_issue']})
    races = pd.DataFrame(race_rows)
    runners = pd.DataFrame(runner_rows)
    gaps = pd.DataFrame(evidence_rows, columns=['batch_id', 'reason'])
    eligible = races[races['status'].eq('EVALUABLE_PROVISIONAL')] if not races.empty else races
    paired = eligible[eligible['market_issue'].isna() & eligible['market_favourite'].notna()].copy() if not eligible.empty else eligible.copy()
    summary = pd.DataFrame([{
        'version': VERSION, 'archived_races': len(races), 'result_complete_provisional': len(eligible),
        'paired_saved_market_races': len(paired),
        'model_top1_wins': int(eligible['top1_won'].sum()) if not eligible.empty else 0,
        'model_top1_rate': float(eligible['top1_won'].mean()) if not eligible.empty else None,
        'model_top3_hits': int(eligible['top3_hit'].sum()) if not eligible.empty else 0,
        'model_top3_rate': float(eligible['top3_hit'].mean()) if not eligible.empty else None,
        'random_top1_expected': float(eligible['random_top1_expected'].sum()) if not eligible.empty else None,
        'random_top3_expected': float(eligible['random_top3_expected'].sum()) if not eligible.empty else None,
        'market_favourite_wins_on_paired': int(paired['market_favourite_won'].sum()) if not paired.empty else 0,
        'model_wins_on_paired': int(paired['top1_won'].sum()) if not paired.empty else 0,
        'certified_point_in_time_races': 0, 'promotion_gate': 'CLOSED: actual off & input provenance not independently verified',
        'previous_18_races_included': False,
    }])
    return {'races': races, 'runner_audit': runners, 'market_paired': paired,
            'missing_evidence': gaps, 'summary': summary}


def archive_client(secret):
    """Server-side key only.  Do not place service-role credentials in client code."""
    from supabase import create_client
    url = secret('SUPABASE_URL')
    key = secret('SUPABASE_SERVICE_ROLE_KEY') or secret('SUPABASE_KEY')
    if not url or not key:
        raise RuntimeError('Supabase server-side credentials missing')
    return create_client(url, key)


def _read_all(client, table, order=None, descending=False):
    data, offset = [], 0
    while True:
        query = client.table(table).select('*')
        if order:
            query = query.order(order, desc=descending)
        rows = query.range(offset, offset+999).execute().data or []
        data.extend(rows)
        if len(rows) < 1000:
            return data
        offset += len(rows)


def render(secret):
    st.subheader('🔐 v0.6.1 — Prospective Prediction Validation')
    st.caption('New immutable, at-lock source captures only. Historical 1,040 and old 18 races are NOT backfilled. '
               'These are prospective snapshots but NOT certified actual-off times or certified input-as-of data.')
    try:
        client = archive_client(secret)
        archives = _read_all(client, TABLE, order='created_at', descending=False)
    except Exception as exc:
        st.error('v0.6.1 archive table missing or inaccessible. Run migrate_v061.sql and use a server-side '
                 'service-role Supabase secret; then reload. Database message: '+str(exc))
        return
    st.metric('Prospective archive rows', len(archives))
    if not archives:
        st.info('No prospective snapshots yet. For an upcoming race, Analyse Race and click '
                'Save & Lock All Race Predictions. A fresh at-lock archive will be captured.')
        return
    if st.button('Run v0.6.1 forward evidence audit', key='v061_run'):
        try:
            results = _read_all(client, 'runner_results', order=None)
            report = evaluate_archives(archives, results)
        except Exception as exc:
            st.error('Read-only evaluation failed: '+str(exc))
            return
        st.dataframe(report['summary'], hide_index=True, use_container_width=True)
        st.warning('PROMOTION GATE: CLOSED until independent actual-off time AND input point-in-time evidence '
                   'are verified. A scheduled start alone is not official actual-off verification.')
        st.dataframe(report['races'], hide_index=True, use_container_width=True)
        for name, frame in report.items():
            st.download_button('Download v0.6.1 '+name+' CSV',
                               frame.to_csv(index=False).encode('utf-8'),
                               file_name='v061_'+name+'.csv', mime='text/csv',
                               key='v061_csv_'+name)
