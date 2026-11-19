import sys, os; sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from bt import *
from collections import Counter
def why(s, V):
    side = s['side']; sg = 1 if side == 'LONG' else -1; atr = s['atr']; mark = s['mark']; sl = s['sl']
    tp2 = s['tp2_struct']
    if tp2 is None:
        if not V['atr_tgt']: return 'no_struct'
        tp2 = mark + sg * V['atr_tgt'] * atr
    if s['past_atr'] < V['depth']: return 'shallow'
    if s['book'] == 'HIGH':
        if s['past_atr'] > V['high_chase']: return 'chase_atr'
    else:
        if side == 'SHORT' and s['rng_pos'] < V['core_chase']: return 'chase_sup'
        if side == 'LONG' and s['rng_pos'] > 1 - V['core_chase']: return 'chase_res'
    risk_m = sg * (mark - sl)
    if sg * (tp2 - mark) / risk_m < 2.0: return 'rr_weak'
    if s['form_vr'] < 0.08 and s['vr'] < 0.8: return 'thin'
    thr = V['high_min'] if s['book'] == 'HIGH' else V['core_min']
    if s['score_raw'] - PEN < thr: return 'score'
    lim = s['close']
    if (side == 'LONG' and lim >= mark) or (side == 'SHORT' and lim <= mark): return 'maker_cross'
    risk = sg * (lim - sl)
    tp1 = lim + sg * 1.5 * risk; cap4 = lim + sg * 4 * risk
    tp2 = min(tp2, cap4) if side == 'LONG' else max(tp2, cap4)
    if sg * (tp2 - tp1) <= 0: return 'tp2_in_tp1'
    rew = sg * (tp2 - lim) - lim * MAKER - tp2 * TAKER; rsk = risk + lim * MAKER + sl * TAKER
    if rew / rsk < 2.0: return 'rr_net'
    if risk / atr < 1.0: return 'sl_lt_1atr'
    if abs(lim / mark - 1) > 0.008: return 'limit_far'
    if (side == 'SHORT' and mark <= tp1) or (side == 'LONG' and mark >= tp1): return 'past_tp1'
    return 'OK'
for nm, V in [('base', BASE), ('atr3', dict(BASE, atr_tgt=3.0)), ('all_loose', dict(BASE, depth=0.0, atr_tgt=3.0, high_chase=99, core_chase=0, core_min=0, high_min=0))]:
    c = Counter(why(s, V) for L_ in raw.values() for s in L_ if s['ts'] >= START)
    print(nm, c.most_common())
