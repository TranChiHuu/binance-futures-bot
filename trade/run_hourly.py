#!/usr/bin/env python3
"""One-shot hourly crypto futures scan + position check for holic coder."""
import os, sys, json, time, math, datetime
from urllib.error import HTTPError
sys.path.insert(0, '/workspace')
from bn import call, pub, BASE

CORE = ['BTCUSDT','ETHUSDT','SOLUSDT','BNBUSDT','XRPUSDT','LINKUSDT']
HIGH = ['DOGEUSDT','SUIUSDT','AVAXUSDT','1000PEPEUSDT','WIFUSDT','NEARUSDT','APTUSDT',
        'INJUSDT','TIAUSDT','SEIUSDT','WLDUSDT','ARBUSDT','OPUSDT']
SAIGON = datetime.timezone(datetime.timedelta(hours=7))
NOW = datetime.datetime.now(SAIGON)
TS = NOW.strftime('%Y-%m-%dT%H:%M+07:00')
DAY = NOW.strftime('%Y-%m-%d')
DAY_MS = int(datetime.datetime(NOW.year, NOW.month, NOW.day, tzinfo=SAIGON).timestamp()*1000)
DAILY_STOP_HARD = -7.5     # hard cap (USDT realized, Saigon day)
DAILY_STOP = DAILY_STOP_HARD  # tightened below once wallet is known (A7)
RISK_PCT = 0.01            # A4 2026-11-11: risk per trade = 1% of live walletBalance, BOTH books
DAILY_STOP_RISK_MULT = 3.0 # A7: daily stop = max(-7.5, -3 x per-trade risk)
MIN_SL_ATR = 1.0           # A3: skip candidates whose SL distance < 1.0 x ATR(14,1h)
# --- 2026-11-11 (t50u): maker entries + wallet-adaptive risk ---
MAKER_ENTRY = True         # BREAKOUT: post-only (GTX) limit at the breakout candle close; never chase with market
MAKER_FEE = 0.0002         # measured maker fee (entry)
TAKER_FEE = 0.0005         # measured taker fee (SL / TP market)
MAKER_EXPIRE_MIN = 60      # cancel unfilled maker entry (+ its SL/TP) after 60 min (pos_manage/pos_watch enforce)
FEE_RATE = 0.0004  # est taker round-trip buffer for SL loss
# --- 2026-11-10: retest mode + looser HIGH-RISK filter ---
CORE_SCORE_MIN = 3.5
HIGH_SCORE_MIN = 2.5
RETEST_LOOKBACK = 8        # closed 1h candles to look back for a real 20h break
RETEST_ZONE = 0.008        # mark within 0.8% of broken level on breakout side
RETEST_CLOSE_TOL = 0.003   # no close back through level by more than 0.3%
MARKET_ZONE = 0.003        # market entry if within 0.3% of level, else limit at level
MAX_LIMIT_DIST = 0.008     # limit never more than 0.8% from mark (hard cap 1.5% for stale cancel)
RETEST_ATR_BUF = 0.4       # SL buffer beyond swing/level, x ATR(1h,14)
HIGH_CHASE_ATR = 1.5       # HIGH: skip breakout only if > 1.5 ATR past level
DRY = '--dry-run' in sys.argv
sys.path.insert(0, '/workspace/trade')
import pos_manage as pm
_LOCK = pm.Lock(blocking=True, timeout=240)
if not _LOCK.__enter__():
    print(json.dumps({'ok': False, 'err': 'pos lock busy >240s'})); sys.exit(2)
LESSON_ADJ = pm.load_adjust()
if LESSON_ADJ.get('fee_rate'):
    FEE_RATE = max(FEE_RATE, float(LESSON_ADJ['fee_rate']))
dry_writes = []

_rate_limited = False

def _is_ban(r):
    if not isinstance(r, dict):
        return False
    blob = str(r)
    return r.get('http') == 418 or 'banned until' in blob or 'Way too many requests' in blob or r.get('code') == -1003

def safe_call(path, params=None, method='GET'):
    global _rate_limited
    if _rate_limited:
        return {'err': 'rate_limited', 'http': 418}
    if DRY and method in ('POST','DELETE','PUT'):
        dry_writes.append({'method': method, 'path': path, 'params': params})
        return {'orderId': 'DRY_RUN', 'algoId': 'DRY_RUN', 'status': 'DRY_RUN'}
    try:
        time.sleep(0.12)
        r = call(BASE, path, params or {}, method)
        if _is_ban(r):
            _rate_limited = True
        if isinstance(r, dict) and ('http' in r or 'err' in r or 'code' in r):
            return r
        return r
    except Exception as e:
        return {'err': str(e)}

def safe_pub(path, params=None):
    global _rate_limited
    if _rate_limited:
        return {'err': 'rate_limited', 'http': 418}
    try:
        time.sleep(0.15)
        r = pub(path, params or {})
        if _is_ban(r):
            _rate_limited = True
        return r
    except Exception as e:
        msg = str(e)
        if '418' in msg or 'banned' in msg or 'Too many' in msg:
            _rate_limited = True
        return {'err': msg}

def fnum(x, d=0.0):
    try: return float(x)
    except: return d

auth_error = None
actions = []
skips = []
changed = False
telegram_sent = False
telegram_message = None
telegram_error = None

# --- 1) Account snapshot ---
acct = safe_call('/fapi/v2/account')
if isinstance(acct, dict) and ('err' in acct or 'http' in acct or 'code' in acct) and 'assets' not in acct:
    auth_error = str(acct)
    print('AUTH/ACCOUNT ERROR', auth_error)
    out = {
        'ts_saigon': TS, 'walletBalance': None, 'availableBalance': None,
        'realizedPnL_today': None, 'positions': 0, 'openOrders': 0, 'openAlgo': 0,
        'actions': [], 'skips': [f'API/account error: {auth_error}'],
        'telegram_sent': False, 'auth_error': auth_error, 'changed': True,
        'skip_summary': 'API auth/account error — abort scan',
    }
    # try telegram for API error
    msg = f"LOI API Binance\n{auth_error}\nKhong quet/dat lenh. Kiem tra key."
    if DRY:
        print(json.dumps(out, ensure_ascii=False, indent=2)); sys.exit(1)
    try:
        import subprocess
        p = subprocess.run(['python3','/workspace/tg_trade.py'], input=msg.encode('utf-8'),
                           capture_output=True, timeout=20)
        out['telegram_sent'] = b'True' in p.stdout or p.stdout.strip()==b'true' or 'true' in p.stdout.decode().lower()
        out['telegram_message'] = msg
        telegram_sent = out['telegram_sent']
    except Exception as e:
        out['telegram_error'] = str(e)
        out['telegram_message'] = msg
    open('/workspace/trade/last_run.json','w').write(json.dumps(out, ensure_ascii=False, indent=2))
    with open('/workspace/trade/hourly_log.jsonl','a') as f:
        f.write(json.dumps(out, ensure_ascii=False)+'\n')
    print(json.dumps(out, ensure_ascii=False, indent=2))
    sys.exit(1)

wallet = fnum(acct.get('totalWalletBalance'))
avail = fnum(acct.get('availableBalance'))
upnl = fnum(acct.get('totalUnrealizedProfit'))

pos_raw = safe_call('/fapi/v2/positionRisk')
positions = []
if isinstance(pos_raw, list):
    for p in pos_raw:
        amt = fnum(p.get('positionAmt'))
        if amt != 0:
            sym = p['symbol']
            port = 'CORE' if sym in CORE else ('HIGH' if sym in HIGH else 'OTHER')
            positions.append({
                'symbol': sym, 'port': port, 'amt': amt, 'entry': fnum(p.get('entryPrice')),
                'mark': fnum(p.get('markPrice')), 'upnl': fnum(p.get('unRealizedProfit')),
                'lev': int(fnum(p.get('leverage'))), 'marginType': p.get('marginType'),
                'liq': fnum(p.get('liquidationPrice')),
            })

