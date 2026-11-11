"""Wallet-adaptive risk per trade (approved 2026-11-11, t50u; small-wallet t52u/t53u/t54s275).
base: walletBalance < 100 USDT is per-book — CORE 2.5% (SMALL core), HIGH/MAO HIEM 4%;
>= 100 USDT -> 1.0% for both books;
DD multipliers apply to both books (x0.75 / x0.5);
drawdown from peak (high-water mark in risk_state.json): >=10% -> x0.75, >=20% -> x0.5; a reduced tier stays
(sticky, never loosens mid-drawdown) until DD < 5%;
scale-up only on journal trades opened since SINCE, and only for the >=100 tier:
>=100 trades, expectancy>0, PF>1.1 -> 1.5%;
>=200 trades same conditions and DD<10% -> 2.0%.
Hard ceilings: small CORE 2.5%, small HIGH 4%, >=100 tier 2% both books;
per-book old caps still apply (CORE <=2.0 USDT, HIGH <=15% wallet).
Daily stop uses the larger book's per-trade risk: max(-7.5, -3 x HIGH risk on the small tier)."""
import json, os, time, datetime
D = '/workspace/trade'
STATE = f'{D}/risk_state.json'
JOURNAL = f'{D}/trade_journal.jsonl'
SINCE = '2026-11-11T11:30+07:00'   # trades opened after the maker/adaptive change
BASE, CEIL = 0.010, 0.020          # >=100 USDT: 1% base, 2% hard ceiling (both books)
SMALL_WALLET = 100.0
SMALL_CORE_BASE, SMALL_CORE_CEIL = 0.025, 0.025  # t53u
SMALL_HIGH_BASE, SMALL_HIGH_CEIL = 0.040, 0.040  # t54s275: MAO HIEM 4%
# aliases kept so older comments/imports still read the CORE small base
SMALL_BASE, SMALL_CEIL = SMALL_CORE_BASE, SMALL_CORE_CEIL
CORE_CAP_USDT, HIGH_CAP_PCT = 2.0, 0.15

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

def compute(wallet, st=None, js=None):
    st = dict(st or {}); js = js or journal_stats()
    wallet = float(wallet)
    peak = max(float(st.get('peak') or 0), wallet)
    dd = (1 - wallet/peak) if peak > 0 else 0.0
    prev_dd_mult = float(st.get('dd_mult', 1.0))
    need = 0.5 if dd >= 0.20 else (0.75 if dd >= 0.10 else 1.0)
    if dd < 0.05: dd_mult = need                      # recovered: back to normal
    else: dd_mult = min(need, prev_dd_mult)           # sticky: keep the lowest tier until DD < 5%
    dd_tag = '' if dd_mult >= 1.0 else ('_DD20' if dd_mult <= 0.5 else '_DD10')
    if wallet < SMALL_WALLET:
        # small wallet: CORE 2.5%, HIGH 4%; no track-record scale-up (already at each book's ceiling)
        pct_core = min(SMALL_CORE_BASE * dd_mult, SMALL_CORE_CEIL)
        pct_high = min(SMALL_HIGH_BASE * dd_mult, SMALL_HIGH_CEIL)
        base_core, base_high = SMALL_CORE_BASE, SMALL_HIGH_BASE
        tier = 'SMALL_2_5_4pct'
        # legacy single-value fields follow the larger book (HIGH) so daily stop has room for a HIGH trade
        base, pct = base_high, pct_high
    else:
        good = js['n'] >= 100 and js['expectancy'] > 0 and js['pf'] > 1.1
        base = BASE
        if dd_mult < 1.0:
            pct, tier = BASE * dd_mult, 'BASE_1pct'
        elif good and js['n'] >= 200 and dd < 0.10:
            pct, tier = 0.020, 'SCALE2'
        elif good:
            pct, tier = 0.015, 'SCALE1.5'
        else:
            pct, tier = BASE, 'BASE_1pct'
        pct = min(pct, CEIL)
        pct_core = pct_high = pct
        base_core = base_high = base
    tier += dd_tag
    out = dict(peak=round(peak, 8), wallet=round(wallet, 8), dd=round(dd, 4), dd_mult=dd_mult,
               base_pct=base, risk_pct=round(pct, 6),
               base_pct_core=base_core, base_pct_high=base_high,
               risk_pct_core=round(pct_core, 6), risk_pct_high=round(pct_high, 6),
               risk_usdt=round(wallet*pct, 6),
               risk_usdt_core=round(wallet*pct_core, 6), risk_usdt_high=round(wallet*pct_high, 6),
               tier=tier, journal=js, since=SINCE,
               updated=datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=7))).strftime('%Y-%m-%dT%H:%M:%S+07:00'))
    return out

def book_cap(book, wallet, risk_usdt=None, rs=None):
    """USDT risk for one book after the tier % and the old caps (CORE <=2 USDT, HIGH <=15% wallet).
    Pass rs= from compute() so small-wallet CORE/HIGH use their own percentages."""
    if rs is not None:
        if book == 'CORE':
            risk_usdt = float(rs.get('risk_usdt_core', rs.get('risk_usdt', 0)))
        else:
            risk_usdt = float(rs.get('risk_usdt_high', rs.get('risk_usdt', 0)))
    if risk_usdt is None:
        raise TypeError('book_cap requires risk_usdt or rs')
    return min(float(risk_usdt), CORE_CAP_USDT if book == 'CORE' else float(wallet) * HIGH_CAP_PCT)

def save(rs, path=STATE):
    tmp = path + '.tmp'; json.dump(rs, open(tmp, 'w'), indent=2); os.replace(tmp, path)
