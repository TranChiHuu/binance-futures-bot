#!/usr/bin/env python3
"""B5 research: maker/limit entries for the new live config (BREAKOUT only, SL>=1 ATR, TP1 1.5R 50% + BE, TP2).
Real fees: taker 0.050%, maker 0.020%. Market entry slippage 0.05%, SL/BE slippage 0.02%.
Limit fills only if a later 15m bar trades THROUGH the limit by >=0.01% within N bars, else cancel (missed trade).
Maker TPs: reduce-only limit at TP, filled only if price trades through by 0.01% (else no fill)."""
import os, json, pickle, itertools, sys, numpy as np, pandas as pd
import matplotlib; matplotlib.use('Agg'); import matplotlib.pyplot as plt
sys.path.insert(0, '/workspace'); import bn
from strategy import LIVE
from engine import portfolio, in_blackout, H1
from metrics import stats, fmt, monthly
from common import signals, all_syms, D
R = f'{D}/results'
TAKER, MAKER = 0.0005, 0.0002
MODES = ['level', 'pb0', 'pb0.1', 'pb0.2', 'pb0.3']
NS = [4, 8, 12]

def transform(S, s, mode):
    """Return a modified signal for this entry mode, or None if it fails live checks (A3, R:R>=2)."""
    if s['setup'] != 'BREAKOUT': return None
    side = s['side']; sg = 1 if side == 'LONG' else -1; mark = s['mark']; sl = s['sl']; atr = s['atr']
    if mode == 'market':
        if abs(mark - sl) < 1.0 * atr: return None
        return s
    if mode == 'level': lim = s['level']
    else:
        x = float(mode[2:]) / 100; cb = S.pr.c[s['j']]
        lim = cb * (1 - sg * x)
    lim = min(lim, mark) if side == 'LONG' else max(lim, mark)   # post-only: never on the wrong side of mark
    risk = sg * (lim - sl)
    if risk <= 0 or risk < 1.0 * atr: return None                # A3 on the actual entry
    tp1 = lim + sg * 1.5 * risk
    tp2 = s['tp2']; cap = lim + sg * 4 * risk
    tp2 = min(tp2, cap) if side == 'LONG' else max(tp2, cap)
    if sg * (tp2 - lim) / risk < 2.0: return None
    return {**s, 'entry': lim, 'entry_type': 'LIMIT', 'tp1': tp1, 'tp2': tp2, 'mode': mode}

def kw(mode, N=8, tp_maker=False):
    if mode == 'market': return dict(fee_in=TAKER, fee_tp=TAKER, fee_out=TAKER, slip_mkt=0.0005)
    return dict(fee_in=MAKER, fee_tp=MAKER if tp_maker else TAKER, fee_out=TAKER, limit_bars=N, lim_through=0.0001,
                tp_through=0.0001 if tp_maker else 0.0)

def sym_study(syms, base, mode, N, tpm, cache):
    """Per-symbol independent study; records fills, misses and what the market entry would have earned on misses."""
    trades, miss_mkt, fill_mkt, nsig = [], [], [], 0
    k = kw(mode, N, tpm); km = kw('market')
    for sym, sigs in base.items():
        S = syms[sym]; busy = 0
        for s in sigs:
            if s['ts'] < busy or in_blackout(s['ts']): continue
            if s['score_raw'] - 0.25 < s['thr']: continue
            t = transform(S, s, mode)
            if t is None: continue
            key = (sym, s['j'], mode, N, tpm)
            r = cache.get(key)
            if r is None:
                r = S.sim_exit(t, tp1_cap=1.5, fee=LIVE.fee, **k); cache[key] = r
            if r is None: continue
            nsig += 1
            busy = r['t_free']
            mk = None
            if s['setup'] == 'BREAKOUT' and abs(s['mark'] - s['sl']) >= s['atr']:
                mk = cache.get((sym, s['j'], 'market', 0, False))
                if mk is None:
                    mk = S.sim_exit(s, tp1_cap=1.5, fee=LIVE.fee, **km); cache[(sym, s['j'], 'market', 0, False)] = mk
            if r['outcome'] == 'NOFILL':
                if mk and mk['outcome'] != 'NOFILL': miss_mkt.append(mk['R'])
                continue
            if mk and mk['outcome'] != 'NOFILL': fill_mkt.append(mk['R'])
            trades.append({'sym': sym, 'book': s['book'], 'ts': s['ts'], **r})
    return trades, nsig, miss_mkt, fill_mkt

