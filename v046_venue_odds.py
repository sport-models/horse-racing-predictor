"""v0.4.6 — bounded live-odds venue reconciliation and immutable provenance.

Only the FormFav-selected Sandown Lakeside race may fall back to the generic
PuntersEdge venue 'Sandown'. 'Sandown Hillside' is NEVER interchangeable.
Strict date, race number, Australian horse category, unique event, circuit,
and (for alias-only matches) scheduled-time gates are applied. Research and
production v0.2 scoring and previously archived evidence are untouched.
"""
from __future__ import annotations

from datetime import datetime, timezone
import re
from zoneinfo import ZoneInfo

import pandas as pd

VENUE_ALIAS_POLICY_VERSION = 'v046-approved-alias-1'
APPROVED_PROVIDER_VENUE_ALIASES = {'sandownlakeside': ('Sandown',)}
ALIAS_MAX_SCHEDULE_DRIFT_SECONDS = 45 * 60
ZONE = ZoneInfo('Australia/Melbourne')


def venue_key(value):
    return re.sub(r'[^a-z0-9]', '', str(value or '').casefold())


def request_venues(formfav_venue):
    """Strictly ONE extra query, for the approved Lakeside -> generic Sandown alias."""
    return [str(formfav_venue)] + list(APPROVED_PROVIDER_VENUE_ALIASES.get(venue_key(formfav_venue), ()))


def _aware_utc(value):
    if isinstance(value, datetime):
        dt = value
    elif isinstance(value, str):
        try:
            dt = datetime.fromisoformat(value.replace('Z', '+00:00'))
        except ValueError:
            return None
    else:
        return None
    return dt.astimezone(timezone.utc) if dt.tzinfo else None


def event_list(payload):
    """Mirror documented current PuntersEdge /racing/next-to-go envelopes."""
    if isinstance(payload, list):
        return payload
    if not isinstance(payload, dict):
        return []
    for key in ('races', 'events', 'data'):
        node = payload.get(key)
        if isinstance(node, list):
            return node
        if isinstance(node, dict):
            for inner in ('races', 'events'):
                if isinstance(node.get(inner), list):
                    return node[inner]
    return []


def _explicit_other_circuit(event):
    """Provider's optional circuit detail must not contradict Lakeside."""
    for field in ('circuit', 'track_variant', 'course_name', 'course', 'track_name'):
        value = event.get(field)
        if isinstance(value, str) and 'hillside' in value.casefold():
            return True
    return False


def select_event(payload, race_date, formfav_track, race_number, scheduled):
    """Return (unique event or None, status, safe diagnostic).

    The original app's exact matching is preserved except for one tightly
    scoped alias. Multiple matches fail closed even if one has the exact name.
    """
    start = _aware_utc(scheduled)
    events = event_list(payload)
    details = {
        'returned_events': len(events),
        'date_race_number_candidates': 0,
        'exact_venue_candidates': 0,
        'approved_alias_candidates': 0,
        'reject_circuit_conflicts': 0,
        'reject_alias_time_mismatch': 0,
        'provider_venues_same_date_and_race': [],
        'selected_provider_venue': None,
        'match_rule': None,
        'alias_schedule_max_offset_minutes': ALIAS_MAX_SCHEDULE_DRIFT_SECONDS // 60,
        'alias_policy_version': VENUE_ALIAS_POLICY_VERSION,
    }
    target = venue_key(formfav_track)
    allowed = {venue_key(x) for x in APPROVED_PROVIDER_VENUE_ALIASES.get(target, ())}
    matched = []
    for e in events:
        if not isinstance(e, dict):
            continue
        if str(e.get('country', '')).upper() != 'AU' or str(e.get('category', '')).lower() != 'horse':
            continue
        try:
            num = int(e.get('race_number'))
        except (TypeError, ValueError, OverflowError):
            continue
        event_start = _aware_utc(e.get('start_time'))
        if (num != int(race_number) or event_start is None
                or event_start.astimezone(ZONE).date().isoformat() != str(race_date)[:10]):
            continue
        details['date_race_number_candidates'] += 1
        provider_venue = str(e.get('venue') or '')
        if provider_venue not in details['provider_venues_same_date_and_race']:
            # Safe, bounded, venue labels only. No bookmaker prices, user data or API secrets.
            if len(details['provider_venues_same_date_and_race']) < 12:
                details['provider_venues_same_date_and_race'].append(provider_venue[:100])
        if venue_key(provider_venue) == target:
            details['exact_venue_candidates'] += 1
            matched.append((e, 'EXACT'))
        elif venue_key(provider_venue) in allowed:
            if _explicit_other_circuit(e):
                details['reject_circuit_conflicts'] += 1
                continue
            if start is None or abs((event_start - start).total_seconds()) > ALIAS_MAX_SCHEDULE_DRIFT_SECONDS:
                details['reject_alias_time_mismatch'] += 1
                continue
            details['approved_alias_candidates'] += 1
            matched.append((e, 'APPROVED_LAKESIDE_TO_GENERIC_SANDOWN_ALIAS'))
    if len(matched) > 1:
        return None, 'AMBIGUOUS_MATCHING_EVENTS', details
    if not matched:
        if details['reject_circuit_conflicts']:
            status = 'PROVIDER_CIRCUIT_CONTRADICTION'
        elif details['reject_alias_time_mismatch']:
            status = 'ALIAS_SCHEDULE_TIME_MISMATCH'
        else:
            status = 'NO_UNIQUE_MATCHED_LIVE_EVENT'
        return None, status, details
    event, rule = matched[0]
    details['selected_provider_venue'] = str(event.get('venue') or '')[:100]
    details['match_rule'] = rule
    return event, 'MATCHED_UNIQUE_EVENT', details