orders = safe_call('/fapi/v1/openOrders')
if not isinstance(orders, list): orders = []
algos = safe_call('/fapi/v1/openAlgoOrders')
if not isinstance(algos, list):
    # some accounts return dict with error if empty endpoint quirk
    if isinstance(algos, dict) and 'err' not in algos and 'http' not in algos:
        algos = algos.get('orders', []) if isinstance(algos.get('orders'), list) else []
    else:
        if isinstance(algos, dict) and ('err' in algos or 'http' in algos):
            # try alternate
            algos2 = safe_call('/fapi/v1/openAlgoOrders', {})
            algos = algos2 if isinstance(algos2, list) else []
        else:
            algos = []

# income today
inc = safe_call('/fapi/v1/income', {'incomeType':'REALIZED_PNL','startTime':DAY_MS,'limit':1000})
comm = safe_call('/fapi/v1/income', {'incomeType':'COMMISSION','startTime':DAY_MS,'limit':1000})
realized = sum(fnum(i.get('income')) for i in inc) if isinstance(inc, list) else 0.0
commission = sum(fnum(i.get('income')) for i in comm) if isinstance(comm, list) else 0.0

import risk_tier as _rt
RISK_STATE = _rt.compute(wallet, _rt.load_state())   # adaptive tier (DD from peak, scale-up after >=100/200 trades)
if not DRY:
    _rt.save(RISK_STATE)
RISK_PCT = RISK_STATE['risk_pct']
risk_budget_trade = wallet * RISK_PCT          # same % for CORE and HIGH (per-book old caps applied at sizing)
high_budget = risk_budget_trade                 # kept name for logging/back-compat
DAILY_STOP = max(DAILY_STOP_HARD, -DAILY_STOP_RISK_MULT * risk_budget_trade)  # A7 (tightening only)
remaining_daily = DAILY_STOP - realized  # e.g. -7.5 - 0 = -7.5; usable risk = 7.5 + realized if realized negative
# daily stop if realized <= -7.5
blocked_daily = realized <= DAILY_STOP
# remaining risk budget before hitting stop: how much more loss allowed
room = abs(DAILY_STOP) + realized  # if realized=0, room=7.5; if realized=-3, room=4.5
if room < 0: room = 0

core_pos = [p for p in positions if p['port']=='CORE']
high_pos = [p for p in positions if p['port']=='HIGH']
core_free = len(core_pos) == 0
high_free = len(high_pos) == 0
pos_used = len([p for p in positions if p['port'] in ('CORE','HIGH')])
slots_ok = pos_used < 2
# fix 2026-11-11: a pending (non-reduceOnly) entry LIMIT occupies its book's slot, so no duplicate entries
_pend_syms = {o.get('symbol') for o in orders if o.get('type')=='LIMIT' and not o.get('reduceOnly') and o.get('symbol') not in {p['symbol'] for p in positions}}
for _s in _pend_syms:
    if _s in CORE: core_free = False
    elif _s in HIGH: high_free = False
pos_used += len([_s for _s in _pend_syms if _s in CORE or _s in HIGH])
slots_ok = pos_used < 2
if _pend_syms: skips.append(f"Pending entries occupy slots: {sorted(_pend_syms)}")

skips.append(f"Book: {len(positions)} pos, {len(orders)} orders, {len(algos)} algo; wallet={wallet:.8f} avail={avail:.8f}")
skips.append(f"Saigon day {DAY} realizedPnL_today={realized:.4f} commission={commission:.4f} — daily stop {'HIT' if blocked_daily else 'OK'} (limit {DAILY_STOP:.2f}, room={room:.2f})")
skips.append(f"Risk tier {RISK_STATE['tier']}: {RISK_PCT*100:.2f}% wallet={risk_budget_trade:.4f} USDT (peak {RISK_STATE['peak']:.2f}, DD {RISK_STATE['dd']*100:.1f}%, post-change trades {RISK_STATE['journal']['n']}); daily stop={DAILY_STOP:.4f} (hard cap {DAILY_STOP_HARD}); min SL {MIN_SL_ATR}xATR; maker entry {'ON' if MAKER_ENTRY else 'OFF'}")

# --- Fix SL/TP / stale pendings ---
# exchangeInfo for filters
exinfo = safe_pub('/fapi/v1/exchangeInfo')
sym_info = {}
if isinstance(exinfo, dict) and 'symbols' in exinfo:
    for s in exinfo['symbols']:
        if s['symbol'] in CORE+HIGH:
            filters = {f['filterType']: f for f in s['filters']}
            sym_info[s['symbol']] = {
                'status': s['status'],
                'pricePrecision': s.get('pricePrecision'),
                'quantityPrecision': s.get('quantityPrecision'),
                'tickSize': fnum(filters.get('PRICE_FILTER',{}).get('tickSize'), 0.01),
                'stepSize': fnum(filters.get('LOT_SIZE',{}).get('stepSize'), 0.001),
                'minQty': fnum(filters.get('LOT_SIZE',{}).get('minQty'), 0),
                'minNotional': fnum(filters.get('MIN_NOTIONAL',{}).get('notional'), 5),
            }
# confirm HIGH symbols exist
valid_high = [s for s in HIGH if s in sym_info and sym_info[s]['status']=='TRADING']
missing_high = [s for s in HIGH if s not in sym_info or sym_info[s]['status']!='TRADING']
if missing_high:
    skips.append(f"HIGH delisted/missing skipped silently: {missing_high}")

# --- position management (shared with pos_watch.py): TP1 -> SL breakeven, closed -> journal + lessons ---
pm_events = pm.manage_positions(lambda path, params=None, method='GET': safe_call(path, params or {}, method), dry=DRY)
for e in pm_events:
    actions.append({'action': 'pm_' + str(e.get('event')), **{k: v for k, v in e.items() if k != 'event'}})
if pm_events:
    skips.append(f"pos_manage events: {[e.get('event') for e in pm_events]}")
    LESSON_ADJ = pm.load_adjust()
# US data blackout: major US releases are typically 20:30-22:30 Asia/Saigon (08:30-10:00 ET) on weekdays.
# This hour (early Asia morning) is outside that window; FOMC 14:00 ET = 02:00+1 VN is handled if we are in 01:30-02:30.
wd_name = NOW.strftime('%A')
hour = NOW.hour
minute = NOW.minute
mins = hour * 60 + minute
within_30_us = False
# Conservative weekday windows in Asia/Saigon around typical US print times:
# 20:00-21:00 (around 08:30 ET) and 21:30-22:30 (around 10:00 ET)
if NOW.weekday() < 5:
    if (20 * 60 - 30) <= mins <= (20 * 60 + 60):  # 19:30-21:00
        within_30_us = True
    if (22 * 60 - 30) <= mins <= (22 * 60 + 30):  # 21:30-22:30
        within_30_us = True
    if (2 * 60 - 30) <= mins <= (2 * 60 + 30):  # 01:30-02:30 FOMC-ish
        # only treat as blackout if it's a scheduled FOMC day; default off at this hour unless flagged
        pass
if within_30_us:
    us_data = f"{wd_name} {NOW.strftime('%H:%M')} Asia/Saigon — possible US data window, no new entries"
    skips.append(us_data)
else:
    us_data = f"{wd_name} {NOW.strftime('%H:%M')} Asia/Saigon — outside typical major US data windows"
    skips.append(us_data)

