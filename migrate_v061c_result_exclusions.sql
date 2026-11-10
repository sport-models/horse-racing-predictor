-- v0.6.1c: reversible exclusions for archived races without accessible final results.
-- Does not modify immutable form captures, v0.2 predictions, or stored final results.
BEGIN;
CREATE TABLE IF NOT EXISTS public.v061b_result_exclusions (
    batch_id uuid PRIMARY KEY REFERENCES public.v061b_hist_races(id),
    reason text NOT NULL CHECK (reason IN ('CONFIRMED_ABANDONED', 'RESULT_UNAVAILABLE', 'OTHER_UNRESOLVED')),
    note text NOT NULL DEFAULT '',
    marked_at timestamptz NOT NULL DEFAULT now()
);
ALTER TABLE public.v061b_result_exclusions ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON public.v061b_result_exclusions FROM PUBLIC, anon, authenticated;
GRANT SELECT, INSERT, DELETE ON public.v061b_result_exclusions TO service_role;
COMMIT;
