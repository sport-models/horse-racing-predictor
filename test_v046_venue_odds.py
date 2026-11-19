"""Regression cases for v0.4.6 Sandown Lakeside bookmaker capture.

No network requests and no Supabase writes. The fixture feeds mimic PuntersEdge's
existing /racing/next-to-go result envelope and preserve the v0.2 scoring path.
"""
from datetime import datetime, timedelta, timezone
import copy
import pytest

from v046_venue_odds import (
    request_venues, select_event, capture_prelock_odds,
    odds_capture_inventory, VENUE_ALIAS_POLICY_VERSION,
)

START = '2026-11-20T05:00:00+00:00'
DAY = '2026-11-20'


def event(venue='Sandown', race=1, start=START, **kwargs):
    return {
        'country': 'AU', 'category': 'horse', 'venue': venue, 'race_number': race,
        'start_time': start, 'runners': [{'name': 'Horse One', 'bookmakers': []}],
        **kwargs,
    }


def clock():
    moment = datetime(2026, 11, 20, 4, 0, tzinfo=timezone.utc)
    def next_time():
        nonlocal moment
        moment += timedelta(seconds=1)
        return moment
    return next_time


def capture(responses, track='Sandown Lakeside', **opts):
    calls=[]
    def request(endpoint, api_key, params):
        assert endpoint == '/racing/next-to-go'
        assert api_key == 'SECRET_SHOULD_NEVER_BE_PERSISTED'
        calls.append(dict(params))
        v=responses[len(calls)-1]
        if isinstance(v, Exception):
            raise v
        return v
    def quote(event, name):
        return [{'bookmaker': 'sportsbet', 'win': 3.6,
                 'last_update': '2026-11-20T04:00:00+00:00', 'stale': False}]
    tick=opts.pop('utc_now',clock())
    data=capture_prelock_odds(
        race_date=DAY, track=track, race_number=1, scheduled=START,
        runner_names=['Horse One'], api_key='SECRET_SHOULD_NEVER_BE_PERSISTED',
        puntersedge_request=request, odds_runner_quotes=quote,
        utc_now=tick, **opts,
    )
    return data,calls


def test_approved_alias_only_one_direction_and_hillside_never_aliases():
    assert request_venues('Sandown Lakeside')==['Sandown Lakeside','Sandown']
    assert request_venues('Sandown')==['Sandown']
    assert request_venues('Sandown Hillside')==['Sandown Hillside']
    assert request_venues('Flemington')==['Flemington']


def test_normal_exact_event_keeps_old_capture_status_with_one_request():
    (quotes,done,status,trace),calls=capture([{'events':[event('Sandown Lakeside')]}])
    assert status=='CAPTURED' and len(calls)==1
    assert quotes['Horse One'][0]['win']==3.6
    assert trace['match_rule']=='EXACT'
    assert trace['total_saved_runner_quotes']==1
    assert done<datetime.fromisoformat(START)


def test_generic_alias_from_initial_response_needs_no_second_credit():
    (quotes,done,status,trace),calls=capture([{'events':[event('Sandown')]}])
    assert status=='CAPTURED'
    assert len(calls)==1
    assert trace['match_rule']=='APPROVED_LAKESIDE_TO_GENERIC_SANDOWN_ALIAS'
    assert trace['selected_provider_venue']=='Sandown'


def test_fallback_request_queries_generic_sandown_if_initial_feed_empty():
    (quotes,done,status,trace),calls=capture([{'events':[]},{'events':[event('Sandown')]}])
    assert status=='CAPTURED'
    assert [x['venue'] for x in calls]==['Sandown Lakeside','Sandown']
    assert trace['match_rule']=='APPROVED_LAKESIDE_TO_GENERIC_SANDOWN_ALIAS'
    assert [x['status'] for x in trace['attempts']]==[
        'NO_UNIQUE_MATCHED_LIVE_EVENT','MATCHED_UNIQUE_EVENT']


def test_missing_market_remains_clearly_unmatched_and_bounded():
    (quotes,done,status,trace),calls=capture([{'events':[]},{'events':[]}])
    assert status=='NO_UNIQUE_MATCHED_LIVE_EVENT'
    assert quotes=={}
    assert len(calls)==2
    assert trace['final_status']==status


def test_sandown_hillside_is_not_lakeside():
    (quotes,done,status,trace),calls=capture([{'events':[event('Sandown Hillside')]},{'events':[]}])
    assert status=='NO_UNIQUE_MATCHED_LIVE_EVENT'
    assert quotes=={}
    assert len(calls)==2
    assert trace['attempts'][0]['date_race_number_candidates']==1
    assert trace['attempts'][0]['provider_venues_same_date_and_race']==['Sandown Hillside']


def test_generic_sandown_circuit_hillside_metadata_is_blocked():
    (quotes,done,status,trace),calls=capture([{'events':[event('Sandown', circuit='Hillside')]}, {'events':[]}])
    assert status=='NO_UNIQUE_MATCHED_LIVE_EVENT'
    assert quotes=={}
    assert trace['attempts'][0]['status']=='PROVIDER_CIRCUIT_CONTRADICTION'
    assert trace['attempts'][0]['reject_circuit_conflicts']==1


