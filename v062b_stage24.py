"""Stage 2.4: original at-lock coverage inventory + FROZEN challenger protocol.

Research-only. No results, new API requests, new scoring weights, database writes,
synthetic debutant rating or retroactive ranking changes.
"""
from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
from io import BytesIO
from zipfile import ZipFile, ZIP_DEFLATED
import hashlib
import json

import pandas as pd

import re
from v062b_stage22 import archived_start_evidence, _locked_runner_metadata, digest, canonical


def normalize_track(value):
    """Local identity rule; isolated from legacy pytest module stubs."""
    return re.sub(r'\s+', ' ', str(value or '').casefold().strip())

from v062b_stage23 import v02_score_gates


PROTOCOL = {
    "name": "v062b_stage24_missing_score_coverage_candidate",
    "version": "0.1.0",
    "state": "SHADOW_DESIGN_ONLY_NO_SCORING_OR_BETTING",
    "reference_engine": "FROZEN_V0.2_UNMODIFIED",
    "purpose": "Assess original at-lock input coverage and prospective external source feasibility",
    "allowed_original_source": "IMMUTABLE_FORMFAV_ARCHIVE_WITH_MATCHING_TWO_HASHES",
    "missing_score_default": "KEEP_SAVED_V02_RANK_AND_NULL_SCORE",
    "candidate_source_policy": "NO_EXTERNAL_SOURCE_PROMOTED_WITHOUT_BEFORE_LOCK_RECEIPT_AND_VERIFIABLE_ASOF",
    "freeze_condition": "SPEC_COMMIT_AND_SOURCE_SCHEMA_BEFORE_FIRST_UNSEEN_RACE_OUTCOME",
    "holdout_policy": "FIRST_NEW_RACE_DATE_AFTER_EXPERIMENT_FREEZE;NO_REUSE_OF_STUDIED_DATES",
    "evaluation_eligibility": [
        "PRE_REGISTERED_CANDIDATE_BEFORE_LOCK",
        "FULL_FIELD_LOCK_AND_ARCHIVE_HASH_PARITY",
        "INDEPENDENT_ACTUAL_OFF_TIME_VERIFIED",
        "INDEPENDENT_PROVIDER_ASOF_VERIFIED",
        "ORIGINAL_AT_LOCK_PRICES_IF_ANY_ROI",
        "SAME_ELIGIBLE_RACES_AS_BASELINE",
        "ABANDONED_AND_JOINT_FIRST_ACCOUNTED_FOR"
    ],
    "scoring_authorization": False,
    "production_promotion": False,
}


def protocol_sha256():
    return hashlib.sha256(json.dumps(
        PROTOCOL,sort_keys=True,separators=(",",":"),ensure_ascii=False).encode("utf-8")).hexdigest()


def _utc(value):
    if not isinstance(value,str) or not value.strip():
        return None
    try:
        v=datetime.fromisoformat(value.strip().replace("Z","+00:00"))
        return v.astimezone(timezone.utc) if v.tzinfo is not None else None
    except ValueError:
        return None


def _race_id(row):
    try:
        n=int(row["race_number"])
    except (ValueError,TypeError,KeyError):
        n=None
    return str(row.get("race_date") or "")[:10],normalize_track(row.get("track")),n


def inspect_archive(archive):
    """Keep all archived race rows; source invalidity is explicit, never omitted."""
    payload=archive.get("payload")
    batch=str(archive.get("batch_id") or "")
    issues=[]
    if not isinstance(payload,dict):
        issues.append("MISSING_OR_INVALID_ARCHIVE_PAYLOAD")
        payload={}
    try:
        if digest(payload)!=archive.get("payload_sha256"):
            issues.append("ARCHIVE_HASH_MISMATCH")
        if digest(payload.get("form_response_raw"))!=payload.get("form_response_sha256"):
            issues.append("ORIGINAL_FORM_HASH_MISMATCH")
    except (TypeError,ValueError,OverflowError):
        issues.append("NONCANONICAL_PAYLOAD")
    if archive.get("model_version")!="v0.2" or payload.get("model_version")!="v0.2":
        issues.append("NOT_FROZEN_V02")
    source_times=payload.get("source_times")
    source_times=source_times if isinstance(source_times,dict) else {}
    received=_utc(source_times.get("form_response_received_utc"))
    ranked=_utc(source_times.get("ranking_generated_utc"))
    scheduled=_utc(archive.get("scheduled_start_utc") or payload.get("scheduled_start_utc"))
    created=_utc(archive.get("created_at"))
    # Actual-off isn't independently certified; before schedule is NOT a true
    # proof of capture before the real race start.
    schedule_before=(received is not None and ranked is not None and
                     scheduled is not None and created is not None and
                     received<=ranked<=created<scheduled)
    if not schedule_before:
        issues.append("SCHEDULE_ORDER_NOT_PROVEN")
    ranked_rows=payload.get("rankings")
    if not isinstance(ranked_rows,list) or not ranked_rows:
        issues.append("NO_LOCKED_RANKINGS")
        ranked_rows=[]
    names=[canonical(x.get("horse")) for x in ranked_rows if isinstance(x,dict)]
    if len(names)!=len(ranked_rows) or any(not n for n in names) or len(names)!=len(set(names)):
        issues.append("INVALID_OR_DUPLICATE_LOCKED_RUNNERS")
    source,duplicated=_locked_runner_metadata(payload.get("form_response_raw"))
    if duplicated:
        issues.append("DUPLICATE_FORM_SOURCE_RUNNERS")
    # The archive's original raw form, not live FormFav, is the only metadata.
    row=dict(batch_id=batch,race_date=str(archive.get("race_date") or "")[:10],
             track=archive.get("track"),race_number=archive.get("race_number"),
             payload_sha256=archive.get("payload_sha256"),
             form_response_sha256=payload.get("form_response_sha256"),
             original_form_received_utc=source_times.get("form_response_received_utc"),
             ranking_generated_utc=source_times.get("ranking_generated_utc"),
             archive_created_at=archive.get("created_at"),
             scheduled_start_utc=archive.get("scheduled_start_utc"),
             schedule_order_before=schedule_before,
             independently_certified_actual_off=False,
             independently_certified_provider_asof=False,
             original_ranked_count=len(ranked_rows),
             original_source_horse_count=len(source),
             raw_duplicate_names_count=len(duplicated),
             protocol_sha256=protocol_sha256(),
             status="ARCHIVE_OK_SCHEDULE_ONLY" if not issues else "|".join(sorted(set(issues))),
             research_only=True)
    return row,ranked_rows,source,duplicated,issues


