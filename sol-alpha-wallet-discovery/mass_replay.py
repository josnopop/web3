from __future__ import annotations

import argparse
import json
import time
from collections import defaultdict
from datetime import date
from pathlib import Path

from config import DEX_PROGRAM_IDS, QUOTE_MINTS, USDC, USDT, WSOL, utc_window_for_local_day
from public_rpc_scan import DEFAULT_RPC, iter_signatures, rpc_call


def _pubkey(x):
    return x.get("pubkey") if isinstance(x, dict) else x

def _ui_amount(b):
    ui=b.get("uiTokenAmount") or {}
    return int(ui.get("amount") or 0)/(10 ** int(ui.get("decimals") or 0))

def token_deltas(meta,wallet):
    pre=defaultdict(float); post=defaultdict(float)
    for b in meta.get("preTokenBalances") or []:
        if b.get("owner")==wallet: pre[b["mint"]]+=_ui_amount(b)
    for b in meta.get("postTokenBalances") or []:
        if b.get("owner")==wallet: post[b["mint"]]+=_ui_amount(b)
    return {m:post[m]-pre[m] for m in set(pre)|set(post) if abs(post[m]-pre[m])>1e-15}

def native_sol_delta(tx,wallet):
    keys=[_pubkey(x) for x in tx["transaction"]["message"].get("accountKeys") or []]
    if wallet not in keys: return 0.0
    i=keys.index(wallet); meta=tx.get("meta") or {}
    pre=meta.get("preBalances") or []; post=meta.get("postBalances") or []
    if i>=len(pre) or i>=len(post): return 0.0
    return (post[i]-pre[i])/1e9

def touches_dex(tx):
    keys={_pubkey(x) for x in tx["transaction"]["message"].get("accountKeys") or []}
    return bool(keys & DEX_PROGRAM_IDS)

def classify(delta,quotes,sol_delta):
    for mint in (WSOL,USDC,USDT):
        q=quotes.get(mint,0.0)
        if delta>0 and q<0: return "BUY",mint,abs(q)
        if delta<0 and q>0: return "SELL",mint,abs(q)
    if delta>0 and sol_delta < -0.0001: return "BUY","SOL",abs(sol_delta)
    if delta<0 and sol_delta > 0.0001: return "SELL","SOL",abs(sol_delta)
    return None,None,0.0

def main():
    p=argparse.ArgumentParser()
    p.add_argument("--candidates", required=True)
    p.add_argument("--date", default="2026-09-16")
    p.add_argument("--shard", type=int, required=True)
    p.add_argument("--shards", type=int, default=10)
    p.add_argument("--pages", type=int, default=3)
    p.add_argument("--rpc", default=DEFAULT_RPC)
    p.add_argument("--out", required=True)
    args=p.parse_args()

    data=json.loads(Path(args.candidates).read_text(encoding="utf-8"))
    wallets=data["wallets"]
    selected=[w for i,w in enumerate(wallets) if i % args.shards == args.shard]
    start,end=utc_window_for_local_day(date.fromisoformat(args.date))
    start_ts,end_ts=int(start.timestamp()),int(end.timestamp())
    summaries=[]; trades=[]

    for wi,w in enumerate(selected,1):
        wallet=w["wallet"]
        sigs=list(iter_signatures(wallet,start_ts,end_ts,max_pages=args.pages,rpc_url=args.rpc,sleep_s=0.4))
        buys=sells=dex_txs=errors=0
        for j,s in enumerate(sigs,1):
            try:
                tx=rpc_call("getTransaction",[s.signature,{"encoding":"jsonParsed","maxSupportedTransactionVersion":0}],args.rpc,retries=6)
                if not tx or (tx.get("meta") or {}).get("err") is not None or not touches_dex(tx):
                    continue
                dex_txs+=1
                meta=tx.get("meta") or {}
                deltas=token_deltas(meta,wallet)
                quotes={m:d for m,d in deltas.items() if m in QUOTE_MINTS}
                assets={m:d for m,d in deltas.items() if m not in QUOTE_MINTS}
                sol_delta=native_sol_delta(tx,wallet)
                for mint,delta in assets.items():
                    side,qmint,qamt=classify(delta,quotes,sol_delta)
                    if not side: continue
                    row={
                        "wallet":wallet,
                        "wallet_score":w.get("score"),
                        "wallet_win_rate":w.get("win_rate"),
                        "wallet_pnl_sol":w.get("net_pnl_sol"),
                        "signature":s.signature,
                        "block_time":tx.get("blockTime"),
                        "slot":tx.get("slot"),
                        "side":side,
                        "token_mint":mint,
                        "token_delta":delta,
                        "quote_mint":qmint,
                        "quote_amount":qamt,
                        "native_sol_delta":sol_delta,
                        "fee_sol":(meta.get("fee") or 0)/1e9,
                    }
                    trades.append(row)
                    if side=="BUY": buys+=1
                    else: sells+=1
            except Exception as e:
                errors+=1
            if j % 50 == 0:
                time.sleep(0.15)
        summaries.append({
            "wallet":wallet,"score":w.get("score"),"win_rate":w.get("win_rate"),
            "pnl_sol":w.get("net_pnl_sol"),"successful_txs":len(sigs),"dex_txs":dex_txs,
            "buys":buys,"sells":sells,"rpc_errors":errors,
            "possible_page_cap":len(sigs)>=args.pages*1000,
        })
        print(f"WALLET {wallet[:6]}... tx={len(sigs)} dex={dex_txs} buys={buys} sells={sells} errors={errors}")

    out={
        "date":args.date,"shard":args.shard,"shards":args.shards,
        "candidate_wallets":len(selected),
        "summaries":summaries,"trades":trades,
        "totals":{
            "successful_txs":sum(x["successful_txs"] for x in summaries),
            "dex_txs":sum(x["dex_txs"] for x in summaries),
            "buys":sum(x["buys"] for x in summaries),
            "sells":sum(x["sells"] for x in summaries),
            "rpc_errors":sum(x["rpc_errors"] for x in summaries),
        }
    }
    path=Path(args.out); path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps(out,ensure_ascii=False,indent=2),encoding="utf-8")
    print("TOTAL",json.dumps(out["totals"]))
    print(f"WROTE={path}")

if __name__=="__main__":
    main()
