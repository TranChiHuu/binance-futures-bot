"""Shared position management for run_hourly.py and pos_watch.py.
- file lock so the two never act at the same time
- pos_state.json: per-symbol trade meta (written at entry)
- SL->breakeven after TP1, never moves SL farther away
- journal + lessons on close
"""
import os, sys, json, time, math, fcntl, datetime, subprocess
sys.path.insert(0, '/workspace')
from bn import call, pub, BASE

D = '/workspace/trade'
STATE = f'{D}/pos_state.json'
LOCK = f'{D}/.pos.lock'
JOURNAL = f'{D}/trade_journal.jsonl'
LESSONS = f'{D}/lessons.json'
SAIGON = datetime.timezone(datetime.timedelta(hours=7))
CORE = ['BTCUSDT','ETHUSDT','SOLUSDT','BNBUSDT','XRPUSDT','LINKUSDT']
BE_FEE_BUF = 0.0010  # entry + ~round-trip taker fees (0.05%/side measured)

def now_s():
    return datetime.datetime.now(SAIGON).strftime('%Y-%m-%dT%H:%M:%S+07:00')

def fnum(x, d=0.0):
    try: return float(x)
    except Exception: return d

class Lock:
    def __init__(self, blocking=True, timeout=180):
        self.blocking, self.timeout, self.f = blocking, timeout, None
    def __enter__(self):
        self.f = open(LOCK, 'w')
        t0 = time.time()
        while True:
            try:
                fcntl.flock(self.f, fcntl.LOCK_EX | fcntl.LOCK_NB); return True
            except BlockingIOError:
                if not self.blocking or time.time()-t0 > self.timeout:
                    self.f.close(); self.f = None; return False
                time.sleep(2)
    def __exit__(self, *a):
        if self.f:
            fcntl.flock(self.f, fcntl.LOCK_UN); self.f.close()

def load_state():
    try: return json.load(open(STATE))
    except Exception: return {}

def save_state(st):
    tmp = STATE + '.tmp'
    json.dump(st, open(tmp, 'w'), indent=2, ensure_ascii=False); os.replace(tmp, STATE)

def tg(msg, dry=False):
    if dry: return 'dry'
    try:
        p = subprocess.run(['python3', '/workspace/tg_trade.py'], input=msg.encode('utf-8'),
                           capture_output=True, timeout=20)
        return p.stdout.decode().strip()
    except Exception as e:
        return f'err {e}'

def label(sym):
    return 'CORE' if sym in CORE else 'MAO HIEM'

def _err(r):
    return not isinstance(r, (dict, list)) or (isinstance(r, dict) and ('err' in r or 'http' in r or ('code' in r and r.get('code') not in (200, 0))))

def tick_round(x, tick, up):
    if tick <= 0: return x
    n = x / tick
    n = math.ceil(n - 1e-9) if up else math.floor(n + 1e-9)
    dec = max(0, -int(math.floor(math.log10(tick)))) if tick < 1 else 0
    return round(n * tick, dec)

def sym_filters(sym):
    try:
        ex = pub('/fapi/v1/exchangeInfo')
        for s in ex['symbols']:
            if s['symbol'] == sym:
                f = {x['filterType']: x for x in s['filters']}
                return fnum(f['PRICE_FILTER']['tickSize']), fnum(f['LOT_SIZE']['stepSize']), s.get('quantityPrecision', 3)
    except Exception:
        pass
    return 0.0, 0.0, 3

