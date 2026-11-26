"""Shared position management for run_hourly.py and pos_watch.py.
- file lock so the two never act at the same time
- pos_state.json: per-symbol trade meta (written at entry)
- SL->breakeven after +1R (before TP1) and after TP1, never moves SL farther away
- journal + lessons on close
- BREAKOUT: flatten if a closed 1h candle closes back through the breakout level
"""
import os, sys, json, time, math, fcntl, datetime, subprocess
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, ROOT+'/trade')
from bn import call, pub, BASE
import breakout_rules as br

D = ROOT+'/trade'
STATE = f'{D}/pos_state.json'
LOCK = f'{D}/.pos.lock'
JOURNAL = f'{D}/trade_journal.jsonl'
LESSONS = f'{D}/lessons.json'
SAIGON = datetime.timezone(datetime.timedelta(hours=7))
CORE = ['BTCUSDT','ETHUSDT','SOLUSDT','BNBUSDT','XRPUSDT','LINKUSDT']
BE_FEE_BUF = 0.0010  # entry + ~round-trip taker fees (0.05%/side measured)
# If mark has already retraced through BE after +1R, place SL this far beyond mark
# so Binance does not reject the stop as immediately triggering (-2021).
NEAR_MARK_BUF = 0.003

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
        p = subprocess.run(['python3', ROOT+'/tg_trade.py'], input=msg.encode('utf-8'),
                           capture_output=True, timeout=20)
        return p.stdout.decode().strip()
    except Exception as e:
        return f'err {e}'

def label(sym):
    return 'CORE' if sym in CORE else 'HIGH'

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


# ---------------- 1R / breakeven helpers ----------------
def r_abs(entry, sl):
    """Initial risk distance R = |entry - initial SL|, or None."""
    try:
        r = abs(float(entry) - float(sl))
    except (TypeError, ValueError):
        return None
    return r if r > 0 else None

def be_trigger(entry, long_, tick, fee_buf=BE_FEE_BUF):
    """Break-even trigger: entry plus fee buffer in the profitable direction, tick-rounded."""
    be = entry * (1 + fee_buf) if long_ else entry * (1 - fee_buf)
    return tick_round(be, tick, up=bool(long_))

def reached_1r(side, entry, sl, fav_px):
    """True if the favorable extreme has reached entry +/- 1R (R = |entry - initial SL|)."""
    r = r_abs(entry, sl)
    if r is None or fav_px is None:
        return False
    if side == 'LONG':
        return fav_px >= entry + r - 1e-12
    return fav_px <= entry - r + 1e-12

def near_mark_sl(long_, mark, tick, mark_buf=NEAR_MARK_BUF):
    """SL just beyond mark so a MARK_PRICE stop will not trigger immediately."""
    if mark is None or mark <= 0:
        return None
    if long_:
        px = tick_round(mark * (1 - mark_buf), tick, up=False)
        if px >= mark:
            px = tick_round(mark - (tick or 0), tick, up=False)
        return px if px < mark else None
    px = tick_round(mark * (1 + mark_buf), tick, up=True)
    if px <= mark:
        px = tick_round(mark + (tick or 0), tick, up=True)
    return px if px > mark else None

def sl_tighter(long_, new_px, cur_sl):
    """True if new_px tightens the stop (or there is no current SL). Never loosens."""
    if new_px is None:
        return False
    if cur_sl is None:
        return True
    if long_:
        return new_px > cur_sl + 1e-12
    return new_px < cur_sl - 1e-12

def sl_at_or_beyond_be(long_, cur_sl, be):
    if cur_sl is None or be is None:
        return False
    return (long_ and cur_sl >= be) or ((not long_) and cur_sl <= be)

