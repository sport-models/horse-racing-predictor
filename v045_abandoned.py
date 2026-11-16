"""v0.4.5 abandoned-race recording. No invented placings, no bet changes."""
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo
import streamlit as st
from v042_results import normalize_track


def melbourne_today():
    return datetime.now(ZoneInfo('Australia/Melbourne')).date()


def race_id(row):
    return (str(row['race_date'])[:10], normalize_track(row['track']),
            int(row['race_number']))


def validate_manual_abandonment(race, predicted_races, saved_result_races, prior):
    """Pure fail-closed validation. A confirmed status, not invented placings."""
    from datetime import date as d
    day, track, num = race
    day = d.fromisoformat(str(day)[:10])
    key = race_id({'race_date': day, 'track': track, 'race_number': num})
    if day > melbourne_today():
        raise ValueError('A future race cannot be verified abandoned')
    if key not in predicted_races:
        raise ValueError('No saved locked prediction found for selected race')
    if key in saved_result_races:
        raise ValueError('Full runner results already exist; manual reconciliation required')
    if key in prior:
        raise ValueError('Race already recorded abandoned; no duplicate allowed')
    return {
        'race_date': day.isoformat(), 'track': track, 'track_key': key[1],
        'race_number': int(num), 'outcome': 'ABANDONED',
        # Explicit reviewer attestation, not a claim that a source URL was captured.
        'reviewed_by': 'USER_CONFIRMED_OFFICIAL_ABANDONMENT',
        'official_evidence_url': None, 'evidence_note': None,
    }

def render(db_rows, db):
    st.subheader('🚫 v0.4.5 — Record Officially Abandoned Race')
    st.caption('Select the race, confirm it was officially abandoned, then save. '
               'No placings, bets or locked predictions are changed.')
    try:
        official = db_rows('abandoned_races',order=None)
    except Exception as exc:
        st.warning('Abandonment registry unavailable. Run the v0.4.5 Supabase migrations '
                   'before using this feature. No changes made. Details: '+str(exc))
        return
    try:
        predictions = db_rows('race_predictions',order='predicted_at')
        results = db_rows('runner_results',columns='race_date,track,race_number',order=None)
    except Exception as exc:
        st.error('Cannot verify pending race identities: '+str(exc));return
    archived = {race_id(x) for x in predictions}
    finished = {race_id(x) for x in results}
    already = {race_id(x) for x in official}
    today = melbourne_today()
    cutoff = today-timedelta(days=30)
    selectable = sorted(r for r in archived if cutoff <= date.fromisoformat(r[0]) <= today
                        and r not in finished and r not in already)
    st.metric('Saved races eligible for abandonment review',len(selectable))
    if not selectable:
        st.info('No unprocessed locked race within the last 30 days.');return
    selected = st.selectbox('Select race already officially declared abandoned',selectable,
                            format_func=lambda r:f'{r[0]} — {r[1]} — R{r[2]}',
                            key='v045_abandoned_selected')
    if st.session_state.get('v045_abandoned_last_selected') != selected:
        # A previously ticked confirmation is NEVER valid for a different race.
        for key in ('v045_abandoned_checked',):
            st.session_state.pop(key,None)
        st.session_state['v045_abandoned_last_selected'] = selected
    st.warning('Confirm only when the racing authority has officially declared this '
               'specific race abandoned — not when results are delayed, the meeting is '
               'postponed, or the result is still pending.')
    accepted = st.checkbox(
        f'I confirm {selected[0]} — {selected[1]} Race {selected[2]} was officially abandoned',
        key='v045_abandoned_checked')
    if st.button('Save abandoned race',
                 disabled=not accepted, key='v045_abandoned_save', type='primary'):
        try:
            record=validate_manual_abandonment(selected,archived,finished,already)
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
