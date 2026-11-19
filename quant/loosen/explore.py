import sys, os, itertools; sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from bt import *
rows = []
for depth, tg, chase, sc in itertools.product([0.6, 0.75, 0.9, 1.0], [-2.5, -3.0, -4.0], ['strict', 'relaxed'], [0.0, -0.5, -1.0]):
    V = dict(BASE, depth=depth, atr_tgt=tg, core_min=3.5 - sc, high_min=2.5 - sc)
    if chase == 'relaxed': V.update(high_chase=2.0, core_chase=0.05)
    tr, nf = run(V); a = summ(tr, 730/30.44); d = summ([t for t in tr if t['ts'] >= END - 90*86400000], 90/30.44)
    y1 = summ([t for t in tr if t['ts'] < START + 365*86400000], 12); y2 = summ([t for t in tr if t['ts'] >= START + 365*86400000], 12)
    rows.append((f"d{depth}|tgt{-tg}R|{chase}|sc+{-sc}", a, d, y1, y2))
for nm, a, d, y1, y2 in sorted(rows, key=lambda r: r[1].get('maxdd', 0)):
    print(f"{nm:30s} n={a['n']:4d} /mo={a['per_month']:5.2f} win={a.get('win')} avgR={a.get('avgR')} PF={a.get('pf')} DD={a.get('maxdd')} | 90d n={d['n']} avgR={d.get('avgR')} | Y1 {y1.get('avgR')} Y2 {y2.get('avgR')}")
