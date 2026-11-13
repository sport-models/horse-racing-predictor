"""Transactional/idempotent source installation for v0.6.1r.

Touches app.py (research UI only) and v061_prospective.py (adds one pure
shadow attachment before archive hash). Keeps backups; NEVER changes v0.2.
"""
from pathlib import Path
import ast
import shutil
import sys

ROOT=Path(__file__).resolve().parent if (Path(__file__).resolve().parent/'app.py').exists() else Path.cwd()
APP=ROOT/'app.py'
PRO=ROOT/'v061_prospective.py'
MOD=ROOT/'v061r_class_validation.py'


def patch(app,pros):
    add_import='import v061r_class_validation'
    render_anchor='    v061q_stayer_validation.render(secret)'
    if add_import not in app:
        if render_anchor not in app:
            raise ValueError('Missing v061q dashboard render anchor; no changes made')
        # import placed near existing imports to avoid import inside indentation
        app=add_import+'\n'+app
    new_render='    v061r_class_validation.render(secret)'
    if new_render not in app:
        if app.count(render_anchor)!=1:raise ValueError('Ambiguous v061q anchor; abort')
        app=app.replace(render_anchor,render_anchor+'\n    st.divider()\n'+new_render)
    archive_anchor="    payload = attach_shadow(payload)  # SAME immutable archive row/hash as v0.2 rankings"
    attach="    from v061r_class_validation import attach_class_shadow\n    payload = attach_class_shadow(payload)  # Frozen at-lock class hypotheses, no score edits"
    if attach not in pros:
        if pros.count(archive_anchor)!=1:
            raise ValueError('Unknown prospective archive layout; abort rather than overwrite model')
        pros=pros.replace(archive_anchor,archive_anchor+'\n'+attach)
    ast.parse(app);ast.parse(pros)
    return app,pros


def main():
    if not all(x.exists() for x in (APP,PRO,MOD)):
        raise SystemExit('Place v061r_class_validation.py and installer beside app.py and v061_prospective.py')
    a=APP.read_text(encoding='utf-8');p=PRO.read_text(encoding='utf-8')
    aa,pp=patch(a,p)
    if a==aa and p==pp:
        print('v0.6.1r already installed; no changes made');return
    # Validate expected importable module before changing project files.
    ast.parse(MOD.read_text(encoding='utf-8'))
    old_files=[(APP,a,aa),(PRO,p,pp)]
    for file,old,new in old_files:
        if old!=new:
            backup=file.with_name(file.stem+'_before_v061r'+file.suffix)
            if not backup.exists():backup.write_text(old,encoding='utf-8')
    done=[]
    try:
        for file,old,new in old_files:
            if old!=new:
                file.write_text(new,encoding='utf-8');done.append((file,old))
    except Exception:
        for file,old in reversed(done):file.write_text(old,encoding='utf-8')
        raise
    print('v0.6.1r installed. app.py UI and immutable archive shadow hook updated. No production scorer changed.')

if __name__=='__main__':main()
