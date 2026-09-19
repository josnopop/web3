import unittest
from unittest.mock import patch

from providers import Coverage, assert_formal_freeze_allowed, best_historical_source


class ProviderTests(unittest.TestCase):
    @patch.dict("os.environ", {}, clear=True)
    def test_public_rpc_is_fallback_not_full(self):
        src = best_historical_source()
        self.assertEqual(src.name, "Solana public RPC")
        self.assertEqual(src.coverage, Coverage.PARTIAL_EXACT_HISTORY)
        with self.assertRaises(RuntimeError):
            assert_formal_freeze_allowed(src)

    @patch.dict("os.environ", {"GOOGLE_CLOUD_PROJECT": "p"}, clear=True)
    def test_bigquery_unlocks_formal_freeze(self):
        src = best_historical_source()
        self.assertEqual(src.coverage, Coverage.FULL_EXACT_HISTORY)
        assert_formal_freeze_allowed(src)


if __name__ == "__main__":
    unittest.main()
