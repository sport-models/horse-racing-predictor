import pytest
from datetime import date
from v0422_manual_results import available_unimported_races,map_provider_rows,fetch_formfav_preview,validate_entries

def test_only_unimported_races_show():
    preds=[dict(race_date='2026-11-21',track='Gosford',race_number=i) for i in [1,2,6]]
    results=[dict(race_date='2026-11-21',track='gosford',race_number=1),dict(race_date='2026-11-21',track='Gosford',race_number=2)]
    assert available_unimported_races(preds,results,[],asof=date(2026,11,22))==[('2026-11-21','Gosford',6)]

def test_abandoned_races_do_not_show():
    a=[dict(race_date='2026-11-21',track='Gosford',race_number=6)]
    b=[dict(race_date='2026-11-21',track_key='gosford',race_number=6)]
    assert available_unimported_races(a,[],b,asof=date(2026,11,22))==[]

def test_formfav_prefills_positions_and_leaves_nonfinish_unknown():
    predictions=[{'horse':'Winner'},{'horse':'Second'},{'horse':'Lost Rider'},{'horse':'Absent'}]
    payload={'resultStatus':'interim','placings':[
      {'name':'Winner','position':1,'margin':'0.0'},
      {'name':'Second','position':2,'margin':'1.25'},
      {'name':'Lost Rider','position':None,'scratched':False},
      {'name':'Extra','position':3,'margin':'3.5'},
    ]}
    rows,info=map_provider_rows(predictions,payload)
    named={x['Horse']:x for x in rows}
    assert named['Winner']['Margin (lengths)']==1.25
    assert named['Second']['Position']==2
    assert named['Lost Rider']['Status']=='UNSET'
    assert named['Absent']['Status']=='UNSET'
    assert named['Extra']['Position']==3
    assert info['matched']==3 and info['extra']==1 and info['unresolved']==2

def test_url_optional_with_review_source():
    rows=validate_entries([{'Horse':'A','Status':'FINISHED','Position':1}],official_url='',
         reviewer='Me',verification_note='Checked Sportsbet settled results',
         entire_field_verified=True, official_final_verified=True, source_name='Sportsbet')
    assert rows[0]['finishing_position']==1

def test_url_invalid_if_supplied():
    with pytest.raises(ValueError,match='HTTPS'):
        validate_entries([{'Horse':'A','Status':'FINISHED','Position':1}],official_url='http://bad',
            reviewer='Me',verification_note='Checked Sportsbet settled results',
            entire_field_verified=True,official_final_verified=True)

def test_nonfinal_still_blocked():
    with pytest.raises(ValueError,match='FINAL'):
        validate_entries([{'Horse':'A','Status':'FINISHED','Position':1}],official_url='',
            reviewer='Me',verification_note='Checked Sportsbet settled results',
            entire_field_verified=True,official_final_verified=False)

def test_readonly_formfav_identity_guard():
    class R:
        def raise_for_status(self):pass
        def json(self):return {'date':'2026-11-21','track':'Esperance','raceNumber':6,'placings':[]}
    with pytest.raises(ValueError,match='identity mismatch'):
        fetch_formfav_preview('key',('2026-11-21','Gosford',6),http_get=lambda *a,**kw:R())
