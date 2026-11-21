# Stage 2.4 — Read-only source coverage audit (17 November 2026)

**Source:** User-uploaded `v062b_stage24_coverage_challenger_audit.zip`, comprising 6 CSV exports and 1 protocol JSON. These are original frozen-v0.2 prospective archives; this report does **not** use race results to select a challenger, does not fetch live or post-race form, and makes no database changes. Audit status is research-only.

## Full export reconciliation

| Measure | Saved archive observation |
| --- | ---: |
| Race archives | **128** |
| Unique date + track + race combinations | **128** |
| Race dates | **6** (12–17 November 2026) |
| Different tracks | **16** |
| Original locked ranked horses | **1,277** |
| Locked horses with numeric original v0.2 score | **1,224** |
| Original locked horses with NULL score | **53 (4.15%)** |
| Races containing at least one NULL-score horse | **28 (21.88%)** |
| Original FormFav raw runner records across archived races | **1,608** |
| Archived race source/hash/scheduled-order failures | **0** |
| Duplicate original locked horse identities/races detected | **0** |
| Score-gate contradictions (saved scores vs exact gates) | **0** |
| Source runners missing for saved locked horses | **0** |
| Locked runners with independently certified actual-off | **0** |
| Locked runners with independently certified provider-as-of | **0** |
| Alternative external source profile lookups performed | **0** |
| Secondary source values proven available prelock | **0** |
| Candidate numerical scores or alternate ranks generated | **0** |

The 1,608 raw source records are a different population from the 1,277 ranked runners. Raw records may include scratched and otherwise excluded runners; the difference (**331**) is **not** evidence of missing locked runners and was not classified in this export. All 1,277 ranked runners matched original raw source entries. All archive source hash, model-version, and scheduled-order gates passed, but original provider as-of timestamps and actual race-off are **not independently certified**.

**Source completeness by date:**

| Date | Archived races | Original ranked horses | NULL-score horses |
| --- | ---: | ---: | ---: |
| 2026-11-12 | 8 | 88 | 8 |
| 2026-11-13 | 16 | 156 | 9 |
| 2026-11-14 | 17 | 164 | 3 |
| 2026-11-15 | 55 | 556 | 19 |
| 2026-11-16 | 16 | 170 | 5 |
| 2026-11-17 | 16 | 143 | 9 |

**Every one of the 53 original NULL-score runners** has the archived status `EXPECTED_NULL_FROM_V02_SOURCE_GATES`, `BOTH_V02_GATES_BLOCK` and `SOURCE_MISSING` for explicitly documented career starts. None carries source-confirmed zero career starts, numeric original `stats.overall.starts`, usable original `form` digits, usable `recentForm` / `recent_form` digits, or positive top-level career-start aliases. Therefore none of the 53 can be recovered into a proper numerical v0.2 score from the **archived fields tested**. Their original locked ranks span **6 through 14**. All 1,224 numerically scored runners satisfy both original gates.

**Do not label them debutants.** Missing source is not observed zero career starts. No FormFav alternate profile API results or external independently verified records are included in this export. At this point, the challenger is a **specification only**, and the correct policy remains to preserve all v0.2 numerical scores, NULL scores and saved ranks unchanged.

## Protocol verification

Version: **0.1.0**; SHA256: `c4aec697e98c77c3f4b6e3a87ae70d195b4af937ec6a4436d17cc9578d987c92`. `shadow_candidate_ready=NO`, `barrier_adjustment_ready=NO`, `production_promotion_approved=NO`. Existing original at-lock archives alone do not demonstrate an additional useful source or a holdout improvement. No actual race results were incorporated in this Stage 2.4 source coverage audit, so no estimated future win rate or ROI is appropriate. The 17 November 2026 races are newly archived inputs, not yet declared evaluable final-result observations here.

## External source feasibility: proposed, not established

FormFav publicly documents a **Pro-tier** runner-search and individual runner-profile facility, including `search_runner` and `get_runner_profile` over its MCP service, and additional runner stats under its Pro `/v1/stats/…` APIs. This is a **potential coverage source to investigate**, not evidence that those endpoints include missing-score horses, supply correct identity matches, or expose genuine pre-race-as-of history. Do not assume the user's API plan has Pro access or a particular undocumented URL. Authentication and data contracts must be verified before implementing any request.

### Next gated action: Stage 2.5 external profile feasibility probe (not yet implemented)

1. Inspect official FormFav API/OpenAPI documentation and available permissions for precise runner-profile endpoints. Verify availability before use; DO NOT invent an endpoint.
2. On a truly future race before the prediction lock, run a **read-only, explicitly enabled probe**, ideally with a small request budget, on preselected no-score horses. Preserve API response, receipt time, response hash, original horse identity and match confidence, provider as-of timestamp **if exposed**, race start and lock timestamp. Treat absent/ambiguous identity or missing as-of as NOT VERIFIED.
3. Maintain **separate append-only source observations** with a defined idempotent identity, explicit opt-in and provenance controls. Never modify locked `race_predictions`, original at-lock archives, `runner_results`, bets or the frozen production v0.2 scorer. Sandbox still shares live Supabase; do not test writes against it without safe isolation.
4. Only after time/identity validity and actual source coverage are established, pre-register a deterministic **shadow** challenger before unseen races and evaluate against v0.2 on the exact same independently verifiable prospective subset. Do not backfill earlier winners to tune the rule.

**Decision:** Stage 2.4 exported source-coverage audit **COMPLETE**. Additional-source recovery **UNPROVEN**. Model scorer unchanged, PR #4 remains draft.
