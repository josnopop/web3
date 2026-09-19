from __future__ import annotations
import json,sys,time,urllib.request
from collections import defaultdict
sys.path.insert(0,".")
from config import QUOTE_MINTS,WSOL,USDC,USDT
from decode_wallet_trades import token_deltas,native_sol_delta
W="DNfuF1L62WWyW3pNakVkyGGFzVVhj4Yr52jSmdTyeBHm"
RPC="https://api.mainnet-beta.solana.com"
def rpc(m,p):
 d=json.dumps({"jsonrpc":"2.0","id":1,"method":m,"params":p}).encode()
 for k in range(8):
  try:
   r=urllib.request.Request(RPC,data=d,headers={"content-type":"application/json"},method="POST")
   with urllib.request.urlopen(r,timeout=40) as x:
    z=json.loads(x.read())
   if z.get("result") is not None:return z["result"]
  except: time.sleep(.4*(k+1))
 return None
start=1789660800; end=1789747200
before=None;sigs=[]
for _ in range(20):
 p=[W,{"limit":1000}]
 if before:p[1]["before"]=before
 b=rpc("getSignaturesForAddress",p) or []
 if not b:break
 for x in b:
  bt=x.get("blockTime") or 0
  if start<=bt<end and x.get("err") is None:sigs.append(x["signature"])
 if (b[-1].get("blockTime") or 0)<start:break
 before=b[-1]["signature"]
rows=[];diag=defaultdict(int);examples={}
for i,s in enumerate(sigs):
 tx=rpc("getTransaction",[s,{"encoding":"jsonParsed","maxSupportedTransactionVersion":0}])
 if not tx:diag["rpc_missing"]+=1;continue
 meta=tx.get("meta") or {}; d=token_deltas(meta,W); sol=native_sol_delta(tx,W)+float(d.get(WSOL,0)); usdc=float(d.get(USDC,0)); usdt=float(d.get(USDT,0))
 assets=[(m,float(v)) for m,v in d.items() if m not in QUOTE_MINTS and abs(float(v))>1e-12]
 if not assets:
  diag["no_asset_delta"]+=1; examples.setdefault("no_asset_delta",s);continue
 emitted=0
 for m,a in assets:
  side=qm=None;qa=0
  if a>0 and usdc<0:side,qm,qa="BUY","USDC",-usdc
  elif a<0 and usdc>0:side,qm,qa="SELL","USDC",usdc
  elif a>0 and usdt<0:side,qm,qa="BUY","USDT",-usdt
  elif a<0 and usdt>0:side,qm,qa="SELL","USDT",usdt
  elif a>0 and sol<-.001:side,qm,qa="BUY","SOL",-sol
  elif a<0 and sol>.001:side,qm,qa="SELL","SOL",sol
  if side:
   rows.append({"signature":s,"block_time":tx.get("blockTime"),"side":side,"token_mint":m,"token_amount":abs(a),"quote_mint":qm,"quote_amount":qa,"sol_delta":sol,"usdc_delta":usdc,"usdt_delta":usdt});emitted+=1
 if not emitted:
  diag["asset_but_unclassified"]+=1;examples.setdefault("asset_but_unclassified",{"sig":s,"deltas":d,"sol":sol})
json.dump({"wallet":W,"signatures":len(sigs),"rows":rows,"diag":dict(diag),"examples":examples},open("out/top1_audit.json","w"),indent=2)
print(json.dumps({"signatures":len(sigs),"rows":len(rows),"buy":sum(x["side"]=="BUY" for x in rows),"sell":sum(x["side"]=="SELL" for x in rows),"diag":dict(diag)}))
