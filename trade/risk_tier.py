"""Wallet-adaptive risk per trade (approved 2026-11-11, t50u; small-wallet tier t52u).
base: walletBalance < 100 USDT -> 2.0% (SMALL_2pct, no scale-up); >= 100 -> 1.0%;
DD multipliers below apply to either base (x0.75 / x0.5);
drawdown from peak (high-water mark in risk_state.json): >=10% -> 0.75%, >=20% -> 0.5%; a reduced tier stays
(sticky, never loosens mid-drawdown) until DD < 5%;
scale-up only on journal trades opened since SINCE: >=100 trades, expectancy>0, PF>1.1 -> 1.5%;
>=200 trades same conditions and DD<10% -> 2.0%. Hard ceiling 2%; per-book old caps (CORE <=2.0 USDT, HIGH <=15% wallet)."""
import json, os, time, datetime
D = '/workspace/trade'
STATE = f'{D}/risk_state.json'
JOURNAL = f'{D}/trade_journal.jsonl'
SINCE = '2026-11-11T11:30+07:00'   # trades opened after the maker/adaptive change
BASE, CEIL = 0.010, 0.020
SMALL_WALLET, SMALL_BASE = 100.0, 0.020   # t52u: wallet < 100 USDT -> 2% base
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
        # small wallet (t52u): trade harder, 2% base; no track-record scale-up (already at ceiling)
        base, tier = SMALL_BASE, 'SMALL_2pct'
        pct = base * dd_mult
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
    tier += dd_tag
    pct = min(pct, CEIL)
    out = dict(peak=round(peak, 8), wallet=round(wallet, 8), dd=round(dd, 4), dd_mult=dd_mult, base_pct=base, risk_pct=round(pct, 6),
               tier=tier, risk_usdt=round(wallet*pct, 6), journal=js, since=SINCE,
               updated=datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=7))).strftime('%Y-%m-%dT%H:%M:%S+07:00'))
    return out

def book_cap(book, wallet, risk_usdt):
    return min(risk_usdt, CORE_CAP_USDT if book == 'CORE' else wallet * HIGH_CAP_PCT)

def save(rs, path=STATE):
    tmp = path + '.tmp'; json.dump(rs, open(tmp, 'w'), indent=2); os.replace(tmp, path)
