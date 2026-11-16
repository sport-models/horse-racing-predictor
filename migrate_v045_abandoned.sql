-- v0.4.5: explicit race-level non-runner outcome; additive only.
-- Run once in Supabase SQL editor before enabling the importer.
-- Existing runner_results, bet settlements and locked predictions are untouched.
create table if not exists public.abandoned_races (
 id bigint generated always as identity primary key,
 race_date date not null,
 track text not null check (length(trim(track))>0),
 track_key text not null check (length(trim(track_key))>0),
 race_number integer not null check (race_number > 0),
 outcome text not null default 'ABANDONED' check (outcome='ABANDONED'),
 official_evidence_url text check (official_evidence_url ~* '^https://[^[:space:]]+
 evidence_note text check (length(trim(evidence_note)) >= 12),
 reviewed_by text not null default 'MANUAL_REVIEW' check (length(trim(reviewed_by))>0),
 verified_at timestamptz not null default now(),
 created_at timestamptz not null default now(),
 unique (race_date,track_key,race_number)
);
comment on table public.abandoned_races is
  'Immutable manual verification of abandoned race; never per-runner placings or bet settlement.';
create or replace function public.prevent_abandoned_races_mutation()
returns trigger language plpgsql as $$
begin
 raise exception 'abandoned_races immutable: escalate corrections to supervised reconciliation';
end $$;
drop trigger if exists abandoned_races_immutable on public.abandoned_races;
create trigger abandoned_races_immutable before update or delete
 on public.abandoned_races for each row
 execute function public.prevent_abandoned_races_mutation();
alter table public.abandoned_races enable row level security;
revoke all on public.abandoned_races from anon, authenticated;
-- Server-side service_role only; no browser secrets.
-- Safe to rerun after the original strict version: make source annotations
-- optional without altering race keys, immutable status, or existing records.
alter table public.abandoned_races
  alter column official_evidence_url drop not null,
  alter column evidence_note drop not null;
comment on column public.abandoned_races.official_evidence_url is
  'Optional official source URL. NULL means no URL was captured; never fabricate evidence.';
comment on column public.abandoned_races.evidence_note is
  'Optional evidence note. Manual confirmation is recorded in reviewed_by and verified_at.';
),
 evidence_note text not null check (length(trim(evidence_note)) >= 12),
 reviewed_by text not null default 'MANUAL_REVIEW' check (length(trim(reviewed_by))>0),
 verified_at timestamptz not null default now(),
 created_at timestamptz not null default now(),
 unique (race_date,track_key,race_number)
);
comment on table public.abandoned_races is
  'Immutable manual verification of abandoned race; never per-runner placings or bet settlement.';
create or replace function public.prevent_abandoned_races_mutation()
returns trigger language plpgsql as $$
begin
 raise exception 'abandoned_races immutable: escalate corrections to supervised reconciliation';
end $$;
drop trigger if exists abandoned_races_immutable on public.abandoned_races;
create trigger abandoned_races_immutable before update or delete
 on public.abandoned_races for each row
 execute function public.prevent_abandoned_races_mutation();
alter table public.abandoned_races enable row level security;
revoke all on public.abandoned_races from anon, authenticated;
-- Server-side service_role only; no browser secrets.
