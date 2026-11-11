"""v0.6.1b - 30-day FormFav research archiver and structured feature backtester.

RESEARCH ONLY. Original v0.2 score_runner is passed in from app.py without edits.
Historical FormFav responses were retrieved after race day; the previously tested
recent-form behaviour does not establish immutable as-of history for every field.
Results are NEVER fetched during the prediction/feature capture stage.

No updates to bets, race_predictions, runner_results or v061 prospective archive.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from datetime import date, datetime, timedelta, timezone
from hashlib import sha256
import inspect
import json
import math
import re
import time
import uuid
from zoneinfo import ZoneInfo

import pandas as pd
import requests

VERSION = 'v0.6.1b-historical-research'
COHORT = 'HISTORICAL_RESEARCH_POST_RACE_RETRIEVAL'
RACES = 'v061b_hist_races'
FEATURES = 'v061b_runner_features'
RESULTS = 'v061b_hist_results'
FORM_URL = 'https://api.formfav.com/v1/form'
MEETINGS_URL = 'https://api.formfav.com/v1/form/meetings'
RESULTS_URL = 'https://api.formfav.com/v1/results'
ZONE = ZoneInfo('Australia/Melbourne')


def now_utc():
    return datetime.now(timezone.utc)


def iso(d):
    return d.isoformat()


def canonical(s):
    import unicodedata
    v = unicodedata.normalize('NFKD', str(s or '')).encode('ascii', 'ignore').decode().casefold()
    return re.sub(r'[^a-z0-9]+', '', v)


def track_key(s):
    return canonical(s)


def digest(obj):
    content = json.dumps(obj, sort_keys=True, separators=(',', ':'), ensure_ascii=False, allow_nan=False)
    return sha256(content.encode('utf-8')).hexdigest()


def numeric(value):
    if value is None or isinstance(value, bool):
        return None
    try:
        x = float(value)
    except (ValueError, TypeError, OverflowError):
        return None
    return x if math.isfinite(x) else None


def integer(value):
    x = numeric(value)
    return int(x) if x is not None and x >= 0 and int(x) == x else None


def nonempty_string(value):
    if isinstance(value, dict):
        value = value.get('name') or value.get('fullName') or value.get('full_name')
    if value is None:
        return None
    value = str(value).strip()
    return value or None


def stat_group(stats, key, prefix):
    data = stats.get(key) if isinstance(stats, dict) else None
    data = data if isinstance(data, dict) else {}
    starts = integer(data.get('starts'))
    wins = numeric(data.get('wins'))
    places = numeric(data.get('places'))
    # Never convert a missing metric to zero. Derived rate only if both present.
    rate = wins / starts if starts and wins is not None and wins <= starts else None
    return {prefix+'_starts': starts, prefix+'_wins': wins,
            prefix+'_places': places, prefix+'_win_rate': rate}


def feature_row(runner, score, rank):
    """One row per DECLARED runner, including scratched and unscored horses."""
    if not isinstance(runner, dict):
        raise ValueError('FormFav runner is not an object')
    horse = nonempty_string(runner.get('name') or runner.get('runnerName') or runner.get('horseName'))
    key = canonical(horse)
    if not horse or not key:
        raise ValueError('Missing runner identity')
    raw_form = runner.get('form')
    form = str(raw_form) if raw_form is not None else None
    digits = re.findall(r'[0-9]', form or '')
    stats = runner.get('stats') or {}
    jockey = runner.get('jockey')
    trainer = runner.get('trainer')
    jockey_stat = runner.get('jockeyStats')
    trainer_stat = runner.get('trainerStats')
    if isinstance(jockey, dict) and jockey_stat is None:
        jockey_stat = jockey
    if isinstance(trainer, dict) and trainer_stat is None:
        trainer_stat = trainer
    barrier_num = integer(runner.get('barrier'))
    weight = numeric(runner.get('weight'))
    row = {
        'horse': horse, 'horse_key': key, 'runner_number': integer(runner.get('number')),
        'scratched_in_historical_response': runner.get('scratched') is True,
        'model_rank': rank, 'model_score': score,
        'recent_form': form, 'recent_form_last5': ''.join(digits[-5:]) or None,
        'barrier': barrier_num, 'weight_kg': weight,
        'jockey': nonempty_string(jockey), 'trainer': nonempty_string(trainer),
        'jockey_stats_raw': jockey_stat, 'trainer_stats_raw': trainer_stat,
        'feature_payload': runner,
        # No after-race bookmaker prices can be described as historical pre-race quotes.
        'bookmaker_quotes': [],
        'bookmaker_quote_note': 'NO_VERIFIED_HISTORICAL_PRE_RACE_QUOTES',
    }
    for key_, prefix in [('overall', 'career'), ('track', 'track'),
                         ('distance', 'distance'), ('trackDistance', 'track_distance')]:
        row.update(stat_group(stats, key_, prefix))
    # Keep jockey and trainer PERFORMANCE in separate optional feature columns.
    # If FormFav provides no such statistics, keep NULL (never claim 0 starts).
    for person_stats, prefix in ((jockey_stat, 'jockey'), (trainer_stat, 'trainer')):
        if isinstance(person_stats, dict):
            base = person_stats.get('overall') if isinstance(person_stats.get('overall'), dict) else person_stats
            row.update(stat_group({prefix: base}, prefix, prefix))
        else:
            row.update(stat_group({}, prefix, prefix))
    return row


def api_get(url, api_key, params, requester=None):
    request = requester or requests.get
    response = request(url, headers={'X-API-Key': str(api_key)}, params=params, timeout=30)
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, (dict, list)):
        raise ValueError('FormFav response must be an object or list')
    return payload


def items(payload, key):
    if isinstance(payload, list):
        return payload
    if not isinstance(payload, dict):
        return []
    if isinstance(payload.get(key), list):
        return payload[key]
    data = payload.get('data')
    if isinstance(data, list):
        return data
    if isinstance(data, dict) and isinstance(data.get(key), list):
        return data[key]
    return []


def race_date_window(start, end, reference_date=None):
    reference_date = reference_date or datetime.now(ZONE).date()
    if not isinstance(start, date) or not isinstance(end, date) or end < start:
        raise ValueError('Invalid historical date range')
    if end >= reference_date:
        raise ValueError('Historical research date must be earlier than today (Melbourne)')
    if start < reference_date - timedelta(days=30):
        raise ValueError('FormFav historical window limited to preceding 30 days')
    if (end-start).days >= 30:
        raise ValueError('Maximum 30 dates per scan')
    return [start + timedelta(days=i) for i in range((end-start).days+1)]


def scan_meetings(dates, api_key, scope='All Australian gallops', requester=None):
    """FormFav meetings ONLY. Explicitly does not retrieve results or form."""
    planned, issues = [], []
    for day in dates:
        try:
            payload = api_get(MEETINGS_URL, api_key,
                              {'date': day.isoformat(), 'race_code': 'gallops', 'country': 'au'}, requester)
            meetings = items(payload, 'meetings')
            for m in meetings:
                if not isinstance(m, dict):
                    continue
                country = str(m.get('country') or m.get('countryCode') or '').lower()
                if country and country not in ('au', 'aus', 'australia'):
                    continue
                track = nonempty_string(m.get('track') or m.get('trackName') or m.get('venue') or m.get('name'))
                if not track:
                    issues.append({'date': str(day), 'error': 'MEETING_WITHOUT_TRACK'}); continue
                if scope == 'Melbourne primary tracks' and track_key(track) not in ('flemington','caulfield','mooneevalley'):
                    continue
                slug = nonempty_string(m.get('slug') or m.get('trackSlug')) or track.lower().replace(' ', '-')
                for race in items(m, 'races'):
                    if not isinstance(race, dict):
                        continue
                    n = integer(race.get('raceNumber', race.get('number')))
                    if n is None or n < 1 or n > 30:
                        issues.append({'date': str(day), 'error': f'BAD_RACE_NUMBER:{track}'}); continue
                    planned.append({'race_date': day.isoformat(), 'track': track,
                                    'track_key': track_key(track), 'track_slug': slug,
                                    'race_number': n, 'scheduled_start': race.get('startTime')})
        except Exception as exc:
            issues.append({'date': str(day), 'error': f'MEETINGS_FETCH_FAILED:{type(exc).__name__}:{exc}'})
    unique = {}
    for r in planned:
        key = (r['race_date'], r['track_key'], r['race_number'])
        if key not in unique:
            unique[key] = r
        else:
            issues.append({'date': r['race_date'], 'error': 'DUPLICATE_MEETING_RACE:'+str(key)})
    return list(unique.values()), issues


def make_capture(race, form_payload, retrieved_start, retrieved_end, scorer):
    """Score only FormFav /form runners via the original v0.2 function.

    No /results data is an argument or dependency here. The source's historical
    scratch flags are captured for disclosure, not silently treated as bet outcomes.
    """
    if not isinstance(form_payload, dict):
        raise ValueError('Unexpected FormFav form format')
    for name, expected in [('date', race['race_date'])]:
        observed = form_payload.get(name)
        if observed is not None and str(observed)[:10] != expected:
            raise ValueError('Form payload race date differs from requested race')
    for name in ('raceNumber', 'number'):
        if form_payload.get(name) is not None and integer(form_payload[name]) != int(race['race_number']):
            raise ValueError('Form payload race number differs from requested race')
    raw_track = form_payload.get('track')
    if raw_track and track_key(raw_track) != race['track_key']:
        raise ValueError('Form payload track differs from requested race')
    runner_list = items(form_payload, 'runners')
    if not runner_list:
        raise ValueError('No runners returned by FormFav')
    keys = [canonical(nonempty_string(r.get('name') or r.get('runnerName') or r.get('horseName')))
            if isinstance(r, dict) else '' for r in runner_list]
    if not all(keys) or len(keys) != len(set(keys)):
        raise ValueError('Missing or duplicate runner identities')
    scored = []
    for i, runner in enumerate(runner_list):
        if runner.get('scratched') is True:
            continue
        try:
            s = scorer(runner)
        except (TypeError, ValueError, AttributeError):
            s = None
        if s is not None and numeric(s) is None:
            s = None
        scored.append((i, float(s) if s is not None else None))
    if not scored:
        raise ValueError('No non-scratched runners')
    # Exact same pandas sort_values call as app.py v0.2; avoid an invented
    # tie-breaker so historical runner ranks reproduce the live application.
    ordered_frame = pd.DataFrame(scored, columns=['runner_index', 'Form Score'])
    ordered_frame = ordered_frame.sort_values('Form Score', ascending=False, na_position='last').reset_index(drop=True)
    ranks = {int(row['runner_index']): rank for rank, row in
             enumerate(ordered_frame.to_dict('records'), 1)}
    scores = dict(scored)
    rows = [feature_row(runner, scores.get(i), ranks.get(i)) for i, runner in enumerate(runner_list)]
    try:
        scorer_sha = sha256(inspect.getsource(scorer).encode('utf-8')).hexdigest()
    except (OSError, TypeError):
        raise ValueError('Cannot hash the v0.2 scoring source; capture blocked')
    batch = {
        'id': str(uuid.uuid4()), 'race_date': race['race_date'], 'track': race['track'],
        'track_key': race['track_key'], 'track_slug': race['track_slug'],
        'race_number': int(race['race_number']), 'model_version': 'v0.2', 'cohort': COHORT,
        'form_request_started_utc': iso(retrieved_start), 'form_received_utc': iso(retrieved_end),
        'scheduled_start_utc': race.get('scheduled_start'),
        'source_form_sha256': digest(form_payload), 'scorer_sha256': scorer_sha,
        'source_form_payload': form_payload, 'runner_count': len(rows),
        'predicted_count': len(ordered_frame),
    }
    # Ensure entire payload is JSON serialisable before any database write.
    json.dumps([batch, rows], allow_nan=False)
    return batch, rows


def capture_one(client, race, api_key, scorer, requester=None):
    # Read-only duplicate check, then one atomic RPC for parent+all horses.
    existing = (client.table(RACES).select('id').eq('race_date', race['race_date'])
                .eq('track_key', race['track_key']).eq('race_number', race['race_number'])
                .limit(1).execute().data or [])
    if existing:
        return {'status': 'SKIPPED_ALREADY_ARCHIVED', 'race': race, 'batch_id': existing[0]['id']}
    started = now_utc()
    payload = api_get(FORM_URL, api_key,
                      {'date': race['race_date'], 'track': race['track_slug'],
                       'race': race['race_number'], 'race_code':'gallops','country':'au'}, requester)
    received = now_utc()
    batch, runners = make_capture(race, payload, started, received, scorer)
    result = client.rpc('v061b_capture_historical', {'p_batch': batch, 'p_runners': runners}).execute()
    if str(result.data).strip('"') != batch['id']:
        raise RuntimeError('Form capture RPC did not confirm the expected batch ID; inspect Supabase before retrying')
    return {'status':'ARCHIVED_RESEARCH','race':race, 'batch_id':batch['id'],
            'all_runners': len(runners), 'ranked':batch['predicted_count']}


def parse_final_results(payload, race):
    """Mirror the existing strict FormFav result importer, without any ranking input."""
    if not isinstance(payload, dict) or payload.get('resultStatus') != 'final' or payload.get('abandoned') is True:
        raise ValueError('Results not final or race abandoned')
    if str(payload.get('date'))[:10] != race['race_date']:
        raise ValueError('Official result date differs from saved race')
    if track_key(payload.get('track')) != race['track_key']:
        raise ValueError('Official result track differs from saved race')
    if integer(payload.get('raceNumber')) != int(race['race_number']):
        raise ValueError('Official result race number differs from saved race')
    placings = payload.get('placings')
    if not isinstance(placings, list) or not placings:
        raise ValueError('Official result missing full placings array')
    declared = integer(payload.get('numberOfRunners')) if payload.get('numberOfRunners') is not None else None
    if declared is not None and declared != len(placings):
        raise ValueError('Official declared field count differs from returned field')
    keys, wins, starters = set(), 0, 0
    for item in placings:
        if not isinstance(item, dict):
            raise ValueError('Invalid result horse')
        key = canonical(item.get('name'))
        if not key or key in keys:
            raise ValueError('Missing or duplicate horse in official results')
        keys.add(key)
        scratched = item.get('scratched') is True
        p = integer(item.get('position'))
        if scratched and p is not None:
            raise ValueError('Scratched runner with finish position')
        if not scratched and (p is None or p < 1):
            raise ValueError('Starter missing finish position')
        if not scratched:
            starters += 1
            wins += (p == 1)
    # V061D_DEAD_HEAT_MULTIPLE_WINNERS: one or several joint firsts are valid.
    if wins < 1:
        raise ValueError('Final official results contain no first-place winner')
    return {'starters':starters,'declared':len(placings),
            'scratches':len(placings)-starters,
            'winners':wins,'dead_heat_first':wins>1}


def fetch_results_preview(race, api_key, requester=None):
    payload = api_get(RESULTS_URL, api_key,
                      {'date':race['race_date'],'track':race['track'],
                       'race':race['race_number'],'race_code':'gallops','country':'au'}, requester)
    meta = parse_final_results(payload, race)
    return {'batch_id':race['id'],'race':race, 'payload':payload,
            'retrieved_at':iso(now_utc()),'result_sha256':digest(payload),**meta}


def persist_results(client, preview):
    r=preview
    meta = parse_final_results(r['payload'],r['race'])
    data = {'batch_id':r['batch_id'],'result_status':'final','source':'FormFav',
            'result_retrieved_utc':r['retrieved_at'], 'result_sha256':digest(r['payload']),
            'actual_starters':meta['starters'], 'result_payload':r['payload']}
    response = client.table(RESULTS).insert(data).execute()
    if not response.data or str(response.data[0].get('batch_id')) != r['batch_id']:
        raise RuntimeError('Official result INSERT not confirmed; inspect Supabase')
    return response.data[0]


def build_reports(batches, feature_rows, results):
    """Pure offline reporting: one race per batch, results joined ONLY here."""
    by_batch=defaultdict(list)
    for row in feature_rows:
        by_batch[str(row.get('batch_id'))].append(row)
    r_by_batch={str(row['batch_id']):row for row in results}
    races=[]; runner_out=[]; issues=[]
    for batch in sorted(batches, key=lambda b:(str(b['race_date']),str(b['track_key']),int(b['race_number']))):
        bid=str(batch['id']); feat=by_batch.get(bid,[])
        res=r_by_batch.get(bid)
        outcome={'batch_id':bid, 'race_date':str(batch['race_date'])[:10],
                 'track':batch['track'],'race_number':int(batch['race_number']),
                 'source_cohort':COHORT, 'runner_count':len(feat),
                 'ranked_count':sum(x.get('model_rank') is not None for x in feat),
                 'unscored_ranked_count':sum(x.get('model_rank') is not None and x.get('model_score') is None for x in feat),
                 'actual_starters':None,'top1_won':None,'winner_in_top3':None,
                 'winner_rank':None,'winner_ranks':None,'winner_count':None,
                 'winning_horses':None,'dead_heat_first':None,
                 'model_selection':None,'model_selection_sp':None,
                 'model_win_sp_profit_10':None,'market_sp_favourite':None,
                 'market_sp_favourite_won':None,'random_top1_expectation':None,
                 'random_top3_expectation':None,'original_top1_scratched':None,
                 'missing_actual_starters':None,'status':'NO_IMPORTED_RESULTS',
                 'point_in_time_class':'RETROSPECTIVE_FORM_RETRIEVED_AFTER_RACE',
                 'certified_pre_race':False}
        if len(feat)!=int(batch.get('runner_count') or 0):
            issues.append({'batch_id':bid,'reason':'RUNNER_COUNT_OR_INCOMPLETE_CAPTURE'})
            outcome['status']='INVALID_CAPTURE'
        elif len({f.get('horse_key') for f in feat})!=len(feat):
            issues.append({'batch_id':bid,'reason':'DUPLICATE_HORSE_KEY'})
            outcome['status']='INVALID_CAPTURE'
        if res is None:
            races.append(outcome)
            continue
        payload=res.get('result_payload') or {}
        try:
            parsed=parse_final_results(payload,{'race_date':outcome['race_date'],
                                               'track_key':batch['track_key'],
                                               'race_number':outcome['race_number']})
        except Exception as exc:
            issues.append({'batch_id':bid,'reason':'INVALID_OFFICIAL_RESULT:'+str(exc)})
            outcome['status']='INVALID_RESULT';races.append(outcome);continue
        if digest(payload)!=res.get('result_sha256'):
            issues.append({'batch_id':bid,'reason':'RESULT_HASH_MISMATCH'})
            outcome['status']='INVALID_RESULT';races.append(outcome);continue
        starters={canonical(p['name']):p for p in payload['placings'] if p.get('scratched') is not True}
        scratched={canonical(p['name']) for p in payload['placings'] if p.get('scratched') is True}
        winners={k for k,v in starters.items() if integer(v.get('position'))==1}
        winner_count=len(winners)
        outcome['winner_count']=winner_count
        outcome['dead_heat_first']=winner_count>1
        outcome['winning_horses']=' | '.join(sorted(
            p['name'] for p in payload['placings']
            if p.get('scratched') is not True and integer(p.get('position'))==1))
        prediction_keys={f['horse_key'] for f in feat if f.get('model_rank') is not None}
        missing=sorted(set(starters)-prediction_keys)
        outcome['actual_starters']=len(starters)
        # Probability that a uniformly chosen runner (or top-3 set) includes
        # at least one of the W first-place finishers; valid for dead-heats.
        n=len(starters)
        m=min(3,n)
        outcome['random_top1_expectation']=winner_count/n
        outcome['random_top3_expectation']=(
            1 - math.comb(n-winner_count,m)/math.comb(n,m)
            if n-winner_count >= m else 1.0)
        outcome['missing_actual_starters']=len(missing)
        if outcome['status']=='INVALID_CAPTURE' or missing:
            outcome['status']='INCOMPLETE_PREDICTIONS'
            issues.append({'batch_id':bid,'reason':'ACTUAL_STARTERS_MISSING:'+','.join(missing)})
            races.append(outcome);continue
        ranked=sorted((f for f in feat if f.get('model_rank') is not None),key=lambda f:int(f['model_rank']))
        rank_map={f['horse_key']:int(f['model_rank']) for f in ranked}
        original_top=ranked[0]
        outcome['original_top1_scratched']=original_top['horse_key'] in scratched
        outcome['model_selection']=original_top['horse']
        win_ranks=sorted(rank_map[k] for k in winners if k in rank_map)
        outcome['winner_rank']=win_ranks[0] if win_ranks else None  # best winner rank
        outcome['winner_ranks']=','.join(str(rank) for rank in win_ranks)
        outcome['winner_in_top3']=any(rank<=3 for rank in win_ranks)
        if original_top.get('model_score') is None:
            outcome['status']='UNSCORED_TOP1'
            issues.append({'batch_id':bid,'reason':'UNSCORED_TOP1_EXCLUDE_FROM_WIN_RATE'})
        elif original_top['horse_key'] in scratched:
            outcome['status']='ORIGINAL_TOP1_SCRATCHED'
            issues.append({'batch_id':bid,'reason':'ORIGINAL_TOP1_SCRATCHED_DO_NOT_SUBSTITUTE'})
        else:
            outcome['top1_won']=original_top['horse_key'] in winners
            sp=numeric(starters.get(original_top['horse_key'],{}).get('startingPrice'))
            if sp is not None and sp>1:
                outcome['model_selection_sp']=sp
                # Retrospective SP proxy, not a real settled bet. For a dead
                # heat, divide the winning return by co-winners (pro-rata
                # face value). The original $10 is the cost in all cases.
                gross=10*sp/winner_count if outcome['top1_won'] else 0
                outcome['model_win_sp_profit_10']=gross-10
            outcome['status']='EVALUABLE_RETROSPECTIVE_RESEARCH'
        sp_candidates=[]
        for h, item in starters.items():
            sp=numeric(item.get('startingPrice'))
            if sp is not None and sp>1:
                sp_candidates.append((sp,h))
        if len(sp_candidates)==len(starters):
            best=min(p for p,_ in sp_candidates)
            ties=[h for p,h in sp_candidates if p==best]
            if len(ties)==1:
                outcome['market_sp_favourite']=next((f['horse'] for f in feat if f['horse_key']==ties[0]),ties[0])
                outcome['market_sp_favourite_won']=ties[0] in winners
            else:
                issues.append({'batch_id':bid,'reason':'TIED_STARTING_PRICE_FAVOURITE'})
        for f in feat:
            pos=integer(starters[f['horse_key']]['position']) if f['horse_key'] in starters else None
            runner_out.append({
                'batch_id':bid,'race_date':outcome['race_date'],'track':batch['track'],
                'race_number':outcome['race_number'],'horse':f['horse'],
                'model_rank':f.get('model_rank'),'model_score':f.get('model_score'),
                'jockey':f.get('jockey'),'trainer':f.get('trainer'),
                'finishing_position':pos, 'official_scratched':f['horse_key'] in scratched,
                'win':pos==1 if pos is not None else None,
                'starting_price':numeric(starters[f['horse_key']].get('startingPrice')) if pos else None,
            })
        races.append(outcome)
    df=pd.DataFrame(races)
    # Chronological, date-scoped RESEARCH slices only. They are not an untouched
    # independent holdout and cannot certify point-in-time predictive performance.
    dates=sorted(set(df['race_date'].astype(str))) if not df.empty else []
    if len(dates) >= 2:
        cutoff=dates[max(0,min(len(dates)-2,int(len(dates)*.7)-1))]
        df['research_slice']=df['race_date'].apply(lambda d: 'EARLIER_RESEARCH' if str(d)<=cutoff else 'LATER_RESEARCH')
    elif not df.empty:
        df['research_slice']='RESEARCH_UNSPLIT'
    evaluate=df[df['status']=='EVALUABLE_RETROSPECTIVE_RESEARCH'] if not df.empty else df
    win_n=int(evaluate['top1_won'].sum()) if not evaluate.empty else 0
    top3_n=int(evaluate['winner_in_top3'].sum()) if not evaluate.empty else 0
    sp=evaluate.dropna(subset=['model_win_sp_profit_10']) if not evaluate.empty else evaluate
    summary=pd.DataFrame([{
        'version':VERSION,'archived_races':len(df),'runners_saved':len(feature_rows),
        'official_results_imported':len(results),'evaluable_research_races':len(evaluate),
        'first_place_dead_heats_imported':int(df['dead_heat_first'].fillna(False).sum()) if not df.empty else 0,
        'first_place_dead_heats_evaluable':int(evaluate['dead_heat_first'].fillna(False).sum()) if not evaluate.empty else 0,
        'top1_wins':win_n,'top1_rate':win_n/len(evaluate) if len(evaluate) else None,
        'winner_in_top3':top3_n,'top3_rate':top3_n/len(evaluate) if len(evaluate) else None,
        'random_top1_expected_wins':evaluate['random_top1_expectation'].sum() if not evaluate.empty else None,
        'random_top3_expected_hits':evaluate['random_top3_expectation'].sum() if not evaluate.empty else None,
        'sp_research_races':len(sp),'sp_research_net_profit_10':sp['model_win_sp_profit_10'].sum() if len(sp) else None,
        'sp_research_roi':sp['model_win_sp_profit_10'].sum()/(10*len(sp)) if len(sp) else None,
        'sp_return_basis':'RETROSPECTIVE_SP_PROXY_PRO_RATA_DEAD_HEAT_NOT_SETTLED_BETS',
        'pre_race_certified_count':0,
        'promotion_status':'CLOSED_HISTORICAL_AFTER_RACE_RETRIEVAL',
    }])
    research_slices=[]
    if not evaluate.empty:
        for segment,part in evaluate.groupby('research_slice',dropna=False):
            research_slices.append({'research_slice':segment,'races':len(part),
                                    'model_top1_wins':int(part['top1_won'].sum()),
                                    'model_top1_rate':float(part['top1_won'].mean()),
                                    'winner_top3_hits':int(part['winner_in_top3'].sum()),
                                    'winner_top3_rate':float(part['winner_in_top3'].mean()),
                                    'certified_pre_race':False})
    return {'races':df,'runner_outcomes':pd.DataFrame(runner_out),
            'summary':summary,'research_slices':pd.DataFrame(research_slices),
            'issues':pd.DataFrame(issues,columns=['batch_id','reason'])}


def read_all(client, table, columns='*', order=None):
    output=[];offset=0
    while True:
        q=client.table(table).select(columns)
        if order:q=q.order(order,desc=False)
        data=q.range(offset,offset+499).execute().data or []
        output.extend(data)
        if len(data)<500:return output
        offset+=len(data)


def server_db(secret):
    from supabase import create_client
    key=secret('SUPABASE_SERVICE_ROLE_KEY')
    url=secret('SUPABASE_URL')
    if not key or not url:
        raise RuntimeError('Set SUPABASE_SERVICE_ROLE_KEY and SUPABASE_URL in server-side Streamlit Secrets')
    return create_client(url,key)


def render(secret, score_runner):
    import streamlit as st
    st.subheader('📚 v0.6.1b — Historical Backtesting & Runner Feature Archive')
    st.caption('Research cohort only: FormFav responses retrieved after the race, scored without fetching results. '
               'This does not alter v0.2, betting data, existing predictions or v0.6.1 prospective captures.')
    st.warning('Historical recent form was checked on example races, but complete field-wide as-of provenance '
               'is not independently certified. Do not promote model changes based on these data alone.')
    try:
        client=server_db(secret)
        existing=read_all(client,RACES,
            'id,race_date,track,track_key,track_slug,race_number,runner_count,predicted_count,form_received_utc,source_form_sha256,scorer_sha256',
            'race_date')
    except Exception as exc:
        st.error('Historical table not accessible. Run migrate_v061b.sql and configure server-side service role: '+str(exc))
        return
    st.metric('Historical races archived',len(existing))
    api_key=secret('FORMFAV_API_KEY')
    if not api_key:
        st.error('FORMFAV_API_KEY is required');return
    tabs=st.tabs(['1. Discover and archive form','2. Import official results separately','3. Research dashboard / exports'])
    with tabs[0]:
        today=datetime.now(ZONE).date()
        start_min=today-timedelta(days=30)
        yesterday=today-timedelta(days=1)
        col1,col2=st.columns(2)
        start=col1.date_input('From (historical)',value=max(start_min,today-timedelta(days=7)),min_value=start_min,max_value=yesterday,key='v061b_start')
        end=col2.date_input('Through (historical)',value=yesterday,min_value=start_min,max_value=yesterday,key='v061b_end')
        scope=st.selectbox('Meetings', ['All Australian gallops','Melbourne primary tracks'],key='v061b_scope')
        if st.button('Scan meetings — no results fetched',key='v061b_scan'):
            try:
                days=race_date_window(start,end,today)
                with st.spinner('Reading FormFav meeting schedules for selected historical dates...'):
                    plan,scan_issues=scan_meetings(days,api_key,scope)
                st.session_state['v061b_plan']=plan
                st.session_state['v061b_index']=0
                st.session_state['v061b_log']=[]
                st.session_state['v061b_scan_issues']=scan_issues
            except Exception as exc:
                st.error('Historical scan rejected: '+str(exc))
        plan=st.session_state.get('v061b_plan',[])
        if plan:
            i=st.session_state.get('v061b_index',0)
            current_keys={(str(row['race_date'])[:10],row['track_key'],int(row['race_number'])) for row in existing}
            remain=sum(1 for row in plan[i:] if (row['race_date'],row['track_key'],int(row['race_number'])) not in current_keys)
            st.info(f'Discovered {len(plan)} races. Processed {i}; ~{remain} remain unarchived in this plan.')
            st.download_button('Download discovery plan CSV',pd.DataFrame(plan).to_csv(index=False).encode(),
                               file_name='v061b_discovery_plan.csv',mime='text/csv',key='v061b_plan_export')
            quantity=st.select_slider('Races per click (avoid API rate limits)',options=[5,10,15,20],value=10,key='v061b_batchsize')
            if i<len(plan) and st.button('Archive next batch — form only, frozen v0.2',type='primary',key='v061b_capture'):
                new_log=[]
                with st.spinner('Capturing each race and every declared runner without result lookups...'):
                    for race in plan[i:i+quantity]:
                        try:
                            outcome=capture_one(client,race,api_key,score_runner)
                            new_log.append({'race':f"{race['race_date']} {race['track']} R{race['race_number']}",
                                            'status':outcome['status'],'info':str(outcome.get('all_runners',''))})
                        except Exception as exc:
                            new_log.append({'race':f"{race['race_date']} {race['track']} R{race['race_number']}",
                                            'status':'FAILED_RETRYABLE', 'info':f'{type(exc).__name__}:{exc}'[:400]})
                        time.sleep(0.15)
                st.session_state['v061b_index']=min(len(plan),i+quantity)
                st.session_state['v061b_log']=st.session_state.get('v061b_log',[])+new_log
                st.rerun()
            logs=st.session_state.get('v061b_log',[])
            if logs:
                st.dataframe(pd.DataFrame(logs).tail(100),hide_index=True,use_container_width=True)
                st.download_button('Download archive run log',pd.DataFrame(logs).to_csv(index=False).encode(),
                                   file_name='v061b_archive_log.csv',mime='text/csv',key='v061b_log_download')
                if st.button('Retry failed races (same form-only operation)',key='v061b_retry'):
                    failed_labels={x['race'] for x in logs if x['status']=='FAILED_RETRYABLE'}
                    retry=[r for r in plan if f"{r['race_date']} {r['track']} R{r['race_number']}" in failed_labels]
                    st.session_state['v061b_plan']=retry
                    st.session_state['v061b_index']=0
                    st.session_state['v061b_log']=[]
                    st.rerun()
            scan_issues=st.session_state.get('v061b_scan_issues',[])
            if scan_issues:
                st.warning(f'{len(scan_issues)} meeting scan issues')
                st.dataframe(pd.DataFrame(scan_issues),hide_index=True)
    with tabs[1]:
        st.caption('Results are fetched ONLY after a historical feature capture exists; inspect and confirm before saving.')
        try:
            imported=read_all(client,RESULTS,'id,batch_id,result_retrieved_utc,actual_starters')
        except Exception as exc:
            st.error('Results table unavailable: '+str(exc));imported=[]
        done={str(r['batch_id']) for r in imported}
        # Exclusions are deliberately separate from immutable historical captures/results.
        # A failed API response is NOT evidence of abandonment: the operator chooses a reason.
        try:
            exclusions=read_all(client,'v061b_result_exclusions','batch_id,reason,note,marked_at')
            exclusion_store_ready=True
        except Exception as exc:
            exclusions=[]
            exclusion_store_ready=False
            st.warning('Result-exclusion table not available. Run migrate_v061c_result_exclusions.sql in Supabase SQL Editor. '+str(exc))
        excluded_ids={str(x['batch_id']) for x in exclusions}
        pending=[r for r in existing if str(r['id']) not in done and str(r['id']) not in excluded_ids]
        st.metric('Archived races awaiting official results',len(pending))
        st.caption(f'Excluded/unresolved races: {len(exclusions)} (separate from successfully imported results).')
        if exclusion_store_ready:
            with st.expander('Manage abandoned / unavailable results (reversible)', expanded=False):
                if pending:
                    exclude_options={str(r['id']):r for r in pending}
                    exclude_ids=st.multiselect(
                        'Select races to exclude from pending results',list(exclude_options),
                        format_func=lambda k: f"{exclude_options[k]['race_date']} {exclude_options[k]['track']} R{exclude_options[k]['race_number']}",
                        key='v061c_exclude_ids')
                    exclude_reason=st.selectbox('Exclusion reason',[
                        'RESULT_UNAVAILABLE','CONFIRMED_ABANDONED','OTHER_UNRESOLVED'],
                        format_func=lambda x: {
                            'RESULT_UNAVAILABLE':'Official results unavailable / API error (not confirmed abandoned)',
                            'CONFIRMED_ABANDONED':'Confirmed abandoned (verified externally)',
                            'OTHER_UNRESOLVED':'Other unresolved issue'
                        }[x],key='v061c_exclude_reason')
                    exclude_note=st.text_input('Optional evidence / notes',key='v061c_exclude_note')
                    exclude_confirm=st.checkbox('Exclude selected races from pending results; preserve archived data',
                                                key='v061c_exclude_confirm')
                    if st.button('Exclude selected races',disabled=not(exclude_ids and exclude_confirm),
                                 key='v061c_exclude_button'):
                        saved=0
                        for bid in exclude_ids:
                            try:
                                client.table('v061b_result_exclusions').insert({
                                    'batch_id':bid,'reason':exclude_reason,'note':exclude_note
                                }).execute()
                                saved+=1
                            except Exception as exc:
                                st.error(f'Unable to exclude {bid}: {exc}')
                        st.success(f'Excluded {saved} races. Original archived data retained.')
                        if saved:
                            st.session_state.pop('v061b_result_previews',None)
                            st.rerun()
                if exclusions:
                    excluded_options={str(r['batch_id']):r for r in exclusions}
                    restore_ids=st.multiselect(
                        'Restore previously excluded races to pending results',list(excluded_options),
                        format_func=lambda k: f"{k} — {excluded_options[k]['reason']}",
                        key='v061c_restore_ids')
                    if st.button('Restore selected races',disabled=not bool(restore_ids),key='v061c_restore_button'):
                        restored=0
                        for bid in restore_ids:
                            try:
                                client.table('v061b_result_exclusions').delete().eq('batch_id',bid).execute()
                                restored+=1
                            except Exception as exc:
                                st.error(f'Unable to restore {bid}: {exc}')
                        st.success(f'Restored {restored} races to pending results.')
                        if restored:
                            st.rerun()
                if exclusions:
                    st.dataframe(pd.DataFrame(exclusions),hide_index=True,use_container_width=True)
        if pending:
            pending.sort(key=lambda x:(x['race_date'],x['track'],x['race_number']))
            options={str(p['id']):p for p in pending}
            selected=st.multiselect('Select races (max 20 per fetch)',list(options),
                                    default=list(options)[:min(10,len(options))],
                                    format_func=lambda k:f"{options[k]['race_date']} {options[k]['track']} R{options[k]['race_number']}",
                                    key='v061b_result_selection')
            if st.button('Fetch and preview FINAL official results',key='v061b_result_preview'):
                preview=[];problems=[]
                if not selected or len(selected)>20:
                    st.error('Choose 1–20 races')
                else:
                    for bid in selected:
                        r=options[bid]
                        try:preview.append(fetch_results_preview(r,api_key))
                        except Exception as exc: problems.append({'race':f"{r['race_date']} {r['track']} R{r['race_number']}",
                                                                  'issue':f'{type(exc).__name__}:{exc}'[:400]})
                    st.session_state['v061b_result_previews']=preview
                    st.session_state['v061b_result_errors']=problems
            previews=st.session_state.get('v061b_result_previews',[])
            problems=st.session_state.get('v061b_result_errors',[])
            if problems: st.dataframe(pd.DataFrame(problems),hide_index=True)
            if previews:
                if set(p['batch_id'] for p in previews) != set(selected):
                    st.warning('Selection changed since preview. Fetch and preview the new selection before saving.')
                    previews=[]
            if previews:
                st.dataframe(pd.DataFrame([{'date':p['race']['race_date'],'track':p['race']['track'],
                                            'race':p['race']['race_number'],'declared':p['declared'],
                                            'starters':p['starters'],'scratches':p['scratches'],
                                            'first_place_winners':p['winners'],
                                            'dead_heat_first':p['dead_heat_first']}
                                           for p in previews]),hide_index=True,use_container_width=True)
                st.warning('Review the full official field for each race. No results are saved until explicit confirmation.')
                for p in previews:
                    with st.expander(f"{p['race']['race_date']} {p['race']['track']} R{p['race']['race_number']} — full results"):
                        st.dataframe(pd.DataFrame([{'Horse':x.get('name'),'Position':x.get('position'),
                                                    'Scratched':x.get('scratched'),'SP':x.get('startingPrice')}
                                                   for x in p['payload']['placings']]),hide_index=True)
                confirmed=st.checkbox('I reviewed all official results and approve saving to the separate historical results table',
                                     key='v061b_confirm_results')
                if st.button('Save reviewed historical results',disabled=not confirmed,type='primary',key='v061b_save_results'):
                    log=[]
                    for p in previews:
                        try:
                            old=(client.table(RESULTS).select('batch_id').eq('batch_id',p['batch_id']).limit(1).execute().data or [])
                            if old:
                                log.append({'race':p['batch_id'],'status':'SKIPPED_ALREADY_IMPORTED'});continue
                            persist_results(client,p)
                            log.append({'race':p['batch_id'],'status':'SAVED'})
                        except Exception as exc:
                            log.append({'race':p['batch_id'],'status':'FAILED', 'reason':str(exc)[:400]})
                    st.session_state['v061b_result_previews']=[]
                    st.dataframe(pd.DataFrame(log),hide_index=True)
                    st.success(f"Saved {sum(x['status']=='SAVED' for x in log)} result races. Existing bet settlements untouched.")
    with tabs[2]:
        if st.button('Run historical research backtest',key='v061b_run_analysis'):
            try:
                raced=read_all(client,RACES,
                    'id,race_date,track,track_key,track_slug,race_number,runner_count,predicted_count,source_form_sha256,scorer_sha256,cohort',
                    'race_date')
                feats=read_all(client,FEATURES,
                    'batch_id,horse,horse_key,runner_number,scratched_in_historical_response,model_rank,model_score,recent_form,recent_form_last5,'
                    'career_starts,career_wins,career_places,career_win_rate,track_starts,track_wins,track_places,track_win_rate,'
                    'distance_starts,distance_wins,distance_places,distance_win_rate,track_distance_starts,track_distance_wins,'
                    'track_distance_places,track_distance_win_rate,barrier,weight_kg,jockey,trainer,'
                    'jockey_starts,jockey_wins,jockey_places,jockey_win_rate,trainer_starts,trainer_wins,'
                    'trainer_places,trainer_win_rate,bookmaker_quote_note')
                result_data=read_all(client,RESULTS,'batch_id,result_payload,result_sha256,actual_starters')
                reports=build_reports(raced,feats,result_data)
                st.session_state['v061b_reports']=reports
                st.session_state['v061b_feature_export']=pd.DataFrame(feats)
            except Exception as exc:
                st.error('Backtest query failed: '+str(exc))
        reports=st.session_state.get('v061b_reports')
        if reports:
            st.warning('RESEARCH ONLY: retrieved after race, no certified pre-race market quotes, '
                       'and no fresh untouched holdout. Historical SP ROI is retrospective, not an executable betting result.')
            st.dataframe(reports['summary'],hide_index=True,use_container_width=True)
            if not reports['races'].empty:
                st.dataframe(reports['races'].tail(50),hide_index=True,use_container_width=True)
            for name,df in [*reports.items(),('structured_features',st.session_state['v061b_feature_export'])]:
                st.download_button('Download '+name+' CSV',df.to_csv(index=False).encode('utf-8'),
                                   file_name='v061b_'+name+'.csv',mime='text/csv',key='v061b_export_'+name)
