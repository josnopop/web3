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
        "min_tokens": 3,
        "sort": sort,
        "exclude_bots": "false",
        "limit": limit,
        "offset": offset,
    })
    req = urllib.request.Request(
        BASE + "?" + qs,
        headers={"Authorization": f"Bearer {key}", "user-agent": "sol-alpha-wallet-discovery/0.4"},
    )
    with urllib.request.urlopen(req, timeout=45) as r:
        return json.loads(r.read().decode("utf-8"))

def parse_ts(s):
    if not s:
        return None
    return datetime.fromisoformat(str(s).replace("Z", "+00:00")).astimezone(timezone.utc)

def metric_score(tokens, wr, pnl, roi):
    # Discovery score only. Do not use it as final copy permission.
    return (
        min(max(wr,0)/0.80,1)*30
        + min(max(pnl,0)/25,1)*25
        + min(max(roi,0)/0.75,1)*20
        + min(max(tokens,0)/25,1)*15
        + min(max(tokens,0)/100,1)*10
    )

def main():
    p=argparse.ArgumentParser()
    p.add_argument("--cutoff", default="2026-09-15T16:00:00Z")
    p.add_argument("--pages", type=int, default=5)
    p.add_argument("--limit", type=int, default=100)
    p.add_argument("--historical-mode", choices=("strict_snapshot","broad_discovery"), default="broad_discovery")
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
                elif sort not in old.setdefault("seen_sorts",[]):
                    old["seen_sorts"].append(sort)

    candidates=[]
    rejected={"hard_garbage":0,"strict_post_cutoff":0}
    for wallet,row in raw.items():
        last=parse_ts(row.get("last_seen"))
        tokens=int(row.get("tokens_traded") or row.get("tokens") or 0)
        wins=int(row.get("wins") or 0)
        losses=int(row.get("losses") or 0)
        wr=float(row.get("win_rate") or (wins/max(wins+losses,1)))
        pnl=float(row.get("net_pnl_sol") or row.get("pnl") or 0)
        roi=float(row.get("roi") or 0)

        # Critical fix: current last_seen is NOT historical evidence.
        # In broad_discovery mode it may never delete a wallet from a past-date replay.
        if args.historical_mode == "strict_snapshot" and last is not None and last >= cutoff:
            rejected["strict_post_cutoff"]+=1
            continue

        # Discovery gate is intentionally broad. Final Wallet Score V0.3 decides copyability.
        # Keep profitable/skillful/active wallets even when one headline metric is weak.
        useful = (
            tokens >= 3
            and (
                (pnl > 0 and wr >= 0.40)
                or (roi >= 0.05 and wr >= 0.45)
                or (tokens >= 20 and (pnl > 0 or roi > 0))
                or (wr >= 0.65 and tokens >= 5)
            )
        )
        if not useful:
            rejected["hard_garbage"]+=1
            continue

        score=metric_score(tokens,wr,pnl,roi)
        candidates.append({
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
            "discovery_only":True,
            "copy_permission":"UNVERIFIED",
            "historical_safety":(
                "current leaderboard used only for broad candidate discovery; "
                "last_seen is never treated as a historical snapshot; "
                "pre-cutoff chain history must decide final eligibility"
            ),
        })

    candidates.sort(key=lambda x:(-x["score"],-x["net_pnl_sol"],x["wallet"]))
    payload={
        "freeze_cutoff_utc":args.cutoff,
        "anti_lookahead":True,
        "historical_mode":args.historical_mode,
        "source":"MadeOnSol leaderboard broad discovery + pre-cutoff chain validation",
        "warning":"Current leaderboard metrics are discovery hints only, never historical proof.",
        "raw_unique_wallets":len(raw),
        "eligible_count":len(candidates),
        "rejected":rejected,
        "api_calls":calls,
        "wallets":candidates,
    }
    out=Path(args.out)
    out.parent.mkdir(parents=True,exist_ok=True)
    out.write_text(json.dumps(payload,ensure_ascii=False,indent=2),encoding="utf-8")
    print(f"RAW={len(raw)} CANDIDATES={len(candidates)} MODE={args.historical_mode}")
    for x in candidates[:50]:
        print(f"{x['wallet']} score={x['score']} WR={x['win_rate']:.1%} pnl={x['net_pnl_sol']:.2f} roi={x['roi']:.1%} tokens={x['tokens']}")
    print(f"WROTE={out}")

if __name__=="__main__":
    main()
