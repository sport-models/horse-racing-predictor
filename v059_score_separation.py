"""v0.5.9 — read-only score separation and ranking confidence research.

Not a probability model. No bet settlement, no odds ingestion, no database writes,
no optimisation on the exposed comparison-only split, and no production promotion.
"""
from __future__ import annotations

from pathlib import Path
import math
import re

import pandas as pd

VERSION = "v0.5.9-score-separation-audit"
GAP_BANDS = ("<5", "5–<10", "10–<20", "≥20")
SPLIT_ORDER = ("development", "validation", "comparison_only_exposed", "saved_snapshot")
COHORT_ORDER = ("historical_retrieved_after_race", "saved_snapshot_unverified_start")
REQUIRED_RACE_COLUMNS = {
    "race_key", "race_date", "track", "race_number", "cohort", "split",
    "field_size", "field_size_band", "winner_rank", "top1_won", "winner_in_top3",
    "score_gap", "relative_gap", "gap_band", "sp_win_profit_1u",
    "point_in_time_verified", "evaluation_class",
}


def _key_date(value):
    return str(value)[:10]


def _key_track(value):
    return re.sub(r"[^a-z0-9]", "", str(value).lower())


def make_race_key(date, track, race):
    return f"{_key_date(date)}|{_key_track(track)}|R{int(race)}"


def band(gap):
    try:
        v = float(gap)
    except (TypeError, ValueError):
        return "INVALID"
    if not math.isfinite(v) or v < 0:
        return "INVALID"
    if v < 5:
        return "<5"
    if v < 10:
        return "5–<10"
    if v < 20:
        return "10–<20"
    return "≥20"


def field_band(size):
    n = int(size)
    if n <= 9:
        return "Small (1–9)"
    if n <= 14:
        return "Medium (10–14)"
    return "Large (15+)"


def _bool(value):
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)) and not pd.isna(value):
        if value in (0, 1):
            return bool(value)
    v = str(value).strip().casefold()
    if v in {"true", "t", "yes", "y", "1"}:
        return True
    if v in {"false", "f", "no", "n", "0", "nan", "", "none"}:
        return False
    raise ValueError(f"Invalid boolean: {value!r}")


def _finite(x):
    try:
        v = float(x)
        return math.isfinite(v)
    except (ValueError, TypeError):
        return False


def _validate_race_table(df):
    missing = sorted(REQUIRED_RACE_COLUMNS - set(df.columns))
    if missing:
        raise ValueError("Missing race-level columns: " + ", ".join(missing))
    return df


