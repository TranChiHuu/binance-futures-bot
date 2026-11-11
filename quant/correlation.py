#!/usr/bin/env python3
"""Rolling correlation of 1h log returns among the 19 coins (30-day window, no look-ahead)."""
import os, json, numpy as np, pandas as pd
import matplotlib; matplotlib.use('Agg'); import matplotlib.pyplot as plt
from engine import SYMS, D as DD
D = os.path.dirname(os.path.abspath(__file__)); R = f'{D}/results'; os.makedirs(R, exist_ok=True)
WIN = 720
class Corr:
    def __init__(self):
        px = pd.DataFrame({s: pd.read_parquet(f'{DD}/{s}_1h.parquet').set_index('ot')['c'] for s in SYMS}).sort_index()
        ret = np.log(px).diff()
        self.idx = {s: i for i, s in enumerate(SYMS)}
        days = (ret.index.values // 86400000)
        self.day0 = int(days.min()); self.mats = {}
        vals = ret.values; ot = ret.index.values
        for d in np.unique(days):
            end = np.searchsorted(ot, d * 86400000)        # bars strictly before day start
            if end < WIN: continue
            w = vals[end - WIN:end]
            df = pd.DataFrame(w)
            self.mats[int(d)] = df.corr(min_periods=200).values
        self.ret = ret
    def __call__(self, a, b, ts):
        m = self.mats.get(int(ts // 86400000))
        if m is None: return 0.0
        v = m[self.idx[a], self.idx[b]]
        return 0.0 if np.isnan(v) else float(v)

if __name__ == '__main__':
    C = Corr()
    full = C.ret.corr().round(2)
    # fraction of days each pair > 0.8
    keys = sorted(C.mats)
    frac = np.nanmean(np.stack([C.mats[k] > 0.8 for k in keys]), axis=0)
    avg_roll = np.nanmean(np.stack([C.mats[k] for k in keys]), axis=0)
    off = ~np.eye(len(SYMS), dtype=bool)
    mean_pair_series = pd.Series({pd.to_datetime(k * 86400000, unit='ms'): np.nanmean(C.mats[k][off]) for k in keys})
    pairs = []
    for i in range(len(SYMS)):
        for j in range(i + 1, len(SYMS)):
            pairs.append((SYMS[i], SYMS[j], round(float(avg_roll[i, j]), 3), round(float(frac[i, j]), 3)))
    pairs.sort(key=lambda x: -x[2])
    out = dict(mean_pairwise_rolling=round(float(np.nanmean(avg_roll[off])), 3),
               min_pairwise=pairs[-5:], top_pairs=pairs[:15],
               btc_corr={s: float(full.loc['BTCUSDT', s]) for s in SYMS},
               share_of_pairs_above_08=round(float(np.nanmean(frac[off])), 3),
               mean_pair_corr_last_day=round(float(mean_pair_series.iloc[-1]), 3))
    json.dump(out, open(f'{R}/correlation.json', 'w'), indent=1)
    plt.figure(figsize=(9, 8)); plt.imshow(avg_roll, vmin=0, vmax=1, cmap='viridis'); plt.colorbar()
    lab = [s.replace('USDT', '') for s in SYMS]
    plt.xticks(range(len(SYMS)), lab, rotation=90); plt.yticks(range(len(SYMS)), lab)
    plt.title('Tuong quan trung binh (cua so 30 ngay, loi nhuan 1h)'); plt.tight_layout(); plt.savefig(f'{R}/correlation_heatmap.png', dpi=100); plt.close()
    plt.figure(figsize=(11, 3.5)); mean_pair_series.plot(); plt.title('Tuong quan trung binh giua cac cap theo thoi gian (30 ngay)'); plt.grid(alpha=.3); plt.tight_layout(); plt.savefig(f'{R}/correlation_over_time.png', dpi=100); plt.close()
    print(json.dumps(out, indent=1)[:2500])
