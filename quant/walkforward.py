#!/usr/bin/env python3
"""Walk-forward optimisation of a SMALL param set + hypothesis tests from live losses.
Train 6 months -> test next 2 months, rolling by 2 months. Objective on train: total R (net of fees),
requiring >= 40 trades. Evaluation uses the per-symbol independent study (no portfolio caps), so
samples are large enough; final portfolio replay of the walk-forward choice is in run_portfolio_tests.py."""
import os, json, itertools, pickle, numpy as np, pandas as pd
from strategy import P, LIVE
from engine import symbol_level
from metrics import stats, fmt
from common import signals, all_syms, D
R = f'{D}/results'; os.makedirs(R, exist_ok=True)
MS_M = 30.44 * 86400000

def make_filter(reentry_block=False, min_sl_atr=0.0, max_age=None, setups=None, max_sl_atr=None):
    def f(s, traded):
        if setups is not None and s['setup'] not in setups: return False
        if min_sl_atr and abs(s['entry'] - s['sl']) < min_sl_atr * s['atr']: return False
        if max_sl_atr and abs(s['entry'] - s['sl']) > max_sl_atr * s['atr']: return False
        if s['setup'] == 'RETEST':
            if max_age is not None and s['brk_age'] > max_age: return False
            if reentry_block and (s['side'], round(s['level'], 10)) in traded: return False
        return True
    return f

def combo_params(atr_buf, zone, lookback, score_add, tp1):
    return P(atr_buf=atr_buf, zone=zone, lookback=lookback, core_min=3.5 + score_add, high_min=2.5 + score_add, tp1_max_r=tp1)

GRID = dict(atr_buf=[0.3, 0.4, 0.6], zone=[0.004, 0.008], lookback=[3, 5, 8],
            score_add=[0.0, 0.5, 1.0], tp1=[1.0, 1.5, 2.0], reentry=[False, True], min_sl_atr=[0.0, 0.75, 1.0])

if __name__ == '__main__':
    syms = all_syms()
    t_all = np.concatenate([syms[s].ot for s in syms]); t0, t1 = int(np.percentile(t_all, 1)), int(t_all.max())
    t0 = int(min(syms['BTCUSDT'].ot)) + 60 * 3600000
    folds = []
    a = t0
    while a + 8 * MS_M <= t1 + 5 * 86400000:
        folds.append((a, a + 6 * MS_M, min(a + 8 * MS_M, t1))); a += 2 * MS_M
    print('folds', len(folds))
    results = []   # (combo, trades)
    keys = list(GRID)
    scan_sets = list(itertools.product(GRID['atr_buf'], GRID['zone'], GRID['lookback']))
    cache = {}
    allres = {}
    for (ab, zn, lb) in scan_sets:
        sigs = signals(P(atr_buf=ab, zone=zn, lookback=lb))
        for sa, tp1, re, ms in itertools.product(GRID['score_add'], GRID['tp1'], GRID['reentry'], GRID['min_sl_atr']):
            p = combo_params(ab, zn, lb, sa, tp1)
            tr = symbol_level(syms, sigs, p, filt=make_filter(re, ms), cache=cache)
            allres[(ab, zn, lb, sa, tp1, re, ms)] = [(t['ts'], t['t_exit'], t['R'], t['book'], t['setup']) for t in tr]
        cache.clear()
        print('scan set done', ab, zn, lb, flush=True)
    pickle.dump(dict(allres=allres, folds=folds), open(f'{R}/wf_allres.pkl', 'wb'))
    live_key = (0.4, 0.008, 8, 0.0, 1.5, False, 0.0)
    def seg(trs, a, b): return [x for x in trs if a <= x[0] < b]
    def score(trs):
        if len(trs) < 40: return -1e9
        return sum(x[2] for x in trs)
    wf = []; oos = []; oos_live = []
    for (a, m, b) in folds:
        best = max(allres, key=lambda k: score(seg(allres[k], a, m)))
        tr_is = seg(allres[best], a, m); tr_oos = seg(allres[best], m, b)
        live_is = seg(allres[live_key], a, m); live_oos = seg(allres[live_key], m, b)
        r = lambda L: dict(n=len(L), sumR=round(sum(x[2] for x in L), 2), avgR=round(np.mean([x[2] for x in L]), 3) if L else None)
        wf.append(dict(train=[pd.to_datetime(a, unit='ms').strftime('%Y-%m-%d'), pd.to_datetime(m, unit='ms').strftime('%Y-%m-%d')],
                       test_end=pd.to_datetime(b, unit='ms').strftime('%Y-%m-%d'), best=dict(zip(keys, best)),
                       IS=r(tr_is), OOS=r(tr_oos), LIVE_IS=r(live_is), LIVE_OOS=r(live_oos)))
        oos += tr_oos; oos_live += live_oos
        print(wf[-1], flush=True)
    def st(L):
        return stats([dict(R=x[2], t_exit=x[1]) for x in L])
    # full-period in-sample ranking (to show optimism)
    full = sorted(allres, key=lambda k: -sum(x[2] for x in allres[k]))
    top = [dict(params=dict(zip(keys, k)), **{kk: (round(v, 3) if isinstance(v, float) else v) for kk, v in st(allres[k]).items() if kk != 'ci95'}) for k in full[:10]]
    # per-dimension marginal effect (avg over all others): mean sumR per value
    marg = {}
    for i, kname in enumerate(keys):
        marg[kname] = {str(v): round(float(np.mean([sum(x[2] for x in allres[k]) for k in allres if k[i] == v])), 1) for v in GRID[kname]}
    # stability: how often each value is chosen across folds
    chosen = {kname: pd.Series([w['best'][kname] for w in wf]).astype(str).value_counts().to_dict() for kname in keys}
    # first-year vs second-year for live and full-best
    mid = (t0 + t1) // 2
    halves = {}
    for nm, k in [('live', live_key), ('full_best', full[0])]:
        halves[nm] = dict(y1=st(seg(allres[k], 0, mid)), y2=st(seg(allres[k], mid, 1 << 62)))
    out = dict(folds=wf, OOS_walkforward=st(oos), OOS_live_same_windows=st(oos_live), LIVE_full=st(allres[live_key]),
               full_period_top10_IN_SAMPLE=top, marginal_sumR=marg, chosen_counts=chosen, halves=halves,
               n_combos=len(allres))
    json.dump(out, open(f'{R}/walkforward.json', 'w'), indent=1, default=str)
    print('OOS walk-forward:', fmt(out['OOS_walkforward']))
    print('OOS live same windows:', fmt(out['OOS_live_same_windows']))
    print('LIVE full:', fmt(out['LIVE_full']))
    print('top IS:', top[:3]); print('marginal', marg); print('chosen', chosen)
