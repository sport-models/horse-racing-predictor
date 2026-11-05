"""Install v0.4 validation tab into the existing app.py; no scoring or schema changes."""
from pathlib import Path
import ast
import shutil

app = Path('app.py')
module = Path('v04_validation.py')
if not app.exists() or not module.exists():
    raise SystemExit('Place install_v04.py and v04_validation.py beside app.py, then retry.')
s = app.read_text(encoding='utf-8')
ast.parse(s)
ast.parse(module.read_text(encoding='utf-8'))
if 'v04_validation.render(db_rows)' in s:
    raise SystemExit('v0.4 validation already installed; no changes made.')
old = """t1, t2, t3, t4, t5 = st.tabs([
    '🏇 Race Analysis',
    '💰 Bet Tracker',
    '🏁 Race Results',
    '📊 Performance',
    '🔌 API Test'
])"""
new = """t1, t2, t3, t4, t5, t6 = st.tabs([
    '🏇 Race Analysis',
    '💰 Bet Tracker',
    '🏁 Race Results',
    '📊 Performance',
    '🧪 Model Validation',
    '🔌 API Test'
])"""
if s.count(old) != 1 or s.count('with t5:\n    puntersedge_tab()') != 1:
    raise SystemExit('Unexpected app.py layout. No changes made; please send the error.')
updated = 'import v04_validation\n' + s.replace(old, new).replace('with t5:\n    puntersedge_tab()', 'with t5:\n    v04_validation.render(db_rows)\nwith t6:\n    puntersedge_tab()')
ast.parse(updated)
shutil.copy2(app, Path('app.py.pre_v04_backup'))
app.write_text(updated, encoding='utf-8')
print('SUCCESS: v0.4 validation tab installed. Run: python -m py_compile app.py v04_validation.py')
