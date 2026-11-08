"""v0.5.7b: read-only prediction field coverage diagnostics.

No changes to bets, prediction records, official results or production scoring.
"""
from collections import defaultdict
from datetime import datetime, timezone
from difflib import get_close_matches
import pandas as pd
import streamlit as st
from v057a_market_benchmark import horse_key, race_key, timestamp


def _batch_time(rows):
    times = [timestamp(r.get('predicted_at')) for r in rows]
    return min(times) if times and all(times) else datetime.max.replace(tzinfo=timezone.utc)


def _name_map(rows):
    d = defaultdict(list)
    for r in rows:
        d[horse_key(r.get('horse'))].append(r)
    return d


def diagnose(predictions, results):
    """Return race summary, missing-runner details and per-batch diagnostics."""
    pred, official = defaultdict(list), defaultdict(list)
    errors = []
    for kind, rows, dest in [('prediction', predictions, pred), ('result', results, official)]:
        for row in rows:
            try:
                dest[race_key(row)].append(row)
            except (KeyError, TypeError, ValueError) as exc:
                errors.append({'source': kind, 'row_id': str(row.get('id', '?')),
                               'issue': 'INVALID_RACE_KEY', 'detail': str(exc)})
    overview, details, batch_rows = [], [], []
    for race in sorted(set(pred) | set(official)):
        ps, rs = pred[race], official[race]
        label = f'{race[0]} {race[1]} R{race[2]}'
        batches = defaultdict(list)
        for p in ps:
            batches[str(p.get('prediction_batch_id') or f'MISSING_BATCH:{p.get("id", "?")}')].append(p)
        ordered = sorted(batches.items(), key=lambda pair: (_batch_time(pair[1]), pair[0]))
        result_map = _name_map(rs)
        result_keys = set(result_map) - {''}
        starter_keys = {k for k, entries in result_map.items() if any(r.get('scratched') is False for r in entries)} - {''}
        scratch_keys = {k for k, entries in result_map.items() if any(r.get('scratched') is True for r in entries)} - {''}
        result_duplicate = sorted(k for k, entries in result_map.items() if len(entries) > 1 or not k)
        chosen_id, chosen = ordered[0] if ordered else ('', [])
        selected = _name_map(chosen)
        predicted_keys = set(selected) - {''}
        missing_starters = sorted(starter_keys - predicted_keys)
        predicted_scratches = sorted(predicted_keys & scratch_keys)
        predicted_not_in_results = sorted(predicted_keys - result_keys)
        extra_starters = sorted(starter_keys - predicted_keys)
        duplicate_predictions = sorted(k for k, entries in selected.items() if len(entries) > 1 or not k)
        latest_batch = ordered[-1][0] if ordered else ''
        selected_at = _batch_time(chosen)
        selected_at_str = selected_at.isoformat() if selected_at != datetime.max.replace(tzinfo=timezone.utc) else ''
        if not ps:
            status = 'MISSING_PREDICTIONS'
        elif not rs:
            status = 'MISSING_RESULTS'
        elif result_duplicate:
            status = 'AMBIGUOUS_OFFICIAL_RESULTS'
        elif duplicate_predictions:
            status = 'AMBIGUOUS_PREDICTIONS'
        elif not selected_at_str:
            status = 'UNVERIFIED_PREDICTION_TIMESTAMP'
        elif predicted_not_in_results:
            status = 'PREDICTED_HORSES_ABSENT_FROM_RESULTS'
        elif missing_starters:
            status = 'MISSING_ACTUAL_STARTERS'
        elif predicted_keys == result_keys:
            status = 'FULL_DECLARED_FIELD'
        else:
            status = 'ALL_STARTERS_COVERED_DECLARED_FIELD_DIFFERS'
        overview.append({
            'race': label, 'race_date': race[0], 'track_key': race[1], 'race_number': race[2],
            'status': status, 'official_declared_count': len(rs), 'official_starters': len(starter_keys),
            'official_scratches': len(scratch_keys), 'earliest_batch_id': chosen_id,
            'earliest_batch_at_utc': selected_at_str, 'total_prediction_batches': len(ordered),
            'latest_batch_id': latest_batch, 'earliest_batch_horses': len(chosen),
            'missing_actual_starters': len(missing_starters),
            'predicted_scratches': len(predicted_scratches),
            'predicted_not_in_results': len(predicted_not_in_results),
            'duplicate_result_keys': len(result_duplicate),
            'duplicate_prediction_keys': len(duplicate_predictions),
            'full_declared_field_match': bool(predicted_keys == result_keys and not result_duplicate and not duplicate_predictions),
            'all_actual_starters_predicted': bool(starter_keys <= predicted_keys and bool(starter_keys)),
            'point_in_time_verified': False,
            'official_start_time_verified': False,
        })
        for kind, keys, lookup in [
            ('MISSING_ACTUAL_STARTER', missing_starters, result_map),
            ('PREDICTED_SCRATCH', predicted_scratches, result_map),
            ('PREDICTED_NOT_IN_RESULTS', predicted_not_in_results, selected),
            ('DUPLICATE_OFFICIAL_HORSE', result_duplicate, result_map),
            ('DUPLICATE_PREDICTED_HORSE', duplicate_predictions, selected),
        ]:
            other_keys = predicted_keys if kind == 'MISSING_ACTUAL_STARTER' else result_keys
            for key in keys:
                candidates = get_close_matches(key, list(other_keys), n=3, cutoff=0.65)
                details.append({'race': label, 'batch_id': chosen_id, 'issue': kind,
                                'horse': ' / '.join(str(x.get('horse', '')) for x in lookup.get(key, [])),
                                'horse_key': key, 'possible_name_matches': '; '.join(candidates),
                                'note': 'Suggestions only; never auto-merge horse identities.'})
        for idx, (bid, group) in enumerate(ordered):
            pmap = _name_map(group)
            pkeys = set(pmap) - {''}
            batch_rows.append({'race': label, 'batch_id': bid, 'batch_order': idx + 1,
                               'prediction_at_utc': _batch_time(group).isoformat() if _batch_time(group) != datetime.max.replace(tzinfo=timezone.utc) else '',
                               'predicted_count': len(group), 'missing_starters': len(starter_keys - pkeys),
                               'predicted_not_in_results': len(pkeys - result_keys),
                               'declared_field_match': pkeys == result_keys,
                               'all_starters_covered': bool(starter_keys) and starter_keys <= pkeys,
                               'is_earliest_batch': idx == 0,
                               'research_only': True})
    return (pd.DataFrame(overview), pd.DataFrame(details, columns=['race','batch_id','issue','horse','horse_key','possible_name_matches','note']),
            pd.DataFrame(batch_rows), pd.DataFrame(errors, columns=['source','row_id','issue','detail']))


