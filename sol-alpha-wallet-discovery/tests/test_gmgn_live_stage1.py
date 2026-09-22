import json
import re
import time
import unittest
import urllib.parse
import urllib.request
import uuid


README = "https://raw.githubusercontent.com/GMGNAI/gmgn-skills/main/Readme.md"
BASE = "https://openapi.gmgn.ai"


def demo_key():
    with urllib.request.urlopen(README, timeout=20) as response:
        readme = response.read().decode("utf-8")
    match = re.search(r"GMGN_API_KEY=([A-Za-z0-9_]+)", readme)
    if not match:
        raise AssertionError("GMGN public demo key not found in upstream README")
    return match.group(1)


def gmgn_get(path, params, key):
    query = dict(params)
    query["timestamp"] = int(time.time())
    query["client_id"] = str(uuid.uuid4())
    encoded = urllib.parse.urlencode(query, doseq=True)
    request = urllib.request.Request(
        f"{BASE}{path}?{encoded}",
        headers={
            "X-APIKEY": key,
            "Content-Type": "application/json",
            "User-Agent": "gmgn-live-verify",
        },
    )
    started = time.perf_counter()
    with urllib.request.urlopen(request, timeout=30) as response:
        body = response.read().decode("utf-8")
        status = response.status
    elapsed_ms = round((time.perf_counter() - started) * 1000)
    return status, elapsed_ms, json.loads(body)


class TestGMGNLive(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.key = demo_key()
        status, cls.stage1_ms, payload = gmgn_get(
            "/v1/user/smartmoney",
            {"chain": "sol", "limit": 200},
            cls.key,
        )
        if status != 200 or payload.get("code") != 0:
            raise AssertionError(payload)
        cls.smart_rows = (payload.get("data") or {}).get("list") or []
        cls.makers = list(
            dict.fromkeys(r.get("maker") for r in cls.smart_rows if r.get("maker"))
        )

    def test_01_solana_smartmoney_live(self):
        rows = self.smart_rows
        makers = self.makers
        tokens = [r.get("base_address") for r in rows if r.get("base_address")]
        sides = {}
        tags = {}
        for row in rows:
            side = row.get("side")
            if side:
                sides[side] = sides.get(side, 0) + 1
            for tag in ((row.get("maker_info") or {}).get("tags") or []):
                tags[tag] = tags.get(tag, 0) + 1

        print("GMGN_STAGE1_HTTP_STATUS=200")
        print(f"GMGN_STAGE1_ELAPSED_MS={self.stage1_ms}")
        print(f"GMGN_STAGE1_RECORDS={len(rows)}")
        print(f"GMGN_STAGE1_UNIQUE_MAKERS={len(makers)}")
        print(f"GMGN_STAGE1_UNIQUE_TOKENS={len(set(tokens))}")
        print("GMGN_STAGE1_SIDES=" + json.dumps(sides, sort_keys=True))
        print("GMGN_STAGE1_TAGS=" + json.dumps(tags, sort_keys=True))
        self.assertGreater(len(rows), 0)
        self.assertGreater(len(makers), 0)

    def test_02_batch_wallet_stats_7d(self):
        self.assertGreater(len(self.makers), 0)
        status, elapsed_ms, payload = gmgn_get(
            "/v1/user/wallet_stats",
            {
                "chain": "sol",
                "wallet_address": self.makers,
                "period": "7d",
            },
            self.key,
        )
        self.assertEqual(status, 200)
        self.assertEqual(payload.get("code"), 0, payload)

        data = payload.get("data")
        if isinstance(data, list):
            rows = data
        elif isinstance(data, dict):
            rows = [data]
        else:
            rows = []

        core = ("realized_profit", "winrate", "buy_count", "sell_count", "pnl")
        with_core = sum(
            1
            for row in rows
            if isinstance(row, dict) and any(row.get(k) is not None for k in core)
        )
        fields = sorted({k for row in rows if isinstance(row, dict) for k in row})

        print(f"GMGN_STAGE2_INPUT_WALLETS={len(self.makers)}")
        print(f"GMGN_STAGE2_7D_HTTP_STATUS={status}")
        print(f"GMGN_STAGE2_7D_ELAPSED_MS={elapsed_ms}")
        print(f"GMGN_STAGE2_7D_ROWS={len(rows)}")
        print(f"GMGN_STAGE2_7D_WITH_CORE_STATS={with_core}")
        print("GMGN_STAGE2_7D_FIELDS=" + ",".join(fields))
        if rows:
            sample = rows[0]
            sample_core = {
                k: sample.get(k)
                for k in (
                    "wallet_address",
                    "realized_profit",
                    "unrealized_profit",
                    "winrate",
                    "total_cost",
                    "buy_count",
                    "sell_count",
                    "pnl",
                )
                if k in sample
            }
            print(
                "GMGN_STAGE2_7D_SAMPLE="
                + json.dumps(sample_core, sort_keys=True)
            )

        self.assertGreater(len(rows), 0)
        self.assertGreater(with_core, 0)


if __name__ == "__main__":
    unittest.main()
