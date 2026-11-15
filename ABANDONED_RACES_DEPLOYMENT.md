# v0.4.5 Officially abandoned races — deploy runbook

**Status: DRAFT / not yet deployed.** Sandbox and production share the same Supabase backend. Never use sandbox manual abandonment recording as if it were an isolated dry run.

## Why a dedicated table

FormFav `/v1/results` can lack `resultStatus=final` for a race that was abandoned or never started. Inserting a fictitious losing position or marking all runners scratched to close the race would corrupt the official result dataset and prospective model metrics.

The new `abandoned_races` registry is intentionally **race-level only**. It never creates a `runner_results` placings row and never changes locked predictions, historical results or bets.

## Required one-time migration

1. In the shared Supabase project open **SQL Editor**.
2. Open `migrate_v045_abandoned.sql` from PR #7; review and run it **once** before releasing the application code. It adds a new unique race-level table and protects against updates/deletes. The migration does **not** alter existing `runner_results`, `bets`, `race_predictions`, or research tables.
3. Check the new `abandoned_races` table exists and is empty. The server-side Supabase role must be able to read and insert; browser/anonymous roles cannot.
4. In Codespaces switch to `fix-abandoned-race-status` and run `python -m pytest -q`. Verify the full suite and test the UI using **preview / official evidence** only. **Do not click the final save button in sandbox** unless intending to write into shared production Supabase.
5. Merge PR #7 only after migration and checks; confirm the live Streamlit instance pulled the merge.

## Real abandoned race workflow

1. `Race Results → v0.4.5 — Record Officially Abandoned Race`. Select the exact saved race within 30 days, with no existing final placings or abandoned marker.
2. Check an authoritative racing-body notice or official announcement for **that race/date/meeting**; enter its full HTTPS URL and the exact reason/evidence statement. A race merely delayed or results pending is **not** verified abandoned.
3. Explicitly tick the attestation, type `ABANDONED`, then click the insert action. This **writes to the shared Supabase database**. The record is immutable; mistakes require supervised reconciliation, not deletion/reimport.
4. Race disappears from bulk pending list. Prospective `Today` will show `ABANDONED_OFFICIAL_MANUAL_VERIFICATION`, with `top1_won/top3_hit=NULL`; the race is excluded from hit-rate, simulated unit ROI and paired market denominators.
5. If a race was abandoned after wagers were accepted, separately check bookmaker policy and manually review/refund/void affected `Pending` bets. **No auto-settlement or auto-void** is performed. The bet tracker remains the source of actual settlements.

## Safety limits

- Both the bulk and single result importers check the registry before writing any official per-runner finishers.
- A saved runner-results row blocks later manual abandonment and a saved abandoned marker blocks new final-result inserts. Any unexpected cross-table conflict is flagged in prospective evaluation, not silently resolved.
- This system uses a user attestation and evidence URL, **not automatically independent official-source verification**. It must not turn an uncertain race into a settled abandoned outcome.
- Racing date is evaluated in Australia/Melbourne to avoid UTC date-boundary errors.
- Before the migration is installed, the affected importer shows a clear error rather than silently recording a race with guessed placements.
