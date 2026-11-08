"""v0.6.0 — read-only consolidated horse racing validation hub.

Built on existing v0.5.9 race-level historical data and first ORIGINAL saved
prediction batches. No back-filled point-in-time status and no model tuning.

The apparent advantage over UNIFORM RANDOM RANKING is diagnostic only:
- not evidence of beating the betting market;
- historically scored form was retrieved AFTER race start;
- saved snapshots have no independently verified official race-off times.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import hashlib
import json
import math

import numpy as np
import pandas as pd

import v059_score_separation as v059

VERSION = "v0.6.0-consolidated-validation-read-only"
FROZEN_PRODUCTION = "v0.2"
HIST_SPLITS = ("development", "validation", "comparison_only_exposed")
EXPECTED_COHORT_SPLITS = {
    "historical_retrieved_after_race": set(HIST_SPLITS),
    "saved_snapshot_unverified_start": {"saved_snapshot"},
}
CSV_COLUMNS = {
    "ranking": ["cohort", "split", "races", "race_dates", "model_top1_wins", "model_top1_pct",
                "random_top1_expected", "random_top1_pct", "top1_excess_pp", "top1_lift_vs_random",
                "model_top3_hits", "model_top3_pct", "random_top3_expected", "random_top3_pct",
                "top3_excess_pp", "top3_lift_vs_random", "top3_excess_block_bootstrap95_low_pp",
                "top3_excess_block_bootstrap95_high_pp", "distinct_dates", "point_in_time_verified_races",
                "independent_market_comparison", "gate"],
    "issues": ["cohort", "split", "race_key", "issue_code", "detail"],
}


@dataclass
class Results:
    ranking: pd.DataFrame
    meetings: pd.DataFrame
    point_in_time: pd.DataFrame
    market: pd.DataFrame
    issues: pd.DataFrame
    experiment_register: pd.DataFrame
    promotion_gates: pd.DataFrame
    race_detail: pd.DataFrame
    manifest: dict


def sha256_file(path: Path) -> str:
    hasher = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1048576), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


def load_historical_verified_manifest(module_dir: Path | None = None) -> tuple[pd.DataFrame, dict]:
    """Verify packaged v059 historical aggregation against its immutable hash metadata.

    This is a data-integrity verification, NOT point-in-time verification.
    """
    folder = module_dir if module_dir is not None else Path(v059.__file__).parent
    data_file = folder / "v059_historical_race_features.csv"
    provenance_file = folder / "v059_historical_race_features.provenance.json"
    if not data_file.is_file() or not provenance_file.is_file():
        raise FileNotFoundError("v0.5.9 historical CSV and .provenance.json must both be installed")
    provenance = json.loads(provenance_file.read_text(encoding="utf-8"))
    hash_expected = provenance.get("output_sha256")
    hash_actual = sha256_file(data_file)
    if not hash_expected or hash_actual != hash_expected:
        raise ValueError("Bundled v0.5.9 historical CSV SHA256 does not match provenance manifest")
    if provenance.get("historical_point_in_time_verified") is not False:
        raise ValueError("Historical source manifest unexpectedly claims point-in-time verification")
    historical = v059.load_bundled_historical() if module_dir is None else pd.read_csv(data_file)
    if len(historical) != int(provenance["qualified_races"]):
        raise ValueError("Historical race count differs from provenance manifest")
    observed = {str(k): int(v) for k, v in historical.groupby("split").size().items()}
    expected = {str(k): int(v) for k, v in provenance["split_counts"].items()}
    if observed != expected:
        raise ValueError("Split populations differ from recorded v0.5.9 provenance")
    return historical, {"source_sha256": hash_actual, "provenance": provenance}


def _issue(cohort, split, key, code, detail="") -> dict:
    return {"cohort": cohort, "split": split, "race_key": key,
            "issue_code": code, "detail": str(detail)}


def validate_race_cohorts(historical: pd.DataFrame, saved: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Reject corrupt/inconsistent race observations, NEVER silently correct source values."""
    issues = []
    combined = []
    for label, source in (("historical", historical), ("saved", saved)):
        if source is None or source.empty:
            continue
        v059._validate_race_table(source)
        frame = source.copy()
        for cohort, split in frame[["cohort", "split"]].itertuples(index=False, name=None):
            if split not in EXPECTED_COHORT_SPLITS.get(str(cohort), set()):
                raise ValueError(f"Unexpected cohort/split combination: {cohort}/{split}")
        if frame["race_key"].isna().any() or frame["race_key"].duplicated().any():
            raise ValueError(f"{label}: missing or duplicate race identifiers; refusing misleading evaluation")
        for _, x in frame.iterrows():
            cohort, split, key = str(x["cohort"]), str(x["split"]), str(x["race_key"])
            reasons = []
            n = pd.to_numeric(x["field_size"], errors="coerce")
            rank = pd.to_numeric(x["winner_rank"], errors="coerce")
            original_size = (pd.to_numeric(x.get("original_snapshot_field_size"), errors="coerce")
                             if cohort == "saved_snapshot_unverified_start" else n)
            if pd.isna(n) or not float(n).is_integer() or int(n) < 2:
                reasons.append("INVALID_FIELD_SIZE")
            if pd.isna(original_size) or not float(original_size).is_integer():
                reasons.append("INVALID_ORIGINAL_RANK_FIELD_SIZE")
            if pd.isna(rank) or not float(rank).is_integer() or rank < 1 or (not pd.isna(original_size) and rank > original_size):
                reasons.append("INVALID_WINNER_RANK")
            if not reasons:
                expected_win, expected_top3 = int(rank == 1), int(rank <= 3)
                try:
                    actual_win, actual_top3 = int(x["top1_won"]), int(x["winner_in_top3"])
                except (TypeError, ValueError):
                    actual_win, actual_top3 = -1, -1
                if actual_win != expected_win or actual_top3 != expected_top3:
                    reasons.append("INCONSISTENT_OUTCOME_FLAGS")
            try:
                claimed_verified = v059._bool(x["point_in_time_verified"])
            except (TypeError, ValueError) as exc:
                reasons.append("INVALID_POINT_IN_TIME_FIELD")
                claimed_verified = False
            # No source used by v060 has been independently certified as pre-off.
            if claimed_verified:
                reasons.append("UNSUPPORTED_POINT_IN_TIME_CLAIM")
            if cohort == "historical_retrieved_after_race" and str(x.get("retrieval_timing", "")) not in (
                "RETRIEVED_AFTER_START", "UNVERIFIED"):
                reasons.append("UNKNOWN_HISTORICAL_RETRIEVAL_CLASS")
            if cohort == "saved_snapshot_unverified_start":
                if pd.isna(original_size) or (not pd.isna(n) and original_size < n):
                    reasons.append("INVALID_ORIGINAL_SNAPSHOT_FIELD_SIZE")
                if v059._bool(x.get("selection_promoted_after_scratch", False)):
                    reasons.append("EX_POST_SCRATCH_PROMOTION")
            for reason in reasons:
                issues.append(_issue(cohort, split, key, reason))
            if not reasons:
                # Distinct denominators: historical ranks over actual starters,
                # saved ORIGINAL ranks over all ORIGINAL predicted horses, including
                # subsequently scratched ones. Do not use post-result active size.
                denom = int(x.get("original_snapshot_field_size") if cohort ==
                            "saved_snapshot_unverified_start" else n)
                record = x.to_dict()
                record["uniform_rank_field_size"] = denom
                record["random_top1_probability"] = 1.0 / denom
                record["random_top3_probability"] = min(3, denom) / denom
                record["pit_certified_by_v060"] = False
                record["pit_blocker"] = (
                    "POST_RACE_FEATURE_RETRIEVAL" if cohort == "historical_retrieved_after_race"
                    else "OFF_TIME_AND_FEATURE_PROVENANCE_UNVERIFIED")
                combined.append(record)
    validated = pd.DataFrame(combined)
    if not validated.empty:
        # Historical and saved cohorts must not be silently joined or double-counted.
        if validated.duplicated(["cohort", "race_key"]).any():
            raise ValueError("Repeated races in same cohort")
        date_split = validated[validated["cohort"] == "historical_retrieved_after_race"].groupby("race_date")["split"].nunique()
        if (date_split > 1).any():
            raise ValueError("Historical partitions overlap on calendar dates — time leakage risk")
        valid_dates = validated[validated["cohort"] == "historical_retrieved_after_race"].groupby("split")["race_date"].agg(["min", "max"])
        if all(k in valid_dates.index for k in HIST_SPLITS):
            if not (valid_dates.loc["development", "max"] < valid_dates.loc["validation", "min"] and
                    valid_dates.loc["validation", "max"] < valid_dates.loc["comparison_only_exposed", "min"]):
                raise ValueError("Historical chronological development/validation/comparison ordering failed")
        validated = validated.sort_values(["cohort", "race_date", "track", "race_number"]).reset_index(drop=True)
    result_issues = pd.DataFrame(issues, columns=CSV_COLUMNS["issues"])
    return validated, result_issues


