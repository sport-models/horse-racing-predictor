"""Idempotent install: app.py import and render entries only. Does NOT edit scorer."""
from pathlib import Path
import ast
root=Path('app.py')
if not root.exists():raise SystemExit('Run from project root containing app.py')
for file in ('v061k_integrity.py','v061k_stage2.py','v061j_integrity.py','v061h_strategy.py'):
    if not Path(file).exists():raise SystemExit('Missing '+file+' — no changes made')
s=root.read_text(encoding='utf-8')
if 'v061k_integrity.render(secret)' in s and 'v061k_stage2.render(secret)' in s:
    print('Already installed. No modifications.');raise SystemExit(0)
if s.count('    v061j_integrity.render(secret)') != 1:
    raise SystemExit('v0.6.1j render anchor missing/ambiguous. No changes made.')
if 'v061k_integrity.render(secret)' in s or 'v061k_stage2.render(secret)' in s:
    raise SystemExit('Partial v061k installation: inspect app.py manually, no changes made.')
if s.count('import v061j_integrity')!=1:raise SystemExit('v061j import anchor missing/ambiguous')
s=s.replace('import v061j_integrity','import v061j_integrity\nimport v061k_integrity\nimport v061k_stage2',1)
s=s.replace('    v061j_integrity.render(secret)',
    '    v061j_integrity.render(secret)\n    st.divider()\n    v061k_integrity.render(secret)\n    st.divider()\n    v061k_stage2.render(secret)',1)
ast.parse(s)
backup=Path('app.py.before_v061k.bak')
if not backup.exists():backup.write_text(root.read_text(encoding='utf-8'),encoding='utf-8')
root.write_text(s,encoding='utf-8')
print('Installed scratch-aware v061k and Stage 2 (additive); saved app.py.before_v061k.bak')
