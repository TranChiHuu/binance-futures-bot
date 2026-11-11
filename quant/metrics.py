import numpy as np, pandas as pd
def stats(trades, key='R'):
    if not trades: return dict(n=0)
    r = np.array([t[key] for t in trades])
    pos, neg = r[r > 0].sum(), -r[r < 0].sum()
    eq = np.cumsum(r); dd = (np.maximum.accumulate(np.concatenate([[0], eq]))[1:] - eq).max()
    df = pd.DataFrame({'d': pd.to_datetime([t['t_exit'] for t in trades], unit='ms') + pd.Timedelta(hours=7), 'r': r})
    daily = df.set_index('d')['r'].resample('D').sum()
    sh = daily.mean() / daily.std() * np.sqrt(365) if daily.std() > 0 else 0
    out = dict(n=len(r), win=float((r > 0).mean()), avgR=float(r.mean()), sumR=float(r.sum()),
               pf=float(pos / neg) if neg > 0 else float('inf'), maxdd=float(dd), sharpe=float(sh),
               medR=float(np.median(r)))
    # 95% CI for expectancy (normal approx)
    out['ci95'] = (float(r.mean() - 1.96 * r.std(ddof=1) / np.sqrt(len(r))), float(r.mean() + 1.96 * r.std(ddof=1) / np.sqrt(len(r)))) if len(r) > 2 else None
    return out
def monthly(trades, key='R'):
    if not trades: return {}
    df = pd.DataFrame({'d': pd.to_datetime([t['t_exit'] for t in trades], unit='ms') + pd.Timedelta(hours=7), 'r': [t[key] for t in trades]})
    m = df.set_index('d')['r'].resample('ME').sum()
    return {k.strftime('%Y-%m'): round(float(v), 2) for k, v in m.items()}
def fmt(s):
    if not s or s.get('n', 0) == 0: return 'n=0'
    ci = s.get('ci95')
    return (f"n={s['n']} win={s['win']*100:.0f}% avgR={s['avgR']:+.3f} sumR={s['sumR']:+.1f} PF={s['pf']:.2f} "
            f"maxDD={s['maxdd']:.1f}R sharpe={s['sharpe']:.2f}" + (f" CI95=[{ci[0]:+.3f},{ci[1]:+.3f}]" if ci else ''))
