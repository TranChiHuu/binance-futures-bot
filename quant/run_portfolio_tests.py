#!/usr/bin/env python3
"""Hypothesis tests (a) re-entry block, (b) max breakout age, (c) min SL vs ATR, and correlation rules,
both per-symbol (large sample) and in the full portfolio replay. Also replays the walk-forward choice
fold by fold in the portfolio (out-of-sample)."""
import os, json, pickle, numpy as np, pandas as pd
import matplotlib; matplotlib.use('Agg'); import matplotlib.pyplot as plt
from strategy import P, LIVE
from engine import symbol_level, portfolio
from metrics import stats, fmt, monthly
from common import signals, all_syms, D
from walkforward import make_filter, combo_params
from correlation import Corr
R = f'{D}/results'

def split(trs, mid):
    return stats([t for t in trs if t['ts'] < mid]), stats([t for t in trs if t['ts'] >= mid])

if __name__ == '__main__':
    syms = all_syms(); cache = {}
    sigs = signals(LIVE)
    mid = int(np.median(syms['BTCUSDT'].ot))
    out = {'hyp': {}, 'corr': {}}
    tests = [('live', None), ('a_reentry_block', make_filter(reentry_block=True))]
    for n in (0, 1, 2, 3, 4): tests.append((f'b_max_age_{n}h', make_filter(max_age=n)))
    for x in (0.5, 0.75, 1.0, 1.25, 1.5): tests.append((f'c_min_sl_{x}atr', make_filter(min_sl_atr=x)))
    tests.append(('a+b3+c1.0', make_filter(True, 1.0, 3)))
    tests.append(('a+c1.0', make_filter(True, 1.0, None)))
    tests.append(('no_retest', make_filter(setups={'BREAKOUT', 'MOMENTUM'})))
    tests.append(('breakout_only', make_filter(setups={'BREAKOUT'})))
    tests.append(('breakout_only_minsl1', make_filter(min_sl_atr=1.0, setups={'BREAKOUT'})))
    tests.append(('breakout_only_sl1to2', make_filter(min_sl_atr=1.0, setups={'BREAKOUT'}, max_sl_atr=2.0)))
    for name, f in tests:
        st = symbol_level(syms, sigs, LIVE, filt=f, cache=cache)
        pt, eq = portfolio(syms, sigs, LIVE, filt=f, cache=cache, wallet0=1e6, high_fixed=6.75)
        ptc, eqc = portfolio(syms, sigs, LIVE, filt=f, cache=cache)
        y1, y2 = split(st, mid)
        out['hyp'][name] = dict(symbol=stats(st), symbol_y1=y1, symbol_y2=y2,
                                retest_symbol=stats([t for t in st if t['setup'] == 'RETEST']),
                                portfolio=stats(pt), portfolio_total_usdt=float(sum(t['pnl'] for t in pt)), portfolio_usdt=stats(pt, 'pnl'),
                                portfolio_y1=split(pt, mid)[0], portfolio_y2=split(pt, mid)[1],
                                compounding_wallet_end=eqc[-1][1] if eqc else 45,
                                compounding_ruin=(pd.to_datetime(eqc[-1][0], unit='ms').strftime('%Y-%m-%d') if eqc and eqc[-1][1] < 5 else None))
        print(f"{name:18s} SYM {fmt(out['hyp'][name]['symbol'])}\n{'':18s} RETEST {fmt(out['hyp'][name]['retest_symbol'])}\n{'':18s} y1 {fmt(y1)} | y2 {fmt(y2)}\n{'':18s} PORT(fixed) {fmt(out['hyp'][name]['portfolio'])} USDT={out['hyp'][name]['portfolio_total_usdt']:.0f} | compounding 45->{out['hyp'][name]['compounding_wallet_end']:.1f} ruin={out['hyp'][name]['compounding_ruin']}", flush=True)
    C = Corr()
    eqs = {}
    for rule in ('none', 'groups', 'rolling', 'groups+rolling'):
        pt, eq = portfolio(syms, sigs, LIVE, corr_rule=rule, corr_fn=C, cache=cache, wallet0=1e6, high_fixed=6.75)
        pb, eqb = portfolio(syms, sigs, LIVE, filt=make_filter(min_sl_atr=1.0, setups={'BREAKOUT'}), corr_rule=rule, corr_fn=C, cache=cache, wallet0=1e6, high_fixed=6.75)
        out['corr'][rule] = dict(portfolio=stats(pt), total_usdt=float(sum(t['pnl'] for t in pt)), usdt=stats(pt, 'pnl'),
                                 breakout_minsl1=stats(pb), breakout_minsl1_usdt=float(sum(t['pnl'] for t in pb)))
        print('corr', rule, 'breakout_minsl1', fmt(stats(pb)), round(out['corr'][rule]['breakout_minsl1_usdt'], 1))
        eqs[rule] = eq
        print('corr', rule, fmt(out['corr'][rule]['portfolio']), 'USDT', round(out['corr'][rule]['total_usdt'], 1), flush=True)
    # overlap analysis on live portfolio: pairs of simultaneous same-direction trades
    pt, _ = portfolio(syms, sigs, LIVE, corr_rule='groups', cache=cache, wallet0=1e6, high_fixed=6.75)
    ov = []
    for i, a in enumerate(pt):
        for b in pt[i+1:]:
            if b['t_fill'] < a['t_exit'] and a['t_fill'] < b['t_exit'] and a['book'] != b['book']:
                ov.append(dict(same=a['side'] == b['side'], c=C(a['sym'], b['sym'], max(a['ts'], b['ts'])), ra=a['R'], rb=b['R']))
    if ov:
        df = pd.DataFrame(ov)
        out['overlap'] = dict(n=len(df), same_dir=int(df.same.sum()), same_dir_corr08=int((df.same & (df.c > 0.8)).sum()),
                              both_lose_same_dir=int((df.same & (df.ra < 0) & (df.rb < 0)).sum()),
                              both_lose_opp_dir=int((~df.same & (df.ra < 0) & (df.rb < 0)).sum()),
                              mean_corr_same_dir=float(df[df.same].c.mean()) if df.same.any() else None)
        print('overlap', out['overlap'])
    # walk-forward choice replayed in the portfolio, fold by fold (OOS)
    wf = json.load(open(f'{R}/walkforward.json'))
    allres = pickle.load(open(f'{R}/wf_allres.pkl', 'rb'))
    folds = allres['folds']
    oos_fixed = []; live_fixed = []
    wallet = 45.0; oos_tr = []; eq_all = []; live_tr = []; wallet_l = 45.0; eq_l = []
    for (a, m, b), fd in zip(folds, wf['folds']):
        bp = fd['best']
        p = combo_params(bp['atr_buf'], bp['zone'], bp['lookback'], bp['score_add'], bp['tp1'])
        sg = signals(p)
        sgw = {s: [x for x in v if m <= x['ts'] < b] for s, v in sg.items()}
        tr, eq = portfolio(syms, sgw, p, wallet0=wallet, filt=make_filter(bp['reentry'], bp['min_sl_atr']), cache=cache)
        trf, _ = portfolio(syms, sgw, p, wallet0=1e6, high_fixed=6.75, filt=make_filter(bp['reentry'], bp['min_sl_atr']), cache=cache)
        oos_fixed += trf
        trlf, _ = portfolio(syms, {s_: [x for x in v if m <= x['ts'] < b] for s_, v in sigs.items()}, LIVE, wallet0=1e6, high_fixed=6.75, cache=cache)
        live_fixed += trlf
        if eq: wallet = eq[-1][1]
        oos_tr += tr; eq_all += eq
        sgl = {s: [x for x in v if m <= x['ts'] < b] for s, v in sigs.items()}
        trl, eql = portfolio(syms, sgl, LIVE, wallet0=wallet_l, cache=cache)
        if eql: wallet_l = eql[-1][1]
        live_tr += trl; eq_l += eql
    out['wf_portfolio_fixed'] = dict(OOS=stats(oos_fixed), usdt=float(sum(t['pnl'] for t in oos_fixed)),
                                     setups={k: stats([t for t in oos_fixed if t['setup'] == k]) for k in ('RETEST', 'BREAKOUT', 'MOMENTUM')})
    out['live_portfolio_fixed_same_windows'] = dict(R=stats(live_fixed), usdt=float(sum(t['pnl'] for t in live_fixed)))
    print('WF portfolio fixed OOS', fmt(out['wf_portfolio_fixed']['OOS']), 'USDT', round(out['wf_portfolio_fixed']['usdt'], 1), {k: fmt(v) for k, v in out['wf_portfolio_fixed']['setups'].items()})
    print('LIVE portfolio fixed same windows', fmt(out['live_portfolio_fixed_same_windows']['R']), 'USDT', round(out['live_portfolio_fixed_same_windows']['usdt'], 1))
    out['wf_portfolio'] = dict(OOS=stats(oos_tr), wallet=wallet, usdt=stats(oos_tr, 'pnl'), monthly_usdt=monthly(oos_tr, 'pnl'))
    out['live_portfolio_same_windows'] = dict(R=stats(live_tr), wallet=wallet_l, usdt=stats(live_tr, 'pnl'), monthly_usdt=monthly(live_tr, 'pnl'))
    print('WF portfolio OOS', fmt(out['wf_portfolio']['OOS']), 'wallet', round(wallet, 1))
    print('LIVE portfolio same windows', fmt(out['live_portfolio_same_windows']['R']), 'wallet', round(wallet_l, 1))
    plt.figure(figsize=(11, 4.5))
    for nm, e in [('Walk-forward (OOS)', eq_all), ('Luat live', eq_l)]:
        if e: plt.plot(pd.to_datetime([x[0] for x in e], unit='ms') + pd.Timedelta(hours=7), [x[1] for x in e], label=nm)
    plt.title('Danh muc tren cac cua so kiem tra (out-of-sample), von 45 USDT'); plt.ylabel('USDT'); plt.legend(); plt.grid(alpha=.3); plt.tight_layout()
    plt.savefig(f'{R}/equity_walkforward_oos_usdt.png', dpi=110); plt.close()
    json.dump(out, open(f'{R}/portfolio_tests.json', 'w'), indent=1, default=str)
