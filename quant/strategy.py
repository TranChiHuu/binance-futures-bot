"""Faithful port of scan_sym() from /workspace/trade/run_hourly.py (version of 2026-11-11 04:59),
rewritten index-based on numpy arrays so it can run over 2 years of history.

run_hourly.py executes its whole body at import (account calls, lock, orders), so it cannot be
imported safely. Every formula below mirrors the live code line by line; differences are listed in
DIFFERENCES (also copied into REPORT.md)."""
from dataclasses import dataclass, asdict
import numpy as np

CORE = ['BTCUSDT','ETHUSDT','SOLUSDT','BNBUSDT','XRPUSDT','LINKUSDT']
HIGH = ['DOGEUSDT','SUIUSDT','AVAXUSDT','1000PEPEUSDT','WIFUSDT','NEARUSDT','APTUSDT',
        'INJUSDT','TIAUSDT','SEIUSDT','WLDUSDT','ARBUSDT','OPUSDT']

DIFFERENCES = [
    "Gia: dung gia last (klines) thay cho mark price; SL/TP live kich hoat theo MARK_PRICE.",
    "Thoi diem quet: gia 'mark' = close nen 15m dau tien cua gio moi (~:15); volume nen dang chay = 1/3 volume nen 15m do (gia lap quet luc ~:05 nhu live).",
    "Xu huong 4h tinh tu nen 1h gop lai (moc 4h UTC nhu Binance).",
    "Khong mo phong tickSize/stepSize; minNotional 5 USDT.",
    "lessons.json mo phong o dang tinh hien tai (RETEST -0.5, tat ca -0.25, TP1<=1.5R, phi 0.055%), khong tu cap nhat theo chuoi thang/thua.",
    "Danh sach nhom tuong quan tinh (BTC/ETH/SOL/BNB, ARB/OP, DOGE/PEPE/WIF) giong live.",
]

@dataclass(frozen=True)
class P:
    core_min: float = 3.5
    high_min: float = 2.5
    atr_buf: float = 0.4          # RETEST_ATR_BUF (also breakout SL buffer)
    zone: float = 0.008           # RETEST_ZONE
    lookback: int = 8             # RETEST_LOOKBACK = max breakout age for retest
    close_tol: float = 0.003
    market_zone: float = 0.003
    max_limit_dist: float = 0.008
    high_chase_atr: float = 1.5
    pen_retest: float = 0.5       # lessons.json score_penalty
    pen_all: float = 0.25
    tp1_max_r: float = 1.5
    fee: float = 0.00055
    def d(self): return asdict(self)

LIVE = P()

class Prep:
    """Per-symbol precomputed arrays (global index g = 1h bar)."""
    def __init__(self, h, l, c, v, t4):
        self.h, self.l, self.c, self.v, self.t4 = h, l, c, v, t4
        n = len(c)
        self.PH = np.full(n, np.nan); self.PL = np.full(n, np.nan); self.BVR = np.zeros(n)
        for g in range(20, n):
            self.PH[g] = h[g-20:g].max(); self.PL[g] = l[g-20:g].min()
            s = v[g-20:g].sum(); self.BVR[g] = v[g] / (s/20) if s else 0
        self.BRK = np.zeros(n, dtype=np.int8)
        with np.errstate(invalid='ignore'):
            self.BRK[c > self.PH] = 1
            self.BRK[(c < self.PL) & (self.BRK == 0)] = -1
        tr = np.zeros(n)
        tr[1:] = np.maximum.reduce([h[1:]-l[1:], np.abs(h[1:]-c[:-1]), np.abs(l[1:]-c[:-1])])
        cs = np.concatenate([[0], np.cumsum(tr)])
        self.ATR = np.zeros(n)
        self.ATR[14:] = (cs[15:] - cs[1:-14]) / 14   # mean of TR[g-13..g]

def next_target(side, entry, risk, levels, beyond):
    if side == 'LONG':
        cands = sorted(x for x in levels if x and x > beyond * 1.001)
        tp2 = cands[0] if cands else None
        cap = entry + 4.0 * risk
        return min(tp2, cap) if tp2 else None
    cands = sorted((x for x in levels if x and x < beyond * 0.999), reverse=True)
    tp2 = cands[0] if cands else None
    cap = entry - 4.0 * risk
    return max(tp2, cap) if tp2 else None

