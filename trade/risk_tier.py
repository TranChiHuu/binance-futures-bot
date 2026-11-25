"""Wallet-adaptive risk per trade.

2026-11-20 (user approved t103u / follow-up): both books risk 25% of wallet per
trade; user accepted the risk of blowing up the account. Drawdown multipliers
still apply (x0.75 / x0.5) but cannot push risk ABOVE 25%. Daily stop is -50%
of the Saigon start-of-day wallet (replaces max(-7.5, -3x risk)).
Deposits and withdrawals (Binance TRANSFER income) are excluded from drawdown
and from the start-of-day wallet used for the daily stop.

Older small-wallet 2.5%/4% and >=100 1–2% scale-up paths are retired; aliases
remain so older comments/imports still resolve, all pointing at 25%.
"""
import json, os, time, datetime
from decimal import Decimal, ROUND_FLOOR

SAIGON = datetime.timezone(datetime.timedelta(hours=7))
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
D = ROOT+'/trade'
STATE = f'{D}/risk_state.json'
JOURNAL = f'{D}/trade_journal.jsonl'
XFER_CACHE = f'{D}/transfers_cache.json'
SINCE = '2026-11-11T11:30+07:00'   # trades opened after the maker/adaptive change
ACCOUNT_START_MS = int(datetime.datetime(2026, 11, 2, tzinfo=SAIGON).timestamp() * 1000)

# user 2026-11-20: 25% both books, hard ceiling 25%
BASE, CEIL = 0.25, 0.25
HARD_CEIL_PCT = 0.25
DAILY_STOP_PCT = 0.50
SMALL_WALLET = 100.0  # kept for compatibility; no longer a different risk %
SMALL_CORE_BASE, SMALL_CORE_CEIL = BASE, CEIL
SMALL_HIGH_BASE, SMALL_HIGH_CEIL = BASE, CEIL
SMALL_BASE, SMALL_CEIL = BASE, CEIL
# old USDT/pct caps would have clipped 25% risk; book_cap now only enforces HARD_CEIL_PCT
CORE_CAP_USDT, HIGH_CAP_PCT = None, HARD_CEIL_PCT

# isolated-margin safety (liq clearly beyond SL)
MMR = 0.01                 # ~1% maintenance margin used in the liq estimate
LIQ_SL_MULT = 1.5          # liquidation at least 1.5x the SL distance from entry
LEV_HARD_MAX = 25          # default if the exchange max is unknown; 25x keeps liq ~3% away
MARGIN_USE_FRAC = 0.85     # never commit more than this fraction of available balance


def load_state(path=STATE):
    try: return json.load(open(path))
    except Exception: return {}


def journal_stats(path=JOURNAL, since=SINCE):
    pn = []
    try:
        for l in open(path):
            if not l.strip(): continue
            r = json.loads(l)
            if str(r.get('ts_open', '')) >= since and r.get('pnl_net') is not None:
                pn.append(float(r['pnl_net']))
    except Exception:
        pass
    pos = sum(x for x in pn if x > 0); neg = -sum(x for x in pn if x < 0)
    return dict(n=len(pn), expectancy=(sum(pn)/len(pn)) if pn else 0.0, pf=(pos/neg) if neg > 0 else (99.0 if pos > 0 else 0.0))


def daily_stop_usdt(start_wallet, pct=DAILY_STOP_PCT):
    """Negative USDT daily-stop level: -pct * start-of-day wallet."""
    return -abs(float(pct)) * float(start_wallet)


def net_transfers(transfers, start_ms=None, end_ms=None):
    """Sum TRANSFER income (deposit > 0, withdrawal < 0) in [start_ms, end_ms)."""
    total = 0.0
    for t in transfers or []:
        try:
            ts = int(t.get('time') or 0)
        except (TypeError, ValueError):
            continue
        if start_ms is not None and ts < start_ms:
            continue
        if end_ms is not None and ts >= end_ms:
            continue
        try:
            total += float(t.get('income') or 0)
        except (TypeError, ValueError):
            continue
    return total


def trading_equity(wallet, transfers, end_ms=None):
    """Wallet with deposits/withdrawals removed (end_ms exclusive)."""
    return float(wallet) - net_transfers(transfers, end_ms=end_ms)


def load_transfers(path=XFER_CACHE):
    try:
        d = json.load(open(path))
        if isinstance(d, list):
            return d
        return d.get('transfers') or []
    except Exception:
        return []