def historical_from_runner_rows(runners: pd.DataFrame, integrity: pd.DataFrame):
    """Rebuild the historical research data using preserved integrity split labels.

    Rows without eligibility matching race_integrity are NOT silently included.
    Information retrieved after the start is NOT a point-in-time valid backtest.
    """
    needed_r = {"date", "track", "race", "horse", "rank", "form_score", "finish", "scratched"}
    needed_i = {"race_key", "split", "runners", "point_in_time_verified", "retrieval_timing"}
    for label, frame, required in (("runners", runners, needed_r), ("integrity", integrity, needed_i)):
        absent = sorted(required - set(frame.columns))
        if absent:
            raise ValueError(f"{label} missing columns: {absent}")
    if integrity["race_key"].duplicated().any():
        raise ValueError("Integrity file contains duplicated race keys; cannot assign trusted splits")

    r = runners.copy()
    r["race_key"] = [make_race_key(x, y, z) for x, y, z in
                     zip(r["date"], r["track"], r["race"])]
    index = integrity.set_index("race_key")
    grouped = {key: frame for key, frame in r.groupby("race_key", sort=False)}
    out, exclusions = [], []
    for key, integrity_row in index.iterrows():
        group = grouped.get(key)
        if group is None:
            exclusions.append({"cohort": "historical", "race_key": key, "reason": "NO_RUNNER_ROWS"})
            continue
        try:
            expected = int(integrity_row["runners"])
            n = len(group)
            if n != expected or n < 2:
                raise ValueError("FIELD_SIZE_MISMATCH")
            if group["horse"].isna().any() or group["horse"].astype(str).str.strip().eq("").any():
                raise ValueError("MISSING_HORSE")
            if group["horse"].astype(str).str.casefold().duplicated().any():
                raise ValueError("DUPLICATE_HORSE")
            if any(_bool(x) for x in group["scratched"]):
                raise ValueError("SCRATCHED_IN_STARTERS")
            numeric_ranks = pd.to_numeric(group["rank"], errors="coerce")
            if numeric_ranks.isna().any() or sorted(numeric_ranks.tolist()) != list(range(1, n + 1)):
                raise ValueError("INVALID_OR_DUPLICATE_RANKS")
            g = group.copy()
            g["rank"] = numeric_ranks.astype(int)
            g = g.sort_values("rank")
            finishes = pd.to_numeric(g["finish"], errors="coerce")
            if finishes.isna().any() or int((finishes == 1).sum()) != 1:
                raise ValueError("INVALID_FINISH_OR_WINNER")
            top1, top2 = g.iloc[0], g.iloc[1]
            s1, s2 = top1["form_score"], top2["form_score"]
            if not _finite(s1) or not _finite(s2):
                raise ValueError("TOP_TWO_MISSING_SCORE")
            gap = float(s1) - float(s2)
            if band(gap) == "INVALID":
                raise ValueError("SCORE_RANK_CONTRADICTION")
            winner_rank = int(g.loc[finishes == 1, "rank"].iloc[0])
            split = str(integrity_row["split"])
            if split not in ("development", "validation", "comparison_only_exposed"):
                raise ValueError("UNEXPECTED_SPLIT")
            point_valid = _bool(integrity_row["point_in_time_verified"])
            # Historical pipeline retrieved ALL known batches after their races; even if
            # inconsistent flag appears, force independent audit instead of trusting it.
            timing = str(integrity_row["retrieval_timing"])
            if timing not in ("RETRIEVED_AFTER_START", "UNVERIFIED"):
                raise ValueError("UNKNOWN_RETRIEVAL_TIMING")
            if point_valid:
                raise ValueError("UNEXPECTED_PIT_TRUE_IN_RETROSPECTIVE_SOURCE")
            date_s, track_s, race_s = key.split("|")
            out.append({
                "race_key": key, "race_date": date_s, "track": track_s,
                "race_number": int(race_s[1:]),
                "cohort": "historical_retrieved_after_race", "split": split,
                "field_size": n, "field_size_band": field_band(n),
                "top1_score": float(s1), "top2_score": float(s2),
                "score_gap": gap, "relative_gap": gap / max(abs(float(s1)), 0.01),
                "gap_band": band(gap), "winner_rank": winner_rank,
                "winner_rank_fraction": (winner_rank - 1) / (n - 1),
                "top1_won": int(winner_rank == 1),
                "winner_in_top3": int(winner_rank <= 3),
                "top1_finishing_position": int(float(g.iloc[0]["finish"])),
                "point_in_time_verified": False,
                "evaluation_class": "RETROSPECTIVE_FEATURES_NOT_PIT_VERIFIED",
                "retrieval_timing": timing,
                "prediction_batch_id": "",
                "sp_win_profit_1u": float("nan"),
                "selection_promoted_after_scratch": False,
            })
        except (KeyError, ValueError, TypeError, IndexError) as exc:
            exclusions.append({"cohort": "historical", "race_key": key, "reason": str(exc)})
    racedf = pd.DataFrame(out)
    if not racedf.empty:
        _validate_race_table(racedf)
        racedf = racedf.sort_values(["race_date", "track", "race_number"]).reset_index(drop=True)
    excluded = pd.DataFrame(exclusions, columns=["cohort", "race_key", "reason"])
    return racedf, excluded


