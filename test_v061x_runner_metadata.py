"""Isolated regression tests: older tests stub sys.modules at collection time."""
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def _probe(expression):
    # Fresh interpreter avoids legacy collection-time sys.modules substitutions
    # for both v061_prospective and its v042_results dependency.
    code = (
        "import json\n"
        "from v061_prospective import _runner_scoring_status, _locked_runner_metadata\n"
        "from v042_results import canonical\n"
        "print(json.dumps(" + expression + "))\n"
    )
    proc = subprocess.run(
        [sys.executable, "-c", code], cwd=ROOT, capture_output=True,
        text=True, check=True, timeout=30,
    )
    return json.loads(proc.stdout.strip().splitlines()[-1])


def test_null_score_explicit_zero_starts():
    assert _probe("_runner_scoring_status(None, {'careerStarts': 0})") == 'NULL_SCORE_ZERO_CAREER_STARTS'


def test_null_score_empty_recent_form():
    assert _probe("_runner_scoring_status(None, {'form': []})") == 'NULL_SCORE_NO_RECORDED_RECENT_FORM'


def test_null_score_missing_form_not_assumed_debut():
    assert _probe("_runner_scoring_status(None, {'name': 'Debut Unknown'})") == 'NULL_SCORE_REASON_UNVERIFIED'


def test_null_score_nonempty_form_not_assumed_debut():
    assert _probe("_runner_scoring_status(None, {'form': ['2', '3']})") == 'NULL_SCORE_REASON_UNVERIFIED'


def test_valid_score_preserved():
    assert _probe("_runner_scoring_status(0, {'form': []})") == 'SCORED'


def test_metadata_read_from_archived_form():
    expression = (
        "_locked_runner_metadata({'runners': ["
        "{'name': 'Example Horse', 'jockey': 'J. Smith', 'trainer': 'T. Jones'}"
        "]})[0][canonical('Example Horse')]['jockey']"
    )
    assert _probe(expression) == 'J. Smith'
