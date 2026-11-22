"""Manual official full-field results capture — v0.4.2.2.

No provider-final bypass: requires independently checked *final* official result.
Never edits predictions, existing results, or abandonment records.
"""
from collections import Counter
from decimal import Decimal, InvalidOperation
from datetime import date, datetime, timezone, timedelta
import re
import unicodedata
from urllib.parse import urlparse

import requests

STATUSES = ('UNSET', 'FINISHED', 'DNF', 'F', 'LR', 'PU', 'SCRATCHED')
NON_FINISH = frozenset(('DNF', 'F', 'LR', 'PU'))


def canonical(value):
    value = unicodedata.normalize('NFKD', str(value or ''))
    return re.sub(r'[^a-z0-9]+', '', value.encode('ascii', 'ignore').decode().casefold())


def validated_url(url):
    url = str(url or '').strip()
    parsed = urlparse(url)
    return bool(parsed.scheme == 'https' and parsed.netloc and '.' in parsed.netloc and not parsed.username and not parsed.password)


def earliest_batch(rows):
    if not rows:
        raise ValueError('No saved predictions for this race.')
    # Deterministic selection: batch clock, then batch UUID. No arbitrary reassignment.
    batches = {}
    for p in rows:
        batches.setdefault(str(p.get('prediction_batch_id')), []).append(p)
    chosen_id = min(batches, key=lambda b: (min(str(p.get('predicted_at') or '') for p in batches[b]), b))
    chosen = batches[chosen_id]
    keys = [canonical(r.get('horse')) for r in chosen]
    if not all(keys) or len(set(keys)) != len(keys):
        raise ValueError('Original prediction batch has missing/duplicate horse identities. Manual review required; no automatic repair.')
    return chosen_id, chosen


def parse_margin(raw, horse):
    """Accept official margin in LENGTHS only; never infer from race time or gap to previous finisher."""
    if raw is None or (not isinstance(raw, str) and str(raw).strip() in ('nan', '<NA>', 'None')) or (isinstance(raw, str) and raw.strip() in ('', '<NA>')):
        return None
    try:
        margin = Decimal(str(raw).strip())
    except (InvalidOperation, ValueError):
        raise ValueError(f'{horse}: margin must be a numeric length value (e.g. 1.25).')
    if not margin.is_finite() or margin < 0 or margin > 500:
        raise ValueError(f'{horse}: margin must be a finite number between 0 and 500 lengths.')
    if margin.as_tuple().exponent < -3:
        raise ValueError(f'{horse}: margin supports at most 3 decimal places (lengths).')
    return margin


