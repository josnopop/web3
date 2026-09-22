import json
import re
import time
import unittest
import urllib.parse
import urllib.request
import uuid
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation


README = "https://raw.githubusercontent.com/GMGNAI/gmgn-skills/main/Readme.md"
GMGN_BASE = "https://openapi.gmgn.ai"
GT_BASE = "https://api.geckoterminal.com/api/v2"


def demo_key():
    with urllib.request.urlopen(README, timeout=20) as response:
        readme = response.read().decode("utf-8")
    match = re.search(r"GMGN_API_KEY=([A-Za-z0-9_]+)", readme)
    if not match:
        raise AssertionError("GMGN public demo key not found")
    return match.group(1)


def get_json(url, headers=None, timeout=30):
    req = urllib.request.Request(
        url,
        headers=headers or {"User-Agent": "sol-copyability-live-verify"},
    )
    started = time.perf_counter()
    with urllib.request.urlopen(req, timeout=timeout) as response:
        raw = response.read().decode("utf-8")
        status = response.status
    return status, round((time.perf_counter() - started) * 1000), json.loads(raw)


def gmgn_get(path, params, key):
    query = dict(params)
    query["timestamp"] = int(time.time())
    query["client_id"] = str(uuid.uuid4())
    url = f"{GMGN_BASE}{path}?" + urllib.parse.urlencode(query, doseq=True)
    return get_json(
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


class TestGMGNStage4Live(unittest.TestCase):
    def test_copyability_with_second_level_pool_trades(self):
        key = demo_key()
        status, gmgn_ms, payload = gmgn_get(
            "/v1/user/smartmoney",
            {"chain": "sol", "limit": 100},
            key,
        )
        self.assertEqual(status, 200)
        self.assertEqual(payload.get("code"), 0, payload)
        rows = (payload.get("data") or {}).get("list") or []

        buys = []
        seen_tokens = set()
        for row in sorted(rows, key=lambda r: r.get("timestamp") or 0, reverse=True):
            token = row.get("base_address")
            if row.get("side") != "buy" or not token or token in seen_tokens:
                continue
            if not row.get("transaction_hash"):
                continue
            seen_tokens.add(token)
            buys.append(row)
            if len(buys) >= 4:
                break

        print(f"GMGN_STAGE4_GMGN_ELAPSED_MS={gmgn_ms}")
        print(f"GMGN_STAGE4_CANDIDATE_BUYS={len(buys)}")

        matched = None
        gt_calls = 0

        for cand in buys:
            token = cand["base_address"]
            pools_url = f"{GT_BASE}/networks/solana/tokens/{token}/pools?page=1"
            try:
                _, pools_ms, pools_payload = get_json(pools_url)
                gt_calls += 1
            except Exception as exc:
                print(f"GMGN_STAGE4_POOL_LOOKUP_ERROR={token}:{type(exc).__name__}")
                continue

            pools = pools_payload.get("data") or []
            print(f"GMGN_STAGE4_TOKEN_POOL_COUNT={token}:{len(pools)}:{pools_ms}ms")
            for pool in pools[:2]:
                pool_addr = (pool.get("attributes") or {}).get("address")
                if not pool_addr:
                    continue
                trades_url = f"{GT_BASE}/networks/solana/pools/{pool_addr}/trades"
                try:
                    _, trades_ms, trades_payload = get_json(trades_url)
                    gt_calls += 1
                except Exception as exc:
                    print(f"GMGN_STAGE4_TRADES_ERROR={pool_addr}:{type(exc).__name__}")
                    continue

                trades = [t.get("attributes") or {} for t in (trades_payload.get("data") or [])]
                exact = next(
                    (t for t in trades if t.get("tx_hash") == cand.get("transaction_hash")),
                    None,
                )
                print(
                    f"GMGN_STAGE4_POOL_TRADES={pool_addr}:{len(trades)}:{trades_ms}ms:"
                    f"exact={bool(exact)}"
                )
                if exact:
                    matched = (cand, pool_addr, trades, exact)
                    break
            if matched:
                break

        print(f"GMGN_STAGE4_GT_CALLS={gt_calls}")
        self.assertIsNotNone(matched, "No exact GMGN Smart Money tx found in sampled GeckoTerminal pools")

        cand, pool_addr, trades, entry_trade = matched
        token = cand["base_address"]
        entry_ts = iso_ts(entry_trade.get("block_timestamp")) or int(cand.get("timestamp") or 0)
        entry_price = token_price_usd(entry_trade, token) or dec(cand.get("price_usd"))
        self.assertIsNotNone(entry_price)
        self.assertGreater(entry_price, 0)

        timeline = []
        for trade in trades:
            ts = iso_ts(trade.get("block_timestamp"))
            price = token_price_usd(trade, token)
            if ts is not None and price is not None and price > 0:
                timeline.append((ts, price, trade.get("tx_hash")))
        timeline.sort(key=lambda x: x[0])

        print(f"GMGN_STAGE4_MATCH_TOKEN={token}")
        print(f"GMGN_STAGE4_MATCH_POOL={pool_addr}")
        print(f"GMGN_STAGE4_MATCH_TX={cand.get('transaction_hash')}")
        print(f"GMGN_STAGE4_ENTRY_TS={entry_ts}")
        print(f"GMGN_STAGE4_ENTRY_PRICE_USD={entry_price}")
        print(f"GMGN_STAGE4_TRADE_TIMELINE_ROWS={len(timeline)}")

        resolved = 0
        for delay in (2, 5, 10):
            target = entry_ts + delay
            after = next(((ts, price, tx) for ts, price, tx in timeline if ts >= target), None)
            if not after:
                print(f"GMGN_STAGE4_DELAY_{delay}S=NO_LATER_TRADE")
                continue
            ts, price, tx = after
            pct = (price / entry_price - Decimal("1")) * Decimal("100")
            print(
                f"GMGN_STAGE4_DELAY_{delay}S="
                f"actual_lag={ts-entry_ts},price={price},move_pct={pct:.6f},tx={tx}"
            )
            resolved += 1

        print(f"GMGN_STAGE4_RESOLVED_DELAYS={resolved}")
        self.assertGreaterEqual(resolved, 2)


if __name__ == "__main__":
    unittest.main()
