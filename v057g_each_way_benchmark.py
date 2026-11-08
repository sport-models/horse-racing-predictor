"""v0.5.7g — read-only, evidence-gated each-way benchmark.

Uses the earliest saved race-prediction batch and its saved bookmaker quotes.
Calculates model-only strategy wherever supported, and an independent pre-race
price-based favourite comparison *only* when one bookmaker priced EVERY starter.
Never uses SP to choose the pre-race favourite, never invents place terms, and
never writes to Supabase or modifies the frozen v0.2 production scoring model.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import date
import io
import math
import re

import pandas as pd
import streamlit as st

from v057c_corrected_benchmark import benchmark, horse_key, race_key, track_key
from v057f_odds_integrity import (parse_saved_quote, preferred_key,
                                  settlement, valid_places)

OVERRIDE_COLS = ['race_date', 'track', 'race_number', 'bookmaker', 'places_paid',
                 'source_reference', 'source_type', 'notes']
SOURCE_TYPES = {'archived_bookmaker_terms', 'archived_bet_slip',
                'provider_historical_archive', 'other_documented_evidence'}


def _label(r):
    return f"{r['race_date']} {r['track']} R{r['race_number']}"


def _race_label_key(row):
    return race_key(row)


def _parse_override_records(content):
    """Parse documentary terms; never treat a blank or guessed value as evidence.

    Returns (validated dictionary, audit rows). Override applies only to exact
    date/track/race/book; it is explicitly marked user-documented, NOT verified
    by the program. Reject ambiguous duplicates, even if values agree.
    """
    if content is None:
        return {}, []
    if isinstance(content, bytes):
        content = content.decode('utf-8-sig')
    if hasattr(content, 'read'):
        content = content.read()
        if isinstance(content, bytes):
            content = content.decode('utf-8-sig')
    frame = pd.read_csv(io.StringIO(content), dtype=str, keep_default_na=False)
    missing = set(OVERRIDE_COLS) - set(frame.columns)
    if missing:
        raise ValueError(f"Terms CSV is missing columns: {', '.join(sorted(missing))}")
    values, log, duplicate_keys = {}, [], set()
    for idx, rec in frame.iterrows():
        x = rec.to_dict()
        issue = ''
        key = None
        dt = str(x['race_date']).strip()
        track = str(x['track']).strip()
        book = str(x['bookmaker']).strip().casefold()
        num = str(x['race_number']).strip()
        if not re.fullmatch(r'\d{4}-\d{2}-\d{2}', dt):
            issue = 'INVALID_DATE'
        else:
            try:
                date.fromisoformat(dt)
            except ValueError:
                issue = 'INVALID_DATE'
        if not issue and (not track_key(track) or not book or not re.fullmatch(r'\d+', num)):
            issue = 'INVALID_RACE_OR_BOOKMAKER'
        if not issue:
            key = (dt, track_key(track), int(num), book)
            if int(num) < 1:
                issue = 'INVALID_RACE_NUMBER'
        count = valid_places(x['places_paid'])
        if not issue and count is None:
            issue = 'MISSING_OR_INVALID_PLACES_PAID'
        source = str(x['source_reference']).strip()
        stype = str(x['source_type']).strip().casefold()
        if not issue and (len(source) < 8 or source.casefold() in {'assumed', 'standard', 'default', 'guess', 'unknown'}):
            issue = 'MISSING_SPECIFIC_DOCUMENTARY_SOURCE'
        if not issue and stype not in SOURCE_TYPES:
            issue = 'INVALID_SOURCE_TYPE'
        logrow = {'csv_row': int(idx)+2, **x, 'acceptance': 'REJECTED' if issue else 'ACCEPTED',
                  'reason': issue, 'evidence_status': 'USER_DOCUMENTED_NOT_INDEPENDENTLY_VERIFIED'}
        log.append(logrow)
        if issue or key is None:
            continue
        if key in values:
            duplicate_keys.add(key)
        else:
            values[key] = {'places_paid': count, 'source_reference': source,
                           'source_type': stype, 'notes': x.get('notes', '')}
    if duplicate_keys:
        for key in duplicate_keys:
            values.pop(key, None)
        for row in log:
            try:
                key = (str(row['race_date']).strip(), track_key(row['track']),
                       int(row['race_number']), str(row['bookmaker']).strip().casefold())
            except (ValueError, TypeError):
                continue
            if key in duplicate_keys:
                row['acceptance'] = 'REJECTED'
                row['reason'] = 'DUPLICATE_DOCUMENTARY_TERMS'
    return values, log


def _quotes_for_runner(prediction, field_size):
    """All valid non-stale saved quotes by bookmaker; reject duplicate books."""
    result, audit, seen = {}, [], set()
    if prediction is None:
        return {}, [{'quote_status': 'MISSING_PREDICTION_ROW'}]
    raw = prediction.get('bookmaker_quotes') or []
    if not isinstance(raw, list):
        return {}, [{'quote_status': 'INVALID_QUOTES_CONTAINER'}]
    for idx, quote in enumerate(raw):
        parsed, issue = parse_saved_quote(quote, prediction.get('predicted_at'), field_size)
        book = (parsed or {}).get('bookmaker', '')
        if not book and isinstance(quote, dict):
            book = str(quote.get('bookmaker') or quote.get('key') or '').strip().casefold()
        audit.append({'quote_index': idx, 'bookmaker': book,
                      'quote_status': issue or 'VALID_SAVED_PRICES',
                      'win': (parsed or {}).get('win'),
                      'place': (parsed or {}).get('place'),
                      'saved_bookmaker_places_paid': (parsed or {}).get('places_paid'),
                      'quote_time_quality': (parsed or {}).get('quote_time_quality', '')})
        if book in seen:
            result.pop(book, None)
            audit[-1]['quote_status'] = 'DUPLICATE_BOOKMAKER_QUOTE'
            continue
        if parsed and book not in seen:
            result[book] = parsed
        if book:
            seen.add(book)
    if not raw:
        audit.append({'quote_status': 'NO_SAVED_QUOTES'})
    return result, audit


def _resolve_terms(quote, bkey, evidence, field_size):
    """Price record wins over manually documented terms; never replace it."""
    explicit = quote.get('places_paid')
    if explicit is not None:
        return explicit, 'SAVED_BOOKMAKER_QUOTE', '', ''
    ov = evidence.get(bkey)
    if ov is None:
        return None, 'MISSING_BOOKMAKER_PLACES_PAID', '', ''
    n = valid_places(ov['places_paid'], field_size)
    if n is None:
        return None, 'INVALID_DOCUMENTED_TERMS_FOR_FIELD_SIZE', '', ov['source_reference']
    return n, 'USER_DOCUMENTED_NOT_INDEPENDENTLY_VERIFIED', ov['source_type'], ov['source_reference']


def _settle_selected(quote, result, bkey, overrides, field_size):
    """Return a stable dictionary with explicit eligibility & evidence fields."""
    finish = result.get('finishing_position') if result else None
    terms, provenance, evidence_type, reference = _resolve_terms(quote, bkey, overrides, field_size)
    output = {
        'win_odds': quote['win'], 'place_odds': quote['place'],
        'place_terms': terms if quote['place'] >= 2 else None,
        'place_terms_provenance': provenance if quote['place'] >= 2 else 'NOT_REQUIRED_WIN_ONLY',
        'evidence_type': evidence_type if quote['place'] >= 2 else '',
        'evidence_reference': reference if quote['place'] >= 2 else '',
        'quote_at_utc': quote['quote_updated_at_utc'],
        'quote_time_quality': quote['quote_time_quality'],
        'finish': finish, 'bet_type': 'EACH_WAY' if quote['place'] >= 2 else 'WIN',
        'settlement_status': 'UNRESOLVED', 'settlement_reason': '',
        'total_staked': None, 'win_return': None, 'place_return': None,
        'total_return': None, 'net_profit': None, 'win_hit': None, 'paid_place_hit': None,
    }
    if result is None:
        output['settlement_reason'] = 'SELECTED_RESULT_MISSING'
        return output
    try:
        calculation = settlement(quote['win'], quote['place'], finish, terms)
    except (ValueError, KeyError, TypeError) as exc:
        output['settlement_reason'] = str(exc)
    else:
        output.update(calculation)
        output.update(settlement_status='CALCULABLE', settlement_reason='')
    return output


def evaluate(predictions, results, uploaded_terms_csv=None):
    """Return (all_races, side_summary, paired_summary, quote_audit,
    missing_terms_template, evidence_audit, base_exclusions).

    Internal statuses prevent partial odds coverage from accidentally being
    treated as a verified market favourite. Snapshot pre-off remains unverified.
    """
    evidence, evidence_log = _parse_override_records(uploaded_terms_csv)
    eligible, excluded = benchmark(predictions, results)
    if eligible.empty:
        empty = pd.DataFrame()
        return empty, empty, empty, empty, empty, pd.DataFrame(evidence_log), excluded

    batch_map = defaultdict(dict)
    for p in predictions:
        try:
            k = (race_key(p), str(p['prediction_batch_id']))
            h = horse_key(p.get('horse'))
            if h:
                batch_map[k][h] = p
        except (ValueError, KeyError, TypeError):
            continue
    result_map = defaultdict(dict)
    for r in results:
        try:
            h = horse_key(r.get('horse'))
            if h:
                result_map[race_key(r)][h] = r
        except (ValueError, TypeError, KeyError):
            continue

    rows, qaudit, needs = [], [], []
    for base in eligible.to_dict('records'):
        k = race_key(base)
        pid = str(base['prediction_batch_id'])
        pred = batch_map.get((k, pid), {})
        official = result_map.get(k, {})
        starters = {h: r for h, r in official.items() if r.get('scratched') is False}
        if not starters:
            continue
        field_size = len(starters)
        quoted = {}
        for h, result in starters.items():
            valid, audit = _quotes_for_runner(pred.get(h), field_size)
            quoted[h] = valid
            for rec in audit:
                qaudit.append({'race_date': base['race_date'], 'track': base['track'],
                               'race_number': base['race_number'], 'horse': result.get('horse'), **rec})
        m = horse_key(base['model_horse'])
        row = dict(base)
        row.update({'evaluation_type': 'SAVED_PRICES_NOT_VERIFIED_PRE_OFF',
                    'model_status': 'UNPRICED', 'model_reason': '',
                    'model_bookmaker': '', 'snapshot_market_status': 'NOT_AVAILABLE',
                    'snapshot_market_reason': '', 'snapshot_market_bookmaker': '',
                    'snapshot_market_horse': '', 'snapshot_market_tied': '',
                    'paired_status': 'NOT_PAIRED', 'paired_reason': ''})

        # Fairer comparison: require one *same bookmaker* with a quote for EVERY
        # actual starter, not just the model selection and eventual SP favourite.
        full_books = set.intersection(*(set(quoted[h]) for h in starters))
        chosen_full_book = min(full_books, key=preferred_key) if full_books else None
        # Fall back to a preferred model-only book if no whole-field snapshot exists.
        model_books = quoted.get(m, {})
        selected_model_book = chosen_full_book or (min(model_books, key=preferred_key) if model_books else None)
        if selected_model_book:
            mquote = model_books[selected_model_book]
            mout = _settle_selected(mquote, starters.get(m),
                    (*k, selected_model_book), evidence, field_size)
            row['model_bookmaker'] = selected_model_book
            for name, value in mout.items():
                row['model_'+name] = value
            row['model_status'] = mout['settlement_status']
            row['model_reason'] = mout['settlement_reason']
            if mout['bet_type'] == 'EACH_WAY' and mout['place_terms_provenance'] in (
                    'MISSING_BOOKMAKER_PLACES_PAID', 'INVALID_DOCUMENTED_TERMS_FOR_FIELD_SIZE'):
                needs.append({'race_date': base['race_date'], 'track': base['track'],
                              'race_number': base['race_number'], 'bookmaker': selected_model_book,
                              'places_paid': '', 'source_reference': '', 'source_type': '', 'notes':
                              f"Model {base['model_horse']} finished {mout['finish']}; saved place odds {mquote['place']}"})
        else:
            row['model_reason'] = 'NO_VALID_SAVED_MODEL_BOOKMAKER_QUOTES'

        if not chosen_full_book:
            row['snapshot_market_reason'] = 'NO_BOOKMAKER_PRICED_ALL_ACTUAL_STARTERS'
        else:
            row['snapshot_market_bookmaker'] = chosen_full_book
            lowest = min(quoted[h][chosen_full_book]['win'] for h in starters)
            favs = [h for h in starters if math.isclose(quoted[h][chosen_full_book]['win'], lowest, rel_tol=0, abs_tol=1e-9)]
            if len(favs) != 1:
                row['snapshot_market_reason'] = 'TIED_SNAPSHOT_MARKET_FAVOURITES'
                row['snapshot_market_tied'] = ';'.join(starters[h].get('horse', h) for h in favs)
            else:
                fh = favs[0]
                row['snapshot_market_status'] = 'PRICE_SELECTED'
                row['snapshot_market_horse'] = starters[fh].get('horse', '')
                fout = _settle_selected(quoted[fh][chosen_full_book], starters[fh],
                        (*k, chosen_full_book), evidence, field_size)
                for name, value in fout.items():
                    row['snapshot_market_'+name] = value
                row['snapshot_market_status'] = fout['settlement_status']
                row['snapshot_market_reason'] = fout['settlement_reason']
                if (fout['bet_type'] == 'EACH_WAY' and fout['place_terms_provenance'] in (
                        'MISSING_BOOKMAKER_PLACES_PAID', 'INVALID_DOCUMENTED_TERMS_FOR_FIELD_SIZE')
                        and fh != m):
                    needs.append({'race_date': base['race_date'], 'track': base['track'],
                                  'race_number': base['race_number'], 'bookmaker': chosen_full_book,
                                  'places_paid': '', 'source_reference': '', 'source_type': '',
                                  'notes': f"Saved-odds favourite {row['snapshot_market_horse']} finished {fout['finish']}"})
        if row['model_status'] == 'CALCULABLE' and row['snapshot_market_status'] == 'CALCULABLE':
            row['paired_status'] = 'CALCULABLE'
            row['paired_reason'] = ''
        else:
            row['paired_reason'] = 'MODEL:' + str(row['model_reason']) + ' | MARKET:' + str(row['snapshot_market_reason'])
        rows.append(row)

    all_df = pd.DataFrame(rows)
    side_sum = []
    pairs_sum = []
    if not all_df.empty:
        for prefix, title in [('model', 'Model top actual starter'), ('snapshot_market', 'Saved-odds market favourite')]:
            settled = all_df[all_df[prefix+'_status'] == 'CALCULABLE']
            if settled.empty:
                continue
            side_sum.append(_summary_row(settled, prefix, title, 'INDIVIDUAL_SIDE'))
        pair_df = all_df[all_df['paired_status'] == 'CALCULABLE']
        for prefix, title in [('model', 'Model top actual starter'), ('snapshot_market', 'Saved-odds market favourite')]:
            if not pair_df.empty:
                pairs_sum.append(_summary_row(pair_df, prefix, title, 'SAME_RACES_SAME_BOOKMAKER'))
    # Exact duplicates across the model and favourite are fine; only request one
    # documentary record per date/track/race/bookmaker.
    unique = {}
    for rec in needs:
        key = (rec['race_date'], track_key(rec['track']), int(rec['race_number']), rec['bookmaker'])
        unique.setdefault(key, rec)
    template = pd.DataFrame(list(unique.values()), columns=OVERRIDE_COLS)
    return (all_df, pd.DataFrame(side_sum), pd.DataFrame(pairs_sum),
            pd.DataFrame(qaudit), template, pd.DataFrame(evidence_log), excluded)


def _summary_row(data, prefix, label, comparison_scope):
    stake = float(data[prefix+'_total_staked'].sum())
    payout = float(data[prefix+'_total_return'].sum())
    total = len(data)
    # No rounding in source data: export full precision and format in Streamlit only.
    return {'comparison_scope': comparison_scope, 'selection': label,
            'races': total, 'win_bets': int((data[prefix+'_bet_type'] == 'WIN').sum()),
            'each_way_bets': int((data[prefix+'_bet_type'] == 'EACH_WAY').sum()),
            'wins': int(data[prefix+'_win_hit'].sum()),
            'paid_place_legs': int(data[prefix+'_paid_place_hit'].sum()),
            'total_stake': stake, 'total_return': payout,
            'net_profit': payout - stake,
            'roi_pct': 100 * (payout - stake) / stake if stake else None,
            'user_documented_term_races': int((data.get(prefix+'_place_terms_provenance', pd.Series(index=data.index, dtype='object'))
                                              == 'USER_DOCUMENTED_NOT_INDEPENDENTLY_VERIFIED').sum()),
            'unverified_quote_time_races': int((data.get(prefix+'_quote_time_quality', pd.Series(index=data.index, dtype='object'))
                                                != 'QUOTE_TIMESTAMP_PRESENT').sum())}


def _download(name, table, label):
    st.download_button(label, data=table.to_csv(index=False).encode('utf-8-sig'),
                       mime='text/csv', file_name=name, key='v057g_dl_'+name)


def render(db_rows):
    st.subheader('🧾 v0.5.7g — Documented Each-Way Settlement & Saved-Odds Market Comparison')
    st.caption('Read-only, frozen v0.2. Place odds below $2 → $10 WIN; place odds $2 or above → '
               '$10 WIN + $10 PLACE. Each-way place payouts need documented bookmaker-specific places-paid terms.')
    st.warning('Saved quote timestamps and official race start times are not independently verified. '
               'A manual terms CSV is evidence supplied by you, not independently authenticated. '
               'This is retrospective research, not proof of a pre-off executable advantage.')
    with st.expander('Optional: upload documentary bookmaker places-paid terms for old races', expanded=False):
        st.write('Supply the **exact date, track, race number and bookmaker**. '
                 'The source_reference must identify supporting evidence (e.g. an archived race-specific '
                 'bookmaker terms page or a bet slip); never enter assumed 3-place rules.')
        sample = pd.DataFrame(columns=OVERRIDE_COLS)
        _download('v057g_blank_terms_template.csv', sample, 'Download blank evidence template')
        upload = st.file_uploader('Upload documented places-paid CSV (optional)', type=['csv'], key='v057g_terms')
    # Persist the latest completed read-only run. Streamlit download buttons can
    # trigger reruns, which otherwise hide every result before all files are saved.
    latest_evidence = upload.getvalue() if upload is not None else None
    import hashlib
    evidence_fingerprint = hashlib.sha256(latest_evidence or b'').hexdigest()
    if st.button('Run v0.5.7g research benchmark', key='v057g_run', type='primary'):
        try:
            with st.spinner('Analysing earliest saved prediction batches and actual finishing positions...'):
                preds = db_rows('race_predictions', order='predicted_at')
                results = db_rows('runner_results', order=None)
                output = evaluate(preds, results, latest_evidence)
            st.session_state['v057g_last_run'] = (evidence_fingerprint, output)
        except Exception as exc:
            st.error(f'v0.5.7g could not run; no records were modified: {exc}')
            st.session_state.pop('v057g_last_run', None)
            return
    stored = st.session_state.get('v057g_last_run')
    if stored is None:
        return
    if stored[0] != evidence_fingerprint:
        st.info('The terms file changed since the last run. Select Run again to refresh the evidence and returns.')
        return
    races, singles, paired, audit, missing, evidence, excluded = stored[1]
    if races.empty:
        st.info('No eligible matched races in saved history.')
    else:
        a, b, c, d = st.columns(4)
        a.metric('Matched races', len(races))
        b.metric('Model calculable', int((races['model_status'] == 'CALCULABLE').sum()))
        c.metric('Price-favourite calculable', int((races['snapshot_market_status'] == 'CALCULABLE').sum()))
        d.metric('Fair paired comparisons', int((races['paired_status'] == 'CALCULABLE').sum()))
        st.subheader('Model and market: individual coverage (NOT the same sample)')
        if singles.empty:
            st.info('No calculable model or market selections yet.')
        else:
            st.dataframe(singles, hide_index=True, use_container_width=True)
            _download('v057g_side_summary.csv', singles, 'Download model / market standalone summary')
        st.subheader('Market comparison — same races, same bookmaker')
        if paired.empty:
            st.info('No fully documented, same-bookmaker paired races. Do not compare standalone ROIs as a head-to-head result.')
        else:
            st.dataframe(paired, hide_index=True, use_container_width=True)
            _download('v057g_paired_summary.csv', paired, 'Download fair paired summary')
        st.subheader('Every race: finish, prices, bet type, documentary status and net return')
        st.dataframe(races, hide_index=True, use_container_width=True)
        _download('v057g_all_races.csv', races, 'Download all race diagnostics')
        _download('v057g_paired_races.csv', races[races['paired_status'] == 'CALCULABLE'],
                  'Download paired comparison races')
    if not missing.empty:
        st.subheader('Missing bookmaker-specific place terms — fill only when supported by evidence')
        st.dataframe(missing, hide_index=True, use_container_width=True)
        _download('v057g_missing_terms_template.csv', missing, 'Download prefilled place-terms evidence template')
    if not evidence.empty:
        st.subheader('Supplied historical evidence CSV audit')
        st.dataframe(evidence, hide_index=True, use_container_width=True)
        _download('v057g_evidence_audit.csv', evidence, 'Download evidence audit')
    if not audit.empty:
        with st.expander('Quote-level audit'):
            st.dataframe(audit, hide_index=True, use_container_width=True)
            _download('v057g_quote_audit.csv', audit, 'Download quote-level audit')
    if not excluded.empty:
        _download('v057g_base_exclusions.csv', excluded, 'Download base race exclusions')
    st.caption('Never modifies prediction scores, bet settlements, prior quotes, Supabase records or historical results.')
