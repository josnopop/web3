from __future__ import annotations

import argparse
import json
import time
import urllib.error
import urllib.request
from datetime import date
from pathlib import Path

from config import utc_window_for_local_day
from public_rpc_scan import DEFAULT_RPC, PUBLIC_RPC_POOL, iter_signatures, rpc_call
from s_day_replay import decode_one


def rpc_targets(url: str) -> list[str]:
    if url in PUBLIC_RPC_POOL:
        return list(dict.fromkeys((url, *PUBLIC_RPC_POOL)))
    return [url]


def batch_get_transactions(signatures: list[str], rpc_url: str, batch_size: int = 20):
    targets = rpc_targets(rpc_url)
    for offset in range(0, len(signatures), batch_size):
        chunk = signatures[offset:offset + batch_size]
        payload = json.dumps([
            {
                "jsonrpc": "2.0",
                "id": i,
                "method": "getTransaction",
                "params": [sig, {"encoding": "jsonParsed", "maxSupportedTransactionVersion": 0}],
            }
            for i, sig in enumerate(chunk)
        ]).encode("utf-8")

        body = None
        last = None
        for attempt in range(max(6, len(targets) * 2)):
            target = targets[attempt % len(targets)]
            req = urllib.request.Request(
                target,
                data=payload,
                headers={
                    "content-type": "application/json",
                    "user-agent": "sol-alpha-wallet-discovery/0.5-fast",
                },
                method="POST",
            )
            try:
                with urllib.request.urlopen(req, timeout=45) as r:
                    body = json.loads(r.read().decode("utf-8"))
                if isinstance(body, list):
                    break
                raise RuntimeError("batch RPC returned non-list")
            except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, RuntimeError) as e:
                last = e
                body = None
                time.sleep(min(0.25 * (attempt + 1), 1.5))
        if body is None:
            print(f"BATCH_FAIL offset={offset} n={len(chunk)} error={type(last).__name__}")
            yield from [(sig, None, "batch_failed") for sig in chunk]
            continue

        by_id = {int(x.get("id")): x for x in body if isinstance(x, dict) and x.get("id") is not None}
        for i, sig in enumerate(chunk):
            item = by_id.get(i) or {}
            if item.get("error"):
                yield sig, None, str(item.get("error"))
            else:
                yield sig, item.get("result"), None

        if offset and offset % 400 == 0:
            print(f"BATCH_PROGRESS {offset}/{len(signatures)}")


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--pool", required=True)
    p.add_argument("--date", required=True)
    p.add_argument("--shard", type=int, required=True)
    p.add_argument("--shards", type=int, default=12)
    p.add_argument("--pages", type=int, default=4)
    p.add_argument("--rpc", default=DEFAULT_RPC)
    p.add_argument("--out", required=True)
    p.add_argument("--batch-size", type=int, default=20)
    args = p.parse_args()

    data = json.loads(Path(args.pool).read_text(encoding="utf-8"))
    wallets = data.get("wallets") or []
    selected = [w for i, w in enumerate(wallets) if i % args.shards == args.shard]

    start, end = utc_window_for_local_day(date.fromisoformat(args.date))
    start_ts, end_ts = int(start.timestamp()), int(end.timestamp())

    all_trades = []
    summaries = []
    for src in selected:
        wallet = src["wallet"]
        sig_rows = list(iter_signatures(
            wallet, start_ts, end_ts,
            max_pages=args.pages, rpc_url=args.rpc, sleep_s=0.08
        ))
        sigs = [x.signature for x in sig_rows]
        meta_by_sig = {x.signature: x for x in sig_rows}
        decoded = []
        errors = 0

        for sig, tx, err in batch_get_transactions(sigs, args.rpc, args.batch_size):
            if err:
                # Public RPCs frequently reject JSON-RPC batches with 429/403.
                # Fall back to the battle-tested single-call rotator so failed
                # batches never become silently missing wallet history.
                try:
                    tx = rpc_call(
                        "getTransaction",
                        [sig, {"encoding": "jsonParsed", "maxSupportedTransactionVersion": 0}],
                        args.rpc,
                        timeout=35,
                        retries=6,
                    )
                    err = None
                except Exception:
                    errors += 1
                    continue

            t = decode_one(tx, wallet, sig)
            if t:
                t["source_win_rate"] = src.get("win_rate")
                t["source_net_pnl_sol"] = src.get("net_pnl_sol")
                t["source_tokens"] = src.get("tokens")
                decoded.append(t)

        all_trades.extend(decoded)
        summaries.append({
            "wallet": wallet,
            "successful_txs": len(sigs),
            "decoded_sol_swap_rows": len(decoded),
            "buys": sum(1 for x in decoded if x["side"] == "BUY"),
            "sells": sum(1 for x in decoded if x["side"] == "SELL"),
            "rpc_errors": errors,
            "possible_page_cap": len(sigs) >= args.pages * 1000,
            "source": src,
        })
        print(
            f"DONE {wallet} tx={len(sigs)} decoded={len(decoded)} "
            f"buy={summaries[-1]['buys']} sell={summaries[-1]['sells']} errors={errors}"
        )

    payload = {
        "date_local": args.date,
        "window_utc": [start.isoformat(), end.isoformat()],
        "shard": args.shard,
        "wallets": len(selected),
        "summaries": summaries,
        "trades": all_trades,
    }
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"WROTE={out}")


if __name__ == "__main__":
    main()