def saved_from_db(predictions, results):
    """Score saved *original* top-two ranks; never infer the gap from post-race scratches.

    v0.5.8 remains the official race eligibility/coverage evaluator. For this
    experiment, gap and winner rank are read from the earliest *original*
    saved prediction batch, independent of later scratches.
    """
    from v058_field_size import evaluate as existing_evaluate, horse_key, track_key
    from collections import defaultdict

    base, _, _, prior_exclusions = existing_evaluate(predictions, results)
    if base.empty:
        return pd.DataFrame(), prior_exclusions
    batches = defaultdict(list)
    for p in predictions:
        batch = p.get("prediction_batch_id")
        if batch is not None:
            batches[str(batch)].append(p)
    winner_by_race = defaultdict(list)
    for r in results:
        try:
            if _bool(r.get("scratched")):
                continue
            if int(r.get("finishing_position")) != 1:
                continue
            key = (_key_date(r["race_date"]), track_key(r["track"]), int(r["race_number"]))
            winner_by_race[key].append(horse_key(r.get("horse")))
        except (ValueError, TypeError, KeyError):
            continue

    out, exclusions = [], []
    for _, row in base.iterrows():
        obj = row.to_dict()
        key = make_race_key(obj["race_date"], obj["track"], obj["race_number"])
        try:
            if _bool(obj.get("selection_promoted_after_scratch", False)):
                raise ValueError("EX_POST_SCRATCH_SELECTION_PROMOTION")
            batch_id = str(obj["prediction_batch_id"])
            candidates = batches.get(batch_id, [])
            if len(candidates) < 2:
                raise ValueError("MISSING_OR_INCOMPLETE_ORIGINAL_SNAPSHOT")
            ranks = [int(p["model_rank"]) for p in candidates]
            if sorted(ranks) != list(range(1, len(ranks) + 1)):
                raise ValueError("INVALID_ORIGINAL_SNAPSHOT_RANKS")
            original = sorted(candidates, key=lambda p: int(p["model_rank"]))
            top1, top2 = original[:2]
            if horse_key(top1.get("horse")) != horse_key(obj["original_model_horse"]):
                raise ValueError("ORIGINAL_TOP1_MISMATCH")
            if not _finite(top1.get("form_score")) or not _finite(top2.get("form_score")):
                raise ValueError("MISSING_ORIGINAL_TOP2_SCORES")
            s1, s2 = float(top1["form_score"]), float(top2["form_score"])
            gap = s1 - s2
            if band(gap) == "INVALID":
                raise ValueError("INVALID_ORIGINAL_TOP2_SCORE_GAP")
            race_id = (str(obj["race_date"]), track_key(obj["track"]), int(obj["race_number"]))
            winners = winner_by_race.get(race_id, [])
            if len(winners) != 1:
                raise ValueError("MISSING_OR_DUPLICATE_OFFICIAL_WINNER")
            matches = [p for p in original if horse_key(p.get("horse")) == winners[0]]
            if len(matches) != 1:
                raise ValueError("WINNER_NOT_UNIQUE_IN_ORIGINAL_SNAPSHOT")
            original_winner_rank = int(matches[0]["model_rank"])
            if int(obj["model_won"]) != int(original_winner_rank == 1):
                raise ValueError("WIN_CHECK_MISMATCH")
            size = int(obj["field_size"])
            if size < 2:
                raise ValueError("FIELD_TOO_SMALL")
            active_gap = obj.get("top2_form_score_gap")
            changed = bool(_finite(active_gap) and abs(float(active_gap) - gap) > 0.000001)
            out.append({
                "race_key": key, "race_date": str(obj["race_date"]),
                "track": str(obj["track"]), "race_number": int(obj["race_number"]),
                "cohort": "saved_snapshot_unverified_start", "split": "saved_snapshot",
                "field_size": size, "field_size_band": field_band(size),
                "original_snapshot_field_size": len(original),
                "top1_score": s1, "top2_score": s2,
                "score_gap": gap, "relative_gap": gap / max(abs(s1), 0.01),
                "active_runner_gap_post_result": float(active_gap) if _finite(active_gap) else float("nan"),
                "gap_changed_by_scratches": changed,
                "gap_band": band(gap), "winner_rank": original_winner_rank,
                "active_winner_rank_post_result": int(obj["winner_rank_in_model"]),
                "winner_rank_fraction": (original_winner_rank - 1) / (len(original) - 1),
                "top1_won": int(original_winner_rank == 1),
                "winner_in_top3": int(original_winner_rank <= 3),
                "top1_finishing_position": int(obj["model_top_finish"]),
                "point_in_time_verified": False,
                "evaluation_class": "SAVED_ORIGINAL_SNAPSHOT_UNVERIFIED_START",
                "retrieval_timing": str(obj.get("time_validation", "UNVERIFIED")),
                "prediction_batch_id": batch_id,
                "prediction_at_utc": str(obj.get("prediction_at_utc", "")),
                "sp_win_profit_1u": float(obj["model_profit_1u"]),
                "selection_promoted_after_scratch": False,
            })
        except (ValueError, TypeError, KeyError) as exc:
            exclusions.append({"cohort": "saved_snapshot", "race_key": key, "reason": str(exc)})
    races = pd.DataFrame(out)
    if not races.empty:
        _validate_race_table(races)
        races = races.sort_values(["race_date", "track", "race_number"]).reset_index(drop=True)
    extras = pd.DataFrame(exclusions, columns=["cohort", "race_key", "reason"])
    if prior_exclusions is None or prior_exclusions.empty:
        return races, extras
    prev = prior_exclusions.copy()
    prev["cohort"] = "saved_snapshot"
    if "race_key" not in prev.columns:
        prev["race_key"] = prev.get("race", "")
    if "reason" not in prev.columns:
        prev["reason"] = "EXCLUDED_UPSTREAM"
    return races, pd.concat([prev[["cohort", "race_key", "reason"]], extras], ignore_index=True)