# Cancel stale LIMIT pendings >1.5% from mark or >2h
marks = {}
for o in orders:
    sym = o['symbol']
    if o.get('type') == 'LIMIT' and o.get('reduceOnly') in (False, 'false', None):
        # entry pending
        if sym not in marks:
            pi = safe_pub('/fapi/v1/premiumIndex', {'symbol': sym})
            marks[sym] = fnum(pi.get('markPrice')) if isinstance(pi, dict) else 0
        mk = marks[sym]
        px = fnum(o.get('price'))
        age_h = (time.time()*1000 - fnum(o.get('time'))) / 3600000
        dist = abs(px - mk) / mk * 100 if mk else 999
        if dist > 1.5:
            r = safe_call('/fapi/v1/order', {'symbol':sym,'orderId':o['orderId']}, 'DELETE')
            actions.append({'action':'cancel_stale_limit_far','sym':sym,'dist_pct':round(dist,2),'orderId':o['orderId'],'res':str(r)[:120]})
            changed = True
        elif age_h > 2 or within_30_us or (o.get('timeInForce') == 'GTX' and age_h * 60 >= MAKER_EXPIRE_MIN):
            r = safe_call('/fapi/v1/order', {'symbol':sym,'orderId':o['orderId']}, 'DELETE')
            actions.append({'action':('cancel_maker_60m' if o.get('timeInForce') == 'GTX' and not within_30_us else ('cancel_stale_limit_2h' if age_h > 2 else 'cancel_limit_us_data')),'sym':sym,'age_h':round(age_h,2),'orderId':o['orderId'],'res':str(r)[:120]})
            changed = True
        else:
            continue
        # entry cancelled: drop its SL/TP algos too if no position on this symbol
        if not any(p['symbol']==sym for p in positions):
            for a_ in [a_ for a_ in algos if a_.get('symbol')==sym and a_.get('algoId')]:
                ra = safe_call('/fapi/v1/algoOrder', {'symbol':sym,'algoId':a_['algoId']}, 'DELETE')
                actions.append({'action':'cancel_orphan_algo','sym':sym,'algoId':a_['algoId'],'res':str(ra)[:120]})

# Position SL/TP check
for p in positions:
    sym = p['symbol']
    # algos for this sym
    sa = [a for a in algos if a.get('symbol')==sym]
    has_sl = any(a.get('orderType')=='STOP_MARKET' or a.get('type')=='STOP_MARKET' or
                 str(a.get('algoType','')).upper()=='CONDITIONAL' and 'STOP' in str(a.get('orderType',a.get('type',''))).upper()
                 for a in sa)
    # also check regular orders
    so = [o for o in orders if o.get('symbol')==sym]
    has_sl = has_sl or any(o.get('type')=='STOP_MARKET' for o in so)
    tps = [a for a in sa if 'TAKE_PROFIT' in str(a.get('orderType',a.get('type',''))).upper()]
    tps += [o for o in so if 'TAKE_PROFIT' in str(o.get('type','')).upper()]
    n_tp = len(tps)
    if not has_sl:
        skips.append(f"WARN {sym}: missing SL — needs manual/fix (no auto invent SL without structure)")
        # Don't invent SL blindly; note for telegram if we can't fix
    if n_tp < 2 and abs(p['amt']) > 0:
        skips.append(f"WARN {sym}: only {n_tp} TP level(s) — should be 2; fix if data allows")

if not positions and not orders and not algos:
    skips.append("Book flat: 0 positions, 0 open orders, 0 algo orders; no SL/TP fix or stale pending")

# Watchers
import subprocess
watch_check = subprocess.run(['bash','-lc',"ps aux | grep -E '/workspace/(xrp|arb|sol|inj|arb_tp)_watch' | grep -v grep || true"],
                             capture_output=True, text=True)
watchers_running = bool(watch_check.stdout.strip())
if watchers_running:
    skips.append(f"Watchers running: {watch_check.stdout.strip()[:200]}")
else:
    skips.append("No trade watchers running (xrp/arb/sol/inj idle)")

# --- 2) SCAN ---
def ema(arr, n):
    if len(arr) < n: return arr[-1] if arr else 0
    k = 2/(n+1)
    e = sum(arr[:n])/n
    for x in arr[n:]:
        e = x*k + e*(1-k)
    return e

def floor_step(q, step):
    """Round quantity DOWN to stepSize exactly (Decimal, no float round-up)."""
    from decimal import Decimal, ROUND_FLOOR
    if step <= 0 or q <= 0: return max(q, 0.0)
    d = Decimal(str(step))
    return float((Decimal(repr(q)) / d).to_integral_value(rounding=ROUND_FLOOR) * d)

def round_step(q, step):
    if step <= 0: return q
    precision = max(0, -int(math.floor(math.log10(step)))) if step < 1 else 0
    return math.floor(q / step) * step

def atr_calc(h, l, c, n=14):
    trs = []
    for i in range(1, len(c)):
        trs.append(max(h[i]-l[i], abs(h[i]-c[i-1]), abs(l[i]-c[i-1])))
    if not trs: return 0
    return sum(trs[-n:]) / min(n, len(trs))

def next_target(side, entry, risk, levels, beyond):
    """Nearest structure level beyond `beyond` (TP1 / break extreme) in trade direction, capped at 4R."""
    if side == 'LONG':
        cands = sorted(x for x in levels if x and x > beyond * 1.001)
        tp2 = cands[0] if cands else None
        cap = entry + 4.0 * risk
        return min(tp2, cap) if tp2 else None
    else:
        cands = sorted((x for x in levels if x and x < beyond * 0.999), reverse=True)
        tp2 = cands[0] if cands else None
        cap = entry - 4.0 * risk
        return max(tp2, cap) if tp2 else None

