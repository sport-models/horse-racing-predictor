"""v0.6.1u — one selectable, highlighted rankings table.

Presentation only. Uses frozen v0.2 verified breakdowns from v0.6.1t.
Never recomputes rankings or touches the lock/write path.
"""
from __future__ import annotations
import pandas as pd
from v061t_missing_data_ui import details_for_rankings, explanation_for_runner
from v061s_feature_audit_ui import WEIGHTS


def formatted_rankings(frame):
    """Return original fields with best-book odds, no fabricated/stale pricing."""
    required={'Rank','Horse','Form Score','Sportsbet Win','Sportsbet Place','Best Win','Best Win Book',
              'Best Place','Best Place Book'}
    missing=required-set(frame.columns)
    if missing:
        raise ValueError('Ranking display missing fields: '+', '.join(sorted(missing)))
    display=frame.copy().reset_index(drop=True)
    display['Rank']=pd.to_numeric(display['Rank'],errors='raise').astype(int)
    if display['Rank'].duplicated().any() or list(display['Rank'])!=list(range(1,len(display)+1)):
        raise ValueError('Invalid display rank sequence; table not rendered')
    # Keep original numeric odds values rather than rounding/modifying underlying prices.
    return display


def row_highlight(row):
    """Styler only: subtle fixed colouring by pre-existing model rank."""
    rank=int(row['Rank'])
    color={1:'#e5f5ea',2:'#e9f2ff',3:'#fff7dc'}.get(rank,'')
    return [f'background-color: {color}' if color else '' for _ in row]


def render(frame, source_form, race_key=None):
    import streamlit as st
    display=formatted_rankings(frame)
    details=details_for_rankings(source_form,display)
    st.caption('Select a horse row to see its verified v0.2 scoring explanation. Bookmaker odds do not affect rank.')
    styling=display.style.apply(row_highlight,axis=1).format({
        'Form Score':lambda x:'—' if pd.isna(x) else f'{x:.2f}',
        'Sportsbet Win':lambda x:'—' if pd.isna(x) else f'${x:.2f}',
        'Sportsbet Place':lambda x:'—' if pd.isna(x) else f'${x:.2f}',
        'Best Win':lambda x:'—' if pd.isna(x) else f'${x:.2f}',
        'Best Place':lambda x:'—' if pd.isna(x) else f'${x:.2f}',
    })
    # Streamlit native dataframe: real table borders, horizontal scroll, and
    # row-selection event. No separate per-runner expander or fake column table.
    event=st.dataframe(styling,hide_index=True,use_container_width=True,
                       on_select='rerun',selection_mode='single-row',
                       key='v061u_selected_'+str(race_key or 'race'))
    indices=(event.selection.rows if event is not None else []) or []
    idx=indices[0] if indices and isinstance(indices[0],int) and 0<=indices[0]<len(details) else 0
    item=details[idx]
    st.markdown(f"**Why #{item['rank']} — {item['horse']}?**")
    st.info(explanation_for_runner(item,details))
    if item['status']=='VERIFIED':
        breakdown=item['breakdown']
        factors=pd.DataFrame([{'Factor':factor,'Fixed weight':f'{WEIGHTS[factor]:.0%}',
                               'Component score':round(breakdown['factors'][factor],4),
                               'Weighted contribution (pts)':round(breakdown['contributions'][factor],3)}
                              for factor in WEIGHTS])
        st.dataframe(factors,hide_index=True,use_container_width=True)
        st.caption(breakdown['note'])
    else:
        st.caption('An unscored or unverified horse has no fabricated scoring explanation.')
