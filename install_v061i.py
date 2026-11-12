"""Install read-only v0.6.1i dashboard in app.py, idempotently and defensively."""
from pathlib import Path
import ast

p=Path('app.py')
if not p.is_file():
    raise SystemExit('Run from project root alongside app.py')
s=p.read_text(encoding='utf-8')
if 'v061i_analysis.render(secret)' in s:
    print('v0.6.1i already installed; no changes')
    raise SystemExit(0)
if 'import v061h_strategy' not in s or 'v061h_strategy.render(secret)' not in s:
    raise SystemExit('v0.6.1h must be installed before v0.6.1i; app.py unchanged')
anchor='    v061h_strategy.render(secret)'
if s.count(anchor)!=1:
    raise SystemExit('Cannot uniquely identify v0.6.1h dashboard insertion point')
# Keep new import close to the top so streamlit script never executes it late.
if 'import v061i_analysis' not in s:
    s='import v061i_analysis\n'+s
s=s.replace(anchor,anchor+'\n    st.divider()\n    v061i_analysis.render(secret)',1)
ast.parse(s)
backup=Path('app.py.before_v061i.bak')
if not backup.exists():backup.write_text(p.read_text(encoding='utf-8'),encoding='utf-8')
p.write_text(s,encoding='utf-8')
print('Installed v0.6.1i dashboard. Original saved as app.py.before_v061i.bak')
