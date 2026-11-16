-- v0.4.5 follow-up for users who already ran migrate_v045_abandoned.sql.
-- Safe to run more than once. It does NOT change any existing rows, lock states,
-- bets, runner_results, race_predictions, or immutable-update trigger.
-- Blank evidence is stored as NULL; the race/date checkbox is the attestation.
alter table public.abandoned_races
  alter column official_evidence_url drop not null,
  alter column evidence_note drop not null;
