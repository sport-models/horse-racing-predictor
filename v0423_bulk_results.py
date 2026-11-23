"""v0.4.2.3 — Review and import multiple verified full-field race results.

READS FormFav *provisional* as draft. Requires explicit verified-final attestation
for each race, never overrides existing results or archived forecasts.
"""
from datetime import datetime, timezone
import requests

import v0422_manual_results as manual

MAX_FETCH = 24


def race_identity(row):
    return (str(row['race_date'])[:10], manual.canonical(row['track']), int(row['race_number']))


def unique_races(races):
    result = {}
    for r in races:
        result.setdefault((r[0], manual.canonical(r[1]), int(r[2])), (r[0], r[1], int(r[2])))
    return sorted(result.values(), reverse=True)


def select_unimported(predictions, results, abandoned, *, asof=None):
    """Return the truly unimported prospectively locked races, once per race."""
    return unique_races(manual.available_unimported_races(predictions, results, abandoned, asof=asof))


def select_original_batch(db, race):
    """Fetch ONLY one race's predictions, ordered by timestamp and UUID."""
    data = (db.table('race_predictions')
            .select('horse,model_rank,predicted_at,prediction_batch_id,id')
            .eq('race_date', race[0]).eq('track', race[1]).eq('race_number', race[2])
            .order('predicted_at').order('id').limit(201).execute()).data or []
    if len(data) >= 201:
        raise ValueError('Prediction batch too large; manual review required.')
    return manual.earliest_batch(data)


def is_previously_imported(db, race):
    return bool((db.table('runner_results').select('id')
                 .eq('race_date',race[0]).eq('track',race[1])
                 .eq('race_number',race[2]).limit(1).execute()).data)


def is_abandoned(db, race):
    # Note: abandoned track key is normalized lower-case, whitespace trimmed.
    return bool((db.table('abandoned_races').select('id')
                 .eq('race_date',race[0]).eq('track_key',str(race[1]).strip().casefold())
                 .eq('race_number',race[2]).limit(1).execute()).data)


def prepare_preview(db, race, key):
    if is_previously_imported(db,race):
        raise ValueError('Race already imported; no overwrite.')
    if is_abandoned(db,race):
        raise ValueError('Race officially abandoned; no result import.')
    batch, predicted = select_original_batch(db,race)
    predicted = sorted(predicted, key=lambda p: (p.get('model_rank') or 999, p['horse']))
    initial = [{'Horse':p['horse'],'Status':'UNSET','Position':None,'Margin (lengths)':None} for p in predicted]
    status='unavailable'; warnings=[]
    try:
        payload=manual.fetch_formfav_preview(key,race)
        rows,stats=manual.map_provider_rows(predicted,payload)
        status=str(payload.get('resultStatus') or 'unknown')
        warnings=stats['warnings']
    except (requests.RequestException, ValueError, TypeError, KeyError) as exc:
        rows=initial
        stats={'matched':0,'extra':0,'unresolved':len(rows)}
        warnings=[f'FormFav preview unavailable: {exc}. Verify and complete using an independent results source.']
    return {'race':race,'batch':batch,'locked':[p['horse'] for p in predicted],
            'rows':rows,'status':status,'stats':stats,'warnings':warnings}


def validate_for_save(preview, edited_rows, *, source, reviewer, note, final_verified,
                      complete_verified, dead_heat_verified=False, url=''):
    if not isinstance(preview,dict) or not preview.get('batch'):
        raise ValueError('Original prediction batch absent; cannot save.')
    parsed=manual.validate_entries(edited_rows,official_url=url,reviewer=reviewer,
        verification_note=note,entire_field_verified=complete_verified,
        official_final_verified=final_verified,dead_heat_verified=dead_heat_verified,
        source_name=source)
    locked={manual.canonical(h) for h in preview['locked']}
    submitted={p['horse_key'] for p in parsed}
    if locked-submitted:
        raise ValueError(f'Original locked runners missing from full field: {sorted(locked-submitted)}')
    return parsed


def build_insert_rows(parsed, race, *, source, reviewer, note, url='', now=None):
    stamp=now or datetime.now(timezone.utc).isoformat()
    output=[]
    for row in parsed:
        item=dict(row)
        item.update({'race_date':race[0],'track':race[1],'race_number':race[2],
            'result_status':'final','source':'ManualResults:'+source,
            'official_evidence_url':str(url).strip() or None,
            'verified_by':reviewer.strip(),
            'verification_note':f'Source: {source}; {note.strip()}',
            'verified_at':stamp})
        output.append(item)
    return output


def save_approved_race(db, preview, edited_rows, *, source, reviewer, note,
                       final_verified, complete_verified, dead_heat_verified=False, url=''):
    """One race per DB insertion. No partial field by design. May be retried safely
    after an ambiguous transport failure ONLY after checking DB for existing rows.
    """
    race=tuple(preview['race'])
    parsed=validate_for_save(preview,edited_rows,source=source,reviewer=reviewer,
        note=note,final_verified=final_verified,complete_verified=complete_verified,
        dead_heat_verified=dead_heat_verified,url=url)
    if is_previously_imported(db,race):
        raise ValueError('Already imported. No duplicates or overwrite permitted.')
    if is_abandoned(db,race):
        raise ValueError('Abandoned race. No results saved.')
    # Revalidate earliest batch before committing, so a stale UI cannot be used
    # if a prediction batch was changed/added after the screen loaded.
    original,_=select_original_batch(db,race)
    if original!=preview['batch']:
        raise ValueError('Original prediction batch changed since preview; reload.')
    rows=build_insert_rows(parsed,race,source=source,reviewer=reviewer,note=note,url=url)
    db.table('runner_results').insert(rows).execute()
    return len(rows)


