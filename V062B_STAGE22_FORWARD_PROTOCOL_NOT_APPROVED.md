# v0.6.2B Stage 2.2 — forward protocol (NOT APPROVED FOR PROD)

## Immutable research boundary
- Production **v0.2** horse scoring and relative barrier contribution remain frozen and unchanged. The 10 October–15 November 2026 Stage 1/2/2.1 datasets were reviewed during hypothesis development and are **not untouched holdout** for Stage 2.2.
- Prospective tests require original **at-lock timestamp + payload SHA256 + per-provider receipt time** and independent **actual race-off** verification. Scheduled off alone is insufficient; FormFav current-day or retrospective score/odds cannot substitute for original snapshots.
- Only genuine **official final results** are eligible for top1 win, top3 hit, and one-winner barrier outcomes. Two-way dead heats are valid in the primary production model, but the research one-winner draw benchmark must document and exclude them. **Abandoned or void race**: no winner, no simulated win loss, no eligible barrier result; a separate verified abandonment registry is the planned source once the isolated fix is deployed.

## Stage 2.2 archive-based debutant provenance
- Read provider's original archived `careerStarts`, `starts`, `stats.overall.starts`, `stats.overall.careerStarts`. Accept only explicit non-negative integer counts. If they disagree, flag source conflict, do **not** select an arbitrary count. Zero starts in a historical *post-race retrieval* is not independently certified pre-race. A prospective at-lock snapshot is not proof that provider data were accurate or as-of timing independently verified.
- Map by original `batch_id` and canonical runner name. Hash validation for prospective archives. Duplicate ambiguous records are removed from the source index.
- Missing model score for a runner with confirmed zero starts is *not* a numeric zero score. Such runners remain in full-field uniform draw baselines. A race with any unscored actual starter is excluded from the complete-score conditional quality-tier analysis. Never impute scores and never alter saved archives.

## Meeting classification (track name is not enough)
- Require dated, **per-meeting** evidence from an official racing authority, including the explicit meeting designation (METRO, PROVINCIAL or COUNTRY), official HTTPS URL and exact citation text. The dashboard accepts an optional **user-supplied manifest** `race_date,track,meeting_class,official_source_url,official_source_statement,reviewer_verified`. It flags invalid/double-supplied entries, keeps unclassified races unknown, never silently assumes country for non-metro tracks, and NEVER labels an unvisited URL as independently verified.
- Manual reviewer attestation is recorded as **USER_ATTESTED_OFFICIAL_SOURCE_NOT_INDEPENDENTLY_VALIDATED**, which is not sufficient for production or promotional model approval. A later independent authorised source verification + versioned fixture mapping is mandatory before any predictive meeting-tier experiment.
- For provenance and audit, retain all unclassified and source-conflict records and coverage losses. Avoid false equivalence of country and provincial. Never combine historical discovery with prospective evidence.

## Pre-register next hypotheses before future race capture
1. H0: the existing frozen v0.2 barrier term is at least as effective on genuinely future independently time-verified races as any meeting-tier candidate.
2. H1, exploratory: country inside-third draw may have greater association than middle-third draw after field size and source-confirmed horse quality. Historical association was not replicated across the 12–15 Nov prospective sample. DO NOT fit a new weight from these snapshots.
3. H2, exploratory: for metropolitan race meetings, outside third is weaker than inside/middle. The old prospective metro sample consists of one track on one date, so venue and date confounding must be addressed.
4. Compare original top1 winner hit, top3 hit, coverage, whole-field calibration if valid probabilities become available, descriptive expected-vs-observed and units ROI using *original at-lock quoted odds only*. Include scratches, missing scores, dead heats, abandoned races and data-quality exclusions in a fixed complete race inventory.
5. At least 15 **independent race dates** and 100 eligible original-score-quality starters per proposed segment, plus credible multiple metropolitan course exposure. These are eligibility floors, NOT statistical significance or sufficient sample-size proofs. Stratify by date/track; predefine distance and field-size bands before inspecting new results.
6. No feature promotion or 2026 retrospective auto-optimisation. All candidate calculation versions and thresholds must be frozen and reviewed prior to observing future outcome data, with prospective shadow-only operation until independent holdout results establish repeatable incremental value.

## How to run Stage 2.2
```bash
git fetch origin
git switch v062b-barrier-bias-research
git pull --ff-only origin v062b-barrier-bias-research
python -m pytest -q
python -m streamlit run app.py --server.address 0.0.0.0 --server.port 8501
```

Choose **Model Validation → Stage 2.2 — Meeting class & debutant provenance**. Run **Historical** and **Prospective** separately and download the two full ZIP exports. No manifest is necessary for the initial archive-provenance audit; the export will explicitly show missing official meeting classes. Add verified official fixture citations later without pretending they were captured at the original prediction lock.

All work is read-only, research branch only, not production or merged.