def validate_entries(entries, *, official_url, reviewer, verification_note, entire_field_verified, official_final_verified, dead_heat_verified=False, source_name="Sportsbet"):
    """Validate entire official declared field. Return rows ready for runner_results.

    Explicit finishing position for FINISHED, no position for DNF/F/LR/PU/SCRATCHED.
    Official source verification is an operator attestation, never asserted by provider.
    """
    if not official_final_verified:
        raise ValueError('Confirm the external OFFICIAL result is FINAL; a provisional API result is not sufficient.')
    if not entire_field_verified:
        raise ValueError('Confirm EVERY declared runner and scratch against the complete official field.')
    if str(official_url or '').strip() and not validated_url(official_url):
        raise ValueError('If provided, the evidence URL must be a valid HTTPS URL.')
    if source_name not in ('Sportsbet', 'Official racing authority', 'Other bookmaker', 'Other verified source'):
        raise ValueError('Select the source used to check the result.')
    if len(str(reviewer or '').strip()) < 2:
        raise ValueError('Reviewer name or identifier is required.')
    if len(str(verification_note or '').strip()) < 8:
        raise ValueError('Provide a verification note (at least 8 characters) explaining the source/classification.')
    if not entries or len(entries) > 60:
        raise ValueError('Expected a complete field of 1–60 declared runners.')
    normalized, seen, position_rows = [], set(), []
    for index, raw in enumerate(entries, start=1):
        horse = str(raw.get('Horse') or '').strip()
        key = canonical(horse)
        if not key or key in seen:
            raise ValueError(f'Row {index}: missing or duplicate horse name: {horse!r}.')
        seen.add(key)
        status = str(raw.get('Status') or 'UNSET').strip().upper()
        if status not in STATUSES or status == 'UNSET':
            raise ValueError(f'{horse}: select FINISHED, DNF, F, LR, PU, or SCRATCHED.')
        pos = raw.get('Position')
        if pos is None or str(pos).strip() in ('', '<NA>', 'nan', 'None'):
            pos = None
        else:
            try:
                number = float(pos)
            except (ValueError, TypeError):
                raise ValueError(f'{horse}: invalid finishing position.')
            if not number.is_integer() or not 1 <= number <= 60:
                raise ValueError(f'{horse}: finishing position must be a positive whole number (1–60).')
            pos = int(number)
        if status == 'FINISHED' and pos is None:
            raise ValueError(f'{horse}: FINISHED needs a numerical position.')
        if status != 'FINISHED' and pos is not None:
            raise ValueError(f'{horse}: {status} must have a BLANK finishing position.')
        margin = parse_margin(raw.get('Margin (lengths)'), horse)
        # "Winning" margin means distance ahead of second place, for the winner only.
        # "Beaten" margin means distance behind the WINNER, not behind previous finisher.
        # Non-finishers and scratches have no legitimate numeric finishing margin.
        if status != 'FINISHED' and margin is not None:
            raise ValueError(f'{horse}: {status} must have a BLANK margin; do not fabricate a finishing distance.')
        if pos is not None:
            position_rows.append(pos)
        margin_type = ('WINNING' if pos == 1 else 'BEATEN') if margin is not None else None
        normalized.append({'horse':horse, 'horse_key':key,
                           'finishing_position':pos, 'finish_code': None if status == 'FINISHED' else status,
                           'scratched':status == 'SCRATCHED',
                           'margin_lengths':float(margin) if margin is not None else None,
                           'margin_type':margin_type,
                           # Keep legacy field populated consistently for downstream integrations.
                           'margin':format(margin, 'f') if margin is not None else None})
    winners = Counter(position_rows)[1]
    if winners == 2 and not dead_heat_verified:
        raise ValueError('Two first-place finishers require separate official dead-heat confirmation.')
    if winners not in (1, 2):
        raise ValueError(f'Expected one winner or verified two-way first-place dead heat; found {winners}.')
    for pos, count in Counter(position_rows).items():
        if count > 1 and not (pos == 1 and count == 2 and dead_heat_verified):
            raise ValueError(f'Duplicate finishing position {pos}; review official dead heats/placings.')
    if winners == 2:
        for row in normalized:
            if row['finishing_position'] == 1 and row['margin_lengths'] not in (None, 0):
                raise ValueError('Dead-heat joint winners cannot have a positive winning margin.')
    else:
        winner = next(r for r in normalized if r['finishing_position'] == 1)
        second = next((r for r in normalized if r['finishing_position'] == 2), None)
        # Same race distance expressed from opposing sides must agree if both known.
        if (second is not None and winner['margin_lengths'] is not None
                and second['margin_lengths'] is not None
                and abs(winner['margin_lengths'] - second['margin_lengths']) > 0.00001):
            raise ValueError('Winner margin and second-place beaten margin disagree; verify official distance.')
    return normalized


def available_unimported_races(predictions, results, abandoned, *, asof=None):
    """Only show locked races with NO saved runner results and not abandoned.

    Race identities are deduplicated; no edits to existing prediction batches.
    """
    asof = asof or date.today()
    cutoff = asof - timedelta(days=45)
    completed = {(str(r['race_date'])[:10], canonical(r['track']), int(r['race_number']))
                 for r in results}
    abandoned_keys = {(str(r['race_date'])[:10], canonical(r.get('track_key') or r.get('track')),
                       int(r['race_number'])) for r in abandoned}
    output = set()
    for r in predictions:
        day = str(r['race_date'])[:10]
        if not cutoff <= date.fromisoformat(day) <= asof:
            continue
        identity = (day, canonical(r['track']), int(r['race_number']))
        if identity not in completed and identity not in abandoned_keys:
            output.add((day, str(r['track']), int(r['race_number'])))
    return sorted(output, reverse=True)


