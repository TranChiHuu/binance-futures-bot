#!/usr/bin/env python3
"""Offline unit checks for concurrent slot counting and correlation. No API calls."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
import slots as S

CORE = ['BTCUSDT','ETHUSDT','SOLUSDT','BNBUSDT','XRPUSDT','LINKUSDT']
HIGH = ['DOGEUSDT','SUIUSDT','AVAXUSDT','1000PEPEUSDT','WIFUSDT','NEARUSDT','APTUSDT',
        'INJUSDT','TIAUSDT','SEIUSDT','WLDUSDT','ARBUSDT','OPUSDT']


def check(name, cond, detail=''):
    if not cond:
        raise AssertionError(f'FAIL {name}: {detail}')
    print('OK', name, detail)


def main():
    empty = S.count_slots([], [], CORE, HIGH)
    check('empty core 0', empty['core_used'] == 0)
    check('empty high 0', empty['high_used'] == 0)
    check('empty core 2 free', empty['core_slots_free'] == 2 and empty['core_free'])
    check('empty high 2 free', empty['high_slots_free'] == 2 and empty['high_free'])
    check('empty slots_ok', empty['slots_ok'])

    # Live snapshot after this change: 1 open HIGH AVAX long, no pending
    avax = [{'symbol': 'AVAXUSDT', 'port': 'HIGH', 'amt': 8.0}]
    live = S.count_slots(avax, [], CORE, HIGH)
    check('live high used 1', live['high_used'] == 1, live)
    check('live core used 0', live['core_used'] == 0)
    check('live high 1 free', live['high_slots_free'] == 1 and live['high_free'])
    check('live core 2 free', live['core_slots_free'] == 2 and live['core_free'])
    check('live total 1', live['total_used'] == 1 and live['slots_ok'])

    # 1 pos + 1 pending on HIGH already uses 2 HIGH slots
    pend_doge = [{'symbol': 'DOGEUSDT', 'port': 'HIGH', 'side': 'LONG', 'pending': True}]
    full_high = S.count_slots(avax, pend_doge, CORE, HIGH)
    check('high pos+pending = 2', full_high['high_used'] == 2 and full_high['high_slots_free'] == 0)
    check('high full, core still 2 free', full_high['core_slots_free'] == 2 and full_high['slots_ok'])

    # Pending also inferred from raw open-order shape
    orders = [
        {'symbol': 'BTCUSDT', 'type': 'LIMIT', 'reduceOnly': False, 'side': 'BUY'},
        {'symbol': 'AVAXUSDT', 'type': 'LIMIT', 'reduceOnly': True, 'side': 'SELL'},  # TP, not a slot
    ]
    pends = S.pending_entries(orders, open_syms={'AVAXUSDT'}, core=CORE, high=HIGH)
    check('pending BTC only', [p['symbol'] for p in pends] == ['BTCUSDT'] and pends[0]['port'] == 'CORE')
    mixed = S.count_slots(avax, pends, CORE, HIGH)
    check('1 HIGH open + 1 CORE pending', mixed['high_used'] == 1 and mixed['core_used'] == 1
          and mixed['core_slots_free'] == 1 and mixed['high_slots_free'] == 1)

    # Duplicate pending on same symbol counts once
    dup_orders = [
        {'symbol': 'ETHUSDT', 'type': 'LIMIT', 'reduceOnly': False, 'side': 'BUY'},
        {'symbol': 'ETHUSDT', 'type': 'LIMIT', 'reduceOnly': False, 'side': 'BUY'},
    ]
    dups = S.pending_entries(dup_orders, open_syms=set(), core=CORE, high=HIGH)
    check('dedupe pending symbol', len(dups) == 1)
    c = S.count_slots([], dups, CORE, HIGH)
    check('deduped pending uses 1 CORE', c['core_used'] == 1)

    two_core_two_high = S.count_slots(
        [{'symbol': 'BTCUSDT', 'port': 'CORE'}, {'symbol': 'ETHUSDT', 'port': 'CORE'},
         {'symbol': 'DOGEUSDT', 'port': 'HIGH'}, {'symbol': 'INJUSDT', 'port': 'HIGH'}],
        [], CORE, HIGH)
    check('4 full, no room', two_core_two_high['total_used'] == 4 and not two_core_two_high['slots_ok']
          and two_core_two_high['core_slots_free'] == 0 and two_core_two_high['high_slots_free'] == 0)

    two_core = S.count_slots(
        [{'symbol': 'BTCUSDT', 'port': 'CORE'}, {'symbol': 'XRPUSDT', 'port': 'CORE'}],
        [], CORE, HIGH)
    check('2 CORE full, HIGH 2 free', two_core['core_free'] is False and two_core['high_slots_free'] == 2
          and two_core['slots_ok'])

    # Correlation
    held_avax = [{'symbol': 'AVAXUSDT', 'port': 'HIGH', 'amt': 8.0, 'side': 'LONG'}]
    check('AVAX long blocks SUI long', S.is_correlated('SUIUSDT', 'LONG', held_avax))
    check('AVAX long allows SUI short', not S.is_correlated('SUIUSDT', 'SHORT', held_avax))
    check('AVAX long allows DOGE long (other group)', not S.is_correlated('DOGEUSDT', 'LONG', held_avax))
    check('AVAX long blocks NEAR long', S.is_correlated('NEARUSDT', 'LONG', held_avax))
    check('same symbol blocked', S.is_correlated('AVAXUSDT', 'SHORT', held_avax))
    check('already_held AVAX', S.already_held('AVAXUSDT', held_avax))
    check('not already_held SUI', not S.already_held('SUIUSDT', held_avax))

    held_doge = [{'symbol': 'DOGEUSDT', 'amt': 1}]
    check('DOGE long blocks PEPE long', S.is_correlated('1000PEPEUSDT', 'LONG', held_doge))
    check('DOGE long blocks WIF long', S.is_correlated('WIFUSDT', 'LONG', held_doge))

    held_btc = [{'symbol': 'BTCUSDT', 'side': 'LONG', 'amt': 0.01}]
    check('BTC long blocks ETH long', S.is_correlated('ETHUSDT', 'LONG', held_btc))
    check('BTC long allows XRP long', not S.is_correlated('XRPUSDT', 'LONG', held_btc))

    held_eth = [{'symbol': 'ETHUSDT', 'side': 'LONG', 'amt': 1}]
    check('ETH long blocks LINK long', S.is_correlated('LINKUSDT', 'LONG', held_eth))
    check('ETH long blocks ARB long', S.is_correlated('ARBUSDT', 'LONG', held_eth))

    pend_eth = S.pending_entries(
        [{'symbol': 'ETHUSDT', 'type': 'LIMIT', 'reduceOnly': False, 'side': 'BUY'}],
        open_syms=set(), core=CORE, high=HIGH)
    check('pending ETH BUY blocks SOL LONG', S.is_correlated('SOLUSDT', 'LONG', pend_eth))
    check('pending ETH BUY allows SOL SHORT', not S.is_correlated('SOLUSDT', 'SHORT', pend_eth))

    print('ALL_PASSED')


if __name__ == '__main__':
    main()
