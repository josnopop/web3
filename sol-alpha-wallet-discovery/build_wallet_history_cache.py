from __future__ import annotations

import argparse
import json
import time
from datetime import date
from pathlib import Path

from config import utc_window_for_local_day
from history_rescore import decode_one
from public_rpc_scan import DEFAULT_RPC, iter_signatures, rpc_call


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--candidates", required=True)
    p.add_argument("--start-date", required=True)
    p.add_argument("--end-date", required=True, help="exclusive local date, e.g. 2026-09-17")
    p.add_argument("--shard", type=int, required=True)
    p.add_argument("--shards", type=int, default=16)
    p.add_argument("--pages", type=int, default=12)
    p.add_argument("--rpc", default=DEFAULT_RPC)
    p.add_argument("--out", required=True)
    args = p.parse_args()

    start, _ = utc_window_for_local_day(date.fromisoformat(args.start_date))
    end, _ = utc_window_for_local_day(date.fromisoformat(args.end_date))
    start_ts = int(start.timestamp())
    end_ts = int(end.timestamp())

    data = json.loads(Path(args.candidates).read_text(encoding="utf-8"))
    wallets = data.get("wallets") or data.get("candidates") or []
    selected = [w for i, w in enumerate(wallets) if i % args.shards == args.shard]

    rows = []
    for src in selected:
        wallet = src.get("wallet") or src.get("address")
        if not wallet:
            continue
        sigs = list(iter_signatures(wallet, start_ts, end_ts, max_pages=args.pages, rpc_url=args.rpc, sleep_s=0.25))
        truncated = len(sigs) >= args.pages * 1000
        trades = []
        errors = 0
        for j, s in enumerate(sigs, 1):
            try:
                tx = rpc_call(
                    "getTransaction",
                    [s.signature, {"encoding": "jsonParsed", "maxSupportedTransactionVersion": 0}],
                    args.rpc,
                    retries=5,
                )
                t = decode_one(tx, wallet, s.signature)
                if t:
                    trades.append(t)
            except Exception:
                errors += 1
            if j % 100 == 0:
                time.sleep(0.15)

        rows.append({
            "wallet": wallet,
            "source": src,
            "signature_count": len(sigs),
            "rpc_errors": errors,
            "truncated": truncated,
            "complete_enough": (not truncated and errors <= max(5, len(sigs) * 0.02)),
            "trades": trades,
        })
        print(
            f"{wallet[:8]} sigs={len(sigs)} trades={len(trades)} "
            f"errors={errors} truncated={truncated}"
        )

    payload = {
        "start_utc": start.isoformat(),
        "end_utc": end.isoformat(),
        "rows": rows,
    }
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"WROTE={out} wallets={len(rows)}")


if __name__ == "__main__":
    main()
