#!/usr/bin/env python3
"""Download Binance USDT-M public klines (1h, 15m) + funding history for the bot's 19 symbols.
Public endpoints only. Respects weight header; backs off on 418/429. Caches to data/*.parquet, resumable."""
import os, sys, time, json, urllib.request, urllib.parse, urllib.error
import pandas as pd
BASE = 'https://fapi.binance.com'
CORE = ['BTCUSDT','ETHUSDT','SOLUSDT','BNBUSDT','XRPUSDT','LINKUSDT']
HIGH = ['DOGEUSDT','SUIUSDT','AVAXUSDT','1000PEPEUSDT','WIFUSDT','NEARUSDT','APTUSDT',
        'INJUSDT','TIAUSDT','SEIUSDT','WLDUSDT','ARBUSDT','OPUSDT']
SYMS = CORE + HIGH
if os.environ.get('SYMS'): SYMS = os.environ['SYMS'].split(',')
D = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'data')
os.makedirs(D, exist_ok=True)
DAYS = int(os.environ.get('DAYS', '730'))
END = int(time.time() // 3600 * 3600 * 1000)
START = END - DAYS * 86400 * 1000

def get(path, params, weight_cap=1200):
    url = BASE + path + '?' + urllib.parse.urlencode(params)
    for attempt in range(8):
        try:
            r = urllib.request.urlopen(url, timeout=30)
            w = int(r.headers.get('X-MBX-USED-WEIGHT-1M', '0') or 0)
            data = json.load(r)
            if w > weight_cap:
                print(f'  weight {w} > {weight_cap}, sleeping 30s', flush=True); time.sleep(30)
            return data
        except urllib.error.HTTPError as e:
            if e.code in (418, 429):
                ra = int(e.headers.get('Retry-After', '60') or 60)
                print(f'  HTTP {e.code}, backing off {max(ra,60)}s', flush=True)
                time.sleep(max(ra, 60) * (1 if e.code == 429 else 3)); continue
            if e.code >= 500:
                time.sleep(5); continue
            raise
        except Exception as e:
            print('  err', e, flush=True); time.sleep(5)
    raise RuntimeError('too many retries')

COLS = ['ot','o','h','l','c','v','ct','qv','n','tbv','tbqv','ig']
def klines(sym, interval):
    fn = f'{D}/{sym}_{interval}.parquet'
    ms = {'1h': 3600000, '15m': 900000}[interval]
    old = pd.read_parquet(fn) if os.path.exists(fn) else None
    start = START if old is None else int(old['ot'].max()) + ms
    rows = []
    while start < END:
        k = get('/fapi/v1/klines', {'symbol': sym, 'interval': interval, 'startTime': start, 'limit': 1500})
        if not k: 
            # before listing: jump ahead
            start += 1500 * ms; continue
        rows += k
        start = int(k[-1][0]) + ms
        time.sleep(0.35)
    if rows:
        df = pd.DataFrame(rows, columns=COLS)[['ot','o','h','l','c','v']]
        df['ot'] = df['ot'].astype('int64')
        for c in 'ohlcv': df[c] = df[c].astype(float)
        df = df[df['ot'] < END]  # closed bars only
        if old is not None: df = pd.concat([old, df])
        df = df.drop_duplicates('ot').sort_values('ot').reset_index(drop=True)
        df.to_parquet(fn)
    n = len(pd.read_parquet(fn)) if os.path.exists(fn) else 0
    print(f'{sym} {interval}: {n} bars', flush=True)

def funding(sym):
    fn = f'{D}/{sym}_funding.parquet'
    start, rows = START, []
    while start < END:
        f = get('/fapi/v1/fundingRate', {'symbol': sym, 'startTime': start, 'limit': 1000})
        if not f: break
        rows += f
        start = int(f[-1]['fundingTime']) + 1
        if len(f) < 1000: break
        time.sleep(1.0)
    df = pd.DataFrame(rows)
    if len(df):
        df = df[['fundingTime','fundingRate']].astype({'fundingTime':'int64','fundingRate':float})
        df.to_parquet(fn)
    print(f'{sym} funding: {len(df)}', flush=True)

if __name__ == '__main__':
    which = sys.argv[1:] or ['1h', 'funding', '15m']
    for w in which:
        for s in SYMS:
            if w == 'funding': funding(s)
            else: klines(s, w)
