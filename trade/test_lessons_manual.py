#!/usr/bin/env python3
"""MANUAL and TEST setups are journaled but never drive update_lessons(). No API calls."""
import json, sys, tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import pos_manage as pm


def check(name, cond, detail=''):
    if not cond:
        raise AssertionError(f'FAIL {name}: {detail}')
    print('OK', name, detail)


def rec(setup, pnl, symbol='AAAUSDT', ts='2026-11-25T12:00+07:00', **kw):
    row = dict(ts_close=ts, ts_open='2026-11-25T10:00+07:00', symbol=symbol,
               book='HIGH', setup=setup, side='LONG', entry=1.0, sl=0.9, tp1=1.15, tp2=1.3,
               sl_pct=10.0, score=None, rr_plan=2.0, qty=1.0, exit=1.0 if pnl >= 0 else 0.9,
               exit_reason='SL' if pnl < 0 else 'TP1+rest', pnl_realized=pnl, fees=0.0,
               pnl_net=pnl, r_multiple=1.0 if pnl > 0 else -1.0, held_min=30.0,
               mae_pct=-1.0, mfe_pct=2.0, mfe_r=1.0, tp1_done=pnl > 0, note='')
    row.update(kw)
    return row


def main():
    tmp = Path(tempfile.mkdtemp(prefix='lessons_test_'))
    journal = tmp / 'trade_journal.jsonl'
    lessons = tmp / 'lessons.json'
    locked = tmp / 'lessons_locked.json'
    locked.write_text(json.dumps({'adjust': {}}))
    rows = [
        rec('BREAKOUT', 1.0, symbol='ARBUSDT', ts='2026-11-21T13:14+07:00'),
        rec('TEST', 23.0, symbol='SOLUSDT', ts='2026-11-21T10:33+07:00'),
        rec('MANUAL', 10.645, symbol='INJUSDT', ts='2026-11-24T05:11+07:00'),
        rec('MANUAL', -3.7419, symbol='SUIUSDT', ts='2026-11-23T19:47+07:00'),
    ]
    journal.write_text(''.join(json.dumps(r) + '\n' for r in rows))
    pm.D = str(tmp)
    pm.JOURNAL = str(journal)
    pm.LESSONS = str(lessons)
    # update_lessons also reads lessons_locked.json from D
    out = pm.update_lessons()
    check('lessons trade count excludes TEST and MANUAL', out['trades'] == 1, out['trades'])
    check('wins from BREAKOUT only', out['wins'] == 1 and out['losses'] == 0, (out['wins'], out['losses']))
    check('net_pnl ignores MANUAL/TEST', abs(out['net_pnl'] - 1.0) < 1e-9, out['net_pnl'])
    setups = {l.get('setup') for l in out.get('lessons') or [] if 'setup' in l}
    check('no MANUAL/TEST lesson rules', setups.isdisjoint({'MANUAL', 'TEST'}), setups)

    # two MANUAL losses must not create a setup_losing_streak
    rows.append(rec('MANUAL', -1.0, symbol='XXXUSDT', ts='2026-11-25T15:00+07:00'))
    rows.append(rec('MANUAL', -1.0, symbol='YYYUSDT', ts='2026-11-25T16:00+07:00'))
    journal.write_text(''.join(json.dumps(r) + '\n' for r in rows))
    out2 = pm.update_lessons()
    manual_rules = [l for l in out2.get('lessons') or [] if l.get('setup') == 'MANUAL']
    check('two MANUAL losses do not streak-penalize', manual_rules == [], manual_rules)
    check('trade count still 1', out2['trades'] == 1, out2['trades'])
    print('ALL_PASSED')


if __name__ == '__main__':
    main()
