from __future__ import annotations

import argparse
import json
from datetime import date
from pathlib import Path

from config import utc_window_for_local_day
from public_rpc_scan import DEFAULT_RPC, iter_signatures


def main():
    p=argparse.ArgumentParser()
    p.add_argument("--candidates", required=True)
    p.add_argument("--date", required=True)
    p.add_argument("--shard", type=int, required=True)
    p.add_argument("--shards", type=int, default=20)
    p.add_argument("--pages", type=int, default=2)
    p.add_argument("--rpc", default=DEFAULT_RPC)
    p.add_argument("--out", required=True)
    args=p.parse_args()

    d=json.loads(Path(args.candidates).read_text(encoding="utf-8"))
    wallets=d.get("wallets") or []
    selected=[w for i,w in enumerate(wallets) if i%args.shards==args.shard]
    start,end=utc_window_for_local_day(date.fromisoformat(args.date))
    start_ts,end_ts=int(start.timestamp()),int(end.timestamp())

    active=[]
    checked=0
    for w in selected:
        wallet=w.get("wallet") or w.get("address")
        if not wallet: continue
        checked+=1
        rows=list(iter_signatures(wallet,start_ts,end_ts,max_pages=args.pages,rpc_url=args.rpc,sleep_s=0.10))
        if not rows: continue
        active.append({
            "wallet":wallet,
            "source_candidate":w,
            "successful_transactions":len(rows),
            "possible_page_cap":len(rows)>=args.pages*1000,
            "signatures":[r.signature for r in rows],
        })
        print(f"ACTIVE {wallet} tx={len(rows)}")

    payload={
        "date":args.date,
        "shard":args.shard,
        "checked_wallets":checked,
        "active_wallets":len(active),
        "rows":active,
    }
    out=Path(args.out); out.parent.mkdir(parents=True,exist_ok=True)
    out.write_text(json.dumps(payload,ensure_ascii=False,indent=2),encoding="utf-8")
    print(f"CHECKED={checked} ACTIVE={len(active)} WROTE={out}")

if __name__=="__main__":
    main()
