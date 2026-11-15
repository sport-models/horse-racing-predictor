"""Read-only visibility for archived prospective predictions by track.

A locked race belongs in the inventory even without results or bets.
Accuracy and returns must still use EVALUABLE_PROVISIONAL races only.
No database writes, live provider calls or retroactive rank substitutions.
"""
from __future__ import annotations

import pandas as pd

INVENTORY_COLUMNS = [
    "race_date", "track", "race_number", "batch_id",
    "locked_at_utc", "scheduled_start_utc", "locked_runners",
    "rank1_at_lock", "prediction_status", "raw_evaluation_status",
]
SUMMARY_COLUMNS = ["track", "archived_races", "awaiting_results",
                   "review_required", "evaluated_races"]


def prediction_status(raw_status):
    """Display saved-archive lifecycle; never claim final accuracy without results."""
    raw = str(raw_status or "")
    if raw == "EVALUABLE_PROVISIONAL":
        return "EVALUATED_PROVISIONAL"
    if raw == "NO_FINAL_RESULTS":
        return "AWAITING_FINAL_RESULTS"
    if "NO_FINAL_RESULTS" in raw:
        return "EVIDENCE_REVIEW_AWAITING_RESULTS"
    if any(problem in raw for problem in
           ("INVALID_OR_INCOMPLETE_OFFICIAL_RESULTS",
            "ACTUAL_STARTER_MISSING_FROM_SNAPSHOT", "NO_ACTUAL_STARTERS")):
        return "RESULT_REVIEW_REQUIRED"
    return "EVIDENCE_REVIEW_REQUIRED"


def _original_rank_one(rankings):
    if not isinstance(rankings, list):
        return None
    top = []
    for runner in rankings:
        if not isinstance(runner, dict):
            continue
        try:
            if int(runner.get("rank")) == 1:
                top.append(str(runner.get("horse") or "").strip())
        except (TypeError, ValueError, OverflowError):
            continue
    return top[0] if len(top) == 1 and top[0] else None


def build_locked_inventory(visible_races, archives):
    """Match already date/track-filtered evaluations to original saved archives.

    A lack of official results must not hide successfully archived locks.
    Original rank #1 comes ONLY from the immutable saved payload, not a
    retrospective active-runner substitution.
    """
    if visible_races is None or visible_races.empty:
        return pd.DataFrame(columns=INVENTORY_COLUMNS)
    by_batch = {}
    duplicate_ids = set()
    for archive in sorted(archives or [], key=lambda a: str(a.get("created_at") or "")):
        batch_id = str(archive.get("batch_id") or "")
        if batch_id in by_batch:
            duplicate_ids.add(batch_id)
        elif batch_id:
            by_batch[batch_id] = archive

    rows = []
    for race in visible_races.to_dict("records"):
        batch_id = str(race.get("batch_id") or "")
        saved = by_batch.get(batch_id) or {}
        payload = saved.get("payload") or {}
        ranks = payload.get("rankings")
        raw_status = str(race.get("status") or "UNASSESSED")
        status = prediction_status(raw_status)
        if batch_id in duplicate_ids:
            status = "EVIDENCE_REVIEW_REQUIRED"
            raw_status += "|DUPLICATE_ARCHIVE_BATCH"
        rows.append({
            "race_date": str(race.get("race_date") or "")[:10],
            "track": str(race.get("track") or "Unknown"),
            "race_number": race.get("race_number"),
            "batch_id": batch_id,
            "locked_at_utc": saved.get("created_at") or race.get("server_archived_at"),
            "scheduled_start_utc": saved.get("scheduled_start_utc") or race.get("scheduled_start_utc"),
            "locked_runners": len(ranks) if isinstance(ranks, list) else None,
            "rank1_at_lock": _original_rank_one(ranks),
            "prediction_status": status,
            "raw_evaluation_status": raw_status,
        })
    return (pd.DataFrame(rows, columns=INVENTORY_COLUMNS)
            .sort_values(["race_date", "track", "race_number"], kind="stable")
            .reset_index(drop=True))


def summarise_locked_inventory(inventory):
    """Expose archived pending races without contaminating win-rate denominators."""
    if inventory is None or inventory.empty:
        return pd.DataFrame(columns=SUMMARY_COLUMNS)
    totals = (inventory.groupby("track", dropna=False)
              .agg(archived_races=("batch_id", "size"),
                   awaiting_results=("prediction_status", lambda s:
                       int(s.eq("AWAITING_FINAL_RESULTS").sum())),
                   review_required=("prediction_status", lambda s:
                       int(s.isin(["EVIDENCE_REVIEW_AWAITING_RESULTS",
                                  "RESULT_REVIEW_REQUIRED",
                                  "EVIDENCE_REVIEW_REQUIRED"]).sum())),
                   evaluated_races=("prediction_status", lambda s:
                       int(s.eq("EVALUATED_PROVISIONAL").sum())))
              .reset_index())
    return totals[SUMMARY_COLUMNS]


def merge_locked_with_evaluated_metrics(locked_summary, evaluated_metrics, columns):
    """Left join: archived races stay visible when no results have been graded.

    The evaluated-races count comes from audited statuses, never from bets.
    No missing win percentage or ROI is filled with a misleading zero.
    """
    if locked_summary is None or locked_summary.empty:
        return pd.DataFrame(columns=columns)
    metrics = (evaluated_metrics if evaluated_metrics is not None else
               pd.DataFrame(columns=["track"]))
    metrics = metrics.drop(columns=["evaluated_races", "archived_races",
                                    "awaiting_results", "review_required"],
                           errors="ignore")
    return (locked_summary.merge(metrics, on="track", how="left",
                                 validate="one_to_one")
            .reindex(columns=columns))


LEGACY_ONLY_COLUMNS = ["race_date", "track", "race_number",
                       "prediction_batch_id", "saved_runner_rows"]


def legacy_only_saved_batches(predictions, archives, in_window, selected_track):
    """Show saved legacy batches lacking a matching v0.6.1 evidence archive.

    These might be earlier manual saves or partial bulk locks; do not relabel
    them as prospective evidence, evaluated predictions, or lost bets.
    """
    archived_ids = {str(a.get("batch_id")) for a in (archives or [])}
    count_by_key = {}
    for row in predictions or []:
        batch = str(row.get("prediction_batch_id") or "")
        date = str(row.get("race_date") or "")[:10]
        track = str(row.get("track") or "")
        if (not batch or batch in archived_ids or not in_window(date) or
                (selected_track != "All tracks" and track != selected_track)):
            continue
        key = (date, track, row.get("race_number"), batch)
        count_by_key[key] = count_by_key.get(key, 0) + 1
    data = [{
        "race_date": date, "track": track, "race_number": race_num,
        "prediction_batch_id": batch, "saved_runner_rows": count,
    } for (date, track, race_num, batch), count in count_by_key.items()]
    return (pd.DataFrame(data, columns=LEGACY_ONLY_COLUMNS)
            .sort_values(["race_date", "track", "race_number"], kind="stable")
            .reset_index(drop=True))
