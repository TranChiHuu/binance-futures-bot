#!/usr/bin/env python3
"""READ-ONLY: pull income, userTrades and allOrders since 2026-11-02 (Saigon) via /workspace/bn.py.
Never places/cancels orders. Output: data/account_*.json"""
import sys, json, time, datetime, os
sys.path.insert(0, '/workspace'); import bn
D = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'data')
SG = datetime.timezone(datetime.timedelta(hours=7))
START = int(datetime.datetime(2026, 11, 2, tzinfo=SG).timestamp() * 1000)
NOW = int(time.time() * 1000)
def c(path, p):
    for _ in range(4):
        r = bn.call(bn.BASE, path, p, 'GET')
        if isinstance(r, dict) and r.get('http') in (418, 429):
            print('backoff', r.get('http')); time.sleep(120); continue
        time.sleep(0.5); return r
    return []
inc, s = [], START
while True:
    r = c('/fapi/v1/income', {'startTime': s, 'endTime': NOW, 'limit': 1000})
    if not isinstance(r, list) or not r: break
    inc += r; s = int(r[-1]['time']) + 1
    if len(r) < 1000: break
json.dump(inc, open(f'{D}/account_income.json', 'w'))
syms = sorted({i['symbol'] for i in inc if i.get('symbol')})
print('income rows', len(inc), 'symbols', syms)
trades, orders = {}, {}
for sym in syms:
    tr = []
    # userTrades window max 7 days
    t0 = START
    while t0 < NOW:
        t1 = min(t0 + 7*86400*1000 - 1, NOW)
        r = c('/fapi/v1/userTrades', {'symbol': sym, 'startTime': t0, 'endTime': t1, 'limit': 1000})
        if isinstance(r, list): tr += r
        t0 = t1 + 1
    trades[sym] = tr
    od = []
    t0 = START
    while t0 < NOW:
        t1 = min(t0 + 7*86400*1000 - 1, NOW)
        r = c('/fapi/v1/allOrders', {'symbol': sym, 'startTime': t0, 'endTime': t1, 'limit': 1000})
        if isinstance(r, list): od += r
        t0 = t1 + 1
    orders[sym] = od
    print(sym, 'trades', len(tr), 'orders', len(od))
json.dump(trades, open(f'{D}/account_userTrades.json', 'w'))
json.dump(orders, open(f'{D}/account_allOrders.json', 'w'))
