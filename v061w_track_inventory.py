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


def _race_match_key(item):
    """Mirror the race identity used by prospective evaluation."""
    try:
        number = int(item.get("race_number"))
    except (TypeError, ValueError, OverflowError):
        return None
    return (
        str(item.get("race_date") or "")[:10],
        " ".join(str(item.get("track") or "").casefold().split()),
        number,
    )


def build_result_join_diagnostics(visible_races, imported_results):
    """Explain why a saved lock is absent from *evaluated* model performance.

    Read the exact runner_results rows already read by Performance by Track.
    This never treats bet settlements or historical research imports as
    completed official runner_results imports.
    """
    columns = [
        "race_date", "track", "race_number", "batch_id",
        "imported_result_rows", "final_result_rows", "starter_rows",
        "scratched_rows", "winner_rows", "evaluation_status", "diagnosis",
    ]
    if visible_races is None or visible_races.empty:
        return pd.DataFrame(columns=columns)
    from collections import defaultdict
    grouped = defaultdict(list)
    for result in imported_results or []:
        key = _race_match_key(result)
        if key:
            grouped[key].append(result)
    rows = []
    for race in visible_races.to_dict("records"):
        results = grouped.get(_race_match_key(race), [])
        final = sum(r.get("result_status") == "final" for r in results)
        starters = [r for r in results if r.get("scratched") is False]
        scratched = sum(r.get("scratched") is True for r in results)
        winners = sum(r.get("finishing_position") == 1 for r in starters)
        status = str(race.get("status") or "UNASSESSED")
        if status == "EVALUABLE_PROVISIONAL":
            diagnosis = "EVALUATED_OK"
        elif not results:
            diagnosis = "NO_MATCHING_RUNNER_RESULTS"
        elif final != len(results):
            diagnosis = "IMPORTED_RESULTS_NOT_ALL_FINAL"
        elif winners != 1:
            diagnosis = "INVALID_OR_INCOMPLETE_WINNER_ROWS"
        elif "INVALID_OR_INCOMPLETE_OFFICIAL_RESULTS" in status:
            diagnosis = "OFFICIAL_RESULT_INTEGRITY_REVIEW"
        elif "ACTUAL_STARTER_MISSING_FROM_SNAPSHOT" in status:
            diagnosis = "OFFICIAL_STARTER_NOT_IN_LOCKED_RANKINGS"
        elif "ORIGINAL_TOP1_SCRATCHED_RETROSPECTIVE_SUBSTITUTION" in status:
            diagnosis = "ORIGINAL_TOP_RANKED_HORSE_SCRATCHED"
        else:
            diagnosis = "ARCHIVE_OR_TIMING_EVIDENCE_REVIEW"
        rows.append({
            "race_date": str(race.get("race_date") or "")[:10],
            "track": str(race.get("track") or ""),
            "race_number": race.get("race_number"),
            "batch_id": race.get("batch_id"),
            "imported_result_rows": len(results),
            "final_result_rows": final,
            "starter_rows": len(starters),
            "scratched_rows": scratched,
            "winner_rows": winners,
            "evaluation_status": status,
            "diagnosis": diagnosis,
        })
    return pd.DataFrame(rows, columns=columns)


def result_import_reconciliation(visible_races, imported_results, start_date=None, end_date=None):
    """Read-only, result-side complement to the per-archive matching audit.

    Shows whether runner_results contains any imported rows in the date window,
    and whether those results are keyed to a locked archive race. Do not infer
    that another table or provider has a completed official import.
    """
    locked_keys = set()
    if visible_races is not None and not visible_races.empty:
        locked_keys = {_race_match_key(x) for x in visible_races.to_dict("records")}
    result_rows = {}
    day_counts = {}
    for r in imported_results or []:
        k = _race_match_key(r)
        if k is None:
            continue
        day_counts[k[0]] = day_counts.get(k[0], 0) + 1
        if start_date is not None and k[0] < start_date:
            continue
        if end_date is not None and k[0] > end_date:
            continue
        result_rows.setdefault(k, []).append(r)
    report = []
    for (race_day, track_key, number), items in sorted(result_rows.items()):
        report.append({
            "race_date": race_day,
            "results_track": str(items[0].get("track") or ""),
            "race_number": number,
            "imported_runner_rows": len(items),
            "all_rows_final": all(x.get("result_status") == "final" for x in items),
            "matches_selected_locked_race": (race_day, track_key, number) in locked_keys,
        })
    columns = ["race_date", "results_track", "race_number",
               "imported_runner_rows", "all_rows_final",
               "matches_selected_locked_race"]
    other_dates = (pd.DataFrame([
        {"race_date": day, "imported_runner_rows": n}
        for day, n in day_counts.items()
    ], columns=["race_date", "imported_runner_rows"])
      .sort_values("race_date", ascending=False, kind="stable")
      .reset_index(drop=True))
    return pd.DataFrame(report, columns=columns), other_dates
