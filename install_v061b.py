"""Idempotent, narrow installer for the existing Streamlit app.py.

Run in GitHub Codespaces from the repository root. Does not touch the v0.2 scorer.
"""
from __future__ import annotations
import argparse
import ast
from pathlib import Path
import shutil
from datetime import datetime, timezone

IMPORT_ANCHOR='import v061_prospective\n'
RENDER_ANCHOR='    v061_prospective.render(secret)\n'
NEW_IMPORT='import v061b_historical\n'
NEW_RENDER='    v061b_historical.render(secret, score_runner)\n'


def score_source(src):
    tree=ast.parse(src)
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name=='score_runner':
            return ast.get_source_segment(src,node)
    raise RuntimeError('Frozen score_runner() not found')


def updated(src):
    ast.parse(src)
    score=score_source(src)
    if NEW_IMPORT not in src:
        if src.count(IMPORT_ANCHOR)!=1:
            raise RuntimeError('Unexpected app.py structure: missing or repeated import anchor')
        src=src.replace(IMPORT_ANCHOR,NEW_IMPORT+IMPORT_ANCHOR,1)
    if NEW_RENDER not in src:
        if src.count(RENDER_ANCHOR)!=1:
            raise RuntimeError('Unexpected app.py structure: missing or repeated render anchor')
        src=src.replace(RENDER_ANCHOR,RENDER_ANCHOR+"    st.divider()\n"+NEW_RENDER,1)
    ast.parse(src)
    if score_source(src)!=score:
        raise AssertionError('FROZEN v0.2 scoring source changed; refusing installation')
    return src


def run():
    parser=argparse.ArgumentParser()
    parser.add_argument('--check',action='store_true',help='Verify patch, do not change app.py')
    parser.add_argument('--app',default='app.py',help='Path to Streamlit app')
    args=parser.parse_args()
    p=Path(args.app)
    module=p.parent/'v061b_historical.py'
    if not p.is_file():raise SystemExit(f'Cannot find {p}')
    if not module.is_file():raise SystemExit(f'Upload v061b_historical.py alongside {p}')
    source=p.read_text(encoding='utf-8')
    new=updated(source)
    if args.check:
        print('CHECK OK: app patch is compatible, frozen v0.2 score_runner unchanged, '
              + ('already installed' if source==new else 'ready to install'))
        return
    if new==source:
        print('Already installed: no changes made')
        return
    backup=p.with_name(p.name+'.v061b_backup_'+datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S'))
    shutil.copy2(p,backup)
    p.write_text(new,encoding='utf-8')
    print('Installed v0.6.1b; backed up original app.py to '+str(backup))
    print('Check syntax: python -m py_compile app.py v061b_historical.py')


if __name__=='__main__':
    run()