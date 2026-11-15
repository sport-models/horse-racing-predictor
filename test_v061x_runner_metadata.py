# Other legacy test modules install stub v061_prospective in sys.modules during
# pytest collection. Load the real module under a unique name for isolation.
import importlib.util
from pathlib import Path
from v042_results import canonical

_spec = importlib.util.spec_from_file_location(
    "_v061x_real_prospective", Path(__file__).with_name("v061_prospective.py")
)
_real = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_real)
_runner_scoring_status = _real._runner_scoring_status
_locked_runner_metadata = _real._locked_runner_metadata


def test_null_score_explicit_zero_starts():
    assert _runner_scoring_status(None, {'careerStarts': 0}) == 'NULL_SCORE_ZERO_CAREER_STARTS'


def test_null_score_empty_recent_form():
    assert _runner_scoring_status(None, {'form': []}) == 'NULL_SCORE_NO_RECORDED_RECENT_FORM'


def test_null_score_missing_form_not_assumed_debut():
    assert _runner_scoring_status(None, {'name': 'Debut Unknown'}) == 'NULL_SCORE_REASON_UNVERIFIED'


def test_null_score_nonempty_form_not_assumed_debut():
    assert _runner_scoring_status(None, {'form': ['2', '3']}) == 'NULL_SCORE_REASON_UNVERIFIED'


def test_valid_score_preserved():
    assert _runner_scoring_status(0, {'form': []}) == 'SCORED'


def test_metadata_read_from_archived_form():
    data, duplicates = _locked_runner_metadata({'runners': [
        {'name': 'Example Horse', 'jockey': 'J. Smith', 'trainer': 'T. Jones'}
    ]})
    assert data[canonical('Example Horse')]['jockey'] == 'J. Smith'
    assert not duplicates
