-- v0.6.1b: historical research captures only. Run in Supabase SQL Editor.
-- Three new append-only tables. No modifications to v0.2, bets or prospective records.
BEGIN;
CREATE EXTENSION IF NOT EXISTS pgcrypto;

CREATE TABLE IF NOT EXISTS public.v061b_hist_races (
    id uuid PRIMARY KEY,
    race_date date NOT NULL,
    track text NOT NULL CHECK (length(trim(track)) > 0),
    track_key text NOT NULL CHECK (length(track_key) > 0),
    race_number integer NOT NULL CHECK (race_number BETWEEN 1 AND 30),
    track_slug text NOT NULL,
    model_version text NOT NULL DEFAULT 'v0.2' CHECK (model_version = 'v0.2'),
    cohort text NOT NULL DEFAULT 'HISTORICAL_RESEARCH_POST_RACE_RETRIEVAL'
        CHECK (cohort = 'HISTORICAL_RESEARCH_POST_RACE_RETRIEVAL'),
    form_request_started_utc timestamptz NOT NULL,
    form_received_utc timestamptz NOT NULL,
    scheduled_start_utc timestamptz,
    source_form_sha256 text NOT NULL CHECK (length(source_form_sha256) = 64),
    scorer_sha256 text NOT NULL CHECK (length(scorer_sha256) = 64),
    source_form_payload jsonb NOT NULL,
    runner_count integer NOT NULL CHECK (runner_count > 0),
    predicted_count integer NOT NULL CHECK (predicted_count > 0),
    created_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT v061b_hist_race_unique UNIQUE(race_date, track_key, race_number)
);

CREATE TABLE IF NOT EXISTS public.v061b_runner_features (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    batch_id uuid NOT NULL REFERENCES public.v061b_hist_races(id),
    horse text NOT NULL CHECK (length(trim(horse)) > 0),
    horse_key text NOT NULL CHECK (length(horse_key) > 0),
    runner_number integer,
    scratched_in_historical_response boolean NOT NULL DEFAULT false,
    model_rank integer,
    model_score double precision,
    recent_form text,
    recent_form_last5 text,
    career_starts integer,
    career_wins numeric,
    career_places numeric,
    career_win_rate double precision,
    track_starts integer,
    track_wins numeric,
    track_places numeric,
    track_win_rate double precision,
    distance_starts integer,
    distance_wins numeric,
    distance_places numeric,
    distance_win_rate double precision,
    track_distance_starts integer,
    track_distance_wins numeric,
    track_distance_places numeric,
    track_distance_win_rate double precision,
    barrier integer,
    weight_kg numeric,
    jockey text,
    trainer text,
    jockey_starts integer,
    jockey_wins numeric,
    jockey_places numeric,
    jockey_win_rate double precision,
    trainer_starts integer,
    trainer_wins numeric,
    trainer_places numeric,
    trainer_win_rate double precision,
    jockey_stats_raw jsonb,
    trainer_stats_raw jsonb,
    feature_payload jsonb NOT NULL,
    bookmaker_quotes jsonb NOT NULL DEFAULT '[]'::jsonb,
    bookmaker_quote_note text NOT NULL DEFAULT 'NO_HISTORICAL_PRE_RACE_QUOTES',
    CONSTRAINT v061b_runner_unique UNIQUE(batch_id, horse_key),
    CONSTRAINT v061b_rank_valid CHECK (model_rank IS NULL OR model_rank >= 1)
);
CREATE INDEX IF NOT EXISTS v061b_runner_batch_index ON public.v061b_runner_features(batch_id);
CREATE INDEX IF NOT EXISTS v061b_runner_jockey_index ON public.v061b_runner_features(jockey);
CREATE INDEX IF NOT EXISTS v061b_runner_trainer_index ON public.v061b_runner_features(trainer);

