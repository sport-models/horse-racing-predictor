"""Regression coverage: genuine two-way official first-place dead heats."""
from datetime import datetime, timedelta, timezone
import math

import pytest

from v042_results import parse
from v061_prospective import digest, evaluate_one, simulated_win_profit_units


def _placings(first=2):
    return [
        {"name": "Horse A", "position": 1 if first >= 1 else 2,
         "scratched": False, "startingPrice": 4.5},
        {"name": "Horse B", "position": 1 if first >= 2 else 2,
         "scratched": False, "startingPrice": 7.5},
        {"name": "Horse C", "position": 1 if first >= 3 else 3,
         "scratched": False, "startingPrice": 10},
        {"name": "Horse D", "position": None, "scratched": True},
    ]


def _result_payload(placings):
    return {
        "resultStatus": "final", "abandoned": False,
        "date": "2026-11-15", "track": "Moe", "raceNumber": 5,
        "numberOfRunners": len(placings),
        "placings": placings,
    }


def test_two_way_dead_heat_preserves_both_official_first_place_rows():
    source = _result_payload(_placings(2))
    records = parse(source)
    winners = [x for x in records if x["finishing_position"] == 1]
    assert [x["horse"] for x in winners] == ["Horse A", "Horse B"]
    assert all(x["result_status"] == "final" for x in records)
    assert records[-1]["scratched"] is True


def test_single_winner_unchanged():
    records = parse(_result_payload(_placings(1)))
    assert sum(r["finishing_position"] == 1 for r in records) == 1


@pytest.mark.parametrize("first", [0, 3])
def test_missing_or_three_winners_still_blocked(first):
    with pytest.raises(ValueError, match="Expected one winner"):
        parse(_result_payload(_placings(first)))


def test_nonfinal_and_scratched_winners_blocked():
    bad = _result_payload(_placings(2))
    bad["resultStatus"] = "pending"
    with pytest.raises(ValueError, match="not final"):
        parse(bad)
    bad["resultStatus"] = "final"
    bad["placings"][0]["scratched"] = True
    with pytest.raises(ValueError, match="Scratched horse"):
        parse(bad)


def _archive():
    rankings = [
        {"horse": "Horse A", "rank": 1, "score": 90},
        {"horse": "Horse B", "rank": 2, "score": 85},
        {"horse": "Horse C", "rank": 3, "score": 83},
    ]
    source = {"date": "2026-11-15", "track": "Moe"}
    payload = {
        "model_version": "v0.2", "rankings": rankings,
        "form_response_raw": source,
        "form_response_sha256": digest(source),
        "bookmaker_capture_status": "CAPTURED",
        "source_times": {
            "form_response_received_utc": "2026-11-14T23:00:00+00:00",
            "ranking_generated_utc": "2026-11-14T23:01:00+00:00",
            "odds_response_received_utc": "2026-11-14T23:01:00+00:00",
        },
    }
    return {
        "batch_id": "m5", "race_date": "2026-11-15", "track": "Moe",
        "race_number": 5, "model_version": "v0.2",
        "created_at": "2026-11-14T23:02:00+00:00",
        "scheduled_start_utc": "2026-11-15T04:00:00+00:00",
        "payload": payload, "payload_sha256": digest(payload),
    }


def _results(winners=("Horse A", "Horse B")):
    results = []
    for name in ("Horse A", "Horse B", "Horse C"):
        results.append({
            "horse": name, "result_status": "final", "scratched": False,
            "finishing_position": 1 if name in winners else 3,
        })
    return results


def test_prospective_joint_winners_pass_integrity_and_preserve_top_pick(monkeypatch):
    import v061_prospective as p
    monkeypatch.setattr(p, "_canonical_market_quotes",
                        lambda payload, starters, cutoff:
                        ("sportsbet", {"horsea": 4.0, "horseb": 6.0, "horsec": 8.0}, None))
    report, issues, detail = evaluate_one(_archive(), _results())
    assert report["status"] == "EVALUABLE_PROVISIONAL", issues
    assert issues == []
    assert report["joint_first_count"] == 2
    assert report["is_dead_heat_first"] is True
    assert report["top1_won"] is True
    assert report["top3_hit"] is True
    assert set(report["winning_horses"]) == {"Horse A", "Horse B"}
    assert math.isclose(report["random_top1_expected"], 2 / 3)
    assert math.isclose(report["random_top3_expected"], 1)
    assert [r["won"] for r in detail] == [True, True, False]
    assert set(r["joint_first_count"] for r in detail) == {2}
    assert report["market_favourite_won"] is True


def test_other_joint_winner_can_win_when_original_rank1_does_not(monkeypatch):
    import v061_prospective as p
    monkeypatch.setattr(p, "_canonical_market_quotes",
                        lambda payload, starters, cutoff:
                        ("sportsbet", {"horsea": 4.0, "horseb": 6.0, "horsec": 8.0}, None))
    report, issues, detail = evaluate_one(
        _archive(), _results(winners=("Horse B", "Horse C")))
    assert report["status"] == "EVALUABLE_PROVISIONAL"
    assert report["top1_won"] is False
    assert report["top3_hit"] is True
    assert report["market_favourite_won"] is False
    assert [r["won"] for r in detail] == [False, True, True]


def test_single_winner_prospective_remains_evaluable():
    report, issues, _ = evaluate_one(_archive(), _results(winners=("Horse A",)))
    assert report["status"] == "EVALUABLE_PROVISIONAL", issues
    assert report["joint_first_count"] == 1
    assert report["is_dead_heat_first"] is False
    assert math.isclose(report["random_top1_expected"], 1 / 3)


def test_simulated_dead_heat_profit_never_pays_full_win_odds():
    assert simulated_win_profit_units(True, 5.0, 2) == 1.5
    assert simulated_win_profit_units(False, 5.0, 2) == -1
    assert simulated_win_profit_units(True, 5.0, 1) == 4.0


@pytest.mark.parametrize("count", [0, 3, None])
def test_invalid_dead_heat_winner_count_blocks_simulation(count):
    with pytest.raises(ValueError):
        simulated_win_profit_units(True, 5, count)


def test_zero_winners_in_imported_results_not_evaluable():
    report, issues, _ = evaluate_one(_archive(), _results(winners=()))
    assert report["status"] != "EVALUABLE_PROVISIONAL"
    assert "INVALID_OR_INCOMPLETE_OFFICIAL_RESULTS" in issues
