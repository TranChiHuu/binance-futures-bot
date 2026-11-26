#!/usr/bin/env python3
"""+1R break-even rule: move SL to BE before TP1. No live API calls."""
import json, sys, tempfile, time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import pos_manage as pm


def check(name, cond, detail=''):
    if not cond:
        raise AssertionError(f'FAIL {name}: {detail}')
    print('OK', name, detail)


# SEI TEST numbers from 2026-11-26
SEI_ENTRY = 0.03576
SEI_SL = 0.03416
SEI_R = 0.0016          # |0.03576 - 0.03416|
SEI_1R = 0.03736        # entry + 1R
SEI_HIGH = 0.03785      # ~1.3R, then gave back
SEI_TICK = 0.00001
SEI_BE = 0.03580        # tick_round(0.03576 * 1.001, up)


def test_helpers():
    check('r_abs SEI', abs(pm.r_abs(SEI_ENTRY, SEI_SL) - SEI_R) < 1e-12, pm.r_abs(SEI_ENTRY, SEI_SL))
    check('r_abs equal is None', pm.r_abs(1.0, 1.0) is None)
    check('r_abs bad is None', pm.r_abs(None, 1) is None)

    check('reached_1r at exact 1R long', pm.reached_1r('LONG', SEI_ENTRY, SEI_SL, SEI_1R))
    check('reached_1r just below 1R long is False',
          not pm.reached_1r('LONG', SEI_ENTRY, SEI_SL, SEI_1R - SEI_TICK))
    check('reached_1r SEI 1.3R high', pm.reached_1r('LONG', SEI_ENTRY, SEI_SL, SEI_HIGH))
    check('reached_1r at entry is False', not pm.reached_1r('LONG', SEI_ENTRY, SEI_SL, SEI_ENTRY))

    check('reached_1r short at 1R', pm.reached_1r('SHORT', 100.0, 110.0, 90.0))
    check('reached_1r short just above 1R is False', not pm.reached_1r('SHORT', 100.0, 110.0, 90.01))
    check('reached_1r short beyond 1R', pm.reached_1r('SHORT', 100.0, 110.0, 89.0))

    be = pm.be_trigger(SEI_ENTRY, True, SEI_TICK)
    check('be_trigger SEI long is 0.03580', be == SEI_BE, be)
    be_s = pm.be_trigger(100.0, False, 0.01)
    # 100 * (1-0.001) = 99.9, tick 0.01 down -> 99.90
    check('be_trigger short fee buffer down', abs(be_s - 99.90) < 1e-9, be_s)