def _error_label(exc):
    # Never persist repr(exc), response bodies, request URL with secrets, etc.
    try:
        response = getattr(exc, 'response', None)
        code = getattr(response, 'status_code', None)
        code = int(code) if code is not None else None
    except (TypeError, ValueError, OverflowError):
        code = None
    return type(exc).__name__, code


def capture_prelock_odds(*, race_date, track, race_number, scheduled, runner_names,
                         api_key, puntersedge_request, odds_runner_quotes,
                         odds_find_race=None, utc_now=None):
    """At most TWO requests; only one approved alias may trigger the second.

    Returns (original odds quote dict, last source receipt, legacy odds status,
    diagnostic dict). Quotes exist ONLY for one unique identity-matched event.
    """
    if utc_now is None:
        utc_now = lambda: datetime.now(timezone.utc)
    trace = {
        'version': 'v0.4.6', 'alias_policy_version': VENUE_ALIAS_POLICY_VERSION,
        'selected_formfav_track': str(track), 'race_date': str(race_date)[:10],
        'race_number': int(race_number), 'attempts': [], 'selected_provider_venue': None,
        'match_rule': None, 'matched_runner_names_with_quotes': 0,
        'total_saved_runner_quotes': 0,
        'source_provenance': 'REQUEST_CAPTURED_BEFORE_SCHEDULE_ONLY_NOT_INDEPENDENT_ACTUAL_OFF',
    }
    if not api_key:
        trace['final_status'] = 'NO_ODDS_KEY'
        return {}, None, 'NO_ODDS_KEY', trace
    scheduled = _aware_utc(scheduled)
    if scheduled is None:
        raise ValueError('Must supply timezone-aware scheduled race start')
    quotes, last_receipt, final_status = {}, None, 'NO_UNIQUE_MATCHED_LIVE_EVENT'
    for request_venue in request_venues(track):
        if utc_now() >= scheduled:
            final_status = 'ODDS_AFTER_SCHEDULE'
            break
        attempt = {'venue_requested': request_venue, 'request_started_utc': utc_now().isoformat(),
                   'request_finished_utc': None, 'status': 'UNASSESSED'}
        trace['attempts'].append(attempt)
        try:
            result = puntersedge_request('/racing/next-to-go', api_key, {
                'country': 'AU', 'categories': 'horse', 'include_unresolved': 'true',
                'venue': request_venue, 'num_races': 20,
            })
        except Exception as exc:
            received = utc_now()
            last_receipt = received
            attempt['request_finished_utc'] = received.isoformat()
            error_type, http_status = _error_label(exc)
            attempt.update(status='REQUEST_EXCEPTION', exception_type=error_type,
                           http_status=http_status)
            final_status = 'ODDS_FETCH_FAILED:' + error_type
            # Stop on API failures rather than retrying auth errors/rate limits
            # or consuming further credits without a valid provider response.
            break
        received = utc_now()
        last_receipt = received
        attempt['request_finished_utc'] = received.isoformat()
        if received >= scheduled:
            attempt['status'] = 'ODDS_AFTER_SCHEDULE'
            final_status = 'ODDS_AFTER_SCHEDULE'
            break
        event, status, details = select_event(result, race_date, track, race_number, scheduled)
        # Preserve the EXISTING, tested, exact venue matching callback. That
        # callback already enforces country/category/date/race/unique identity
        # for the production app. The new resolver is ONLY needed to recognise
        # the tightly scoped Lakeside -> generic Sandown provider alias.
        # Never override an ambiguity that our new source scanner detected.
        if odds_find_race is not None and status != 'AMBIGUOUS_MATCHING_EVENTS':
            try:
                exact = odds_find_race(result, str(race_date)[:10], track, race_number)
            except Exception as exc:
                error_type, _http_status = _error_label(exc)
                attempt['status'] = 'ORIGINAL_MATCHER_EXCEPTION'
                attempt['exception_type'] = error_type
                final_status = 'ODDS_FETCH_FAILED:' + error_type
                break
            if exact is not None:
                event = exact
                status = 'MATCHED_UNIQUE_EVENT'
                details['match_rule'] = 'ORIGINAL_V061_EXACT_MATCHER'
                details['selected_provider_venue'] = str(exact.get('venue') or track)[:100]
        attempt.update(details)
        attempt['status'] = status
        final_status = status
        if status == 'AMBIGUOUS_MATCHING_EVENTS':
            # Do not search until we get the answer we want; source ambiguous.
            break
        if event is None:
            # No replay of already-locked data; second request is only the single
            # preapproved alternative query spelling.
            continue
        try:
            for horse in runner_names:
                quotes[str(horse)] = odds_runner_quotes(event, str(horse))
        except Exception as exc:
            # Bookmaker evidence is optional: preserve the frozen v0.2 lock
            # rather than blocking predictions or recording partial quote sets.
            quotes = {}
            error_type, _http_status = _error_label(exc)
            attempt['status'] = 'QUOTE_EXTRACTION_FAILED'
            attempt['exception_type'] = error_type
            final_status = 'ODDS_RUNNER_QUOTE_EXTRACTION_FAILED:' + error_type
            break
        trace['selected_provider_venue'] = details['selected_provider_venue']
        trace['match_rule'] = details['match_rule']
        trace['matched_runner_names_with_quotes'] = sum(bool(v) for v in quotes.values())
        trace['total_saved_runner_quotes'] = sum(len(v or []) for v in quotes.values())
        final_status = 'CAPTURED'  # preserve existing v061 downstream market gate
        break
    if final_status == 'MATCHED_UNIQUE_EVENT':
        raise AssertionError('Internal odds capture invariant broken')
    trace['final_status'] = final_status
    return quotes, last_receipt, final_status, trace