def scan_sym(sym, port):
    out = {'sym': sym, 'port': port, 'score': 0.0, 'side': None, 'decision': 'SKIP', 'setup': None,
           'vr': 0, 'brkUP': False, 'brkDN': False, 'rng_pos': 0.5, 'rr': None,
           'mark': 0, 'hi20': 0, 'lo20': 0, 'hi48': 0, 'lo48': 0,
           'mom3': 0, 't4': '-', 'funding': None, 'plan': None, 'note': ''}
    k = safe_pub('/fapi/v1/klines', {'symbol':sym,'interval':'1h','limit':60})
    if not isinstance(k, list) or len(k) < 40:
        out['decision'] = 'SKIP_no_data'
        return out
    closed = k[:-1]
    h = [fnum(x[2]) for x in closed]
    l = [fnum(x[3]) for x in closed]
    c = [fnum(x[4]) for x in closed]
    v = [fnum(x[5]) for x in closed]
    forming = k[-1]
    f_h, f_l, f_c, f_v = fnum(forming[2]), fnum(forming[3]), fnum(forming[4]), fnum(forming[5])
    last = c[-1]
    N = len(c)
    hi20 = max(h[-20:]); lo20 = min(l[-20:])
    hi48 = max(h[-48:]); lo48 = min(l[-48:])
    phi, plo = max(h[-21:-1]), min(l[-21:-1])
    brkUP = last > phi
    brkDN = last < plo
    pi = safe_pub('/fapi/v1/premiumIndex', {'symbol':sym})
    mark = fnum(pi.get('markPrice')) if isinstance(pi, dict) else f_c
    if mark <= 0:
        out['decision'] = 'SKIP_no_mark'; return out
    atr = atr_calc(h, l, c, 14)
    rng = hi20 - lo20
    rng_pos = (mark - lo20)/rng if rng > 0 else 0.5
    avg_v = sum(v[-20:])/20 if v else 1
    vr = (v[-1]/avg_v) if avg_v else 0
    form_vr = (f_v/avg_v) if avg_v else 0
    mom3 = (c[-1]/c[-4]-1)*100 if c[-4] else 0
    k4 = safe_pub('/fapi/v1/klines', {'symbol':sym,'interval':'4h','limit':30})
    t4 = '-'
    if isinstance(k4, list) and len(k4) >= 21:
        c4 = [fnum(x[4]) for x in k4[:-1]]
        t4 = 'UP' if c4[-1] > sum(c4[-20:])/20 else 'DN'
    funding = fnum(pi.get('lastFundingRate')) if isinstance(pi, dict) else None
    is_high = (port == 'HIGH')
    thr = HIGH_SCORE_MIN if is_high else CORE_SCORE_MIN

    # ---- detect real 20h breaks in last RETEST_LOOKBACK closed candles (most recent first) ----
    brks = []  # dicts(side, level, idx, rng_h, rng_l, bvr)
    for i in range(N-1, max(N-1-RETEST_LOOKBACK, 20), -1):
        ph, pl = max(h[i-20:i]), min(l[i-20:i])
        bvr = v[i] / (sum(v[i-20:i])/20) if sum(v[i-20:i]) else 0
        if c[i] > ph:
            brks.append({'side':'LONG','level':ph,'idx':i,'rng_h':ph,'rng_l':pl,'bvr':bvr})
        elif c[i] < pl:
            brks.append({'side':'SHORT','level':pl,'idx':i,'rng_h':ph,'rng_l':pl,'bvr':bvr})

    score = 0.0; side = None; setup = None; plan = None; decision = 'SKIP'; note = ''
    alignDN = t4=='DN' and mom3 < 0 and rng_pos < 0.45
    alignUP = t4=='UP' and mom3 > 0 and rng_pos > 0.55

    def mk_plan(side, entry, sl, tp1_hint, levels, entry_type, beyond):
        risk = (entry - sl) if side=='LONG' else (sl - entry)
        if risk <= 0: return None, 'bad_risk'
        # TP1 ~1.5R, or break extreme if it sits 1.0-2.0R away
        tp1 = entry + 1.5*risk if side=='LONG' else entry - 1.5*risk
        if tp1_hint:
            r_h = (tp1_hint-entry)/risk if side=='LONG' else (entry-tp1_hint)/risk
            if 1.0 <= r_h <= 2.0: tp1 = tp1_hint
        tp2 = next_target(side, entry, risk, levels, max(tp1, beyond) if side=='LONG' else min(tp1, beyond))
        if tp2 is None: return None, 'no_structure_target'
        rr = (tp2-entry)/risk if side=='LONG' else (entry-tp2)/risk
        return {'side':side,'entry':entry,'entry_type':entry_type,'sl':sl,'tp1':tp1,'tp2':tp2,
                'risk_pct':round(risk/entry*100,3),'rr':round(rr,2),'atr':atr}, None

    # ---- A) RETEST of a level that broke in the last ~8 candles ----
    for brk in brks:
        if setup is not None: break
        L = brk['level']; bi = brk['idx']; s = brk['side']
        mm = L + (brk['rng_h']-brk['rng_l']) if s=='LONG' else L - (brk['rng_h']-brk['rng_l'])
        post_h = h[bi:] + [f_h]; post_l = l[bi:] + [f_l]; post_c = c[bi+1:]
        if s == 'LONG':
            ext = max(post_h)
            dist = (mark - L)/L
            held = all(x >= L*(1-RETEST_CLOSE_TOL) for x in post_c)
            pulled = (ext - L) >= 0.5*atr and (ext - mark) >= 0.4*atr
            if -RETEST_CLOSE_TOL <= dist <= RETEST_ZONE and held and pulled:
                setup = 'RETEST'; side = 'LONG'
                swing = min(l[bi+1:] + [f_l]) if bi+1 <= N-1 else f_l
                sl = min(swing, L) - RETEST_ATR_BUF*atr
                if abs(dist) <= MARKET_ZONE: entry, et = mark, 'MARKET'
                else: entry, et = L*1.0005, 'LIMIT'
                plan, why = mk_plan('LONG', entry, sl, ext, [hi48, mm], et, ext)
        else:
            ext = min(post_l)
            dist = (L - mark)/L
            held = all(x <= L*(1+RETEST_CLOSE_TOL) for x in post_c)
            pulled = (L - ext) >= 0.5*atr and (mark - ext) >= 0.4*atr
            if -RETEST_CLOSE_TOL <= dist <= RETEST_ZONE and held and pulled:
                setup = 'RETEST'; side = 'SHORT'
                swing = max(h[bi+1:] + [f_h]) if bi+1 <= N-1 else f_h
                sl = max(swing, L) + RETEST_ATR_BUF*atr
                if abs(dist) <= MARKET_ZONE: entry, et = mark, 'MARKET'
                else: entry, et = L*0.9995, 'LIMIT'
                plan, why = mk_plan('SHORT', entry, sl, ext, [lo48, mm], et, ext)
        if setup == 'RETEST':
            score = 2.5
            if (side=='LONG' and t4=='UP') or (side=='SHORT' and t4=='DN'): score += 1.0
            elif not is_high: score -= 0.5  # CORE stays strict: counter-4h-trend retest needs more
            if brk['bvr'] >= 1.5: score += 0.5
            elif brk['bvr'] >= 1.0: score += 0.25
            score += 0.5  # held the level cleanly (checked above)
            if abs(dist) <= MARKET_ZONE: score += 0.3
            if plan is None: decision = f'SKIP_retest_{why}'
            note = f"lvl={L:.6g} dist={dist*100:.2f}% bvr={brk['bvr']:.2f} brk_age={N-1-bi}h"

    # ---- B) fresh breakout on last closed candle (original logic, honest TP2) ----
    if setup is None and (brkUP or brkDN):
        setup = 'BREAKOUT'
        side = 'LONG' if brkUP else 'SHORT'
        L = phi if brkUP else plo
        if (brkUP and t4=='UP') or (brkDN and t4=='DN'): score += 2.5
        else: score += 1.5
        if alignUP or alignDN: score += 1.0
        if vr >= 1.5: score += 1.0
        elif vr >= 1.0: score += 0.5
        if abs(mom3) >= 1.5: score += 0.7
        elif abs(mom3) >= 0.8: score += 0.3
        past = (mark - L) if side=='LONG' else (L - mark)
        mm = L + (phi-plo) if side=='LONG' else L - (phi-plo)
        if side == 'LONG':
            sl = min(L, min(l[-3:])) - RETEST_ATR_BUF*atr
            plan, why = mk_plan('LONG', mark, sl, None, [hi48, mm], 'MARKET', h[-1])
        else:
            sl = max(L, max(h[-3:])) + RETEST_ATR_BUF*atr
            plan, why = mk_plan('SHORT', mark, sl, None, [lo48, mm], 'MARKET', l[-1])
        note = f"lvl={L:.6g} past={past/atr if atr else 0:.2f}ATR"
        if plan is None: decision = f'SKIP_{why}'
        elif is_high:
            if past > HIGH_CHASE_ATR*atr: decision = 'SKIP_chase_atr'
        else:
            if side=='SHORT' and rng_pos < 0.12: decision = 'SKIP_chase_support'
            if side=='LONG' and rng_pos > 0.88: decision = 'SKIP_chase_resist'

    # ---- C) HIGH only: momentum continuation without a fresh break ----
    if setup is None and is_high and vr >= 1.5 and (
            (t4=='UP' and mom3 > 0.5 and rng_pos > 0.5) or (t4=='DN' and mom3 < -0.5 and rng_pos < 0.5)):
        setup = 'MOMENTUM'
        side = 'LONG' if t4=='UP' else 'SHORT'
        score = 1.0 + 1.0 + (0.7 if abs(mom3) >= 1.5 else 0.3)
        if vr >= 2.5: score += 0.3
        if side == 'LONG':
            sl = min(l[-5:]) - 0.3*atr
            plan, why = mk_plan('LONG', mark, sl, None, [hi20, hi48], 'MARKET', mark)
        else:
            sl = max(h[-5:]) + 0.3*atr
            plan, why = mk_plan('SHORT', mark, sl, None, [lo20, lo48], 'MARKET', mark)
        if plan is None: decision = f'SKIP_mom_{why}'

    if setup is None:
        if alignDN: side = 'SHORT'
        elif alignUP: side = 'LONG'
        elif mom3 < 0: side = 'SHORT'
        elif mom3 > 0: side = 'LONG'
        score = 1.0 if (alignDN or alignUP) else 0.0
        decision = 'SKIP_midRange' if 0.35 <= rng_pos <= 0.65 else 'SKIP_no_setup'
    if form_vr < 0.15:
        score *= 0.9

    rr = plan['rr'] if plan else None
    if decision == 'SKIP' and setup is not None:
        if rr is None or rr < 2.0: decision = 'SKIP_rr_weak'
        elif plan['entry_type']=='LIMIT' and abs(plan['entry']/mark-1) > MAX_LIMIT_DIST: decision = 'SKIP_limit_far'
        elif form_vr < 0.08 and vr < 0.8: decision = 'SKIP_thin_liq'
        elif score < thr: decision = 'SKIP_score_low'
        else: decision = 'CANDIDATE'

    out.update({
        'score': round(score, 2), 'side': side, 'decision': decision, 'setup': setup,
        'vr': round(vr, 2), 'form_vr': round(form_vr, 3),
        'brkUP': brkUP, 'brkDN': brkDN, 'rng_pos': round(rng_pos, 2), 'rr': rr,
        'mark': mark, 'hi20': hi20, 'lo20': lo20, 'hi48': hi48, 'lo48': lo48, 'phi': phi, 'plo': plo,
        'mom3': round(mom3, 3), 't4': t4, 'funding': funding, 'last_closed': last,
        'atr': atr, 'plan': plan, 'note': note, 'thr': thr,
    })
    return out

