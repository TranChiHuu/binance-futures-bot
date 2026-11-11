import os, pickle, hashlib, json
from multiprocessing import Pool
from engine import Sym, SYMS
from strategy import P
D = os.path.dirname(os.path.abspath(__file__))
CACHE = f'{D}/data/sigcache'; os.makedirs(CACHE, exist_ok=True)
_S = {}
def load_sym(s):
    if s not in _S: _S[s] = Sym(s)
    return _S[s]
def _gen(args):
    s, p = args
    return s, load_sym(s).signals(p)
def scan_key(p):
    d = {k: v for k, v in p.d().items() if k in ('atr_buf','zone','lookback','close_tol','market_zone','max_limit_dist','high_chase_atr')}
    return hashlib.md5(json.dumps(d, sort_keys=True).encode()).hexdigest()[:10]
def signals(p, procs=4):
    fn = f'{CACHE}/{scan_key(p)}.pkl'
    if os.path.exists(fn): return pickle.load(open(fn, 'rb'))
    with Pool(procs) as pool:
        res = dict(pool.map(_gen, [(s, p) for s in SYMS]))
    pickle.dump(res, open(fn, 'wb'))
    return res
def all_syms():
    return {s: load_sym(s) for s in SYMS}