# ---------------- breakeven ----------------
def manage_positions(sc, dry=False, log=print):
    """sc = signed call fn(path, params, method). Returns list of event dicts. Caller holds Lock."""
    events = []
    st = load_state()
    pr = sc('/fapi/v2/positionRisk', {}, 'GET')
    if not isinstance(pr, list):
        return [{'event': 'error', 'res': str(pr)[:200]}]
    algos = sc('/fapi/v1/openAlgoOrders', {}, 'GET')
    algos = algos if isinstance(algos, list) else []
    open_pos = {p['symbol']: p for p in pr if fnum(p.get('positionAmt')) != 0}
    for sym, p in open_pos.items():
        amt = fnum(p['positionAmt']); entry = fnum(p['entryPrice']); mark = fnum(p['markPrice'])
        long_ = amt > 0
        s = st.get(sym)
        if s is None:
            s = {'symbol': sym, 'book': 'CORE' if sym in CORE else 'HIGH', 'side': 'LONG' if long_ else 'SHORT',
                 'entry': entry, 'qty': abs(amt), 'opened_ms': int(time.time()*1000), 'meta_missing': True}
            st[sym] = s
        s.pop('pending_limit', None)
        s['entry'] = entry
        if not s.get('qty'): s['qty'] = abs(amt)
        sa = [a for a in algos if a.get('symbol') == sym]
        sls = [a for a in sa if 'STOP' in str(a.get('orderType', '')).upper() and 'TAKE' not in str(a.get('orderType','')).upper()]
        tps = [a for a in sa if 'TAKE_PROFIT' in str(a.get('orderType', '')).upper()]
        tp1 = s.get('tp1')
        tp1_alive = any(tp1 and abs(fnum(a.get('triggerPrice')) - tp1) <= abs(tp1)*1e-6 + 1e-12 for a in tps) if tp1 else len(tps) >= 2
        half_left = abs(amt) <= s['qty'] * 0.6
        if not s.get('tp1_done') and not tp1_alive and half_left:
            s['tp1_done'] = True; s['tp1_ms'] = int(time.time()*1000)
            events.append({'event': 'TP1', 'sym': sym, 'remaining': abs(amt)})
            tg(f"{label(sym)} {sym} TP1 da khop (~{tp1}). Con lai {abs(amt)}.", dry)
        if s.get('tp1_done') and not s.get('be_done'):
            tick, step, qp = sym_filters(sym)
            be = entry * (1 + BE_FEE_BUF) if long_ else entry * (1 - BE_FEE_BUF)
            be = tick_round(be, tick, up=long_)
            cur = [fnum(a.get('triggerPrice')) for a in sls]
            cur_best = (max(cur) if long_ else min(cur)) if cur else None
            # never move farther: only tighten
            if cur_best is not None and ((long_ and cur_best >= be) or (not long_ and cur_best <= be)):
                s['be_done'] = True
                events.append({'event': 'BE_already', 'sym': sym, 'sl': cur_best})
            elif (long_ and mark <= be) or (not long_ and mark >= be):
                events.append({'event': 'BE_wait_price', 'sym': sym, 'be': be, 'mark': mark})
            else:
                r = sc('/fapi/v1/algoOrder', {
                    'algoType': 'CONDITIONAL', 'symbol': sym, 'side': 'SELL' if long_ else 'BUY',
                    'type': 'STOP_MARKET', 'triggerPrice': be, 'quantity': abs(amt),
                    'reduceOnly': 'true', 'workingType': 'MARK_PRICE'}, 'POST')
                if not _err(r):
                    for a in sls:  # cancel old SL only after new one is live
                        sc('/fapi/v1/algoOrder', {'symbol': sym, 'algoId': a['algoId']}, 'DELETE')
                    s['be_done'] = True; s['sl_be'] = be
                    events.append({'event': 'BE_moved', 'sym': sym, 'be': be, 'old': cur})
                    tg(f"{label(sym)} {sym}: SL da doi ve hoa von {be} cho {abs(amt)} con lai.", dry)
                else:
                    events.append({'event': 'BE_failed', 'sym': sym, 'res': str(r)[:200]})
    # closed trades
    for sym in list(st.keys()):
        if sym in open_pos: continue
        s = st[sym]
        if s.get('pending_limit'):
            # entry limit not filled yet; drop meta if the entry order is gone
            oo = sc('/fapi/v1/openOrders', {'symbol': sym}, 'GET')
            if isinstance(oo, list) and any(o.get('type') == 'LIMIT' for o in oo):
                continue
            if time.time()*1000 - s.get('opened_ms', 0) < 120000:
                continue
            tr = sc('/fapi/v1/userTrades', {'symbol': sym, 'startTime': s['opened_ms']-60000, 'limit': 100}, 'GET')
            if not (isinstance(tr, list) and tr):
                del st[sym]; continue
        ev = close_trade(sc, s, dry, log)
        if ev:
            for a in [a for a in algos if a.get('symbol') == sym]:
                sc('/fapi/v1/algoOrder', {'symbol': sym, 'algoId': a['algoId']}, 'DELETE')
            events.append(ev)
            del st[sym]
    if not dry:
        save_state(st)
    return events

# ---------------- journal ----------------
def excursions(sym, side, entry, t0, t1):
    try:
        k = pub('/fapi/v1/klines', {'symbol': sym, 'interval': '5m', 'startTime': t0, 'endTime': t1, 'limit': 1000})
        hi = max(fnum(x[2]) for x in k); lo = min(fnum(x[3]) for x in k)
        if side == 'LONG': return (lo/entry-1)*100, (hi/entry-1)*100
        return (1-hi/entry)*100, (1-lo/entry)*100
    except Exception:
        return None, None