scan_results = []
for sym in CORE:
    scan_results.append(scan_sym(sym, 'CORE'))
for sym in valid_high:
    scan_results.append(scan_sym(sym, 'HIGH'))

# --- apply lessons.json adjustments ---
_pen = (LESSON_ADJ or {}).get('score_penalty', {}) or {}
_maxsl = (LESSON_ADJ or {}).get('max_sl_pct', {}) or {}
for s_ in scan_results:
    p_ = float(_pen.get(s_.get('setup') or '', 0)) + float(_pen.get('*', 0))
    if p_ and s_.get('setup'):
        s_['score'] = round(s_['score'] - p_, 2)
        s_['note'] = (s_.get('note') or '') + f' lesson-{p_}'
        if s_['decision'] == 'CANDIDATE' and s_['score'] < s_.get('thr', 3.5):
            s_['decision'] = 'SKIP_score_low_lesson'
    _t1 = (LESSON_ADJ or {}).get('tp1_max_r')
    if _t1 and s_.get('plan'):
        pl_ = s_['plan']; r_ = abs(pl_['entry'] - pl_['sl'])
        if r_ and abs(pl_['tp1'] - pl_['entry']) / r_ > float(_t1):
            pl_['tp1'] = pl_['entry'] + float(_t1) * r_ if pl_['side'] == 'LONG' else pl_['entry'] - float(_t1) * r_
    cap_ = _maxsl.get(s_['port'])
    if cap_ and s_.get('plan') and s_['decision'] == 'CANDIDATE' and s_['plan']['risk_pct'] > float(cap_):
        s_['decision'] = 'SKIP_sl_wide_lesson'
# --- maker entry (2026-11-11, t50u): BREAKOUT -> post-only limit at the breakout candle close ---
for s_ in scan_results:
    pl_ = s_.get('plan')
    if not (MAKER_ENTRY and s_['decision'] == 'CANDIDATE' and pl_ and s_.get('setup') == 'BREAKOUT'):
        continue
    side_ = pl_['side']; sg_ = 1 if side_ == 'LONG' else -1
    lim_ = float(s_['last_closed']); mk_ = float(s_['mark']); sl0_ = pl_['sl']
    if (side_ == 'LONG' and lim_ >= mk_) or (side_ == 'SHORT' and lim_ <= mk_):
        s_['decision'] = 'SKIP_maker_would_cross'   # price already back through the close: post-only would take; never chase
        continue
    risk_ = sg_ * (lim_ - sl0_)
    if risk_ <= 0:
        s_['decision'] = 'SKIP_maker_bad_risk'; continue
    tp1_ = lim_ + sg_ * 1.5 * risk_
    t1cap_ = (LESSON_ADJ or {}).get('tp1_max_r')
    if t1cap_ and 1.5 > float(t1cap_): tp1_ = lim_ + sg_ * float(t1cap_) * risk_
    cap4_ = lim_ + sg_ * 4.0 * risk_
    tp2_ = min(pl_['tp2'], cap4_) if side_ == 'LONG' else max(pl_['tp2'], cap4_)
    if sg_ * (tp2_ - tp1_) <= 0:
        s_['decision'] = 'SKIP_maker_tp2_inside_tp1'; continue
    # R:R after fees: maker entry, taker exits
    rew_ = sg_ * (tp2_ - lim_) - lim_ * MAKER_FEE - tp2_ * TAKER_FEE
    rsk_ = risk_ + lim_ * MAKER_FEE + sl0_ * TAKER_FEE
    rr_net_ = rew_ / rsk_ if rsk_ > 0 else 0
    pl_.update({'entry': lim_, 'entry_type': 'LIMIT_GTX', 'tp1': tp1_, 'tp2': tp2_, 'risk_pct': round(risk_/lim_*100, 3),
                'rr': round(sg_ * (tp2_ - lim_) / risk_, 2), 'rr_net': round(rr_net_, 2), 'mark_at_scan': mk_})
    s_['rr'] = pl_['rr']
    s_['note'] = (s_.get('note') or '') + f' maker@{lim_:.6g} rr_net={rr_net_:.2f}'
    if rr_net_ < 2.0:
        s_['decision'] = 'SKIP_maker_rr_net_lt2'
# --- A3 (2026-11-11, approved): SL distance must be >= MIN_SL_ATR x ATR(14,1h), all setups/books ---
for s_ in scan_results:
    pl_ = s_.get('plan')
    if s_['decision'] == 'CANDIDATE' and pl_ and s_.get('atr'):
        sl_atr_ = abs(pl_['entry'] - pl_['sl']) / s_['atr']
        if sl_atr_ < MIN_SL_ATR:
            s_['decision'] = 'SKIP_sl_lt_1atr'
            s_['note'] = (s_.get('note') or '') + f' sl={sl_atr_:.2f}ATR'
scan_results.sort(key=lambda x: -x['score'])
top = scan_results[:8]
max_score = top[0]['score'] if top else 0
max_sym = top[0]['sym'] if top else None

