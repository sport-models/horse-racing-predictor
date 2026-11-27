"""Production capture-only, non-blocking hook for individual and bulk locks.

Only a fresh archive receipt may be passed. No historical retry/backfill.
"""
from datetime import datetime, timezone
import v062b_stage26b3_capture as capture


def attempt(db_client, receipt, *, now=None):
    try:
        captured_at = now if now is not None else datetime.now(timezone.utc)
        body = capture.persist(db_client, receipt, now=captured_at)
        if str(body['original_batch_id']) != str(receipt['batch_id']):
            raise RuntimeError('Post-insert original batch identity mismatch')
        return 'CAPTURED', 'Pre-scheduled research evidence saved (capture-only)'
    except Exception as exc:
        return 'FAILED', f'{type(exc).__name__}: {exc}'