def map_provider_rows(predicted, payload):
    """Read-only prefill; never infer a non-finish from a missing position.

    Provider margins are beaten-distance conventions (winner often 0).
    Transfer 2nd-place beaten margin to winning margin only when valid.
    Everything shown remains user-reviewable even if provider reports FINAL.
    """
    base = {canonical(p['horse']): {'Horse':p['horse'], 'Status':'UNSET',
                                    'Position':None, 'Margin (lengths)':None}
            for p in predicted}
    if not isinstance(payload, dict) or not isinstance(payload.get('placings'), list):
        return list(base.values()), {'matched':0, 'extra':0, 'unresolved':len(base), 'warnings':['No placings list from provider']}
    placings = payload['placings']
    seen = set()
    warning=[]; matched=0; extra=0
    valid_second_margin = None
    for r in placings:
        if not isinstance(r,dict):
            warning.append('Invalid provider row ignored'); continue
        if r.get('scratched') is True: continue
        if str(r.get('position') or '').strip() == '2':
            try:
                valid_second_margin = parse_margin(r.get('margin'), str(r.get('name') or 'Second runner'))
            except ValueError:
                warning.append('Second place margin not safely parseable; winner margin left blank')
            break
    for r in placings:
        if not isinstance(r,dict): continue
        horse=str(r.get('name') or '').strip()
        key=canonical(horse)
        if not key or key in seen:
            warning.append('Duplicate or unidentified provider runner skipped; manual review required')
            continue
        seen.add(key)
        row=base.get(key)
        if row is None:
            extra+=1
            row={'Horse':horse,'Status':'UNSET','Position':None,'Margin (lengths)':None}
            base[key]=row
        else:
            matched+=1
        scratched=r.get('scratched') is True
        raw_position=r.get('position')
        position=None
        try:
            if raw_position is not None and str(raw_position).strip():
                value=int(raw_position)
                if 1<=value<=60 and str(raw_position).strip()==str(value):position=value
        except (TypeError, ValueError):
            pass
        if scratched and position is None:
            row['Status']='SCRATCHED'
        elif position is not None and not scratched:
            row['Status']='FINISHED'
            row['Position']=position
            try:
                margin = valid_second_margin if position == 1 else parse_margin(r.get('margin'),horse)
                row['Margin (lengths)'] = float(margin) if margin is not None else None
            except ValueError:
                warning.append(f'{horse}: provider margin not numeric; left blank')
        # Other/non-numeric values (including null) intentionally remain UNSET.
    unresolved=sum(row['Status']=='UNSET' for row in base.values())
    return list(base.values()), {'matched':matched,'extra':extra,'unresolved':unresolved,'warnings':warning}


def fetch_formfav_preview(api_key, race, *, http_get=None):
    """Read-only fetch. A provisional response is a draft, never authority to save."""
    http_get = http_get or requests.get
    response = http_get('https://api.formfav.com/v1/results',
                        headers={'X-API-Key':str(api_key)},
                        params={'date':race[0],'track':race[1],'race':race[2],
                                'race_code':'gallops','country':'au'},timeout=30)
    response.raise_for_status()
    payload=response.json()
    if not isinstance(payload,dict):raise ValueError('FormFav returned invalid results payload')
    if (str(payload.get('date'))[:10] != race[0] or canonical(payload.get('track')) != canonical(race[1])
        or str(payload.get('raceNumber')) != str(race[2])):
        raise ValueError('FormFav race identity mismatch; prefill blocked.')
    if payload.get('abandoned') is True:
        raise ValueError('Provider reports race abandoned; manual results blocked pending review.')
    return payload