def build_coverage(archives):
    """No result table dependency: all observations frozen at original archive lock."""
    race_rows=[]
    runners=[]
    problems=[]
    identities=set()
    for archive in sorted(archives,key=lambda x:str(x.get("created_at") or "")):
        race,ranks,raw,duplicates,issues=inspect_archive(archive)
        key=_race_id(archive)
        if key in identities:
            issues.append("DUPLICATE_ARCHIVED_RACE_IDENTITY")
        identities.add(key)
        # Fail closed: don't use any unverified raw form for source-coverage
        # conclusions, even when we can display saved rank/source count.
        blocked=bool(issues)
        race["status"]="REJECTED_FOR_COVERAGE" if blocked else race["status"]
        race_rows.append(race)
        for issue in sorted(set(issues)):
            problems.append({"batch_id":race["batch_id"],"race_date":race["race_date"],
                             "track":race["track"],"race_number":race["race_number"],
                             "issue":issue})
        for saved in ranks:
            if not isinstance(saved,dict):
                continue
            name=str(saved.get("horse") or "")
            keyname=canonical(name)
            source=raw.get(keyname) if not blocked and keyname not in duplicates else None
            gates=v02_score_gates(source)
            starts,starts_status,starts_fields=archived_start_evidence(source)
            score=saved.get("score")
            score_is_null=(score is None)
            if blocked:
                label="ARCHIVE_INVALID_NO_COVERAGE_INFERENCE"
            elif source is None:
                label="RAW_PROVIDER_HORSE_MISSING_OR_AMBIGUOUS"
            elif score_is_null and gates["v02_gate_could_score"]:
                label="NULL_SCORE_BUT_REQUIRED_FORM_AND_STARTS_PRESENT"
            elif not score_is_null and not gates["v02_gate_could_score"]:
                label="SAVED_SCORE_DESPITE_V02_SOURCE_GAP"
            elif score_is_null:
                label="EXPECTED_NULL_FROM_V02_SOURCE_GATES"
            else:
                label="V02_SCORE_SOURCE_GATES_PRESENT"
            runners.append({
                "batch_id":race["batch_id"],"race_date":race["race_date"],
                "track":race["track"],"race_number":race["race_number"],
                "horse":name,"horse_key":keyname,
                "original_rank":saved.get("rank"),
                "original_score":score,
                "original_scoring_status":saved.get("scoring_status"),
                "source_field_coverage":label,
                "raw_form_runner_present":source is not None,
                "original_source_start_count":starts,
                "original_source_start_status":starts_status,
                "original_source_start_fields":starts_fields,
                "provider_reports_explicit_zero_starts":starts_status=="PROVIDER_ZERO_STARTS",
                "independently_certified_debutant":False,
                **gates,
                "original_schedule_order_before":race["schedule_order_before"],
                "actual_off_independently_verified":False,
                "provider_asof_independently_verified":False,
                "external_alternative_source_checked":False,
                "alternative_source_availability":"NOT_ASSESSED_NO_PROVIDER_INTEGRATION",
                "candidate_status":"DESIGN_ONLY_KEEP_V02_RANK",
                "shadow_score":None,
                "shadow_rank":None,
                "protocol_sha256":protocol_sha256(),
                "research_only":True,
            })
    race_frame=pd.DataFrame(race_rows)
    runner_frame=pd.DataFrame(runners)
    issue_frame=pd.DataFrame(problems,columns=[
        "batch_id","race_date","track","race_number","issue"])
    if runner_frame.empty:
        return race_frame,runner_frame,pd.DataFrame(),issue_frame
    coverage=(runner_frame.groupby(
        ["source_field_coverage","v02_null_gate_reason","original_source_start_status"],
        dropna=False).agg(
            runners=("horse","size"),
            races=("batch_id","nunique"),
            racing_dates=("race_date","nunique"),
            scored=("original_score",lambda x:int(x.notna().sum())),
            raw_source_found=("raw_form_runner_present","sum")
        ).reset_index())
    return race_frame,runner_frame,coverage,issue_frame


