from __future__ import annotations

import argparse
import json
import os
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Iterable

def _default_rpc() -> str:
    if os.getenv("SOLANA_RPC_URL"):
        return os.environ["SOLANA_RPC_URL"]
    if os.getenv("HELIUS_API_KEY"):
        return "https://mainnet.helius-rpc.com/?api-key=" + os.environ["HELIUS_API_KEY"]
    if os.getenv("SOLANATRACKER_API_KEY"):
        return "https://rpc-mainnet.solanatracker.io/?api_key=" + os.environ["SOLANATRACKER_API_KEY"]
    return "https://api.mainnet-beta.solana.com"


DEFAULT_RPC = _default_rpc()


@dataclass(frozen=True)
class SignatureRow:
    signature: str
    block_time: int
    slot: int


def rpc_call(method: str, params: list, url: str = DEFAULT_RPC, timeout: int = 30, retries: int = 6):
    payload = json.dumps({
        "jsonrpc": "2.0",
        "id": 1,
        "method": method,
        "params": params,
    }).encode("utf-8")
    last = None
    for attempt in range(retries):
        req = urllib.request.Request(
            url,
            data=payload,
            headers={"content-type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                body = json.loads(r.read().decode("utf-8"))
            if "error" in body:
                raise RuntimeError(f"RPC {method} error: {body['error']}")
            return body.get("result")
        except urllib.error.HTTPError as e:
            last = e
            if e.code != 429 or attempt == retries - 1:
                raise
            retry_after = e.headers.get("Retry-After")
            wait = float(retry_after) if retry_after else min(2 ** attempt, 20)
            print(f"[rpc] 429 on {method}; retry {attempt + 1}/{retries} in {wait}s")
            time.sleep(wait)
        except (urllib.error.URLError, TimeoutError) as e:
            last = e
            if attempt == retries - 1:
                raise
            wait = min(2 ** attempt, 20)
            print(f"[rpc] transient {type(e).__name__}; retry in {wait}s")
            time.sleep(wait)
    raise RuntimeError(f"RPC failed: {last}")


def iter_signatures(
    address: str,
    start_ts: int,
    end_ts: int,
    *,
    max_pages: int = 3,
    rpc_url: str = DEFAULT_RPC,
    sleep_s: float = 0.8,
) -> Iterable[SignatureRow]:
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
    p.add_argument("--start", required=True)
    p.add_argument("--end", required=True)
    p.add_argument("--pages", type=int, default=1)
    p.add_argument("--rpc", default=DEFAULT_RPC)
    args = p.parse_args()
    probe(args.address, args.start, args.end, args.pages, args.rpc)


if __name__ == "__main__":
    main()
