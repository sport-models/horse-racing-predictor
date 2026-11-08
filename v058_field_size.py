"""v0.5.8 — field-size hypothesis, read-only, earliest saved predictions.

This is a segmentation/coverage experiment, NOT a reweighted model. Retrospective
starter field size and starting prices are not verified executable pre-race data.
"""
from __future__ import annotations

from collections import defaultdict
import math
import re
import unicodedata

import pandas as pd


def horse_key(name):
    return re.sub(r"[^a-z0-9]", "", unicodedata.normalize("NFKD", str(name or "")).encode("ascii", "ignore").decode().casefold())


def track_key(name):
    return re.sub(r"[^a-z0-9]", "", str(name or "").casefold())


def bucket(n):
    if n <= 9:
        return "Small (1–9)"
    if n <= 14:
        return "Medium (10–14)"
    return "Large (15+)"


BUCKET_ORDER = ["Small (1–9)", "Medium (10–14)", "Large (15+)"]


def _first_complete_batch(predictions, prediction_batch_id):
    rows = [p for p in predictions if str(p.get("prediction_batch_id")) == str(prediction_batch_id)]
    return rows


def evaluate(predictions, results):
    """Evaluate the two saved meetings (and any other fully matched races).

    v057c.benchmark is the single source of truth for scratch-aware eligibility.
    Results: (races, bucket_summary, meet_summary, exclusions).
    """
    from v057c_corrected_benchmark import benchmark as existing_benchmark

    base, excluded = existing_benchmark(predictions, results)
    if base.empty:
        return pd.DataFrame(), pd.DataFrame(), pd.DataFrame(), excluded

    p_by = defaultdict(list)
    r_by = defaultdict(list)
    for p in predictions:
        try:
            race = (str(p["race_date"])[:10], track_key(p["track"]), int(p["race_number"]))
            p_by[race].append(p)
        except (KeyError, ValueError, TypeError):
            continue
    for r in results:
        try:
            race = (str(r["race_date"])[:10], track_key(r["track"]), int(r["race_number"]))
            r_by[race].append(r)
        except (KeyError, ValueError, TypeError):
            continue

    enriched = []
    more_issues = []
    for _, base_row in base.iterrows():
        obj = base_row.to_dict()
        key = (obj["race_date"], track_key(obj["track"]), int(obj["race_number"]))
        saved = _first_complete_batch(p_by[key], obj["prediction_batch_id"])
        starters = [r for r in r_by[key] if r.get("scratched") is False]
        by_horse = {horse_key(r.get("horse")): r for r in starters}
        try:
            active = sorted(
                (p for p in saved if horse_key(p.get("horse")) in by_horse),
                key=lambda p: int(p["model_rank"]),
            )
            if len(active) != len(starters):
                raise ValueError("STARTER_COVERAGE_DISCREPANCY")
            winner_rank = next(i + 1 for i, p in enumerate(active)
                               if int(by_horse[horse_key(p["horse"])]["finishing_position"]) == 1)
            top_finish = int(by_horse[horse_key(active[0]["horse"])]["finishing_position"])
            n = len(active)
            if n < 2:
                raise ValueError("FIELD_TOO_SMALL")
            try:
                scores = [float(p.get("form_score")) for p in active[:2]]
                score_gap = scores[0] - scores[1] if all(math.isfinite(x) for x in scores) else None
            except (ValueError, TypeError):
                score_gap = None
            obj.update({
                "field_size_band": bucket(n),
                "winner_rank_in_model": winner_rank,
                "winner_rank_fraction": (winner_rank - 1) / (n - 1),
                "winner_rank_reciprocal": 1 / winner_rank,
                "model_top_finish": top_finish,
                "model_top_finished_top3": int(top_finish <= 3),
                "winner_in_model_top3": int(winner_rank <= 3),
                "top2_form_score_gap": score_gap,
                "evaluation_class": "RETROSPECTIVE_SNAPSHOT_UNVERIFIED_PIT",
                "experiment_version": "v0.5.8-field-size-exploration",
            })
            enriched.append(obj)
        except (ValueError, TypeError, KeyError, StopIteration) as exc:
            more_issues.append({"race": f'{key[0]} {key[1]} R{key[2]}',
                                "reason": "RANKING_ENRICHMENT_FAILED", "detail": str(exc)})

    racedf = pd.DataFrame(enriched)
    issues = pd.concat([excluded, pd.DataFrame(more_issues)], ignore_index=True)
    if racedf.empty:
        return racedf, pd.DataFrame(), pd.DataFrame(), issues

    def summarize(group_cols):
        rows = []
        for group, frame in racedf.groupby(group_cols, sort=True, dropna=False):
            label = group if isinstance(group, tuple) else (group,)
            n = len(frame)
            m_wins = int(frame["model_won"].sum())
            f_wins = int(frame["favourite_won"].sum())
            m_profit = float(frame["model_profit_1u"].sum()) * 10
            f_profit = float(frame["favourite_profit_1u"].sum()) * 10
            rows.append(dict(zip(group_cols, label), **{
                "Races": n,
                "Model wins": m_wins,
                "Model win %": round(100 * m_wins / n, 3),
                "SP favourite wins": f_wins,
                "SP favourite win %": round(100 * f_wins / n, 3),
                "Model top-three finishes": int(frame["model_top_finished_top3"].sum()),
                "Winner ranked top-three": int(frame["winner_in_model_top3"].sum()),
                "Mean winning horse rank": round(float(frame["winner_rank_in_model"].mean()), 3),
                "Mean reciprocal winner rank": round(float(frame["winner_rank_reciprocal"].mean()), 3),
                "Model $10 win P/L at SP": round(m_profit, 2),
                "Model $10 win ROI % at SP": round(100 * m_profit / (n * 10), 3),
                "SP favourite $10 P/L": round(f_profit, 2),
                "SP favourite $10 ROI %": round(100 * f_profit / (n * 10), 3),
                "Evaluation": "EXPLORATORY — UNVERIFIED POINT IN TIME",
            }))
        return pd.DataFrame(rows)

    bands = summarize(["field_size_band"])
    if not bands.empty:
        bands["field_size_band"] = pd.Categorical(bands["field_size_band"], BUCKET_ORDER, ordered=True)
        bands = bands.sort_values("field_size_band").reset_index(drop=True)
        bands["field_size_band"] = bands["field_size_band"].astype(str)
    meets = summarize(["race_date", "track"])
    return racedf, bands, meets, issues


