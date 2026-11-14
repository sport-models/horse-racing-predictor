"""v0.6.1t guarded install. Touches only Race Analysis view + research panel wiring."""
from pathlib import Path
import argparse

MODULE='v061t_missing_data_ui'


def install(content):
    marker=f'import {MODULE}\n'
    if marker not in content: content=marker+content
    old='''    st.dataframe(
        display_df,
        hide_index=True,
        use_container_width=True
    )

    v061s_feature_audit_ui.render_analysis_explanations(analysis['race_data'], df)
'''
    new=f'''    # v0.6.1t: one consolidated full-field table; Why? is inside each row.
    {MODULE}.render_ranking_table(display_df, analysis['race_data'])
'''
    if old in content:content=content.replace(old,new,1)
    elif new not in content:raise RuntimeError('Expected existing v0.6.1s rankings UI not found; app.py NOT changed')
    anchor='    v061s_feature_audit_ui.render_feature_audit(secret)'
    addition=anchor+f'\n    st.divider()\n    {MODULE}.render_data_audit(secret)'
    if addition not in content:
        if anchor not in content:raise RuntimeError('v0.6.1s audit panel not found; app.py NOT changed')
        content=content.replace(anchor,addition,1)
    return content


def main():
    p=argparse.ArgumentParser();p.add_argument('--file',default='app.py');a=p.parse_args()
    path=Path(a.file);old=path.read_text(encoding='utf-8')
    updated=install(old);compile(updated,str(path),'exec')
    if updated!=old:
        path.with_suffix(path.suffix+'.before_v061t.bak').write_text(old,encoding='utf-8')
        path.write_text(updated,encoding='utf-8')
        print('Installed v0.6.1t, backup: app.py.before_v061t.bak')
    else:print('Already installed')
if __name__=='__main__':main()