candidates = [s for s in scan_results if s['decision']=='CANDIDATE']
skips.append(f"Scan CORE+HIGH ({len(scan_results)} pairs): max score {max_score} on {max_sym}")
for t in top[:5]:
    skips.append(
        f"Top {t['sym']} {t['port']} {t.get('side')} sc={t['score']} vr={t['vr']} "
        f"setup={t.get('setup')} brkUP={t['brkUP']} brkDN={t['brkDN']} rng={t['rng_pos']} rr={t.get('rr')} -> {t['decision']}"
    )

# Prefer quality; Sunday stricter
entered = None
if blocked_daily:
    skips.append("Daily stop hit — NO new trades")
elif not slots_ok:
    skips.append(f"Max positions {pos_used}/2 — no new entry")
elif within_30_us:
    skips.append("Within 30min major US data — no new entry")
elif not candidates:
    skips.append("No CANDIDATE (BREAKOUT only while RETEST/MOMENTUM locked; need R:R>=2 to TP2, score>=3.5 CORE / >=2.5 HIGH, SL>=1xATR)")
else:
    # pick best candidate respecting portfolio slots and correlation
    for cand in candidates:
        port = cand['port']
        if port == 'CORE' and not core_free:
            continue
        if port == 'HIGH' and not high_free:
            continue
        risk_budget = _rt.book_cap(port, wallet, risk_budget_trade)  # adaptive tier, never above old book caps
        if port == 'HIGH' and room < risk_budget:
            # don't shrink below meaningful — skip if remaining daily < planned risk
            if room < risk_budget:
                skips.append(f"{cand['sym']} HIGH skipped: remaining daily room {room:.2f} < meaningful")
                continue
            # if room between 1 and risk_budget, still skip per rules (don't shrink)
            skips.append(f"{cand['sym']} HIGH skipped: remaining daily budget {room:.2f} < planned risk {risk_budget:.2f}")
            continue
        if room < risk_budget:
            skips.append(f"{cand['sym']} skipped: daily room {room:.2f} too small")
            continue
        # correlation: avoid same-direction correlated with open
        corr_groups = [
            {'BTCUSDT','ETHUSDT','SOLUSDT','BNBUSDT'},
            {'ARBUSDT','OPUSDT'},
            {'DOGEUSDT','1000PEPEUSDT','WIFUSDT'},
        ]
        blocked_corr = False
        for op in positions:
            if cand['side'] and (
                (cand['side']=='LONG' and op['amt']>0) or (cand['side']=='SHORT' and op['amt']<0)
            ):
                for g in corr_groups:
                    if cand['sym'] in g and op['symbol'] in g:
                        blocked_corr = True
        if blocked_corr:
            skips.append(f"{cand['sym']} skip correlated same-dir with open pos")
            continue

        # SIZE + PLACE (uses the plan computed in scan_sym)
        plan = cand['plan']
        mark = cand['mark']
        side = cand['side']
        info = sym_info.get(cand['sym'], {})
        step = info.get('stepSize', 0.001)
        tick = info.get('tickSize', 0.01)
        min_n = info.get('minNotional', 5)
        lev_max = 10 if port=='CORE' else 20
        pp = info.get('pricePrecision', 4)
        def fmt_p(x):
            # round to tick in the safe direction is overkill here; tick-round then precision
            if tick > 0: x = round(x / tick) * tick
            return float(f"{x:.{pp}f}")
        entry_px = fmt_p(plan['entry'])
        use_market = plan['entry_type'] == 'MARKET'
        if use_market: entry_px = mark
        sl = plan['sl']; tp1 = plan['tp1']; tp2 = plan['tp2']
        if side == 'SHORT':
            risk_per_unit = sl - entry_px; entry_side = 'SELL'; close_side = 'BUY'
        else:
            risk_per_unit = entry_px - sl; entry_side = 'BUY'; close_side = 'SELL'
        if risk_per_unit <= 0:
            skips.append(f"{cand['sym']} invalid risk_per_unit")
            continue
        if not use_market and abs(entry_px/mark - 1) > MAX_LIMIT_DIST:
            skips.append(f"{cand['sym']} limit {entry_px} >{MAX_LIMIT_DIST*100:.1f}% from mark — skip")
            continue
        # leverage so isolated liquidation stays well beyond SL
        sl_dist = risk_per_unit / entry_px
        lev_liq = int(0.9 / (sl_dist + 0.01)) if sl_dist > 0 else lev_max
        lev = max(1, min(lev_max, lev_liq))

        fee_buf = entry_px * FEE_RATE * 2
        qty_raw = risk_budget / (risk_per_unit + fee_buf)
        # cap by margin available (risk may be below cap, never above)
        max_qty_margin = (avail * 0.85 * lev) / entry_px if entry_px else 0
        if qty_raw > max_qty_margin:
            qty_raw = max_qty_margin
        qty = floor_step(qty_raw, step)   # A4: always round DOWN, never upsize
        qp = info.get('quantityPrecision')
        while qty > 0 and qty * (risk_per_unit + fee_buf) > risk_budget * (1 + 1e-9):
            qty = floor_step(qty - step, step)
        notional = qty * entry_px
        min_q = info.get('minQty', 0) or 0
        if qty <= 0 or qty < min_q or notional < min_n:
            skips.append(f"{cand['sym']} SKIP_min_notional qty={qty} (minQty {min_q}) notional={notional:.2f} < min {min_n} at risk {risk_budget:.4f}")
            cand['decision'] = 'SKIP_min_notional'
            continue
        margin_needed = notional / lev
        if margin_needed > avail * 0.9:
            skips.append(f"{cand['sym']} margin {margin_needed:.2f} > avail*0.9; skip")
            continue
        # approx isolated liq (MMR ~1%): must be beyond SL
        liq_est = entry_px * (1 - 1/lev + 0.01) if side=='LONG' else entry_px * (1 + 1/lev - 0.01)
        if (side=='LONG' and liq_est >= sl) or (side=='SHORT' and liq_est <= sl):
            skips.append(f"{cand['sym']} est liq {liq_est:.6g} not beyond SL {sl:.6g} — skip")
            continue

        rr_check = abs((tp2-entry_px)/(entry_px-sl)) if side=='LONG' else abs((entry_px-tp2)/(sl-entry_px))
        if rr_check < 2.0:
            skips.append(f"{cand['sym']} final R:R {rr_check:.2f} < 2 — skip")
            continue

        # guard (2026-11-10 fix): pending limit must not have TP1 already passed by mark
        if not use_market and ((side=='SHORT' and mark <= tp1) or (side=='LONG' and mark >= tp1)):
            skips.append(f"{cand['sym']} mark {mark:.6g} already past TP1 {tp1:.6g} for pending limit — skip")
            continue

        r1 = safe_call('/fapi/v1/marginType', {'symbol':cand['sym'],'marginType':'ISOLATED'}, 'POST')
        r2 = safe_call('/fapi/v1/leverage', {'symbol':cand['sym'],'leverage':lev}, 'POST')

        sl_p = fmt_p(sl); tp1_p = fmt_p(tp1); tp2_p = fmt_p(tp2)

        half = floor_step(qty/2, step)
        half2 = floor_step(qty - half + step*1e-6, step)   # exact remainder on the step grid
        if half + half2 > qty + step*1e-6: half2 = floor_step(qty - half, step)
        if half <= 0 or half2 <= 0:
            skips.append(f"{cand['sym']} cannot split 50/50 stepSize — skip")
            continue

        if use_market:
            order = safe_call('/fapi/v1/order', {
                'symbol': cand['sym'], 'side': entry_side, 'type': 'MARKET',
                'quantity': qty,
            }, 'POST')
        else:
            lim = entry_px
            is_gtx = plan['entry_type'] == 'LIMIT_GTX'
            order = safe_call('/fapi/v1/order', {
                'symbol': cand['sym'], 'side': entry_side, 'type': 'LIMIT',
                'timeInForce': 'GTX' if is_gtx else 'GTC', 'price': lim, 'quantity': qty,
            }, 'POST')
            if is_gtx and isinstance(order, dict) and ('-5022' in str(order.get('code', '')) + str(order.get('err', '')) or order.get('status') == 'EXPIRED'):
                # post-only rejected (would have crossed): skip, never chase with market
                skips.append(f"{cand['sym']} SKIP_maker_rejected GTX would cross: {str(order)[:120]}")
                actions.append({'action': 'maker_rejected', 'sym': cand['sym'], 'res': str(order)[:160]})
                continue

        if isinstance(order, dict) and (order.get('orderId') or order.get('status') in ('NEW','FILLED','PARTIALLY_FILLED')):
            changed = True
            # Place SL closePosition STOP_MARKET
            sl_order = safe_call('/fapi/v1/algoOrder', {
                'algoType': 'CONDITIONAL',
                'symbol': cand['sym'],
                'side': close_side,
                'type': 'STOP_MARKET',
                'triggerPrice': sl_p,
                'workingType': 'MARK_PRICE',
                **({'closePosition': 'true'} if use_market else {'quantity': qty, 'reduceOnly': 'true'}),
            }, 'POST')
            # If algo fails try classic
            if isinstance(sl_order, dict) and ('err' in sl_order or 'code' in sl_order or 'http' in sl_order):
                sl_order = safe_call('/fapi/v1/order', {
                    'symbol': cand['sym'], 'side': close_side, 'type': 'STOP_MARKET',
                    'stopPrice': sl_p, 'closePosition': 'true', 'workingType': 'MARK_PRICE',
                }, 'POST')
            # TP1 / TP2 TAKE_PROFIT_MARKET reduceOnly
            tp1_o = safe_call('/fapi/v1/algoOrder', {
                'algoType': 'CONDITIONAL', 'symbol': cand['sym'], 'side': close_side,
                'type': 'TAKE_PROFIT_MARKET', 'triggerPrice': tp1_p,
                'quantity': half, 'reduceOnly': 'true', 'workingType': 'MARK_PRICE',
            }, 'POST')
            if isinstance(tp1_o, dict) and ('err' in tp1_o or 'code' in tp1_o or 'http' in tp1_o):
                tp1_o = safe_call('/fapi/v1/order', {
                    'symbol': cand['sym'], 'side': close_side, 'type': 'TAKE_PROFIT_MARKET',
                    'stopPrice': tp1_p, 'quantity': half, 'reduceOnly': 'true', 'workingType': 'MARK_PRICE',
                }, 'POST')
            tp2_o = safe_call('/fapi/v1/algoOrder', {
                'algoType': 'CONDITIONAL', 'symbol': cand['sym'], 'side': close_side,
                'type': 'TAKE_PROFIT_MARKET', 'triggerPrice': tp2_p,
                'quantity': half2, 'reduceOnly': 'true', 'workingType': 'MARK_PRICE',
            }, 'POST')
            if isinstance(tp2_o, dict) and ('err' in tp2_o or 'code' in tp2_o or 'http' in tp2_o):
                tp2_o = safe_call('/fapi/v1/order', {
                    'symbol': cand['sym'], 'side': close_side, 'type': 'TAKE_PROFIT_MARKET',
                    'stopPrice': tp2_p, 'quantity': half2, 'reduceOnly': 'true', 'workingType': 'MARK_PRICE',
                }, 'POST')

            def _bad(x): return (not isinstance(x, dict)) or ('err' in x) or ('http' in x) or (str(x.get('code','200')) not in ('200',) and 'algoId' not in x and 'orderId' not in x)
            if _bad(sl_order) or _bad(tp1_o) or _bad(tp2_o):
                # protection incomplete: undo entry (limit) / leave for watcher fix (market) and report
                undo = []
                if not use_market and order.get('orderId'):
                    for o_ in (tp1_o, tp2_o, sl_order):
                        if isinstance(o_, dict) and o_.get('algoId'):
                            undo.append(safe_call('/fapi/v1/algoOrder', {'symbol':cand['sym'],'algoId':o_['algoId']}, 'DELETE'))
                    undo.append(safe_call('/fapi/v1/order', {'symbol':cand['sym'],'orderId':order['orderId']}, 'DELETE'))
                    skips.append(f"{cand['sym']} SL/TP placement failed -> limit entry cancelled: sl={str(sl_order)[:120]} tp1={str(tp1_o)[:120]}")
                    actions.append({'action':'cancel_unprotected','sym':cand['sym'],'port':port,'reason':'SL/TP placement failed'})
                    continue
            if (not use_market) and plan['entry_type'] == 'LIMIT_GTX' and not DRY and order.get('orderId'):
                chk = safe_call('/fapi/v1/order', {'symbol': cand['sym'], 'orderId': order['orderId']})
                if isinstance(chk, dict) and chk.get('status') == 'EXPIRED':
                    for o_ in (tp1_o, tp2_o, sl_order):
                        if isinstance(o_, dict) and o_.get('algoId'):
                            safe_call('/fapi/v1/algoOrder', {'symbol': cand['sym'], 'algoId': o_['algoId']}, 'DELETE')
                    skips.append(f"{cand['sym']} SKIP_maker_rejected (GTX expired right after placement) -> SL/TP removed")
                    actions.append({'action': 'maker_rejected', 'sym': cand['sym'], 'res': 'EXPIRED'})
                    continue
            risk_usdt = qty * risk_per_unit + qty * entry_px * FEE_RATE * 2
            entered = {
                'sym': cand['sym'], 'port': port, 'side': side, 'entry': 'MARKET' if use_market else plan['entry_type'],
                'risk_tier': RISK_STATE['tier'],
                'qty': qty, 'mark': mark, 'entry_px': entry_px, 'setup': cand.get('setup'), 'sl': sl_p, 'tp1': tp1_p, 'tp2': tp2_p,
                'lev': lev, 'risk_usdt': round(risk_usdt, 4),
                'risk_pct_wallet': round(risk_usdt/wallet*100, 2) if wallet else None,
                'rr': round(rr_check, 2),
                'order': {k: order.get(k) for k in ('orderId','status','avgPrice','executedQty') if k in order},
                'sl_res': str(sl_order)[:200], 'tp1_res': str(tp1_o)[:200], 'tp2_res': str(tp2_o)[:200],
            }
            actions.append({'action':'enter', **entered})
            if not DRY:
                st_ = pm.load_state()
                st_[cand['sym']] = {'symbol': cand['sym'], 'book': port, 'setup': cand.get('setup'), 'side': side,
                    'entry': entry_px, 'sl': sl_p, 'tp1': tp1_p, 'tp2': tp2_p, 'qty': qty, 'lev': lev,
                    'score': cand['score'], 'rr': round(rr_check, 2), 'opened_ms': int(time.time()*1000),
                    'risk_usdt': round(risk_usdt, 4), 'pending_limit': not use_market, 'note': cand.get('note'),
                    'maker': plan['entry_type'] == 'LIMIT_GTX', 'risk_tier': RISK_STATE['tier'], 'risk_pct': RISK_PCT,
                    **({'expire_ms': int(time.time()*1000) + MAKER_EXPIRE_MIN*60000, 'entry_order_id': order.get('orderId')} if plan['entry_type'] == 'LIMIT_GTX' else {})}
                pm.save_state(st_)
                if not subprocess.run(['pgrep','-f','/workspace/trade/pos_watch.py'], capture_output=True).stdout.strip():
                    subprocess.Popen(['nohup','python3','/workspace/trade/pos_watch.py'], stdout=open('/workspace/trade/pos_watch.out','a'),
                                     stderr=subprocess.STDOUT, start_new_session=True)
                    entered['watcher'] = 'started'
            # only one new entry per run typically
            break
        else:
            actions.append({'action':'enter_failed', 'sym': cand['sym'], 'res': str(order)[:300]})
            skips.append(f"Entry order failed {cand['sym']}: {str(order)[:200]}")
            changed = True  # failed attempt worth noting? Only if API error — treat as change for telegram on auth
            if isinstance(order, dict) and ('api' in str(order).lower() or order.get('http')==401):
                auth_error = str(order)