def render(db_rows):
    import streamlit as st

    st.subheader("🧪 v0.5.8 — Field-Size Sensitivity | Frozen v0.2")
    st.caption("Read-only, research only. Uses both saved Flemington meetings (5 & 7 November 2026) "
               "when those races exist in Supabase, plus any other eligible saved races. "
               "No scoring, bet or production changes.")
    st.warning("The historical starter field and starting prices are retrospective. "
               "Official race-off times and feature availability are unverified. "
               "This is a small-sample exploratory test, not proven forward performance.")
    if not st.button("Run v0.5.8 field-size experiment", key="v058_field_run"):
        return
    try:
        predictions = db_rows("race_predictions", order="predicted_at")
        official_results = db_rows("runner_results", order=None)
        races, bands, meets, exclusions = evaluate(predictions, official_results)
    except Exception as exc:
        st.error(f"Field-size experiment failed safely without database writes: {exc}")
        return
    if races.empty:
        st.info("No matched complete races. Inspect the exclusions and existing v0.5.7c diagnostic.")
    else:
        dates = sorted(races["race_date"].unique().tolist())
        st.info(f"Evaluated {len(races)} races from {len(meets)} meetings: {', '.join(dates)}. "
                "Buckets are predeclared: 1–9, 10–14 and 15+ actual starters.")
        st.dataframe(meets, hide_index=True, use_container_width=True)
        st.subheader("Field-size segments")
        st.dataframe(bands, hide_index=True, use_container_width=True)
        st.subheader("Race-level ranking audit")
        st.dataframe(races, hide_index=True, use_container_width=True)
        if len(races) < 100 or len(dates) < 5:
            st.error("PROMOTION GATE: CLOSED. Fewer than 100 eligible races or five distinct race dates. "
                     "No field-size rule or model weights may be promoted on this sample.")
    for name, frame in [("races", races), ("field_bands", bands), ("meetings", meets), ("exclusions", exclusions)]:
        st.download_button(f"Download v0.5.8 {name} CSV", frame.to_csv(index=False).encode("utf-8"),
                           file_name=f"v058_{name}.csv", mime="text/csv", key=f"v058_download_{name}")