def test_alias_time_mismatch_is_not_used():
    future='2026-11-20T06:00:00+00:00'
    (quotes,done,status,trace),calls=capture([{'events':[event('Sandown', start=future)]},{'events':[]}])
    assert quotes=={}
    assert trace['attempts'][0]['status']=='ALIAS_SCHEDULE_TIME_MISMATCH'
    assert trace['attempts'][0]['reject_alias_time_mismatch']==1


def test_exact_name_time_matching_remains_backward_compatible():
    ev=event('Sandown Lakeside',start='2026-11-20T06:00:00+00:00')
    row,status,meta=select_event({'events':[ev]},DAY,'Sandown Lakeside',1,START)
    assert status=='MATCHED_UNIQUE_EVENT' and row is ev and meta['match_rule']=='EXACT'


def test_multiple_candidate_events_fail_closed_without_extra_request():
    (quotes,done,status,trace),calls=capture([{'events':[event('Sandown'),event('Sandown Lakeside')]}])
    assert status=='AMBIGUOUS_MATCHING_EVENTS' and quotes=={}
    assert len(calls)==1
    assert trace['attempts'][0]['approved_alias_candidates']==1
    assert trace['attempts'][0]['exact_venue_candidates']==1


def test_wrong_date_and_race_or_country_do_not_match():
    feed={'events':[event('Sandown',race=2),event('Sandown',start='2026-11-21T05:00:00Z'),
                    event('Sandown',country='US'),event('Sandown',category='greyhound')]}
    row,status,meta=select_event(feed,DAY,'Sandown Lakeside',1,START)
    assert row is None and status=='NO_UNIQUE_MATCHED_LIVE_EVENT'
    assert meta['date_race_number_candidates']==0


def test_provider_exception_does_not_expose_api_secret_and_does_not_retry():
    (quotes,done,status,trace),calls=capture([RuntimeError('SECRET_SHOULD_NEVER_BE_PERSISTED')])
    assert status=='ODDS_FETCH_FAILED:RuntimeError' and quotes=={}
    assert len(calls)==1
    assert 'SECRET_SHOULD_NEVER_BE_PERSISTED' not in repr(trace)
    assert trace['attempts'][0]['exception_type']=='RuntimeError'


def test_no_key_does_not_call_provider():
    called=[]
    result=capture_prelock_odds(race_date=DAY,track='Sandown Lakeside',race_number=1,
        scheduled=START,runner_names=['One'],api_key='',
        puntersedge_request=lambda *a: called.append(a),
        odds_runner_quotes=lambda e,h:[],utc_now=clock())
    assert result[2]=='NO_ODDS_KEY' and called==[]


def test_start_elapsed_blocks_capture():
    (quotes,done,status,trace),calls=capture([{'events':[]}],
        utc_now=lambda: datetime(2026,11,20,5,1,tzinfo=timezone.utc))
    assert status=='ODDS_AFTER_SCHEDULE' and calls==[] and quotes=={}


def test_no_secret_in_diagnostics_source_receipt_provenance():
    (quotes,done,status,trace),calls=capture([{'events':[event('Sandown')]}])
    assert 'SECRET_SHOULD_NEVER_BE_PERSISTED' not in repr(trace)
    assert trace['attempts'][0]['request_started_utc']
    assert trace['attempts'][0]['request_finished_utc']
    assert trace['alias_policy_version']==VENUE_ALIAS_POLICY_VERSION
    assert trace['source_provenance'].endswith('NOT_INDEPENDENT_ACTUAL_OFF')


def test_existing_archive_is_not_mutated_in_diagnostic_export():
    archive={'batch_id':'old','race_date':'2026-11-18','track':'Sandown Lakeside',
        'race_number':1,'payload_sha256':'frozen_hash',
        'payload':{'bookmaker_capture_status':'NO_UNIQUE_MATCHED_LIVE_EVENT',
                   'source_times':{'odds_response_received_utc':'2026-11-18T01:31:00+00:00'}}}
    before=copy.deepcopy(archive)
    result=odds_capture_inventory([archive])
    assert archive==before
    assert len(result)==1
    assert result.iloc[0].has_v046_capture_diagnostic==False
    assert result.iloc[0].source_note=='ORIGINAL_LEGACY_ARCHIVE_NO_NEW_DIAGNOSTICS'
    assert result.iloc[0].original_payload_sha256=='frozen_hash'


def test_diagnostic_export_includes_alias_capture_without_reconstructing_old_prices():
    (quotes,done,status,trace),calls=capture([{'events':[event('Sandown')]}])
    original={'batch_id':'new','race_date':DAY,'track':'Sandown Lakeside',
        'race_number':1,'payload_sha256':'snapshot_hash',
        'payload':{'bookmaker_capture_status':status,'odds_capture_diagnostic':trace,
                   'source_times':{'odds_response_received_utc':done.isoformat()}}}
    row=odds_capture_inventory([original]).iloc[0]
    assert row.has_v046_capture_diagnostic==True
    assert row.matched_provider_venue=='Sandown'
    assert row.match_rule=='APPROVED_LAKESIDE_TO_GENERIC_SANDOWN_ALIAS'
    assert row.original_bookmaker_capture_status=='CAPTURED'


