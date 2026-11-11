"""Event-driven backtest engine for the live strategy (see strategy.py for the ported signal logic).
- signals: hourly scan on every symbol (scan ~ :15 of each hour, see DIFFERENCES)
- exits: simulated on 15m bars, pessimistic (SL before TP when both inside one 15m bar)
- TP1 50% -> SL to entry +-0.10% (pos_manage BE_FEE_BUF) from the next 15m bar; TP2 rest
- fees 0.055%/side, slippage, funding (8h history)
- portfolio: 1 pos per book, 2 total, pending limit occupies slot, one entry per scan,
  daily stop -7.5 USDT realized (Saigon day), HIGH room rule, US-data blackout, static corr groups,
  sizing CORE 2 USDT / HIGH 15% wallet, lev<=10/20 auto-lowered for liq beyond SL."""
import os, datetime, numpy as np, pandas as pd
from strategy import P, LIVE, Prep, scan, final_score, CORE, HIGH
D = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'data')
H1, M15, H4 = 3600000, 900000, 4*3600000
SG = 7*3600000
SYMS = CORE + HIGH

class Sym:
    def __init__(self, sym):
        self.sym = sym; self.is_high = sym in HIGH
        d1 = pd.read_parquet(f'{D}/{sym}_1h.parquet')
        d15 = pd.read_parquet(f'{D}/{sym}_15m.parquet')
        self.ot = d1['ot'].values.astype(np.int64)
        h, l, c, v = (d1[x].values for x in 'hlcv')
        # 4h trend: closed 4h bars (UTC aligned), close > SMA20 of closes
        end = self.ot + H1
        m4 = (end % H4) == 0
        c4 = pd.Series(c[m4], index=end[m4])
        sma = c4.rolling(20).mean()
        up = (c4 > sma).where(sma.notna())
        t4 = np.array(['-'] * len(c), dtype=object)
        k_end = (end // H4) * H4           # last closed 4h bar end at scan time
        s = up.reindex(k_end).values
        t4[s == True] = 'UP'; t4[s == False] = 'DN'
        self.pr = Prep(h, l, c, v, t4)
        self.ot15 = d15['ot'].values.astype(np.int64)
        self.H, self.L, self.C, self.V = (d15[x].values for x in 'hlcv')
        self.i15 = {t: i for i, t in enumerate(self.ot15)}
        try:
            f = pd.read_parquet(f'{D}/{sym}_funding.parquet')
            self.ft = f['fundingTime'].values.astype(np.int64); self.fr = f['fundingRate'].values
        except Exception:
            self.ft = np.array([], dtype=np.int64); self.fr = np.array([])
        self.fcs = np.concatenate([[0], np.cumsum(self.fr)])

    def funding_sum(self, t0, t1):
        a = np.searchsorted(self.ft, t0, 'right'); b = np.searchsorted(self.ft, t1, 'right')
        return self.fcs[b] - self.fcs[a]

    def signals(self, p=LIVE, vol_frac=1/3):
        out = []
        pr = self.pr
        for j in range(60, len(self.ot)):
            k0 = self.i15.get(int(self.ot[j] + H1))
            if k0 is None: continue
            mark = self.C[k0]
            sig = scan(pr, j, self.H[k0], self.L[k0], self.V[k0]*vol_frac, mark, self.is_high, p)
            if sig:
                sig.update(sym=self.sym, book='HIGH' if self.is_high else 'CORE', j=j, k0=k0,
                           ts=int(self.ot15[k0] + M15))
                out.append(sig)
        return out

    def sim_exit(self, sig, tp1_cap=1.5, fee=0.00055, slip_mkt=0.0005, slip_sl=0.0002,
                 limit_bars=8, be_buf=0.0010, max_bars=96*45):
        """Returns trade dict in R units (1R = planned risk incl. 2x fee buffer, as live sizing)."""
        side = sig['side']; sgn = 1 if side == 'LONG' else -1
        k0 = sig['k0']; n = len(self.H)
        e_plan = sig['entry']; sl = sig['sl']; tp2 = sig['tp2']
        rpu = abs(e_plan - sl)
        tp1 = sig['tp1']
        if abs(tp1 - e_plan) / rpu > tp1_cap: tp1 = e_plan + sgn * tp1_cap * rpu
        H, L = self.H, self.L
        if sig['entry_type'] == 'MARKET':
            fill = e_plan * (1 + sgn * slip_mkt); kf = k0 + 1; start_tp = kf
        else:
            if (side == 'SHORT' and sig['mark'] <= tp1) or (side == 'LONG' and sig['mark'] >= tp1):
                return None  # live guard: mark already past TP1 for pending limit
            kf = None
            for k in range(k0 + 1, min(k0 + 1 + limit_bars, n)):
                if (side == 'SHORT' and H[k] >= e_plan * 1.0002) or (side == 'LONG' and L[k] <= e_plan * 0.9998):
                    kf = k; break
            if kf is None:
                return dict(outcome='NOFILL', t_free=int(self.ot15[min(k0 + limit_bars, n-1)] + M15))
            fill = e_plan; start_tp = kf + 1
        risk_den = rpu + e_plan * fee * 2
        end = min(n, kf + max_bars)
        hs, ls = H[kf:end], L[kf:end]
        if side == 'LONG':
            sl_hit = ls <= sl; tp1_hit = hs >= tp1; tp2_hit = hs >= tp2
        else:
            sl_hit = hs >= sl; tp1_hit = ls <= tp1; tp2_hit = ls <= tp2
        tp1_hit = tp1_hit.copy(); tp2_hit = tp2_hit.copy()
        off = start_tp - kf
        tp1_hit[:off] = False; tp2_hit[:off] = False
        a = np.argmax(sl_hit) if sl_hit.any() else 10**9
        b = np.argmax(tp1_hit) if tp1_hit.any() else 10**9
        # MFE/MAE before exit (R)
        def fin(parts, kx, reason):
            # parts: list of (frac, price, t_ms)
            t_fill = int(self.ot15[kf]); t_exit = int(self.ot15[min(kx, n-1)] + M15)
            gross = sum(fr * sgn * (px - fill) for fr, px, _ in parts)
            fees = fill * fee + sum(fr * px * fee for fr, px, _ in parts)
            fund = 0.0; last = t_fill; rem = 1.0
            for fr, px, tt in sorted(parts, key=lambda x: x[2]):
                fund += -sgn * rem * self.funding_sum(last, tt) * fill; rem -= fr; last = tt
            seg_h = H[kf:min(kx, n-1)+1]; seg_l = L[kf:min(kx, n-1)+1]
            mfe = (seg_h.max() - fill) / rpu if side == 'LONG' else (fill - seg_l.min()) / rpu
            mae = (fill - seg_l.min()) / rpu if side == 'LONG' else (seg_h.max() - fill) / rpu
            return dict(outcome=reason, fill=fill, t_fill=t_fill, t_exit=t_exit, t_free=t_exit,
                        R=(gross - fees + fund) / risk_den, R_gross=gross / risk_den,
                        fee_R=fees / risk_den, fund_R=fund / risk_den, mfe=float(mfe), mae=float(mae),
                        tp1_px=tp1, rpu=rpu, risk_den=risk_den, hold_h=(t_exit - t_fill) / H1, amb=bool(a == b and a < 10**9))
        sl_px = sl * (1 - sgn * slip_sl)
        if a == 10**9 and b == 10**9:
            return fin([(1.0, self.C[end-1], int(self.ot15[end-1] + M15))], end-1, 'OPEN_END')
        if a <= b:
            return fin([(1.0, sl_px, int(self.ot15[kf+a] + M15))], kf + a, 'SL')
        kb = kf + b
        be = fill * (1 + sgn * be_buf)
        t_tp1 = int(self.ot15[kb] + M15)
        hs2, ls2 = H[kb:end], L[kb:end]
        if side == 'LONG':
            be_hit = ls2 <= be; t2 = hs2 >= tp2
        else:
            be_hit = hs2 >= be; t2 = ls2 <= tp2
        be_hit = be_hit.copy(); be_hit[0] = False
        c_ = np.argmax(be_hit) if be_hit.any() else 10**9
        d_ = np.argmax(t2) if t2.any() else 10**9
        if c_ == 10**9 and d_ == 10**9:
            return fin([(0.5, tp1, t_tp1), (0.5, self.C[end-1], int(self.ot15[end-1] + M15))], end-1, 'TP1_OPEN_END')
        if c_ <= d_:
            return fin([(0.5, tp1, t_tp1), (0.5, be * (1 - sgn * slip_sl), int(self.ot15[kb+c_] + M15))], kb + c_, 'TP1_BE')
        return fin([(0.5, tp1, t_tp1), (0.5, tp2, int(self.ot15[kb+d_] + M15))], kb + d_, 'TP1_TP2')

def saigon_day(ms): return (ms + SG) // 86400000
def in_blackout(ms):
    dt = datetime.datetime.utcfromtimestamp((ms + SG) / 1000)
    if dt.weekday() >= 5: return False
    m = dt.hour * 60 + dt.minute
    return 1170 <= m <= 1260 or 1290 <= m <= 1350

CORR_GROUPS = [{'BTCUSDT','ETHUSDT','SOLUSDT','BNBUSDT'}, {'ARBUSDT','OPUSDT'}, {'DOGEUSDT','1000PEPEUSDT','WIFUSDT'}]

def symbol_level(syms, sigs_by_sym, p=LIVE, tp1_cap=None, filt=None, sim_kw=None, cache=None):
    """Per-symbol independent study: take every passing signal when that symbol is flat (no portfolio caps)."""
    tp1_cap = p.tp1_max_r if tp1_cap is None else tp1_cap
    sim_kw = sim_kw or {}
    trades = []
    for sym, sigs in sigs_by_sym.items():
        S = syms[sym]; busy_until = 0; traded_levels = set()
        for s in sigs:
            if s['ts'] < busy_until: continue
            if final_score(s, p) < (p.high_min if S.is_high else p.core_min): continue
            if in_blackout(s['ts']): continue
            if filt and not filt(s, traded_levels): continue
            key = (sym, s['j'], s['entry'], s['sl'], tp1_cap, p.fee, tuple(sorted(sim_kw.items())))
            r = cache.get(key) if cache is not None else None
            if r is None:
                r = S.sim_exit(s, tp1_cap=tp1_cap, fee=p.fee, **sim_kw)
                if cache is not None: cache[key] = r
            if r is None: continue
            busy_until = r['t_free']
            if r['outcome'] == 'NOFILL': continue
            if s['setup'] != 'MOMENTUM': traded_levels.add((s['side'], round(s['level'], 10)))
            trades.append({**{k: s[k] for k in ('sym','book','setup','side','ts','entry','sl','tp2','rr','score_raw','entry_type','atr','brk_age')},
                           'level': s.get('level'), 'score': final_score(s, p), **r})
    return trades

def portfolio(syms, sigs_by_sym, p=LIVE, wallet0=45.0, filt=None, corr_rule='groups', corr_fn=None,
              sim_kw=None, cache=None, tp1_cap=None, daily_stop=-7.5, core_risk=2.0, high_pct=0.15, high_fixed=None,
              risk_pct_both=None, daily_mult=None, filters=None):
    """Full live-rules portfolio replay. Returns (trades, equity list)."""
    tp1_cap = p.tp1_max_r if tp1_cap is None else tp1_cap
    sim_kw = sim_kw or {}
    by_ts = {}
    for sym, sigs in sigs_by_sym.items():
        for s in sigs:
            if final_score(s, p) < (p.high_min if s['book'] == 'HIGH' else p.core_min): continue
            by_ts.setdefault(s['ts'], []).append(s)
    wallet = wallet0; open_ = {}  # book -> trade
    realized = {}; trades = []; equity = []; traded_levels = set()
    for ts in sorted(by_ts):
        # close finished trades
        for bk in list(open_):
            t = open_[bk]
            if t['t_free'] <= ts:
                if t['outcome'] != 'NOFILL':
                    wallet += t['pnl']; d = saigon_day(t['t_exit'])
                    realized[d] = realized.get(d, 0) + t['pnl_gross']
                    trades.append(t); equity.append((t['t_exit'], wallet))
                del open_[bk]
        if wallet < 5: break
        rt = realized.get(saigon_day(ts), 0.0)
        ds = daily_stop if not daily_mult else max(daily_stop, -daily_mult * wallet * risk_pct_both)
        if rt <= ds or len(open_) >= 2 or in_blackout(ts): continue
        room = max(0.0, abs(ds) + rt)
        cands = sorted(by_ts[ts], key=lambda s: -final_score(s, p))
        for s in cands:
            bk = s['book']
            if bk in open_: continue
            if filt and not filt(s, traded_levels): continue
            if risk_pct_both:
                budget = wallet * risk_pct_both
                if room < budget: continue
            else:
                budget = core_risk if bk == 'CORE' else (high_fixed if high_fixed else wallet * high_pct)
                if bk == 'HIGH' and room < budget: continue
                if bk == 'CORE' and room < core_risk: continue
            blocked = False
            for t in open_.values():
                if t['side'] != s['side']: continue
                if corr_rule == 'groups':
                    blocked |= any(s['sym'] in g and t['sym'] in g for g in CORR_GROUPS)
                elif corr_rule == 'rolling' and corr_fn is not None:
                    blocked |= corr_fn(s['sym'], t['sym'], ts) > 0.8
                elif corr_rule == 'groups+rolling' and corr_fn is not None:
                    blocked |= any(s['sym'] in g and t['sym'] in g for g in CORR_GROUPS) or corr_fn(s['sym'], t['sym'], ts) > 0.8
            if blocked: continue
            S = syms[s['sym']]
            e = s['entry']; rpu = abs(e - s['sl']); sl_dist = rpu / e
            lev_max = 10 if bk == 'CORE' else 20
            lev = max(1, min(lev_max, int(0.9 / (sl_dist + 0.01))))
            fee_buf = e * p.fee * 2
            qty = budget / (rpu + fee_buf)
            avail = wallet - sum(t['margin'] for t in open_.values())
            qty = min(qty, max(0, avail * 0.85 * lev / e))
            if filters and s['sym'] in filters:
                stp, mq, mn = filters[s['sym']]
                qty = np.floor(qty / stp + 1e-9) * stp
                if qty < mq or qty * e < mn or np.floor(qty / 2 / stp + 1e-9) < 1: continue
            elif qty * e < 5: continue
            liq = e * (1 - 1/lev + 0.01) if s['side'] == 'LONG' else e * (1 + 1/lev - 0.01)
            if (s['side'] == 'LONG' and liq >= s['sl']) or (s['side'] == 'SHORT' and liq <= s['sl']): continue
            key = (s['sym'], s['j'], s['entry'], s['sl'], tp1_cap, p.fee, tuple(sorted(sim_kw.items())))
            r = cache.get(key) if cache is not None else None
            if r is None:
                r = S.sim_exit(s, tp1_cap=tp1_cap, fee=p.fee, **sim_kw)
                if cache is not None: cache[key] = r
            if r is None: continue
            risk_usdt = qty * (rpu + fee_buf)
            t = {**{k: s[k] for k in ('sym','book','setup','side','ts','entry','sl','tp2','rr','entry_type','brk_age')},
                 'level': s.get('level'), 'score': final_score(s, p), **r, 'qty': qty, 'lev': lev,
                 'margin': qty * e / lev, 'risk_usdt': risk_usdt}
            if r['outcome'] != 'NOFILL':
                t['pnl'] = r['R'] * risk_usdt; t['pnl_gross'] = r['R_gross'] * risk_usdt
                if s['setup'] != 'MOMENTUM': traded_levels.add((s['side'], round(s['level'], 10)))
            open_[bk] = t
            break
    for t in open_.values():
        if t['outcome'] != 'NOFILL':
            wallet += t['pnl']; trades.append(t); equity.append((t['t_exit'], wallet))
    return trades, equity
