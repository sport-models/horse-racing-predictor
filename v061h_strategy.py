"""v0.6.1h: prospective $5-per-leg top-1/2/3 strategy evaluation.

Only immutable PRE-SCHEDULE prospective snapshots; no historical SP substitution.
User rule: place odds <2 => $10 WIN only; place odds >=2 => $5 WIN + $5 PLACE.
No actual wagers are placed. Results are hypothetical, cash-only, and read-only.
"""
from __future__ import annotations
from collections import defaultdict
from hashlib import sha256
import json
from datetime import timezone
import pandas as pd
from v042_results import canonical, normalize_track
from v057f_odds_integrity import bookmaker_id, valid_price, valid_places, iso_timestamp, preferred_key


def bet_rule(win,place,places_paid,rank,book):
    if win is None or place is None:return None
    if place<2:
        return {'rank':rank,'bookmaker':book,'bet_type':'WIN_ONLY','win_odds':win,
                'place_odds':place,'places_paid':None,'win_stake':10.0,'place_stake':0.0,'stake':10.0}
    return {'rank':rank,'bookmaker':book,'bet_type':'EACH_WAY','win_odds':win,
            'place_odds':place,'places_paid':places_paid,'win_stake':5.0,'place_stake':5.0,'stake':10.0}


def best_quote(quotes, cutoff, field_size):
    choices=[]
    for q in quotes or []:
        if not isinstance(q,dict) or q.get('stale') is not False:continue
        book,error=bookmaker_id(q)
        if error:continue
        win,place=valid_price(q.get('win')),valid_price(q.get('place'))
        updated=iso_timestamp(q.get('last_update'))
        if win is None or place is None or updated is None or cutoff is None or updated>cutoff:continue
        paid=valid_places(q.get('places_paid'),field_size)
        if q.get('places_paid_source') not in ('bookmaker_quote',None):paid=None
        choices.append((preferred_key(book),{'bookmaker':book,'win':win,'place':place,'places_paid':paid,
                                             'last_update':updated.isoformat()}))
    # For an each-way choice, prefer an explicitly documented bookmaker place
    # limit when available; a preferred bookmaker with missing terms must not
    # hide a usable quote from another bookmaker.
    choices.sort(key=lambda x:(x[1]['place']>=2 and x[1]['places_paid'] is None,x[0]))
    return choices[0][1] if choices else None


def race_id(x):return (str(x.get('race_date'))[:10],normalize_track(x.get('track')),int(x.get('race_number')))

def _payload_valid(archive):
    supplied=archive.get('payload_sha256')
    if not supplied:return True  # Older tests/partial archive surfaces may omit proof; never certify.
    try:
        raw=json.dumps(archive.get('payload'),ensure_ascii=False,sort_keys=True,
                       separators=(',',':'),allow_nan=False).encode('utf-8')
        return sha256(raw).hexdigest()==str(supplied)
    except (TypeError,ValueError,OverflowError):return False

def build_tickets(archives):
    rows=[]
    for arc in archives:
        p=arc.get('payload') or {}
        if p.get('model_version')!='v0.2':continue
        times=p.get('source_times') or {}
        cutoff=min((x for x in (iso_timestamp(p.get('scheduled_start_utc')),
                                iso_timestamp(arc.get('created_at')),
                                iso_timestamp(times.get('odds_response_received_utc'))) if x is not None),default=None)
        pre = iso_timestamp(arc.get('created_at')) and iso_timestamp(p.get('scheduled_start_utc')) and iso_timestamp(arc.get('created_at'))<iso_timestamp(p.get('scheduled_start_utc'))
        hash_ok=_payload_valid(arc)
        rankings=p.get('rankings') or []
        quotes=p.get('odds_quotes') or {}
        field_size=len(rankings)
        for r in rankings:
            if not isinstance(r,dict) or r.get('rank') not in (1,2,3):continue
            horse=str(r.get('horse') or '')
            q=best_quote(quotes.get(horse),cutoff,field_size) if pre else None
            bet=bet_rule(q['win'],q['place'],q['places_paid'],r['rank'],q['bookmaker']) if q else None
            status=('ARCHIVE_HASH_MISMATCH' if not hash_ok else ('ELIGIBLE' if bet else 'MISSING_VERIFIABLY_TIMED_WIN_PLACE_QUOTES'))
            if status=='ELIGIBLE' and bet['bet_type']=='EACH_WAY' and bet['places_paid'] is None:
                status='MISSING_BOOKMAKER_PLACE_TERMS'
            rows.append({'batch_id':str(arc.get('batch_id')),'race_date':str(arc.get('race_date'))[:10],
                         'track':arc.get('track'),'race_number':arc.get('race_number'),
                         'horse':horse,'horse_key':canonical(horse),'rank':int(r['rank']),
                         'snapshot_ranked_field':field_size,
                         'status':status,'captured_before_schedule':bool(pre),
                         'quote_time':q.get('last_update') if q else None,
                         **(bet or {'bookmaker':None,'bet_type':None,'win_odds':None,'place_odds':None,
                                    'places_paid':None,'win_stake':None,'place_stake':None,'stake':None})})
    return pd.DataFrame(rows)

