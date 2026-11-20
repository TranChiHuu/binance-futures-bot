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

    print('ALL_PASSED')


if __name__ == '__main__':
    main()
