import unittest

from phantom_cli_alerts import parse_event


class ParseEventTests(unittest.TestCase):
    def test_parses_phantom_event(self):
        row = {
            "TIME": "9:31:10",
            "SYMBOL": "qqq",
            "MESSAGE": "Phantom Print Volume: 10 Spot: $612.22",
            "PRICE": "$613.11",
        }
        event = parse_event(row)
        self.assertIsNotNone(event)
        assert event is not None
        self.assertEqual(event.symbol, "QQQ")
        self.assertEqual(event.phantom_volume, 10)
        self.assertAlmostEqual(event.phantom_spot, 612.22)

    def test_ignores_non_phantom(self):
        row = {
            "TIME": "10:00:00",
            "SYMBOL": "SPY",
            "MESSAGE": "Dark Pool Sweep Volume: 200 Spot: $520.00",
            "PRICE": "$521.00",
        }
        self.assertIsNone(parse_event(row))


if __name__ == "__main__":
    unittest.main()
