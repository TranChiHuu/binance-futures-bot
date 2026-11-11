import os,time,hmac,hashlib,urllib.request,urllib.parse,json,sys
K=os.environ.get('BINANCE_API_KEY');S=os.environ.get('BINANCE_API_SECRET')
BASE='https://fapi.binance.com'
def call(base,path,params=None,method='GET'):
    p=dict(params or {});p['timestamp']=int(time.time()*1000);p['recvWindow']=10000
    q=urllib.parse.urlencode(p);sig=hmac.new(S.encode(),q.encode(),hashlib.sha256).hexdigest()
    url=f"{base}{path}?{q}&signature={sig}"
    data=None
    headers={'X-MBX-APIKEY':K}
    if method in ('POST','DELETE','PUT'):
        data=b''
        headers['Content-Type']='application/x-www-form-urlencoded'
    req=urllib.request.Request(url,data=data,method=method,headers=headers)
    try: return json.load(urllib.request.urlopen(req,timeout=20))
    except urllib.error.HTTPError as e: return {'http':e.code,'err':e.read().decode()}
def pub(path,params=None):
    q=('?'+urllib.parse.urlencode(params)) if params else ''
    return json.load(urllib.request.urlopen(BASE+path+q,timeout=20))
if __name__=='__main__':
    args=sys.argv[1:]
    if not args:
        print('key set:',bool(K),'secret set:',bool(S))
        r=call('https://api.binance.com','/sapi/v1/account/apiRestrictions');print('restrictions:',{k:v for k,v in r.items() if k not in('createTime',)} if isinstance(r,dict) else r)
        a=call(BASE,'/fapi/v2/account')
        if 'assets' in a:
            print('wallet',a['totalWalletBalance'],'avail',a['availableBalance'],'uPnL',a['totalUnrealizedProfit'])
            print('positions',[(p['symbol'],p['positionAmt'],p['entryPrice']) for p in a['positions'] if float(p['positionAmt'])!=0])
        else: print('account:',a)
        o=call(BASE,'/fapi/v1/openOrders');print('open orders:',[(x['symbol'],x['side'],x['type'],x['price'],x['origQty']) for x in o] if isinstance(o,list) else o)
        sys.exit(0)
    method=args[0].upper(); path=args[1]; params={}
    for a in args[2:]:
        if '=' in a:
            k,v=a.split('=',1); params[k]=v
    print(json.dumps(call(BASE if path.startswith('/fapi') else 'https://api.binance.com', path, params, method), ensure_ascii=False))
