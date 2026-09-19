from __future__ import annotations
import argparse,json,time,urllib.request,urllib.error
from datetime import datetime,timezone
from pathlib import Path
from collections import defaultdict
from config import QUOTE_MINTS,WSOL,USDC,USDT
from public_rpc_scan import iter_signatures
from decode_wallet_trades import token_deltas,native_sol_delta

W="2ksQ77e9e5SS6VA6poanRGWfkU3R4R5wZnptbJHb2nx9"
SIG_RPC="https://api.mainnet.solana.com"
RPCS=["https://api.mainnet.solana.com","https://solana-rpc.publicnode.com","https://solana.drpc.org/"]

def call_tx(sig):
    payload=json.dumps({"jsonrpc":"2.0","id":1,"method":"getTransaction","params":[sig,{"encoding":"jsonParsed","maxSupportedTransactionVersion":0}]}).encode()
    last=None
    for rnd in range(6):
        for url in RPCS:
            req=urllib.request.Request(url,data=payload,headers={"content-type":"application/json","user-agent":"s10-part-audit/1.0"},method="POST")
            try:
                with urllib.request.urlopen(req,timeout=25) as r: body=json.loads(r.read().decode())
                if body.get("error"): raise RuntimeError(str(body["error"]))
                if body.get("result") is not None: return body["result"]
            except Exception as e: last=e
        time.sleep(min(.25*(rnd+1),1.5))
    raise RuntimeError(str(last))

def classify(tx,sig):
    if not tx or (tx.get("meta") or {}).get("err") is not None:return []
    meta=tx.get("meta") or {}
    d=token_deltas(meta,W)
    assets=[(m,float(v)) for m,v in d.items() if m not in QUOTE_MINTS and abs(float(v))>1e-12]
    native=native_sol_delta(tx,W); wsol=float(d.get(WSOL,0.0))
    qsol=native+wsol; usdc=float(d.get(USDC,0.0)); usdt=float(d.get(USDT,0.0))
    out=[]
    for mint,ad in assets:
        side=None; qm=None; qa=0.0
        if ad>0 and qsol<-0.0001: side,qm,qa="BUY","SOL",-qsol
        elif ad<0 and qsol>0.0001: side,qm,qa="SELL","SOL",qsol
        elif ad>0 and usdc<0: side,qm,qa="BUY","USDC",-usdc
        elif ad<0 and usdc>0: side,qm,qa="SELL","USDC",usdc
        elif ad>0 and usdt<0: side,qm,qa="BUY","USDT",-usdt
        elif ad<0 and usdt>0: side,qm,qa="SELL","USDT",usdt
        if side:
            out.append({"wallet":W,"signature":sig,"block_time":int(tx.get("blockTime") or 0),"slot":int(tx.get("slot") or 0),
                "side":side,"token_mint":mint,"token_amount":abs(ad),"quote_mint":qm,"quote_amount":qa,
                "quote_per_token":qa/abs(ad),"fee_sol":float(meta.get("fee") or 0)/1e9})
    return out

def main():
    p=argparse.ArgumentParser(); p.add_argument("--part",type=int,required=True); p.add_argument("--parts",type=int,default=6); p.add_argument("--out",required=True); a=p.parse_args()
    S=int(datetime(2026,9,15,16,tzinfo=timezone.utc).timestamp()); E=int(datetime(2026,9,16,16,tzinfo=timezone.utc).timestamp())
    sigs=list(iter_signatures(W,S,E,max_pages=4,rpc_url=SIG_RPC,sleep_s=.05))
    chosen=sigs[a.part::a.parts]; rows=[]; errors=[]
    for i,s in enumerate(chosen,1):
        try: rows.extend(classify(call_tx(s.signature),s.signature))
        except Exception as e: errors.append({"signature":s.signature,"error":str(e)})
        if i%20==0: print("progress",a.part,i,len(chosen),"rows",len(rows),"err",len(errors))
    out={"part":a.part,"parts":a.parts,"signature_total":len(sigs),"signature_part":len(chosen),"rows":rows,"errors":errors}
    Path(a.out).parent.mkdir(parents=True,exist_ok=True); Path(a.out).write_text(json.dumps(out,ensure_ascii=False,indent=2),encoding="utf-8")
    print(json.dumps({"part":a.part,"sig":len(chosen),"rows":len(rows),"buys":sum(x["side"]=="BUY" for x in rows),"sells":sum(x["side"]=="SELL" for x in rows),"errors":len(errors)}))
if __name__=="__main__": main()
