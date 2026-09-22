import json
import re
import time
import unittest
import urllib.parse
import urllib.request
import uuid
from collections import defaultdict


README = "https://raw.githubusercontent.com/GMGNAI/gmgn-skills/main/Readme.md"
BASE = "https://openapi.gmgn.ai"


def demo_key():
    req = urllib.request.Request(README, headers={"User-Agent": "gmgn-stage5-live-verify"})
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
    url = f"{BASE}{path}?" + urllib.parse.urlencode(query, doseq=True)
    req = urllib.request.Request(
        url,
        headers={
            "X-APIKEY": key,
            "Content-Type": "application/json",
            "User-Agent": "gmgn-stage5-live-verify",
        },
    )
    started = time.perf_counter()
    with urllib.request.urlopen(req, timeout=30) as response:
        raw = response.read().decode("utf-8")
        status = response.status
    elapsed_ms = round((time.perf_counter() - started) * 1000)
    return status, elapsed_ms, json.loads(raw)


class TestGMGNStage5Cluster(unittest.TestCase):
    def test_smartmoney_cluster_and_independence_signals(self):
        key = demo_key()
        status, feed_ms, payload = gmgn_get(
            "/v1/user/smartmoney",
            {"chain": "sol", "limit": 100},
            key,
        )
        self.assertEqual(status, 200)
        self.assertEqual(payload.get("code"), 0, payload)
        rows = (payload.get("data") or {}).get("list") or []

        by_token = defaultdict(list)
        for row in rows:
            if row.get("side") != "buy":
                continue
            token = row.get("base_address")
            maker = row.get("maker")
            ts = int(row.get("timestamp") or 0)
            if token and maker and ts:
                by_token[token].append(row)

        clusters = []
        for token, items in by_token.items():
            items = sorted(items, key=lambda x: int(x.get("timestamp") or 0))
            # Feed is very recent; still explicitly enforce <=30m rolling windows.
            best = []
            left = 0
            for right in range(len(items)):
                right_ts = int(items[right].get("timestamp") or 0)
                while left <= right and right_ts - int(items[left].get("timestamp") or 0) > 1800:
                    left += 1
                window = items[left:right + 1]
                distinct = {}
                for item in window:
                    distinct[item["maker"]] = item
                if len(distinct) > len(best):
                    best = list(distinct.values())
            if len(best) >= 2:
                times = [int(x["timestamp"]) for x in best]
                clusters.append({
                    "token": token,
                    "makers": [x["maker"] for x in best],
                    "count": len(best),
                    "span_s": max(times) - min(times),
                    "amount_usd": sum(float(x.get("amount_usd") or 0) for x in best),
                })

        clusters.sort(key=lambda x: (x["count"], x["amount_usd"]), reverse=True)
        strong = [c for c in clusters if c["count"] >= 3]

        print(f"GMGN_STAGE5_FEED_ELAPSED_MS={feed_ms}")
        print(f"GMGN_STAGE5_RECORDS={len(rows)}")
        print(f"GMGN_STAGE5_BUY_TOKENS={len(by_token)}")
        print(f"GMGN_STAGE5_CLUSTERS_GE2={len(clusters)}")
        print(f"GMGN_STAGE5_CLUSTERS_GE3={len(strong)}")
        if clusters:
            print("GMGN_STAGE5_TOP_CLUSTER=" + json.dumps(clusters[0], sort_keys=True))

        self.assertGreater(len(clusters), 0, "No >=2-wallet convergence cluster in live feed")
        top = clusters[0]
        sample_makers = top["makers"][:3]

        profiles = []
        for index, wallet in enumerate(sample_makers):
            if index:
                time.sleep(0.8)
            status, elapsed_ms, stat_payload = gmgn_get(
                "/v1/user/wallet_stats",
                {
                    "chain": "sol",
                    "wallet_address": wallet,
                    "period": "7d",
                },
                key,
            )
            self.assertEqual(status, 200)
            self.assertEqual(stat_payload.get("code"), 0, stat_payload)
            data = stat_payload.get("data") or {}
            common = data.get("common") or {}
            profiles.append({
                "wallet": wallet,
                "elapsed_ms": elapsed_ms,
                "fund_from": common.get("fund_from"),
                "fund_from_address": common.get("fund_from_address"),
                "tag": common.get("tag"),
                "tags": common.get("tags") or [],
                "realized_profit": data.get("realized_profit"),
                "buy": data.get("buy"),
                "sell": data.get("sell"),
            })

        fund_addresses = [
            p["fund_from_address"]
            for p in profiles
            if p.get("fund_from_address")
        ]
        same_funder = (
            len(fund_addresses) >= 2
            and len(set(fund_addresses)) < len(fund_addresses)
        )

        print("GMGN_STAGE5_PROFILES=" + json.dumps(profiles, sort_keys=True))
        print(f"GMGN_STAGE5_FUNDING_ADDRESSES_AVAILABLE={len(fund_addresses)}")
        print(f"GMGN_STAGE5_SAME_FUNDER_SIGNAL={same_funder}")
        print(
            "GMGN_STAGE5_INDEPENDENCE_PRECHECK="
            + ("SUSPICIOUS_SHARED_FUNDER" if same_funder else "NO_SHARED_FUNDER_DETECTED")
        )

        self.assertGreaterEqual(len(profiles), 2)


if __name__ == "__main__":
    unittest.main()
