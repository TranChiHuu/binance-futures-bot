import sys, os, json; sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bt
from bt import *
CH = dict(BASE, depth=1.0, atr_tgt=-4.0)
out = {}
for nm, V in [('baseline', BASE), ('chosen d1.0|tgt4R|strict|sc0', CH), ('d0.75|tgt4R only', dict(BASE, atr_tgt=-4.0))]:
    tr, nf = run(V)
    S = lambda L, m: summ(L, m)
    r = dict(all=S(tr, 730/30.44), d90=S([t for t in tr if t['ts'] >= END-90*86400000], 90/30.44), d30=S([t for t in tr if t['ts'] >= END-30*86400000], 1),
             CORE=S([t for t in tr if t['book']=='CORE'], 730/30.44), HIGH=S([t for t in tr if t['book']=='HIGH'], 730/30.44),
             Y1=S([t for t in tr if t['ts'] < START+365*86400000], 12), Y2=S([t for t in tr if t['ts'] >= START+365*86400000], 12),
             nofill=nf, outcomes=pd.Series([t['outcome'] for t in tr]).value_counts().to_dict())
    # max consecutive losses
    mx = cur = 0
    for t in tr:
        cur = cur + 1 if t['R'] < 0 else 0; mx = max(mx, cur)
    r['max_loss_streak'] = mx
    out[nm] = r
    print(nm, json.dumps(r, default=str))
# cost stress for chosen: SL slippage 0.05%, TP 0.01% through
bt._cache.clear()
