#!/usr/bin/env python3
"""Offline unit checks for shallow-breakout skip and 1h-close invalidation. No API calls."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
import breakout_rules as B


def check(name, cond, detail=''):
    if not cond:
        raise AssertionError(f'FAIL {name}: {detail}')
    print('OK', name, detail)


def main():
    # Journal cases 2026-11-12 (ATR backed out of past / past_atr in the note)
    avax_atr = (8.579 - 8.546) / 0.38  # mark-based note; still << 0.75
    tia_past = 0.4052 - 0.4014
    tia_atr = tia_past / 0.64
    arb_past = 0.99 * 0.01  # dummy atr=0.01 -> 0.99 ATR
    check('AVAX 0.38 ATR is shallow', B.is_shallow_breakout(0.38 * avax_atr, avax_atr))
    check('TIA 0.64 ATR is shallow', B.is_shallow_breakout(0.64 * tia_atr, tia_atr))
    check('ARB 0.99 ATR is not shallow', not B.is_shallow_breakout(0.99 * 0.01, 0.01))
    check('exactly 0.75 ATR is not shallow', not B.is_shallow_breakout(0.75 * 1.0, 1.0))
    check('just under 0.75 is shallow', B.is_shallow_breakout(0.749 * 1.0, 1.0))
    check('missing ATR does not skip', not B.is_shallow_breakout(0.1, 0))
    check('None ATR does not skip', not B.is_shallow_breakout(0.1, None))

    # LONG: close back under the level
    check('LONG close below level is invalid', B.level_invalidated('LONG', 8.50, 8.546, atr=0.2))
    check('LONG close still above level is valid', not B.level_invalidated('LONG', 8.58, 8.546, atr=0.2))
    # hairline: 0.05*0.2=0.01, close 8.545 is only 0.001 through -> not invalid
    check('LONG hairline close is not invalid', not B.level_invalidated('LONG', 8.545, 8.546, atr=0.2))
    # SHORT: close back above the level (TIA)
    check('SHORT close above level is invalid', B.level_invalidated('SHORT', 0.412, 0.4052, atr=0.006))
    check('SHORT close still below level is valid', not B.level_invalidated('SHORT', 0.4014, 0.4052, atr=0.006))
    check('wick does not matter, only close', not B.level_invalidated('SHORT', 0.404, 0.4052, atr=0.006))
    check('no level -> not invalid', not B.level_invalidated('SHORT', 0.42, None, atr=0.006))
    check('bad side -> not invalid', not B.level_invalidated('FLAT', 0.42, 0.40, atr=0.006))

    check('parse lvl from AVAX note', B.parse_level('lvl=8.546 past=0.38ATR lesson-0.25') == 8.546)
    check('parse lvl from TIA note', abs(B.parse_level('lvl=0.4052 past=0.64ATR lesson-0.25 maker@0.4014') - 0.4052) < 1e-9)
    check('parse empty', B.parse_level('') is None and B.parse_level(None) is None)
    print('ALL OK')


if __name__ == '__main__':
    main()
