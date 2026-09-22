import json
import re
import time
import unittest
import urllib.error
import urllib.parse
import urllib.request
import uuid
from decimal import Decimal


README = "https://raw.githubusercontent.com/GMGNAI/gmgn-skills/main/Readme.md"
BASE = "https://openapi.gmgn.ai"
EXCLUDED = {
    "So11111111111111111111111111111111111111112",
    "11111111111111111111111111111111",
}


def demo_key():
    with urllib.request.urlopen(README, timeout=20) as response:
        text = response.read().decode("utf-8")
    match = re.search(r"GMGN_API_KEY=([A-Za-z0-9_]+)", text)
    if not match:
        raise AssertionError("GMGN demo key not found")
    return match.group(1)


def gmgn_get(path, params, key):
    query = dict(params)
    query["timestamp"] = int(time.time())
    query["client_id"] = str(uuid.uuid4())
    url = f"{BASE}{path}?" + urllib.parse.urlencode(query, doseq=True)
    req = urllib.request.Request(
        url,
        headers={
            "X-APIKEY": key,
            "Content-Type": "application/json",
            "User-Agent": "gmgn-stage4-kline-verify",
        },
    )
    started = time.perf_counter()
    try:
        with urllib.request.urlopen(req, timeout=30) as response:
            raw = response.read().decode("utf-8")
            status = response.status
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        elapsed_ms = round((time.perf_counter() - started) * 1000)
        return exc.code, elapsed_ms, json.loads(raw) if raw.startswith("{") else {"raw": raw}
    elapsed_ms = round((time.perf_counter() - started) * 1000)
    return status, elapsed_ms, json.loads(raw)


class TestGMGNStage4Kline(unittest.TestCase):
    def test_second_level_copyability_kline(self):
        key = demo_key()

        status, feed_ms, payload = gmgn_get(
            "/v1/user/smartmoney",
            {"chain": "sol", "limit": 100},
            key,
        )
        self.assertEqual(status, 200)
        self.assertEqual(payload.get("code"), 0, payload)
        rows = (payload.get("data") or {}).get("list") or []

        candidate = None
        now = int(time.time())
        for row in sorted(rows, key=lambda r: r.get("timestamp") or 0, reverse=True):
            token = row.get("base_address")
            ts = int(row.get("timestamp") or 0)
            if row.get("side") != "buy" or not token or token in EXCLUDED:
                continue
            if now - ts < 12:
                continue
            candidate = row
            break

        self.assertIsNotNone(candidate, "No suitable recent Smart Money buy")
        token = candidate["base_address"]
        entry_ts = int(candidate["timestamp"])

        status, kline_ms, kline_payload = gmgn_get(
            "/v1/market/token_kline",
            {
                "chain": "sol",
                "address": token,
                "resolution": "1s",
                "from": entry_ts - 2,
                "to": entry_ts + 20,
            },
            key,
        )

        print(f"GMGN_STAGE4_FEED_ELAPSED_MS={feed_ms}")
        print(f"GMGN_STAGE4_TOKEN={token}")
        print(f"GMGN_STAGE4_ENTRY_TX={candidate.get('transaction_hash')}")
        print(f"GMGN_STAGE4_ENTRY_TS={entry_ts}")
        print(f"GMGN_STAGE4_ENTRY_PRICE_USD={candidate.get('price_usd')}")
        print(f"GMGN_STAGE4_KLINE_HTTP_STATUS={status}")
        print(f"GMGN_STAGE4_KLINE_ELAPSED_MS={kline_ms}")
        print("GMGN_STAGE4_KLINE_ENVELOPE=" + json.dumps({
            k: kline_payload.get(k)
            for k in ("code", "error", "message", "upgrade_message")
            if k in kline_payload
        }, sort_keys=True))

        self.assertEqual(status, 200, kline_payload)
        self.assertEqual(kline_payload.get("code"), 0, kline_payload)

        data = kline_payload.get("data") or {}
        candles = data.get("list") or []
        print(f"GMGN_STAGE4_KLINE_ROWS={len(candles)}")
        print("GMGN_STAGE4_KLINE_FIELDS=" + ",".join(
            sorted({k for c in candles for k in c.keys()})
        ))

        self.assertGreater(len(candles), 0)

        entry_price = Decimal(str(candidate.get("price_usd") or "0"))
        resolved = 0
        for delay in (2, 5, 10):
            target = entry_ts + delay
            candle = next((c for c in candles if int(c.get("time") or 0) >= target), None)
            if not candle:
                print(f"GMGN_STAGE4_DELAY_{delay}S=NO_CANDLE")
                continue
            price = Decimal(str(candle.get("close") or "0"))
            move = ((price / entry_price) - Decimal("1")) * Decimal("100") if entry_price > 0 else Decimal("0")
            print(
                f"GMGN_STAGE4_DELAY_{delay}S="
                f"actual_lag={int(candle.get('time'))-entry_ts},close={price},move_pct={move:.6f}"
            )
            resolved += 1

        print(f"GMGN_STAGE4_RESOLVED_DELAYS={resolved}")
        self.assertGreaterEqual(resolved, 2)


if __name__ == "__main__":
    unittest.main()
