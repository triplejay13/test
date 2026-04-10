import unittest

from unified_phantom_alerts import parse_watchlist, passes_watchlist


class UnifiedHelpersTests(unittest.TestCase):
    def test_parse_watchlist(self):
        wl = parse_watchlist("qqq, spy , TSLA")
        self.assertEqual(wl, {"QQQ", "SPY", "TSLA"})

    def test_passes_watchlist(self):
        wl = {"QQQ", "SPY"}
        self.assertTrue(passes_watchlist("QQQ", wl))
        self.assertFalse(passes_watchlist("TSLA", wl))
        self.assertTrue(passes_watchlist("TSLA", set()))


if __name__ == "__main__":
    unittest.main()