def test_plan():
    # 1R not reached
    p = pm.plan_1r_sl('LONG', SEI_ENTRY, SEI_SL, fav_px=0.0365, mark=0.0365,
                      cur_sl=SEI_SL, tick=SEI_TICK)
    check('plan skip when not 1R', p['action'] == 'skip' and p['reason'] == 'not_1r', p)

    # 1R reached, mark above BE -> move to BE
    p = pm.plan_1r_sl('LONG', SEI_ENTRY, SEI_SL, fav_px=SEI_HIGH, mark=0.0370,
                      cur_sl=SEI_SL, tick=SEI_TICK)
    check('plan move to BE when mark above BE', p['action'] == 'move' and p['kind'] == 'be', p)
    check('plan BE trigger 0.03580', p['trigger'] == SEI_BE, p)
    check('plan has near-mark fallback', p['fallback'] is not None and p['fallback'] < 0.0370, p)

    # 1R reached (historical high) but mark retraced below BE -> near mark
    mark = 0.03550  # between original SL and BE
    p = pm.plan_1r_sl('LONG', SEI_ENTRY, SEI_SL, fav_px=SEI_HIGH, mark=mark,
                      cur_sl=SEI_SL, tick=SEI_TICK)
    check('plan near_mark when mark below BE', p['action'] == 'move' and p['kind'] == 'near_mark', p)
    check('near_mark sl is under mark and tighter than old SL',
          p['trigger'] < mark and p['trigger'] > SEI_SL, p)
    # ~ mark * 0.997
    expect = pm.near_mark_sl(True, mark, SEI_TICK)
    check('near_mark matches helper', p['trigger'] == expect, (p['trigger'], expect))

    # already at BE
    p = pm.plan_1r_sl('LONG', SEI_ENTRY, SEI_SL, fav_px=SEI_HIGH, mark=0.0370,
                      cur_sl=SEI_BE, tick=SEI_TICK)
    check('plan already when SL at BE', p['action'] == 'already', p)

    # SL already tighter than BE (e.g. moved past BE) 
    p = pm.plan_1r_sl('LONG', SEI_ENTRY, SEI_SL, fav_px=SEI_HIGH, mark=0.0370,
                      cur_sl=0.03600, tick=SEI_TICK)
    check('plan already when SL beyond BE', p['action'] == 'already', p)

    # short: 1R reached, mark below BE -> BE
    p = pm.plan_1r_sl('SHORT', 100.0, 110.0, fav_px=89.0, mark=95.0,
                      cur_sl=110.0, tick=0.01)
    check('plan short move to BE', p['action'] == 'move' and p['kind'] == 'be', p)
    check('plan short BE ~99.90', abs(p['trigger'] - 99.90) < 1e-9, p)

    # short: mark retraced above BE -> near mark (above mark)
    p = pm.plan_1r_sl('SHORT', 100.0, 110.0, fav_px=89.0, mark=100.2,
                      cur_sl=110.0, tick=0.01)
    check('plan short near_mark', p['action'] == 'move' and p['kind'] == 'near_mark', p)
    check('short near_mark above mark', p['trigger'] > 100.2, p)

    # cannot tighten: mark below original SL (would loosen)
    p = pm.plan_1r_sl('LONG', SEI_ENTRY, SEI_SL, fav_px=SEI_HIGH, mark=0.03300,
                      cur_sl=SEI_SL, tick=SEI_TICK)
    check('plan skip when near-mark would loosen', p['action'] == 'skip' and p['reason'] == 'cannot_tighten', p)


def _pos(sym, amt, entry, mark):
    return {'symbol': sym, 'positionAmt': str(amt), 'entryPrice': str(entry), 'markPrice': str(mark)}


def _algo(sym, typ, trigger, algo_id):
    return {'symbol': sym, 'orderType': typ, 'triggerPrice': str(trigger), 'algoId': algo_id}


