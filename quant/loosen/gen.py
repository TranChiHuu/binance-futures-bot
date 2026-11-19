"""2026-11-19 loosening study: raw BREAKOUT candidates mirroring live run_hourly.scan_sym (2026-11-17 version).
Generates every fresh 20h breakout (where live would evaluate BREAKOUT, i.e. no RETEST match first) with all
features needed to apply each filter post-hoc."""
import sys, os, pickle, numpy as np
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from strategy import LIVE, next_target
from engine import Sym, SYMS, H1, M15
from multiprocessing import Pool

def raw(pr, j, f_h, f_l, f_v, mark, is_high, p=LIVE):
    h, l, c, v = pr.h, pr.l, pr.c, pr.v
    phi, plo = pr.PH[j], pr.PL[j]
    brkUP = c[j] > phi; brkDN = c[j] < plo
    if not (brkUP or brkDN): return None
    atr = pr.ATR[j]
    if not atr or atr <= 0: return None
    hi20 = h[j-19:j+1].max(); lo20 = l[j-19:j+1].min()
    hi48 = h[j-47:j+1].max(); lo48 = l[j-47:j+1].min()
    rng = hi20 - lo20
    rng_pos = (mark - lo20)/rng if rng > 0 else 0.5
    avg_v = v[j-19:j+1].sum()/20
    vr = v[j]/avg_v if avg_v else 0
    form_vr = f_v/avg_v if avg_v else 0
    mom3 = (c[j]/c[j-3]-1)*100 if c[j-3] else 0
    t4 = pr.t4[j]
    # RETEST precedence (live checks RETEST first; RETEST is locked so such hours yield nothing)
    N0 = j - 58
    for i in range(58, max(58 - p.lookback, 20), -1):
        g = N0 + i; b = pr.BRK[g]
        if not b: continue
        L = pr.PH[g] if b > 0 else pr.PL[g]
        post_c = c[g+1:j+1]
        if b > 0:
            ext = max(h[g:j+1].max(), f_h); dist = (mark - L)/L
            held = bool(np.all(post_c >= L*(1-p.close_tol)))
            pulled = (ext - L) >= 0.5*atr and (ext - mark) >= 0.4*atr
        else:
            ext = min(l[g:j+1].min(), f_l); dist = (L - mark)/L
            held = bool(np.all(post_c <= L*(1+p.close_tol)))
            pulled = (L - ext) >= 0.5*atr and (mark - ext) >= 0.4*atr
        if -p.close_tol <= dist <= p.zone and held and pulled:
            return None
    side = 'LONG' if brkUP else 'SHORT'; sg = 1 if brkUP else -1
    L = phi if brkUP else plo
    alignDN = t4 == 'DN' and mom3 < 0 and rng_pos < 0.45
    alignUP = t4 == 'UP' and mom3 > 0 and rng_pos > 0.55
    score = 2.5 if ((brkUP and t4 == 'UP') or (brkDN and t4 == 'DN')) else 1.5
    if alignUP or alignDN: score += 1.0
    if vr >= 1.5: score += 1.0
    elif vr >= 1.0: score += 0.5
    if abs(mom3) >= 1.5: score += 0.7
    elif abs(mom3) >= 0.8: score += 0.3
    if form_vr < 0.15: score *= 0.9
    past = sg * (c[j] - L)
    mm = L + (phi-plo) if brkUP else L - (phi-plo)
    if brkUP:
        sl = min(L, l[j-2:j+1].min()) - p.atr_buf*atr; beyond0 = h[j]; levels = [hi48, mm]
    else:
        sl = max(L, h[j-2:j+1].max()) + p.atr_buf*atr; beyond0 = l[j]; levels = [lo48, mm]
    risk = sg * (mark - sl)
    if risk <= 0: return None
    tp1 = mark + sg*1.5*risk
    beyond = max(tp1, beyond0) if brkUP else min(tp1, beyond0)
    tp2s = next_target(side, mark, risk, levels, beyond)
    return dict(side=side, level=float(L), past_atr=float(past/atr), atr=float(atr), mark=float(mark),
                close=float(c[j]), sl=float(sl), tp2_struct=(None if tp2s is None else float(tp2s)),
                score_raw=float(score), rng_pos=float(rng_pos), vr=float(vr), form_vr=float(form_vr), t4=t4)

def gen(sym):
    S = Sym(sym); pr = S.pr; out = []
    for j in range(60, len(S.ot)):
        k0 = S.i15.get(int(S.ot[j] + H1))
        if k0 is None: continue
        r = raw(pr, j, S.H[k0], S.L[k0], S.V[k0]/3, S.C[k0], S.is_high)
        if r:
            r.update(sym=sym, book='HIGH' if S.is_high else 'CORE', j=j, k0=k0, ts=int(S.ot15[k0] + M15))
            out.append(r)
    return sym, out

if __name__ == '__main__':
    with Pool(4) as pool:
        res = dict(pool.map(gen, SYMS))
    pickle.dump(res, open(os.path.join(os.path.dirname(os.path.abspath(__file__)), 'raw.pkl'), 'wb'))
    print({k: len(v) for k, v in res.items()})
