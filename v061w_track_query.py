"""Bounded, deterministic read-only queries for v0.6.1w Performance by Track.

Date filtering is pushed to Supabase BEFORE fetching archive payloads or results.
No mutations, no prediction changes and no model logic here.
"""
from datetime import date, timedelta

_PAGE = 200
_MAX_DAYS = 31
_ORDER = {
    'v061_prospective_snapshots': 'batch_id',
    'runner_results': 'id',
    'abandoned_races': 'id',
}


def selected_window(choice, today, selected_range=None):
    """Return ISO date bounds. All dates is resolved using the earliest archive."""
    if choice == 'Today':
        return today.isoformat(), today.isoformat()
    if choice == 'Yesterday':
        yesterday = today - timedelta(days=1)
        return yesterday.isoformat(), yesterday.isoformat()
    if choice == 'Date range':
        if not isinstance(selected_range, (tuple, list)) or len(selected_range) != 2:
            raise ValueError('Select both start and end dates.')
        start, end = selected_range
        if start > end:
            raise ValueError('Start date must not be after end date.')
        return start.isoformat(), end.isoformat()
    if choice == 'All dates':
        return None, None
    raise ValueError('Unknown meeting-date filter')


def earliest_archive_date(client):
    response = (client.table('v061_prospective_snapshots')
                .select('race_date').order('race_date').limit(1).execute())
    rows = response.data or []
    return str(rows[0]['race_date'])[:10] if rows else None


def load_window(client, table, start_date, end_date, *, page_size=_PAGE):
    """Retrieve only needed race dates, in <=31-day and <=200-row chunks.

    Stable unique-key ordering ensures offset pagination cannot skip rows
    merely because multiple races share the same date. Duplicate keys fail.
    """
    if table not in _ORDER:
        raise ValueError('Table is not permitted for track performance')
    if not (1 <= page_size <= 500):
        raise ValueError('Unsafe page size')
    if not start_date or not end_date:
        return []
    current = date.fromisoformat(start_date)
    last = date.fromisoformat(end_date)
    if current > last:
        return []
    data = []
    seen = set()
    unique_key = _ORDER[table]
    while current <= last:
        end = min(current + timedelta(days=_MAX_DAYS-1), last)
        offset = 0
        while True:
            q = (client.table(table).select('*')
                 .gte('race_date', current.isoformat())
                 .lte('race_date', end.isoformat())
                 .order(unique_key))
            rows = q.range(offset, offset+page_size-1).execute().data or []
            for row in rows:
                identifier = str(row.get(unique_key))
                if not identifier or identifier == 'None' or identifier in seen:
                    raise ValueError('Missing/duplicate pagination key for ' + table)
                seen.add(identifier)
                data.append(row)
            if len(rows) < page_size:
                break
            offset += len(rows)
        current = end + timedelta(days=1)
    return data
