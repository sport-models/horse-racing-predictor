"""One-time idempotent integration. Run from repository root: python install_v061h.py"""
from __future__ import annotations
from pathlib import Path
from datetime import datetime,timezone
import ast

IMPORTS='import v061h_readiness\nimport v061h_strategy\n'
ANCHOR='    v060_validation_hub.render(db_rows)'
ADDITION='    v061h_readiness.render(secret)\n    st.divider()\n    v061h_strategy.render(secret)\n    st.divider()\n'

def score_source(src):
    parsed=ast.parse(src)
    for node in parsed.body:
        if isinstance(node,ast.FunctionDef) and node.name=='score_runner':
            return ast.get_source_segment(src,node)
    raise ValueError('No frozen v0.2 score_runner function found')

def updated(src):
    if 'v061h_readiness.render(secret)' in src and 'v061h_strategy.render(secret)' in src:
        return src
    if 'v061h_readiness.render(secret)' in src or 'v061h_strategy.render(secret)' in src:
        raise ValueError('Partial v0.6.1h integration. Inspect app.py before modifying.')
    if src.count(ANCHOR)!=1:raise ValueError('Could not identify unique Model Validation anchor')
    before=score_source(src)
    if 'import v061h_readiness' not in src:
        src=IMPORTS+src
    src=src.replace(ANCHOR,ADDITION+ANCHOR,1)
    ast.parse(src)
    if score_source(src)!=before:raise ValueError('Frozen score_runner changed; refusing write')
    return src

def main():
    root=Path(__file__).resolve().parent
    for filename in ('v061h_readiness.py','v061h_strategy.py'):
        if not (root/filename).exists():raise SystemExit('Missing '+filename+'; upload all package files first')
    target=root/'app.py'
    src=target.read_text(encoding='utf-8')
    out=updated(src)
    if out==src:print('Already installed; nothing changed');return
    backup=root/('app.py.v061h_backup_'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ'))
    backup.write_text(src,encoding='utf-8')
    target.write_text(out,encoding='utf-8')
    print('Integrated v0.6.1h; backup:',backup.name)
    print('Frozen v0.2 score_runner verified unchanged')

if __name__=='__main__':main()