if entered is None and candidates:
    skips.append("Candidates existed but blocked by slot/budget/corr/size")
elif entered is None:
    skips.append("No HQ entry this run; quality over slots")

# refresh counts after actions
orders2 = safe_call('/fapi/v1/openOrders')
if not isinstance(orders2, list): orders2 = orders
algos2 = safe_call('/fapi/v1/openAlgoOrders')
if not isinstance(algos2, list): algos2 = algos
pos2 = positions
if entered:
    pr = safe_call('/fapi/v2/positionRisk')
    if isinstance(pr, list):
        pos2 = []
        for p in pr:
            amt = fnum(p.get('positionAmt'))
            if amt != 0:
                sym = p['symbol']
                port = 'CORE' if sym in CORE else ('HIGH' if sym in HIGH else 'OTHER')
                pos2.append({'symbol':sym,'port':port,'amt':amt,'entry':fnum(p.get('entryPrice')),
                             'mark':fnum(p.get('markPrice')),'upnl':fnum(p.get('unRealizedProfit')),
                             'lev':int(fnum(p.get('leverage'))),'marginType':p.get('marginType'),
                             'liq':fnum(p.get('liquidationPrice'))})

# Telegram
if changed:
    lines = []
    if entered:
        label = 'CORE' if entered['port']=='CORE' else 'MAO HIEM'
        lines.append(f"VAO LENH {label}")
        lines.append(f"{entered['sym']} {entered['side']} {'LIMIT maker (huy sau 60 phut neu chua khop)' if entered['entry']=='LIMIT_GTX' else entered['entry']}")
        lines.append(f"qty={entered['qty']} lev={entered['lev']}x isolated")
        lines.append(f"entry~{entered['mark']} SL={entered['sl']} TP1={entered['tp1']} TP2={entered['tp2']}")
        lines.append(f"risk={entered['risk_usdt']} USDT ({entered['risk_pct_wallet']}% vi) R:R={entered['rr']}")
        lines.append(f"vi={wallet:.2f} PnL hom nay={realized:.2f}")
    for a in actions:
        if a.get('action','').startswith('cancel'):
            lines.append(f"Huy pending {a.get('sym')} ({a.get('action')})")
        if a.get('action')=='enter_failed':
            lines.append(f"Loi dat lenh {a.get('sym')}: {a.get('res','')[:120]}")
    if auth_error:
        lines.append(f"Loi API: {auth_error[:200]}")
    if not lines:
        lines.append(f"Thay doi trang thai vi={wallet:.2f}")
    telegram_message = '\n'.join(lines)
    if DRY:
        telegram_error = 'dry-run: telegram not sent'
    try:
        if DRY: raise RuntimeError('dry-run: telegram not sent')
        p = subprocess.run(['python3','/workspace/tg_trade.py'], input=telegram_message.encode('utf-8'),
                           capture_output=True, timeout=20)
        out_tg = p.stdout.decode().strip()
        telegram_sent = out_tg.lower() in ('true','1') or 'true' in out_tg.lower()
        if p.returncode != 0 and not telegram_sent:
            telegram_error = p.stderr.decode()[:300] or out_tg
    except Exception as e:
        telegram_error = str(e)

