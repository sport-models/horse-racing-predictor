"""Idempotent installer: inserts research-only lab into app.py; backs up original."""
from pathlib import Path
import ast
p=Path('app.py');s=p.read_text(encoding='utf-8')
if 'import v061o_weight_lab' not in s:
 s='import v061o_weight_lab\n'+s
if 'v061o_weight_lab.render(secret)' not in s:
 anchor='    v061n_relative_lab.render(secret)'
 if anchor not in s:raise SystemExit('v0.6.1n render anchor missing. No file modified.')
 s=s.replace(anchor,anchor+'\n    st.divider()\n    v061o_weight_lab.render(secret)',1)
ast.parse(s)
backup=Path('app_before_v061o.py')
if not backup.exists():backup.write_text(p.read_text(encoding='utf-8'),encoding='utf-8')
p.write_text(s,encoding='utf-8')
print('v0.6.1o installed; v0.2 score_runner unchanged')
