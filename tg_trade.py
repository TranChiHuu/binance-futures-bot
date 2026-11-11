import os,sys,json,urllib.request
T=os.environ["TELEGRAM_BOT_TOKEN"]
CID=open("/workspace/business/.telegram_chat_id").read().strip()
text=sys.stdin.read() if len(sys.argv)<2 else " ".join(sys.argv[1:])
# force clean UTF-8; strip lone surrogates / nulls
text=text.encode("utf-8","replace").decode("utf-8").strip()
body=json.dumps({"chat_id":CID,"text":text},ensure_ascii=False).encode("utf-8")
req=urllib.request.Request(
    f"https://api.telegram.org/bot{T}/sendMessage",
    data=body,
    headers={"Content-Type":"application/json; charset=utf-8"},
)
print(json.load(urllib.request.urlopen(req,timeout=15)).get("ok"))
