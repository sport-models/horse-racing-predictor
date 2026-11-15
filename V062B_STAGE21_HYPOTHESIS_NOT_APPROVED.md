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