def save_transfers(transfers, path=XFER_CACHE, fetched_at=None):
    seen = set()
    out = []
    for t in sorted(transfers or [], key=lambda x: int(x.get('time') or 0)):
        try:
            key = (int(t.get('time') or 0), round(float(t.get('income') or 0), 8), str(t.get('tranId') or ''))
        except (TypeError, ValueError):
            continue
        if key in seen:
            continue
        seen.add(key)
        out.append({'time': int(t['time']), 'income': float(t['income']),
                    'asset': t.get('asset') or 'USDT', 'tranId': t.get('tranId') or ''})
    payload = {
        'fetched_at': fetched_at or datetime.datetime.now(SAIGON).strftime('%Y-%m-%dT%H:%M:%S+07:00'),
        'transfers': out,
    }
    tmp = path + '.tmp'
    json.dump(payload, open(tmp, 'w'), indent=2)
    os.replace(tmp, path)
    return out


def day_start_ms(day):
    """Epoch ms of 00:00 Asia/Saigon for YYYY-MM-DD."""
    y, m, d = map(int, str(day).split('-'))
    return int(datetime.datetime(y, m, d, tzinfo=SAIGON).timestamp() * 1000)


def refresh_transfers(sc, start_ms=None, now_ms=None, cache_path=XFER_CACHE):
    """Merge new TRANSFER income into the cache. On API failure, keep the cache.

    sc(path, params, method='GET') should return a list of income rows or an error dict.
    """
    cached = load_transfers(cache_path)
    now_ms = int(now_ms or time.time() * 1000)
    if start_ms is None:
        start_ms = (max(int(t['time']) for t in cached) + 1) if cached else ACCOUNT_START_MS
    start_ms = int(start_ms)
    rows = list(cached)
    cur = start_ms
    ok = True
    while cur <= now_ms:
        end = min(cur + 7 * 86400 * 1000 - 1, now_ms)
        try:
            r = sc('/fapi/v1/income', {'incomeType': 'TRANSFER', 'startTime': cur, 'endTime': end, 'limit': 1000}, 'GET')
        except TypeError:
            r = sc('/fapi/v1/income', {'incomeType': 'TRANSFER', 'startTime': cur, 'endTime': end, 'limit': 1000})
        if not isinstance(r, list):
            ok = False
            break
        rows.extend(r)
        if len(r) < 1000:
            cur = end + 1
        else:
            cur = int(r[-1]['time']) + 1
    cleaned = save_transfers(rows, path=cache_path) if (rows or cached) else []
    return cleaned, ok


def adj_peak(st, adj, transfers, now_ms=None):
    """Transfer-adjusted equity peak. Migrates a legacy raw-wallet peak.

    Legacy peak is a raw wallet high (deposits included, withdrawals not subtracted).
    If peak_ts_ms is set, transfers up to that time are removed from the raw peak.
    If it is missing, deposits are assumed to already be in the raw peak and
    withdrawals are assumed to have happened after it (the usual stale-peak case).
    """
    st = st or {}
    now_ms = int(now_ms or time.time() * 1000)
    adj = float(adj)
    if st.get('peak_basis') == 'adj':
        prev = float(st.get('peak') or 0)
        if adj > prev:
            return adj, now_ms
        return prev, st.get('peak_ts_ms')
    raw = float(st.get('peak') or 0)
    peak_ts = st.get('peak_ts_ms')
    if peak_ts in (None, ''):
        net_at = sum(float(t.get('income') or 0) for t in (transfers or []) if float(t.get('income') or 0) > 0)
    else:
        net_at = net_transfers(transfers, end_ms=int(peak_ts) + 1)
    prev_adj = raw - net_at
    if adj >= prev_adj:
        return (adj if adj > 0 else raw), now_ms
    return (prev_adj if prev_adj > 0 else raw), (int(peak_ts) if peak_ts not in (None, '') else None)


def start_of_day_wallet(st, wallet, day, realized=0.0, commission=0.0, transfers_today=0.0):
    """Reuse today's snapshot, or back out realized+commission+transfers on the first run of the day.

    A legacy snapshot that did not exclude transfers is recomputed.
    """
    st = st or {}
    if (st.get('day_start_date') == day and st.get('day_start_wallet') not in (None, '')
            and st.get('day_start_excludes_transfers')):
        try:
            sow = float(st['day_start_wallet'])
            if sow > 0:
                return sow
        except (TypeError, ValueError):
            pass
    sow = float(wallet) - float(realized or 0) - float(commission or 0) - float(transfers_today or 0)
    if sow <= 0:
        sow = float(wallet) - float(transfers_today or 0)
        if sow <= 0:
            sow = float(wallet)
    return sow


