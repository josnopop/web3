from __future__ import annotations
import json,time
from datetime import datetime,timezone
from pathlib import Path
from config import QUOTE_MINTS,WSOL,USDC,USDT
from public_rpc_scan import iter_signatures
from s_day_replay_fast import batch_get_transactions
from decode_wallet_trades import token_deltas,native_sol_delta

W="2ksQ77e9e5SS6VA6poanRGWfkU3R4R5wZnptbJHb2nx9"
URL="https://api.mainnet.solana.com"
S=int(datetime(2026,9,15,16,0,tzinfo=timezone.utc).timestamp())
E=int(datetime(2026,9,16,16,0,tzinfo=timezone.utc).timestamp())

sigs=list(iter_signatures(W,S,E,max_pages=4,rpc_url=URL,sleep_s=.05))
rows=[]; counts={}; errors=0
for sig,tx,err in batch_get_transactions([x.signature for x in sigs],URL,batch_size=20):
    if err or not tx or (tx.get("meta") or {}).get("err") is not None:
        errors+=1; continue
    meta=tx.get("meta") or {}
    d=token_deltas(meta,W)
    assets=[(m,float(v)) for m,v in d.items() if m not in QUOTE_MINTS and abs(float(v))>1e-12]
    native=native_sol_delta(tx,W)
    q={m:float(d.get(m,0.0)) for m in (WSOL,USDC,USDT)}
    sol=native+q[WSOL]
    kind="NO_ASSET_DELTA"; detail=[]
    for mint,ad in assets:
        if ad>0 and sol<-0.0001: k="BUY_SOL"; qa=-sol
        elif ad<0 and sol>0.0001: k="SELL_SOL"; qa=sol
        elif ad>0 and q[USDC]<0: k="BUY_USDC"; qa=-q[USDC]
        elif ad<0 and q[USDC]>0: k="SELL_USDC"; qa=q[USDC]
        elif ad>0 and q[USDT]<0: k="BUY_USDT"; qa=-q[USDT]
        elif ad<0 and q[USDT]>0: k="SELL_USDT"; qa=q[USDT]
        elif ad>0: k="TOKEN_IN_OTHER"; qa=0
        else: k="TOKEN_OUT_OTHER"; qa=0
        detail.append((mint,ad,k,qa))
        counts[k]=counts.get(k,0)+1
    if not assets: counts[kind]=counts.get(kind,0)+1
    if detail:
        rows.append({"signature":sig,"block_time":int(tx.get("blockTime") or 0),"slot":int(tx.get("slot") or 0),
                     "native_sol_delta":native,"sol_plus_wsol":sol,"quotes":q,"assets":detail,
                     "fee_sol":float(meta.get("fee") or 0)/1e9})
out={"wallet":W,"signature_count":len(sigs),"errors":errors,"counts":counts,"rows":rows}
Path("out").mkdir(exist_ok=True)
Path("out/s16_s10_balance_audit_fast.json").write_text(json.dumps(out,ensure_ascii=False,indent=2),encoding="utf-8")
print(json.dumps({"signature_count":len(sigs),"errors":errors,"counts":counts,"interesting_rows":len(rows)},ensure_ascii=False))