def test_quote_extraction_failure_does_not_block_predictions_or_save_partial_prices():
    calls=[]
    def request(endpoint,key,params):
        calls.append(params)
        return {'events':[event('Sandown')]}
    def broken_quote(e,horse):
        raise ValueError('Sensitive provider body should not be archived')
    quotes,done,status,trace=capture_prelock_odds(
        race_date=DAY,track='Sandown Lakeside',race_number=1,scheduled=START,
        runner_names=['One','Two'],api_key='test',puntersedge_request=request,
        odds_runner_quotes=broken_quote,utc_now=clock())
    assert quotes=={}
    assert status=='ODDS_RUNNER_QUOTE_EXTRACTION_FAILED:ValueError'
    assert trace['attempts'][0]['status']=='QUOTE_EXTRACTION_FAILED'
    assert 'Sensitive provider body' not in repr(trace)
    assert len(calls)==1


def test_generic_venue_with_unsafe_hillside_course_metadata_is_never_used():
    row,status,meta=select_event({'races':[event('Sandown',course_name='Sandown Hillside')]},
                                DAY,'Sandown Lakeside',1,START)
    assert row is None and status=='PROVIDER_CIRCUIT_CONTRADICTION'


def test_feed_accepts_supported_nested_races_envelope():
    row,status,meta=select_event({'data':{'races':[event('Sandown')]}},
                                  DAY,'Sandown Lakeside',1,START)
    assert row is not None and status=='MATCHED_UNIQUE_EVENT'


def test_no_full_market_cannot_be_claimed_if_runner_quotes_absent():
    calls=[]
    def request(endpoint,key,params):
        calls.append(params)
        return {'events':[event('Sandown')]}
    quotes,done,status,trace=capture_prelock_odds(
        race_date=DAY,track='Sandown Lakeside',race_number=1,scheduled=START,
        runner_names=['One','Two'],api_key='test',puntersedge_request=request,
        odds_runner_quotes=lambda event,horse:[],utc_now=clock())
    assert status=='CAPTURED'  # legacy event-capture semantics preserved
    assert trace['matched_runner_names_with_quotes']==0
    assert trace['total_saved_runner_quotes']==0
    assert all(v==[] for v in quotes.values())
    # Existing market validation will correctly exclude this incomplete field.


def test_earlier_races_remain_untouched_when_bulk_exporting_archives():
    original=[{'batch_id':str(i),'track':'Sandown Lakeside','race_number':i,
        'race_date':'2026-11-18','payload_sha256':str(i),
        'payload':{'bookmaker_capture_status':'NO_UNIQUE_MATCHED_LIVE_EVENT'}}
              for i in range(1,9)]
    frozen=copy.deepcopy(original)
    df=odds_capture_inventory(original)
    assert len(df)==8
    assert df.has_v046_capture_diagnostic.sum()==0
    assert (df.original_bookmaker_capture_status=='NO_UNIQUE_MATCHED_LIVE_EVENT').all()
    assert original==frozen


def test_preserves_existing_v061_exact_matcher_callback_and_bookmaker_fixture():
    """Backward-compatibility with test_v061.py's {'event': quotes} mock."""
    mock_book={'quotes':{'Horse One':[{'bookmaker':'sportsbet','win':3.6,'stale':False,
        'last_update':'2026-11-20T04:00:00+00:00'}]}}
    result={'event':mock_book}
    calls=[]
    def original_matcher(payload,day,track,num):
        assert day==DAY and track=='Flemington' and num==1
        return payload['event']
    def getquotes(event,horse):
        return event.get('quotes',{}).get(horse,[])
    quotes,received,status,trace=capture_prelock_odds(
        race_date=DAY,track='Flemington',race_number=1,scheduled=START,
        runner_names=['Horse One'],api_key='test',
        puntersedge_request=lambda endpoint,key,params: calls.append(params) or result,
        odds_runner_quotes=getquotes,odds_find_race=original_matcher,utc_now=clock())
    assert status=='CAPTURED'
    assert len(calls)==1
    assert quotes['Horse One'][0]['win']==3.6
    assert trace['match_rule']=='ORIGINAL_V061_EXACT_MATCHER'


def test_ambiguous_alias_feed_blocks_even_when_legacy_exact_callback_would_select():
    payload={'events':[event('Sandown Lakeside'),event('Sandown')]}
    original_matcher=lambda payload,day,track,num: payload['events'][0]
    quotes,received,status,trace=capture_prelock_odds(
        race_date=DAY,track='Sandown Lakeside',race_number=1,scheduled=START,
        runner_names=['Horse One'],api_key='test',
        puntersedge_request=lambda endpoint,key,params: payload,
        odds_runner_quotes=lambda *args:[{'win':3.5}],
        odds_find_race=original_matcher,utc_now=clock())
    assert status=='AMBIGUOUS_MATCHING_EVENTS' and quotes=={}
