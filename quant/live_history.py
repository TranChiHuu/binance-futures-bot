#!/usr/bin/env python3
"""Learn from the real account: journal trades + userTrades/income (read-only data pulled by fetch_account.py).
- fees maker/taker actually paid, entry slippage (planned mark in hourly_log vs fill), exit slippage vs trigger
- port fidelity: live hourly scans vs strategy.py port on the same hour
- per trade: ATR at entry, SL/ATR, breakout age, re-entry on same level, which tested rules would have blocked it,
  and what the backtest engine predicts for that exact plan."""
import os, json, datetime, numpy as np, pandas as pd
from engine import Sym, H1, M15
from strategy import LIVE, final_score
D = os.path.dirname(os.path.abspath(__file__)); R = f'{D}/results'; os.makedirs(R, exist_ok=True)
T = '/workspace/trade'
SG = datetime.timezone(datetime.timedelta(hours=7))
def ms(s): return int(datetime.datetime.fromisoformat(s).timestamp() * 1000)
def hs(m): return datetime.datetime.fromtimestamp(m / 1000, SG).strftime('%m-%d %H:%M')

trades = json.load(open(f'{D}/data/account_userTrades.json'))
orders = json.load(open(f'{D}/data/account_allOrders.json'))
income = json.load(open(f'{D}/data/account_income.json'))
journal = [json.loads(l) for l in open(f'{T}/trade_journal.jsonl') if l.strip()]
logs = []
for l in open(f'{T}/hourly_log.jsonl'):
    try: logs.append(json.loads(l))
    except Exception: pass
out = {}
# fills
fills = []
for s, ts in trades.items():
    byo = {}
    for t in ts: byo.setdefault(t['orderId'], []).append(t)
    for oid, f in byo.items():
        q = sum(float(x['qty']) for x in f); px = sum(float(x['qty']) * float(x['price']) for x in f) / q
        cm = sum(float(x['commission']) for x in f)
        fills.append(dict(sym=s, t=f[0]['time'], side=f[0]['side'], qty=q, px=px, maker=all(x['maker'] for x in f),
                          fee_rate=cm / (q * px), rpnl=sum(float(x['realizedPnl']) for x in f), comm=cm))
fd = pd.DataFrame(fills).sort_values('t')
out['fee_rates'] = dict(taker=round(float(fd[~fd.maker].fee_rate.mean()) * 100, 4), maker=round(float(fd[fd.maker].fee_rate.mean()) * 100, 4) if fd.maker.any() else None,
                        n_taker=int((~fd.maker).sum()), n_maker=int(fd.maker.sum()))
inc = pd.DataFrame(income); inc['income'] = inc.income.astype(float)
out['income_by_type'] = inc.groupby('incomeType').income.sum().round(4).to_dict()
out['net_trading'] = round(float(inc[inc.incomeType.isin(['REALIZED_PNL', 'COMMISSION', 'FUNDING_FEE'])].income.sum()), 4)
# entry slippage: planned mark vs fill for MARKET entries
slip = []
for r in logs:
    for a in r.get('actions') or []:
        if isinstance(a, dict) and a.get('action') == 'enter':
            t0 = ms(r['ts_saigon'].replace('+07:00', '') + ':00+07:00') if len(r['ts_saigon']) == 22 else ms(r['ts_saigon'])
            sd = 'SELL' if a['side'] == 'SHORT' else 'BUY'
            cand = fd[(fd.sym == a['sym']) & (fd.side == sd) & (fd.t >= t0 - 60000) & (fd.t <= t0 + 3 * 3600000)]
            if a['entry'] == 'MARKET' and len(cand):
                f = cand.iloc[0]; sgn = 1 if a['side'] == 'LONG' else -1
                adverse = sgn * (f.px / a['mark'] - 1) * 100
                slip.append(dict(sym=a['sym'], side=a['side'], planned=a['mark'], fill=f.px, delay_s=round((f.t - t0) / 1000), adverse_pct=round(adverse, 3)))
            elif a['entry'] == 'LIMIT':
                f = cand[(cand.px - a['entry_px']).abs() / a['entry_px'] < 0.001]
                slip.append(dict(sym=a['sym'], side=a['side'], limit=a['entry_px'], filled=bool(len(f)), fill_delay_min=round((f.iloc[0].t - t0) / 60000) if len(f) else None))
out['entry_slippage'] = slip
mk = [x['adverse_pct'] for x in slip if 'adverse_pct' in x]
out['entry_slippage_mean_adverse_pct'] = round(float(np.mean(mk)), 3) if mk else None
# exit slippage vs trigger (journal)
ex = []
for j in journal:
    if j['exit_reason'] == 'SL' and j.get('sl'):
        sgn = 1 if j['side'] == 'LONG' else -1
        ex.append(dict(sym=j['symbol'], trigger=j['sl'], fill=j['exit'], adverse_pct=round(-sgn * (j['exit'] / j['sl'] - 1) * 100, 3)))
