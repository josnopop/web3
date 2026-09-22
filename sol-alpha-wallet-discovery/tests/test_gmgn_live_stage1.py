import json
import re
import time
import unittest
import urllib.parse
import urllib.request
import uuid
from datetime import datetime
from decimal import Decimal, InvalidOperation


README = "https://raw.githubusercontent.com/GMGNAI/gmgn-skills/main/Readme.md"
GMGN_BASE = "https://openapi.gmgn.ai"
GT_BASE = "https://api.geckoterminal.com/api/v2"
SOL_RPC = "https://api.mainnet-beta.solana.com"
EXCLUDED = {
    "So11111111111111111111111111111111111111112",
    "11111111111111111111111111111111",
    "EPjFWdd5AufqSSqeM2qQ9y8vS3T4U2Jb7Dd8xM6Y8T9",
}


def public_get(url, headers=None, timeout=30):
    req = urllib.request.Request(
        url,
        headers=headers or {"User-Agent": "sol-copyability-live-verify"},
    )
    started = time.perf_counter()
    with urllib.request.urlopen(req, timeout=timeout) as response:
        raw = response.read().decode("utf-8")
        status = response.status
    return status, round((time.perf_counter() - started) * 1000), json.loads(raw)


def post_json(url, body, timeout=30):
    req = urllib.request.Request(
        url,
        data=json.dumps(body).encode("utf-8"),
        method="POST",
        headers={"Content-Type": "application/json", "User-Agent": "sol-copyability-live-verify"},
    )
    started = time.perf_counter()
    with urllib.request.urlopen(req, timeout=timeout) as response:
        raw = response.read().decode("utf-8")
        status = response.status
    return status, round((time.perf_counter() - started) * 1000), json.loads(raw)


def demo_key():
    _, _, payload = public_get(README)
    raise AssertionError("unexpected")


def read_demo_key():
    req = urllib.request.Request(README, headers={"User-Agent": "gmgn-stage4-live-verify"})
    with urllib.request.urlopen(req, timeout=20) as response:
        text = response.read().decode("utf-8")
    match = re.search(r"GMGN_API_KEY=([A-Za-z0-9_]+)", text)
    if not match:
        raise AssertionError("GMGN demo key not found")
    return match.group(1)


def gmgn_get(path, params, key):
    query = dict(params)
    query["timestamp"] = int(time.time())
    query["client_id"] = str(uuid.uuid4())
    url = f"{GMGN_BASE}{path}?" + urllib.parse.urlencode(query, doseq=True)
    return public_get(
        url,
        headers={
            "X-APIKEY": key,
            "Content-Type": "application/json",
            "User-Agent": "gmgn-stage4-live-verify",
        },
    )


def iso_ts(value):
    if not value:
        return None
    return int(datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp())


def dec(value):
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return None


def token_price_usd(attrs, token):
    if attrs.get("from_token_address") == token:
        return dec(attrs.get("price_from_in_usd"))
    if attrs.get("to_token_address") == token:
        return dec(attrs.get("price_to_in_usd"))
    return None


def tx_account_keys(tx_payload):
    result = tx_payload.get("result") or {}
    tx = result.get("transaction") or {}
    message = tx.get("message") or {}
    keys = set()
    for item in message.get("accountKeys") or []:
        if isinstance(item, str):
            keys.add(item)
        elif isinstance(item, dict) and item.get("pubkey"):
            keys.add(item["pubkey"])
    meta = result.get("meta") or {}
    loaded = meta.get("loadedAddresses") or {}
    keys.update(loaded.get("writable") or [])
    keys.update(loaded.get("readonly") or [])
    return keys


