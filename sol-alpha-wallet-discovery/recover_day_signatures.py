from __future__ import annotations

import argparse
import json
from datetime import date
from pathlib import Path

from config import utc_window_for_local_day
from public_rpc_scan import iter_signatures


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--date", required=True)
    p.add_argument("--wallets", default="frozen_wallets_2026_09_15.json")
    p.add_argument("--pages", type=int, default=10)
    p.add_argument("--out", default=None)
    args = p.parse_args()

    day = date.fromisoformat(args.date)
    start, end = utc_window_for_local_day(day)
    start_ts, end_ts = int(start.timestamp()), int(end.timestamp())
    data = json.loads(Path(args.wallets).read_text(encoding="utf-8"))

    result = {
        "replay_day_local": args.date,
        "window_utc": [start.isoformat(), end.isoformat()],
        "coverage": "EXACT_PER_WALLET_SIGNATURES",
        "page_cap_per_wallet": args.pages,
        "wallets": [],
    }

    for i, w in enumerate(data["wallets"], 1):
        rows = list(iter_signatures(
            w["address"], start_ts, end_ts, max_pages=args.pages
        ))
        item = {
            "name": w["name"],
            "address": w["address"],
            "successful_transactions": len(rows),
            "first_in_window": rows[-1].block_time if rows else None,
            "last_in_window": rows[0].block_time if rows else None,
            "signatures": [r.signature for r in rows],
        }
        result["wallets"].append(item)
        print(f"[{i:02d}/{len(data['wallets'])}] {w['name']}: {len(rows)} tx")

    out = Path(args.out or f"out/{args.date}/wallet_signatures.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"TOTAL={sum(x['successful_transactions'] for x in result['wallets'])}")
    print(f"WROTE={out}")


if __name__ == "__main__":
    main()
