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


def auth_url(path, params):
    query = dict(params)
    query["timestamp"] = int(time.time())
    query["client_id"] = str(uuid.uuid4())
    return f"{BASE}{path}?" + urllib.parse.urlencode(query, doseq=True)


def gmgn_request(method, path, params, key, body=None):
    encoded_body = None if body is None else json.dumps(body).encode("utf-8")
    request = urllib.request.Request(
        auth_url(path, params),
        data=encoded_body,
        method=method,
        headers={
            "X-APIKEY": key,
            "Content-Type": "application/json",
            "User-Agent": "gmgn-live-verify",
        },
    )
    started = time.perf_counter()
    with urllib.request.urlopen(request, timeout=30) as response:
        raw = response.read().decode("utf-8")
        status = response.status
    elapsed_ms = round((time.perf_counter() - started) * 1000)
    return status, elapsed_ms, json.loads(raw)


class TestGMGNLive(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.key = demo_key()
        status, cls.stage1_ms, payload = gmgn_request(
            "GET",
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
        print(f"GMGN_STAGE1_UNIQUE_MAKERS={len(self.makers)}")
        print(f"GMGN_STAGE1_UNIQUE_TOKENS={len(set(tokens))}")
        print("GMGN_STAGE1_SIDES=" + json.dumps(sides, sort_keys=True))
        print("GMGN_STAGE1_TAGS=" + json.dumps(tags, sort_keys=True))
        self.assertGreater(len(rows), 0)
        self.assertGreater(len(self.makers), 0)

    def test_02_batch_wallet_profits_7d(self):
        status, elapsed_ms, payload = gmgn_request(
            "POST",
            "/v1/user/wallet_profits",
            {},
            self.key,
            {
                "chain": "sol",
                "period": "7d",
                "wallet_addresses": self.makers,
            },
        )
        self.assertEqual(status, 200)
        self.assertEqual(payload.get("code"), 0, payload)
        data = payload.get("data") or {}
        rows = data.get("list") or []

        fields = sorted({k for row in rows if isinstance(row, dict) for k in row})
        with_profit = sum(
            1
            for row in rows
            if isinstance(row, dict) and row.get("realized_profit") is not None
        )
        addresses = {r.get("wallet_address") for r in rows if r.get("wallet_address")}

        print(f"GMGN_STAGE2_INPUT_WALLETS={len(self.makers)}")
        print(f"GMGN_STAGE2_7D_HTTP_STATUS={status}")
        print(f"GMGN_STAGE2_7D_ELAPSED_MS={elapsed_ms}")
        print(f"GMGN_STAGE2_7D_ROWS={len(rows)}")
        print(f"GMGN_STAGE2_7D_UNIQUE_WALLETS={len(addresses)}")
        print(f"GMGN_STAGE2_7D_WITH_PROFIT={with_profit}")
        print("GMGN_STAGE2_7D_FIELDS=" + ",".join(fields))

        preview = []
        for row in rows[:5]:
            preview.append(
                {
                    k: row.get(k)
                    for k in (
                        "wallet_address",
                        "realized_profit",
                        "realized_profit_cost",
                        "buy",
                        "sell",
                        "unrealized_profit",
                        "total_realized_profit",
                        "total_profit",
                        "total_cost",
                    )
                    if k in row
                }
            )
        print("GMGN_STAGE2_7D_PREVIEW=" + json.dumps(preview, sort_keys=True))

        self.assertGreater(len(rows), 0)
        self.assertEqual(len(addresses), len(self.makers))
        self.assertEqual(with_profit, len(rows))


if __name__ == "__main__":
    unittest.main()
