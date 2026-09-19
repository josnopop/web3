from __future__ import annotations

import argparse
import json
import os
import time
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Iterable

DEFAULT_RPC = os.getenv("SOLANA_RPC_URL", "https://api.mainnet-beta.solana.com")


@dataclass(frozen=True)
class SignatureRow:
    signature: str
    block_time: int
    slot: int


def rpc_call(method: str, params: list, url: str = DEFAULT_RPC, timeout: int = 30):
    payload = json.dumps({
        "jsonrpc": "2.0",
        "id": 1,
        "method": method,
        "params": params,
    }).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=payload,
        headers={"content-type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        body = json.loads(r.read().decode("utf-8"))
    if "error" in body:
        raise RuntimeError(f"RPC {method} error: {body['error']}")
    return body.get("result")


def iter_signatures(
    address: str,
    start_ts: int,
    end_ts: int,
    *,
    max_pages: int = 3,
    rpc_url: str = DEFAULT_RPC,
    sleep_s: float = 0.25,
) -> Iterable[SignatureRow]:
    """Exact-timestamp but bounded public-RPC fallback.

    This is intentionally coverage-limited. It is useful for verification and
    targeted historical recovery, not for claiming exhaustive market coverage.
    """
    before = None
    for _ in range(max_pages):
        opts = {"limit": 1000}
        if before:
            opts["before"] = before
        rows = rpc_call("getSignaturesForAddress", [address, opts], rpc_url) or []
        if not rows:
            return
        stop = False
        for row in rows:
            bt = row.get("blockTime")
            if bt is None:
                continue
            if bt < start_ts:
                stop = True
                break
            if start_ts <= bt < end_ts and row.get("err") is None:
                yield SignatureRow(row["signature"], int(bt), int(row["slot"]))
        before = rows[-1]["signature"]
        if stop or len(rows) < 1000:
            return
        time.sleep(sleep_s)


def signer_wallet(signature: str, rpc_url: str = DEFAULT_RPC) -> str | None:
    tx = rpc_call(
        "getTransaction",
        [signature, {"encoding": "jsonParsed", "maxSupportedTransactionVersion": 0}],
        rpc_url,
    )
    if not tx:
        return None
    keys = tx["transaction"]["message"]["accountKeys"]
    for key in keys:
        if isinstance(key, dict) and key.get("signer"):
            return str(key.get("pubkey"))
    return None


def probe(address: str, start: str, end: str, pages: int, rpc_url: str):
    s = int(datetime.fromisoformat(start).replace(tzinfo=timezone.utc).timestamp())
    e = int(datetime.fromisoformat(end).replace(tzinfo=timezone.utc).timestamp())
    rows = list(iter_signatures(address, s, e, max_pages=pages, rpc_url=rpc_url))
    print(json.dumps({
        "provider": "solana-public-rpc",
        "coverage": "PARTIAL_EXACT_HISTORY",
        "address": address,
        "window_utc": [start, end],
        "pages_cap": pages,
        "matching_signatures": len(rows),
        "sample": [r.__dict__ for r in rows[:10]],
    }, indent=2))


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--address", required=True)
    p.add_argument("--start", required=True, help="UTC ISO, e.g. 2026-09-16T00:00:00")
    p.add_argument("--end", required=True)
    p.add_argument("--pages", type=int, default=1)
    p.add_argument("--rpc", default=DEFAULT_RPC)
    args = p.parse_args()
    probe(args.address, args.start, args.end, args.pages, args.rpc)


if __name__ == "__main__":
    main()
