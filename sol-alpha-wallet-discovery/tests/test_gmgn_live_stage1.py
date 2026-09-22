import json
import re
import time
import unittest
import urllib.parse
import urllib.request
import uuid


README = "https://raw.githubusercontent.com/GMGNAI/gmgn-skills/main/Readme.md"
ENDPOINT = "https://openapi.gmgn.ai/v1/user/smartmoney"


class TestGMGNStage1Live(unittest.TestCase):
    def test_solana_smartmoney_live(self):
        with urllib.request.urlopen(README, timeout=20) as response:
            readme = response.read().decode("utf-8")
        match = re.search(r"GMGN_API_KEY=([A-Za-z0-9_]+)", readme)
        self.assertIsNotNone(match, "GMGN public demo key not found in upstream README")
        api_key = match.group(1)

        params = urllib.parse.urlencode(
            {
                "chain": "sol",
                "limit": 200,
                "timestamp": int(time.time()),
                "client_id": str(uuid.uuid4()),
            }
        )
        request = urllib.request.Request(
            f"{ENDPOINT}?{params}",
            headers={
                "X-APIKEY": api_key,
                "Content-Type": "application/json",
                "User-Agent": "gmgn-stage1-live-verify",
            },
        )

        started = time.perf_counter()
        with urllib.request.urlopen(request, timeout=30) as response:
            body = response.read().decode("utf-8")
            status = response.status
        elapsed_ms = round((time.perf_counter() - started) * 1000)

        payload = json.loads(body)
        self.assertEqual(status, 200)
        self.assertEqual(payload.get("code"), 0, payload)

        data = payload.get("data") or {}
        rows = data.get("list") or []
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

        print(f"GMGN_STAGE1_HTTP_STATUS={status}")
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
        print(
            "GMGN_STAGE1_SAMPLE_MAKERS="
            + ",".join(list(dict.fromkeys(makers))[:10])
        )

        self.assertGreater(len(rows), 0, "No smart-money records returned")
        self.assertGreater(len(set(makers)), 0, "No unique maker wallets returned")


if __name__ == "__main__":
    unittest.main()
