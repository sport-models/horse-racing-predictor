"""Guarded, idempotent installation. Patches UI wiring only, never score_runner or lock logic."""
from pathlib import Path
import argparse

MODULE='v061s_feature_audit_ui'


def install(text):
    if f'import {MODULE}\n' not in text:
        text=f'import {MODULE}\n'+text
    old="    st.subheader('📊 Full Race Rankings')\n    st.dataframe(df, hide_index=True, use_container_width=True)\n"
    if old in text:
        text=text.replace(old,"    # v0.6.1s: combined rankings and odds appear below; avoid duplicate tables.\n",1)
    elif 'v0.6.1s: combined rankings' not in text:
        raise RuntimeError('Analysis rankings structure not recognised; app.py unchanged')
    old='    st.subheader("💰 Sportsbet & Best Available Odds")'
    new='    st.subheader("📊 Full race rankings & bookmaker odds")'
    if old in text:text=text.replace(old,new,1)
    elif new not in text:raise RuntimeError('Odds table location not recognised; app.py unchanged')
    old='''    st.subheader('⭐ Top Five Model Selections')
    st.dataframe(df.head(5), hide_index=True, use_container_width=True)
'''
    new="    # v0.6.1s: top five are already rows 1–5 of the consolidated full-field table.\n"
    if old in text:text=text.replace(old,new,1)
    elif new not in text:raise RuntimeError('Top-five table not recognised; app.py unchanged')
    anchor='''    with st.expander("Compare all bookmakers"):
'''
    new=f'''    {MODULE}.render_analysis_explanations(analysis['race_data'], df)

'''+anchor
    if new not in text:
        if anchor not in text:raise RuntimeError('Bookmaker comparison location not found; app.py unchanged')
        text=text.replace(anchor,new,1)
    anchor='    v061q_stayer_validation.render(secret)'
    new=anchor+f'\n    st.divider()\n    {MODULE}.render_feature_audit(secret)'
    if new not in text:
        if anchor not in text:raise RuntimeError('Validation page anchor missing; app.py unchanged')
        text=text.replace(anchor,new,1)
    return text


def main():
    p=argparse.ArgumentParser();p.add_argument('--file',default='app.py');args=p.parse_args()
    target=Path(args.file);original=target.read_text(encoding='utf-8');updated=install(original)
    compile(updated,str(target),'exec')
    if updated!=original:
        target.with_suffix(target.suffix+'.before_v061s.bak').write_text(original,encoding='utf-8')
        target.write_text(updated,encoding='utf-8')
        print('Installed v0.6.1s; original saved to app.py.before_v061s.bak')
    else:print('v0.6.1s already installed; no changes')

if __name__=='__main__':main()