def render(db, db_rows, secret=None):
    import pandas as pd
    import streamlit as st

    with st.expander('📝 v0.4.2.2 — Manual results + margins (unimported races only)', expanded=False):
        st.caption('Only races without saved runner results. Prefill from FormFav where known; verify or correct against your results source. Missing positions are not automatically classed as LR/F/DNF.')
        try:
            all_races=db_rows('race_predictions', columns='race_date,track,race_number',order='predicted_at')
            saved_results=db_rows('runner_results',columns='race_date,track,race_number',order=None)
            abandoned=db_rows('abandoned_races',columns='race_date,track_key,race_number',order=None)
            races=available_unimported_races(all_races,saved_results,abandoned)
            if not races:
                st.success('No unimported saved races in the last 45 days.')
                return
            race=st.selectbox('Unimported race',races,
                format_func=lambda r:f'{r[0]} — {r[1]} — R{r[2]}',key='v0422_race')
            if st.button('Fetch FormFav positions + load remaining runners (READ ONLY)',key='v0422_load'):
                st.session_state.pop('v0422_load_data',None)
                response=(db().table('race_predictions').select('horse,model_rank,predicted_at,prediction_batch_id')
                          .eq('race_date',race[0]).eq('track',race[1]).eq('race_number',race[2])
                          .order('predicted_at').order('id').limit(200).execute())
                saved=response.data or []
                if len(saved)==200: raise ValueError('Race has >=200 predictions; manual review required.')
                batch_id,chosen=earliest_batch(saved)
                if (db().table('runner_results').select('id').eq('race_date',race[0])
                      .eq('track',race[1]).eq('race_number',race[2]).limit(1).execute()).data:
                    raise ValueError('Results already saved for this race; manual overwrite is blocked.')
                if (db().table('abandoned_races').select('id').eq('race_date',race[0])
                      .eq('track_key',race[1].strip().casefold()).eq('race_number',race[2]).limit(1).execute()).data:
                    raise ValueError('Race is marked abandoned; manual results blocked.')
                chosen=sorted(chosen,key=lambda p:(p.get('model_rank') or 999,p['horse']))
                rows=[{'Horse':p['horse'],'Status':'UNSET','Position':None,'Margin (lengths)':None} for p in chosen]
                provider_meta={'resultStatus':'not fetched','unresolved':len(rows),'matched':0,'extra':0,'warnings':[]}
                if secret:
                    key=secret('FORMFAV_API_KEY')
                    if key:
                        try:
                            payload=fetch_formfav_preview(key,race)
                            rows,stats=map_provider_rows(chosen,payload)
                            provider_meta={'resultStatus':payload.get('resultStatus','unknown'),**stats}
                        except (requests.RequestException,ValueError,TypeError) as exc:
                            provider_meta['warnings'].append(f'FormFav prefill unavailable: {exc}. Manual entry remains possible.')
                    else: provider_meta['warnings'].append('FORMFAV_API_KEY unavailable; manually enter results.')
                else: provider_meta['warnings'].append('FormFav access not configured in app; manually enter results.')
                st.session_state['v0422_load_data']={'race':race,'batch':batch_id,'rows':rows,
                                                    'locked_horses':[p['horse'] for p in chosen],
                                                    'provider':provider_meta}
                st.session_state['v0422_nonce']=st.session_state.get('v0422_nonce',0)+1
            loaded=st.session_state.get('v0422_load_data')
            if not loaded or tuple(loaded['race'])!=tuple(race):return
            meta=loaded['provider']
            st.info(f'FormFav status: {meta["resultStatus"]}. Matched: {meta["matched"]}; additional provider runners: {meta["extra"]}; unresolved: {meta["unresolved"]}. All imported values need verification.')
            for msg in meta['warnings']:st.warning(msg)
            st.caption(f'Original prediction batch: {loaded["batch"]}. Add any missing runners/scratches to represent the full field.')
            with st.form('v0422_entry_form'):
                edited=st.data_editor(pd.DataFrame(loaded['rows']),num_rows='dynamic',column_config={
                    'Horse':st.column_config.TextColumn('Horse',required=True),
                    'Status':st.column_config.SelectboxColumn('Status',options=list(STATUSES),required=True),
                    'Position':st.column_config.NumberColumn('Finish position',min_value=1,max_value=60,step=1,format='%d'),
                    'Margin (lengths)':st.column_config.NumberColumn('Margin (lengths)',min_value=0.0,max_value=500.0,step=0.1,format='%.3f')},
                    hide_index=True,use_container_width=True,key=f'v0422_editor_{st.session_state["v0422_nonce"]}')
                st.caption('Winner margin = lengths ahead of 2nd; others = lengths behind winner. Provider prefill is provisional until you verify it. Blank for non-finishers and scratches.')
                source=st.selectbox('Where did you verify the result?',
                    ['Sportsbet','Official racing authority','Other bookmaker','Other verified source'])
                url=st.text_input('Result URL (optional)',placeholder='Leave blank if checking in Sportsbet app')
                reviewer=st.text_input('Checked by (name or identifier)')
                note=st.text_area('Verification note (e.g. Sportsbet result, LR confirmed in stewards report)')
                final=st.checkbox('I checked that the result is settled/final with my selected source (not a live or provisional placing)')
                complete=st.checkbox('I checked every runner, position, scratch, non-finisher code and any entered margin')
                deadheat=st.checkbox('Official two-way DEAD HEAT for first confirmed (only if applicable)')
                save=st.form_submit_button('Validate and save reviewed results',type='primary')
            if save:
                parsed=validate_entries(edited.to_dict('records'),official_url=url,reviewer=reviewer,
                    verification_note=note,entire_field_verified=complete,official_final_verified=final,
                    dead_heat_verified=deadheat,source_name=source)
                predicted_keys={canonical(h) for h in loaded['locked_horses']}
                entered_keys={r['horse_key'] for r in parsed}
                if predicted_keys-entered_keys:
                    raise ValueError(f'Locked runners missing: {sorted(predicted_keys-entered_keys)}')
                if (db().table('runner_results').select('id').eq('race_date',race[0])
                      .eq('track',race[1]).eq('race_number',race[2]).limit(1).execute()).data:
                    raise ValueError('Race already has imported results. No overwrites allowed.')
                if (db().table('abandoned_races').select('id').eq('race_date',race[0])
                      .eq('track_key',race[1].strip().casefold()).eq('race_number',race[2]).limit(1).execute()).data:
                    raise ValueError('Race marked abandoned. Nothing saved.')
                now=datetime.now(timezone.utc).isoformat()
                for item in parsed:
                    item.update({'race_date':race[0],'track':race[1],'race_number':race[2],
                        'result_status':'final','source':'ManualResults:'+source,
                        'official_evidence_url':url.strip() or None,'verified_by':reviewer.strip(),
                        'verification_note':f'Source: {source}; {note.strip()}','verified_at':now})
                db().table('runner_results').insert(parsed).execute()
                st.success(f'Saved {len(parsed)} reviewed runner results. Original predictions unchanged.')
                st.session_state.pop('v0422_load_data',None)
        except Exception as exc:
            st.error(f'Manual result entry blocked: {exc}')
