from datetime import date,timedelta
import pytest
from v045_abandoned import validate_manual_abandonment, race_id

def _fixture():
    race=('2026-11-15','Donald',5)
    predicted={race_id({'race_date':race[0],'track':race[1],'race_number':race[2]})}
    return race,predicted

def test_valid_official_abandonment_without_placings(monkeypatch):
    from v045_abandoned import date as date_obj
    # Date check permits today/past, but not future. Test on present date.
    race=(date.today().isoformat(),'Donald',5)
    pred={race_id({'race_date':race[0],'track':race[1],'race_number':5})}
    row=validate_manual_abandonment(race,'https://racing.example/notice/5',
        'Official notice confirms race five abandoned',pred,set(),set())
    assert row['outcome']=='ABANDONED'
    assert 'finishing_position' not in row
    assert 'horse' not in row
    assert row['track_key']=='donald'

@pytest.mark.parametrize('url',['','http://authority.test/results','file:///etc/passwd',
                               'https://user:pass@authority.test/races','https://'])
def test_unsafe_or_missing_official_source_rejected(url):
    race=(date.today().isoformat(),'Donald',5)
    pred={race_id({'race_date':race[0],'track':race[1],'race_number':5})}
    with pytest.raises(ValueError,match='HTTPS'):
        validate_manual_abandonment(race,url,'Official notice of abandoned race',pred,set(),set())

def test_no_prediction_cannot_manually_create_race():
    race=(date.today().isoformat(),'Donald',5)
    with pytest.raises(ValueError,match='prediction'):
        validate_manual_abandonment(race,'https://official.example/race',
            'Official notice of abandoned race',set(),set(),set())

def test_existing_results_or_status_block_duplicate():
    race=(date.today().isoformat(),'Donald',5)
    key=race_id({'race_date':race[0],'track':race[1],'race_number':5})
    for finals,prior in [({key},set()),(set(),{key})]:
        with pytest.raises(ValueError):
            validate_manual_abandonment(race,'https://official.example/race',
                'Official notice of abandoned race',{key},finals,prior)

def test_future_race_cannot_be_marked_abandoned():
    race=((date.today()+timedelta(days=1)).isoformat(),'Donald',5)
    key=race_id({'race_date':race[0],'track':race[1],'race_number':5})
    with pytest.raises(ValueError,match='future'):
        validate_manual_abandonment(race,'https://official.example/race',
            'Official notice of abandoned race',{key},set(),set())

def test_short_attestation_rejected():
    race=(date.today().isoformat(),'Donald',5)
    key=race_id({'race_date':race[0],'track':race[1],'race_number':5})
    with pytest.raises(ValueError,match='Describe'):
        validate_manual_abandonment(race,'https://official.example/race',
            'yes',{key},set(),set())