def odds_capture_inventory(archives):
    """Read-only export for old/new stored archives; no recalculation or API access."""
    rows = []
    for archive in archives:
        payload = archive.get('payload') or {}
        if not isinstance(payload, dict):
            payload = {}
        trace = payload.get('odds_capture_diagnostic')
        if not isinstance(trace, dict):
            trace = {}
        times = payload.get('source_times')
        times = times if isinstance(times, dict) else {}
        attempts = trace.get('attempts') if isinstance(trace.get('attempts'), list) else []
        rows.append({
            'batch_id': archive.get('batch_id'),
            'race_date': archive.get('race_date'),
            'track': archive.get('track'),
            'race_number': archive.get('race_number'),
            'original_bookmaker_capture_status': payload.get('bookmaker_capture_status'),
            'has_v046_capture_diagnostic': bool(trace),
            'original_payload_sha256': archive.get('payload_sha256'),
            'original_odds_response_received_utc': times.get('odds_response_received_utc'),
            'request_count': len(attempts) if trace else None,
            'requested_venues': '|'.join(str(x.get('venue_requested') or '') for x in attempts),
            'attempt_results': '|'.join(str(x.get('status') or '') for x in attempts),
            'matched_provider_venue': trace.get('selected_provider_venue'),
            'match_rule': trace.get('match_rule'),
            'capture_diagnostic_final_status': trace.get('final_status'),
            'total_saved_runner_quotes': trace.get('total_saved_runner_quotes'),
            'source_note': ('NEW_CAPTURE_WITH_DIAGNOSTIC' if trace
                            else 'ORIGINAL_LEGACY_ARCHIVE_NO_NEW_DIAGNOSTICS'),
        })
    return pd.DataFrame(rows, columns=[
        'batch_id', 'race_date', 'track', 'race_number', 'original_bookmaker_capture_status',
        'has_v046_capture_diagnostic', 'original_payload_sha256',
        'original_odds_response_received_utc', 'request_count', 'requested_venues',
        'attempt_results', 'matched_provider_venue', 'match_rule',
        'capture_diagnostic_final_status', 'total_saved_runner_quotes', 'source_note',
    ])
