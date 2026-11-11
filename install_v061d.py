"""v0.6.1d: safely patch historical results for dead-heats in first place.

Run from repo root in Codespaces. Backups are written before changes.
Does not modify app.py, production v0.2 scorer, bets, or Supabase schema.
"""
from __future__ import annotations

import ast
from datetime import datetime, timezone
from pathlib import Path

TARGET = Path('v061b_historical.py')
TEST_TARGET = Path('test_v061b.py')
MARKER = 'V061D_DEAD_HEAT_MULTIPLE_WINNERS'


def change_once(source, old, new, label):
    n = source.count(old)
    if n != 1:
        raise ValueError(f'{label}: expected exactly 1 matching code block, found {n}; no changes saved')
    return source.replace(old, new, 1)


def update_historical(src):
    if MARKER in src:
        return src

    src = change_once(src,
        """    if wins != 1:
        raise ValueError(f'Expected exactly one winner, found {wins}')
    return {'starters':starters,'declared':len(placings),'scratches':len(placings)-starters}
""",
        """    # V061D_DEAD_HEAT_MULTIPLE_WINNERS: one or several joint firsts are valid.
    if wins < 1:
        raise ValueError('Final official results contain no first-place winner')
    return {'starters':starters,'declared':len(placings),
            'scratches':len(placings)-starters,
            'winners':wins,'dead_heat_first':wins>1}
""", 'official result parser')

    src = change_once(src,
        """                 'actual_starters':None,'top1_won':None,'winner_in_top3':None,
                 'winner_rank':None,'model_selection':None,'model_selection_sp':None,
""",
        """                 'actual_starters':None,'top1_won':None,'winner_in_top3':None,
                 'winner_rank':None,'winner_ranks':None,'winner_count':None,
                 'winning_horses':None,'dead_heat_first':None,
                 'model_selection':None,'model_selection_sp':None,
""", 'report columns')

    src = change_once(src,
        """        winner=next(k for k,v in starters.items() if integer(v.get('position'))==1)
        prediction_keys={f['horse_key'] for f in feat if f.get('model_rank') is not None}
        missing=sorted(set(starters)-prediction_keys)
        outcome['actual_starters']=len(starters)
        outcome['random_top1_expectation']=1/len(starters)
        outcome['random_top3_expectation']=min(3,len(starters))/len(starters)
""",
        """        winners={k for k,v in starters.items() if integer(v.get('position'))==1}
        winner_count=len(winners)
        outcome['winner_count']=winner_count
        outcome['dead_heat_first']=winner_count>1
        outcome['winning_horses']=' | '.join(sorted(
            p['name'] for p in payload['placings']
            if p.get('scratched') is not True and integer(p.get('position'))==1))
        prediction_keys={f['horse_key'] for f in feat if f.get('model_rank') is not None}
        missing=sorted(set(starters)-prediction_keys)
        outcome['actual_starters']=len(starters)
        # Probability that a uniformly chosen runner (or top-3 set) includes
        # at least one of the W first-place finishers; valid for dead-heats.
        n=len(starters)
        m=min(3,n)
        outcome['random_top1_expectation']=winner_count/n
        outcome['random_top3_expectation']=(
            1 - math.comb(n-winner_count,m)/math.comb(n,m)
            if n-winner_count >= m else 1.0)
""", 'winner lookup and random baselines')

    src = change_once(src,
        """        outcome['winner_rank']=rank_map.get(winner)
        outcome['winner_in_top3']=rank_map.get(winner) is not None and rank_map[winner] <= 3
""",
        """        win_ranks=sorted(rank_map[k] for k in winners if k in rank_map)
        outcome['winner_rank']=win_ranks[0] if win_ranks else None  # best winner rank
        outcome['winner_ranks']=','.join(str(rank) for rank in win_ranks)
        outcome['winner_in_top3']=any(rank<=3 for rank in win_ranks)
""", 'model winner ranking')

    src = change_once(src,
        """            outcome['top1_won']=original_top['horse_key']==winner
            sp=numeric(starters.get(original_top['horse_key'],{}).get('startingPrice'))
            if sp is not None and sp>1:
                outcome['model_selection_sp']=sp
                outcome['model_win_sp_profit_10']=10*(sp if outcome['top1_won'] else 0)-10
""",
        """            outcome['top1_won']=original_top['horse_key'] in winners
            sp=numeric(starters.get(original_top['horse_key'],{}).get('startingPrice'))
            if sp is not None and sp>1:
                outcome['model_selection_sp']=sp
                # Retrospective SP proxy, not a real settled bet. For a dead
                # heat, divide the winning return by co-winners (pro-rata
                # face value). The original $10 is the cost in all cases.
                gross=10*sp/winner_count if outcome['top1_won'] else 0
                outcome['model_win_sp_profit_10']=gross-10
""", 'model SP return')

    src = change_once(src,
        """                outcome['market_sp_favourite_won']=ties[0]==winner
""",
        """                outcome['market_sp_favourite_won']=ties[0] in winners
""", 'market SP favourite')

    src = change_once(src,
        """        'version':VERSION,'archived_races':len(df),'runners_saved':len(feature_rows),
        'official_results_imported':len(results),'evaluable_research_races':len(evaluate),
""",
        """        'version':VERSION,'archived_races':len(df),'runners_saved':len(feature_rows),
        'official_results_imported':len(results),'evaluable_research_races':len(evaluate),
        'first_place_dead_heats_imported':int(df['dead_heat_first'].fillna(False).sum()) if not df.empty else 0,
        'first_place_dead_heats_evaluable':int(evaluate['dead_heat_first'].fillna(False).sum()) if not evaluate.empty else 0,
""", 'summary dead-heat counts')

    src = change_once(src,
        """        'sp_research_roi':sp['model_win_sp_profit_10'].sum()/(10*len(sp)) if len(sp) else None,
        'pre_race_certified_count':0,
""",
        """        'sp_research_roi':sp['model_win_sp_profit_10'].sum()/(10*len(sp)) if len(sp) else None,
        'sp_return_basis':'RETROSPECTIVE_SP_PROXY_PRO_RATA_DEAD_HEAT_NOT_SETTLED_BETS',
        'pre_race_certified_count':0,
""", 'SP calculation disclosure')

    src = change_once(src,
        """                                            'starters':p['starters'],'scratches':p['scratches']}
""",
        """                                            'starters':p['starters'],'scratches':p['scratches'],
                                            'first_place_winners':p['winners'],
                                            'dead_heat_first':p['dead_heat_first']}
""", 'preview dead-heat columns')

    ast.parse(src,filename=str(TARGET))
    return src


