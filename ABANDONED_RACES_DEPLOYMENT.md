# Abandoned races — simplified confirmation and safe deployment

**Status: PR #7, draft, not deployed.** Sandbox and production use the same Supabase database. The existing migration has already been run by the user; the simple-confirmation schema upgrade must run before any app merge.

## Why the separate table?

`runner_results` records **individual official finishers**. For an abandoned race there is no finishing-position record to amend; adding a flag to `runner_results` would create artificial runner results, or require a fabricated placeholder row. `race_predictions` holds the locked *pre-race* prediction rows; updating them after abandonment would mutate historical prediction evidence. `abandoned_races` is a small race-level status ledger keyed by **date + track + race number**, not a duplicate result set.

## Existing SQL has already been run — one small follow-up

Open `migrate_v045_abandoned_simple_confirmation.sql` in GitHub branch `fix-abandoned-race-status`; in Supabase SQL Editor run:

```sql
alter table public.abandoned_races
  alter column official_evidence_url drop not null,
  alter column evidence_note drop not null;
```

This makes the originally required evidence URL and description **optional**, without inserting any fake evidence or altering existing rows. The original full `migrate_v045_abandoned.sql` has also been updated to support future clean installs and reruns.

## Reopening the sandbox

The SQL migration **does not start Codespaces or deploy application code**. In GitHub Codespaces, start/resume your existing Codespace and open its terminal. Stop an existing Streamlit process if necessary, then:

```bash
git fetch origin
git switch fix-abandoned-race-status
git pull --ff-only origin fix-abandoned-race-status
python -m pytest -q
python -m streamlit run app.py --server.address 0.0.0.0 --server.port 8501
```

Use the Codespaces **PORTS** panel to open forwarded port **8501**. If no preview appears, run `curl -I http://localhost:8501/_stcore/health` in Codespaces and inspect terminal errors. No assumption about what caused the inaccessible sandbox without its precise error message.

## New simplified one-checkbox workflow

In Race Results, under **v0.4.5 — Record Officially Abandoned Race**:
1. Select the race from saved but unprocessed races.
2. Tick one checkbox confirming that **this exact race was officially abandoned**. Never use it for postponed, results-pending or merely delayed races.
3. Click **Save abandoned race**.

The record saves date, track, race number, outcome, reviewer attestation and server timestamp; URL and note are NULL if not collected. Manual confirmation is honestly labelled — it does **not** certify that an independent official URL was retrieved. One row per race, immutable (no update/delete), no changes to runner placings, locked predictions, bet records or score algorithm. Bulk and single final-results importers block races in the registry; the prospective dashboard excludes the race from win hit rate and ROI denominators.

### Deployment gate

Full Python CI tests must pass. Test race selection/display in Codespaces without clicking Save; the sandbox shares live Supabase. After the optional-evidence migration and live app integration verification, PR #7 can be reviewed for merge into main. Keep v0.6.2B Stage 2.2 PR #4 separate. **No automatic bookmaker bet voiding:** outstanding wagers require independent bookmaker settlement verification.
