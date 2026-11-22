import pytest
from v0422_manual_results import validate_entries, earliest_batch
URL='https://www.racingnsw.com.au/results/official'

def check(rows,**options):
    args=dict(official_url=URL,reviewer='Reviewer1',verification_note='Official steward results checked',entire_field_verified=True,official_final_verified=True)
    return validate_entries(rows,**(args|options))

def test_legitimate_nonfinish_and_scratch():
    a=check([dict(Horse='Winner',Status='FINISHED',Position=1),dict(Horse='Fall',Status='F',Position=None),dict(Horse='Lost Rider',Status='LR',Position=None),dict(Horse='Pulled Up',Status='PU',Position=None),dict(Horse='Did Not Finish',Status='DNF',Position=None),dict(Horse='Scratched',Status='SCRATCHED',Position=None)])
    assert [x['finishing_position'] for x in a]==[1,None,None,None,None,None]
    assert [x['finish_code'] for x in a]==[None,'F','LR','PU','DNF','SCRATCHED']

def test_missing_positions_blocked():
    with pytest.raises(ValueError,match='numerical position'):check([dict(Horse='A',Status='FINISHED',Position=None)])

def test_nonfinish_cannot_have_position():
    with pytest.raises(ValueError,match='BLANK'):check([dict(Horse='A',Status='FINISHED',Position=1),dict(Horse='B',Status='LR',Position=3)])

def test_duplicate_horses_block():
    with pytest.raises(ValueError,match='duplicate horse'):check([dict(Horse='A B',Status='FINISHED',Position=1),dict(Horse='A-B',Status='F',Position=None)])

def test_nonfinal_block():
    with pytest.raises(ValueError,match='FINAL'):check([dict(Horse='A',Status='FINISHED',Position=1)],official_final_verified=False)

def test_blank_source_url_allowed():
    assert len(check([dict(Horse='A',Status='FINISHED',Position=1)],official_url='')) == 1

def test_requires_complete_field_confirmation():
    with pytest.raises(ValueError,match='EVERY'):check([dict(Horse='A',Status='FINISHED',Position=1)],entire_field_verified=False)

def test_invalid_unknown_code_block():
    with pytest.raises(ValueError,match='select'):check([dict(Horse='A',Status='FINISHED',Position=1),dict(Horse='B',Status='UNKNOWN',Position=None)])

def test_two_way_dead_heat_requires_confirmation():
    rows=[dict(Horse='A',Status='FINISHED',Position=1),dict(Horse='B',Status='FINISHED',Position=1)]
    with pytest.raises(ValueError,match='dead-heat'):check(rows)
    assert len(check(rows,dead_heat_verified=True))==2

def test_earliest_batch_preserves_identity():
    rows=[dict(horse='A',prediction_batch_id='batch1',predicted_at='2026-11-21T00:00:00'),dict(horse='B',prediction_batch_id='batch1',predicted_at='2026-11-21T00:00:00'),dict(horse='C',prediction_batch_id='batch2',predicted_at='2026-11-21T00:01:00')]
    assert earliest_batch(rows)[0]=='batch1'

def test_earliest_batch_duplicate_fail_closed():
    with pytest.raises(ValueError,match='duplicate'):earliest_batch([dict(horse='A B',prediction_batch_id='1',predicted_at='a'),dict(horse='A-B',prediction_batch_id='1',predicted_at='a')])

def test_positions_duplicate_block():
    with pytest.raises(ValueError,match='Duplicate finishing position'):check([dict(Horse='A',Status='FINISHED',Position=1),dict(Horse='B',Status='FINISHED',Position=2),dict(Horse='C',Status='FINISHED',Position=2)])

# Margin extension tests (all values in lengths; separate WINNING from BEATEN)
def test_winning_and_beaten_margins_preserve_precision():
    rows=check([
      dict(Horse='A',Status='FINISHED',Position=1,**{'Margin (lengths)': '1.250'}),
      dict(Horse='B',Status='FINISHED',Position=2,**{'Margin (lengths)': 1.25}),
      dict(Horse='C',Status='FINISHED',Position=3,**{'Margin (lengths)': '4.125'}),
    ])
    assert [(x['margin_type'],x['margin_lengths']) for x in rows] == [('WINNING',1.25),('BEATEN',1.25),('BEATEN',4.125)]
    assert rows[0]['margin']=='1.250'

def test_blank_margins_allowed_without_zero_fabrication():
    rows=check([dict(Horse='A',Status='FINISHED',Position=1),dict(Horse='B',Status='FINISHED',Position=2)])
    assert all(r['margin'] is None and r['margin_type'] is None and r['margin_lengths'] is None for r in rows)

@pytest.mark.parametrize('bad',[-1,'NaN','Infinity','1.1234',501,'lengths','2L'])
def test_bad_margins_blocked(bad):
    with pytest.raises(ValueError,match='margin'):
        check([dict(Horse='A',Status='FINISHED',Position=1,**{'Margin (lengths)':bad})])

@pytest.mark.parametrize('code',['DNF','F','LR','PU','SCRATCHED'])
def test_nonfinish_margin_forbidden(code):
    with pytest.raises(ValueError,match='BLANK margin'):
        check([dict(Horse='A',Status='FINISHED',Position=1),dict(Horse='B',Status=code,Position=None,**{'Margin (lengths)':2.0})])

def test_verified_dead_heat_cannot_report_positive_winning_margin():
    with pytest.raises(ValueError,match='Dead-heat'):
        check([dict(Horse='A',Status='FINISHED',Position=1,**{'Margin (lengths)':1.2}),dict(Horse='B',Status='FINISHED',Position=1)],dead_heat_verified=True)

def test_verified_dead_heat_can_report_zero_winning_margin():
    rows=check([dict(Horse='A',Status='FINISHED',Position=1,**{'Margin (lengths)':0}),dict(Horse='B',Status='FINISHED',Position=1,**{'Margin (lengths)':0})],dead_heat_verified=True)
    assert all(r['margin_lengths']==0 and r['margin_type']=='WINNING' for r in rows)


def test_winner_and_second_margin_must_agree_if_both_entered():
    with pytest.raises(ValueError,match='disagree'):
        check([dict(Horse='A',Status='FINISHED',Position=1,**{'Margin (lengths)':1.5}),
               dict(Horse='B',Status='FINISHED',Position=2,**{'Margin (lengths)':2.5})])
