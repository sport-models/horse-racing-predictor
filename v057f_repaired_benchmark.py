"""v0.5.7f: read-only odds-recovery diagnostics and corrected conditional EW benchmark.

Strict about provenance: saved quote, earliest prediction batch, actual starter,
bookmaker-specific terms for EW. Never fetch today's odds or write Supabase rows.
"""
from __future__ import annotations

from collections import defaultdict
import pandas as pd
import streamlit as st

from v057c_corrected_benchmark import benchmark, horse_key, race_key
from v057f_odds_integrity import parse_saved_quote, preferred_key, settlement


def _parse_quotes(pred, snapshot_at, field_size, race_id, selection):
    raw = pred.get('bookmaker_quotes') or []
    audit = []
    accepted = {}
    if not isinstance(raw, list):
        raw = []
        invalid_container = True
    else:
        invalid_container = False
    for n, q in enumerate(raw):
        parsed, issue = parse_saved_quote(q, snapshot_at, field_size)
        qd = q if isinstance(q, dict) else {}
        book = parsed['bookmaker'] if parsed else str(qd.get('key') or qd.get('bookmaker') or '')
        if parsed and book in accepted:
            issue = 'DUPLICATE_BOOKMAKER_QUOTES'
            accepted.pop(book, None)  # ambiguous source: neither quote is safely usable
        elif parsed and book not in accepted:
            accepted[book] = parsed
        audit.append({
            'race': race_id, 'selection': selection, 'quote_index': n,
            'saved_bookmaker_field': qd.get('bookmaker'), 'saved_key_field': qd.get('key'),
            'resolved_bookmaker': book, 'win_odds': qd.get('win'), 'place_odds': qd.get('place'),
            'saved_places_paid': qd.get('places_paid'),
            'race_places_reference_NOT_BOOK_TERMS': qd.get('race_places_paid_reference'),
            'stale': qd.get('stale'), 'last_update': qd.get('last_update'),
            'quote_status': issue or ('VALID_WIN_ONLY_OR_EW_WITH_TERMS' if parsed['places_paid'] is not None
                                       else 'VALID_PRICES_MISSING_EW_TERMS'),
            'legacy_bookmaker_recovered': bool(parsed and parsed['legacy_bookmaker_field']),
        })
    # Hard-fail any later repeated book, even on third or fourth occurrence.
    duplicated = {r['resolved_bookmaker'] for r in audit if r['quote_status'] == 'DUPLICATE_BOOKMAKER_QUOTES'}
    for b in duplicated:
        accepted.pop(b, None)
    if invalid_container:
        audit.append({'race': race_id, 'selection': selection, 'quote_index': None,
                      'quote_status': 'MALFORMED_BOOKMAKER_QUOTES_CONTAINER'})
    if not raw:
        audit.append({'race': race_id, 'selection': selection, 'quote_index': None,
                      'quote_status': 'MALFORMED_BOOKMAKER_QUOTES_CONTAINER' if invalid_container else 'NO_SAVED_BOOKMAKER_QUOTES'})
    return accepted, audit


def _choose_common(model_quotes, fav_quotes):
    """Return first fixed-priority common bookmaker with enough settlement evidence."""
    books = sorted(set(model_quotes) & set(fav_quotes), key=preferred_key)
    if not books:
        return None, 'NO_SHARED_BOOKMAKER_WITH_VALID_SAVED_WIN_AND_PLACE_PRICES'
    failures = []
    for book in books:
        m, f = model_quotes[book], fav_quotes[book]
        if m['places_paid'] is not None and f['places_paid'] is not None and m['places_paid'] != f['places_paid']:
            failures.append(f'{book}:CONFLICTING_BOOKMAKER_PLACES_PAID')
            continue
        if m['place'] >= 2.0 and m['places_paid'] is None:
            failures.append(f'{book}:MODEL_MISSING_EXPLICIT_PLACES_PAID')
            continue
        if f['place'] >= 2.0 and f['places_paid'] is None:
            failures.append(f'{book}:FAVOURITE_MISSING_EXPLICIT_PLACES_PAID')
            continue
        return (book, m, f), ''
    return None, ';'.join(failures)