def update_tests(src):
    old="""        copy=json.loads(json.dumps(RESULTS));copy['placings'][1]['position']=1
        with self.assertRaises(ValueError):m.parse_final_results(copy,RACE)
"""
    new="""        copy=json.loads(json.dumps(RESULTS));copy['placings'][1]['position']=1
        parsed=m.parse_final_results(copy,RACE)
        self.assertEqual(parsed['winners'],2)
        self.assertTrue(parsed['dead_heat_first'])
"""
    if new in src:
        return src
    src=change_once(src,old,new,'legacy test expectation')
    ast.parse(src,filename=str(TEST_TARGET))
    return src


def main():
    if not TARGET.is_file() or not TEST_TARGET.is_file():
        raise SystemExit('Run this from the repository root with v061b_historical.py and test_v061b.py')
    old_src=TARGET.read_text(encoding='utf-8')
    old_test=TEST_TARGET.read_text(encoding='utf-8')
    try:
        new_src=update_historical(old_src)
        new_test=update_tests(old_test)
    except (ValueError, SyntaxError) as exc:
        raise SystemExit(f'NOT INSTALLED: {exc}') from exc
    if new_src==old_src and new_test==old_test:
        print('v0.6.1d already installed. No files changed.')
        return
    stamp=datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')
    for path,before,after in ((TARGET,old_src,new_src),(TEST_TARGET,old_test,new_test)):
        if before==after:
            continue
        backup=path.with_name(path.name+'.pre_v061d_'+stamp+'.bak')
        backup.write_text(before,encoding='utf-8')
        path.write_text(after,encoding='utf-8')
        print(f'Updated {path}; backup {backup}')
    print('No SQL migration needed. Re-run tests then deploy to Streamlit.')


if __name__=='__main__':
    main()
