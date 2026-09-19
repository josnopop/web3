import unittest

from scoring import WalletFeatures, WindowMetrics, freeze_snapshot, score_wallet


def wm(days, pnl, wins, closed, hold=30, same_slot=0.05, tpm=4, top1=0.25, top3=0.55):
    return WindowMetrics(
        days=days, dex_txs=80, active_days=min(days, 16), distinct_tokens=35,
        realized_pnl_sol=pnl, win_tokens=wins, closed_tokens=closed,
        median_token_roi=0.35, top1_profit_concentration=top1,
        top3_profit_concentration=top3, median_hold_minutes=hold,
        rug_like_tokens=1, same_slot_ratio=same_slot, max_trades_per_minute=tpm,
    )


class WalletScoringTests(unittest.TestCase):
    def test_good_copyable_wallet_survives(self):
        f = WalletFeatures("GoodWallet", wm(7, 8, 9, 12), wm(15, 15, 17, 23), wm(30, 28, 24, 32))
        s = score_wallet(f)
        self.assertNotEqual(s.wallet_class, "Rejected")
        self.assertGreater(s.copyability, 0.5)

    def test_extreme_hft_is_rejected(self):
        f = WalletFeatures(
            "BotWallet",
            wm(7, 50, 30, 35, hold=0.2, same_slot=0.75, tpm=200),
            wm(15, 100, 60, 70, hold=0.2, same_slot=0.75, tpm=200),
            wm(30, 200, 120, 140, hold=0.2, same_slot=0.75, tpm=200),
        )
        s = score_wallet(f)
        self.assertEqual(s.wallet_class, "Rejected")
        self.assertIn("hft_or_market_maker", s.notes)

    def test_freeze_hash_is_deterministic(self):
        f = WalletFeatures("W", wm(7, 5, 8, 10), wm(15, 8, 12, 16), wm(30, 14, 20, 28))
        s = score_wallet(f)
        a = freeze_snapshot([s], {"freeze": "2026-09-15T16:00:00+00:00"})
        b = freeze_snapshot([s], {"freeze": "2026-09-15T16:00:00+00:00"})
        self.assertEqual(a, b)


if __name__ == "__main__":
    unittest.main()