out['sl_exit_slippage'] = ex
# port fidelity vs live scans
cache = {}
def S(sym):
    if sym not in cache: cache[sym] = Sym(sym)
    return cache[sym]
fid = []
for r in logs:
    try: t = ms(r['ts_saigon'].replace('+07:00', ':00+07:00')) if r['ts_saigon'].count(':') == 2 else ms(r['ts_saigon'])
    except Exception: continue
    for x in r.get('scan_top') or []:
        if not x.get('plan') or x.get('setup') not in ('RETEST', 'BREAKOUT', 'MOMENTUM'): continue
        try: s = S(x['sym'])
        except Exception: continue
        j = int(np.searchsorted(s.ot, t - H1, 'right') - 1)
        if j < 60 or j >= len(s.ot): continue
        k0 = s.i15.get(int(s.ot[j] + H1))
        if k0 is None: continue
        from strategy import scan
        sig = scan(s.pr, j, s.H[k0], s.L[k0], s.V[k0] / 3, s.C[k0], s.is_high, LIVE)
        live_sl = x['plan']['sl']
        fid.append(dict(t=r['ts_saigon'], sym=x['sym'], live=f"{x['setup']} {x['side']}", port=(f"{sig['setup']} {sig['side']}" if sig else 'none/skip'),
                        same=bool(sig and sig['setup'] == x['setup'] and sig['side'] == x['side']),
                        sl_diff_pct=round(abs(sig['sl'] / live_sl - 1) * 100, 3) if sig and sig['setup'] == x['setup'] else None))
fdf = pd.DataFrame(fid)
if len(fdf):
    out['port_fidelity'] = dict(n=len(fdf), same_setup_side=round(float(fdf.same.mean()), 3),
                                median_sl_diff_pct=float(fdf.sl_diff_pct.dropna().median()) if fdf.sl_diff_pct.notna().any() else None,
                                share_sl_within_0_1pct=round(float((fdf.sl_diff_pct.dropna() <= 0.1).mean()), 3) if fdf.sl_diff_pct.notna().any() else None,
                                note='port "none/skip" = port rejected it (rr/limit_far) at :15 price while live saw it at its own scan time; live list includes SKIP_* rows')
# per journal trade analysis
rows = []
seen_levels = {}
for jt in journal:
    sym = jt['symbol']; s = S(sym); t_open = ms(jt['ts_open'].replace('+07:00', ':00+07:00'))
    j = int(np.searchsorted(s.ot, t_open - H1, 'right') - 1)
    atr = float(s.pr.ATR[j]); e = jt['entry']; sl = jt.get('sl')
    sl_atr = abs(e - sl) / atr if sl else None
    note = jt.get('note') or ''
    lvl = None; age = None
    for part in note.split():
        if part.startswith('lvl='): lvl = float(part[4:])
        if part.startswith('brk_age='): age = int(part[8:].rstrip('h'))
    re_entry = bool(lvl and (sym, jt['side'], lvl) in seen_levels)
    if lvl: seen_levels[(sym, jt['side'], lvl)] = 1
    sim = None
    if sl and jt.get('tp2'):
        k0 = int(np.searchsorted(s.ot15, t_open, 'right') - 1)
        sig = dict(side=jt['side'], k0=k0, entry=e, sl=sl, tp1=jt['tp1'], tp2=jt['tp2'], entry_type='MARKET', mark=e)
        r = s.sim_exit(sig, slip_mkt=0.0)
        sim = dict(outcome=r['outcome'], R=round(r['R'], 2), mfe=round(r['mfe'], 2), mae=round(r['mae'], 2)) if r else None
    blocked = []
    if re_entry: blocked.append('a_reentry_block')
    if age is not None and age > 3: blocked.append('b_max_age_3h')
    if age is not None and age > 2: blocked.append('b_max_age_2h')
    for x in (0.75, 1.0, 1.25):
        if sl_atr is not None and sl_atr < x: blocked.append(f'c_min_sl_{x}atr')
    rows.append(dict(open=jt['ts_open'], sym=sym, book=jt['book'], setup=jt['setup'], side=jt['side'], R=jt.get('r_multiple'),
                     pnl_net=jt['pnl_net'], exit=jt['exit_reason'], mfe_r=jt.get('mfe_r'), atr_pct=round(atr / e * 100, 3),
                     sl_pct=jt.get('sl_pct'), sl_atr=round(sl_atr, 2) if sl_atr else None, brk_age=age, re_entry=re_entry,
                     would_block=blocked, backtest_sim_same_plan=sim))
out['journal_analysis'] = rows
pd.set_option('display.width', 250)
print(json.dumps({k: v for k, v in out.items() if k not in ('journal_analysis',)}, indent=1, default=str)[:4000])
print(pd.DataFrame(rows).to_string())
if len(fdf): print(fdf.tail(40).to_string())
json.dump(out, open(f'{R}/live_history.json', 'w'), indent=1, default=str)
