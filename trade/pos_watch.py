#!/usr/bin/env python3
"""Background position watcher: every ~60s run shared manage_positions (TP1 -> BE, close -> journal+lessons+Telegram).
Shares /workspace/trade/.pos.lock with run_hourly.py, so actions never overlap. Exits when flat and no pending entry."""
import sys, time, json, datetime, os
sys.path.insert(0, '/workspace/trade'); sys.path.insert(0, '/workspace')
import pos_manage as pm
from bn import call, BASE

LOG = '/workspace/trade/pos_watch.log'
PID = '/workspace/trade/pos_watch.pid'
_ban_until = 0

def log(*a):
    with open(LOG, 'a') as f:
        f.write(f"{pm.now_s()} {' '.join(str(x) for x in a)}\n")

def sc(path, params=None, method='GET'):
    global _ban_until
    if time.time() < _ban_until:
        return {'err': 'rate_limited', 'http': 418}
    time.sleep(0.2)
    try:
        r = call(BASE, path, params or {}, method)
    except Exception as e:
        return {'err': str(e)}
    if isinstance(r, dict) and (r.get('http') in (418, 429) or r.get('code') == -1003 or 'banned' in str(r)):
        _ban_until = time.time() + 300; log('rate limited, backoff 300s', str(r)[:150])
    return r

def main():
    open(PID, 'w').write(str(os.getpid()))
    log('start')
    idle = 0
    while True:
        try:
            with pm.Lock(blocking=False) as got:
                if got:
                    ev = pm.manage_positions(sc, dry=False, log=log)
                    for e in ev:
                        if e.get('event') not in ('BE_wait_price',):
                            log('event', json.dumps(e, ensure_ascii=False)[:400])
                    st = pm.load_state()
                    pr = sc('/fapi/v2/positionRisk')
                    if isinstance(pr, list):
                        has_pos = any(float(p['positionAmt']) != 0 for p in pr)
                        if not has_pos and not st:
                            idle += 1
                            if idle >= 2:
                                log('flat, no pending -> exit'); break
                        else:
                            idle = 0
                else:
                    log('lock busy (hourly running), skip')
        except Exception as e:
            log('err', repr(e)[:300])
        time.sleep(60)
    try: os.remove(PID)
    except Exception: pass

if __name__ == '__main__':
    main()
