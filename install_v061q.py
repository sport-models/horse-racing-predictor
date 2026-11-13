"""Idempotent installer, failing closed if prior validated v0.6.1p anchor is absent."""
import ast
from pathlib import Path
app=Path('app.py');source=app.read_text(encoding='utf-8')
anchor='    v061p_race_type_lab.render(secret)'
if anchor not in source and 'v061q_stayer_validation.render(secret)' not in source:
    raise SystemExit('Expected v0.6.1p Model Validation hook not found; app.py unchanged')
new=source
if 'import v061q_stayer_validation' not in new:new='import v061q_stayer_validation\n'+new
if 'v061q_stayer_validation.render(secret)' not in new:
    new=new.replace(anchor,anchor+'\n    st.divider()\n    v061q_stayer_validation.render(secret)',1)
ast.parse(new)
if new!=source:
    backup=Path('app_before_v061q.py')
    if not backup.exists():backup.write_text(source,encoding='utf-8')
    app.write_text(new,encoding='utf-8')
print('v0.6.1q Model Validation hook installed (idempotent). v0.2 unchanged.')