def candidate_go_no_go(archives_df, runners_df, issue_df):
    """Never authorise modelling from data-availability counts alone."""
    total_archived=len(archives_df)
    valid=int(archives_df.status.eq("ARCHIVE_OK_SCHEDULE_ONLY").sum()) if total_archived else 0
    null_runners=int(runners_df.original_score.isna().sum()) if not runners_df.empty else 0
    return pd.DataFrame([{
        "protocol_name":PROTOCOL["name"],
        "protocol_version":PROTOCOL["version"],
        "protocol_sha256":protocol_sha256(),
        "archived_races":total_archived,
        "schedule_only_archives_with_valid_source":valid,
        "archived_null_score_horses":null_runners,
        "archive_issues":len(issue_df),
        "verified_external_alt_provider_sources":0,
        "independent_actual_off_verifications":0,
        "independent_provider_asof_verifications":0,
        "shadow_candidate_scores_generated":0,
        "unseen_forward_outcomes_evaluated":0,
        "shadow_candidate_ready":"NO",
        "barrier_adjustment_ready":"NO",
        "production_promotion_approved":"NO",
        "reason":"NO_VERIFIED_SECONDARY_PRELOCK_SOURCE_AND_NO_UNSEEN_CERTIFIED_FORWARD_HOLDOUT",
    }])


def render(secret):
    import streamlit as st
    st.subheader("🔬 v0.6.2B Stage 2.4 — Pre-race data coverage & challenger design")
    st.caption("Read-only inspection of all immutable prospective v0.2 archives. "
               "No fresh API requests, results, bettor outcomes, scores or rankings "
               "are used to design the new challenger.")
    st.info("Challenger v0.1 is SPECIFICATION ONLY: preserve every v0.2 score and rank "
            "until an independently verifiable alternative source has been captured "
            "BEFORE the original race lock. No guessed debutant score or uplift.")
    if not st.button("Run Stage 2.4 original at-lock coverage audit",
                     key="v062b_stage24_run"):
        return
    try:
        from v061_prospective import archive_client,_read_all,TABLE
        client=archive_client(secret)
        archived=_read_all(client,TABLE,order="created_at")
        races,runners,coverage,issues=build_coverage(archived)
        decision=candidate_go_no_go(races,runners,issues)
    except Exception as exc:
        st.error("Stage 2.4 read-only audit failed: "+str(exc))
        st.exception(exc)
        return
    a,b,c=st.columns(3)
    a.metric("Original archived races",len(races))
    b.metric("Archived unscored runners",
             int(runners.original_score.isna().sum()) if not runners.empty else 0)
    c.metric("Rejected original archive identities",int(races.status.eq(
        "REJECTED_FOR_COVERAGE").sum()) if not races.empty else 0)
    st.markdown("**Frozen v0.2 pre-race source coverage**")
    st.dataframe(coverage,hide_index=True,use_container_width=True)
    st.markdown("**Challenger policy approval gates**")
    st.dataframe(decision,hide_index=True,use_container_width=True)
    if not issues.empty:
        st.warning("Invalid or duplicate evidence encountered. See exported issue manifest.")
    files=[("archived_races",races),("original_runner_coverage",runners),
           ("source_coverage_summary",coverage),("source_integrity_issues",issues),
           ("challenger_design_gates",decision),
           ("challenger_protocol",pd.DataFrame([{
               "protocol_json":json.dumps(PROTOCOL,sort_keys=True),
               "protocol_sha256":protocol_sha256()}]))]
    output=BytesIO()
    with ZipFile(output,"w",ZIP_DEFLATED) as z:
        for key,frame in files:
            z.writestr(f"v062b_stage24_{key}.csv",frame.to_csv(index=False))
        z.writestr("v062b_stage24_challenger_protocol.json",
                   json.dumps(PROTOCOL,sort_keys=True,indent=2)+"\n")
    st.download_button("Download Stage 2.4 coverage and challenger-spec ZIP",
                       data=output.getvalue(),mime="application/zip",
                       file_name="v062b_stage24_coverage_challenger_audit.zip",
                       key="v062b_stage24_bundle")
    st.warning("RESEARCH ONLY. Captured before scheduled start is not certified "
               "as before actual race-off; provider data as-of is unverified. "
               "No source selection from known outcomes and no production activation.")