def render(db,db_rows,secret):
    import pandas as pd
    import streamlit as st
    with st.expander('📋 v0.4.2.3 — Bulk verification of outstanding results (REVIEW REQUIRED)',expanded=False):
        st.caption('Select outstanding prospective races. FormFav prefill is a draft, including when its status is interim. Approve EACH race against a settled independent result. Never changes locked predictions.')
        key=secret('FORMFAV_API_KEY')
        if not key:
            st.error('FORMFAV_API_KEY is required to prefill races.');return
        try:
            all_preds=db_rows('race_predictions',columns='race_date,track,race_number',order='predicted_at')
            all_results=db_rows('runner_results',columns='race_date,track,race_number',order=None)
            all_abandoned=db_rows('abandoned_races',columns='race_date,track_key,race_number',order=None)
            races=select_unimported(all_preds,all_results,all_abandoned)
            if not races:
                st.success('No outstanding races in the last 45 days.');return
            label=lambda r:f'{r[0]} — {r[1]} R{r[2]}'
            chosen=st.multiselect('Select races to prefill (maximum 24)',races,
                format_func=label, key='v0423_select')
            if st.button('Fetch selected races — DRAFT ONLY (no writes)',key='v0423_fetch'):
                if not chosen: st.warning('Choose at least one race.')
                elif len(chosen)>MAX_FETCH: st.error('Select 24 races or fewer per batch.')
                else:
                    previews={};errors={}
                    bar=st.progress(0)
                    for i,race in enumerate(chosen):
                        try:previews[race]=prepare_preview(db,race,key)
                        except Exception as exc:errors[race]=str(exc)
                        bar.progress((i+1)/len(chosen))
                    st.session_state['v0423_previews']=previews
                    st.session_state['v0423_errors']=errors
                    st.session_state['v0423_nonce']=st.session_state.get('v0423_nonce',0)+1
            for race,reason in st.session_state.get('v0423_errors',{}).items():
                st.error(f'{label(race)}: {reason}')
            previews=st.session_state.get('v0423_previews') or {}
            if not previews:return
            st.info(f'{len(previews)} race(s) previewed. Only explicitly checked and verified races will be committed. Partial success is possible: each race is saved independently.')
            source=st.selectbox('Verification source for this batch',
                ['Sportsbet','Official racing authority','Other bookmaker','Other verified source'],key='v0423_source')
            reviewer=st.text_input('Checked by (required)',key='v0423_reviewer')
            url=st.text_input('Supporting result URL (optional; leave blank for Sportsbet app)',key='v0423_url')
            candidates=[]
            for race,preview in list(previews.items()):
                with st.expander(f'{label(race)} — FormFav {preview["status"]}; {preview["stats"]["unresolved"]} unresolved',expanded=True):
                    st.caption(f'Prediction batch: {preview["batch"]}. Matched {preview["stats"]["matched"]}, additional runners {preview["stats"]["extra"]}. Inspect every runner and scratch.')
                    for warning in preview['warnings']:st.warning(warning)
                    index=f'{race[0]}_{manual.canonical(race[1])}_{race[2]}_{st.session_state.get("v0423_nonce",0)}'
                    frame=st.data_editor(pd.DataFrame(preview['rows']),
                        num_rows='dynamic',hide_index=True,use_container_width=True,key='v0423_ed_'+index,
                        column_config={
                            'Horse':st.column_config.TextColumn('Horse',required=True),
                            'Status':st.column_config.SelectboxColumn('Status',options=list(manual.STATUSES),required=True),
                            'Position':st.column_config.NumberColumn('Finish position',min_value=1,max_value=60,step=1,format='%d'),
                            'Margin (lengths)':st.column_config.NumberColumn('Margin (lengths)',min_value=0.0,max_value=500.0,step=0.1,format='%.3f')})
                    st.caption('For 1st: winning margin ahead of 2nd. For other finishers: lengths behind winner. Unknown margin: blank. DNF/F/LR/PU and scratched: blank position and margin.')
                    note=st.text_input('Evidence note specific to this race (at least 8 characters)',key='v0423_note_'+index)
                    final=st.checkbox('I independently verified the settled FINAL results (not merely an interim FormFav feed)',key='v0423_final_'+index)
                    complete=st.checkbox('I verified EVERY declared runner, scratch, finish code, position and entered margin',key='v0423_complete_'+index)
                    deadheat=st.checkbox('Verified official two-way dead heat for first (if applicable)',key='v0423_deadheat_'+index)
                    approve=st.checkbox('APPROVE this race for saving',key='v0423_approve_'+index)
                    if approve:
                        candidates.append((race,preview,frame.to_dict('records'),note,final,complete,deadheat))
            if st.button(f'Validate and SAVE approved races ({len(candidates)})',type='primary',key='v0423_save',disabled=not candidates):
                saved=[];blocked=[]
                for race,preview,rows,note,final,complete,deadheat in candidates:
                    try:
                        n=save_approved_race(db,preview,rows,source=source,reviewer=reviewer,note=note,
                            final_verified=final,complete_verified=complete,dead_heat_verified=deadheat,url=url)
                        saved.append((race,n))
                        previews.pop(race,None)
                    except Exception as exc:
                        blocked.append((race,str(exc)))
                st.session_state['v0423_previews']=previews
                for race,n in saved:st.success(f'{label(race)}: saved {n} independently verified runners.')
                for race,issue in blocked:st.error(f'{label(race)}: BLOCKED — {issue}')
                if saved:
                    st.info('Successfully saved races are excluded from future outstanding-race lists; refresh to update the selectors.')
        except Exception as exc:
            st.error(f'Bulk verification blocked: {exc}')
