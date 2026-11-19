"""2026-11-19 loosening study. Live-faithful BREAKOUT filter chain + maker GTX entry at breakout close
(fill only if a 15m bar trades through by 0.01% within 60 min), TP1 1.5R 50% -> SL to BE(+0.10%), TP2 rest (cap 4R),
flatten on a closed 1h back through level (0.05 ATR) before TP1, fees maker 0.02% in / taker 0.05% out,
SL slippage 0.02%, funding. R denominator = risk + 2*0.055% entry (live sizing).
Portfolio: per-symbol one at a time, 2 CORE + 2 HIGH slots, static corr groups same side, US-data blackout."""
import sys, os, json, pickle, itertools, numpy as np, pandas as pd
Q = os.path.dirname(os.path.dirname(os.path.abspath(__file__))); sys.path.insert(0, Q)
from engine import Sym, SYMS, H1, M15, in_blackout, CORR_GROUPS
from metrics import stats
HERE = os.path.dirname(os.path.abspath(__file__))
MAKER, TAKER, FEE_R = 0.0002, 0.0005, 0.00055
PEN = 0.75   # lessons.json: BREAKOUT 0.5 + all 0.25
raw = pickle.load(open(f'{HERE}/raw.pkl', 'rb'))
syms = {s: Sym(s) for s in SYMS}
END = max(int(S.ot15[-1]) + M15 for S in syms.values())
START = END - 730 * 86400000

def plan(s, V):
    """Apply the live filter chain under variant V. Returns maker plan or None."""
    side = s['side']; sg = 1 if side == 'LONG' else -1; atr = s['atr']; mark = s['mark']; sl = s['sl']
    tp2 = s['tp2_struct']
    if tp2 is None:
        if not V['atr_tgt']: return None                     # SKIP_no_structure_target
        if V['atr_tgt'] > 0: tp2 = mark + sg * V['atr_tgt'] * atr       # ATR fallback target
        else: tp2 = mark + sg * (-V['atr_tgt']) * sg * (mark - sl)      # R-multiple fallback target
    if s['past_atr'] < V['depth']: return None               # SKIP_breakout_shallow
    if s['book'] == 'HIGH':
        if s['past_atr'] > V['high_chase']: return None      # SKIP_chase_atr (live measures past from close)
    else:
        if side == 'SHORT' and s['rng_pos'] < V['core_chase']: return None
        if side == 'LONG' and s['rng_pos'] > 1 - V['core_chase']: return None
    risk_m = sg * (mark - sl)
    if sg * (tp2 - mark) / risk_m < 2.0: return None         # SKIP_rr_weak (plan at mark)
    if s['form_vr'] < 0.08 and s['vr'] < 0.8: return None    # SKIP_thin_liq
    thr = V['high_min'] if s['book'] == 'HIGH' else V['core_min']
    if s['score_raw'] - PEN < thr: return None               # score after lesson penalty
    lim = s['close']
    if (side == 'LONG' and lim >= mark) or (side == 'SHORT' and lim <= mark): return None  # maker would cross
    risk = sg * (lim - sl)
    if risk <= 0: return None
    tp1 = lim + sg * 1.5 * risk
    cap4 = lim + sg * 4 * risk
    tp2 = min(tp2, cap4) if side == 'LONG' else max(tp2, cap4)
    if sg * (tp2 - tp1) <= 0: return None
    rew = sg * (tp2 - lim) - lim * MAKER - tp2 * TAKER; rsk = risk + lim * MAKER + sl * TAKER
    if rew / rsk < 2.0: return None                          # SKIP_maker_rr_net_lt2
    if risk / atr < 1.0: return None                         # A3 SL >= 1 ATR
    if abs(lim / mark - 1) > 0.008: return None              # limit > 0.8% from mark
    if (side == 'SHORT' and mark <= tp1) or (side == 'LONG' and mark >= tp1): return None
    return dict(entry=lim, sl=sl, tp1=tp1, tp2=tp2)

_cache = {}
def sim(s, pl):
    key = (s['sym'], s['j'], round(pl['tp2'], 12))
    if key in _cache: return _cache[key]
    S = syms[s['sym']]; H, L, C, ot = S.H, S.L, S.C, S.ot15; n = len(H)
    side = s['side']; sg = 1 if side == 'LONG' else -1
    e, sl, tp1, tp2 = pl['entry'], pl['sl'], pl['tp1'], pl['tp2']
    lvl = s['level']; tol = 0.05 * s['atr']; k0 = s['k0']
    kf = None
    for k in range(k0 + 1, min(k0 + 5, n)):
        if (side == 'LONG' and L[k] <= e * (1 - 0.0001)) or (side == 'SHORT' and H[k] >= e * (1 + 0.0001)):
            kf = k; break
    if kf is None:
        r = dict(outcome='NOFILL', t_free=int(ot[min(k0 + 4, n - 1)] + M15)); _cache[key] = r; return r
    rpu = sg * (e - sl); den = rpu + e * FEE_R * 2
    parts = []; stage = 0; be = e * (1 + sg * 0.0010); k = kf; outcome = None
    end = min(n, kf + 96 * 45)
    while k < end:
        hk, lk = H[k], L[k]
        if stage == 0:
            if (side == 'LONG' and lk <= sl) or (side == 'SHORT' and hk >= sl):
                parts.append((1.0, sl * (1 - sg * 0.0002), k, TAKER)); outcome = 'SL'; break
            if k > kf and ((side == 'LONG' and hk >= tp1) or (side == 'SHORT' and lk <= tp1)):
                parts.append((0.5, tp1, k, TAKER)); stage = 1; k += 1; continue
            # hourly scan right after an hour close: flatten if closed 1h back through level
            if k > kf and ot[k] % H1 == 0:
                c1 = C[k - 1]
                if (side == 'LONG' and c1 < lvl - tol) or (side == 'SHORT' and c1 > lvl + tol):
                    parts.append((1.0, C[k] * (1 - sg * 0.0005), k, TAKER)); outcome = 'INVALID'; break
        else:
            if (side == 'LONG' and lk <= be) or (side == 'SHORT' and hk >= be):
                parts.append((0.5, be * (1 - sg * 0.0002), k, TAKER)); outcome = 'TP1_BE'; break
            if (side == 'LONG' and hk >= tp2) or (side == 'SHORT' and lk <= tp2):
                parts.append((0.5, tp2, k, TAKER)); outcome = 'TP1_TP2'; break
        k += 1
    if outcome is None:
        rem = 1.0 - sum(p[0] for p in parts); parts.append((rem, C[end - 1], end - 1, TAKER)); outcome = 'OPEN_END'
    gross = sum(fr * sg * (px - e) for fr, px, _, _ in parts)
    fees = e * MAKER + sum(fr * px * f for fr, px, _, f in parts)
    fund = 0.0; last = int(ot[kf]); rem = 1.0
    for fr, px, kk, _ in parts:
        tt = int(ot[kk] + M15); fund += -sg * rem * S.funding_sum(last, tt) * e; rem -= fr; last = tt
    kx = parts[-1][2]
    r = dict(outcome=outcome, R=(gross - fees + fund) / den, t_fill=int(ot[kf]), t_exit=int(ot[kx] + M15), t_free=int(ot[kx] + M15))
    _cache[key] = r; return r

