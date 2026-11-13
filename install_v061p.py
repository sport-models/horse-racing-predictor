"""Safe idempotent v0.6.1p install: research UI only; no score_runner editing."""
from pathlib import Path
import ast
p=Path('app.py')
s=p.read_text(encoding='utf-8')
if 'import v061p_race_type_lab' not in s:
    s='import v061p_race_type_lab\n'+s
if 'v061p_race_type_lab.render(secret)' not in s:
    anchor='    v061o_weight_lab.render(secret)'
    if anchor not in s:
        raise SystemExit('v0.6.1o render anchor missing; app.py not modified')
    s=s.replace(anchor,anchor+'\n    st.divider()\n    v061p_race_type_lab.render(secret)',1)
ast.parse(s)
backup=Path('app_before_v061p.py')
if not backup.exists():backup.write_text(p.read_text(encoding='utf-8'),encoding='utf-8')
p.write_text(s,encoding='utf-8')
print('v0.6.1p installed; frozen v0.2 scorer unchanged')