class TestGMGNStage4FreeReplay(unittest.TestCase):
    def test_free_second_level_copyability_replay(self):
        key = read_demo_key()
        status, gmgn_ms, payload = gmgn_get(
            "/v1/user/smartmoney",
            {"chain": "sol", "limit": 100},
            key,
        )
        self.assertEqual(status, 200)
        self.assertEqual(payload.get("code"), 0, payload)
        rows = (payload.get("data") or {}).get("list") or []

        candidates = []
        seen = set()
        for row in sorted(rows, key=lambda r: r.get("timestamp") or 0, reverse=True):
            token = row.get("base_address")
            if row.get("side") != "buy" or not token or token in EXCLUDED or token in seen:
                continue
            if not row.get("transaction_hash"):
                continue
            seen.add(token)
            candidates.append(row)
            if len(candidates) >= 5:
                break

        print(f"GMGN_STAGE4_FREE_GMGN_ELAPSED_MS={gmgn_ms}")
        print(f"GMGN_STAGE4_FREE_CANDIDATES={len(candidates)}")

        chosen = None
        total_external_calls = 0

        for cand in candidates:
            token = cand["base_address"]
            tx_hash = cand["transaction_hash"]
            age = int(time.time()) - int(cand.get("timestamp") or 0)

            try:
                _, pools_ms, pools_payload = public_get(
                    f"{GT_BASE}/networks/solana/tokens/{token}/pools?page=1"
                )
                total_external_calls += 1
            except Exception as exc:
                print(f"GMGN_STAGE4_FREE_POOL_LOOKUP_ERROR={token}:{type(exc).__name__}")
                continue
            pools = pools_payload.get("data") or []
            pool_map = {
                (p.get("attributes") or {}).get("address"): p
                for p in pools
                if (p.get("attributes") or {}).get("address")
            }
            if not pool_map:
                print(f"GMGN_STAGE4_FREE_NO_POOLS={token}")
                continue

            try:
                _, rpc_ms, tx_payload = post_json(
                    SOL_RPC,
                    {
                        "jsonrpc": "2.0",
                        "id": 1,
                        "method": "getTransaction",
                        "params": [
                            tx_hash,
                            {
                                "encoding": "jsonParsed",
                                "maxSupportedTransactionVersion": 0,
                                "commitment": "confirmed",
                            },
                        ],
                    },
                )
                total_external_calls += 1
            except Exception as exc:
                print(f"GMGN_STAGE4_FREE_RPC_ERROR={tx_hash}:{type(exc).__name__}")
                continue

            account_keys = tx_account_keys(tx_payload)
            matching_pools = [p for p in pool_map if p in account_keys]
            print(
                f"GMGN_STAGE4_FREE_CANDIDATE={token}:age={age}:"
                f"pools={len(pool_map)}:account_keys={len(account_keys)}:"
                f"matched_pools={len(matching_pools)}:pool_ms={pools_ms}:rpc_ms={rpc_ms}"
            )
            if not matching_pools:
                continue

            # Give the market enough time to produce +2/+5/+10 second observations.
            age_now = int(time.time()) - int(cand.get("timestamp") or 0)
            if age_now < 14:
                time.sleep(14 - age_now)

            for pool_addr in matching_pools[:2]:
                try:
                    _, trades_ms, trades_payload = public_get(
                        f"{GT_BASE}/networks/solana/pools/{pool_addr}/trades"
                    )
                    total_external_calls += 1
                except Exception as exc:
                    print(f"GMGN_STAGE4_FREE_TRADES_ERROR={pool_addr}:{type(exc).__name__}")
                    continue

                trades = [t.get("attributes") or {} for t in trades_payload.get("data") or []]
                exact = next((t for t in trades if t.get("tx_hash") == tx_hash), None)
                print(
                    f"GMGN_STAGE4_FREE_POOL_TRADES={pool_addr}:{len(trades)}:"
                    f"{trades_ms}ms:exact={bool(exact)}"
                )
                if not exact:
                    continue

                entry_ts = iso_ts(exact.get("block_timestamp")) or int(cand.get("timestamp") or 0)
                timeline = []
                for trade in trades:
                    ts = iso_ts(trade.get("block_timestamp"))
                    price = token_price_usd(trade, token)
                    if ts is not None and price is not None and price > 0:
                        timeline.append((ts, price, trade.get("tx_hash")))
                timeline.sort(key=lambda x: x[0])
                if any(ts > entry_ts for ts, _, _ in timeline):
                    chosen = (cand, pool_addr, exact, timeline)
                    break
            if chosen:
                break

        print(f"GMGN_STAGE4_FREE_EXTERNAL_CALLS={total_external_calls}")
        self.assertIsNotNone(chosen, "Could not resolve a replayable Smart Money pool via free public sources")

        cand, pool_addr, entry_trade, timeline = chosen
        token = cand["base_address"]
        entry_ts = iso_ts(entry_trade.get("block_timestamp")) or int(cand["timestamp"])
        entry_price = token_price_usd(entry_trade, token) or dec(cand.get("price_usd"))
        self.assertIsNotNone(entry_price)
        self.assertGreater(entry_price, 0)

        print(f"GMGN_STAGE4_FREE_TOKEN={token}")
        print(f"GMGN_STAGE4_FREE_POOL={pool_addr}")
        print(f"GMGN_STAGE4_FREE_ENTRY_TX={cand.get('transaction_hash')}")
        print(f"GMGN_STAGE4_FREE_ENTRY_PRICE={entry_price}")
        print(f"GMGN_STAGE4_FREE_TIMELINE_ROWS={len(timeline)}")

        resolved = 0
        for delay in (2, 5, 10):
            target = entry_ts + delay
            after = next(((ts, price, tx) for ts, price, tx in timeline if ts >= target), None)
            if not after:
                print(f"GMGN_STAGE4_FREE_DELAY_{delay}S=NO_TRADE")
                continue
            ts, price, tx = after
            move = (price / entry_price - Decimal("1")) * Decimal("100")
            print(
                f"GMGN_STAGE4_FREE_DELAY_{delay}S="
                f"actual_lag={ts-entry_ts},price={price},move_pct={move:.6f},tx={tx}"
            )
            resolved += 1

        print(f"GMGN_STAGE4_FREE_RESOLVED_DELAYS={resolved}")
        self.assertGreaterEqual(resolved, 2)


if __name__ == "__main__":
    unittest.main()