def run(V, start=START):
    cands = []
    for sym, L_ in raw.items():
        for s in L_:
            if s['ts'] < start or in_blackout(s['ts']): continue
            pl = plan(s, V)
            if pl: cands.append((s['ts'], s, pl))
    cands.sort(key=lambda x: (x[0], -x[1]['score_raw']))
    open_ = []; busy = {}; trades = []; nofill = 0
    for ts, s, pl in cands:
        open_ = [o for o in open_ if o['t_free'] > ts]
        if busy.get(s['sym'], 0) > ts: continue
        bk = s['book']
        if sum(o['book'] == bk for o in open_) >= 2 or len(open_) >= 4: continue
        if any(o['side'] == s['side'] and any(s['sym'] in g and o['sym'] in g for g in CORR_GROUPS) for o in open_): continue
        r = sim(s, pl)
        busy[s['sym']] = r['t_free']
        open_.append(dict(book=bk, side=s['side'], sym=s['sym'], t_free=r['t_free']))
        if r['outcome'] == 'NOFILL': nofill += 1; continue
        trades.append(dict(sym=s['sym'], book=bk, ts=ts, **r))
    return trades, nofill

def summ(tr, months):
    st = stats(tr)
    if st.get('n', 0) == 0: return dict(n=0, per_month=0)
    return dict(n=st['n'], per_month=round(st['n'] / months, 2), win=round(st['win'] * 100, 1), avgR=round(st['avgR'], 3),
                sumR=round(st['sumR'], 1), pf=round(st['pf'], 2), maxdd=round(st['maxdd'], 1))

BASE = dict(depth=0.75, atr_tgt=0.0, high_chase=1.5, core_chase=0.12, core_min=3.5, high_min=2.5)
if __name__ == '__main__':
    grid = []
    for depth, atr_tgt, chase, sc in itertools.product([0.75, 0.6, 0.5, 0.4], [0.0, 3.0, -3.0], ['strict', 'relaxed'], [0.0, 0.5]):
        V = dict(BASE, depth=depth, atr_tgt=atr_tgt, core_min=3.5 - sc, high_min=2.5 - sc)
        if chase == 'relaxed': V.update(high_chase=2.0, core_chase=0.05)
        tg = {0.0:'struct',3.0:'tgtATR3',-3.0:'tgt3R'}[atr_tgt]
        name = f"d{depth}|{tg}|{chase}|sc-{sc}"
        grid.append((name, V))
    out = {}
    for name, V in grid:
        tr, nf = run(V)
        r = dict(V=V, all=summ(tr, 730 / 30.44), nofill=nf,
                 d90=summ([t for t in tr if t['ts'] >= END - 90 * 86400000], 90 / 30.44),
                 d30=summ([t for t in tr if t['ts'] >= END - 30 * 86400000], 1.0),
                 by_book={b: summ([t for t in tr if t['book'] == b], 730 / 30.44) for b in ('CORE', 'HIGH')},
                 outcomes=pd.Series([t['outcome'] for t in tr]).value_counts().to_dict() if tr else {},
                 first_year=summ([t for t in tr if t['ts'] < START + 365 * 86400000], 12), second_year=summ([t for t in tr if t['ts'] >= START + 365 * 86400000], 12))
        out[name] = r
        a = r['all']; d = r['d90']; m = r['d30']
        print(f"{name:34s} n={a['n']:4d} /mo={a['per_month']:5.2f} win={a.get('win')}% avgR={a.get('avgR')} PF={a.get('pf')} DD={a.get('maxdd')}R sumR={a.get('sumR')} | 90d n={d['n']} avgR={d.get('avgR')} sumR={d.get('sumR')} | 30d n={m['n']} sumR={m.get('sumR')} | Y1 {r['first_year'].get('avgR')} Y2 {r['second_year'].get('avgR')}", flush=True)
    json.dump(out, open(f'{HERE}/results.json', 'w'), indent=1, default=str)
    print('END', pd.to_datetime(END, unit='ms') + pd.Timedelta(hours=7))