def plan_1r_sl(side, entry, sl, fav_px, mark, cur_sl, tick,
               fee_buf=BE_FEE_BUF, mark_buf=NEAR_MARK_BUF):
    """Decide a +1R break-even SL move. Pure function, no I/O.

    Returns dict with action 'skip' | 'already' | 'move', plus be / trigger /
    fallback / kind / reason. kind is 'be' or 'near_mark'.
    """
    long_ = (side == 'LONG')
    be = be_trigger(entry, long_, tick, fee_buf=fee_buf) if entry else None
    out = {'action': 'skip', 'reason': '', 'be': be, 'trigger': None,
           'fallback': None, 'kind': None}
    if not reached_1r(side, entry, sl, fav_px):
        out['reason'] = 'not_1r'
        return out
    if sl_at_or_beyond_be(long_, cur_sl, be):
        out['action'] = 'already'
        out['reason'] = 'sl_already_be'
        return out
    mark_ok_for_be = (mark is not None and
                      ((long_ and mark > be) or ((not long_) and mark < be)))
    near = near_mark_sl(long_, mark, tick, mark_buf=mark_buf)
    if mark_ok_for_be and sl_tighter(long_, be, cur_sl):
        out['action'] = 'move'
        out['trigger'] = be
        out['kind'] = 'be'
        out['reason'] = 'be'
        if sl_tighter(long_, near, cur_sl):
            out['fallback'] = near
        return out
    if sl_tighter(long_, near, cur_sl):
        out['action'] = 'move'
        out['trigger'] = near
        out['kind'] = 'near_mark'
        out['reason'] = 'near_mark'
        return out
    out['reason'] = 'cannot_tighten'
    return out

def favorable_extreme(sym, side, opened_ms, mark):
    """Best favorable price since open: current mark, plus 1m (and 1h if needed) high/low."""
    px = mark
    t0 = int(opened_ms or 0)
    now = int(time.time() * 1000)

    def _apply(kl, cur):
        if not isinstance(kl, list) or not kl:
            return cur
        if side == 'LONG':
            return max(cur, max(fnum(x[2]) for x in kl))
        return min(cur, min(fnum(x[3]) for x in kl))

    try:
        params = {'symbol': sym, 'interval': '1m', 'limit': 1000}
        if t0 > 0:
            params['startTime'] = t0
        px = _apply(pub('/fapi/v1/klines', params), px)
        if t0 and (now - t0) > 1000 * 60 * 1000:
            params = {'symbol': sym, 'interval': '1h', 'limit': 1000, 'startTime': t0}
            px = _apply(pub('/fapi/v1/klines', params), px)
    except Exception:
        pass
    return px

def _immediate_trigger(r):
    blob = str(r).lower()
    if isinstance(r, dict) and r.get('code') == -2021:
        return True
    return 'immediately trigger' in blob or '-2021' in blob

def _place_sl_algo(sc, sym, long_, trigger, amt, close_position=False):
    params = {
        'algoType': 'CONDITIONAL', 'symbol': sym,
        'side': 'SELL' if long_ else 'BUY',
        'type': 'STOP_MARKET', 'triggerPrice': trigger,
        'workingType': 'MARK_PRICE',
    }
    if close_position:
        params['closePosition'] = 'true'
    else:
        params['quantity'] = abs(amt)
        params['reduceOnly'] = 'true'
    return sc('/fapi/v1/algoOrder', params, 'POST')

# ---------------- breakeven ----------------
def last_closed_1h(sym):
    """Return close of the last *closed* 1h candle, or None."""
    try:
        k = pub('/fapi/v1/klines', {'symbol': sym, 'interval': '1h', 'limit': 3})
        if isinstance(k, list) and len(k) >= 2:
            return fnum(k[-2][4])
    except Exception:
        return None
    return None


def _flatten_market(sc, sym, amt, dry=False):
    """Reduce-only market close of the current position size."""
    tick, step, qp = sym_filters(sym)
    qty = abs(amt)
    if step and step > 0:
        qty = math.floor(qty / step + 1e-9) * step
        qty = round(qty, int(qp) if qp is not None else 8)
    if qty <= 0:
        return {'err': 'qty_zero'}
    if dry:
        return {'status': 'DRY_RUN', 'symbol': sym, 'qty': qty}
    return sc('/fapi/v1/order', {
        'symbol': sym, 'side': 'SELL' if amt > 0 else 'BUY',
        'type': 'MARKET', 'quantity': qty, 'reduceOnly': 'true',
    }, 'POST')