def evaluate(predictions, results):
    """Return (race rows, paired strategy rows, quote audit, summary, exclusions)."""
    eligible, excluded = benchmark(predictions, results)
    if eligible.empty:
        return eligible, pd.DataFrame(), pd.DataFrame(), pd.DataFrame(), excluded
    preds = defaultdict(dict)
    for p in predictions:
        try:
            preds[(race_key(p), str(p['prediction_batch_id']))][horse_key(p['horse'])] = p
        except (KeyError, TypeError, ValueError):
            pass
    result_map = defaultdict(dict)
    for r in results:
        try:
            result_map[race_key(r)][horse_key(r['horse'])] = r
        except (KeyError, TypeError, ValueError):
            pass
    rows, paired, audits = [], [], []
    for base in eligible.to_dict('records'):
        label = f"{base['race_date']} {base['track']} R{base['race_number']}"
        key = race_key(base)
        p = preds.get((key, str(base['prediction_batch_id'])), {})
        r = result_map.get(key, {})
        m_key, f_key = horse_key(base['model_horse']), horse_key(base['favourite_horse'])
        m_r, f_r = r.get(m_key), r.get(f_key)
        m_p, f_p = p.get(m_key), p.get(f_key)
        row = dict(base)
        row.update({'pricing_status': 'NOT_PRICED', 'pricing_reason': '',
                    'model_saved_quote_count': len((m_p or {}).get('bookmaker_quotes') or []),
                    'favourite_saved_quote_count': len((f_p or {}).get('bookmaker_quotes') or [])})
        if not all((m_r, f_r, m_p, f_p)):
            row['pricing_reason'] = 'INTERNAL_SELECTED_RUNNER_LOOKUP_FAILED'
            rows.append(row)
            continue
        model_quotes, ma = _parse_quotes(m_p, base['prediction_at_utc'], int(base['field_size']), label, 'MODEL')
        fav_quotes, fa = _parse_quotes(f_p, base['prediction_at_utc'], int(base['field_size']), label, 'SP_FAVOURITE')
        audits.extend(ma)
        audits.extend(fa)
        row['model_valid_price_books'] = ';'.join(sorted(model_quotes))
        row['favourite_valid_price_books'] = ';'.join(sorted(fav_quotes))
        row['shared_valid_price_books'] = ';'.join(sorted(set(model_quotes) & set(fav_quotes)))
        row['legacy_bookmaker_quotes_recovered'] = sum(bool(q.get('legacy_bookmaker_recovered')) for q in ma+fa)
        missing_quotes = []
        if not (m_p.get('bookmaker_quotes') or []):
            missing_quotes.append('MODEL:NO_SAVED_BOOKMAKER_QUOTES')
        if not (f_p.get('bookmaker_quotes') or []):
            missing_quotes.append('FAVOURITE:NO_SAVED_BOOKMAKER_QUOTES')
        if missing_quotes:
            row['pricing_reason'] = ' | '.join(missing_quotes)
        else:
            selected, reason = _choose_common(model_quotes, fav_quotes)
            if selected is None:
                row['pricing_reason'] = reason
            else:
                book, m_quote, f_quote = selected
                try:
                    m_out = settlement(m_quote['win'], m_quote['place'], m_r['finishing_position'], m_quote['places_paid'])
                    f_out = settlement(f_quote['win'], f_quote['place'], f_r['finishing_position'], f_quote['places_paid'])
                except (ValueError, TypeError, KeyError) as exc:
                    row['pricing_reason'] = f'SETTLEMENT_INPUT_FAILURE:{exc}'
                else:
                    row.update({'pricing_status': 'PRICED_SAVED_QUOTES', 'pricing_reason': '',
                                'pricing_bookmaker': book,
                                'price_time_verification': ('BOTH_QUOTES_HAVE_TIMESTAMPS'
                                    if m_quote['quote_time_quality'] == f_quote['quote_time_quality'] == 'QUOTE_TIMESTAMP_PRESENT'
                                    else 'ONE_OR_BOTH_QUOTE_TIMES_UNVERIFIED'),
                                'model_finish': int(m_r['finishing_position']),
                                'favourite_finish': int(f_r['finishing_position'])})
                    for prefix, quote, outcome in [('model', m_quote, m_out), ('favourite', f_quote, f_out)]:
                        row[f'{prefix}_quoted_win_odds'] = quote['win']
                        row[f'{prefix}_quoted_place_odds'] = quote['place']
                        row[f'{prefix}_bookmaker_places_paid'] = quote['places_paid']
                        row[f'{prefix}_quote_updated_at_utc'] = quote['quote_updated_at_utc']
                        for k, v in outcome.items():
                            row[f'{prefix}_{k}'] = v
                    paired.append(dict(row))
        rows.append(row)
    all_df = pd.DataFrame(rows)
    paired_df = pd.DataFrame(paired)
    audit_df = pd.DataFrame(audits)
    summaries = []
    if not paired_df.empty:
        for side, name in [('model', 'Model top active'), ('favourite', 'Retrospective SP favourite')]:
            stake = float(paired_df[f'{side}_total_staked'].sum())
            payout = float(paired_df[f'{side}_total_return'].sum())
            n = len(paired_df)
            summaries.append({'selection': name, 'matched_races': n,
                              'win_only_bets': int((paired_df[f'{side}_bet_type'] == 'WIN').sum()),
                              'each_way_bets': int((paired_df[f'{side}_bet_type'] == 'EACH_WAY').sum()),
                              'wins': int(paired_df[f'{side}_win_hit'].sum()),
                              'paid_ew_placings': int(paired_df[f'{side}_paid_place_hit'].sum()),
                              'total_staked': stake, 'total_return': payout,
                              'net_profit': payout-stake,
                              'roi_pct': 100*(payout-stake)/stake if stake else None})
    return all_df, paired_df, audit_df, pd.DataFrame(summaries), excluded


