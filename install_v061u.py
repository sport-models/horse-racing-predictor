"""Safe, fail-closed installer for v0.6.1u.
Creates dated backup; only exact known previous-version patterns modified.
Run from repository root after copying module files next to app.py.
"""
from pathlib import Path
from datetime import datetime,timezone
import ast

ROOT=Path(__file__).resolve().parent
APP=ROOT/'app.py'
FULL=ROOT/'v043_full_field.py'
for f in (APP,FULL,ROOT/'v061u_analysis_ui.py',ROOT/'v061u_full_field.py'):
    if not f.exists():raise SystemExit(f'Missing required file: {f.name}')
app=APP.read_text(encoding='utf8')
full=FULL.read_text(encoding='utf8')

if 'v061u_analysis_ui.render(display_df' not in app:
    target="v061t_missing_data_ui.render_ranking_table(display_df, analysis['race_data'])"
    if app.count(target)!=1:raise SystemExit('Unexpected current UI pattern. No files changed; inspect app.py.')
    app=app.replace(target,"v061u_analysis_ui.render(display_df, analysis['race_data'], race_key)")
    if 'import v061u_analysis_ui' not in app:
        app='import v061u_analysis_ui\n'+app

# v0.6.1 prospective snapshots are service-role protected; pass secret to the
# existing read-only full-field viewer rather than using the public db_rows.
if 'v043_full_field.render(db_rows, secret)' not in app:
    old='v043_full_field.render(db_rows)'
    if app.count(old)!=1:raise SystemExit('Unexpected v043 call in app.py. No files changed.')
    app=app.replace(old,'v043_full_field.render(db_rows, secret)')

if "'Schedule evidence'" not in full:
    if full.count('def render(db_rows):')!=1:raise SystemExit('Unexpected v043 render signature. No files changed.')
    full=full.replace('def render(db_rows):','def render(db_rows, secret):')
    search="    try:\n        predictions = db_rows('race_predictions', order='predicted_at')\n        results = db_rows('runner_results', order=None)"
    insert="    try:\n        predictions = db_rows('race_predictions', order='predicted_at')\n        results = db_rows('runner_results', order=None)\n        import v061_prospective\n        from v061u_full_field import make_archive_index, proof_for_batch\n        try:\n            archived = v061_prospective._read_all(v061_prospective.archive_client(secret), v061_prospective.TABLE)\n            archive_index = make_archive_index(archived)\n        except Exception as archive_error:\n            archive_index = {}\n            st.warning('Pre-schedule archive evidence unavailable; older metrics remain visible. '+type(archive_error).__name__)"
    if full.count(search)!=1:raise SystemExit('Unexpected v043 fetch pattern. No files changed.')
    full=full.replace(search,insert)
    search="        summary.append({'Date': race[0], 'Track': official[0]['track'], 'Race': race[2],"
    insert="        evidence = proof_for_batch(batch, selected, archive_index)\n        summary.append({'Date': race[0], 'Track': official[0]['track'], 'Race': race[2],\n                        'Schedule evidence': evidence,"
    if full.count(search)!=1:raise SystemExit('Unexpected v043 summary pattern. No files changed.')
    full=full.replace(search,insert)
    search="    st.subheader('Race-by-race performance')"
    insert="""    # Live descriptive scoreboard for every valid imported race, regardless of
    # prediction timing. Never treat this as an approved prospective baseline.
    all_complete=s[(s['Complete field']) & (s['Data valid']) & (s['Top pick finish'].notna())]
    st.markdown('**All complete imported races — descriptive, including same-day locks**')
    st.caption('Updates when additional official race results are imported. Settling bets does '
               'not change race winners or these counters. This includes locks without independently '
               'verified timing and is NOT the strict prospective cohort.')
    a,b,c,d=st.columns(4)
    a.metric('Races with valid top pick',len(all_complete))
    b.metric('Top pick winners',f"{int((all_complete['Top pick finish']==1).sum())}/{len(all_complete)}")
    c.metric('Top pick top-3 finishes',f"{int((all_complete['Top pick finish']<=3).sum())}/{len(all_complete)}")
    d.metric('Winner ranked top 3',f"{int((all_complete['Top 3 covered']==True).sum())}/{len(all_complete)}")
    # New same-day-eligible cohort: only exact archived, before-scheduled-start
    # snapshots qualify. NO assertion that actual-off timing was independently verified.
    prestart=s[(s['Complete field']) & (s['Data valid']) &
               (s['Schedule evidence']=='BEFORE_SCHEDULE_ARCHIVED_NOT_ACTUAL_OFF_CERTIFIED') &
               (s['Top pick finish'].notna())]
    st.markdown('**Before-scheduled-start archive cohort — includes eligible same-day locks**')
    st.caption('Matches each earliest saved prediction batch to its immutable, hash-verified archive, '
               'with fresh form, ranking and server archive timestamps before the scheduled start. '
               'This is NOT independently certified actual race-off timing. Bets do not alter these counts.')
    x,y,z,w=st.columns(4)
    x.metric('Matched pre-schedule races',len(prestart))
    y.metric('Top pick winners', f\"{int((prestart['Top pick finish']==1).sum())}/{len(prestart)}\")
    z.metric('Top pick top-3 finishes', f\"{int((prestart['Top pick finish']<=3).sum())}/{len(prestart)}\")
    w.metric('Winner ranked top 3', f\"{int((prestart['Top 3 covered']==True).sum())}/{len(prestart)}\")
    other=s[(s['Complete field']) & (s['Data valid']) &
            (s['Schedule evidence']!='BEFORE_SCHEDULE_ARCHIVED_NOT_ACTUAL_OFF_CERTIFIED')]
    if not other.empty:
        st.info(f'{len(other)} complete imported race(s) lack a matching before-schedule archive. '
                'They remain visible in the race-by-race table and are excluded from the new cohort.')
    st.subheader('Race-by-race performance')"""
    if full.count(search)!=1:raise SystemExit('Unexpected v043 race table pattern. No files changed.')
    full=full.replace(search,insert)

for path,text in ((APP,app),(FULL,full)):
    ast.parse(text,filename=str(path))

changes=[(APP,app),(FULL,full)]
changed=[]
for path,new in changes:
    old=path.read_text(encoding='utf8')
    if new!=old:
        suffix=datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
        backup=path.with_name(path.name+'.pre_v061u_'+suffix+'.bak')
        backup.write_text(old,encoding='utf8')
        path.write_text(new,encoding='utf8')
        changed.append(path.name)
print('v0.6.1u installer completed. Changed:',', '.join(changed) or 'none (already installed)')
print('No scorer, historical archive, prediction/lock, odds data, or bet records modified.')
