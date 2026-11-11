#!/usr/bin/env python3
"""Comparison charts + OOS breakdown per book/setup + monthly tables used in REPORT.md."""
import json, pickle, numpy as np, pandas as pd
import matplotlib; matplotlib.use('Agg'); import matplotlib.pyplot as plt
from strategy import LIVE
from engine import symbol_level, portfolio
from metrics import stats, monthly, fmt
from common import signals, all_syms, D
from walkforward import make_filter
R = f'{D}/results'
syms = all_syms(); sigs = signals(LIVE); cache = {}
variants = {'Luat live hien tai': None, 'Bo RETEST (chi BREAKOUT)': make_filter(setups={'BREAKOUT'}),
            'BREAKOUT + SL>=1 ATR': make_filter(min_sl_atr=1.0, setups={'BREAKOUT'})}
out = {'monthly_symbol_R': {}, 'monthly_fixed_usdt': {}, 'fixed_book_setup': {}}
plt.figure(figsize=(11, 4.8))
for nm, f in variants.items():
    st = sorted(symbol_level(syms, sigs, LIVE, filt=f, cache=cache), key=lambda t: t['t_exit'])
    plt.plot(pd.to_datetime([t['t_exit'] for t in st], unit='ms') + pd.Timedelta(hours=7), np.cumsum([t['R'] for t in st]), label=nm)
    out['monthly_symbol_R'][nm] = monthly(st)
    pf, _ = portfolio(syms, sigs, LIVE, filt=f, cache=cache, wallet0=1e6, high_fixed=6.75)
    out['monthly_fixed_usdt'][nm] = monthly(pf, 'pnl')
    out['fixed_book_setup'][nm] = {f'{b}/{s}': stats([t for t in pf if t['book'] == b and t['setup'] == s]) for b in ('CORE', 'HIGH') for s in ('RETEST', 'BREAKOUT', 'MOMENTUM')}
    out['fixed_book_setup'][nm].update({b: stats([t for t in pf if t['book'] == b]) for b in ('CORE', 'HIGH')})
plt.axhline(0, color='grey', lw=.5); plt.title('R tich luy (moi coin doc lap, sau phi + truot gia), 19 coin, 2 nam'); plt.ylabel('R'); plt.legend(); plt.grid(alpha=.3); plt.tight_layout()
plt.savefig(f'{R}/equity_variants_R.png', dpi=110); plt.close()
# breakout-only fixed portfolio equity
pf, eqf = portfolio(syms, sigs, LIVE, filt=make_filter(min_sl_atr=1.0, setups={'BREAKOUT'}), cache=cache, wallet0=1e6, high_fixed=6.75)
pl, eql = portfolio(syms, sigs, LIVE, cache=cache, wallet0=1e6, high_fixed=6.75)
plt.figure(figsize=(11, 4.5))
for nm, e in [('Luat live', eql), ('BREAKOUT + SL>=1 ATR', eqf)]:
    plt.plot(pd.to_datetime([x[0] for x in e], unit='ms') + pd.Timedelta(hours=7), [x[1] - 1e6 for x in e], label=nm)
plt.axhline(0, color='grey', lw=.5); plt.title('Danh muc day du, khoi luong co dinh (CORE 2 / MAO HIEM 6.75 USDT), lai/lo tich luy'); plt.ylabel('USDT'); plt.legend(); plt.grid(alpha=.3); plt.tight_layout()
plt.savefig(f'{R}/equity_portfolio_fixed_compare_usdt.png', dpi=110); plt.close()
# WF OOS breakdown per book / setup (symbol-level)
wf = json.load(open(f'{R}/walkforward.json')); A = pickle.load(open(f'{R}/wf_allres.pkl', 'rb'))
folds = A['folds']; allres = A['allres']; keys = ['atr_buf', 'zone', 'lookback', 'score_add', 'tp1', 'reentry', 'min_sl_atr']
oos, ins, live_oos = [], [], []
live_key = (0.4, 0.008, 8, 0.0, 1.5, False, 0.0)
for (a, m, b), fd in zip(folds, wf['folds']):
    k = tuple(fd['best'][x] for x in keys)
    oos += [x for x in allres[k] if m <= x[0] < b]; ins += [x for x in allres[k] if a <= x[0] < m]
    live_oos += [x for x in allres[live_key] if m <= x[0] < b]
def br(L):
    d = {'ALL': stats([dict(R=x[2], t_exit=x[1]) for x in L])}
    for bk in ('CORE', 'HIGH'):
        d[bk] = stats([dict(R=x[2], t_exit=x[1]) for x in L if x[3] == bk])
        for s in ('RETEST', 'BREAKOUT', 'MOMENTUM'):
            z = [dict(R=x[2], t_exit=x[1]) for x in L if x[3] == bk and x[4] == s]
            if z: d[f'{bk}/{s}'] = stats(z)
    return d
out['wf_IS_breakdown'] = br(ins); out['wf_OOS_breakdown'] = br(oos); out['live_OOS_windows_breakdown'] = br(live_oos)
json.dump(out, open(f'{R}/extra.json', 'w'), indent=1, default=str)
for nm in ('wf_IS_breakdown', 'wf_OOS_breakdown', 'live_OOS_windows_breakdown'):
    print('==', nm)
    for k, v in out[nm].items(): print('  ', k, fmt(v))
for nm, d in out['fixed_book_setup'].items():
    print('== fixed', nm)
    for k, v in d.items():
        if v.get('n'): print('  ', k, fmt(v))
print(json.dumps(out['monthly_symbol_R'], indent=0)[:3000])
print(json.dumps(out['monthly_fixed_usdt'], indent=0)[:3000])
