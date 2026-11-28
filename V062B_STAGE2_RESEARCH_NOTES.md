# v0.6.2B Stage 2 — exploratory barrier exposure, NOT APPROVED

**Branch:** `v062b-barrier-bias-research` (PR #4, draft). **Frozen production scorer:** v0.2; unchanged. All queries read-only.

## Source isolation
- **Historical discovery:** `v061b_hist_races` + `v061b_runner_features` + `v061b_hist_results`. FormFav feature and scores retrieved **after** the races. This cohort is **not** independently as-of validated and cannot establish forward performance.
- **Prospective audit:** immutable `v061_prospective_snapshots` + final `runner_results`, filtered through `EVALUABLE_PROVISIONAL`. Saved scores, ranks and barrier positions are read from the original archive, not recomputed. Actual off-times and provider input-as-of remain uncertified. Races pending official results are excluded from measured outcomes, never counted as losses.
- Cohorts are never combined. Neither subset changes predictions, archived hashes, bets or results. All original Stage 1 integrity exclusions remain visible.

## Field-size-normalised draw
For each fully covered race of **actual starters**, sort occupied barriers into `draw_order` from 1 to N. Define `draw_percentile = (draw_order - 0.5)/N`. Compare three pre-specified draw zones (inside, middle, outside), with baseline `1/N` expected wins per starter. Gaps caused by scratched gates do not distort ordinal positions. Field-size bands: 2–7, 8–11 and 12+ starters. Distance bands: <=1200m, 1201–1600m, >1600m, and an explicit unknown category. No venue/distance pooling is disguised as exact-distance evidence.

## Controlling for the model's barrier term
The **frozen v0.2 model already uses barrier**: `context=(barrier_score+weight_score)/2`, contributing `0.10 * context * 100` to its score. An adjustment based on stored `model_rank` would therefore partially adjust for barrier itself. To prevent this:

```
barrier_score = clamp(1 - (barrier - 1)/16, 0, 1)
barrier_free_score = original_saved_v02_score - 5 * barrier_score
```

This is a **barrier-free v0.2 composite**, not independent intrinsic horse ability, and is affected by score rounding. If scores are missing, non-finite or ambiguous at tercile boundaries, no quality adjustment is inferred.

Place starters into pre-specified score terciles based only on the saved, debarriered composite. The **rank-matched null** conditions on the **observed winner's score tercile** and distributes one expected winner uniformly among all horses *in that same tercile*. This uses the observed outcome to define the null stratum; its `quality_matched_expected` is **NOT** a prospective probability, betting forecast or valid predicted win chance. It can detect preliminary association while roughly conditioning for quality, not prove incremental out-of-sample lift. No p-values or effect recommendations are generated.

Per segment report the number of starters, wins, uniform expected wins, coverage of complete score matching, matched wins, conditional-null expectation and excess. Historical after-race score provenance is explicitly labelled. Race validation excludes incomplete fields, duplicate barrier numbers, missing/ambiguous saved scores (from matched analysis), invalid winner counts (including multiple first-place winners), and ambiguous split ties. Exclusions are exported.

## Validation / promotion
These are predefined **exploratory exposure** thresholds, not proof of a real edge: 100 matched starters and 15 distinct race dates per segment. Until full forward as-of certification, held-out days, stability by venue/distance/field-size, and a properly trained out-of-sample quality-only vs quality+barrier prediction comparison exist, **all feature promotion remains blocked**. No parameter in the v0.2 engine may be adjusted from Stage 2.

## Running
```bash
git pull --ff-only origin v062b-barrier-bias-research
python -m pytest -q
python -m streamlit run app.py --server.address 0.0.0.0 --server.port 8501
```

In **Model Validation → v0.6.2B Stage 2**, run each cohort separately. Export the three `v062b_stage2_*` tables for **historical** and **prospective**, plus any Stage 1 precondition exclusions.

**Offline test scope:** The standalone Stage 2 analysis had 13 synthetic tests passing locally; full-repository regression and Streamlit/dev-Supabase integration still require Codespaces verification.
