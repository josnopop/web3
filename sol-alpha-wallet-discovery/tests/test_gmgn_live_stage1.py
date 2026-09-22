import json
import re
import time
import unittest
import urllib.parse
import urllib.request
import uuid
from collections import defaultdict
from decimal import Decimal, InvalidOperation


README = "https://raw.githubusercontent.com/GMGNAI/gmgn-skills/main/Readme.md"
BASE = "https://openapi.gmgn.ai"
WALLET = "4mdMHfiMjBLNGwirfmVwH4N4B5LzPBqadqBrgkY6Z5q5"


def demo_key():
    with urllib.request.urlopen(README, timeout=20) as response:
        readme = response.read().decode("utf-8")
    match = re.search(r"GMGN_API_KEY=([A-Za-z0-9_]+)", readme)
    if not match:
        raise AssertionError("GMGN public demo key not found")
    return match.group(1)


def get(path, params, key):
    query = dict(params)
    query["timestamp"] = int(time.time())
    query["client_id"] = str(uuid.uuid4())
    url = f"{BASE}{path}?" + urllib.parse.urlencode(query, doseq=True)
    req = urllib.request.Request(
        url,
        headers={
            "X-APIKEY": key,
            "Content-Type": "application/json",
            "User-Agent": "gmgn-stage3-live-verify",
        },
    )
    started = time.perf_counter()
    with urllib.request.urlopen(req, timeout=30) as response:
        raw = response.read().decode("utf-8")
        status = response.status
    return status, round((time.perf_counter() - started) * 1000), json.loads(raw)


def dec(value):
    try:
        return Decimal(str(value or "0"))
    except (InvalidOperation, ValueError):
        return Decimal("0")


class TestGMGNStage3Live(unittest.TestCase):
    def test_wallet_activity_profit_concentration_and_transfers(self):
        key = demo_key()
        status, elapsed_ms, payload = get(
            "/v1/user/wallet_activity",
            {
                "chain": "sol",
                "wallet_address": WALLET,
                "limit": 100,
                "type": ["buy", "sell", "transferIn", "transferOut"],
            },
            key,
        )
        self.assertEqual(status, 200)
        self.assertEqual(payload.get("code"), 0, payload)
        data = payload.get("data") or {}
        rows = data.get("activities") or []

        counts = defaultdict(int)
        pnl_by_token = defaultdict(Decimal)
        sell_pnls = []
        fields = set()

        for row in rows:
            fields.update(row.keys())
            event = row.get("event_type") or row.get("type") or "unknown"
            counts[event] += 1
            if event == "sell":
                proceeds = dec(row.get("cost_usd"))
                basis = dec(row.get("buy_cost_usd"))
                pnl = proceeds - basis
                sell_pnls.append(pnl)
                token = (row.get("token") or {}).get("address") or "unknown"
                pnl_by_token[token] += pnl

        positive_by_token = sorted(
            (v for v in pnl_by_token.values() if v > 0),
            reverse=True,
        )
        positive_total = sum(positive_by_token, Decimal("0"))
        top1 = positive_by_token[0] if positive_by_token else Decimal("0")
        top3 = sum(positive_by_token[:3], Decimal("0"))
        top1_share = (top1 / positive_total) if positive_total > 0 else Decimal("0")
        top3_share = (top3 / positive_total) if positive_total > 0 else Decimal("0")
        net_sell_pnl = sum(sell_pnls, Decimal("0"))

        print(f"GMGN_STAGE3_WALLET={WALLET}")
        print(f"GMGN_STAGE3_HTTP_STATUS={status}")
        print(f"GMGN_STAGE3_ELAPSED_MS={elapsed_ms}")
        print(f"GMGN_STAGE3_ACTIVITY_ROWS={len(rows)}")
        print("GMGN_STAGE3_EVENT_COUNTS=" + json.dumps(dict(counts), sort_keys=True))
        print(f"GMGN_STAGE3_SELL_ROWS={len(sell_pnls)}")
        print(f"GMGN_STAGE3_NET_SELL_PNL={net_sell_pnl}")
        print(f"GMGN_STAGE3_POSITIVE_TOKEN_COUNT={len(positive_by_token)}")
        print(f"GMGN_STAGE3_TOP1_POSITIVE_PROFIT_SHARE={float(top1_share):.6f}")
        print(f"GMGN_STAGE3_TOP3_POSITIVE_PROFIT_SHARE={float(top3_share):.6f}")
        print(f"GMGN_STAGE3_TRANSFER_IN_COUNT={counts.get('transferIn', 0)}")
        print(f"GMGN_STAGE3_TRANSFER_OUT_COUNT={counts.get('transferOut', 0)}")
        print("GMGN_STAGE3_FIELDS=" + ",".join(sorted(fields)))

        self.assertGreater(len(rows), 0)
        self.assertGreater(counts.get("buy", 0) + counts.get("sell", 0), 0)


if __name__ == "__main__":
    unittest.main()
