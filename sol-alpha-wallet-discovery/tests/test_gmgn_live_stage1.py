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
    payload = json.loads(body)
    return status, elapsed_ms, payload


def smartmoney_rows(key):
    status, elapsed_ms, payload = gmgn_get(
        "/v1/user/smartmoney", {"chain": "sol", "limit": 200}, key
    )
    if status != 200 or payload.get("code") != 0:
        raise AssertionError(payload)
    rows = (payload.get("data") or {}).get("list") or []
    return elapsed_ms, rows


class TestGMGNLive(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.key = demo_key()

    def test_01_solana_smartmoney_live(self):
        elapsed_ms, rows = smartmoney_rows(self.key)
        makers = [r.get("maker") for r in rows if r.get("maker")]
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
        print(f"GMGN_STAGE1_ELAPSED_MS={elapsed_ms}")
        print(f"GMGN_STAGE1_RECORDS={len(rows)}")
        print(f"GMGN_STAGE1_UNIQUE_MAKERS={len(set(makers))}")
        print(f"GMGN_STAGE1_UNIQUE_TOKENS={len(set(tokens))}")
        print("GMGN_STAGE1_SIDES=" + json.dumps(sides, sort_keys=True))
        print("GMGN_STAGE1_TAGS=" + json.dumps(tags, sort_keys=True))
        print(
            "GMGN_STAGE1_FIELDS="
            + ",".join(sorted({key for row in rows for key in row.keys()}))
        )

        self.assertGreater(len(rows), 0)
        self.assertGreater(len(set(makers)), 0)

    def test_02_batch_wallet_stats(self):
        _, rows = smartmoney_rows(self.key)
        makers = list(dict.fromkeys(r.get("maker") for r in rows if r.get("maker")))
        self.assertGreater(len(makers), 0)

        results = {}
        total_started = time.perf_counter()
        for period in ("7d", "30d"):
            status, elapsed_ms, payload = gmgn_get(
                "/v1/user/wallet_stats",
                {"chain": "sol", "wallet_address": makers, "period": period},
                self.key,
            )
            self.assertEqual(status, 200)
            self.assertEqual(payload.get("code"), 0, payload)
            data = payload.get("data")
            if isinstance(data, dict):
                stats_rows = [data]
            elif isinstance(data, list):
                stats_rows = data
            else:
                stats_rows = []
            results[period] = (elapsed_ms, stats_rows)

        total_ms = round((time.perf_counter() - total_started) * 1000)
        print(f"GMGN_STAGE2_INPUT_WALLETS={len(makers)}")
        print(f"GMGN_STAGE2_TOTAL_ELAPSED_MS={total_ms}")

        for period, (elapsed_ms, stats_rows) in results.items():
            field_union = sorted({k for r in stats_rows if isinstance(r, dict) for k in r})
            with_core = sum(
                1
                for r in stats_rows
                if isinstance(r, dict)
                and any(
                    r.get(k) is not None
                    for k in ("realized_profit", "winrate", "buy_count", "sell_count", "pnl")
                )
            )
            print(f"GMGN_STAGE2_{period.upper()}_ELAPSED_MS={elapsed_ms}")
            print(f"GMGN_STAGE2_{period.upper()}_ROWS={len(stats_rows)}")
            print(f"GMGN_STAGE2_{period.upper()}_WITH_CORE_STATS={with_core}")
            print(
                f"GMGN_STAGE2_{period.upper()}_FIELDS="
                + ",".join(field_union)
            )
            if stats_rows:
                sample = stats_rows[0]
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
                    f"GMGN_STAGE2_{period.upper()}_SAMPLE="
                    + json.dumps(sample_core, sort_keys=True)
                )

            self.assertGreater(len(stats_rows), 0)
            self.assertGreater(with_core, 0)


if __name__ == "__main__":
    unittest.main()