def close_trade(sc, s, dry=False, log=print):
    sym = s['symbol']
    tr = sc('/fapi/v1/userTrades', {'symbol': sym, 'startTime': max(0, s.get('opened_ms', 0) - 60000), 'limit': 500}, 'GET')
    if not isinstance(tr, list) or not tr:
        return None
    rec = build_record(s, tr)
    if dry:
        return {'event': 'CLOSED_dry', **rec}
    with open(JOURNAL, 'a') as f:
        f.write(json.dumps(rec, ensure_ascii=False) + '\n')
    les = update_lessons()
    why = rec['exit_reason']
    tg(f"{label(sym)} {sym} {rec['side']} da dong ({why}). Exit~{rec['exit']} PnL={rec['pnl_net']:+.2f} USDT "
       f"(giu {rec['held_min']:.0f} phut). Bai hoc moi: {les.get('last_change') or 'khong'}", dry)
    return {'event': 'CLOSED', **rec}

def build_record(s, tr):
    sym = s['symbol']; side = s['side']
    open_side = 'BUY' if side == 'LONG' else 'SELL'
    ins = [t for t in tr if t['side'] == open_side]; outs = [t for t in tr if t['side'] != open_side]
    def vwap(ts): 
        q = sum(fnum(t['qty']) for t in ts); return (sum(fnum(t['price'])*fnum(t['qty']) for t in ts)/q) if q else None
    entry = vwap(ins) or s.get('entry'); exitp = vwap(outs)
    pnl = sum(fnum(t['realizedPnl']) for t in tr); fee = sum(fnum(t['commission']) for t in tr)
    t0 = min(t['time'] for t in ins) if ins else s.get('opened_ms'); t1 = max(t['time'] for t in outs) if outs else int(time.time()*1000)
    mae, mfe = excursions(sym, side, entry, t0, t1)
    sl = s.get('sl'); risk = abs(entry - sl) if sl else None
    reason = 'CLOSE'
    if exitp and sl:
        if s.get('be_done') and s.get('sl_be') and abs(exitp - s['sl_be'])/entry < 0.003: reason = 'BE'
        elif abs(exitp - sl)/entry < 0.003: reason = 'SL'
        elif s.get('tp2') and abs(exitp - s['tp2'])/entry < 0.004: reason = 'TP2'
        elif s.get('tp1_done'): reason = 'TP1+rest'
    return {
        'ts_close': datetime.datetime.fromtimestamp(t1/1000, SAIGON).strftime('%Y-%m-%dT%H:%M+07:00'),
        'ts_open': datetime.datetime.fromtimestamp(t0/1000, SAIGON).strftime('%Y-%m-%dT%H:%M+07:00'),
        'symbol': sym, 'book': s.get('book'), 'setup': s.get('setup', 'UNKNOWN'), 'side': side,
        'entry': round(entry, 8), 'sl': sl, 'tp1': s.get('tp1'), 'tp2': s.get('tp2'),
        'sl_pct': round(risk/entry*100, 3) if risk else None,
        'score': s.get('score'), 'rr_plan': s.get('rr'), 'qty': s.get('qty'),
        'exit': round(exitp, 8) if exitp else None, 'exit_reason': reason,
        'pnl_realized': round(pnl, 4), 'fees': round(fee, 4), 'pnl_net': round(pnl - fee, 4),
        'r_multiple': round(((exitp-entry) if side=='LONG' else (entry-exitp))/risk, 2) if (risk and exitp) else None,
        'held_min': round((t1 - t0)/60000, 1),
        'mae_pct': round(mae, 3) if mae is not None else None, 'mfe_pct': round(mfe, 3) if mfe is not None else None,
        'mfe_r': round(mfe/(risk/entry*100), 2) if (mfe is not None and risk) else None,
        'tp1_done': bool(s.get('tp1_done')), 'note': s.get('note', ''),
    }

