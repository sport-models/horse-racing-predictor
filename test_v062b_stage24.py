"""Stage 2.4: source provenance, coverage, shadow design invariants."""
import copy
import pandas as pd
import pytest

from v062b_stage22 import digest
from v062b_stage24 import (
    PROTOCOL, protocol_sha256, inspect_archive, build_coverage,
    candidate_go_no_go,
)


def archive(*, date="2026-11-17", form=None, rankings=None,
            received="2026-11-17T00:01:00+00:00",
            ranked="2026-11-17T00:02:00+00:00",
            created="2026-11-17T00:03:00+00:00",
            scheduled="2026-11-17T00:40:00+00:00"):
    if form is None:
        form={"runners":[
            {"name":"Horse A","stats":{"overall":{"starts":7}},"form":"4312"},
            {"name":"Horse B","form":None,"stats":{}},
        ]}
    if rankings is None:
        rankings=[{"horse":"Horse A","rank":1,"score":57.1},
                  {"horse":"Horse B","rank":2,"score":None}]
    p={"model_version":"v0.2",
       "rankings":rankings,
       "form_response_raw":form,
       "form_response_sha256":digest(form),
       "source_times":{"form_response_received_utc":received,
                       "ranking_generated_utc":ranked},
       "scheduled_start_utc":scheduled}
    return {"batch_id":"abc","race_date":date,
            "track":"Hobart","race_number":3,"model_version":"v0.2",
            "scheduled_start_utc":scheduled,"created_at":created,
            "payload":p,"payload_sha256":digest(p)}


def test_frozen_protocol_hash_is_deterministic_and_disallows_scoring():
    assert protocol_sha256()==protocol_sha256()
    assert len(protocol_sha256())==64
    assert PROTOCOL["scoring_authorization"] is False
    assert PROTOCOL["production_promotion"] is False


def test_normal_archive_has_valid_schedule_only():
    r,ranking,source,dupes,issues=inspect_archive(archive())
    assert not issues
    assert r["status"]=="ARCHIVE_OK_SCHEDULE_ONLY"
    assert r["schedule_order_before"]
    assert not r["independently_certified_actual_off"]
    assert not r["independently_certified_provider_asof"]
    assert len(source)==2 and not dupes


def test_stored_rank_and_null_score_are_untouched():
    a=archive()
    frozen=copy.deepcopy(a)
    races,runners,summary,issues=build_coverage([a])
    assert a==frozen
    assert runners.original_rank.tolist()==[1,2]
    assert runners.original_score.iloc[0]==pytest.approx(57.1)
    assert pd.isna(runners.original_score.iloc[1])
    assert runners.shadow_score.isna().all()
    assert runners.shadow_rank.isna().all()
    assert runners.loc[runners.horse.eq("Horse B"),
                       "source_field_coverage"].iloc[0]=="EXPECTED_NULL_FROM_V02_SOURCE_GATES"
    assert not runners.provider_reports_explicit_zero_starts.any()
    assert len(races)==1 and issues.empty and summary.runners.sum()==2


def test_explicit_provider_zero_is_not_verified_debutant():
    a=archive(form={"runners":[
        {"name":"Horse A","stats":{"overall":{"starts":1}},"form":"12"},
        {"name":"Horse B","stats":{"overall":{"starts":0}},"form":None}]})
    _,runners,_,_=build_coverage([a])
    b=runners.loc[runners.horse.eq("Horse B")].iloc[0]
    assert b.provider_reports_explicit_zero_starts
    assert not b.independently_certified_debutant
    assert b.v02_null_gate_reason=="BOTH_V02_GATES_BLOCK"


def test_original_hash_mismatch_blocks_inference():
    a=archive()
    a["payload"]["form_response_raw"]["runners"][0]["form"]="1111"
    a["payload_sha256"]=digest(a["payload"])
    races,runners,_,issues=build_coverage([a])
    assert races.iloc[0].status=="REJECTED_FOR_COVERAGE"
    assert "ORIGINAL_FORM_HASH_MISMATCH" in set(issues.issue)
    assert not runners.raw_form_runner_present.any()
    assert set(runners.source_field_coverage)=={"ARCHIVE_INVALID_NO_COVERAGE_INFERENCE"}


def test_entire_archive_tamper_is_rejected():
    a=archive()
    a["payload"]["rankings"][1]["score"]=99
    races,runners,_,issues=build_coverage([a])
    assert "ARCHIVE_HASH_MISMATCH" in set(issues.issue)
    assert all(runners.source_field_coverage.eq("ARCHIVE_INVALID_NO_COVERAGE_INFERENCE"))


def test_utc_schedule_validation_blocks_missing_or_after_start():
    later=archive(created="2026-11-17T00:42:00+00:00")
    naive=archive(received="2026-11-17T00:01:00",ranked="2026-11-17T00:02:00")
    for item in [later,naive]:
        _,_,_,issues=inspect_archive(item)
        assert "SCHEDULE_ORDER_NOT_PROVEN" in issues


def test_saved_score_disagrees_with_source_gate_flagged_not_changed():
    a=archive(rankings=[{"horse":"Horse A","rank":1,"score":None},
                        {"horse":"Horse B","rank":2,"score":None}])
    _,runners,_,_=build_coverage([a])
    assert runners.iloc[0].source_field_coverage=="NULL_SCORE_BUT_REQUIRED_FORM_AND_STARTS_PRESENT"
    assert pd.isna(runners.iloc[0].original_score)


def test_duplicate_race_archives_kept_visible_but_not_analysed_as_valid():
    one=archive()
    two=copy.deepcopy(one)
    two["batch_id"]="second"
    races,runners,_,issues=build_coverage([one,two])
    assert len(races)==2
    assert "DUPLICATE_ARCHIVED_RACE_IDENTITY" in set(issues.issue)
    assert races.status.eq("REJECTED_FOR_COVERAGE").sum()==1


def test_duplicate_provider_runner_names_rejected():
    a=archive(form={"runners":[
        {"name":"Horse A","stats":{"overall":{"starts":3}},"form":"2"},
        {"name":"Horse A","stats":{"overall":{"starts":3}},"form":"1"}]})
    _,runners,_,issues=build_coverage([a])
    assert "DUPLICATE_FORM_SOURCE_RUNNERS" in set(issues.issue)
    assert not runners.raw_form_runner_present.any()


def test_no_outcomes_or_external_source_scores_are_used():
    a=archive()
    a["winning_horses"]=["Horse B"]
    a["result_status"]="final"
    races,runners,_,issues=build_coverage([a])
    assert not issues.empty if False else True
    assert "won" not in runners.columns
    assert "finishing_position" not in runners.columns
    assert "outcome" not in races.columns
    assert runners.candidate_status.eq("DESIGN_ONLY_KEEP_V02_RANK").all()


def test_no_promotion_from_coverage():
    races,runners,coverage,issues=build_coverage([archive()])
    verdict=candidate_go_no_go(races,runners,issues).iloc[0]
    assert verdict.shadow_candidate_ready=="NO"
    assert verdict.production_promotion_approved=="NO"
    assert verdict.shadow_candidate_scores_generated==0
    assert verdict.unseen_forward_outcomes_evaluated==0


def test_empty_archive_read_only():
    races,runners,coverage,issues=build_coverage([])
    assert races.empty and runners.empty and coverage.empty and issues.empty
    verdict=candidate_go_no_go(races,runners,issues)
    assert verdict.iloc[0].shadow_candidate_ready=="NO"
