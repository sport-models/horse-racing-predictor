# v0.6.2B Stage 2.1 — VENUE HYPOTHESIS / FIRST-STARTER AUDIT (NOT APPROVED)

**Research hypothesis:** country/regional courses may favour an inside-third occupied draw, while metropolitan courses may favour the middle third. This is not a tested fact or an approved feature. **Country, metropolitan, provincial and unclassified are four separate venue-based PROXY populations.** Grouping all non-metropolitan venues as “country” would be misleading. **A track's location is not proof of a given race meeting's metropolitan/provincial/country racing classification.** Racing Victoria also conducts metropolitan-designated meetings at some otherwise regional venues.

### Classification and sources
The frozen, conservative venue lookup in `v062b_stage21.py` identifies familiar venue groups, with **no guessed default** for unknown courses. Exports carry `venue_class_is_meeting_certified=False` and `VENUE_PROXY_NOT_MEETING_CLASSIFICATION`. The lookup is not a regulator-certified fixture-by-fixture racing type.

- Victoria metropolitan four-course reference: Racing Victoria race-date guidance https://www.racingvictoria.com.au/news/2026/04/30/track-enhancements-and-strategic-initiatives-shape-2026-27-victorian-race-dates and Racing Victoria racing rules (metro courses: Flemington, Caulfield, Sandown, Moonee Valley).
- Sydney metropolitan Randwick, Rosehill, Canterbury and Warwick Farm, separate NSW provincial and country sectors: Racing NSW https://www.racingnsw.com.au/about-us-contacts/about-racing-nsw/
- Queensland venue-specific metro/provincial designations can change by **meeting**: Racing Queensland calendar https://www.racingqueensland.com.au/racing/full-calendar. This is why Gold Coast, Sunshine Coast, Toowoomba and Ipswich are placed in a provisional provincial/nonmetro group and not mixed into country or metro without fixture evidence.
- Other conservative course aliases are venue proxies requiring separate official fixture verification before any production-level claim.

### Analysis
1. Run existing Stage 1 integrity checks and Stage 2 `analyse()` for *one* explicit cohort (historical discovery OR prospective). Do not pool.
2. Map exactly **the same** actual starter draw terciles previously used by Stage 2. Compare uniform field-size wins and the descriptive *outcome-conditioned* barrier-free-score-tier null, with **venue class**, venue×field-size and venue×distance breakdowns. A null conditioned on the observed winner's quality tercile is not a pre-race win probability, effect test or usable predictive ROI.
3. Report the descriptive `inside_excess_wins - middle_excess_wins` contrast alongside the number of races and **distinct race dates** and all unclassified venues. Exposure thresholds (100 quality-matched starter observations; 15 distinct racing dates) are exploratory gates only. No p-values, significance claims or 2026 retrospective optimization.
4. For every starter, preserve the original score and its absence. `PROVIDER_REPORTED_ZERO_CAREER_STARTS` requires explicit source evidence: historical stored `career_starts=0` (retrieved **after** racing) or prospective original `scoring_status=NULL_SCORE_ZERO_CAREER_STARTS`. Empty/missing form histories are **not** proven first starts. Preserve other `NULL_SCORE` reasons separately.
5. An unscored horse is **retained in the uniform actual-starter draw denominator** and may have won, but the entire race is excluded from complete-score quality matching; never impute a weak or arbitrary “new horse” score. Follow up separately on scorer design, frozen v0.2 unaffected.
6. Full-field genuine two-way dead heats remain legitimate **model** performance and imported results after production PR #6. However, the Stage 1/2 barrier benchmark assumes one winner per race: explicitly exclude two-way dead heats from that benchmark, and publish the immutable race identifier and both original joint winners in `dead_heat_exclusions`. Never choose a sole winner, double-count as two races, or rewrite official rows. For Donald Race 5 (15 Nov 2026) this should manifest as **one excluded race**, if imported and matching its archive.

### Prior six-file exploratory snapshot (15 November 2026)
Historical Stage 2: **1,347 eligible single-winner races**, 12,540 actual starters, 30 distinct dates. Prospectives: **65**, 622 starters, **4 distinct dates**. Stage 2.1 has a specific hypothesis map but those cohorts have **already been inspected** and cannot be treated as an untouched test for a new hypothesis. In a dry-run over those same six saved CSV exports, **metro proxy = 334 historical races from 10 dates and 10 prospective races from 1 date**; **country proxy = 354 historical races from 27 dates and 40 prospective races from 3 dates**. Those samples cannot establish a metro-vs-country race-day edge. This map deliberately leaves many of the 121 historical venues unclassified rather than silently treating them as country.

