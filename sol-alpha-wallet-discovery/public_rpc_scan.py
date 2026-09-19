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
    return "https://api.mainnet.solana.com"


DEFAULT_RPC = _default_rpc()
PUBLIC_RPC_POOL = (
    "https://api.mainnet.solana.com",
    "https://solana-rpc.publicnode.com",
    "https://solana.drpc.org/",
)


def _rpc_targets(url: str) -> list[str]:
    # Dedicated/keyed endpoints remain sticky. Only rotate when we are on
    # the free public fallback so one provider's 429/403 does not stall scans.
    if (
        os.getenv("SOLANA_RPC_URL")
        or os.getenv("HELIUS_API_KEY")
        or os.getenv("SOLANATRACKER_API_KEY")
        or url not in PUBLIC_RPC_POOL
    ):
        return [url]
    return list(dict.fromkeys((url, *PUBLIC_RPC_POOL)))


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
    targets = _rpc_targets(url)
    total_attempts = max(retries, len(targets) * 2)
    for attempt in range(total_attempts):
        target = targets[attempt % len(targets)]
        req = urllib.request.Request(
            target,
            data=payload,
            headers={"content-type": "application/json", "user-agent": "sol-alpha-wallet-discovery/0.4"},
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
            if e.code not in (403, 429) or attempt == total_attempts - 1:
                raise
            if len(targets) > 1:
                print(f"[rpc] HTTP {e.code} on {target}; rotating provider")
                time.sleep(0.35)
            else:
                retry_after = e.headers.get("Retry-After")
                wait = float(retry_after) if retry_after else min(2 ** attempt, 20)
                print(f"[rpc] {e.code} on {method}; retry {attempt + 1}/{total_attempts} in {wait}s")
                time.sleep(wait)
        except (urllib.error.URLError, TimeoutError) as e:
            last = e
            if attempt == total_attempts - 1:
                raise
            wait = 0.35 if len(targets) > 1 else min(2 ** attempt, 20)
            print(f"[rpc] transient {type(e).__name__} on {target}; rotating/retrying")
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
