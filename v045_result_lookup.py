"""Read-only, fail-closed matching of official results to pending bets."""
from collections import defaultdict
from v042_results import canonical, normalize_track


def race_key(row):
    return (str(row['race_date'])[:10], normalize_track(row['track']), int(row['race_number']))


def result_index(rows):
    groups = defaultdict(list)
    for row in rows:
        key = race_key(row) + (canonical(row.get('horse_key') or row.get('horse')),)
        groups[key].append(row)
    return groups


def lookup(bet, index):
    key = race_key(bet) + (canonical(bet['horse']),)
    matches = index.get(key, [])
    if not matches:
        return {'state': 'missing', 'position': None, 'scratched': False}
    if len(matches) != 1:
        return {'state': 'ambiguous', 'position': None, 'scratched': False}
    row = matches[0]
    if row.get('result_status') != 'final':
        return {'state': 'invalid', 'position': None, 'scratched': False}
    scratched = row.get('scratched') is True
    position = row.get('finishing_position')
    if scratched:
        if position is not None:
            return {'state': 'invalid', 'position': None, 'scratched': False}
        return {'state': 'ready', 'position': None, 'scratched': True}
    try:
        position = int(position)
    except (TypeError, ValueError):
        return {'state': 'invalid', 'position': None, 'scratched': False}
    if position < 1 or position > 99:
        return {'state': 'invalid', 'position': None, 'scratched': False}
    return {'state': 'ready', 'position': position, 'scratched': False}