def compute(wallet, st=None, js=None, day=None, realized=0.0, commission=0.0,
            transfers=None, now_ms=None):
    st = dict(st or {}); js = js or journal_stats()
    wallet = float(wallet)
    now_ms = int(now_ms or time.time() * 1000)
    xfers = list(transfers) if transfers is not None else []
    net_all = net_transfers(xfers)
    adj = trading_equity(wallet, xfers)
    peak, peak_ts = adj_peak(st, adj, xfers, now_ms=now_ms)
    if peak <= 0:
        peak = adj if adj > 0 else wallet
    dd = ((peak - adj) / peak) if peak > 0 else 0.0
    if dd < 0:
        dd = 0.0
    prev_dd_mult = float(st.get('dd_mult', 1.0))
    need = 0.5 if dd >= 0.20 else (0.75 if dd >= 0.10 else 1.0)
    if dd < 0.05: dd_mult = need                      # recovered: back to normal
    else: dd_mult = min(need, prev_dd_mult)           # sticky: keep the lowest tier until DD < 5%
    dd_tag = '' if dd_mult >= 1.0 else ('_DD20' if dd_mult <= 0.5 else '_DD10')
    # both books, all wallet sizes: 25% * dd_mult, never above CEIL
    base = BASE
    pct = min(BASE * dd_mult, CEIL)
    pct_core = pct_high = pct
    base_core = base_high = base
    tier = 'RISK_25pct' + dd_tag
    out = dict(peak=round(peak, 8), wallet=round(wallet, 8), dd=round(dd, 4), dd_mult=dd_mult,
               base_pct=base, risk_pct=round(pct, 6),
               base_pct_core=base_core, base_pct_high=base_high,
               risk_pct_core=round(pct_core, 6), risk_pct_high=round(pct_high, 6),
               risk_usdt=round(wallet*pct, 6),
               risk_usdt_core=round(wallet*pct_core, 6), risk_usdt_high=round(wallet*pct_high, 6),
               tier=tier, journal=js, since=SINCE,
               updated=datetime.datetime.now(SAIGON).strftime('%Y-%m-%dT%H:%M:%S+07:00'))
    transfers_today = net_transfers(xfers, start_ms=day_start_ms(day)) if day else 0.0
    sow = start_of_day_wallet(st, wallet, day, realized, commission, transfers_today) if day else adj
    out['peak_basis'] = 'adj'
    out['peak_ts_ms'] = peak_ts
    out['adj_wallet'] = round(adj, 8)
    out['net_transfers'] = round(net_all, 8)
    if day:
        out['day_start_date'] = day
        out['day_start_wallet'] = round(sow, 8)
        out['day_start_excludes_transfers'] = True
        out['transfers_today'] = round(transfers_today, 8)
    out['daily_stop'] = round(daily_stop_usdt(sow), 6)
    return out


def book_cap(book, wallet, risk_usdt=None, rs=None):
    """USDT risk for one book after the tier % and the 25% hard ceiling.

    Pass rs= from compute() so CORE/HIGH use their own percentages.
    """
    if rs is not None:
        if book == 'CORE':
            risk_usdt = float(rs.get('risk_usdt_core', rs.get('risk_usdt', 0)))
        else:
            risk_usdt = float(rs.get('risk_usdt_high', rs.get('risk_usdt', 0)))
    if risk_usdt is None:
        raise TypeError('book_cap requires risk_usdt or rs')
    return min(float(risk_usdt), float(wallet) * HARD_CEIL_PCT)


def save(rs, path=STATE):
    tmp = path + '.tmp'; json.dump(rs, open(tmp, 'w'), indent=2); os.replace(tmp, path)


def floor_step(q, step):
    """Round quantity DOWN to stepSize exactly (Decimal, no float round-up)."""
    if step <= 0 or q <= 0:
        return max(float(q), 0.0)
    d = Decimal(str(step))
    return float((Decimal(repr(q)) / d).to_integral_value(rounding=ROUND_FLOOR) * d)


def liq_price(entry, side, lev, mmr=MMR):
    """Conservative isolated-liq estimate (MMR added/subtracted toward entry)."""
    entry = float(entry); lev = max(1, int(lev)); mmr = float(mmr)
    if side == 'LONG':
        return entry * (1 - 1 / lev + mmr)
    return entry * (1 + 1 / lev - mmr)


def liq_ok(entry, sl, side, lev, mmr=MMR, mult=LIQ_SL_MULT):
    """True if estimated liq is on the SL side of entry and at least `mult` x SL-distance away."""
    liq = liq_price(entry, side, lev, mmr)
    sl_dist = abs(float(entry) - float(sl))
    liq_dist = abs(float(entry) - float(liq))
    if sl_dist <= 0:
        return False
    if side == 'LONG':
        if not (liq < float(sl)):
            return False
    else:
        if not (liq > float(sl)):
            return False
    return liq_dist + 1e-12 >= float(mult) * sl_dist


