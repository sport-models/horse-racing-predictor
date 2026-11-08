"""v0.5.7f — explicit bookmaker quote capture + backward-compatible READ-ONLY parsing.

No Supabase write logic. Never substitute race-average places paid for one book's terms.
"""
from __future__ import annotations

from datetime import datetime, timezone
import math
import re

PREFERRED_BOOKMAKERS = (
    'sportsbet', 'ladbrokes_au', 'tabtouch', 'tab', 'bet365',
    'betright', 'unibet', 'neds', 'pointsbetau',
)


def valid_price(value):
    if value is None or isinstance(value, bool):
        return None
    try:
        x = float(value)
    except (ValueError, TypeError, OverflowError):
        return None
    return x if math.isfinite(x) and 1 < x < 100000 else None


def valid_places(value, field_size=None):
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, str) and not re.fullmatch(r'\s*[0-9]+\s*', value):
        return None
    try:
        numeric = float(value)
        n = int(numeric)
    except (ValueError, TypeError, OverflowError):
        return None
    if not math.isfinite(numeric) or numeric != n:
        return None
    if not 1 <= n <= 100:
        return None
    if field_size is not None and n > int(field_size):
        return None
    return n


def iso_timestamp(value):
    if value is None or not str(value).strip():
        return None
    try:
        dt = datetime.fromisoformat(str(value).strip().replace('Z', '+00:00'))
    except (TypeError, ValueError, OverflowError):
        return None
    return dt.astimezone(timezone.utc) if dt.tzinfo else None


def bookmaker_id(q):
    """Resolve legacy bookmaker field; reject inconsistent dual identity."""
    key = str(q.get('key') or '').strip().casefold()
    name = str(q.get('bookmaker') or '').strip().casefold()
    if key and name and key != name:
        return None, 'CONFLICTING_BOOKMAKER_IDS'
    book = key or name
    return (book, None) if book else (None, 'MISSING_BOOKMAKER_ID')


def make_snapshot_quote(book, event):
    """Normalise an observed provider quote, preserving explicit *bookmaker* terms.

    event['places_paid'] is a race-level reference, not evidence of an individual
    bookmaker's payout; never copy it to 'places_paid'.
    """
    if not isinstance(book, dict):
        return {}
    bookid, error = bookmaker_id(book)
    win = valid_price(book.get('win_price') if book.get('win_price') is not None else book.get('win'))
    place = valid_price(book.get('place_price') if book.get('place_price') is not None else book.get('place'))
    raw_terms = book.get('places_paid')
    if raw_terms is None:
        raw_terms = book.get('placesPaid')
    paid = valid_places(raw_terms)
    race_ref = valid_places(event.get('places_paid')) if isinstance(event, dict) else None
    return {
        'key': bookid,
        'bookmaker': bookid,
        'win': win, 'place': place,
        'stale': book.get('stale') is not False,
        'last_update': book.get('last_update') or book.get('observed_at'),
        'age_seconds': book.get('age_seconds'),
        'source_url': book.get('source_url'),
        'places_paid': paid,
        'places_paid_source': 'bookmaker_quote' if paid is not None else None,
        'race_places_paid_reference': race_ref,
        'each_way_fraction': book.get('each_way_fraction'),
        'bookmaker_id_issue': error,
    }


def parse_saved_quote(q, snapshot_at, field_size):
    """Parse saved quote; accepts legacy 'bookmaker', never invents prices or terms.

    Returns ({...}, None) for valid win+place quotes. Missing explicit terms are
    allowed *only* for WIN-only strategy assessment. Each-way must check them.
    """
    if not isinstance(q, dict):
        return None, 'INVALID_QUOTE_ROW'
    book, issue = bookmaker_id(q)
    if issue:
        return None, issue
    if q.get('stale') is not False:
        return None, 'STALE_OR_UNKNOWN_QUOTE'
    win = valid_price(q.get('win'))
    place = valid_price(q.get('place'))
    if win is None or place is None:
        return None, 'MISSING_OR_INVALID_WIN_PLACE_ODDS'
    when = iso_timestamp(snapshot_at)
    if when is None:
        return None, 'MISSING_PREDICTION_TIMESTAMP'
    raw_quote_time = q.get('last_update') or q.get('observed_at')
    quote_at = iso_timestamp(raw_quote_time)
    if raw_quote_time and quote_at is None:
        return None, 'INVALID_QUOTE_TIMESTAMP'
    if quote_at and quote_at > when:
        return None, 'QUOTE_UPDATED_AFTER_PREDICTION'
    provenance = str(q.get('places_paid_source') or '').casefold()
    paid = valid_places(q.get('places_paid'), field_size)
    if provenance and provenance != 'bookmaker_quote':
        paid = None
    return {
        'bookmaker': book, 'win': win, 'place': place,
        'places_paid': paid,
        'quote_updated_at_utc': quote_at.isoformat() if quote_at else '',
        'quote_time_quality': 'QUOTE_TIMESTAMP_PRESENT' if quote_at else 'UNVERIFIED_QUOTE_TIME',
        'legacy_bookmaker_field': not bool(q.get('key')) and bool(q.get('bookmaker')),
        'race_places_paid_reference': valid_places(q.get('race_places_paid_reference')),
        'places_paid_source': provenance or ('legacy_explicit_quote_field' if paid is not None else ''),
    }, None


def preferred_key(book):
    return (PREFERRED_BOOKMAKERS.index(book) if book in PREFERRED_BOOKMAKERS else len(PREFERRED_BOOKMAKERS), book)


def settlement(win, place, finishing_position, paid):
    """Exact user rule: <2 means $10 WIN; >=2 means $10 WIN + $10 PLACE.

    Explicit paid-place terms are only mandatory when an each-way bet is made.
    """
    w, p = valid_price(win), valid_price(place)
    if w is None or p is None:
        raise ValueError('MISSING_WIN_OR_PLACE_ODDS')
    pos = valid_places(finishing_position)
    if pos is None:
        raise ValueError('INVALID_FINISHING_POSITION')
    is_ew = p >= 2.0
    if is_ew and valid_places(paid) is None:
        raise ValueError('MISSING_BOOKMAKER_PLACES_PAID_FOR_EACH_WAY')
    paid_count = valid_places(paid)
    win_hit = pos == 1
    place_hit = bool(is_ew and pos <= paid_count)
    total_stake = 20.0 if is_ew else 10.0
    win_return = 10 * w if win_hit else 0.0
    place_return = 10 * p if place_hit else 0.0
    return {
        'bet_type': 'EACH_WAY' if is_ew else 'WIN',
        'win_stake': 10., 'place_stake': 10. if is_ew else 0.,
        'total_staked': total_stake, 'win_hit': int(win_hit),
        'paid_place_hit': int(place_hit),
        'win_return': win_return, 'place_return': place_return,
        'total_return': win_return + place_return,
        'net_profit': win_return + place_return - total_stake,
    }