def wilson95(successes, n):
    """Frequentist 95% Wilson interval; descriptive, not calibrated confidence."""
    if n <= 0:
        return float("nan"), float("nan")
    p, z = successes / n, 1.959963984540054
    den = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / den
    margin = z * math.sqrt((p * (1 - p) + z * z / (4 * n)) / n) / den
    return max(0, centre - margin), min(1, centre + margin)


def auc_score_gap(frame):
    """Uncalibrated score-gap ranking AUC for top-1 win indicator.

    AUC of 0.5 means no discrimination. No fitting, no threshold tuning.
    """
    if frame.empty:
        return float("nan")
    y = frame["top1_won"].astype(int)
    positive = int(y.sum())
    negative = len(y) - positive
    if not positive or not negative:
        return float("nan")
    ranks = frame["score_gap"].astype(float).rank(ascending=True, method="average")
    return (float(ranks[y == 1].sum()) - positive * (positive + 1) / 2) / (positive * negative)


def _row_summary(g, **labels):
    n = len(g)
    if not n:
        return None
    wins = int(g["top1_won"].sum())
    top3 = int(g["winner_in_top3"].sum())
    low, high = wilson95(wins, n)
    # Some or all race SP prices can be missing. Never treat missing as zero.
    sp = pd.to_numeric(g.get("sp_win_profit_1u"), errors="coerce")
    complete_sp = sp is not None and sp.notna().all()
    return {
        **labels, "races": n, "distinct_dates": int(g["race_date"].nunique()),
        "top1_wins": wins, "top1_win_rate_pct": 100 * wins / n,
        "top1_wilson95_low_pct": 100 * low, "top1_wilson95_high_pct": 100 * high,
        "winner_in_model_top3": top3, "top3_hit_rate_pct": 100 * top3 / n,
        "mean_winner_rank": float(g["winner_rank"].mean()),
        "mean_score_gap": float(g["score_gap"].mean()),
        "median_score_gap": float(g["score_gap"].median()),
        "median_relative_gap": float(g["relative_gap"].median()) if g["relative_gap"].notna().any() else float("nan"),
        "win_only_SP_ROI_pct_if_complete": (100 * float(sp.mean()) if complete_sp else float("nan")),
        "SP_priced_races": int(sp.notna().sum()) if sp is not None else 0,
        "PIT_verified_races": int(g["point_in_time_verified"].map(_bool).sum()),
        "evaluation": "RESEARCH_ONLY_NOT_PIT_VALIDATED",
    }