def leverage_for_sl(entry, sl, side, lev_max=LEV_HARD_MAX, mmr=MMR, mult=LIQ_SL_MULT):
    """Highest integer leverage whose isolated liq stays >= `mult` x SL distance away."""
    entry = float(entry); sl = float(sl)
    sl_pct = abs(entry - sl) / entry if entry else 0.0
    if sl_pct <= 0:
        return 1
    cap = 1.0 / (float(mult) * sl_pct + float(mmr))
    lev = max(1, min(int(lev_max), int(cap)))  # floor
    while lev > 1 and not liq_ok(entry, sl, side, lev, mmr, mult):
        lev -= 1
    return lev


def size_position(entry, sl, side, risk_budget, avail, step, min_qty=0, min_notional=5,
                  fee_rate=0.0004, lev_max=LEV_HARD_MAX, mmr=MMR, mult=LIQ_SL_MULT,
                  margin_frac=MARGIN_USE_FRAC):
    """Size a futures order: risk_budget at SL, isolated lev with liq >= 1.5x SL away.

    If available margin is tight, quantity is reduced to what fits (never above risk_budget).
    Returns a dict with ok=True and qty/lev/margin/liq, or ok=False and a reason.
    """
    entry = float(entry); sl = float(sl)
    risk_budget = float(risk_budget); avail = float(avail)
    step = float(step or 0)
    min_qty = float(min_qty or 0)
    min_notional = float(min_notional or 0)
    fee_rate = float(fee_rate)
    if side == 'SHORT':
        risk_per_unit = sl - entry
    else:
        risk_per_unit = entry - sl
    if entry <= 0 or risk_per_unit <= 0 or risk_budget <= 0:
        return dict(ok=False, code='invalid_risk', reason=f'invalid risk entry={entry} sl={sl} budget={risk_budget}')
    if avail <= 0:
        return dict(ok=False, code='no_margin', reason=f'available balance {avail} <= 0')

    lev = leverage_for_sl(entry, sl, side, lev_max=lev_max, mmr=mmr, mult=mult)
    if not liq_ok(entry, sl, side, lev, mmr, mult):
        liq = liq_price(entry, side, lev, mmr)
        return dict(ok=False, code='liq_not_beyond_sl',
                    reason=f'est liq {liq:.6g} not >= {mult}x beyond SL {sl:.6g} even at lev={lev}',
                    lev=lev, liq_est=liq)

    fee_buf = entry * fee_rate * 2
    qty_raw = risk_budget / (risk_per_unit + fee_buf)
    max_qty_margin = (avail * float(margin_frac) * lev) / entry
    sized_down = qty_raw > max_qty_margin + 1e-12
    if sized_down:
        qty_raw = max_qty_margin
    qty = floor_step(qty_raw, step)
    while qty > 0 and qty * (risk_per_unit + fee_buf) > risk_budget * (1 + 1e-9):
        qty = floor_step(qty - step, step)
        sized_down = True
    # if margin still slightly above 90% of avail (rounding), step down rather than fail
    while qty > 0 and (qty * entry / lev) > avail * 0.9 + 1e-12:
        qty = floor_step(qty - step, step)
        sized_down = True

    notional = qty * entry
    if qty <= 0 or qty < min_qty or notional < min_notional:
        return dict(ok=False, code='min_notional',
                    reason=f'qty={qty} (minQty {min_qty}) notional={notional:.2f} < min {min_notional} at risk {risk_budget:.4f} lev={lev}',
                    qty=qty, notional=notional, lev=lev, sized_down=sized_down)

    margin = notional / lev
    liq = liq_price(entry, side, lev, mmr)
    if not liq_ok(entry, sl, side, lev, mmr, mult):
        return dict(ok=False, code='liq_not_beyond_sl',
                    reason=f'est liq {liq:.6g} not >= {mult}x beyond SL {sl:.6g}',
                    qty=qty, notional=notional, lev=lev, margin=margin, liq_est=liq)

    sl_dist = abs(entry - sl)
    liq_dist = abs(entry - liq)
    risk_usdt = qty * risk_per_unit + qty * fee_buf
    return dict(
        ok=True, code='ok', reason='ok',
        qty=qty, notional=round(notional, 8), lev=int(lev),
        margin=round(margin, 8), liq_est=liq,
        sl_dist=sl_dist, sl_pct=sl_dist / entry,
        liq_dist=liq_dist, liq_vs_sl=liq_dist / sl_dist if sl_dist else None,
        risk_usdt=round(risk_usdt, 8), sized_down=bool(sized_down),
        risk_per_unit=risk_per_unit,
    )