CREATE TABLE IF NOT EXISTS public.v061b_hist_results (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    batch_id uuid NOT NULL UNIQUE REFERENCES public.v061b_hist_races(id),
    source text NOT NULL DEFAULT 'FormFav' CHECK (source = 'FormFav'),
    result_status text NOT NULL CHECK (result_status = 'final'),
    result_retrieved_utc timestamptz NOT NULL,
    result_sha256 text NOT NULL CHECK (length(result_sha256) = 64),
    actual_starters integer NOT NULL CHECK (actual_starters > 0),
    result_payload jsonb NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now()
);

-- RLS: no browser, anon or authenticated table access; service-role access only.
ALTER TABLE public.v061b_hist_races ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.v061b_runner_features ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.v061b_hist_results ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON public.v061b_hist_races FROM PUBLIC, anon, authenticated;
REVOKE ALL ON public.v061b_runner_features FROM PUBLIC, anon, authenticated;
REVOKE ALL ON public.v061b_hist_results FROM PUBLIC, anon, authenticated;
GRANT SELECT, INSERT ON public.v061b_hist_races TO service_role;
GRANT SELECT, INSERT ON public.v061b_runner_features TO service_role;
GRANT SELECT, INSERT ON public.v061b_hist_results TO service_role;

-- Safeguard against accidental edits to immutable historical captures.
CREATE OR REPLACE FUNCTION public.v061b_immutable_guard()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'v0.6.1b historical capture is append-only; corrections require a new version';
END $$;
DROP TRIGGER IF EXISTS v061b_races_immutable ON public.v061b_hist_races;
CREATE TRIGGER v061b_races_immutable BEFORE UPDATE OR DELETE ON public.v061b_hist_races
FOR EACH ROW EXECUTE FUNCTION public.v061b_immutable_guard();
DROP TRIGGER IF EXISTS v061b_features_immutable ON public.v061b_runner_features;
CREATE TRIGGER v061b_features_immutable BEFORE UPDATE OR DELETE ON public.v061b_runner_features
FOR EACH ROW EXECUTE FUNCTION public.v061b_immutable_guard();
DROP TRIGGER IF EXISTS v061b_results_immutable ON public.v061b_hist_results;
CREATE TRIGGER v061b_results_immutable BEFORE UPDATE OR DELETE ON public.v061b_hist_results
FOR EACH ROW EXECUTE FUNCTION public.v061b_immutable_guard();

-- Atomic parent + every runner insert; no half-saved races, idempotency via unique race key.
CREATE OR REPLACE FUNCTION public.v061b_capture_historical(p_batch jsonb, p_runners jsonb)
RETURNS uuid LANGUAGE plpgsql SECURITY INVOKER SET search_path = public, pg_temp AS $$
DECLARE
    new_id uuid;
    item jsonb;
    saved_count integer := 0;
