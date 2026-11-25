#!/usr/bin/env python3
"""Offline unit checks for 25% risk sizing, daily stop, and drawdown caps. No API calls."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
import risk_tier as R


def check(name, cond, detail=''):
    if not cond:
        raise AssertionError(f'FAIL {name}: {detail}')
    print('OK', name, detail)


def main():
    wallet = 42.9
    js = dict(n=0, expectancy=0.0, pf=0.0)

    rs = R.compute(wallet, st={'peak': wallet, 'dd_mult': 1.0}, js=js, day='2026-11-20')
    check('tier name', rs['tier'] == 'RISK_25pct', rs['tier'])
    check('core 25%', abs(rs['risk_pct_core'] - 0.25) < 1e-9 and abs(rs['base_pct_core'] - 0.25) < 1e-9)
    check('high 25%', abs(rs['risk_pct_high'] - 0.25) < 1e-9 and abs(rs['base_pct_high'] - 0.25) < 1e-9)
    check('risk usdt 10.725', abs(rs['risk_usdt_core'] - 10.725) < 1e-9 and abs(rs['risk_usdt_high'] - 10.725) < 1e-9)
    check('book_cap not clipped', abs(R.book_cap('CORE', wallet, rs=rs) - 10.725) < 1e-9)
    check('book_cap HIGH same', abs(R.book_cap('HIGH', wallet, rs=rs) - 10.725) < 1e-9)
    check('never above 25%', rs['risk_pct'] <= R.CEIL + 1e-12)
    check('daily stop -50%', abs(rs['daily_stop'] - (-0.5 * wallet)) < 1e-9, rs['daily_stop'])
    check('daily_stop_usdt helper', abs(R.daily_stop_usdt(wallet) - (-21.45) ) < 1e-9)

    # large wallet also 25%, no 1% fallback
    big = R.compute(200.0, st={'peak': 200.0, 'dd_mult': 1.0}, js=js)
    check('>=100 wallet still 25%', abs(big['risk_pct_core'] - 0.25) < 1e-9 and abs(big['risk_pct_high'] - 0.25) < 1e-9)

    # DD 10% -> 18.75%, cannot go above 25%
    st10 = {'peak': 100.0, 'dd_mult': 1.0}
    r10 = R.compute(90.0, st=st10, js=js)
    check('DD10 18.75%', abs(r10['risk_pct_core'] - 0.1875) < 1e-9 and r10['tier'] == 'RISK_25pct_DD10', r10['tier'])
    check('DD10 still <=25%', r10['risk_pct'] <= 0.25)

    # DD 20% -> 12.5%
    r20 = R.compute(80.0, st={'peak': 100.0, 'dd_mult': 1.0}, js=js)
    check('DD20 12.5%', abs(r20['risk_pct'] - 0.125) < 1e-9 and r20['tier'] == 'RISK_25pct_DD20')

    # sticky: prev 0.5, dd now 8% (>=5%) stays 0.5
    sticky = R.compute(92.0, st={'peak': 100.0, 'dd_mult': 0.5}, js=js)
    check('sticky DD20 until <5%', abs(sticky['dd_mult'] - 0.5) < 1e-12 and abs(sticky['risk_pct'] - 0.125) < 1e-9)

    # recovered: dd 4% -> back to 25%
    rec = R.compute(96.0, st={'peak': 100.0, 'dd_mult': 0.5}, js=js)
    check('recovered to 25%', rec['dd_mult'] == 1.0 and abs(rec['risk_pct'] - 0.25) < 1e-9, rec['tier'])

    # start-of-day snapshot reused; first snapshot backs out realized+commission
    first = R.compute(42.9, st={'peak': 42.9, 'dd_mult': 1.0}, js=js,
                      day='2026-11-20', realized=1.2, commission=-0.1)
    # wallet - realized - commission = 42.9 - 1.2 - (-0.1) = 41.8
    check('day start backed out', abs(first['day_start_wallet'] - 41.8) < 1e-9, first['day_start_wallet'])
    check('daily stop from start wallet', abs(first['daily_stop'] - (-20.9)) < 1e-9, first['daily_stop'])
    again = R.compute(40.0, st=first, js=js, day='2026-11-20', realized=-1.0, commission=-0.2)
    check('same day reuses snapshot', abs(again['day_start_wallet'] - 41.8) < 1e-9)
    next_day = R.compute(40.0, st=again, js=js, day='2026-11-21', realized=0.0, commission=0.0)
    check('new day new snapshot', abs(next_day['day_start_wallet'] - 40.0) < 1e-9)

    # --- deposits/withdrawals excluded from drawdown and daily stop (2026-11-25) ---
    dep_ms = 1793635679351   # 2026-11-02 23:07+07 deposit
    wdr_ms = 1795593390281   # 2026-11-25 14:56+07 withdrawal 30 USDT
    peak_ts = 1795584960000  # 2026-11-21 13:16+07, raw wallet peak before the withdrawal
    # confirm peak_ts
    import datetime as _dt
    _sg = _dt.timezone(_dt.timedelta(hours=7))
    peak_ts = int(_dt.datetime(2026, 11, 21, 13, 16, tzinfo=_sg).timestamp() * 1000)
    xfers = [
        {'time': dep_ms, 'income': 49.60027647, 'asset': 'USDT'},
        {'time': wdr_ms, 'income': -30.0, 'asset': 'USDT'},
    ]
    live_wallet = 46.18219952
    live_peak = 69.35590022
    live = R.compute(live_wallet, st={'peak': live_peak, 'dd_mult': 0.5, 'peak_ts_ms': peak_ts},
                     js=js, day='2026-11-25', realized=0.0, commission=0.0, transfers=xfers)
    check('withdrawal is not drawdown', live['dd'] < 0.05, live['dd'])
    check('tier back to RISK_25pct', live['tier'] == 'RISK_25pct', live['tier'])
    check('risk back to 25%', abs(live['risk_pct'] - 0.25) < 1e-9, live['risk_pct'])
    check('dd_mult recovered to 1', live['dd_mult'] == 1.0, live['dd_mult'])
    check('never above 25% after recovery', live['risk_pct'] <= 0.25 + 1e-12)
    check('legacy day_start ignores withdrawal', abs(live['day_start_wallet'] - (live_wallet - (-30.0))) < 1e-8,
          live['day_start_wallet'])
    check('daily stop from pre-transfer start', abs(live['daily_stop'] - round(R.daily_stop_usdt(live['day_start_wallet']), 6)) < 1e-12, live['daily_stop'])

    # same numbers with no peak_ts: deposits-in-peak / withdrawals-after inference
    inferred = R.compute(live_wallet, st={'peak': live_peak, 'dd_mult': 0.5}, js=js, transfers=xfers)
    check('inference also 25%', inferred['tier'] == 'RISK_25pct' and abs(inferred['risk_pct'] - 0.25) < 1e-9,
          inferred['tier'])

    # already-adj peak must not jump because of a withdrawal
    adj_now = live['adj_wallet']
    stayed = R.compute(live_wallet, st=live, js=js, transfers=xfers)
    check('adj peak persists', stayed['peak_basis'] == 'adj' and abs(stayed['peak'] - adj_now) < 1e-8)

    # real trading loss then a withdrawal: DD stays the trading 20%, not 50%
    # raw peak 100, lose 20 (wallet 80), withdraw 30 (wallet 50). adj=80, DD=20%.
    loss_then_wdr = [
        {'time': 1000, 'income': -30.0},
    ]
    r_loss = R.compute(50.0, st={'peak': 100.0, 'dd_mult': 1.0, 'peak_ts_ms': 500},
                       js=js, transfers=loss_then_wdr)
    check('trading DD 20% after withdrawal', abs(r_loss['dd'] - 0.20) < 1e-9 and r_loss['tier'] == 'RISK_25pct_DD20',
          (r_loss['dd'], r_loss['tier'], r_loss['adj_wallet']))
    check('DD20 still 12.5%', abs(r_loss['risk_pct'] - 0.125) < 1e-9)

    # deposit does not create a fake recovery: peak 100 adj, wallet 80, deposit +20 -> wallet 100, adj still 80
    r_dep = R.compute(100.0, st={'peak': 100.0, 'dd_mult': 0.5, 'peak_basis': 'adj'},
                      js=js, transfers=[{'time': 2000, 'income': 20.0}])
    check('deposit is not recovery', abs(r_dep['dd'] - 0.20) < 1e-9 and abs(r_dep['dd_mult'] - 0.5) < 1e-12,
          (r_dep['dd'], r_dep['adj_wallet']))

    # stale same-day snapshot (no excludes_transfers flag) is recomputed
    stale = {'peak': 76.18, 'dd_mult': 1.0, 'peak_basis': 'adj',
             'day_start_date': '2026-11-25', 'day_start_wallet': 46.18}
    fixed = R.compute(46.18, st=stale, js=js, day='2026-11-25', realized=0.0, commission=0.0,
                      transfers=[{'time': wdr_ms, 'income': -30.0}])
    check('stale day_start recomputed', abs(fixed['day_start_wallet'] - 76.18) < 1e-9, fixed['day_start_wallet'])
    # later same day reuses the corrected snapshot even if wallet moves from trading
    reused = R.compute(40.18, st=fixed, js=js, day='2026-11-25', realized=-6.0, commission=0.0,
                       transfers=[{'time': wdr_ms, 'income': -30.0}])
    check('corrected day_start reused', abs(reused['day_start_wallet'] - 76.18) < 1e-9)

    # --- dry sizing: wallet 42.9, SL 3% ---
    risk = R.book_cap('HIGH', wallet, rs=rs)
    dry = R.size_position(
        entry=100.0, sl=97.0, side='LONG', risk_budget=risk, avail=wallet,
        step=0.001, min_qty=0.001, min_notional=5, fee_rate=0.0004, lev_max=R.LEV_HARD_MAX,
    )
    check('dry ok', dry['ok'], dry)
    check('dry lev 18', dry['lev'] == 18, dry['lev'])
    check('dry liq beyond 1.5x SL', dry['liq_vs_sl'] >= 1.5 - 1e-9, dry['liq_vs_sl'])
    check('dry liq < SL', dry['liq_est'] < 97.0, dry['liq_est'])
    check('dry risk <= budget', dry['risk_usdt'] <= risk + 1e-6, dry['risk_usdt'])
    check('dry notional / step', abs(dry['qty'] * 100.0 - dry['notional']) < 1e-6)
    check('dry margin = notional/lev', abs(dry['margin'] - dry['notional'] / dry['lev']) < 1e-6)
    # qty = floor(10.725 / 3.08 / 0.001)*0.001 = floor(3.482142... * 1000)/1000
    check('dry qty 3.482', abs(dry['qty'] - 3.482) < 1e-9, dry['qty'])
    print('DRY_EXAMPLE',
          f"wallet={wallet} sl=3% risk_budget={risk:.4f} qty={dry['qty']} "
          f"notional={dry['notional']:.4f} lev={dry['lev']}x margin={dry['margin']:.4f} "
          f"liq={dry['liq_est']:.4f} sl=97.0 liq_vs_sl={dry['liq_vs_sl']:.4f}")

    # short side also 1.5x
    sh = R.size_position(entry=100.0, sl=103.0, side='SHORT', risk_budget=risk, avail=wallet,
                         step=0.001, min_qty=0.001, min_notional=5, fee_rate=0.0004)
    check('short ok', sh['ok'] and sh['liq_est'] > 103.0 and sh['liq_vs_sl'] >= 1.5 - 1e-9, (sh['lev'], sh['liq_est'], sh['liq_vs_sl']))

    # tight available margin -> size down, not fail
    tight = R.size_position(entry=100.0, sl=97.0, side='LONG', risk_budget=risk, avail=8.0,
                            step=0.001, min_qty=0.001, min_notional=5, fee_rate=0.0004)
    check('tight avail sizes down', tight['ok'] and tight['sized_down'] and tight['margin'] <= 8.0 * 0.9 + 1e-6, tight)
    check('tight risk < budget', tight['risk_usdt'] < risk - 0.5, tight['risk_usdt'])

    # avail too small for min notional -> fail
    tiny = R.size_position(entry=100.0, sl=97.0, side='LONG', risk_budget=risk, avail=0.2,
                           step=0.001, min_qty=0.001, min_notional=5, fee_rate=0.0004)
    check('tiny avail min_notional skip', not tiny['ok'] and tiny['code'] == 'min_notional', tiny)

    # step size respected
    check('qty on step', abs(dry['qty'] / 0.001 - round(dry['qty'] / 0.001)) < 1e-9)

    # exchange lev cap 10 still keeps liq beyond 1.5x at 3% SL (liq dist ~9%)
    cap10 = R.size_position(entry=100.0, sl=97.0, side='LONG', risk_budget=risk, avail=wallet,
                            step=0.001, min_qty=0.001, min_notional=5, fee_rate=0.0004, lev_max=10)
    check('lev_max 10 still safe', cap10['ok'] and cap10['lev'] == 10 and cap10['liq_vs_sl'] >= 1.5, cap10['liq_vs_sl'])

    # refresh_transfers incremental merge; API failure keeps the cache
    import tempfile, os
    tdir = tempfile.mkdtemp(prefix='xfer_')
    cpath = os.path.join(tdir, 'transfers_cache.json')
    R.save_transfers([{'time': 100, 'income': 10.0, 'asset': 'USDT', 'tranId': 'a'}], path=cpath)
    def sc(path, params, method='GET'):
        st = int(params['startTime'])
        if st <= 200:
            return [{'time': 200, 'income': -3.0, 'asset': 'USDT', 'tranId': 'b'}]
        return []
    rows, ok = R.refresh_transfers(sc, start_ms=101, now_ms=300, cache_path=cpath)
    check('refresh ok', ok and len(rows) == 2, (ok, rows))
    check('refresh net', abs(R.net_transfers(rows) - 7.0) < 1e-9)
    def sc_fail(path, params, method='GET'):
        return {'http': 418, 'err': 'banned'}
    rows2, ok2 = R.refresh_transfers(sc_fail, start_ms=201, now_ms=400, cache_path=cpath)
    check('refresh failure keeps cache', (not ok2) and len(rows2) == 2 and abs(R.net_transfers(rows2) - 7.0) < 1e-9, (ok2, rows2))

    print('ALL_PASSED')


if __name__ == '__main__':
    main()