def grade(tickets,results):
    """No bookmaker payouts inferred for dead heats or boundary ties; skip uncertain settlement."""
    index=defaultdict(list)
    for r in results:
        try:index[race_id(r)].append(r)
        except (ValueError,TypeError):continue
    lines=[]
    for t in tickets.to_dict('records'):
        item=dict(t,settlement_status=t['status'],finishing_position=None,
                  total_return=None,profit=None,win_hit=None,place_hit=None)
        if t['status']!='ELIGIBLE':lines.append(item);continue
        race=index.get(race_id(t),[])
        if not race:
            item['settlement_status']='NO_IMPORTED_FINAL_RESULTS';lines.append(item);continue
        if any(r.get('result_status')!='final' for r in race):
            item['settlement_status']='UNVERIFIED_FINAL_RESULTS';lines.append(item);continue
        if len(race)<int(t['snapshot_ranked_field']):
            item['settlement_status']='INCOMPLETE_OFFICIAL_FIELD';lines.append(item);continue
        names=[canonical(r.get('horse') or r.get('horse_key')) for r in race]
        if len(names)!=len(set(names)) or any(not n for n in names):
            item['settlement_status']='AMBIGUOUS_RESULT_IDENTITIES';lines.append(item);continue
        matched=[r for r in race if canonical(r.get('horse') or r.get('horse_key'))==t['horse_key']]
        if len(matched)!=1:
            item['settlement_status']='SELECTED_HORSE_MISSING';lines.append(item);continue
        rr=matched[0]
        if rr.get('scratched') is True:
            item['settlement_status']='SCRATCHED_VOID_EXCLUDED';lines.append(item);continue
        try:pos=int(rr['finishing_position'])
        except (ValueError,TypeError):pos=0
        if pos<1 or float(rr['finishing_position'])!=pos:
            item['settlement_status']='INVALID_POSITION';lines.append(item);continue
        positions=[]
        for r in race:
            if r.get('scratched') is True:continue
            try:
                v=int(r['finishing_position'])
                if v<1 or float(r['finishing_position'])!=v:raise ValueError()
                positions.append(v)
            except (ValueError,TypeError):positions=[];break
        if not positions or 1 not in positions:
            item['settlement_status']='INVALID_RACE_POSITIONS';lines.append(item);continue
        if positions.count(1)>1:
            item['settlement_status']='DEAD_HEAT_SETTLEMENT_UNVERIFIED';lines.append(item);continue
        paid=t['places_paid']
        if t['bet_type']=='EACH_WAY' and (paid is None or pd.isna(paid)):
            item['settlement_status']='MISSING_BOOKMAKER_PLACE_TERMS';lines.append(item);continue
        if t['bet_type']=='EACH_WAY' and pos<=paid and positions.count(pos)>1 and pos==paid:
            item['settlement_status']='PLACE_BOUNDARY_DEAD_HEAT_UNVERIFIED';lines.append(item);continue
        win=pos==1
        place=bool(pos<=paid) if t['bet_type']=='EACH_WAY' else False
        payout=(float(t['win_stake'])*float(t['win_odds']) if win else 0.0)
        payout+=(float(t['place_stake'])*float(t['place_odds']) if place else 0.0)
        item.update(settlement_status='GRADED_RESEARCH_ONLY',finishing_position=pos,
                    total_return=payout,profit=payout-float(t['stake']),win_hit=win,place_hit=place)
        lines.append(item)
    return pd.DataFrame(lines)