### Test/run/deployment
```bash
git switch v062b-barrier-bias-research
git pull --ff-only origin v062b-barrier-bias-research
python -m pytest -q
python -m streamlit run app.py --server.address 0.0.0.0 --server.port 8501
```
Navigate to **Model Validation → v0.6.2B Stage 2.1 — Country / metro venue hypothesis & unscored horses**. Run **Historical** and **Prospective** separately. Download **one ZIP per cohort** with `Download ALL Stage 2.1 exports for this cohort (ZIP)`. Each ZIP contains runner, venue, score-reason, score-coverage, dead-heat and issue CSVs. Individual CSV downloads remain optional.

**No recommendation to alter the frozen v0.2 engine, no merged PR #4, and no evidence of an independently verified pre-race incremental barrier edge.** Next required experiment is to freeze a baseline vs candidate prior to an untouched forward sample with independent actual-off and input-as-of verification and demonstrably adequate meeting-class coverage.


## 2026-11-15 Stage 2.1 evidence audit (uploaded ZIP exports)

Files: `v062b_stage21_historical_audit_bundle.zip` and `v062b_stage21_prospective_audit_bundle.zip`. Counts below reflect the *user's exported dataset*, not a live database read or a prospective hypothesis test.

- Historical: 1,347 single-winner races, 12,540 starters, 30 dates, 121 tracks. 1,111 full-score quality-comparison races; 233 have missing scores, three additional races have quality-tercile score ties. 590 actual starters have missing scores, with **zero** source-confirmed 0-career-start labels in this export; two have explicitly recorded past starts, 588 lack sufficiently specific provenance. One excluded historical dead heat: Geraldton R1, 2026-11-03.
- Prospective: 94 single-winner races, 931 starters, **four dates** (12–15 November), 12 tracks. 78 full-score quality-comparison races; 16 have missing-score issues. All 35 missing-score starters have **unverified** score absence; no recorded debutant evidence from exported `locked_scoring_status`. One additional prospectively archived race with two official winners is excluded from the one-winner barrier statistic: Donald R5, 2026-11-15 (Pinazaki and Move It). Neither horse is discarded from the production result or model win evaluation.
- Historical venue proxy group exposure: Country 354 races / 27 dates, Metro 334 / 10 dates, Provincial 146 / 14 dates, Unclassified 513 / 27 dates. **513 / 1,347 = 38.1% unclassified**, so venue-based findings are selective.
- Prospective venue proxy: Country 47 races / 3 dates, Metro 10 / **one date at Sandown only**, Provincial 15 / 2 dates, Unclassified 22 / one date. There is **no metro venue in common** between the historical and prospective subsets; the metro comparison is not a same-track replication.
- Country historical: inside 135 wins vs uniform 119.03, middle 103 vs 115.94. Country prospective: inside 14 vs 15.51, middle 18 vs 15.99. Historical and prospective samples **disagree** on inside-versus-middle ordering. In country prospectives the score-tier conditional null gives middle 12 wins vs 12.62 expected among 35 complete-score races, underscoring that the raw middle +2.01 excess does not demonstrate added predictive lift.
- Metro historical: inside **123** and middle **123** wins, each about 11.6–11.7 above uniform reference, outside 88 wins vs 111.31. Metro prospective: inside 2 vs 3.28 and middle 5 vs 3.45, but only 10 races from Sandown on one day; **no reliable evidence** that metro middle is a stronger feature than inside across metro venues.
- Historical `COUNTRY` excess inside wins is mainly 1,201–1,600 metres (63 vs 52.2 expected), whereas historical country <=1,200m inside is near uniform (49 vs 47.59). This subgroup was examined after observing results: **hypothesis-generating only**.

**Decision:** Country-inside / metro-middle conditional barrier adjustment is **NOT APPROVED**. Both cohorts informed this hypothesis, therefore the full 12–15 Nov prospective dataset is already exploratory; it is **not** an untouched independent validation set. Keep the production v0.2 barrier term frozen. Next experimental design must predefine meeting-class sources, field/distance strata, and a pre-race-calibrated baseline; then gather fresh certified forward races on additional independent dates and with sufficient metro venue representation.

**Capture issue:** Model scores are missing for legitimate potential debutants, but the saved exports have not **established** debut status for any missing-score starter. Do not recode absent scores to `zero starts` by assumption; investigate original archived provider `stats.overall.starts` and missing scoring-status provenance as a separate **read-only** capture audit, without modifying production scores or old archives.

