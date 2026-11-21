# v0.6.2B Stage 2.3 — source audit results, 16 November 2026

**Input evidence:** User-uploaded `v062b_stage23_historical_audit_bundle.zip` and `v062b_stage23_prospective_audit_bundle.zip`. Eight CSV files per ZIP; inspected all files. This audit is read-only and uses only the original saved runner evidence and associated race-result evaluations. Research data have already influenced the hypothesis; this is NOT a fresh untouched holdout.

## Populations
| Metric | Historical discovery | Prospective archive |
| --- | ---: | ---: |
| Race date range | 2026-10-10–2026-11-08 | 2026-11-12–2026-11-16 |
| Unique single-winner races | **1,347** | **110** |
| Starter observations | **12,540** | **1,098** |
| Racing dates | 30 | **5** |
| Tracks | 121 | **14** |
| Numeric-score starters | 11,950 | 1,059 |
| Null-score starters | **590 (4.70%)** | **39 (3.55%)** |
| Races containing null-score starter | **233** | **20** |
| Wins by null-score runners | **56 (4.16% of races)** | **5 (4.55%)** |
| Uniform field-size random expected wins for null-score starters | **63.03** | **3.95** |
| Difference actual − random expected | **−7.03** | **+1.05** |

The uniform expectation is sum of saved per-race 1/field_size for *all unscored starters*. This is a descriptive exposure reference only, not a calibrated horse-quality win model, ROI, or significance test. The observations do **not** establish that giving unscored horses a higher rating would improve model accuracy. Neither sample is independent of hypothesis generation.

## Exact frozen-v0.2 score-null gates
The original v0.2 scorer needs (1) at least one digit in its exact runner `form` field and (2) a positive effective `stats.overall.starts` value. If either fails, the saved numeric score is NULL.

| Why score NULL | Historical starters | Historic winners | Prospective starters | Prospective winners |
| --- | ---: | ---: | ---: | ---: |
| Both score prerequisites fail | **587** | **56** | **39** | **5** |
| Positive career starts, no form digits | **2** | 0 | 0 | 0 |
| Form digits, no career-start count | **1** | 0 | 0 | 0 |
| Total | **590** | **56** | **39** | **5** |

The two historical runners with explicit positive starts but no form digits were **Platinum Card, Tamworth R4, 12 Oct 2026**, and **Mighty Dancer, Orange R4, 19 Oct 2026**. Both show one provider-recorded past start. A third exception, **Ready Mister Music, Gold Coast Polytrack R7, 24 Oct 2026**, had four form digits but the original effective overall starts field was missing. None of these three was a winning horse in the analysed set.

**Critically: NONE of the 590 historical or 39 prospective unscored starters has an explicitly confirmed zero-career-start record in the inspected archived fields.** Absence of stats/form is not independent evidence of debutant status. Every unscored runner had an archived source record; 588 historic and 39 prospective lacked an explicit career-start count. For the unscored runners there are no digit-bearing `recentForm` / `recent_form` aliases or positive root `careerStarts`/`starts` aliases in the original archived runner dictionaries. Thus a trivial alias-name change is not supported as a recovery for these observed cohorts.

Every saved scored runner passes both requirements (11,950 historical; 1,059 prospective), and every unscored runner fails at least one. **Zero score-gate parity contradictions**. The Stage 2.3 raw archive/source issue CSVs are empty. These checks are not independent source-as-of/actual-off certification.

## Original prospective unscored winners (five, unchanged)
| Date | Race | Horse | Original saved rank |
| --- | --- | --- | ---: |
| 2026-11-13 | Geelong R3 | Lafarna | 8 |
| 2026-11-13 | Pakenham R3 | Snow Cabin | 12 |
| 2026-11-14 | Toowoomba R6 | Waiuta Belle | 10 |
| 2026-11-15 | Wyong R8 | Latteragh | 9 |
| 2026-11-15 | Wellington R3 | Wild Bombshell | 8 |

The new 16 November 2026 prospective records expand the prior cohort from 94 races, 931 starters, four dates to **110 races, 1,098 starters, five dates**. They include four additional null-score starters, but no additional unscored winning horse. Do not retrospectively reorder these horses, or treat the resulting backtest as a fresh validation sample.

## Integrity/exclusions
- Stage 2.3 historical: 233 incomplete-score race diagnostics, 3 quality-tercile score-tie exclusions; upstream 169 duplicate/incomplete barrier exposure issues and 1 excluded historic genuine joint-first result.
- Stage 2.3 prospective: 20 incomplete-score race diagnostics and 1 upstream deliberately excluded two-way dead heat.
- Score-null reason is based on an original archived source field, not on today's live provider data. Historical source data were retrieved **after the event** and cannot certify actual pre-race data. Prospective racing is only schedule-timed and provider as-of is not independently validated.

## Research conclusion / decision
**Stage 2.3 root-cause audit COMPLETED. Frozen v0.2 score handling is mechanically consistent, but source-data coverage is incomplete.** The default zero used internally when starts are absent is not evidence of an unraced horse and should not be relabelled “debutant.” Null-score horses win at roughly their race-size-weighted uniform exposure, not at a demonstrated surplus justifying automatic numerical uplift.

**No automatic reranking, guessed score, provider-source backfill, odds-based repair or production change approved.**

### Next steps (separate gated research)
1. Capture and version a **pre-race provider-status observation** for each horse going forward, distinguishing explicit zero career starts, positive career starts with no form, no provider starts/form, source identity mismatch, and scoring exceptions. Never overwrite existing locked ranks or retroactively designate debutants.
2. Confirm whether FormFav offers a **separately timed official starts/history endpoint** covering missing-score horses. If not, label missing. Any alternative data must be captured *before lock*, with provider timestamp, original raw payload hash, and no results leakage; not pulled retrospectively to repair old score evidence.
3. Build a **shadow-only candidate** with documented data-coverage rule and a no-extrapolation default, then pre-register evaluation against frozen v0.2 on genuinely later races. Include race-type/maiden context only with verifiable metadata, never by assuming race number 1–3 implies a maiden.
4. Continue prospective accumulation and independently verify actual race-off and source as-of times. Do not pool country/metro barrier discovery with the missing-score study or use PR #4 in production before proper branch reconciliation.