def summarise(races):
    """Return (overall, by gap, field x gap, diagnostics). Cohorts never pool."""
    if races.empty:
        return (pd.DataFrame(),) * 4
    _validate_race_table(races)
    overall, bands, cross, diagnostics = [], [], [], []
    for (cohort, split), g in races.groupby(["cohort", "split"], sort=False):
        overall.append(_row_summary(g, cohort=cohort, split=split))
        diagnostics.append({
            "cohort": cohort, "split": split,
            "races": len(g), "distinct_dates": int(g["race_date"].nunique()),
            "score_gap_top1_win_AUC": auc_score_gap(g),
            "relative_gap_top1_win_AUC": auc_score_gap(g.rename(columns={"score_gap": "absolute_gap", "relative_gap": "score_gap"})),
            "PIT_verified_races": int(g["point_in_time_verified"].map(_bool).sum()),
            "thresholds_optimised": False,
            "comparison_only_exposed": split == "comparison_only_exposed",
            "decision": "NO_PROMOTION—UNVERIFIED_POINT_IN_TIME",
        })
        for gap_bin in GAP_BANDS:
            sub = g[g["gap_band"] == gap_bin]
            if not sub.empty:
                bands.append(_row_summary(sub, cohort=cohort, split=split, gap_band=gap_bin))
        for fbin in ("Small (1–9)", "Medium (10–14)", "Large (15+)"):
            for gap_bin in GAP_BANDS:
                sub = g[(g["field_size_band"] == fbin) & (g["gap_band"] == gap_bin)]
                if not sub.empty:
                    cross.append(_row_summary(sub, cohort=cohort, split=split,
                                              field_size_band=fbin, gap_band=gap_bin))
    return (pd.DataFrame(overall), pd.DataFrame(bands), pd.DataFrame(cross),
            pd.DataFrame(diagnostics))


def combine_without_leakage(historical: pd.DataFrame, saved: pd.DataFrame):
    """Add cohorts with strict duplicate/split guard. Summaries still group separately."""
    frames = [f for f in (historical, saved) if f is not None and not f.empty]
    if not frames:
        return pd.DataFrame()
    for f in frames:
        _validate_race_table(f)
        if f.duplicated(["cohort", "race_key"]).any():
            raise ValueError("Duplicate race identity within cohort")
        if f.groupby("race_key")["split"].nunique().gt(1).any():
            raise ValueError("A race key appears in conflicting splits")
    return pd.concat(frames, ignore_index=True, sort=False)


def load_bundled_historical():
    path = Path(__file__).with_name("v059_historical_race_features.csv")
    if not path.is_file():
        raise FileNotFoundError(f"Bundled historical features missing: {path.name}")
    historical = pd.read_csv(path, dtype={"race_key": "string", "race_date": "string", "track": "string",
                                           "split": "string", "cohort": "string"})
    _validate_race_table(historical)
    if historical["race_key"].isna().any() or historical.duplicated("race_key").any():
        raise ValueError("Bundled historical features have duplicated/missing race identifiers")
    if set(historical["split"]) != {"development", "validation", "comparison_only_exposed"}:
        raise ValueError("Historical split labels incomplete/unexpected")
    if historical["cohort"].ne("historical_retrieved_after_race").any():
        raise ValueError("Historical cohort label invalid")
    if historical["point_in_time_verified"].map(_bool).any():
        raise ValueError("Historical source incorrectly claims PIT verification")
    if (pd.to_numeric(historical["score_gap"], errors="coerce").map(band) == "INVALID").any():
        raise ValueError("Historical source contains invalid score gaps")
    return historical