def _cluster_bootstrap_excess_pp(frame: pd.DataFrame, column: str, p_column: str,
                                 draws: int = 1200, seed: int = 600) -> tuple[float, float]:
    """Calendar-date block bootstrap of observed-minus-random %point lift.

    Descriptive ONLY. Especially fragile when only 2–4 race dates are available.
    """
    dates = sorted(frame["race_date"].astype(str).unique())
    if len(dates) < 2:
        return math.nan, math.nan
    by_date = []
    for d in dates:
        g = frame[frame["race_date"].astype(str) == d]
        by_date.append((len(g), (g[column].astype(float) - g[p_column].astype(float)).sum()))
    arr = np.asarray(by_date, dtype=float)
    rng = np.random.default_rng(seed)
    selection = rng.integers(0, len(arr), size=(draws, len(arr)))
    data = arr[selection]
    num = data[:, :, 1].sum(axis=1)
    den = data[:, :, 0].sum(axis=1)
    dist = 100 * num / den
    return float(np.percentile(dist, 2.5)), float(np.percentile(dist, 97.5))


def ranking_summary(validated: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    if validated.empty:
        return pd.DataFrame(columns=CSV_COLUMNS["ranking"]), pd.DataFrame()
    rows, meetings = [], []
    for (cohort, split), group in validated.groupby(["cohort", "split"], sort=True):
        n = len(group)
        top1, top3 = int(group["top1_won"].sum()), int(group["winner_in_top3"].sum())
        rand1, rand3 = float(group["random_top1_probability"].sum()), float(group["random_top3_probability"].sum())
        lo, hi = _cluster_bootstrap_excess_pp(group, "winner_in_top3", "random_top3_probability")
        rows.append({
            "cohort": cohort, "split": split, "races": n,
            "race_dates": f"{group['race_date'].min()} to {group['race_date'].max()}",
            "distinct_dates": int(group["race_date"].nunique()),
            "model_top1_wins": top1, "model_top1_pct": 100 * top1 / n,
            "random_top1_expected": rand1, "random_top1_pct": 100 * rand1 / n,
            "top1_excess_pp": 100 * (top1 - rand1) / n,
            "top1_lift_vs_random": top1 / rand1 if rand1 else math.nan,
            "model_top3_hits": top3, "model_top3_pct": 100 * top3 / n,
            "random_top3_expected": rand3, "random_top3_pct": 100 * rand3 / n,
            "top3_excess_pp": 100 * (top3 - rand3) / n,
            "top3_lift_vs_random": top3 / rand3 if rand3 else math.nan,
            "top3_excess_block_bootstrap95_low_pp": lo,
            "top3_excess_block_bootstrap95_high_pp": hi,
            "point_in_time_verified_races": 0,
            "independent_market_comparison": "NOT_AVAILABLE_FOR_HISTORICAL" if cohort.startswith("historical")
                                            else "RETROSPECTIVE_SP_ONLY_WHEN_PRICED",
            "gate": "CLOSED_NO_CERTIFIED_POINT_IN_TIME_DATA",
        })
        for (date, track), meet in group.groupby(["race_date", "track"]):
            nr = len(meet)
            rand_top3 = float(meet["random_top3_probability"].sum())
            meetings.append({"cohort": cohort, "split": split, "race_date": date,
                             "track": track, "races": nr,
                             "model_top1_wins": int(meet["top1_won"].sum()),
                             "model_top3_hits": int(meet["winner_in_top3"].sum()),
                             "uniform_random_top3_expected": rand_top3,
                             "top3_excess_pp": 100 * (float(meet["winner_in_top3"].sum()) - rand_top3) / nr,
                             "pit_verified": False})
    return pd.DataFrame(rows, columns=CSV_COLUMNS["ranking"]), pd.DataFrame(meetings)


def timing_audit(validated: pd.DataFrame) -> pd.DataFrame:
    cols = ["cohort", "split", "race_key", "race_date", "track", "prediction_batch_id",
            "prediction_at_utc", "official_start_verified_at_utc", "input_features_timestamped_pre_off",
            "saved_bookmaker_quote_timestamps_verified", "certified_point_in_time", "blocker"]
    if validated.empty:
        return pd.DataFrame(columns=cols)
    rows = []
    for _, r in validated.iterrows():
        pred = r.get("prediction_at_utc", "")
        if pd.isna(pred):
            pred = ""
        rows.append({
            "cohort": r["cohort"], "split": r["split"], "race_key": r["race_key"],
            "race_date": r["race_date"], "track": r["track"],
            "prediction_batch_id": ("" if pd.isna(r.get("prediction_batch_id")) else str(r.get("prediction_batch_id"))),
            "prediction_at_utc": str(pred),
            "official_start_verified_at_utc": "", "input_features_timestamped_pre_off": False,
            "saved_bookmaker_quote_timestamps_verified": False,
            "certified_point_in_time": False, "blocker": r["pit_blocker"],
        })
    return pd.DataFrame(rows, columns=cols)


def market_sp_paired(saved_market_races: pd.DataFrame, validated: pd.DataFrame) -> pd.DataFrame:
    """Compare the SAME saved races to the SP favourite (retrospective only).

    Takes v058.evaluate race output. No fabricated odds or partial population ROI.
    """
    cols = ["cohort", "race_date", "track", "race_number", "race_key", "model_win",
            "sp_favourite_win", "model_sp_profit_1u", "sp_favourite_profit_1u",
            "original_top1_horse", "sp_favourite_horse", "evidence_class"]
    if saved_market_races is None or saved_market_races.empty or validated.empty:
        return pd.DataFrame(columns=cols)
    val = validated[validated["cohort"] == "saved_snapshot_unverified_start"]
    good_keys = set(val["race_key"])
    pairs = []
    seen_market_keys = set()
    for _, race in saved_market_races.iterrows():
        key = v059.make_race_key(race["race_date"], race["track"], race["race_number"])
        if key in seen_market_keys:
            raise ValueError("Duplicate starting-price benchmark race — cannot compute honest paired ROI")
        seen_market_keys.add(key)
        if key not in good_keys:
            continue
        profit_model = pd.to_numeric(race.get("model_profit_1u"), errors="coerce")
        profit_fav = pd.to_numeric(race.get("favourite_profit_1u"), errors="coerce")
        if pd.isna(profit_model) or pd.isna(profit_fav):
            continue
        pairs.append({"cohort": "saved_snapshot_unverified_start", "race_date": race["race_date"],
                      "track": race["track"], "race_number": int(race["race_number"]),
                      "race_key": key, "model_win": int(race["model_won"]),
                      "sp_favourite_win": int(race["favourite_won"]),
                      "model_sp_profit_1u": float(profit_model),
                      "sp_favourite_profit_1u": float(profit_fav),
                      "original_top1_horse": race["original_model_horse"],
                      "sp_favourite_horse": race["favourite_horse"],
                      "evidence_class": "POST_RACE_STARTING_PRICE_NOT_EX_ANTE_MARKET_FAVOURITE"})
    return pd.DataFrame(pairs, columns=cols)


def experiment_register() -> pd.DataFrame:
    """Narrative conclusions carried forward; not a rerun of retired feature grids."""
    rows = [
        ("v0.5.1", "Track-condition weighting", "Prior experiment", "NOT_PROMOTED", "Prior tests did not establish validation improvement", "EARLIER_REPORTED_RESULT"),
        ("v0.5.2", "Track specialist", "Prior experiment", "NOT_PROMOTED", "Prior tests did not establish repeatable improvement", "EARLIER_REPORTED_RESULT"),
        ("v0.5.6c", "Speed-map settling advantage", "Prior experiment", "NOT_PROMOTED", "Validation improvement was zero in previous tests", "EARLIER_REPORTED_RESULT"),
        ("v0.5.7", "Historical feature integrity", "Audit", "COMPLETED_LIMITATIONS", "Historical form retrieval after races / no certified PIT", "SOURCE_INTEGRITY_AUDIT"),
        ("v0.5.7g", "Betting strategy and place terms", "Market audit", "COMPLETED_LIMITATIONS", "Missing historical place terms and quotes; no validated custom strategy ROI", "PRIOR_SAVED_MARKET_AUDITS"),
        ("v0.5.8", "Field-size segmentation", "Exploratory", "NOT_PROMOTED", "Two November saved meets / 18 races, no certified PIT", "SOURCE_COHORT_RECOMPUTED"),
        ("v0.5.9", "Original top-two score gap", "Exploratory", "NOT_PROMOTED", "Weak/unstable AUC across chronological splits, no certified PIT", "SOURCE_COHORT_RECOMPUTED"),
        ("v0.6.0", "Field-adjusted ranking and evidence gates", "Consolidation", "NO_PRODUCTION_CHANGE", "Compare winner ranking to uniform chance; freeze v0.2", "CURRENT_RACE_LEVEL_AUDIT"),
    ]
    return pd.DataFrame(rows, columns=["version", "hypothesis", "type", "decision",
                                       "reason", "evidence_status"])


def promotion_gates(validated: pd.DataFrame, market: pd.DataFrame, issues: pd.DataFrame) -> pd.DataFrame:
    h = validated[validated["cohort"] == "historical_retrieved_after_race"] if not validated.empty else pd.DataFrame()
    s = validated[validated["cohort"] == "saved_snapshot_unverified_start"] if not validated.empty else pd.DataFrame()
    return pd.DataFrame([
        {"gate": "Frozen production v0.2", "observed": "No production edits in v0.6.0", "passed": True,
         "decision": "PRESERVE"},
        {"gate": "Historical point-in-time features", "observed": f"0/{len(h)} certified historical races",
         "passed": False, "decision": "BLOCK_PROMOTION"},
        {"gate": "Saved prediction point-in-time verification", "observed": f"0/{len(s)} certified saved races",
         "passed": False, "decision": "BLOCK_PROMOTION"},
        {"gate": "Sufficient fresh independent forward sample", "observed": "0 certified prospectively held-out races",
         "passed": False, "decision": "COLLECT_NEW_DATA"},
        {"gate": "Unexposed untouched future holdout", "observed": "Historical comparison-only split already exposed",
         "passed": False, "decision": "NEW_HOLDOUT_REQUIRED"},
        {"gate": "Priced paired win comparison", "observed": f"{len(market)} SP-priced pairs; SP is retrospective",
         "passed": False, "decision": "PRE_RACE_MARKET_SNAPSHOTS_REQUIRED"},
        {"gate": "Validated $2 place-odds strategy", "observed": "Place-payment terms and snapshot price completeness unverified",
         "passed": False, "decision": "NOT_EVALUABLE_AS_FULL_STRATEGY"},
        {"gate": "Race-level integrity after exclusions", "observed": f"{len(issues)} rejected race records",
         "passed": issues.empty, "decision": "AUDIT"},
        {"gate": "Consolidated production promotion decision", "observed": "Insufficient independently verified evidence",
         "passed": False, "decision": "NO_PROMOTION"},
    ])


def evaluate(historical: pd.DataFrame, saved: pd.DataFrame, saved_market_races: pd.DataFrame | None = None,
             integrity_metadata: dict | None = None,
             saved_source_exclusions: pd.DataFrame | None = None) -> Results:
    validated, issues = validate_race_cohorts(historical, saved)
    if saved_source_exclusions is not None and not saved_source_exclusions.empty:
        for _, item in saved_source_exclusions.iterrows():
            issues = pd.concat([issues, pd.DataFrame([_issue("saved_snapshot_unverified_start", "saved_snapshot",
                                   item.get("race_key", item.get("race", "")),
                                   "SAVED_SOURCE_EXCLUSION", item.get("reason", ""))])], ignore_index=True)
    ranking, meetings = ranking_summary(validated)
    pit = timing_audit(validated)
    market = market_sp_paired(saved_market_races, validated)
    registry = experiment_register()
    gates = promotion_gates(validated, market, issues)
    manifest = {
        "version": VERSION, "frozen_production": FROZEN_PRODUCTION,
        "historical_races": int((validated["cohort"] == "historical_retrieved_after_race").sum()) if not validated.empty else 0,
        "saved_races": int((validated["cohort"] == "saved_snapshot_unverified_start").sum()) if not validated.empty else 0,
        "rejected_issues": len(issues),
        "certified_point_in_time_races": 0,
        "promoted_features": [],
        "comparison_only_split_previously_exposed": True,
        "historical_inputs_retrieved_post_race": True,
        "market_comparison_type": "RETROSPECTIVE_STARTING_PRICE_ONLY",
        "historical_source": integrity_metadata or {},
    }
    return Results(ranking, meetings, pit, market, issues, registry, gates, validated, manifest)


def render(db_rows):
    import streamlit as st

    st.subheader("🧪 v0.6.0 — Consolidated Model Validation")
    st.caption("Read-only · Frozen production v0.2 · Original saved prediction ranks · No betting-account changes")
    st.error("PROMOTION GATE CLOSED: Historical form data was retrieved after race start; saved predictions "
             "lack verified official off-time and feature-input provenance. Previously exposed comparison "
             "data is NOT a fresh holdout.")
    st.info("Uniform random ranking is a *sanity-check baseline*, not a bookmaker baseline. "
            "For saved snapshots, the random denominator is the ORIGINAL number of predicted horses "
            "(including subsequently scratched runners), not just the active field.")
    use_historical = st.checkbox("Include 1,040-race historical audit (post-race features)", value=True,
                                 key="v060_use_historical")
    use_saved = st.checkbox("Include original saved Streamlit predictions", value=True,
                            key="v060_use_saved")
    if st.button("Run v0.6.0 consolidated validation", type="primary", key="v060_run"):
        try:
            h, meta = (load_historical_verified_manifest() if use_historical else (pd.DataFrame(), {}))
            s, saved_ex, saved_market = pd.DataFrame(), pd.DataFrame(), pd.DataFrame()
            if use_saved:
                predictions = db_rows("race_predictions", order="predicted_at")
                results = db_rows("runner_results", order=None)
                s, saved_ex = v059.saved_from_db(predictions, results)
                # v058 is the canonical source for scratch-aware paired SP information.
                import v058_field_size as v058
                saved_market, _, _, market_ex = v058.evaluate(predictions, results)
                if market_ex is not None and not market_ex.empty:
                    saved_ex = pd.concat([saved_ex, market_ex], ignore_index=True)
            output = evaluate(h, s, saved_market, meta, saved_ex)
            st.session_state["v060_output"] = output
        except Exception as exc:
            st.error(f"v0.6.0 stopped safely without writing data: {type(exc).__name__}: {exc}")
            return
    output = st.session_state.get("v060_output")
    if output is None:
        return

    st.subheader("1. Field-adjusted ranking vs uniform random baseline")
    if not output.ranking.empty:
        st.dataframe(output.ranking, hide_index=True, use_container_width=True)
        st.caption("Top-3 expected chance = min(3, original ranking field size) / original ranking field size. "
                   "The date-block bootstrap is descriptive and fragile for splits with only 2–4 dates. "
                   "The historical scores are *not* verified pre-race predictions.")
    else:
        st.info("No eligible race-level observations. Inspect exclusions.")

    st.subheader("2. Meeting/day stability")
    if not output.meetings.empty:
        st.dataframe(output.meetings, hide_index=True, use_container_width=True)
    st.subheader("3. Point-in-time audit")
    counts = output.point_in_time.groupby(["cohort", "split", "blocker"], dropna=False).size().reset_index(name="races") \
        if not output.point_in_time.empty else pd.DataFrame()
    if not counts.empty:
        st.dataframe(counts, hide_index=True, use_container_width=True)
    st.warning("A saved prediction timestamp alone does NOT verify that input form, odds and "
               "race-off timestamps were all available beforehand. No race is certified by v0.6.0.")

    st.subheader("4. Matched SP favourite comparison (not executable pre-race)")
    if not output.market.empty:
        n = len(output.market)
        model_w = int(output.market.model_win.sum())
        market_w = int(output.market.sp_favourite_win.sum())
        model_roi = 100 * float(output.market.model_sp_profit_1u.mean())
        market_roi = 100 * float(output.market.sp_favourite_profit_1u.mean())
        st.info(f"{n} paired races, model {model_w} wins vs SP favourite {market_w}; "
                f"hypothetical $10 win-only ROI at SP: model {model_roi:+.2f}% vs "
                f"SP favourite {market_roi:+.2f}%. This is NOT your win/each-way strategy.")
        st.dataframe(output.market, hide_index=True, use_container_width=True)
    else:
        st.info("No complete saved race pairs with starting prices in this run.")
    st.subheader("5. Experiment register (previous results are not re-run)")
    st.dataframe(output.experiment_register, hide_index=True, use_container_width=True)
    st.subheader("6. Promotion gates")
    st.dataframe(output.promotion_gates, hide_index=True, use_container_width=True)
    if not output.issues.empty:
        st.subheader("7. Explicit exclusions / integrity issues")
        st.dataframe(output.issues, hide_index=True, use_container_width=True)
    st.success("v0.6.0 research audit completed. Production v0.2, bets, bonus credits, "
               "settlement, model rankings, and Supabase records are unchanged.")
    for name, obj in (
        ("ranking_baselines", output.ranking),
        ("meeting_consistency", output.meetings),
        ("point_in_time_audit", output.point_in_time),
        ("sp_paired_market", output.market),
        ("integrity_issues", output.issues),
        ("experiment_register", output.experiment_register),
        ("promotion_gates", output.promotion_gates),
        ("race_level_audit", output.race_detail),
    ):
        st.download_button("Download v0.6.0 " + name.replace("_", " ") + " CSV",
                           data=obj.to_csv(index=False).encode("utf-8"),
                           file_name="v060_" + name + ".csv", mime="text/csv",
                           key="v060_export_" + name)
    st.download_button("Download v0.6.0 run manifest JSON",
                       data=json.dumps(output.manifest, indent=2, sort_keys=True).encode("utf-8"),
                       file_name="v060_run_manifest.json", mime="application/json",
                       key="v060_export_manifest")
