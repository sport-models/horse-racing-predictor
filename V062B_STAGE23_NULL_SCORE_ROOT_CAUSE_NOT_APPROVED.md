# v0.6.2B Stage 2.3 — missing-score root-cause audit

Status: RESEARCH ONLY. The frozen production v0.2 scorer and all archival data remain unchanged.

## What v0.2 actually does
In app.py, score_runner returns None whenever recent_form_score(runner.get('form')) is None OR career_starts == 0. The former requires at least one numerical digit in the exact FormFav field named form (last five digits weighted by recency). The latter uses stats.overall.starts (converted to a non-negative integer, defaulting to 0 if absent). The scorer does NOT use root-level careerStarts/starts or aliases recentForm/recent_form. Therefore missing stats may result in a NULL score even for a previously raced horse. A fallback value of zero used in scoring is NEVER evidence of zero career starts.

No replacement numeric ratings are generated in Stage 2.3, and no changes to weights or ranks are made.

## Run in the Codespaces research sandbox
    git fetch origin
    git switch v062b-barrier-bias-research
    git pull --ff-only origin v062b-barrier-bias-research
    python -m pytest -q
    python -m streamlit run app.py --server.address 0.0.0.0 --server.port 8501

Navigate to Model Validation → Stage 2.3 — Unscored horse root-cause audit.

- Select Historical discovery (post-race FormFav), run, download v062b_stage23_historical_audit_bundle.zip.
- Select Prospective archived (schedule-only evidence), run, download v062b_stage23_prospective_audit_bundle.zip.
- Upload ONLY TWO ZIP files. Each ZIP contains runner gate evidence, an unscored subset, explicit reason totals, affected races and unscored winners, source/score consistency anomalies, hash/source issues and Stage 2 exclusions.

This is READ-ONLY. Do not use this older research branch for production abandonment writes or bulk importing; research and production share Supabase.

## Source validation and interpretation

Historical FormFav was fetched after the race; it does not certify pre-race as-of status. Prospective inputs were snapshotted by scheduled off and lack independent actual-off certification. Never use later API data to replace originals. The prospective audit checks the whole payload SHA256, embedded raw form response SHA256 and v0.2 model identity; invalid/missing original source is flagged, never inferred.

Four questions are separated:
(1) Does saved v0.2 scoring data contain usable form digits?
(2) Does saved v0.2 scoring data contain a positive stats.overall.starts count?
(3) Does the source contain alternative field aliases that v0.2 does NOT actually use?
(4) Does the recorded score agree with those two score-eligibility gates?

Explicit provider-reported 0 starts is a SOURCE CLAIM, not independently confirmed debut status; missing stats is never relabelled as debut status. No synthetic score is assigned. A genuine unscored winner remains an official winner and retains its original ranking. Historic and prospective cohorts remain separate, and dead heats and abandoned races are not silently forced into the unique-winner barrier study.

Prospective winners previously examined from 12–15 November 2026 have already influenced this research and cannot serve as untouched forward validation of a newly developed score rule. No production promotion, numerical fallback, form alias switch, historical ranking correction or adjusted ROI is authorised.
