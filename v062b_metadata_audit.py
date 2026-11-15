"""v0.6.2B metadata provenance audit: read existing archives only; never backfill."""
from __future__ import annotations
import re
import pandas as pd

KEYS = {
    'distance': re.compile(r'(?:distance|race.?length|race.?metres|race.?meters)',re.I),
    'rail': re.compile(r'rail',re.I),
    'condition': re.compile(r'(?:track.?condition|going|track.?rating)',re.I),
}
LIMIT=25000

def _flatten(obj, prefix='', depth=0):
    if depth>9:return
    if isinstance(obj,dict):
        for k,v in obj.items():
            path=f'{prefix}.{k}' if prefix else str(k)
            if isinstance(v,(dict,list)):
                yield from _flatten(v,path,depth+1)
            else:
                yield path,v
    elif isinstance(obj,list):
        # Do not treat per-runner distance stats as race length.
        for i,v in enumerate(obj[:100]):
            yield from _flatten(v,f'{prefix}[{i}]',depth+1)

def _safe(value):
    if value is None:return ''
    s=str(value)
    return s[:180]

def audit(historical,prospective):
    rows=[]
    for cohort,archives in [('HISTORICAL_POST_RACE',historical),
                            ('PROSPECTIVE_AT_LOCK',prospective)]:
        for a in archives:
            bid=str(a.get('id') or a.get('batch_id') or '')
            if cohort=='HISTORICAL_POST_RACE':
                sources={'source_form_payload':a.get('source_form_payload'),
                         'historical_race_record':{k:v for k,v in a.items()
                                                   if k!='source_form_payload'}}
            else:
                payload=a.get('payload') or {}
                sources={'form_response_raw':payload.get('form_response_raw'),
                         'locked_payload_metadata':{k:v for k,v in payload.items()
                            if k not in ('form_response_raw','rankings','odds_quotes')},
                         'prospective_race_record':{k:v for k,v in a.items()
                                                   if k!='payload'}}
            found=0
            for source,obj in sources.items():
                for path,value in _flatten(obj):
                    category=next((name for name,rx in KEYS.items()
                                   if rx.search(path.split('.')[-1])),None)
                    if category is None:continue
                    # This is an evidence inventory, not a validated extractor.
                    # Per-horse form stats must never be used as race distance.
                    is_runner=bool(re.search(r'(?:runners|horses|participants|entries|form|stats)\\?\[\d+\]',path,re.I))
                    rows.append({'cohort':cohort,'batch_id':bid,
                                 'race_date':str(a.get('race_date') or '')[:10],
                                 'track':a.get('track'),'race_number':a.get('race_number'),
                                 'category':category,'source':source,'json_path':path,
                                 'value_sample':_safe(value),
                                 'candidate_race_level':not is_runner,
                                 'status':'UNVERIFIED_FIELD_CANDIDATE'})
                    found+=1
                    if found>=LIMIT:break
                if found>=LIMIT:break
            if not found:
                rows.append({'cohort':cohort,'batch_id':bid,
                             'race_date':str(a.get('race_date') or '')[:10],
                             'track':a.get('track'),'race_number':a.get('race_number'),
                             'category':'NONE','source':'NONE','json_path':'',
                             'value_sample':'','candidate_race_level':False,
                             'status':'NO_MATCHING_METADATA_KEY'})
    df=pd.DataFrame(rows)
    coverage=(df.groupby(['cohort','category','source','candidate_race_level','status'],
                         dropna=False).agg(races=('batch_id','nunique'),
                                           occurrences=('batch_id','size')).reset_index()
              if not df.empty else pd.DataFrame())
    return df,coverage


def render(secret):
    import streamlit as st
    from v061_prospective import archive_client,_read_all,TABLE
    from v061b_historical import RACES,read_all
    st.subheader('🧭 v0.6.2B — Distance, rail & going source audit')
    st.caption('Inspects existing archived JSON paths and samples. No inference from '
               'runner distance statistics, no external lookups, no archive writes. '
               'Candidates require manual schema verification before extraction.')
    if not st.button('Inspect original metadata sources',key='v062b_source_audit'):return
    try:
        client=archive_client(secret)
        historical=read_all(client,RACES,'*','race_date')
        prospective=_read_all(client,TABLE,order='created_at')
        fields,coverage=audit(historical,prospective)
    except Exception as exc:
        st.error('Metadata source audit failed: '+str(exc))
        st.exception(exc)
        return
    st.metric('Archived races inspected',len(historical)+len(prospective))
    st.dataframe(coverage,hide_index=True,use_container_width=True)
    st.dataframe(fields,hide_index=True,use_container_width=True)
    for name,frame in [('metadata_candidates',fields),('metadata_coverage',coverage)]:
        st.download_button('Export '+name,frame.to_csv(index=False).encode(),
                           file_name='v062b_'+name+'.csv',mime='text/csv',key='v062b_'+name)
    st.warning('A matching JSON key is not proof of race length or point-in-time validity. '
               'Historical and prospective cohorts remain separate.')
