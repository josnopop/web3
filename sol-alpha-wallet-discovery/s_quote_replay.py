from __future__ import annotations
import argparse,json,time,urllib.request,urllib.error
from datetime import date
from pathlib import Path
from config import QUOTE_MINTS,WSOL,USDC,USDT,utc_window_for_local_day
from public_rpc_scan import iter_signatures
from decode_wallet_trades import token_deltas,native_sol_delta

RPCS=["https://api.mainnet-beta.solana.com","https://api.mainnet.solana.com","https://solana-rpc.publicnode.com","https://solana.drpc.org/"]

def gettx(sig):
    payload=json.dumps({"jsonrpc":"2.0","id":1,"method":"getTransaction","params":[sig,{"encoding":"jsonParsed","maxSupportedTransactionVersion":0}]}).encode()
    last=None
    for rnd in range(5):
      for url in RPCS:
        try:
          req=urllib.request.Request(url,data=payload,headers={"content-type":"application/json","user-agent":"s15-full/1.0"},method="POST")
          with urllib.request.urlopen(req,timeout=25) as r: b=json.loads(r.read().decode())
          if not b.get("error") and b.get("result") is not None:return b["result"]
        except Exception as e:last=e
      time.sleep(.2*(rnd+1))
    raise RuntimeError(str(last))

def decode(tx,w,sig):
    if not tx or (tx.get("meta") or {}).get("err") is not None:return []
    meta=tx.get("meta") or {};d=token_deltas(meta,w)
    assets=[(m,float(v)) for m,v in d.items() if m not in QUOTE_MINTS and abs(float(v))>1e-12]
    sol=native_sol_delta(tx,w)+float(d.get(WSOL,0));usdc=float(d.get(USDC,0));usdt=float(d.get(USDT,0))
    out=[]
    for m,a in assets:
      side=qm=None;qa=0
      if a>0 and sol<-.0001:side,qm,qa="BUY","SOL",-sol
      elif a<0 and sol>.0001:side,qm,qa="SELL","SOL",sol
      elif a>0 and usdc<0:side,qm,qa="BUY","USDC",-usdc
      elif a<0 and usdc>0:side,qm,qa="SELL","USDC",usdc
      elif a>0 and usdt<0:side,qm,qa="BUY","USDT",-usdt
      elif a<0 and usdt>0:side,qm,qa="SELL","USDT",usdt
      if side:out.append({"wallet":w,"signature":sig,"block_time":int(tx.get("blockTime") or 0),"side":side,"token_mint":m,"token_amount":abs(a),"quote_mint":qm,"quote_amount":qa,"quote_per_token":qa/abs(a)})
    return out

def main():
 p=argparse.ArgumentParser();p.add_argument("--pool",required=True);p.add_argument("--date",required=True);p.add_argument("--shard",type=int,required=True);p.add_argument("--shards",type=int,default=12);p.add_argument("--out",required=True);a=p.parse_args()
 wallets=json.loads(Path(a.pool).read_text())["wallets"];src=wallets[a.shard]
 st,en=utc_window_for_local_day(date.fromisoformat(a.date)); sigs=list(iter_signatures(src["wallet"],int(st.timestamp()),int(en.timestamp()),max_pages=4,rpc_url="https://api.mainnet.solana.com",sleep_s=.05))
 rows=[];errs=[]
 for i,s in enumerate(sigs):
  try: rows.extend(decode(gettx(s.signature),src["wallet"],s.signature))
  except Exception as e:errs.append(s.signature)
 out={"date":a.date,"source":src,"successful_txs":len(sigs),"rows":rows,"errors":errs}
 Path(a.out).parent.mkdir(parents=True,exist_ok=True);Path(a.out).write_text(json.dumps(out,ensure_ascii=False,indent=2))
 print(json.dumps({"wallet":a.shard+1,"tx":len(sigs),"rows":len(rows),"buy":sum(x["side"]=="BUY" for x in rows),"sell":sum(x["side"]=="SELL" for x in rows),"err":len(errs)}))
if __name__=="__main__":main()
