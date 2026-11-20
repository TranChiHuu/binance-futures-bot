"""Wallet-adaptive risk per trade.

2026-11-20 (user approved t103u / follow-up): both books risk 25% of wallet per
trade; user accepted the risk of blowing up the account. Drawdown multipliers
still apply (x0.75 / x0.5) but cannot push risk ABOVE 25%. Daily stop is -50%
of the Saigon start-of-day wallet (replaces max(-7.5, -3x risk)).

Older small-wallet 2.5%/4% and >=100 1–2% scale-up paths are retired; aliases
remain so older comments/imports still resolve, all pointing at 25%.
"""
import json, os, time, datetime
from decimal import Decimal, ROUND_FLOOR

D = '/workspace/trade'
STATE = f'{D}/risk_state.json'
JOURNAL = f'{D}/trade_journal.jsonl'
SINCE = '2026-11-11T11:30+07:00'   # trades opened after the maker/adaptive change

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


def start_of_day_wallet(st, wallet, day, realized=0.0, commission=0.0):
    """Reuse today's snapshot, or back out realized+commission on the first run of the day."""
    st = st or {}
    if st.get('day_start_date') == day and st.get('day_start_wallet') not in (None, ''):
        try:
            sow = float(st['day_start_wallet'])
            if sow > 0:
                return sow
        except (TypeError, ValueError):
            pass
    sow = float(wallet) - float(realized or 0) - float(commission or 0)
    if sow <= 0:
        sow = float(wallet)
    return sow


def compute(wallet, st=None, js=None, day=None, realized=0.0, commission=0.0):
    st = dict(st or {}); js = js or journal_stats()
    wallet = float(wallet)
    peak = max(float(st.get('peak') or 0), wallet)
    dd = ((peak - wallet) / peak) if peak > 0 else 0.0
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
               updated=datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=7))).strftime('%Y-%m-%dT%H:%M:%S+07:00'))
    sow = start_of_day_wallet(st, wallet, day, realized, commission) if day else float(wallet)
    if day:
        out['day_start_date'] = day
        out['day_start_wallet'] = round(sow, 8)
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
