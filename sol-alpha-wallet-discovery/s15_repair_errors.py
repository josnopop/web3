from __future__ import annotations
import argparse,json,time,urllib.request
from pathlib import Path
from config import QUOTE_MINTS,WSOL,USDC,USDT
from decode_wallet_trades import token_deltas,native_sol_delta
RPCS=["https://api.mainnet-beta.solana.com","https://api.mainnet.solana.com","https://solana-rpc.publicnode.com","https://solana.drpc.org/"]
def gettx(sig):
 p=json.dumps({"jsonrpc":"2.0","id":1,"method":"getTransaction","params":[sig,{"encoding":"jsonParsed","maxSupportedTransactionVersion":0}]}).encode();last=None
 for rnd in range(12):
  for url in RPCS:
   try:
    req=urllib.request.Request(url,data=p,headers={"content-type":"application/json","user-agent":"s15-repair/1.0"},method="POST")
    with urllib.request.urlopen(req,timeout=30) as r:b=json.loads(r.read().decode())
    if not b.get("error") and b.get("result") is not None:return b["result"]
   except Exception as e:last=e
  time.sleep(min(.35*(rnd+1),2))
 raise RuntimeError(str(last))
def decode(tx,w,sig):
 meta=tx.get("meta") or {};d=token_deltas(meta,w);assets=[(m,float(v)) for m,v in d.items() if m not in QUOTE_MINTS and abs(float(v))>1e-12]
 sol=native_sol_delta(tx,w)+float(d.get(WSOL,0));usdc=float(d.get(USDC,0));usdt=float(d.get(USDT,0));out=[]
 for m,a in assets:
  side=qm=None;qa=0
  # stablecoin first: native SOL may only be rent/fee noise
  if a>0 and usdc<0:side,qm,qa="BUY","USDC",-usdc
  elif a<0 and usdc>0:side,qm,qa="SELL","USDC",usdc
  elif a>0 and usdt<0:side,qm,qa="BUY","USDT",-usdt
  elif a<0 and usdt>0:side,qm,qa="SELL","USDT",usdt
  elif a>0 and sol<-.001:side,qm,qa="BUY","SOL",-sol
  elif a<0 and sol>.001:side,qm,qa="SELL","SOL",sol
  if side:out.append({"wallet":w,"signature":sig,"block_time":int(tx.get("blockTime") or 0),"side":side,"token_mint":m,"token_amount":abs(a),"quote_mint":qm,"quote_amount":qa,"quote_per_token":qa/abs(a)})
 return out
def main():
 p=argparse.ArgumentParser();p.add_argument("--input",required=True);p.add_argument("--out",required=True);a=p.parse_args();d=json.loads(Path(a.input).read_text());w=d["source"]["wallet"];rows=[];left=[]
 for sig in d.get("errors",[]):
  try:rows+=decode(gettx(sig),w,sig)
  except Exception:left.append(sig)
 Path(a.out).write_text(json.dumps({"wallet":w,"repaired_rows":rows,"unresolved":left},indent=2))
 print(json.dumps({"requested":len(d.get("errors",[])),"rows":len(rows),"unresolved":len(left)}))
if __name__=="__main__":main()