def _breakout_lvl_of(s):
    return s.get('breakout_lvl') or br.parse_level(s.get('note'))


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
    # --- maker entries (2026-11-11): cancel unfilled entry + its SL/TP after expire_ms, or if mark passed TP1 before fill ---
    now_ms = int(time.time()*1000)
    for sym in list(st.keys()):
        s = st[sym]
        if not (s.get('maker') and s.get('expire_ms')) or s.get('expiry_done'):
            continue
        why = None
        if s.get('expire_ms') and now_ms >= s['expire_ms']:
            why = 'expired_60m'
        elif sym not in open_pos and s.get('tp1'):
            try:
                mk = fnum(pub('/fapi/v1/premiumIndex', {'symbol': sym}).get('markPrice'))
            except Exception:
                mk = 0
            if mk and ((s['side'] == 'LONG' and mk >= s['tp1']) or (s['side'] == 'SHORT' and mk <= s['tp1'])):
                why = 'mark_passed_tp1'
        if not why:
            continue
        oo = sc('/fapi/v1/openOrders', {'symbol': sym}, 'GET')
        ents = [o for o in (oo if isinstance(oo, list) else []) if o.get('type') == 'LIMIT' and not o.get('reduceOnly')]
        for o in ents:
            sc('/fapi/v1/order', {'symbol': sym, 'orderId': o['orderId']}, 'DELETE')
        if sym in open_pos and not ents:
            s['expiry_done'] = True   # fully filled before expiry: nothing to cancel
            continue
        if sym in open_pos:
            # partial fill: keep SL/TPs (reduceOnly, capped by position), stop treating as pending
            s.pop('pending_limit', None); s['partial_fill'] = True; s['expiry_done'] = True; s['qty'] = abs(fnum(open_pos[sym]['positionAmt']))
            events.append({'event': 'MAKER_PARTIAL_' + why, 'sym': sym, 'filled': s['qty'], 'cancelled': len(ents)})
            continue
        for a_ in [a_ for a_ in algos if a_.get('symbol') == sym]:
            sc('/fapi/v1/algoOrder', {'symbol': sym, 'algoId': a_['algoId']}, 'DELETE')
        # filled-and-closed already? leave journal path to handle; otherwise drop meta
        tr = sc('/fapi/v1/userTrades', {'symbol': sym, 'startTime': max(0, s.get('opened_ms', 0) - 60000), 'limit': 50}, 'GET')
        if isinstance(tr, list) and tr:
            s.pop('pending_limit', None)
            continue
        events.append({'event': 'MAKER_CANCELLED_' + why, 'sym': sym, 'cancelled_entry': len(ents)})
        tg(f"{label(sym)} {sym}: cancelled unfilled maker limit order ({'over 60 min' if why == 'expired_60m' else 'price already past TP1'}), attached SL/TP cancelled too.", dry)
        del st[sym]
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
        # User-placed MANUAL trades: do not adopt/modify SL, TP, or flatten.
        if s.get('setup') == 'MANUAL':
            continue
        sa = [a for a in algos if a.get('symbol') == sym]
        sls = [a for a in sa if 'STOP' in str(a.get('orderType', '')).upper() and 'TAKE' not in str(a.get('orderType','')).upper()]
        tps = [a for a in sa if 'TAKE_PROFIT' in str(a.get('orderType', '')).upper()]
        tp1 = s.get('tp1')
        tp1_alive = any(tp1 and abs(fnum(a.get('triggerPrice')) - tp1) <= abs(tp1)*1e-6 + 1e-12 for a in tps) if tp1 else len(tps) >= 2
        half_left = abs(amt) <= s['qty'] * 0.6
        if not s.get('tp1_done') and not tp1_alive and half_left:
            s['tp1_done'] = True; s['tp1_ms'] = int(time.time()*1000)
            events.append({'event': 'TP1', 'sym': sym, 'remaining': abs(amt)})
            tg(f"{label(sym)} {sym} TP1 filled (~{tp1}). Remaining {abs(amt)}.", dry)
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
                    tg(f"{label(sym)} {sym}: SL moved to breakeven {be} for the remaining {abs(amt)}.", dry)
                else:
                    events.append({'event': 'BE_failed', 'sym': sym, 'res': str(r)[:200]})
        # +1R -> BE (2026-11-26, after SEI TEST gave back 1.3R before TP1). Not MANUAL.
        if (not s.get('be_1r_done') and not s.get('tp1_done') and not s.get('be_done')
                and s.get('sl') is not None):
            tick, step, qp = sym_filters(sym)
            init_sl = fnum(s.get('sl'))
            cur = [fnum(a.get('triggerPrice')) for a in sls]
            cur_best = (max(cur) if long_ else min(cur)) if cur else None
            fav = favorable_extreme(sym, s.get('side') or ('LONG' if long_ else 'SHORT'),
                                    s.get('opened_ms'), mark)
            plan = plan_1r_sl(s.get('side') or ('LONG' if long_ else 'SHORT'),
                              entry, init_sl, fav, mark, cur_best, tick)
            if plan['action'] == 'already':
                s['be_1r_done'] = True
                events.append({'event': 'BE_1R_already', 'sym': sym, 'sl': cur_best, 'be': plan['be']})
            elif plan['action'] == 'move':
                trigger = plan['trigger']
                # reduceOnly+qty, not closePosition: Binance -4130 rejects a second closePosition stop,
                # so we place the new SL first (position stays protected) then cancel the old one.
                r = _place_sl_algo(sc, sym, long_, trigger, amt, close_position=False)
                if _err(r) and _immediate_trigger(r) and plan.get('fallback') is not None:
                    trigger = plan['fallback']
                    r = _place_sl_algo(sc, sym, long_, trigger, amt, close_position=False)
                    plan = dict(plan, kind='near_mark', reason='be_rejected_2021')
                if not _err(r):
                    for a in sls:
                        sc('/fapi/v1/algoOrder', {'symbol': sym, 'algoId': a['algoId']}, 'DELETE')
                    s['be_1r_done'] = True
                    s['sl_be'] = trigger
                    if r.get('algoId') is not None:
                        s['sl_algo'] = r.get('algoId')
                    ev = {'event': 'BE_1R_moved', 'sym': sym, 'sl': trigger,
                          'be': plan['be'], 'kind': plan['kind'], 'old': cur, 'fav': fav}
                    events.append(ev)
                    if plan['kind'] == 'be':
                        tg(f"{label(sym)} {sym}: SL moved to breakeven {trigger} after price reached 1R.", dry)
                    else:
                        tg(f"{label(sym)} {sym}: SL tightened to {trigger} (near mark, not yet breakeven) after price reached 1R.", dry)
                else:
                    events.append({'event': 'BE_1R_failed', 'sym': sym, 'res': str(r)[:200],
                                   'trigger': trigger, 'be': plan['be']})
    # BREAKOUT invalidation: closed 1h back through the breakout level (2026-11-12, AVAX/TIA)
    for sym, p in list(open_pos.items()):
        s = st.get(sym) or {}
        if s.get('setup') and s.get('setup') != 'BREAKOUT':
            continue
        if s.get('tp1_done') or s.get('be_done') or s.get('invalidating'):
            continue
        lvl = _breakout_lvl_of(s)
        if not lvl:
            continue
        # setup missing: only treat as breakout when the entry note recorded past=ATR
        if not s.get('setup') and 'past=' not in str(s.get('note') or ''):
            continue
        last_c = last_closed_1h(sym)
        if last_c is None:
            continue
        side = s.get('side') or ('LONG' if fnum(p['positionAmt']) > 0 else 'SHORT')
        if not br.level_invalidated(side, last_c, lvl, atr=s.get('atr')):
            continue
        amt = fnum(p['positionAmt'])
        r = _flatten_market(sc, sym, amt, dry)
        if _err(r) and not dry:
            events.append({'event': 'INVALID_LEVEL_FAILED', 'sym': sym, 'lvl': lvl, 'close': last_c, 'res': str(r)[:200]})
            continue
        for a in [a for a in algos if a.get('symbol') == sym]:
            sc('/fapi/v1/algoOrder', {'symbol': sym, 'algoId': a['algoId']}, 'DELETE')
        s['exit_hint'] = 'INVALID_LEVEL'
        s['invalidating'] = True
        events.append({'event': 'INVALID_LEVEL', 'sym': sym, 'lvl': lvl, 'close': last_c, 'side': side})
        # fills usually appear immediately; journal this cycle when possible
        if not dry:
            time.sleep(0.8)
            ev = close_trade(sc, s, dry, log)
            if ev:
                events.append(ev)
                if sym in st:
                    del st[sym]
                open_pos.pop(sym, None)
            else:
                st[sym] = s
        else:
            st[sym] = s

    # pending BREAKOUT maker: cancel if the last closed 1h already invalidated the level
    for sym in list(st.keys()):
        if sym in open_pos:
            continue
        s = st[sym]
        if not (s.get('pending_limit') and s.get('setup') == 'BREAKOUT' and s.get('maker') and not s.get('expiry_done')):
            continue
        lvl = _breakout_lvl_of(s)
        if not lvl:
            continue
        last_c = last_closed_1h(sym)
        if last_c is None or not br.level_invalidated(s.get('side'), last_c, lvl, atr=s.get('atr')):
            continue
        oo = sc('/fapi/v1/openOrders', {'symbol': sym}, 'GET')
        ents = [o for o in (oo if isinstance(oo, list) else []) if o.get('type') == 'LIMIT' and not o.get('reduceOnly')]
        for o in ents:
            sc('/fapi/v1/order', {'symbol': sym, 'orderId': o['orderId']}, 'DELETE')
        for a_ in [a_ for a_ in algos if a_.get('symbol') == sym]:
            sc('/fapi/v1/algoOrder', {'symbol': sym, 'algoId': a_['algoId']}, 'DELETE')
        events.append({'event': 'MAKER_CANCELLED_invalid_level', 'sym': sym, 'lvl': lvl, 'close': last_c, 'cancelled_entry': len(ents)})
        tg(f"{label(sym)} {sym}: cancelled maker limit order because a 1h candle closed back through the break level {lvl}.", dry)
        del st[sym]

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
    tg(f"{label(sym)} {sym} {rec['side']} closed ({why}). Exit~{rec['exit']} PnL={rec['pnl_net']:+.2f} USDT "
       f"(held {rec['held_min']:.0f} min). New lesson: {les.get('last_change') or 'none'}", dry)
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
    reason = s.get('exit_hint') or 'CLOSE'
    if reason == 'CLOSE' and exitp and sl:
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
    # 2026-11-20: off-rule TEST trades (user-requested) are journaled but never drive lessons
    # 2026-11-25: user manual trades (setup MANUAL) are journaled but never drive lessons
    J = [r for r in J if r.get('setup') not in ('TEST', 'MANUAL')]
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
    # ratchet: lessons may only tighten -> merge with previous adjust and locked overrides
    srcs = [old.get('adjust') or {}]
    try: srcs.append(json.load(open(f'{D}/lessons_locked.json')).get('adjust', {}) or {})
    except Exception: pass
    for o in srcs:
        for k, v in (o.get('score_penalty') or {}).items():
            adj['score_penalty'][k] = max(float(v), float(adj['score_penalty'].get(k, 0)))
        for k, v in (o.get('max_sl_pct') or {}).items():
            adj['max_sl_pct'][k] = min(float(v), float(adj['max_sl_pct'].get(k, 99)))
        if o.get('fee_rate'):
            adj['fee_rate'] = max(float(o['fee_rate']), float(adj.get('fee_rate') or 0))
        if o.get('tp1_max_r'):
            adj['tp1_max_r'] = min(float(o['tp1_max_r']), float(adj.get('tp1_max_r') or 99))
    kept = [l for l in (old.get('lessons') or []) if l.get('keep')]
    have = {l.get('rule') for l in lessons}
    for l in kept:
        if l.get('rule') not in have:
            lessons.append(l)
    prev = json.dumps(old.get('adjust'), sort_keys=True)
    out = {'updated': now_s(), 'trades': len(J),
           'wins': sum(1 for r in J if r['pnl_net'] > 0), 'losses': sum(1 for r in J if r['pnl_net'] < 0),
           'net_pnl': round(sum(r['pnl_net'] for r in J), 4),
           'adjust': adj, 'lessons': lessons,
           'daily_reviews': (old.get('daily_reviews') or [])[-30:],  # kept from daily self-review (2026-11-20)
           'last_change': None if json.dumps(adj, sort_keys=True) == prev else '; '.join(l['adjust'] for l in lessons)}
    json.dump(out, open(LESSONS, 'w'), indent=2, ensure_ascii=False)
    return out

def load_adjust():
    try: return json.load(open(LESSONS)).get('adjust', {}) or {}
    except Exception: return {}