def test_manage_positions():
    tmp = Path(tempfile.mkdtemp(prefix='be1r_'))
    state_path = tmp / 'pos_state.json'
    lessons_path = tmp / 'lessons.json'
    journal_path = tmp / 'trade_journal.jsonl'
    pm.D = str(tmp)
    pm.STATE = str(state_path)
    pm.LESSONS = str(lessons_path)
    pm.JOURNAL = str(journal_path)
    pm.tg = lambda *a, **k: 'dry'
    pm.sym_filters = lambda sym: (SEI_TICK, 1.0, 0)
    orig_fav = pm.favorable_extreme
    orig_pub = pm.pub

    def run(st, pos, algos, fav, posts=None, deletes=None, sc_extra=None):
        posts = posts if posts is not None else []
        deletes = deletes if deletes is not None else []
        state_path.write_text(json.dumps(st))
        pm.favorable_extreme = lambda *a, **k: fav

        def sc(path, params=None, method='GET'):
            if sc_extra:
                hit = sc_extra(path, params, method)
                if hit is not None:
                    return hit
            if 'positionRisk' in path:
                return pos
            if 'openAlgoOrders' in path:
                return algos
            if path.endswith('/algoOrder') and method == 'POST':
                posts.append(dict(params or {}))
                return {'algoId': 999001, 'code': 200}
            if path.endswith('/algoOrder') and method == 'DELETE':
                deletes.append(dict(params or {}))
                return {'code': 200}
            return {}
        ev = pm.manage_positions(sc, dry=False, log=lambda *a: None)
        st2 = json.loads(state_path.read_text()) if state_path.exists() else {}
        return ev, st2, posts, deletes

    sls = [_algo('SEIUSDT', 'STOP_MARKET', SEI_SL, 4000002209438985)]
    tps = [
        _algo('SEIUSDT', 'TAKE_PROFIT_MARKET', 0.03816, 4000002209443277),
        _algo('SEIUSDT', 'TAKE_PROFIT_MARKET', 0.03976, 4000002209443379),
    ]
    base_st = {'SEIUSDT': {
        'symbol': 'SEIUSDT', 'book': 'HIGH', 'setup': 'TEST', 'side': 'LONG',
        'entry': SEI_ENTRY, 'sl': SEI_SL, 'tp1': 0.03816, 'tp2': 0.03976,
        'qty': 4829.0, 'opened_ms': 1795682797377,
    }}
    pos = [_pos('SEIUSDT', 4829, SEI_ENTRY, 0.03700)]

    ev, st2, posts, deletes = run(base_st, pos, sls + tps, fav=SEI_HIGH)
    kinds = [e.get('event') for e in ev]
    check('TEST position moves SL at 1R', 'BE_1R_moved' in kinds, kinds)
    check('posted reduceOnly STOP_MARKET at BE (not closePosition; Binance -4130)',
          posts and posts[0].get('reduceOnly') == 'true'
          and 'closePosition' not in posts[0]
          and posts[0].get('type') == 'STOP_MARKET'
          and posts[0].get('workingType') == 'MARK_PRICE'
          and float(posts[0]['triggerPrice']) == SEI_BE
          and float(posts[0]['quantity']) == 4829.0, posts)
    check('old SL cancelled after new one posted',
          deletes and deletes[0].get('algoId') == 4000002209438985, deletes)
    check('be_1r_done set once', st2['SEIUSDT'].get('be_1r_done') is True)
    check('sl_algo updated', st2['SEIUSDT'].get('sl_algo') == 999001)
    check('sl_be stored', st2['SEIUSDT'].get('sl_be') == SEI_BE, st2['SEIUSDT'].get('sl_be'))
    check('original sl kept for R calc', st2['SEIUSDT'].get('sl') == SEI_SL)
    check('TPs not cancelled', all(d.get('algoId') not in (4000002209443277, 4000002209443379) for d in deletes), deletes)

    # already flagged: no second move
    st_done = json.loads(json.dumps(base_st)); st_done['SEIUSDT']['be_1r_done'] = True
    ev, st2, posts, deletes = run(st_done, pos, sls + tps, fav=SEI_HIGH)
    check('no second 1R move when be_1r_done',
          'BE_1R_moved' not in [e.get('event') for e in ev] and posts == [], (ev, posts))

    # MANUAL skipped
    st_man = json.loads(json.dumps(base_st)); st_man['SEIUSDT']['setup'] = 'MANUAL'
    ev, st2, posts, deletes = run(st_man, pos, sls + tps, fav=SEI_HIGH)
    check('MANUAL is not managed',
          'BE_1R_moved' not in [e.get('event') for e in ev] and posts == [] and deletes == [], ev)
    check('MANUAL state has no be_1r_done', not st2['SEIUSDT'].get('be_1r_done'))

    # not yet 1R
    ev, st2, posts, deletes = run(base_st, pos, sls + tps, fav=0.03600)
    check('no move before 1R',
          'BE_1R_moved' not in [e.get('event') for e in ev] and posts == [], ev)

    # -2021 on BE, then fallback near mark succeeds
    mark_ok = 0.03700
    def sc_2021(path, params, method):
        if path.endswith('/algoOrder') and method == 'POST':
            px = float((params or {}).get('triggerPrice') or 0)
            if abs(px - SEI_BE) < 1e-12:
                return {'code': -2021, 'msg': 'Order would immediately trigger.'}
        return None
    ev, st2, posts, deletes = run(base_st, [_pos('SEIUSDT', 4829, SEI_ENTRY, mark_ok)],
                                  sls + tps, fav=SEI_HIGH, sc_extra=sc_2021)
    kinds = [e.get('event') for e in ev]
    check('retries near mark after -2021', 'BE_1R_moved' in kinds, ev)
    moved = next(e for e in ev if e['event'] == 'BE_1R_moved')
    check('event kind near_mark after -2021', moved['kind'] == 'near_mark', moved)
    check('one successful fallback POST and one cancel after -2021',
          len(posts) == 1 and len(deletes) == 1, (posts, deletes))
    check('fallback trigger below mark and above old SL',
          float(posts[0]['triggerPrice']) < mark_ok and float(posts[0]['triggerPrice']) > SEI_SL,
          posts[0])

    # TP1 path still moves to BE independently (and 1R is skipped once tp1_done)
    st_tp = json.loads(json.dumps(base_st)); st_tp['SEIUSDT']['tp1_done'] = True
    # remaining half, only TP2 still live (no TP1 algo)
    ev, st2, posts, deletes = run(
        st_tp,
        [_pos('SEIUSDT', 2414.5, SEI_ENTRY, 0.03820)],
        sls + [_algo('SEIUSDT', 'TAKE_PROFIT_MARKET', 0.03976, 4000002209443379)],
        fav=SEI_HIGH,
    )
    kinds = [e.get('event') for e in ev]
    check('TP1-then-BE still runs', 'BE_moved' in kinds, kinds)
    check('1R path does not also fire after tp1_done', 'BE_1R_moved' not in kinds, kinds)
    check('TP1 BE uses reduceOnly quantity, not closePosition',
          posts and posts[0].get('reduceOnly') == 'true' and 'closePosition' not in posts[0],
          posts)

    pm.favorable_extreme = orig_fav
    pm.pub = orig_pub


