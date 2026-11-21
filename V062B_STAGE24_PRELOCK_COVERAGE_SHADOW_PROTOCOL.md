# v0.6.2B Stage 2.4 — pre-race source coverage and shadow challenger design

**DRAFT RESEARCH ONLY. No production scoring changes, no actual bets, and no new Supabase tables.**

## Implemented

The read-only Model Validation panel `v062b_stage24.py` scans **all archived v0.2 prospective prediction locks**, not just races with results. It accesses only `v061_prospective` archival records, their immutable original FormFav provider payload, original per-runner ranks and numeric scores, and lock timestamps. It does **not** read results, retrieve post-race provider data, or contact any new external API.

For every archived race, report:
- Original payload SHA256 and embedded original FormFav response SHA256 parity; frozen model-version parity.
- Original provider receipt time, ranking time, archive creation time and scheduled race start; schedule ordering is **not proof of actual-off timing**.
- Original locked runners, original source runners, duplicate horse identities, missing/invalid provider data.
- Original per-runner `form` digits and `stats.overall.starts`; flags for data found only in alternative field aliases. Absence of a career-start number is **not** proof the runner was making its debut.
- Original numeric score, rank and saved scoring status, with explicit source-coverage/parity classifications. A horse with no score remains **unscored**; no fallback score is created.

A fixed, versioned `PROTOCOL` in the module specifies the shadow candidate (0.1.0), its SHA256, source restrictions and future experiment gates. The candidate **does not yet assign a rating, place a bet, or rerank a horse**. A genuinely scored alternative cannot be implemented responsibly until a verifiable additional **pre-lock** source is demonstrated. The current result is a coverage and feasibility baseline, not a substitute for that source.

## Safety gates

- On archive hash mismatch, form hash mismatch, wrong model version, invalid/missing timestamps, duplicate race or ambiguous runner source: fail closed and report the reason; never replace with fresh FormFav data.
- No fake debutant label, no made-up career-starts, no imputed numerical ranking and no retroactive scoring of known winners.
- No race results consulted when choosing the candidate. Existing 12–16 November 2026 prospective data informed earlier research and are no longer untouched validation data.
- Future candidate must be committed and locked **before** new races are run, with a verifiable source receipt **before prediction lock**, original provider-as-of and independent actual race-off certification. Evaluate the exact same eligible races versus frozen v0.2; track all scratches, dead heats, abandoned races, missing data and return calculations with original at-lock prices only.
- 15 distinct forward race dates / 100 eligible originally scored starters per stratum are screening floors, **not significance tests**; independently evaluate sample power, track/date clustering and diverse metropolitan-course coverage.
- Do **not** alter the v0.2 scorer or promote the challenger without forward gains, integrity gates and explicit approval.

## Run

In Codespaces:

    git fetch origin
    git switch v062b-barrier-bias-research
    git pull --ff-only origin v062b-barrier-bias-research
    python -m pytest -q
    python -m streamlit run app.py --server.address 0.0.0.0 --server.port 8501

Select **Model Validation → v0.6.2B Stage 2.4 — Pre-race data coverage & challenger design**. Click **Run Stage 2.4 original at-lock coverage audit** and download **ONE** `v062b_stage24_coverage_challenger_audit.zip`. The ZIP contains six audit CSVs and the versioned protocol JSON. Upload that one ZIP for an exact coverage audit.

**No FormFav alternative provider endpoint is invented, assumed or called**. Feasibility of another source is explicitly unassessed until a verified connector/API and pre-lock timestamp source are established. Stage 2.4 can reveal whether the existing archive contains enough information already, but cannot manufacture data that was never supplied.

## Branch integration

The research branch has a manually reconstructed tree retaining the production abandoned-race implementation and all Stage 2.4 source changes. PR #4 remains DRAFT and must not be merged before the complete regression suite, sandbox integration and Git history mergeability are verified. The frozen production v0.2 scorer is unchanged.
