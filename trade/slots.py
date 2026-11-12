#!/usr/bin/env python3
"""Concurrent position slots for the live bot.

CORE max 2 open-or-pending, HIGH (MAO HIEM) max 2 open-or-pending, combined max 4.
A pending maker/limit entry occupies a slot on its book. A book with 1 position
and 1 pending already uses 2 slots.
"""

MAX_CORE_SLOTS = 2
MAX_HIGH_SLOTS = 2
MAX_TOTAL_SLOTS = 4

# Same-direction pairs in a group are skipped. Slightly tighter than the old
# 3 groups, because 2 slots per book would otherwise allow two highly
# correlated HIGH (or CORE) longs at once.
CORR_GROUPS = [
    frozenset({'BTCUSDT', 'ETHUSDT', 'SOLUSDT', 'BNBUSDT'}),  # majors
    frozenset({'ETHUSDT', 'LINKUSDT', 'ARBUSDT', 'OPUSDT'}),  # ETH beta / L2s
    frozenset({'ARBUSDT', 'OPUSDT'}),  # ETH L2s (kept explicit)
    frozenset({'DOGEUSDT', '1000PEPEUSDT', 'WIFUSDT'}),  # memes
    frozenset({'AVAXUSDT', 'SUIUSDT', 'NEARUSDT', 'APTUSDT', 'TIAUSDT', 'SEIUSDT'}),  # alt L1s
]


def _norm_side(value):
    if value is None:
        return None
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if value > 0:
            return 'LONG'
        if value < 0:
            return 'SHORT'
        return None
    s = str(value).upper()
    if s in ('LONG', 'BUY'):
        return 'LONG'
    if s in ('SHORT', 'SELL'):
        return 'SHORT'
    return None


def held_dir(item):
    if not isinstance(item, dict):
        return None
    if item.get('amt') not in (None, 0, '0', 0.0):
        try:
            amt = float(item['amt'])
        except (TypeError, ValueError):
            amt = 0.0
        if amt != 0:
            return 'LONG' if amt > 0 else 'SHORT'
    return _norm_side(item.get('side') or item.get('order_side'))


def pending_entries(orders, open_syms, core, high):
    """Non-reduceOnly LIMIT entries whose symbol is not already an open position.

    One slot per symbol (duplicate working limits on the same symbol count once).
    """
    out = []
    seen = set()
    open_syms = set(open_syms or [])
    for o in orders or []:
        if o.get('type') != 'LIMIT' or o.get('reduceOnly'):
            continue
        sym = o.get('symbol')
        if not sym or sym in open_syms or sym in seen:
            continue
        port = 'CORE' if sym in core else ('HIGH' if sym in high else None)
        if not port:
            continue
        side = _norm_side(o.get('side')) or 'LONG'
        seen.add(sym)
        out.append({
            'symbol': sym,
            'port': port,
            'side': side,
            'amt': 1.0 if side == 'LONG' else -1.0,
            'pending': True,
        })
    return out


def count_slots(positions, pendings, core=None, high=None):
    """Count open-or-pending slots per book.

    `pendings` may be a list of dicts (with symbol/port) or a set/list of symbols
    (port inferred from `core`/`high`).
    """
    core = set(core or [])
    high = set(high or [])
    seen = set()
    core_used = 0
    high_used = 0
    for p in positions or []:
        port = p.get('port')
        sym = p.get('symbol') or p.get('sym')
        if port not in ('CORE', 'HIGH'):
            if sym in core:
                port = 'CORE'
            elif sym in high:
                port = 'HIGH'
            else:
                continue
        if sym:
            seen.add(sym)
        if port == 'CORE':
            core_used += 1
        else:
            high_used += 1
    for pend in pendings or []:
        if isinstance(pend, dict):
            sym = pend.get('symbol') or pend.get('sym')
            port = pend.get('port')
        else:
            sym = pend
            port = None
        if not sym or sym in seen:
            continue
        if port not in ('CORE', 'HIGH'):
            if sym in core:
                port = 'CORE'
            elif sym in high:
                port = 'HIGH'
            else:
                continue
        seen.add(sym)
        if port == 'CORE':
            core_used += 1
        else:
            high_used += 1
    total = core_used + high_used
    core_room = max(0, MAX_CORE_SLOTS - core_used)
    high_room = max(0, MAX_HIGH_SLOTS - high_used)
    if total >= MAX_TOTAL_SLOTS:
        core_room = 0
        high_room = 0
    return {
        'core_used': core_used,
        'high_used': high_used,
        'total_used': total,
        'core_slots_free': core_room,
        'high_slots_free': high_room,
        'core_free': core_room > 0,
        'high_free': high_room > 0,
        'slots_ok': core_room > 0 or high_room > 0,
    }


def already_held(cand_sym, held):
    return any((h.get('symbol') or h.get('sym')) == cand_sym for h in (held or []) if isinstance(h, dict))


def is_correlated(cand_sym, cand_side, held, groups=None):
    """True if cand would be same-direction in a correlation group as something already held/pending.

    Same symbol is always treated as correlated (do not double-enter).
    Opposite direction in the same group is allowed.
    """
    groups = groups or CORR_GROUPS
    side = _norm_side(cand_side)
    for op in held or []:
        osym = op.get('symbol') or op.get('sym') if isinstance(op, dict) else None
        if not osym:
            continue
        if osym == cand_sym:
            return True
        if side is None or held_dir(op) != side:
            continue
        for g in groups:
            if cand_sym in g and osym in g:
                return True
    return False
