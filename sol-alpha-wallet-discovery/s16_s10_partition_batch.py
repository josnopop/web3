from __future__ import annotations
import argparse,json,time,urllib.request
from datetime import datetime,timezone
from pathlib import Path
from config import QUOTE_MINTS,WSOL,USDC,USDT
from public_rpc_scan import iter_signatures
from decode_wallet_trades import token_deltas,native_sol_delta

W="2ksQ77e9e5SS6VA6poanRGWfkU3R4R5wZnptbJHb2nx9"
SIG_RPC="https://api.mainnet.solana.com"
RPCS=["https://api.mainnet.solana.com","https://solana-rpc.publicnode.com","https://solana.drpc.org/"]

def post_batch(url,sigs):
    payload=json.dumps([{"jsonrpc":"2.0","id":i,"method":"getTransaction","params":[s,{"encoding":"jsonParsed","maxSupportedTransactionVersion":0}]} for i,s in enumerate(sigs)]).encode()
    req=urllib.request.Request(url,data=payload,headers={"content-type":"application/json","user-agent":"s10-part-batch/1.0"},method="POST")
    with urllib.request.urlopen(req,timeout=40) as r: body=json.loads(r.read().decode())
    if not isinstance(body,list): raise RuntimeError("non-list")
    by={int(x.get("id")):x for x in body if isinstance(x,dict) and x.get("id") is not None}
    return [(s,(by.get(i) or {}).get("result"),(by.get(i) or {}).get("error")) for i,s in enumerate(sigs)]

def classify(tx,sig):
    if not tx or (tx.get("meta") or {}).get("err") is not None:return []
    meta=tx.get("meta") or {}; d=token_deltas(meta,W)
    assets=[(m,float(v)) for m,v in d.items() if m not in QUOTE_MINTS and abs(float(v))>1e-12]
    native=native_sol_delta(tx,W); qsol=native+float(d.get(WSOL,0.0)); usdc=float(d.get(USDC,0.0)); usdt=float(d.get(USDT,0.0))
    out=[]
    for mint,ad in assets:
        side=qm=None; qa=0.0
        if ad>0 and qsol<-0.0001: side,qm,qa="BUY","SOL",-qsol
        elif ad<0 and qsol>0.0001: side,qm,qa="SELL","SOL",qsol
        elif ad>0 and usdc<0: side,qm,qa="BUY","USDC",-usdc
        elif ad<0 and usdc>0: side,qm,qa="SELL","USDC",usdc
        elif ad>0 and usdt<0: side,qm,qa="BUY","USDT",-usdt
        elif ad<0 and usdt>0: side,qm,qa="SELL","USDT",usdt
        if side: out.append({"wallet":W,"signature":sig,"block_time":int(tx.get("blockTime") or 0),"slot":int(tx.get("slot") or 0),"side":side,"token_mint":mint,"token_amount":abs(ad),"quote_mint":qm,"quote_amount":qa,"quote_per_token":qa/abs(ad),"fee_sol":float(meta.get("fee") or 0)/1e9})
    return out

def main():
    p=argparse.ArgumentParser();p.add_argument("--part",type=int,required=True);p.add_argument("--parts",type=int,default=6);p.add_argument("--out",required=True);a=p.parse_args()
    S=int(datetime(2026,9,15,16,tzinfo=timezone.utc).timestamp());E=int(datetime(2026,9,16,16,tzinfo=timezone.utc).timestamp())
    sigs=[x.signature for x in iter_signatures(W,S,E,max_pages=4,rpc_url=SIG_RPC,sleep_s=.03)][a.part::a.parts]
    rows=[]; unresolved=[]; fetched=0
    for off in range(0,len(sigs),15):
        chunk=sigs[off:off+15]; pending=list(chunk); got={}
        for url in RPCS:
            if not pending: break
            try:
                resp=post_batch(url,pending)
            except Exception:
                continue
            nxt=[]
            for s,tx,err in resp:
                if tx is not None and not err: got[s]=tx
                else: nxt.append(s)
            pending=nxt
            time.sleep(.08)
        for s,tx in got.items(): rows.extend(classify(tx,s)); fetched+=1
        unresolved.extend(pending)
        print("part",a.part,"off",off,"fetched",fetched,"unresolved",len(unresolved),"rows",len(rows))
    out={"part":a.part,"signature_part":len(sigs),"fetched":fetched,"unresolved":unresolved,"rows":rows}
    Path(a.out).parent.mkdir(parents=True,exist_ok=True);Path(a.out).write_text(json.dumps(out,ensure_ascii=False,indent=2),encoding="utf-8")
    print(json.dumps({"part":a.part,"sig":len(sigs),"fetched":fetched,"unresolved":len(unresolved),"rows":len(rows),"buys":sum(x["side"]=="BUY" for x in rows),"sells":sum(x["side"]=="SELL" for x in rows)}))
if __name__=="__main__":main()
