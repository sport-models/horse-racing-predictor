"""v0.6.1v: explicit, meeting-level prospective locks; never creates bets.

Every race is independently refreshed, scored, saved, and archived using the
existing single-race evidence pipeline. Failures are isolated per race.
"""
from datetime import datetime, timedelta, timezone
import pandas as pd
import streamlit as st
import v061_prospective as prospective

SAFETY_BUFFER = timedelta(minutes=5)


def run(meeting, races, race_date, *, api_key, puntersedge_key, db_client,
        get_value, meeting_name, score_runner, recent_form_score, win_rate,
        as_number, odds_find_race, odds_runner_quotes, puntersedge_request,
        get_items, snapshot_race):
    track = meeting_name(meeting)
    track_slug = get_value(meeting, 'slug', 'trackSlug',
                           default=track.lower().replace(' ', '-'))
    report = []
    entries = []
    for race in races:
        if not isinstance(race, dict):
            continue
        number = prospective.safe_race_number(get_value(race, 'raceNumber', 'number'))
        schedule = prospective.iso_timestamp(race.get('startTime'))
        if number is None:
            report.append({'Race': '?', 'Status': 'SKIPPED', 'Reason': 'Invalid race number', 'Batch': ''})
            continue
        entries.append((schedule, number))
    entries.sort(key=lambda item: (item[0] is None, item[0] or datetime.max.replace(tzinfo=timezone.utc), item[1]))
    seen = set()
    for scheduled, number in entries:
        if number in seen:
            report.append({'Race': number, 'Status': 'SKIPPED', 'Reason': 'Duplicate race number in meeting', 'Batch': ''})
            continue
        seen.add(number)
        if scheduled is None or datetime.now(timezone.utc) + SAFETY_BUFFER >= scheduled:
            report.append({'Race': number, 'Status': 'SKIPPED', 'Reason': 'No valid schedule or less than 5 minutes to scheduled start', 'Batch': ''})
            continue
        try:
            prospective.ensure_new_race(db_client, race_date, track, number)
            # Do not create a second legacy batch if an earlier archive failed.
            existing = (db_client.table('race_predictions').select('prediction_batch_id')
                        .eq('race_date', str(race_date)[:10]).eq('track', track)
                        .eq('race_number', number).limit(1).execute().data or [])
            if existing:
                report.append({'Race': number, 'Status': 'SKIPPED', 'Reason': 'Legacy predictions already exist; inspect archive integrity', 'Batch': ''})
                continue
            prepared = prospective.prepare_live_snapshot(
                race_date=race_date, track=track, track_slug=track_slug,
                race_number=number, formfav_key=api_key,
                puntersedge_key=puntersedge_key, get_items=get_items,
                get_value=get_value, meeting_name=meeting_name,
                score_runner=score_runner, recent_form_score=recent_form_score,
                win_rate=win_rate, as_number=as_number,
                odds_find_race=odds_find_race, odds_runner_quotes=odds_runner_quotes,
                puntersedge_request=puntersedge_request)
            if datetime.now(timezone.utc) + SAFETY_BUFFER >= scheduled:
                raise ValueError('Less than 5 minutes to scheduled start after fresh retrieval')
            batch = snapshot_race(race_date, track, number,
                                  prepared['rankings_df'],
                                  prepared['bookmaker_quotes'],
                                  prepared['odds_snapshot_at'])
            try:
                prospective.archive_locked_batch(db_client, batch, prepared)
            except Exception as exc:
                report.append({'Race': number, 'Status': 'UNVERIFIED', 'Reason': 'Legacy batch saved; archive failed: ' + str(exc), 'Batch': batch})
                continue
            report.append({'Race': number, 'Status': 'ARCHIVED', 'Reason': 'Fresh pre-scheduled snapshot; actual-off not certified', 'Batch': batch})
        except Exception as exc:
            report.append({'Race': number, 'Status': 'FAILED', 'Reason': str(exc), 'Batch': ''})
    return pd.DataFrame(report, columns=['Race', 'Status', 'Reason', 'Batch'])


def render(meeting, races, race_date, **dependencies):
    with st.expander('🔒 Bulk prospective capture — entire meeting', expanded=False):
        st.caption('Research-only: ranks and locks each upcoming race independently. No bets are created. '
                   'Each race receives a fresh form request, its own lock timestamp and immutable evidence. '
                   'Races within five minutes of scheduled start are skipped. '
                   'Provider odds may be missing; no odds are invented.')
        st.write('Meeting:', dependencies['meeting_name'](meeting))
        st.write('Races on meeting card:', len(races))
        confirmed = st.checkbox('I understand this creates permanent pre-race predictions for eligible races.',
                                key='bulk_lock_confirm')
        if st.button('🔒 Analyse & Lock All Upcoming Races', disabled=not confirmed,
                     key='bulk_lock_meeting', type='primary'):
            with st.spinner('Independently capturing upcoming races; do not refresh this page...'):
                result = run(meeting, races, race_date, **dependencies)
            st.session_state['bulk_capture_report'] = result
        report = st.session_state.get('bulk_capture_report')
        if report is not None:
            st.dataframe(report, hide_index=True, use_container_width=True)
            st.download_button('Download bulk capture report', report.to_csv(index=False).encode('utf-8'),
                               file_name='bulk_prospective_capture.csv', mime='text/csv',
                               key='bulk_capture_download')
            st.warning('Only ARCHIVED rows count as confirmed evidence. UNVERIFIED rows require investigation; '
                       'never backfill them as verified pre-race locks.')