# ---------------- lessons (deterministic, recomputed from the whole journal) ----------------
def update_lessons():
    try:
        J = [json.loads(l) for l in open(JOURNAL) if l.strip()]
    except Exception:
        J = []
    J.sort(key=lambda r: r['ts_close'])
    try: old = json.load(open(LESSONS))
    except Exception: old = {}
    adj = {'score_penalty': {}, 'max_sl_pct': {}, 'fee_rate': None}
    lessons = []
    # 1) losing streak per setup type
    for setup in sorted({r['setup'] for r in J}):
        rs = [r for r in J if r['setup'] == setup]; streak = 0
        for r in reversed(rs):
            if r['pnl_net'] < 0: streak += 1
            else: break
        if streak >= 2:
            pen = 0.5 if streak < 3 else 1.0
            adj['score_penalty'][setup] = pen
            lessons.append({'rule': 'setup_losing_streak', 'setup': setup, 'streak': streak,
                            'adjust': f'score -{pen} for {setup}'})
    # 1b) overall losing streak (any setup) >=2 -> -0.25 on everything
    streak = 0
    for r in reversed(J):
        if r['pnl_net'] < 0: streak += 1
        else: break
    if streak >= 2:
        adj['score_penalty']['*'] = 0.25 if streak < 4 else 0.5
        lessons.append({'rule': 'overall_losing_streak', 'streak': streak, 'adjust': f"score -{adj['score_penalty']['*']} all setups"})
    # 2) fast SL on a wide SL -> cap SL% for that book
    for r in J:
        wide = r.get('sl_pct') and r['sl_pct'] >= (2.5 if r['book'] == 'HIGH' else 1.5)
        if r['exit_reason'] == 'SL' and r['held_min'] <= 60 and wide:
            cap = round(r['sl_pct'] * 0.8, 2)
            b = r['book']; adj['max_sl_pct'][b] = min(adj['max_sl_pct'].get(b, 99), cap)
            lessons.append({'rule': 'fast_sl_wide', 'trade': f"{r['symbol']} {r['ts_close']}", 'adjust': f'max SL {cap}% for {b}'})
    # 3) measured fees: if fees > 8% of |loss| on SL trades, raise fee buffer to measured per-side rate
    rates = []
    for r in J:
        if r.get('qty') and r.get('entry') and r.get('exit'):
            notion = r['qty'] * (r['entry'] + r['exit'])
            if notion: rates.append(r['fees'] / notion)
    if rates:
        fr = round(max(rates) * 1.1, 6)
        if fr > 0.0004:
            adj['fee_rate'] = fr
            lessons.append({'rule': 'measured_fee_rate', 'adjust': f'FEE_RATE per side {fr} (was 0.0004)'})
    # 3b) gave back: SL trades that had run >=1R in favour -> cap TP1 distance below that excursion
    gb = [r for r in J if r['exit_reason'] == 'SL' and r.get('mfe_r') is not None and r['mfe_r'] >= 1.0]
    if gb:
        cap = max(1.0, min(1.5, math.floor(min(r['mfe_r'] for r in gb) * 0.9 * 10) / 10))
        adj['tp1_max_r'] = cap
        lessons.append({'rule': 'gave_back_before_tp1', 'trades': [f"{r['symbol']} mfe {r['mfe_r']}R tp1 plan {round(abs(r['tp1']-r['entry'])/abs(r['entry']-r['sl']),2)}R" for r in gb],
                        'adjust': f'TP1 at most {cap}R'})
    # 4) informational: never reached 0.5R before SL
    weak = [r for r in J if r['exit_reason'] == 'SL' and r.get('mfe_r') is not None and r['mfe_r'] < 0.5]
    if len(weak) >= 2:
        lessons.append({'rule': 'no_follow_through', 'count': len(weak),
                        'note': 'SL trades never reached 0.5R: entries late/at extremes; prefer RETEST over chase',
                        'adjust': 'BREAKOUT +0.25 penalty' })
        adj['score_penalty']['BREAKOUT'] = adj['score_penalty'].get('BREAKOUT', 0) + 0.25
    prev = json.dumps(old.get('adjust'), sort_keys=True)
    out = {'updated': now_s(), 'trades': len(J),
           'wins': sum(1 for r in J if r['pnl_net'] > 0), 'losses': sum(1 for r in J if r['pnl_net'] < 0),
           'net_pnl': round(sum(r['pnl_net'] for r in J), 4),
           'adjust': adj, 'lessons': lessons,
           'last_change': None if json.dumps(adj, sort_keys=True) == prev else '; '.join(l['adjust'] for l in lessons)}
    json.dump(out, open(LESSONS, 'w'), indent=2, ensure_ascii=False)
    return out

def load_adjust():
    try: return json.load(open(LESSONS)).get('adjust', {}) or {}
    except Exception: return {}
