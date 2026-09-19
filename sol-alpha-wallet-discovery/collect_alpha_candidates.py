from __future__ import annotations

import argparse
import json
import os
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

BASE = "https://madeonsol.com/api/v1/alpha/leaderboard"
DEMO_KEY = "msk_demo_try_the_solana_api_2026"

def get_page(sort: str, offset: int, limit: int, key: str):
    qs = urllib.parse.urlencode({
        "period": "all",
        "min_tokens": 5,
        "sort": sort,
        "exclude_bots": "true",
        "limit": limit,
        "offset": offset,
    })
    req = urllib.request.Request(
        BASE + "?" + qs,
        headers={"Authorization": f"Bearer {key}", "user-agent": "sol-alpha-wallet-discovery/0.3"},
    )
    with urllib.request.urlopen(req, timeout=45) as r:
        return json.loads(r.read().decode("utf-8"))

def parse_ts(s):
    if not s:
        return None
    return datetime.fromisoformat(str(s).replace("Z", "+00:00")).astimezone(timezone.utc)

def main():
    p=argparse.ArgumentParser()
    p.add_argument("--cutoff", default="2026-09-15T16:00:00Z")
    p.add_argument("--pages", type=int, default=3)
    p.add_argument("--limit", type=int, default=100)
    p.add_argument("--out", default="out/2026-09-15/alpha_candidates.json")
    args=p.parse_args()
    cutoff=parse_ts(args.cutoff)
    key=os.getenv("MADEONSOL_API_KEY", DEMO_KEY)

    raw={}
    calls=[]
    for sort in ("win_rate","pnl","roi"):
        for page in range(args.pages):
            offset=page*args.limit
            try:
                data=get_page(sort, offset, args.limit, key)
                calls.append({"sort":sort,"offset":offset,"ok":True})
            except Exception as e:
                calls.append({"sort":sort,"offset":offset,"ok":False,"error":f"{type(e).__name__}: {e}"})
                continue
            rows=data.get("leaderboard") or data.get("wallets") or data.get("data") or []
            for row in rows:
                w=row.get("wallet") or row.get("address")
                if not w:
                    continue
                old=raw.get(w)
                if old is None:
                    raw[w]=dict(row)
                    raw[w]["seen_sorts"]=[sort]
                else:
                    if sort not in old.setdefault("seen_sorts",[]):
                        old["seen_sorts"].append(sort)

    eligible=[]
    rejected={"post_cutoff_last_seen":0,"weak_metrics":0,"missing_last_seen":0}
    for wallet,row in raw.items():
        last=parse_ts(row.get("last_seen"))
        if last is None:
            rejected["missing_last_seen"]+=1
            continue
        if last >= cutoff:
            rejected["post_cutoff_last_seen"]+=1
            continue
        tokens=int(row.get("tokens_traded") or row.get("tokens") or 0)
        wins=int(row.get("wins") or 0)
        losses=int(row.get("losses") or 0)
        closed=max(wins+losses, tokens)
        wr=float(row.get("win_rate") or (wins/max(wins+losses,1)))
        pnl=float(row.get("net_pnl_sol") or row.get("pnl") or 0)
        roi=float(row.get("roi") or 0)
        if tokens < 8 or wr < 0.60 or pnl <= 0 or roi < 0.10:
            rejected["weak_metrics"]+=1
            continue
        score=(
            min(wr/0.85,1)*35
            + min(max(pnl,0)/50,1)*25
            + min(max(roi,0)/1.0,1)*20
            + min(tokens/30,1)*20
        )
        eligible.append({
            "wallet":wallet,
            "score":round(score,3),
            "tokens":tokens,
            "wins":wins,
            "losses":losses,
            "win_rate":wr,
            "net_pnl_sol":pnl,
            "roi":roi,
            "last_seen":row.get("last_seen"),
            "seen_sorts":sorted(row.get("seen_sorts",[])),
            "historical_safety":"last_seen strictly before freeze cutoff; no post-cutoff alpha activity",
        })

    eligible.sort(key=lambda x:(-x["score"],-x["net_pnl_sol"],x["wallet"]))
    payload={
        "freeze_cutoff_utc":args.cutoff,
        "anti_lookahead":True,
        "source":"MadeOnSol alpha leaderboard; current index filtered to wallets whose last_seen is strictly pre-cutoff",
        "raw_unique_wallets":len(raw),
        "eligible_count":len(eligible),
        "rejected":rejected,
        "api_calls":calls,
        "wallets":eligible,
    }
    out=Path(args.out)
    out.parent.mkdir(parents=True,exist_ok=True)
    out.write_text(json.dumps(payload,ensure_ascii=False,indent=2),encoding="utf-8")
    print(f"RAW={len(raw)} ELIGIBLE={len(eligible)}")
    for x in eligible[:30]:
        print(f"{x['wallet']} score={x['score']} WR={x['win_rate']:.1%} pnl={x['net_pnl_sol']:.2f} roi={x['roi']:.1%} tokens={x['tokens']} last={x['last_seen']}")
    print(f"WROTE={out}")

if __name__=="__main__":
    main()
