# v0.6.2B Stage 2.2 — archived-source audit (16 November 2026)

**Inputs:** User-provided `v062b_stage22_historical_audit_bundle.zip` and `v062b_stage22_prospective_audit_bundle.zip` (16 CSVs). **Scope:** inspect exported evidence only; no live Supabase queries, new provider fetches, DB writes, or model scoring changes. All cohorts and comparisons are research only.

| Audited metric | Historical discovery | Prospective archived |
|---|---:|---:|
| Date window | 2026-10-10 to 2026-11-08 | 2026-11-12 to 2026-11-15 |
| Unique single-winner races | 1,347 | 94 |
| Starter observations | 12,540 | 931 |
| Distinct racing dates | 30 | 4 |
| Distinct tracks | 121 | 12 |
| Starters with a saved numeric model score | 11,950 | 896 |
| Starters without a saved model score | **590** | **35** |
| Races containing unscored starters | 233 | 16 |
| Quality-comparison eligible races | 1,111 | 78 |
| Explicit positive archived career starts | 11,952 | 896 |
| Explicit zero archived career starts | **0** | **0** |
| Explicit career-starts value missing | **588** | **35** |
| Previously raced but unscored | 2 | 0 established |
| Unscored winning horses | **56** | **5** |
| Race meetings with official meeting-class proof | **0** | **0** |

Historical two unscored horses with *one recorded start* each: **Platinum Card, Tamworth, 2026-10-12** and **Mighty Dancer, Orange, 2026-10-19**. Their status is `UNSCORED_PREVIOUSLY_RACED`, so not all unscored runners are proven debutants.

The **five prospective unscored winners** (all saved model ranks below the top three) were: **Lafarna — Geelong R3, 2026-11-13 (rank 8); Snow Cabin — Pakenham R3, 2026-11-13 (rank 12); Waiuta Belle — Toowoomba R6, 2026-11-14 (rank 10); Latteragh — Wyong R8, 2026-11-15 (rank 9); Wild Bombshell — Wellington R3, 2026-11-15 (rank 8)**. These five of 94 race winners are a substantive **coverage blind spot**, but their unraced/debutant status has *not been confirmed*. Do not retrospectively choose a new scoring or ranking rule using their known outcomes.

**Captured-provider provenance:** `stats.overall.starts` was populated for 11,952 historical and 896 prospective observations; for 588 / 35 unscored source records the explicit career-start count was absent. The historic scored-vs-start count mismatch of two is independently consistent with the two previously raced unscored runners. On the prospective sample, `locked_scoring_status` for the 35 unscored runners is 24 blank, 11 `NULL_SCORE_REASON_UNVERIFIED`; neither is proof of zero career starts. No `SOURCE_CONFLICT` or `SOURCE_INVALID` entries were present in these exports. `raw_archive_issues` is empty in both cohorts, so this audit did not detect an archived hash mismatch, but does not independently certify provider as-of times.

**Quality-matched research exclusions:** historical `stage2_issues` includes 233 `INVALID_OR_MISSING_STORED_MODEL_SCORE` and 3 `QUALITY_SCORE_TIE_SPANS_TERCILES`. Prospective `stage2_issues` includes 16 missing-score races. Original historical integrity has 169 `INCOMPLETE_OR_DUPLICATE_BARRIER_EXPOSURE` and one separately excluded joint-first race; prospective prior issues include one separately excluded joint-first race (Donald R5, 2026-11-15). The comparison populations are selective; descriptive uniform draw statistics retain starters, but complete-score quality comparisons exclude affected races.

**Meeting classification:** the optional official-source CSV manifest was not provided. All **1,347 historical** and **94 prospective** race rows are `NO_MEETING_CLASS_SOURCE`. The approximate venue proxies still divide historical races into country 354, metro 334, provincial 146, unclassified 513; prospective country 47, metro 10, provincial 15, unclassified 22. **No venue proxy is independently certified as an official meeting grade**; it would be incorrect to claim Stage 2.2 validated a metro/country hypothesis.

**Decision and next work:** Stage 2.2 **source audit completed; no barrier/debutant feature promotion**. Protect the frozen production v0.2 engine. Prioritise (1) forward-only explicit scoring-reason capture and first-start evidence instead of assuming unscored = debutant, (2) official date+track meeting-class source coverage for an independently verifiable fixture manifest, (3) frozen prospective challenger methodology with unseen dates and adequately diverse metro venues. Prospective 12–15 November results have already influenced hypothesis selection and are not untouched holdout. Do not merge this research branch to production.
