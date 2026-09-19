import os
import unittest
from unittest.mock import patch

from multi_source_discovery import (
    Evidence,
    _walk_mints,
    _walk_wallets,
    bitquery_candidates,
    cielo_seed_mints,
    codex_candidates,
    dexscreener_seed_mints,
    merge,
)


W1 = "6SAzeAUFCxCWwrDikbkkKZigre1SiJ7hcZE6gHwkuMJf"
W2 = "4EvYSYpt8ZbZNTwB2kjg7s8nXtiESryxCrorFm7cLGLR"
MINT = "So11111111111111111111111111111111111111112"


class MultiSourceDiscoveryTests(unittest.TestCase):
    def test_recursive_extractors(self):
        payload = {
            "walletAddress": W1,
            "nested": [{"trader": W2, "token_address": MINT}],
        }
        self.assertEqual(_walk_wallets(payload), {W1, W2})
        self.assertEqual(_walk_mints(payload), {MINT})

    def test_merge_counts_independent_sources(self):
        rows = merge(
            [
                [Evidence(W1, "A", "x", True, {})],
                [Evidence(W1, "B", "x", True, {}), Evidence(W2, "B", "x", True, {})],
            ]
        )
        by_wallet = {x["wallet"]: x for x in rows}
        self.assertEqual(by_wallet[W1]["source_count"], 2)
        self.assertEqual(by_wallet[W1]["sources"], ["A", "B"])
        self.assertGreater(
            by_wallet[W1]["discovery_priority"],
            by_wallet[W2]["discovery_priority"],
        )

    @patch.dict(os.environ, {"CODEX_API_KEY": "test"}, clear=True)
    @patch("multi_source_discovery._request_json")
    def test_codex_exposes_global_axiom_defined_views(self, req):
        req.return_value = {
            "data": {
                "filterWallets": {
                    "results": [
                        {
                            "address": W1,
                            "labels": [],
                            "tradeSourceIds": ["axiom", "defined"],
                        }
                    ]
                }
            }
        }
        rows = codex_candidates(limit=10)
        self.assertEqual(len(rows), 3)
        self.assertEqual(
            {x.source for x in rows},
            {"Codex Wallet Filter", "Axiom via Codex", "Defined via Codex"},
        )
        self.assertEqual(req.call_count, 3)

    @patch("multi_source_discovery._request_json")
    def test_dexscreener_solana_seed_mints(self, req):
        req.side_effect = [
            [{"chainId": "solana", "tokenAddress": MINT}, {"chainId": "ethereum", "tokenAddress": W1}],
            [], [], []
        ]
        self.assertEqual(dexscreener_seed_mints(), {MINT})
        self.assertEqual(req.call_count, 4)

    @patch.dict(os.environ, {"CIELO_API_KEY": "test"}, clear=True)
    @patch("multi_source_discovery._request_json")
    def test_cielo_seed_mints(self, req):
        req.return_value = {"data": [{"token": {"mint": MINT}}]}
        self.assertEqual(cielo_seed_mints(), {MINT})

    @patch.dict(os.environ, {"BITQUERY_TOKEN": "ory_test"}, clear=True)
    @patch("multi_source_discovery._request_json")
    def test_bitquery_token_top_trader_adapter(self, req):
        req.return_value = {
            "data": {
                "Solana": {
                    "DEXTradeByTokens": [
                        {
                            "Trade": {"Account": {"Owner": W1}},
                            "buys": 8,
                            "sells": 6,
                            "volume": "12000",
                            "trades": 14,
                        }
                    ]
                }
            }
        }
        rows = bitquery_candidates([MINT], per_token_limit=10)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].wallet, W1)
        self.assertEqual(rows[0].source, "Bitquery")


if __name__ == "__main__":
    unittest.main()
