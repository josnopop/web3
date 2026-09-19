from __future__ import annotations
import json,time
from datetime import datetime,timezone
from pathlib import Path
from config import QUOTE_MINTS,WSOL
from public_rpc_scan import rpc_call,iter_signatures
from decode_wallet_trades import token_deltas,native_sol_delta

WALLET="2ksQ77e9e5SS6VA6poanRGWfkU3R4R5wZnptbJHb2nx9"
URL="https://api.mainnet.solana.com"
start=int(datetime(2026,9,15,16,0,tzinfo=timezone.utc).timestamp())
end=int(datetime(2026,9,16,16,0,tzinfo=timezone.utc).timestamp())
rows=[]; counts={}
sigs=list(iter_signatures(WALLET,start,end,max_pages=4,rpc_url=URL,sleep_s=.15))
for i,s in enumerate(sigs,1):
    try:
        tx=rpc_call("getTransaction",[s.signature,{"encoding":"jsonParsed","maxSupportedTransactionVersion":0}],URL,retries=8,timeout=35)
        if not tx or (tx.get("meta") or {}).get("err") is not None: continue
        meta=tx.get("meta") or {}
        d=token_deltas(meta,WALLET)
        assets=[(m,float(v)) for m,v in d.items() if m not in QUOTE_MINTS and abs(float(v))>1e-12]
        sol=native_sol_delta(tx,WALLET)+float(d.get(WSOL,0.0))
        if len(assets)==1:
            mint,ad=assets[0]
            if ad>0 and sol < -0.0001: kind="BUY_SOL"
            elif ad<0 and sol > 0.0001: kind="SELL_SOL"
            elif ad>0: kind="TOKEN_IN_OTHER"
            else: kind="TOKEN_OUT_OTHER"
        elif len(assets)>1: kind="MULTI_ASSET"
        else: kind="NO_ASSET_DELTA"
        counts[kind]=counts.get(kind,0)+1
        if kind!="NO_ASSET_DELTA":
            rows.append({"signature":s.signature,"block_time":s.block_time,"slot":s.slot,"kind":kind,
                         "native_plus_wsol":sol,"assets":assets,"all_token_deltas":d,
                         "fee_sol":float(meta.get("fee") or 0)/1e9})
    except Exception as e:
        counts["ERROR"]=counts.get("ERROR",0)+1
    if i%50==0: print("progress",i,len(sigs),counts)
out={"wallet":WALLET,"signature_count":len(sigs),"counts":counts,"rows":rows}
Path("out").mkdir(exist_ok=True)
Path("out/s16_s10_balance_audit.json").write_text(json.dumps(out,ensure_ascii=False,indent=2),encoding="utf-8")
print(json.dumps({"signature_count":len(sigs),"counts":counts,"interesting_rows":len(rows)},ensure_ascii=False))
