"""v0.4.5 abandoned-race recording. No invented placings, no bet changes."""
from datetime import date, timedelta
from urllib.parse import urlparse
import streamlit as st
from v042_results import canonical, normalize_track


def race_id(row):
    return (str(row['race_date'])[:10], normalize_track(row['track']),
            int(row['race_number']))


def validate_manual_abandonment(race, evidence_url, evidence_note,
                                predicted_races, saved_result_races, prior):
    """Pure fail-closed validator; never authorise with API results inferred."""
    from datetime import date as d
    day,track,num = race
    day = d.fromisoformat(str(day)[:10])
    if day >= d.today():
        # Same-day abandoned is allowed AFTER the user verifies the official announcement.
        if day > d.today():
            raise ValueError('A future race cannot be verified abandoned')
    if race_id({'race_date':day,'track':track,'race_number':num}) not in predicted_races:
        raise ValueError('No saved locked prediction found for selected race')
    if race_id({'race_date':day,'track':track,'race_number':num}) in saved_result_races:
        raise ValueError('Full runner results already exist; manual reconciliation required')
    if race_id({'race_date':day,'track':track,'race_number':num}) in prior:
        raise ValueError('Race already recorded abandoned; no update or duplicate allowed')
    url = urlparse(evidence_url.strip())
    if url.scheme != 'https' or not url.netloc or url.username or url.password:
        raise ValueError('Official evidence must be a full HTTPS URL')
    if len(evidence_note.strip()) < 12:
        raise ValueError('Describe the official announcement in at least 12 characters')
    return {'race_date':day.isoformat(),'track':track,
            'track_key':normalize_track(track),'race_number':int(num),
            'outcome':'ABANDONED','official_evidence_url':evidence_url.strip(),
            'evidence_note':evidence_note.strip(),'reviewed_by':'MANUAL_REVIEW'}


def render(db_rows, db):
    st.subheader('🚫 v0.4.5 — Record Officially Abandoned Race')
    st.caption('Separate from importing final placings. This marks a verified abandoned race '
               'and excludes it from prospective win-rate/ROI denominators. '
               'No results, bets or predictions are written or settled.')
    try:
        official = db_rows('abandoned_races',order=None)
    except Exception as exc:
        st.warning('Abandonment registry is not installed or cannot be read. '
                   'Run migrate_v045_abandoned.sql once in Supabase SQL Editor. '
                   'No changes have been made. Details: '+str(exc))
        return
    try:
        predictions = db_rows('race_predictions',order='predicted_at')
        results = db_rows('runner_results',columns='race_date,track,race_number',order=None)
    except Exception as exc:
        st.error('Cannot verify pending race identities: '+str(exc));return
    archived = {race_id(x) for x in predictions}
    finished = {race_id(x) for x in results}
    already = {race_id(x) for x in official}
    cutoff = date.today()-timedelta(days=30)
    selectable = sorted(r for r in archived if cutoff <= date.fromisoformat(r[0]) <= date.today()
                        and r not in finished and r not in already)
    st.metric('Saved races eligible for abandonment review',len(selectable))
    if not selectable:
        st.info('No unprocessed locked race within the last 30 days.');return
    selected = st.selectbox('Select race already officially declared abandoned',selectable,
                            format_func=lambda r:f'{r[0]} — {r[1]} — R{r[2]}',
                            key='v045_abandoned_selected')
    st.warning('Only use this for an OFFICIALLY CONFIRMED ABANDONMENT. '
               'Do not record delayed, postponed, results-pending, or cancelled-before-start '
               'races without verifying the actual race disposition.')
    url = st.text_input('Official racing authority evidence URL (https://...)',
                        key='v045_abandoned_evidence_url')
    note = st.text_area('What does the official notice say? Include source and exact race.',
                        key='v045_abandoned_evidence_note')
    accepted = st.checkbox('I independently verified this exact race was officially abandoned '
                           'and understand it cannot be edited or automatically settled.',
                           key='v045_abandoned_checked')
    phrase = st.text_input('Type ABANDONED to confirm this outcome',key='v045_abandoned_phrase')
    if st.button('Record verified abandonment — immutable Supabase insert',
                 disabled=not (accepted and phrase.strip()=='ABANDONED'),
                 key='v045_abandoned_save',type='primary'):
        try:
            record=validate_manual_abandonment(
                selected,url,note,archived,finished,already)
            # Race-level recheck at write time: do not overwrite final placings.
            db_client=db()
            if any(normalize_track(row.get('track'))==record['track_key']
                   for row in db_rows('runner_results',
                                      columns='race_date,track,race_number',
                                      order=None)
                   if str(row.get('race_date'))[:10]==record['race_date']
                   and int(row.get('race_number') or 0)==record['race_number']):
                raise ValueError('Final race results now exist: no abandonment saved')
            # Database UNIQUE and immutable trigger guard duplicates/races already recorded.
            db_client.table('abandoned_races').insert(record).execute()
            st.success(f"Official abandonment recorded for {selected[0]} {selected[1]} R{selected[2]}. "
                       'No bets auto-voided. Verify outstanding bets with your bookmaker.')
            st.session_state.pop('v045_abandoned_checked',None)
        except Exception as exc:
            st.error('Nothing overwritten. Abandonment save blocked or failed: '+str(exc))
    if official:
        with st.expander('Already recorded abandoned races (read-only)'):
            import pandas as pd
            st.dataframe(pd.DataFrame(official),hide_index=True,use_container_width=True)