def mc(Rs, risk, n_paths=1000, w0=45.0, seed=1):
    rng = np.random.default_rng(seed); Rs = np.asarray(Rs)
    finals, ever = [], 0
    for _ in range(n_paths):
        x = rng.choice(Rs, size=len(Rs), replace=True)
        w = w0 * np.cumprod(1 + risk * x)
        finals.append(w[-1]); ever += (w.min() < 20)
    f = np.array(finals)
    return dict(median_final=round(float(np.median(f)), 1), p5_final=round(float(np.percentile(f, 5)), 1), p95_final=round(float(np.percentile(f, 95)), 1),
                pct_end_below_20=round(float((f < 20).mean()) * 100, 1), pct_ever_below_20=round(ever / n_paths * 100, 1),
                pct_end_below_45=round(float((f < 45).mean()) * 100, 1))

if __name__ == '__main__':
    syms = all_syms(); base = signals(LIVE); cache = {}
    base = {s: [x for x in v if x['setup'] == 'BREAKOUT'] for s, v in base.items()}
    res = {}; trs = {}
    variants = [('market', 0, False)] + [(m, n, t) for m in MODES for n in NS for t in (False, True)]
    for (m, n, t) in variants:
        tr, nsig, miss, fillm = sym_study(syms, base, m, n, t, cache)
        name = 'market' if m == 'market' else f'{m}|N{n}|TP{"maker" if t else "taker"}'
        st = stats(tr)
        res[name] = dict(stats=st, signals=nsig, fill_rate=round(len(tr) / nsig, 3) if nsig else None,
                         missed_mkt_avgR=round(float(np.mean(miss)), 3) if miss else None, n_missed=len(miss),
                         filled_mkt_avgR=round(float(np.mean(fillm)), 3) if fillm else None)
        trs[name] = [(x['ts'], x['t_exit'], x['R'], x['book'], 'BREAKOUT') for x in tr]
        print(f"{name:24s} fill={res[name]['fill_rate']} {fmt(st)} | missed(mkt R)={res[name]['missed_mkt_avgR']} n={len(miss)} filled(mkt R)={res[name]['filled_mkt_avgR']}", flush=True)
    # walk-forward with the same folds as walkforward.py
    folds = pickle.load(open(f'{R}/wf_allres.pkl', 'rb'))['folds']
    seg = lambda L, a, b: [x for x in L if a <= x[0] < b]
    makers = [k for k in trs if k != 'market']
    wf = []; oos = []; ins = []; oos_m = []; ins_m = []
    for (a, m_, b) in folds:
        best = max(makers, key=lambda k: sum(x[2] for x in seg(trs[k], a, m_)) if len(seg(trs[k], a, m_)) >= 40 else -1e9)
        i_, o_ = seg(trs[best], a, m_), seg(trs[best], m_, b)
        wf.append(dict(test=[pd.to_datetime(m_, unit='ms').strftime('%Y-%m-%d'), pd.to_datetime(b, unit='ms').strftime('%Y-%m-%d')], best=best,
                       IS_avgR=round(np.mean([x[2] for x in i_]), 3), OOS_avgR=round(np.mean([x[2] for x in o_]), 3) if o_ else None, OOS_n=len(o_),
                       MKT_OOS_avgR=round(np.mean([x[2] for x in seg(trs['market'], m_, b)]), 3)))
        oos += o_; ins += i_; oos_m += seg(trs['market'], m_, b); ins_m += seg(trs['market'], a, m_)
        print(wf[-1], flush=True)
    S2 = lambda L: stats([dict(R=x[2], t_exit=x[1]) for x in L])
    res['_walkforward'] = dict(folds=wf, maker_IS=S2(ins), maker_OOS=S2(oos), market_IS=S2(ins_m), market_OOS=S2(oos_m),
                               chosen=pd.Series([w['best'] for w in wf]).value_counts().to_dict())
    print('WF maker IS ', fmt(res['_walkforward']['maker_IS'])); print('WF maker OOS', fmt(res['_walkforward']['maker_OOS']))
    print('WF market IS', fmt(res['_walkforward']['market_IS'])); print('WF market OOS', fmt(res['_walkforward']['market_OOS']))
    json.dump(res, open(f'{R}/maker.json', 'w'), indent=1, default=str)
    pickle.dump(trs, open(f'{R}/maker_trs.pkl', 'wb'))
