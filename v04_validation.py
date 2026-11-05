"""Prospective, as-of-aware validation of locked racing predictions.
No external requests and no database writes.
"""
import pandas as pd
from zoneinfo import ZoneInfo
import streamlit as st


def render(db_rows):
    st.subheader('🧪 v0.4 — Model Validation')
    st.caption('Forward monitoring of saved predictions. Form scores are not calibrated win probabilities.')
    try:
        predictions = db_rows('race_predictions', order='predicted_at')
        results = db_rows('race_results', order='recorded_at')
        bets = db_rows('bets', order='placed_at')
    except Exception as exc:
        st.error(f'Database read failed: {exc}')
        return
    if not predictions:
        st.info('No locked predictions yet. Lock races before they start to begin monitoring.')
        return
    p = pd.DataFrame(predictions)
    needed = {'race_date', 'track', 'race_number', 'horse', 'model_rank', 'prediction_batch_id', 'predicted_at'}
    if not needed.issubset(p.columns):
        st.error(f'Missing prediction columns: {sorted(needed-set(p.columns))}')
        return
    p['lock_utc'] = pd.to_datetime(p['predicted_at'], errors='coerce', utc=True)
    p['race_day'] = pd.to_datetime(p['race_date'], errors='coerce').dt.date
    # Australian local lock date is still insufficient to prove same-day pre-jump timing.
    p['lock_day_utc'] = p['lock_utc'].dt.tz_convert(ZoneInfo('Australia/Melbourne')).dt.date
    p['timing'] = 'UNKNOWN'
    p.loc[p['lock_day_utc'] < p['race_day'], 'timing'] = 'PRIOR_DAY'
    p.loc[p['lock_day_utc'] == p['race_day'], 'timing'] = 'SAME_DAY_UNVERIFIED'
    p.loc[p['lock_day_utc'] > p['race_day'], 'timing'] = 'AFTER_RACE_DAY_EXCLUDE'
    p['race_key'] = p['race_date'].astype(str) + '|' + p['track'].astype(str).str.casefold().str.strip() + '|' + p['race_number'].astype(str)
    p['horse_key'] = p['horse'].astype(str).str.casefold().str.strip()
    p['batch_key'] = p['race_key'] + '|' + p['prediction_batch_id'].astype(str)
    batches = p.groupby('batch_key', as_index=False).agg(race_key=('race_key', 'first'), first_lock=('lock_utc', 'min'), timing=('timing', 'first'), runners=('horse', 'count'))
    first = batches.sort_values('first_lock').drop_duplicates('race_key', keep='first')
    eligible = first[first['timing'] != 'AFTER_RACE_DAY_EXCLUDE']
    monitored = p[p['batch_key'].isin(set(eligible['batch_key']))].copy()
    if results:
        r = pd.DataFrame(results)
        required = {'race_date', 'track', 'race_number', 'horse', 'finishing_position', 'scratched'}
        if required.issubset(r.columns):
            r['race_key'] = r['race_date'].astype(str) + '|' + r['track'].astype(str).str.casefold().str.strip() + '|' + r['race_number'].astype(str)
            r['horse_key'] = r['horse'].astype(str).str.casefold().str.strip()
            r = r.sort_values('recorded_at' if 'recorded_at' in r else 'race_date').drop_duplicates(['race_key', 'horse_key'], keep='last')
            monitored = monitored.merge(r[['race_key', 'horse_key', 'finishing_position', 'scratched']], on=['race_key', 'horse_key'], how='left', validate='many_to_one')
        else:
            st.warning('Results schema incomplete; cannot match recorded outcomes.')
    if 'finishing_position' not in monitored:
        monitored['finishing_position'] = None
    if 'scratched' not in monitored:
        monitored['scratched'] = False
    monitored['finish'] = pd.to_numeric(monitored['finishing_position'], errors='coerce')
    monitored['known'] = monitored['finish'].gt(0) | monitored['scratched'].fillna(False).eq(True)
    a, b, c = st.columns(3)
    a.metric('Eligible locked races', len(eligible))
    b.metric('Runners with recorded outcomes', int(monitored['known'].sum()))
    c.metric('Runners awaiting outcomes', int((~monitored['known']).sum()))
    same_day = int((first['timing'] == 'SAME_DAY_UNVERIFIED').sum())
    st.warning(f'{same_day} race(s) first locked on race day: without start-time evidence, pre-jump timing cannot be proven. Results are provisional.')
    top = monitored[pd.to_numeric(monitored['model_rank'], errors='coerce').eq(1)]
    top_known = top[top['finish'].notna()]
    st.subheader('Top-ranked selections — observed results only')
    if not top_known.empty:
        wins = int(top_known['finish'].eq(1).sum())
        st.metric('Observed top-ranked winners', f'{wins} / {len(top_known)}')
        st.caption('Incomplete-result denominator: do not interpret as unbiased strike rate until all top picks have results.')
    else:
        st.info('No recorded finishing positions for top-ranked horses yet.')
    st.subheader('Settled bet ROI — real ledger')
    settled = pd.DataFrame([x for x in bets if x.get('status') == 'Settled'])
    if not settled.empty:
        settled['stake'] = pd.to_numeric(settled['total_stake'], errors='coerce').fillna(0)
        settled['return'] = pd.to_numeric(settled['total_return'], errors='coerce').fillna(0)
        group = settled.groupby('bet_type', dropna=False).agg(Bets=('id', 'count'), Stake=('stake', 'sum'), Return=('return', 'sum')).reset_index()
        group['P/L'] = group['Return'] - group['Stake']
        group['ROI %'] = group.apply(lambda x: 100*x['P/L']/x['Stake'] if x['Stake'] else float('nan'), axis=1)
        st.dataframe(group, hide_index=True, use_container_width=True)
        st.caption('Pending and void bets excluded. Bet ROI is not model-wide profitability.')
    else:
        st.info('No settled bets yet.')
    st.subheader('Lock audit')
    st.dataframe(first[['race_key', 'first_lock', 'timing', 'runners']], hide_index=True, use_container_width=True)
    st.download_button('Download validation dataset (CSV)', monitored.drop(columns=['lock_utc', 'race_day', 'lock_day_utc'], errors='ignore').to_csv(index=False).encode(), file_name='v04_validation.csv', mime='text/csv')
    st.info('Full historical backtesting and automatic result settlement require verified pre-race archives, complete race results, and a validated official results feed. Not enabled in this release.')