def render(db_rows):
    """Streamlit display; diagnostics only. Supabase read access through db_rows."""
    import streamlit as st

    st.subheader("🧪 v0.5.9 — Ranking Confidence & Score Separation")
    st.caption("Frozen v0.2 | Read-only | Fixed score-gap bands (<5, 5–<10, 10–<20, ≥20) "
               "| No probability calibration or production changes.")
    st.warning("Historical feature scores were retrieved AFTER the races. They are not point-in-time "
               "predictions. Saved predictions also lack verified official-off timestamps. "
               "All results are exploratory; promotion is blocked.")
    st.info("The 1,040-race historical set has preserved chronological development / validation / "
            "EXPOSED comparison-only partitions. Exposed races are reported separately and never "
            "used to set thresholds. The two November Flemington meetings remain a separate cohort.")

    include_historical = st.checkbox("Include bundled retrospective 1,040-race audit", value=True,
                                     key="v059_use_historical")
    include_saved = st.checkbox("Include saved Supabase prediction snapshots", value=True,
                                 key="v059_use_saved")
    if st.button("Run v0.5.9 score-separation experiment", type="primary", key="v059_run"):
        try:
            historical, saved = pd.DataFrame(), pd.DataFrame()
            ex_hist, ex_saved = pd.DataFrame(), pd.DataFrame()
            if include_historical:
                historical = load_bundled_historical()
            if include_saved:
                predictions = db_rows("race_predictions", order="predicted_at")
                results = db_rows("runner_results", order=None)
                saved, ex_saved = saved_from_db(predictions, results)
            all_races = combine_without_leakage(historical, saved)
            overall, bands, cross, diagnostics = summarise(all_races)
            exclusions = pd.concat([x for x in (ex_hist, ex_saved) if not x.empty],
                                   ignore_index=True) if (not ex_hist.empty or not ex_saved.empty) else pd.DataFrame(
                                       columns=["cohort", "race_key", "reason"])
            st.session_state["v059_result"] = {
                "historical_races": historical, "saved_races": saved,
                "overall": overall, "gap_bands": bands, "field_x_gap": cross,
                "diagnostics": diagnostics, "exclusions": exclusions,
            }
        except Exception as exc:
            st.error(f"v0.5.9 stopped safely without writing data: {exc}")
            return

    data = st.session_state.get("v059_result")
    if not data:
        return
    if data["overall"].empty:
        st.info("No eligible races. Inspect exclusions and bundled data installation.")
    else:
        st.error("PRODUCTION PROMOTION GATE: CLOSED — historical inputs not PIT verified; "
                 "saved races also lack verified official start times. No weights or thresholds will change.")
        st.subheader("Cohort and split baseline (never pooled)")
        st.dataframe(data["overall"], hide_index=True, use_container_width=True)
        st.subheader("Predeclared score-gap bands")
        st.dataframe(data["gap_bands"], hide_index=True, use_container_width=True)
        st.subheader("Field size × score separation")
        st.dataframe(data["field_x_gap"], hide_index=True, use_container_width=True)
        st.subheader("Discrimination diagnostics (uncalibrated AUC)")
        st.dataframe(data["diagnostics"], hide_index=True, use_container_width=True)
        st.caption("Win rate confidence intervals are Wilson 95% intervals. "
                   "AUC measures ranking association, not win probability. "
                   "SP win ROI is available only when every race in that group has SP odds, "
                   "and never represents the custom win/each-way strategy.")
        if len(data["saved_races"]) < 100:
            st.warning(f"Saved cohort: {len(data['saved_races'])} races. "
                       "Insufficient for independent high-confidence rule promotion.")
    for name in ("historical_races", "saved_races", "overall", "gap_bands", "field_x_gap", "diagnostics", "exclusions"):
        frame = data[name]
        st.download_button(
            f"Download v0.5.9 {name.replace('_', ' ')} CSV",
            data=frame.to_csv(index=False).encode("utf-8"),
            file_name=f"v059_{name}.csv", mime="text/csv", key=f"v059_{name}_download",
        )