def render(db_rows):
    st.subheader('🔎 v0.5.7b — Prediction Coverage Diagnostics')
    st.caption('Read-only: diagnoses missing starters, scratches, horse-name differences and saved prediction batches.')
    st.warning('This is diagnostic research only. Partial-field rankings are NOT equivalent to full-field predictions. '
               'Prediction timestamps alone do not verify pre-off availability.')
    if not st.button('Run coverage diagnostics', key='v057b_run'):
        return
    try:
        with st.spinner('Checking saved predictions against official results...'):
            predictions = db_rows('race_predictions', order='predicted_at')
            results = db_rows('runner_results', order=None)
            races, runners, batches, errors = diagnose(predictions, results)
    except Exception as exc:
        st.error(f'Diagnostics failed: {exc}')
        return
    st.metric('Races examined', len(races))
    if not races.empty:
        st.dataframe(races['status'].value_counts().rename_axis('Coverage status').reset_index(name='Races'),
                     hide_index=True, use_container_width=True)
        st.dataframe(races, hide_index=True, use_container_width=True)
    else:
        st.info('No saved predictions or results found.')
    for title, frame, filename in [
        ('Race coverage', races, 'v057b_race_coverage.csv'),
        ('Horse-level differences', runners, 'v057b_horse_differences.csv'),
        ('Prediction batch comparison', batches, 'v057b_prediction_batches.csv'),
        ('Invalid input rows', errors, 'v057b_invalid_rows.csv'),
    ]:
        with st.expander(f'{title} ({len(frame)} rows)', expanded=title == 'Horse-level differences'):
            if not frame.empty:
                st.dataframe(frame, hide_index=True, use_container_width=True)
            st.download_button('Download ' + title + ' CSV', frame.to_csv(index=False).encode('utf-8'),
                               file_name=filename, mime='text/csv', key='v057b_download_' + filename)
    st.info('No database writes, bet settlements, prediction changes or production-model changes were made.')
