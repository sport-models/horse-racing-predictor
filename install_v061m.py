"""Idempotent v0.6.1m installer; run once from repository root."""
from pathlib import Path
import ast

root = Path(__file__).resolve().parent
archive_path = root / 'v061_prospective.py'
app_path = root / 'app.py'
if not archive_path.exists() or not app_path.exists():
    raise SystemExit('Missing app.py or v061_prospective.py; run from repository root')
archive = archive_path.read_text(encoding='utf-8')
app = app_path.read_text(encoding='utf-8')
needle = "    payload = dict(prepared['payload'])\n"
insert = needle + "    from v061m_capture import attach_shadow\n    payload = attach_shadow(payload)  # SAME immutable archive row/hash as v0.2 rankings\n"
if 'payload = attach_shadow(payload)' not in archive:
    if archive.count(needle) != 1:
        raise SystemExit('Expected archive_locked_batch layout not found; no files changed')
    archive = archive.replace(needle, insert, 1)
if 'import v061m_capture\n' not in app:
    app = 'import v061m_capture\n' + app
anchor = '    v061l_confidence.render(secret)\n'
if '    v061m_capture.render(secret)\n' not in app:
    if app.count(anchor) != 1:
        raise SystemExit('Model Validation render anchor not found; no files changed')
    app = app.replace(anchor, anchor + '    st.divider()\n    v061m_capture.render(secret)\n', 1)
ast.parse(app)
ast.parse(archive)
archive_path.write_text(archive, encoding='utf-8')
app_path.write_text(app, encoding='utf-8')
print('v0.6.1m installed: confidence shadows attached inside immutable archive write, research UI added.')
