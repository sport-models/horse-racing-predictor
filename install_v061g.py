"""v0.6.1g: add selected Victorian venues to *live* Race Analysis only.

Run from the repository root: python install_v061g.py
Does not touch score_runner, historical data, Bet Tracker, or Supabase.
"""
from pathlib import Path
from datetime import datetime, timezone
import ast

TARGET = Path('app.py')
OLD = "TARGET_TRACKS = {'flemington', 'caulfield', 'moonee valley', 'mooneevalley'}"
NEW = "TARGET_TRACKS = {'flemington', 'caulfield', 'moonee valley', 'mooneevalley',\n                 'geelong', 'sandown', 'ballarat', 'seymour', 'seymore', 'pakenham'}"
OLD_WARNING = "No Flemington, Caulfield or Moonee Valley meeting returned for this date. Your saved bets and results remain accessible in the other tabs."
NEW_WARNING = "No selected Victorian racecourses returned for this date. Check API Meeting Diagnostics for venues returned by FormFav. Your saved bets and results remain accessible in the other tabs."

def updated(source):
    if OLD in source:
        result = source.replace(OLD, NEW, 1)
    elif NEW in source:
        result = source
    else:
        raise ValueError('Unexpected TARGET_TRACKS definition in app.py; no changes made')
    if OLD_WARNING in result:
        result = result.replace(OLD_WARNING, NEW_WARNING, 1)
    elif NEW_WARNING not in result:
        raise ValueError('Race Analysis warning text changed; no changes made')
    ast.parse(result)
    return result

def main():
    if not TARGET.is_file():
        raise SystemExit('Run this command from the Codespaces repository root containing app.py')
    original = TARGET.read_text(encoding='utf-8')
    changed = updated(original)
    if changed == original:
        print('v0.6.1g is already installed; app.py unchanged')
        return
    backup = TARGET.with_name('app.py.v061g_backup_' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ'))
    backup.write_text(original, encoding='utf-8')
    TARGET.write_text(changed, encoding='utf-8')
    print(f'Installed v0.6.1g live-race track selection. Backup: {backup}')
    print('Production score_runner was not modified.')

if __name__ == '__main__':
    main()
