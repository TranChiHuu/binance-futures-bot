#!/usr/bin/env python3
"""Baseline backtest of the CURRENT live rules. Outputs results/baseline.json + equity PNGs."""
import os, json, pickle, sys, numpy as np, pandas as pd
import matplotlib; matplotlib.use('Agg'); import matplotlib.pyplot as plt
from strategy import LIVE, P, CORE, HIGH
from engine import symbol_level, portfolio
from metrics import stats, monthly, fmt
from common import signals, all_syms, D
R = f'{D}/results'; os.makedirs(R, exist_ok=True)

def breakdown(trades):
    out = {'ALL': stats(trades)}
    for bk in ('CORE', 'HIGH'):
        tb = [t for t in trades if t['book'] == bk]; out[bk] = stats(tb)
        for st in ('RETEST', 'BREAKOUT', 'MOMENTUM'):
            ts = [t for t in tb if t['setup'] == st]
            if ts: out[f'{bk}/{st}'] = stats(ts)
    for st in ('RETEST', 'BREAKOUT', 'MOMENTUM'):
        ts = [t for t in trades if t['setup'] == st]
        if ts: out[st] = stats(ts)
    return out

def plot_equity(series, fn, title, ylabel):
    plt.figure(figsize=(11, 4.5))
    for name, (x, y) in series.items():
        plt.plot(pd.to_datetime(x, unit='ms') + pd.Timedelta(hours=7), y, label=name, lw=1.2)
    plt.axhline(series[list(series)[0]][1][0] if False else 0, color='grey', lw=0.5)
    plt.title(title); plt.ylabel(ylabel); plt.legend(); plt.grid(alpha=0.3); plt.tight_layout(); plt.savefig(fn, dpi=110); plt.close()

if __name__ == '__main__':
    syms = all_syms(); cache = {}
    sigs = signals(LIVE)
    res = {}
    for name, p in [('live', LIVE), ('no_lessons', P(pen_retest=0, pen_all=0, tp1_max_r=2.0, fee=0.0004))]:
        st = symbol_level(syms, sigs, p, cache=cache)
        pt, eq = portfolio(syms, sigs, p, cache=cache)
        res[name] = dict(symbol_level=breakdown(st), portfolio_R=breakdown(pt),
                         portfolio_usdt=dict(final_wallet=eq[-1][1] if eq else 45.0, n=len(pt),
                                             monthly_usdt=monthly(pt, 'pnl'), stats_usdt=stats(pt, 'pnl')),
                         monthly_R_symbol_level=monthly(st), monthly_R_portfolio=monthly(pt))
        # fixed-size replay over the full 2 years (today's sizes: CORE 2 USDT, HIGH 6.75 USDT = 15% of 45), no compounding/ruin stop
        pf_, eqf = portfolio(syms, sigs, p, wallet0=1e6, high_fixed=6.75, cache=cache)
        res[name]['portfolio_fixed'] = dict(R=breakdown(pf_), usdt=stats(pf_, 'pnl'), monthly_usdt=monthly(pf_, 'pnl'),
                                            total_usdt=float(sum(t['pnl'] for t in pf_)), ruin_date=(pd.to_datetime(eq[-1][0], unit='ms').strftime('%Y-%m-%d') if eq and eq[-1][1] < 5 else None))
        print('  FIXED', fmt(res[name]['portfolio_fixed']['R']['ALL']), 'USDT', round(res[name]['portfolio_fixed']['total_usdt'], 1))
        for k in ('CORE', 'HIGH', 'CORE/RETEST', 'HIGH/RETEST', 'HIGH/BREAKOUT', 'CORE/BREAKOUT'):
            if k in res[name]['portfolio_fixed']['R']: print('   FIXED', k, fmt(res[name]['portfolio_fixed']['R'][k]))
        pickle.dump(dict(st=st, pt=pt, eq=eq, pf=pf_, eqf=eqf), open(f'{R}/trades_{name}.pkl', 'wb'))
        print('==', name)
        for k, v in res[name]['symbol_level'].items(): print('  SYM ', k, fmt(v))
        for k, v in res[name]['portfolio_R'].items(): print('  PORT', k, fmt(v))
        print('  wallet 45 ->', round(res[name]['portfolio_usdt']['final_wallet'], 2))
        if name == 'live':
            ser = {}
            for bk in ('CORE', 'HIGH'):
                tb = sorted([t for t in st if t['book'] == bk], key=lambda t: t['t_exit'])
                ser[f'{bk} (moi coin doc lap)'] = ([t['t_exit'] for t in tb], np.cumsum([t['R'] for t in tb]))
            plot_equity(ser, f'{R}/equity_symbol_level_R.png', 'Luat live hien tai - tung coin doc lap (R tich luy, sau phi)', 'R')
            plot_equity({'Vi (USDT)': ([e[0] for e in eq], [e[1] for e in eq])}, f'{R}/equity_portfolio_usdt.png',
                        'Luat live hien tai - mo phong danh muc day du, von 45 USDT (lai kep)', 'USDT')
            plot_equity({'Lai/lo tich luy (USDT)': ([e[0] for e in eqf], [e[1] - 1e6 for e in eqf])}, f'{R}/equity_portfolio_fixed_usdt.png',
                        'Luat live hien tai - khoi luong co dinh (CORE 2 / MAO HIEM 6.75 USDT rui ro), 2 nam', 'USDT')
    # sensitivity: real fees (taker 0.05%) and zero slippage, 0.1% slippage
    for nm, kw, p in [('real_fee_0.05', {}, P(fee=0.0005)), ('slip_0', dict(slip_mkt=0.0, slip_sl=0.0), LIVE),
                      ('slip_0.1', dict(slip_mkt=0.001, slip_sl=0.0005), LIVE)]:
        st = symbol_level(syms, sigs, p, sim_kw=kw, cache=cache)
        res['sens_' + nm] = stats(st); print('  sens', nm, fmt(res['sens_' + nm]))
    json.dump(res, open(f'{R}/baseline.json', 'w'), indent=1, default=str)