def scan(pr, j, f_h, f_l, f_v, mark, is_high, p=LIVE):
    """Scan with last closed 1h bar = global index j (live uses 59 closed bars). Returns dict or None."""
    h, l, c, v = pr.h, pr.l, pr.c, pr.v
    hi20 = h[j-19:j+1].max(); lo20 = l[j-19:j+1].min()
    hi48 = h[j-47:j+1].max(); lo48 = l[j-47:j+1].min()
    phi, plo = pr.PH[j], pr.PL[j]
    brkUP = c[j] > phi; brkDN = c[j] < plo
    atr = pr.ATR[j]
    rng = hi20 - lo20
    rng_pos = (mark - lo20)/rng if rng > 0 else 0.5
    avg_v = v[j-19:j+1].sum()/20
    vr = v[j]/avg_v if avg_v else 0
    form_vr = f_v/avg_v if avg_v else 0
    mom3 = (c[j]/c[j-3]-1)*100 if c[j-3] else 0
    t4 = pr.t4[j]
    thr = p.high_min if is_high else p.core_min
    N0 = j - 58  # global index of local 0
    brks = []
    lo_i = max(58 - p.lookback, 20)
    for i in range(58, lo_i, -1):
        g = N0 + i
        b = pr.BRK[g]
        if b:
            brks.append((('LONG' if b > 0 else 'SHORT'), (pr.PH[g] if b > 0 else pr.PL[g]), g, pr.PH[g], pr.PL[g], pr.BVR[g]))
    score = 0.0; side = None; setup = None; plan = None; decision = 'SKIP'; extra = {}
    alignDN = t4 == 'DN' and mom3 < 0 and rng_pos < 0.45
    alignUP = t4 == 'UP' and mom3 > 0 and rng_pos > 0.55

    def mk_plan(side, entry, sl, tp1_hint, levels, et, beyond):
        risk = (entry - sl) if side == 'LONG' else (sl - entry)
        if risk <= 0: return None, 'bad_risk'
        tp1 = entry + 1.5*risk if side == 'LONG' else entry - 1.5*risk
        if tp1_hint:
            r_h = (tp1_hint-entry)/risk if side == 'LONG' else (entry-tp1_hint)/risk
            if 1.0 <= r_h <= 2.0: tp1 = tp1_hint
        tp2 = next_target(side, entry, risk, levels, max(tp1, beyond) if side == 'LONG' else min(tp1, beyond))
        if tp2 is None: return None, 'no_structure_target'
        rr = (tp2-entry)/risk if side == 'LONG' else (entry-tp2)/risk
        return dict(side=side, entry=entry, entry_type=et, sl=sl, tp1=tp1, tp2=tp2, rr=round(rr, 2)), None

    for (s, L, bi, rh, rl, bvr) in brks:
        if setup is not None: break
        mm = L + (rh-rl) if s == 'LONG' else L - (rh-rl)
        post_c = c[bi+1:j+1]
        if s == 'LONG':
            ext = max(h[bi:j+1].max(), f_h)
            dist = (mark - L)/L
            held = bool(np.all(post_c >= L*(1-p.close_tol)))
            pulled = (ext - L) >= 0.5*atr and (ext - mark) >= 0.4*atr
            if -p.close_tol <= dist <= p.zone and held and pulled:
                setup = 'RETEST'; side = 'LONG'
                swing = min(l[bi+1:j+1].min(), f_l) if bi+1 <= j else f_l
                sl = min(swing, L) - p.atr_buf*atr
                if abs(dist) <= p.market_zone: entry, et = mark, 'MARKET'
                else: entry, et = L*1.0005, 'LIMIT'
                plan, why = mk_plan('LONG', entry, sl, ext, [hi48, mm], et, ext)
        else:
            ext = min(l[bi:j+1].min(), f_l)
            dist = (L - mark)/L
            held = bool(np.all(post_c <= L*(1+p.close_tol)))
            pulled = (L - ext) >= 0.5*atr and (mark - ext) >= 0.4*atr
            if -p.close_tol <= dist <= p.zone and held and pulled:
                setup = 'RETEST'; side = 'SHORT'
                swing = max(h[bi+1:j+1].max(), f_h) if bi+1 <= j else f_h
                sl = max(swing, L) + p.atr_buf*atr
                if abs(dist) <= p.market_zone: entry, et = mark, 'MARKET'
                else: entry, et = L*0.9995, 'LIMIT'
                plan, why = mk_plan('SHORT', entry, sl, ext, [lo48, mm], et, ext)
        if setup == 'RETEST':
            score = 2.5
            if (side == 'LONG' and t4 == 'UP') or (side == 'SHORT' and t4 == 'DN'): score += 1.0
            elif not is_high: score -= 0.5
            if bvr >= 1.5: score += 0.5
            elif bvr >= 1.0: score += 0.25
            score += 0.5
            if abs(dist) <= p.market_zone: score += 0.3
            if plan is None: decision = f'SKIP_retest_{why}'
            extra = dict(level=float(L), brk_g=int(bi), brk_age=int(j-bi), dist=float(dist), bvr=float(bvr))

    if setup is None and (brkUP or brkDN):
        setup = 'BREAKOUT'
        side = 'LONG' if brkUP else 'SHORT'
        L = phi if brkUP else plo
        if (brkUP and t4 == 'UP') or (brkDN and t4 == 'DN'): score += 2.5
        else: score += 1.5
        if alignUP or alignDN: score += 1.0
        if vr >= 1.5: score += 1.0
        elif vr >= 1.0: score += 0.5
        if abs(mom3) >= 1.5: score += 0.7
        elif abs(mom3) >= 0.8: score += 0.3
        past = (mark - L) if side == 'LONG' else (L - mark)
        mm = L + (phi-plo) if side == 'LONG' else L - (phi-plo)
        if side == 'LONG':
            sl = min(L, l[j-2:j+1].min()) - p.atr_buf*atr
            plan, why = mk_plan('LONG', mark, sl, None, [hi48, mm], 'MARKET', h[j])
        else:
            sl = max(L, h[j-2:j+1].max()) + p.atr_buf*atr
            plan, why = mk_plan('SHORT', mark, sl, None, [lo48, mm], 'MARKET', l[j])
        extra = dict(level=float(L), brk_g=int(j), brk_age=0, past_atr=float(past/atr) if atr else 0)
        if plan is None: decision = f'SKIP_{why}'
        elif is_high:
            if past > p.high_chase_atr*atr: decision = 'SKIP_chase_atr'
        else:
            if side == 'SHORT' and rng_pos < 0.12: decision = 'SKIP_chase_support'
            if side == 'LONG' and rng_pos > 0.88: decision = 'SKIP_chase_resist'

    if setup is None and is_high and vr >= 1.5 and (
            (t4 == 'UP' and mom3 > 0.5 and rng_pos > 0.5) or (t4 == 'DN' and mom3 < -0.5 and rng_pos < 0.5)):
        setup = 'MOMENTUM'
        side = 'LONG' if t4 == 'UP' else 'SHORT'
        score = 1.0 + 1.0 + (0.7 if abs(mom3) >= 1.5 else 0.3)
        if vr >= 2.5: score += 0.3
        if side == 'LONG':
            sl = l[j-4:j+1].min() - 0.3*atr
            plan, why = mk_plan('LONG', mark, sl, None, [hi20, hi48], 'MARKET', mark)
        else:
            sl = h[j-4:j+1].max() + 0.3*atr
            plan, why = mk_plan('SHORT', mark, sl, None, [lo20, lo48], 'MARKET', mark)
        extra = dict(level=None, brk_g=-1, brk_age=-1)
        if plan is None: decision = f'SKIP_mom_{why}'

    if setup is None:
        return None
    if form_vr < 0.15:
        score *= 0.9
    rr = plan['rr'] if plan else None
    if decision == 'SKIP':
        if rr is None or rr < 2.0: decision = 'SKIP_rr_weak'
        elif plan['entry_type'] == 'LIMIT' and abs(plan['entry']/mark-1) > p.max_limit_dist: decision = 'SKIP_limit_far'
        elif form_vr < 0.08 and vr < 0.8: decision = 'SKIP_thin_liq'
        else: decision = 'OK'   # score threshold applied later (after lesson penalties), as live
    if decision != 'OK':
        return None
    return dict(setup=setup, score_raw=float(score), thr=thr, atr=float(atr), mark=float(mark),
                t4=t4, vr=float(vr), mom3=float(mom3), rng_pos=float(rng_pos), **plan, **extra)

def final_score(sig, p=LIVE):
    pen = (p.pen_retest if sig['setup'] == 'RETEST' else 0.0) + p.pen_all
    return sig['score_raw'] - pen
