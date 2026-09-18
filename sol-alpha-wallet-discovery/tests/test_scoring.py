import unittest

from scoring import WalletFeatures, freeze_snapshot, score_wallet


class WalletScoringTests(unittest.TestCase):
    def test_profitable_human_speed_wallet_survives(self):
        f = WalletFeatures(
            wallet="GoodWallet",
            dex_txs=80,
            active_days=20,
            distinct_tokens=30,
            buys=45,
            sells=35,
            gross_quote_out_sol=30,
            gross_quote_in_sol=44,
            realized_quote_pnl_sol=14,
            win_tokens=20,
            closed_tokens=28,
            median_hold_minutes=55,
            rug_like_tokens=1,
            same_slot_ratio=0.05,
            max_trades_per_minute=4,
        )
        s = score_wallet(f)
        self.assertIn(s.tier, {"S", "A", "B"})
        self.assertGreaterEqual(s.score, 50)

    def test_extreme_hft_is_rejected(self):
        f = WalletFeatures(
            wallet="BotWallet",
            dex_txs=100000,
            active_days=30,
            distinct_tokens=500,
            buys=50000,
            sells=50000,
            gross_quote_out_sol=5000,
            gross_quote_in_sol=5200,
            realized_quote_pnl_sol=200,
            win_tokens=400,
            closed_tokens=500,
            median_hold_minutes=1,
            rug_like_tokens=0,
            same_slot_ratio=0.75,
            max_trades_per_minute=200,
        )
        s = score_wallet(f)
        self.assertEqual(s.tier, "REJECT")
        self.assertIn("hft_or_market_maker", s.notes)

    def test_freeze_hash_is_deterministic(self):
        f = WalletFeatures(
            wallet="W",
            dex_txs=50,
            active_days=15,
            distinct_tokens=20,
            buys=25,
            sells=25,
            gross_quote_out_sol=10,
            gross_quote_in_sol=17,
            realized_quote_pnl_sol=7,
            win_tokens=14,
            closed_tokens=18,
            median_hold_minutes=60,
            rug_like_tokens=0,
            same_slot_ratio=0.03,
            max_trades_per_minute=3,
        )
        s = score_wallet(f)
        meta = {"freeze": "2026-09-02T16:00:00+00:00"}
        a = freeze_snapshot([s], meta)
        b = freeze_snapshot([s], meta)
        self.assertEqual(a, b)


if __name__ == "__main__":
    unittest.main()
