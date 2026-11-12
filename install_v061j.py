"""Safely install v061j view; never replace an existing function or scoring logic."""
from pathlib import Path
import ast

p=Path('app.py')
if not p.is_file():raise SystemExit('Run from repo root beside app.py')
s=p.read_text(encoding='utf-8')
marker='v061j_integrity.render(secret)'
if marker in s:
 print('v0.6.1j already installed. Nothing changed.')
 raise SystemExit(0)
anchor='    v061i_analysis.render(secret)'
if s.count(anchor)!=1 or 'v061i_analysis' not in s:
 raise SystemExit('Expected v0.6.1i in app.py exactly once. No modifications made.')
if not Path('v061j_integrity.py').is_file():
 raise SystemExit('Missing v061j_integrity.py; extract the ZIP first. No changes.')
if 'import v061j_integrity' not in s:
 # Insert below existing module import; do not prepend before future imports.
 imp='import v061i_analysis'
 if s.count(imp)!=1:raise SystemExit('Cannot find unique v061i_analysis import; no changes')
 s=s.replace(imp,imp+'\nimport v061j_integrity',1)
s=s.replace(anchor,anchor+'\n    st.divider()\n    v061j_integrity.render(secret)',1)
ast.parse(s)
backup=Path('app.py.before_v061j.bak')
if not backup.exists():backup.write_text(p.read_text(encoding='utf-8'),encoding='utf-8')
p.write_text(s,encoding='utf-8')
print('Installed v0.6.1j (additive, read-only). Created app.py.before_v061j.bak')