skip_summary = (
    f"Flat; max sc{max_score} {max_sym}; " +
    (f"ENTERED {entered['sym']}" if entered else "no HQ entry") +
    ("; Telegram sent" if telegram_sent else "; Telegram silent")
)
if positions:
    skip_summary = f"{len(positions)} pos; " + skip_summary

result = {
    'ts_saigon': TS,
    'walletBalance': wallet,
    'availableBalance': avail,
    'unrealizedPnL': upnl,
    'realizedPnL_today': realized,
    'commission_today': commission,
    'saigon_day': DAY,
    'positions': len(pos2),
    'positions_detail': pos2,
    'openOrders': len(orders2) if isinstance(orders2, list) else 0,
    'openOrders_detail': [
        {'symbol':o.get('symbol'),'side':o.get('side'),'type':o.get('type'),
         'price':o.get('price'),'qty':o.get('origQty')} for o in (orders2 or [])[:20]
    ],
    'openAlgo': len(algos2) if isinstance(algos2, list) else 0,
    'openAlgo_detail': [
        {'symbol':a.get('symbol'),'side':a.get('side'),
         'type':a.get('orderType',a.get('type')),
         'trigger':a.get('triggerPrice',a.get('stopPrice'))} for a in (algos2 or [])[:20]
    ],
    'actions': actions,
    'skips': skips,
    'skip_summary': skip_summary,
    'telegram_sent': telegram_sent,
    'telegram_message': telegram_message,
    'telegram_error': telegram_error,
    'auth_error': auth_error,
    'us_data_status': us_data,
    'high_risk_budget_usdt': round(high_budget, 4),
    'risk_state': RISK_STATE,
    'scan_top': [
        {k: t.get(k) for k in ('sym','port','setup','score','thr','side','vr','form_vr','brkUP','brkDN','rng_pos','rr','mom3','t4','decision','note','plan')}
        for t in top[:6]
    ],
    'slots': {
        'core_free': core_free and (entered is None or entered.get('port')!='CORE'),
        'high_risk_free': high_free and (entered is None or entered.get('port')!='HIGH'),
        'positions_used': len(pos2),
        'max': 2,
        'blocked_by_daily_stop': blocked_daily,
        'high_blocked_by_remaining_budget': room < high_budget,
    },
    'changed': changed,
    'max_score': max_score,
    'max_sym': max_sym,
}

scan_now = {
    'ts': TS,
    'wallet': wallet,
    'top': result['scan_top'][:5],
    'changed': changed,
    'skip_summary': skip_summary,
}

if DRY:
    result['dry_run'] = True
    result['dry_writes'] = dry_writes
    open('/workspace/trade/_dry_run.json','w').write(json.dumps(result, ensure_ascii=False, indent=2))
else:
    open('/workspace/trade/last_run.json','w').write(json.dumps(result, ensure_ascii=False, indent=2))
    with open('/workspace/trade/hourly_log.jsonl','a') as f:
        f.write(json.dumps(result, ensure_ascii=False)+'\n')
    open('/workspace/trade/_scan_now.json','w').write(json.dumps(scan_now, ensure_ascii=False, indent=2))
print(json.dumps({
    'ok': True,
    'ts': TS,
    'wallet': wallet,
    'avail': avail,
    'realized': realized,
    'positions': len(pos2),
    'orders': result['openOrders'],
    'algos': result['openAlgo'],
    'changed': changed,
    'max_score': max_score,
    'max_sym': max_sym,
    'dry_run': DRY,
    'top_decisions': [(t['sym'], t['port'], t.get('setup'), t.get('side'), t['score'], t.get('rr'), t['decision']) for t in top[:6]],
    'dry_writes': dry_writes if DRY else None,
    'actions': [a.get('action') for a in actions],
    'telegram_sent': telegram_sent,
    'auth_error': auth_error,
}, ensure_ascii=False, indent=2))