BEGIN
    IF p_batch IS NULL OR jsonb_typeof(p_batch) <> 'object'
       OR jsonb_typeof(p_runners) <> 'array' OR jsonb_array_length(p_runners) < 1 THEN
        RAISE EXCEPTION 'Invalid historical capture payload';
    END IF;
    IF (p_batch->>'race_date')::date >= (now() AT TIME ZONE 'Australia/Melbourne')::date THEN
        RAISE EXCEPTION 'Historical capture must be from a previous Melbourne date';
    END IF;
    IF p_batch->>'model_version' <> 'v0.2'
       OR p_batch->>'cohort' <> 'HISTORICAL_RESEARCH_POST_RACE_RETRIEVAL' THEN
        RAISE EXCEPTION 'Historical capture cohort or frozen model mismatch';
    END IF;
    IF (p_batch->>'runner_count')::integer <> jsonb_array_length(p_runners) THEN
        RAISE EXCEPTION 'Runner count mismatch';
    END IF;
    new_id := (p_batch->>'id')::uuid;
    INSERT INTO public.v061b_hist_races (
        id, race_date, track, track_key, race_number, track_slug, model_version,
        cohort, form_request_started_utc, form_received_utc, scheduled_start_utc,
        source_form_sha256, scorer_sha256, source_form_payload, runner_count, predicted_count
    ) VALUES (
        new_id, (p_batch->>'race_date')::date, p_batch->>'track',
        p_batch->>'track_key', (p_batch->>'race_number')::integer,
        p_batch->>'track_slug', p_batch->>'model_version', p_batch->>'cohort',
        (p_batch->>'form_request_started_utc')::timestamptz,
        (p_batch->>'form_received_utc')::timestamptz,
        NULLIF(p_batch->>'scheduled_start_utc','')::timestamptz,
        p_batch->>'source_form_sha256', p_batch->>'scorer_sha256',
        p_batch->'source_form_payload',
        (p_batch->>'runner_count')::integer, (p_batch->>'predicted_count')::integer
    );
    FOR item IN SELECT value FROM jsonb_array_elements(p_runners)
    LOOP
        INSERT INTO public.v061b_runner_features (
            batch_id, horse, horse_key, runner_number, scratched_in_historical_response,
            model_rank, model_score, recent_form, recent_form_last5,
            career_starts, career_wins, career_places, career_win_rate,
            track_starts, track_wins, track_places, track_win_rate,
            distance_starts, distance_wins, distance_places, distance_win_rate,
            track_distance_starts, track_distance_wins, track_distance_places, track_distance_win_rate,
            barrier, weight_kg, jockey, trainer,
            jockey_starts,jockey_wins,jockey_places,jockey_win_rate,
            trainer_starts,trainer_wins,trainer_places,trainer_win_rate,
            jockey_stats_raw, trainer_stats_raw,
            feature_payload, bookmaker_quotes, bookmaker_quote_note
        ) VALUES (
            new_id, item->>'horse', item->>'horse_key', (item->>'runner_number')::integer,
            COALESCE((item->>'scratched_in_historical_response')::boolean, false),
            (item->>'model_rank')::integer, (item->>'model_score')::double precision,
            item->>'recent_form', item->>'recent_form_last5',
            (item->>'career_starts')::integer, (item->>'career_wins')::numeric,
            (item->>'career_places')::numeric, (item->>'career_win_rate')::double precision,
            (item->>'track_starts')::integer, (item->>'track_wins')::numeric,
            (item->>'track_places')::numeric, (item->>'track_win_rate')::double precision,
            (item->>'distance_starts')::integer, (item->>'distance_wins')::numeric,
            (item->>'distance_places')::numeric, (item->>'distance_win_rate')::double precision,
            (item->>'track_distance_starts')::integer, (item->>'track_distance_wins')::numeric,
            (item->>'track_distance_places')::numeric, (item->>'track_distance_win_rate')::double precision,
            (item->>'barrier')::integer, (item->>'weight_kg')::numeric,
            item->>'jockey', item->>'trainer',
            (item->>'jockey_starts')::integer, (item->>'jockey_wins')::numeric,
            (item->>'jockey_places')::numeric, (item->>'jockey_win_rate')::double precision,
            (item->>'trainer_starts')::integer, (item->>'trainer_wins')::numeric,
            (item->>'trainer_places')::numeric, (item->>'trainer_win_rate')::double precision,
            item->'jockey_stats_raw', item->'trainer_stats_raw',
            item->'feature_payload', COALESCE(item->'bookmaker_quotes','[]'::jsonb),
            COALESCE(item->>'bookmaker_quote_note', 'NO_HISTORICAL_PRE_RACE_QUOTES')
        );
        saved_count := saved_count + 1;
    END LOOP;
    IF saved_count <> (p_batch->>'runner_count')::integer THEN
        RAISE EXCEPTION 'Incomplete runner save';
    END IF;
    RETURN new_id;
END $$;
REVOKE ALL ON FUNCTION public.v061b_capture_historical(jsonb, jsonb) FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.v061b_capture_historical(jsonb,jsonb) TO service_role;
COMMIT;