def aggregate(graded):
    if graded.empty:return pd.DataFrame()
    reports=[]
    for depth in (1,2,3):
        group=graded.loc[graded['rank']<=depth].copy()
        # Portfolio only includes complete bet plans AND settlement evidence for ALL ranks in the race.
        complete=group.groupby('batch_id').agg(count=('rank','count'),evaluated=('settlement_status',lambda s:bool((s=='GRADED_RESEARCH_ONLY').all())))
        good=set(complete.index[(complete['count']==depth)&complete['evaluated']])
        group=group.loc[group['batch_id'].isin(good)]
        stake=float(group['stake'].sum()) if not group.empty else 0
        gain=float(group['profit'].sum()) if not group.empty else 0
        reports.append({'strategy':f'Top {depth}','fully_evaluable_races':len(good),
                        'selections':len(group),'win_legs_hit':int(group['win_hit'].sum()) if len(group) else 0,
                        'place_legs_hit':int(group['place_hit'].sum()) if len(group) else 0,
                        'stake_$':stake,'net_profit_$':gain if stake else None,
                        'roi_%':100*gain/stake if stake else None,
                        'race_winning_selection_%':100*group.groupby('batch_id')['win_hit'].any().mean() if len(group) else None})
    return pd.DataFrame(reports)

def matched_portfolios(graded):
    """Fair comparison: same races with all 3 ranked tickets evaluable."""
    if graded.empty:return pd.DataFrame()
    full=graded.loc[graded['rank'].isin([1,2,3])]
    ok=full.groupby('batch_id').agg(n=('rank','nunique'),
          all_graded=('settlement_status',lambda x:bool((x=='GRADED_RESEARCH_ONLY').all())))
    ids=set(ok.index[(ok['n']==3)&ok['all_graded']])
    sample=full.loc[full['batch_id'].isin(ids)]
    return aggregate(sample)

def aggregate_by_track(graded):
    if graded.empty:return pd.DataFrame()
    rows=[]
    for track,part in graded.groupby('track',dropna=False):
        temp=aggregate(part)
        if not temp.empty:
            temp.insert(0,'Track',track)
            rows.extend(temp.to_dict('records'))
    return pd.DataFrame(rows)

def render(secret):
    import streamlit as st
    import v061_prospective as v
    st.subheader('🎟️ v0.6.1h — Prospective Top 1 / 2 / 3 strategy lab')
    st.caption('Read-only hypothetical settlement of locked v0.2 snapshots. Place odds < $2 → $10 win-only; otherwise $5 win + $5 place. One bookmaker per horse, captured quote time and explicit bookmaker places-paid required. No bonus promotions simulated.')
    st.warning('Not actual settled Bet Tracker ROI. Captures before the scheduled time do not independently prove actual-off or input-as-of provenance.')
    if st.button('Evaluate saved Top 1 / 2 / 3 snapshots',key='v061h_strategy_run'):
        try:
            client=v.archive_client(secret)
            archives=v._read_all(client,v.TABLE,order='created_at')
            results=v._read_all(client,'runner_results')
            tickets=build_tickets(archives)
            output=grade(tickets,results) if not tickets.empty else pd.DataFrame()
            summary=aggregate(output)
            by_track=aggregate_by_track(output)
            matched=matched_portfolios(output)
            st.session_state['v061h_strategy_reports']={'tickets':tickets,'evaluations':output,'portfolio':summary,'matched_cohort':matched,'by_track':by_track}
        except Exception as exc:
            st.error('Prospective strategy audit stopped: '+str(exc));return
    reports=st.session_state.get('v061h_strategy_reports')
    if not reports:return
    if not reports['portfolio'].empty:
        st.markdown('**All available evaluable races (cohorts may differ)**')
        st.dataframe(reports['portfolio'],hide_index=True,use_container_width=True)
    if not reports['matched_cohort'].empty:
        st.markdown('**Matched races with all three selections evaluable**')
        st.dataframe(reports['matched_cohort'],hide_index=True,use_container_width=True)
    if not reports['evaluations'].empty:
        st.dataframe(reports['evaluations'].groupby('settlement_status',dropna=False).size().reset_index(name='selections'),hide_index=True,use_container_width=True)
    for name,frame in reports.items():
        st.download_button('Download prospective '+name+' CSV',frame.to_csv(index=False).encode('utf-8'),
                           file_name='v061h_prospective_'+name+'.csv',mime='text/csv',key='v061h_strategy_'+name)
