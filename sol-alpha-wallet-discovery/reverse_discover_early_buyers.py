from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

from solders.pubkey import Pubkey

from config import PUMPFUN_PROGRAM_ID
from public_rpc_scan import DEFAULT_RPC, rpc_call

PUMPFUN = Pubkey.from_string(PUMPFUN_PROGRAM_ID)


def pubkey(x):
    return x.get("pubkey") if isinstance(x, dict) else x


def ui_amount(b):
    ui=b.get("uiTokenAmount") or {}
    return int(ui.get("amount") or 0)/(10**int(ui.get("decimals") or 0))


def token_delta(meta,wallet,mint):
    pre=post=0.0
    for b in meta.get("preTokenBalances") or []:
        if b.get("owner")==wallet and b.get("mint")==mint:
            pre += ui_amount(b)
    for b in meta.get("postTokenBalances") or []:
        if b.get("owner")==wallet and b.get("mint")==mint:
            post += ui_amount(b)
    return post-pre


def sol_delta(tx,wallet):
    keys=[pubkey(x) for x in tx["transaction"]["message"].get("accountKeys") or []]
    if wallet not in keys:
        return 0.0
    i=keys.index(wallet)
    meta=tx.get("meta") or {}
    pre=meta.get("preBalances") or []
    post=meta.get("postBalances") or []
    if i>=len(pre) or i>=len(post):
        return 0.0
    return (post[i]-pre[i])/1e9


def first_signer(tx):
    for x in tx["transaction"]["message"].get("accountKeys") or []:
        if isinstance(x,dict) and x.get("signer"):
            return str(x["pubkey"])
    return None


def all_curve_signatures(curve, max_pages=4):
    out=[]
    before=None
    for _ in range(max_pages):
        opts={"limit":1000}
        if before:
            opts["before"]=before
        rows=rpc_call("getSignaturesForAddress",[curve,opts]) or []
        if not rows:
            break
        out.extend(r for r in rows if r.get("err") is None and r.get("blockTime"))
        before=rows[-1]["signature"]
        if len(rows)<1000:
            break
        time.sleep(0.35)
    # RPC returns newest first; earliest launch activity first.
    out.sort(key=lambda x:(x["blockTime"],x["slot"]))
    return out


def main():
    p=argparse.ArgumentParser()
    p.add_argument("--mints",default="historical_priority_mints.json")
    p.add_argument("--index",type=int,required=True)
    p.add_argument("--max-pages",type=int,default=4)
    p.add_argument("--tx-limit",type=int,default=180)
    p.add_argument("--buyers",type=int,default=20)
    p.add_argument("--out")
    args=p.parse_args()

    mints=json.loads(Path(args.mints).read_text(encoding="utf-8"))["mints"]
    mint=mints[args.index]
    mint_pk=Pubkey.from_string(mint)
    curve,_=Pubkey.find_program_address([b"bonding-curve",bytes(mint_pk)],PUMPFUN)
    sigs=all_curve_signatures(str(curve),args.max_pages)

    earliest=sigs[0]["blockTime"] if sigs else None
    buyers=[]
    seen=set()
    errors=0

    for row in sigs[:args.tx_limit]:
        if len(buyers)>=args.buyers:
            break
        try:
            tx=rpc_call(
                "getTransaction",
                [row["signature"],{"encoding":"jsonParsed","maxSupportedTransactionVersion":0}],
            )
            if not tx or (tx.get("meta") or {}).get("err") is not None:
                continue
            wallet=first_signer(tx)
            if not wallet or wallet in seen:
                continue
            td=token_delta(tx.get("meta") or {},wallet,mint)
            sd=sol_delta(tx,wallet)
            if td>0 and sd < -0.0001:
                seen.add(wallet)
                buyers.append({
                    "rank":len(buyers)+1,
                    "wallet":wallet,
                    "signature":row["signature"],
                    "block_time":row["blockTime"],
                    "seconds_from_curve_start":row["blockTime"]-earliest if earliest else None,
                    "approx_sol_out":round(abs(sd),9),
                })
        except Exception:
            errors += 1
        time.sleep(0.04)

    result={
        "mint":mint,
        "bonding_curve":str(curve),
        "signature_rows":len(sigs),
        "earliest_curve_block_time":earliest,
        "buyers":buyers,
        "rpc_decode_errors":errors,
        "coverage":"pumpfun_bonding_curve_earliest_buyers",
        "anti_lookahead_source": True,
    }
    out=Path(args.out or f"out/2026-09-15/early_buyers_{args.index:02d}.json")
    out.parent.mkdir(parents=True,exist_ok=True)
    out.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding="utf-8")
    print(f"RESULT index={args.index} mint={mint} sigs={len(sigs)} buyers={len(buyers)} errors={errors}")
    print(f"WROTE={out}")


if __name__=="__main__":
    main()
