"""v0.6.1h: read-only historical research quality audit (not certified point-in-time)."""
from __future__ import annotations
from collections import Counter
import json
import pandas as pd

FIELDS = ('recent_form','career_starts','track_starts','distance_starts',
          'track_distance_starts','barrier','weight_kg','jockey','trainer',
          'jockey_starts','jockey_wins','trainer_starts','trainer_wins')
RACE_META = ('trackCondition','track_condition','going','trackRating','track_rating',
             'distance','distanceMeters','raceDistance','surface','trackSurface','trackType')

def truthy_value(v):
    return v is not None and not (isinstance(v,str) and not v.strip())

def summarize(batches,features,results,exclusions):
    """All counts are observational; missing source keys are NOT inferred."""
    batch_by_id={str(r['id']):r for r in batches}
    result_ids={str(r['batch_id']) for r in results}
    excluded={str(r['batch_id']):r for r in exclusions}
    missing_results=set(batch_by_id)-result_ids
    with_exclusion=set(excluded)&missing_results
    features_by_batch=Counter(str(r['batch_id']) for r in features)
    duplicate_race_keys=len(batches)-len({(str(r['race_date']),str(r['track_key']),int(r['race_number'])) for r in batches})
    mismatched=[k for k,r in batch_by_id.items() if features_by_batch[k]!=int(r['runner_count'])]
    scored=[r for r in features if r.get('model_rank') is not None and r.get('model_score') is not None]
    unscored=[r for r in features if r.get('model_rank') is not None and r.get('model_score') is None]
    coverage=pd.DataFrame([{'feature':f,'present':sum(truthy_value(r.get(f)) for r in features),
                            'missing':sum(not truthy_value(r.get(f)) for r in features),
                            'total':len(features),
                            'coverage_pct':100*sum(truthy_value(r.get(f)) for r in features)/len(features) if features else None}
                           for f in FIELDS])
    statuses=Counter((r.get('reason') or 'UNKNOWN') for r in exclusions if str(r.get('batch_id')) in missing_results)
    summary=pd.DataFrame([{'archived_races':len(batches),'runner_rows':len(features),'tracks':len({r['track_key'] for r in batches}),
                           'results_imported':len(result_ids&set(batch_by_id)),
                           'without_results':len(missing_results),'excluded_without_results':len(with_exclusion),
                           'unresolved_without_results':len(missing_results-with_exclusion),
                           'ranked_and_scored':len(scored),'ranked_but_unscored':len(unscored),
                           'runner_count_mismatch_races':len(mismatched),'duplicate_race_keys':duplicate_race_keys,
                           'point_in_time_certified':0}])
    race_rows=[]
    key_counts=Counter()
    for r in batches:
        raw=r.get('source_form_payload')
        raw=raw if isinstance(raw,dict) else {}
        nested=raw.get('data') if isinstance(raw.get('data'),dict) else {}
        for key in RACE_META:
            if truthy_value(raw.get(key)) or truthy_value(nested.get(key)):
                key_counts[key]+=1
        bid=str(r['id'])
        race_rows.append({'batch_id':bid,'race_date':r['race_date'],'track':r['track'],
                          'race_number':r['race_number'],'runner_count_saved':features_by_batch[bid],
                          'runner_count_declared':r['runner_count'],
                          'result_imported':bid in result_ids,
                          'excluded':bid in excluded,
                          'exclusion_reason':excluded.get(bid,{}).get('reason'),
                          'exclusion_note':excluded.get(bid,{}).get('note'),
                          'source_race_distance':first_value(raw,nested,('distance','distanceMeters','raceDistance')),
                          'source_track_condition':first_value(raw,nested,('trackCondition','track_condition','going','trackRating','track_rating')),
                          'source_surface':first_value(raw,nested,('surface','trackSurface','trackType'))})
    metadata=pd.DataFrame([{'source_key':k,'races_with_value':key_counts[k],
                            'coverage_pct':100*key_counts[k]/len(batches) if batches else None} for k in RACE_META])
    return {'readiness_summary':summary,'feature_completeness':coverage,
            'race_metadata_keys':metadata,'race_coverage':pd.DataFrame(race_rows),
            'exclusion_reasons':pd.DataFrame([{'reason':k,'races':v} for k,v in sorted(statuses.items())]),
            'unscored_ranked':pd.DataFrame([{'batch_id':r.get('batch_id'),'horse':r.get('horse'),
                                             'model_rank':r.get('model_rank'),'recent_form':r.get('recent_form'),
                                             'career_starts':r.get('career_starts')} for r in unscored])}

def first_value(raw,nested,names):
    for key in names:
        value=raw.get(key)
        if not truthy_value(value):value=nested.get(key)
        if truthy_value(value):
            return json.dumps(value,ensure_ascii=False)[:200] if isinstance(value,(dict,list)) else str(value)[:200]
    return None

def render(secret):
    import streamlit as st
    import v061b_historical as h
    st.subheader('🔎 v0.6.1h — Historical data readiness')
    st.caption('Read-only quality and source-field audit. Historical form was retrieved after racing; no model promotion evidence is created.')
    if not st.button('Run historical feature and coverage audit',key='v061h_audit_run'):
        reports=st.session_state.get('v061h_audit_reports')
    else:
        try:
            client=h.server_db(secret)
            races=h.read_all(client,h.RACES,'id,race_date,track,track_key,race_number,runner_count,source_form_payload','race_date')
            features=h.read_all(client,h.FEATURES,'batch_id,horse,model_rank,model_score,recent_form,career_starts,track_starts,distance_starts,track_distance_starts,barrier,weight_kg,jockey,trainer,jockey_starts,jockey_wins,trainer_starts,trainer_wins')
            results=h.read_all(client,h.RESULTS,'batch_id')
            excluded=h.read_all(client,'v061b_result_exclusions','batch_id,reason,note')
            reports=summarize(races,features,results,excluded)
            st.session_state['v061h_audit_reports']=reports
        except Exception as exc:
            st.error('Readiness audit could not complete: '+str(exc));return
    if reports:
        st.dataframe(reports['readiness_summary'],hide_index=True,use_container_width=True)
        st.warning('Field availability in post-race responses does not prove point-in-time validity. Source names reflect observed keys only; missing values remain missing.')
        for k,v in reports.items():
            if k!='readiness_summary':
                with st.expander(k.replace('_',' ').title()):
                    st.dataframe(v,hide_index=True,use_container_width=True)
            st.download_button('Download '+k+' CSV',v.to_csv(index=False).encode('utf-8'),
                               file_name='v061h_'+k+'.csv',mime='text/csv',key='v061h_'+k)
