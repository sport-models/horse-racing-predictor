"""Safe, idempotent app.py wiring. Does not modify score_runner or database."""
from pathlib import Path
import ast
import shutil

HERE=Path(__file__).resolve().parent
APP=HERE/'app.py'
IMPORT='import v061l_confidence'
CALL='    v061l_confidence.render(secret)'
ANCHOR='    v061k_stage2.render(secret)'

def main():
    if not APP.exists():raise SystemExit('app.py not found. Upload installer beside app.py.')
    if not (HERE/'v061l_confidence.py').exists():raise SystemExit('v061l_confidence.py is missing.')
    old=APP.read_text(encoding='utf-8')
    if ANCHOR not in old:raise SystemExit('Expected v0.6.1k anchor missing. No changes made.')
    if old.count(ANCHOR)!=1:raise SystemExit('Ambiguous anchor; stopped.')
    if old.count(IMPORT)>1 or old.count(CALL)>1:raise SystemExit('Duplicate wiring detected; stopped.')
    new=old
    if IMPORT not in new:new=IMPORT+'\n'+new
    if CALL not in new:new=new.replace(ANCHOR,ANCHOR+'\n    st.divider()\n'+CALL,1)
    ast.parse(new,filename='app.py')
    ast.parse((HERE/'v061l_confidence.py').read_text(encoding='utf-8'))
    if new==old:
        print('Already installed; no changes.')
        return
    backup=APP.with_name('app_before_v061l.py')
    if not backup.exists():shutil.copy2(APP,backup)
    APP.write_text(new,encoding='utf-8')
    print('Installed v0.6.1l dashboard. Existing app backed up in',backup.name)

if __name__=='__main__':main()
