#!/usr/bin/env python3
"""Backtest of the approved live config: A1 (RETEST+MOMENTUM blocked) + A3 (SL>=1 ATR) + A4 (1% wallet risk, both books,
compounding from 45 USDT, real stepSize/minQty/MIN_NOTIONAL from exchangeInfo) + A7 (daily stop max(-7.5, -3x risk))."""
import json, sys, numpy as np, pandas as pd
import matplotlib; matplotlib.use('Agg'); import matplotlib.pyplot as plt
sys.path.insert(0, '/workspace'); import bn
from strategy import LIVE
from engine import portfolio
from metrics import stats, monthly, fmt
from common import signals, all_syms, D
from walkforward import make_filter
ex = bn.pub('/fapi/v1/exchangeInfo'); F = {}
for s in ex['symbols']:
    f = {x['filterType']: x for x in s['filters']}
    F[s['symbol']] = (float(f['LOT_SIZE']['stepSize']), float(f['LOT_SIZE']['minQty']), float(f['MIN_NOTIONAL']['notional']))
syms = all_syms(); sigs = signals(LIVE); cache = {}
out = {}
for name, filt in [('A1+A3+A4+A7', make_filter(min_sl_atr=1.0, setups={'BREAKOUT'})), ('live_rules+A4+A7', None)]:
    tr, eq = portfolio(syms, sigs, LIVE, wallet0=45.0, filt=filt, cache=cache, risk_pct_both=0.01, daily_mult=3.0, filters=F)
    w = np.array([45.0] + [e[1] for e in eq]); peak = np.maximum.accumulate(w); dd = peak - w; ddp = dd / peak
    days = (tr[-1]['t_exit'] - tr[0]['ts']) / 86400000 if tr else 1
    out[name] = dict(final=round(float(w[-1]), 2), ret_pct=round((w[-1] / 45 - 1) * 100, 1), maxdd_usdt=round(float(dd.max()), 2),
                     maxdd_pct=round(float(ddp.max()) * 100, 1), trades=len(tr), per_week=round(len(tr) / days * 7, 2),
                     R=stats(tr), by_book={b: stats([t for t in tr if t['book'] == b]) for b in ('CORE', 'HIGH')},
                     by_sym_n={s: sum(t['sym'] == s for t in tr) for s in syms}, monthly_usdt=monthly(tr, 'pnl'))
    print(name, {k: v for k, v in out[name].items() if k in ('final', 'ret_pct', 'maxdd_usdt', 'maxdd_pct', 'trades', 'per_week')})
    print('  ', fmt(out[name]['R']), '| CORE', fmt(out[name]['by_book']['CORE']), '| HIGH', fmt(out[name]['by_book']['HIGH']))
    if name == 'A1+A3+A4+A7':
        print('   trades per sym', out[name]['by_sym_n'])
        plt.figure(figsize=(11, 4.5)); plt.plot(pd.to_datetime([e[0] for e in eq], unit='ms') + pd.Timedelta(hours=7), [e[1] for e in eq])
        plt.title('Cau hinh moi (chi BREAKOUT, SL>=1 ATR, rui ro 1% vi, daily stop -3R), lai kep tu 45 USDT'); plt.ylabel('USDT'); plt.grid(alpha=.3); plt.tight_layout()
        plt.savefig(f'{D}/results/equity_new_config_usdt.png', dpi=110); plt.close()
json.dump(out, open(f'{D}/results/bt_a134.json', 'w'), indent=1, default=str)