def test_lessons_keep():
    tmp = Path(tempfile.mkdtemp(prefix='be1r_les_'))
    journal = tmp / 'trade_journal.jsonl'
    lessons = tmp / 'lessons.json'
    locked = tmp / 'lessons_locked.json'
    locked.write_text(json.dumps({'adjust': {}}))
    # one BREAKOUT win so update_lessons has something to write
    row = dict(ts_close='2026-11-21T13:14+07:00', ts_open='2026-11-21T10:00+07:00',
               symbol='ARBUSDT', book='HIGH', setup='BREAKOUT', side='LONG',
               entry=1.0, sl=0.9, tp1=1.15, tp2=1.3, sl_pct=10.0, score=3.5,
               rr_plan=2.0, qty=1.0, exit=1.2, exit_reason='TP1+rest',
               pnl_realized=1.0, fees=0.0, pnl_net=1.0, r_multiple=2.0,
               held_min=30.0, mae_pct=-1.0, mfe_pct=2.0, mfe_r=1.0, tp1_done=True, note='')
    journal.write_text(json.dumps(row) + '\n')
    lessons.write_text(json.dumps({
        'adjust': {},
        'lessons': [{
            'rule': 'be_at_1r', 'keep': True, 'date': '2026-11-26',
            'source': 'SEIUSDT LONG TEST, hit +1.3R at 0.03785 then fell back near entry 0.03576',
            'adjust': 'BE at +1R',
        }],
        'daily_reviews': [{'date': '2026-11-26', 'lessons': ['be at 1R'], 'changes': 'be_1r'}],
    }))
    pm.D = str(tmp)
    pm.JOURNAL = str(journal)
    pm.LESSONS = str(lessons)
    out = pm.update_lessons()
    rules = [l.get('rule') for l in out.get('lessons') or []]
    check('update_lessons keeps be_at_1r', 'be_at_1r' in rules, rules)
    check('daily_reviews 2026-11-26 kept',
          any(r.get('date') == '2026-11-26' for r in out.get('daily_reviews') or []),
          out.get('daily_reviews'))


def main():
    test_helpers()
    test_plan()
    test_manage_positions()
    test_lessons_keep()
    print('ALL_PASSED')


if __name__ == '__main__':
    main()
