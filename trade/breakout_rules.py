"""Breakout quality and invalidation rules.

Tightened 2026-11-12 after two HIGH BREAKOUT stop-outs:
- AVAX LONG past=0.38 ATR, held ~29h, MFE 0.79R, never TP1
- TIA SHORT past=0.64 ATR, held ~6.7h, MFE 0.36R, never TP1
The prior winning ARB SHORT breakout had past=0.99 ATR.
Lessons may only tighten, never loosen.
"""
import re

# Skip a fresh BREAKOUT if the break candle is shallower than this (x ATR(14,1h)).
MIN_BREAKOUT_PAST_ATR = 0.75
# 1h close must travel this far through the level (x ATR) before we flatten.
INVALID_CLOSE_ATR = 0.05
# Fallback when ATR is missing: 0.05% of the level.
INVALID_CLOSE_PCT = 0.0005

_LVL_RE = re.compile(r'lvl=([0-9]+(?:\.[0-9]+)?)')


def parse_level(note):
    if not note:
        return None
    m = _LVL_RE.search(str(note))
    return float(m.group(1)) if m else None


def is_shallow_breakout(past, atr, min_mult=MIN_BREAKOUT_PAST_ATR):
    """True when the break is too shallow to take. If ATR is missing, do not skip."""
    try:
        past = float(past)
        atr = float(atr)
    except (TypeError, ValueError):
        return False
    if atr <= 0:
        return False
    return past < min_mult * atr


def _tol(level, atr):
    try:
        if atr is not None and float(atr) > 0:
            return INVALID_CLOSE_ATR * float(atr)
    except (TypeError, ValueError):
        pass
    try:
        return abs(float(level)) * INVALID_CLOSE_PCT
    except (TypeError, ValueError):
        return 0.0


def level_invalidated(side, last_close, level, atr=None):
    """True when a *closed* candle has closed back through the breakout level.

    Wicks that tag the level do not count; only the close. A small ATR buffer
    avoids flattening on a hairline close.
    """
    if side not in ('LONG', 'SHORT') or last_close is None or level is None:
        return False
    try:
        last_close = float(last_close)
        level = float(level)
    except (TypeError, ValueError):
        return False
    tol = _tol(level, atr)
    if side == 'LONG':
        return last_close < level - tol
    return last_close > level + tol
