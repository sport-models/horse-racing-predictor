import pandas as pd

from v061w_track_inventory import (
    build_locked_inventory, summarise_locked_inventory, prediction_status,
    merge_locked_with_evaluated_metrics,
)


def _archive(batch, number, track="Moe"):
    return {
        "batch_id": batch, "race_date": "2026-11-15", "track": track,
        "race_number": number, "created_at": "2026-11-14T23:00:00+00:00",
        "scheduled_start_utc": "2026-11-15T02:00:00+00:00",
        "payload": {"rankings": [
            {"rank": 1, "horse": "Locked Rank One", "score": 92},
            {"rank": 2, "horse": "Another Runner", "score": 80},
        ]},
    }


def _evaluated(batch, number, status, track="Moe"):
    return {
        "batch_id": batch, "race_date": "2026-11-15", "track": track,
        "race_number": number, "status": status,
        # Deliberately different: result-time scratch replacements must not be used
        "model_top1": "After-Race Replacement",
    }


def test_pending_and_review_races_visible_without_bets_or_results():
    archives = [_archive("pending", 1), _archive("graded", 2), _archive("bad", 3)]
    races = pd.DataFrame([
        _evaluated("pending", 1, "NO_FINAL_RESULTS"),
        _evaluated("graded", 2, "EVALUABLE_PROVISIONAL"),
        _evaluated("bad", 3, "INVALID_OR_INCOMPLETE_OFFICIAL_RESULTS"),
    ])
    inventory = build_locked_inventory(races, archives)
    assert list(inventory.race_number) == [1, 2, 3]
    assert inventory.loc[0, "rank1_at_lock"] == "Locked Rank One"
    assert set(inventory.prediction_status) == {
        "AWAITING_FINAL_RESULTS", "EVALUATED_PROVISIONAL", "RESULT_REVIEW_REQUIRED"}
    summary = summarise_locked_inventory(inventory)
    assert summary.iloc[0].to_dict() == {
        "track": "Moe", "archived_races": 3, "awaiting_results": 1,
        "review_required": 1, "evaluated_races": 1,
    }


def test_not_yet_bulk_finalised_races_do_not_appear():
    archives = [_archive("saved", 1), _archive("other_date", 2)]
    # Caller has already applied the local race-date filter to evaluations.
    today = pd.DataFrame([_evaluated("saved", 1, "NO_FINAL_RESULTS")])
    inventory = build_locked_inventory(today, archives)
    assert len(inventory) == 1
    assert inventory.iloc[0].batch_id == "saved"


def test_incomplete_evidence_is_not_silently_called_pending():
    assert (prediction_status("NO_FINAL_RESULTS|PAYLOAD_HASH_MISMATCH")
            == "EVIDENCE_REVIEW_AWAITING_RESULTS")
    assert prediction_status("PAYLOAD_HASH_MISMATCH") == "EVIDENCE_REVIEW_REQUIRED"
    assert prediction_status("EVALUABLE_PROVISIONAL") == "EVALUATED_PROVISIONAL"


def test_duplicate_archive_batch_must_be_flagged():
    archives = [_archive("dup", 1), _archive("dup", 1)]
    inventory = build_locked_inventory(
        pd.DataFrame([_evaluated("dup", 1, "NO_FINAL_RESULTS")]), archives)
    assert inventory.iloc[0].prediction_status == "EVIDENCE_REVIEW_REQUIRED"
    assert "DUPLICATE_ARCHIVE_BATCH" in inventory.iloc[0].raw_evaluation_status


def test_empty_date_selection_has_no_false_model_performance():
    inventory = build_locked_inventory(pd.DataFrame(), [_archive("a", 1)])
    assert inventory.empty
    assert summarise_locked_inventory(inventory).empty


def test_locked_races_populate_track_model_table_before_results():
    locked = summarise_locked_inventory(build_locked_inventory(
        pd.DataFrame([_evaluated("pending", 1, "NO_FINAL_RESULTS")]),
        [_archive("pending", 1)]))
    expected_columns = ["track", "archived_races", "awaiting_results",
                        "review_required", "evaluated_races",
                        "top1_winners", "top1_win_pct", "simulated_roi_pct"]
    performance = pd.DataFrame(columns=["track", "evaluated_races",
                                        "top1_winners", "top1_win_pct",
                                        "simulated_roi_pct"])
    output = merge_locked_with_evaluated_metrics(locked, performance, expected_columns)
    assert len(output) == 1
    assert output.iloc[0].archived_races == 1
    assert output.iloc[0].awaiting_results == 1
    assert output.iloc[0].evaluated_races == 0
    assert pd.isna(output.iloc[0].top1_win_pct)
    assert pd.isna(output.iloc[0].simulated_roi_pct)
