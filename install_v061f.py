"""Install the read-only v0.6.1f track dashboard into the historical tab.

Run in horse-racing-predictor repository root AFTER v0.6.1b and preferably
v0.6.1d (dead heats) and v0.6.1e (expanded Victorian track scope).
Creates timestamped backup, validates Python syntax, and makes no database edits.
"""
from __future__ import annotations

import ast
from datetime import datetime, timezone
from pathlib import Path

FILE = Path('v061b_historical.py')
MODULE = Path('v061f_track_dashboard.py')
MARKER = '# V061F_TRACK_PERFORMANCE_DASHBOARD'
ANCHOR = """            if not reports['races'].empty:
                st.dataframe(reports['races'].tail(50),hide_index=True,use_container_width=True)
"""
ADDED = ANCHOR + """            # V061F_TRACK_PERFORMANCE_DASHBOARD
            import v061f_track_dashboard
            st.divider()
            v061f_track_dashboard.render(reports)
"""


def updated(src):
    if MARKER in src:
        if ADDED not in src:
            raise ValueError('Dashboard marker found but expected integration missing; no change made')
        ast.parse(src)
        return src
    if src.count(ANCHOR) != 1:
        raise ValueError(f'Expected one historical research tab anchor, found {src.count(ANCHOR)}; no change made')
    changed=src.replace(ANCHOR,ADDED,1)
    ast.parse(changed)
    return changed


def main():
    if not FILE.is_file() or not MODULE.is_file():
        raise SystemExit('Run from repository root with v061b_historical.py and v061f_track_dashboard.py present')
    ast.parse(MODULE.read_text(encoding='utf-8'),filename=str(MODULE))
    before=FILE.read_text(encoding='utf-8')
    try:
        after=updated(before)
    except (ValueError,SyntaxError) as exc:
        raise SystemExit('NOT INSTALLED: '+str(exc)) from exc
    if before==after:
        print('v0.6.1f dashboard already installed; no changes needed')
        return
    backup=FILE.with_name(FILE.name+'.pre_v061f_'+datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')+'.bak')
    backup.write_text(before,encoding='utf-8')
    FILE.write_text(after,encoding='utf-8')
    print('Installed read-only track dashboard in v0.6.1b historical research tab.')
    print('Backup saved:',backup)
    print('No SQL migration, scorer edits or changes to betting ledger.')


if __name__=='__main__':
    main()
