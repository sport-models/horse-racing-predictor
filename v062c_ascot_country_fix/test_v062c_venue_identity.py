"""Tests extracted function from installed source without importing Streamlit/Supabase."""
import ast
from datetime import datetime
from pathlib import Path
import re
import pytest

SRC = Path(__file__).resolve().parent.parent / 'v061_prospective.py'


def normalise(value):
    return re.sub(r'\s+', ' ', str(value or '').casefold().strip())


def get_value(obj, *names, default=None):
    for n in names:
        if obj.get(n) is not None:
            return obj[n]
    return default


def get_items(obj, key):
    return obj.get(key, [])


def load():
    tree = ast.parse(SRC.read_text(encoding='utf-8'))
    function = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == '_race_schedule')
    code = compile(ast.Module(body=[function], type_ignores=[]), str(SRC), 'exec')
    env = dict(normalize_track=normalise,
               safe_race_number=lambda n: int(n) if str(n).isdigit() and 1 <= int(n) <= 30 else None,
               iso_timestamp=lambda s: datetime.fromisoformat(s.replace('Z', '+00:00')) if s else None)
    exec(code, env)
    return env['_race_schedule']


def meeting(country, number=1, start='2026-11-21T05:00:00Z', venue='Ascot', field='country'):
    x = {'track': venue, 'races': [{'raceNumber': number, 'startTime': start}]}
    if country is not None:
        x[field] = country
    return x


def resolve(*items, track='Ascot', number=1):
    return load()({'meetings': list(items)}, track, number,
                  get_items=get_items, get_value=get_value,
                  meeting_name=lambda m: get_value(m, 'track', 'trackName', 'venue', 'name'))


def test_ascot_gb_and_au_distinct():
    race, when = resolve(meeting('GB',start='2026-11-21T13:00:00Z'),meeting('AU'))
    assert race['startTime']=='2026-11-21T05:00:00Z'
    assert when.utcoffset().total_seconds()==0


def test_inverted_order():
    race, _ = resolve(meeting('Australia'),meeting('Great Britain',start='2026-11-21T13:00:00Z'))
    assert race['startTime']=='2026-11-21T05:00:00Z'


def test_country_code_supported():
    race, _ = resolve(meeting('AUS',field='countryCode'),meeting('GB',field='countryCode'))
    assert race['raceNumber']==1


def test_no_au_fails():
    with pytest.raises(ValueError,match='matching AU live'):
        resolve(meeting('GB'))


def test_duplicate_au_fails():
    with pytest.raises(ValueError,match='found 2'):
        resolve(meeting('AU'),meeting('AU'))


def test_missing_country_fails():
    with pytest.raises(ValueError,match='Country missing'):
        resolve(meeting('AU'),meeting(None))


def test_unrelated_unknown_country_does_not_block():
    race,_=resolve(meeting('AU'),meeting(None,number=2))
    assert race['raceNumber']==1


def test_other_meeting_not_considered():
    race,_=resolve(meeting('AU'),meeting('AU',venue='Belmont'))
    assert race['raceNumber']==1


def test_invalid_schedule_fails():
    with pytest.raises(ValueError,match='timezone-aware'):
        resolve(meeting('AU',start=''))


def test_other_country_ignored():
    race,_=resolve(meeting('AU'),meeting('NZ'))
    assert race['raceNumber']==1


def test_same_name_different_race_number():
    race,_=resolve(meeting('AU',number=6),meeting('GB',number=1),number=6)
    assert race['raceNumber']==6