def render(db_rows):
    st.subheader('🔧 v0.5.7f — Repaired Odds Integration & Strategy Audit')
    st.caption('Audits saved quotes, reads legacy bookmaker identifiers, and calculates your rule: '
               'place < $2 = $10 WIN; place ≥ $2 = $10 WIN + $10 PLACE. Read-only.')
    st.warning('No historical prices or bookmaker place terms are invented. WIN-only bets do not need '
               'places-paid terms; EACH-WAY bets do. Saved quotes may not have timestamp verification. '
               'The SP favourite is chosen retrospectively, and historical model features/pre-off availability remain unverified.')
    if not st.button('Run v0.5.7f corrected odds & strategy benchmark', key='v057f_run'):
        return
    try:
        with st.spinner('Checking legacy quotes and recalculating matched-pair strategy returns...'):
            predictions = db_rows('race_predictions', order='predicted_at')
            results = db_rows('runner_results', order=None)
            races, priced, audit, summary, excluded = evaluate(predictions, results)
    except Exception as exc:
        st.error(f'v0.5.7f evaluation failed: {exc}')
        return
    a, b, c = st.columns(3)
    a.metric('Eligible model races', len(races))
    b.metric('Both selections priced', len(priced))
    c.metric('Unpriced / missing evidence', int((races['pricing_status'] == 'NOT_PRICED').sum()) if not races.empty else 0)
    if not priced.empty:
        st.write('**Matched-race conditional win/each-way performance**')
        st.dataframe(summary, hide_index=True, use_container_width=True)
        st.dataframe(priced, hide_index=True, use_container_width=True)
        st.download_button('Download v0.5.7f strategy races', priced.to_csv(index=False).encode(),
                           file_name='v057f_strategy_races.csv', mime='text/csv', key='v057f_priced_csv')
        st.download_button('Download v0.5.7f strategy summary', summary.to_csv(index=False).encode(),
                           file_name='v057f_strategy_summary.csv', mime='text/csv', key='v057f_sum_csv')
    else:
        st.info('No fully supported matched-pair strategy returns yet. The diagnostics below show exactly '
                'which prices or bookmaker-specific place terms are missing. No $0 payouts are imputed.')
    if not races.empty:
        st.write('**Race-by-race evidence and eligibility**')
        st.dataframe(races, hide_index=True, use_container_width=True)
        st.download_button('Download v0.5.7f all race diagnostics', races.to_csv(index=False).encode(),
                           file_name='v057f_all_races.csv', mime='text/csv', key='v057f_races_csv')
    if not audit.empty:
        st.write('**Saved bookmaker quote audit (raw legacy identifiers & missing terms)**')
        st.dataframe(audit, hide_index=True, use_container_width=True)
        st.download_button('Download v0.5.7f bookmaker quote audit', audit.to_csv(index=False).encode(),
                           file_name='v057f_quote_audit.csv', mime='text/csv', key='v057f_quotes_csv')
    if not excluded.empty:
        st.download_button('Download prior race-eligibility exclusions', excluded.to_csv(index=False).encode(),
                           file_name='v057f_base_exclusions.csv', mime='text/csv', key='v057f_exclusions_csv')
    st.caption('Historical snapshots are never overwritten. New snapshots can capture explicit per-book '
               'places_paid when PuntersEdge supplies it; race-level places_paid is only audit context.')
