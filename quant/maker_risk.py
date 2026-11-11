#!/usr/bin/env python3
"""Compounding portfolio from 45 USDT at 1/2/3% risk for market vs best maker variant + bootstrap Monte Carlo."""
import json, sys, numpy as np, pandas as pd
import matplotlib; matplotlib.use('Agg'); import matplotlib.pyplot as plt
sys.path.insert(0, '/workspace'); import bn
from strategy import LIVE
from engine import portfolio
from metrics import stats, fmt
from common import signals, all_syms, D
from maker import transform, kw, mc
R = f'{D}/results'
ex = bn.pub('/fapi/v1/exchangeInfo'); F = {}
for s in ex['symbols']:
    f = {x['filterType']: x for x in s['filters']}
    F[s['symbol']] = (float(f['LOT_SIZE']['stepSize']), float(f['LOT_SIZE']['minQty']), float(f['MIN_NOTIONAL']['notional']))
syms = all_syms(); base = signals(LIVE); cache = {}
VAR = {'market': ('market', 8, False), 'maker pb0|N4|TPmaker': ('pb0', 4, True)}
out = {}; curves = {}
mid = int(np.median(syms['BTCUSDT'].ot))
for vn, (mode, N, tpm) in VAR.items():
    sg = {}
    for sym, v in base.items():
        sg[sym] = [t for t in (transform(syms[sym], x, mode) for x in v) if t is not None]
    for risk in (0.01, 0.02, 0.03):
        skips = {}
        tr, eq = portfolio(syms, sg, LIVE, wallet0=45.0, cache=cache, sim_kw=kw(mode, N, tpm), risk_pct_both=risk, daily_mult=3.0, filters=F, skip_log=skips)
        w = np.array([45.0] + [e[1] for e in eq]); peak = np.maximum.accumulate(w); dd = peak - w
        days = (tr[-1]['t_exit'] - tr[0]['ts']) / 86400000
        nsig = len(tr) + sum(1 for _ in [])
        res = dict(final=round(float(w[-1]), 2), maxdd_usdt=round(float(dd.max()), 2), maxdd_pct=round(float((dd / peak).max()) * 100, 1),
                   trades=len(tr), per_week=round(len(tr) / days * 7, 2), R=stats(tr), y1=stats([t for t in tr if t['ts'] < mid]), y2=stats([t for t in tr if t['ts'] >= mid]),
                   min_notional_skips=dict(sorted(skips.items(), key=lambda x: -x[1])), MC=mc([t['R'] for t in tr], risk))
        out[f'{vn}@{int(risk*100)}%'] = res
        curves[f'{vn} @{int(risk*100)}%'] = eq
        print(f"{vn:22s} {int(risk*100)}%: final={res['final']} maxDD={res['maxdd_usdt']} ({res['maxdd_pct']}%) trades={res['trades']} ({res['per_week']}/wk) {fmt(res['R'])}")
        print(f"   y1 {fmt(res['y1'])} | y2 {fmt(res['y2'])}")
        print(f"   MC {res['MC']}  skips {res['min_notional_skips']}", flush=True)
json.dump(out, open(f'{R}/maker_risk.json', 'w'), indent=1, default=str)
plt.figure(figsize=(11, 5))
for k, e in curves.items():
    plt.plot(pd.to_datetime([x[0] for x in e], unit='ms') + pd.Timedelta(hours=7), [x[1] for x in e], label=k, lw=1.1, ls='-' if 'maker' in k else '--')
plt.axhline(45, color='grey', lw=.5); plt.yscale('log'); plt.ylabel('USDT (log)'); plt.legend(fontsize=8); plt.grid(alpha=.3)
plt.title('BREAKOUT + SL>=1 ATR, lai kep tu 45 USDT: vao market vs limit maker, rui ro 1/2/3%'); plt.tight_layout()
plt.savefig(f'{R}/equity_maker_vs_market_risk.png', dpi=110); plt.close()
