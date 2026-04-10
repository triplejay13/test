import tempfile
import unittest
from pathlib import Path

from phantom_cli_alerts import Deduper, parse_event, parse_volume


class ParseVolumeTests(unittest.TestCase):
    def test_integer(self):
        self.assertEqual(parse_volume("10"), 10)

    def test_comma_formatted(self):
        self.assertEqual(parse_volume("1,500"), 1500)

    def test_k_suffix(self):
        self.assertEqual(parse_volume("198k"), 198_000)

    def test_k_suffix_uppercase(self):
        self.assertEqual(parse_volume("2K"), 2_000)

    def test_m_suffix(self):
        self.assertEqual(parse_volume("2.5m"), 2_500_000)

    def test_b_suffix(self):
        self.assertEqual(parse_volume("1b"), 1_000_000_000)


class ParseEventTests(unittest.TestCase):
    def test_parses_integer_volume(self):
        row = {
            "TIME": "9:31:10", "SYMBOL": "qqq",
            "MESSAGE": "Phantom Print Volume: 10 Spot: $612.22",
            "PRICE": "$613.11",
        }
        event = parse_event(row)
        self.assertIsNotNone(event)
        assert event is not None
        self.assertEqual(event.symbol, "QQQ")
        self.assertEqual(event.phantom_volume, 10)
        self.assertAlmostEqual(event.phantom_spot, 612.22)

    def test_parses_k_suffix_volume(self):
        row = {
            "TIME": "1:11:24", "SYMBOL": "KFY",
            "MESSAGE": "Phantom Print Volume: 198k Spot: $60.8",
            "PRICE": "$60.30",
        }
        event = parse_event(row)
        self.assertIsNotNone(event)
        assert event is not None
        self.assertEqual(event.symbol, "KFY")
        self.assertEqual(event.phantom_volume, 198_000)
        self.assertAlmostEqual(event.phantom_spot, 60.8)

    def test_parses_comma_volume(self):
        row = {
            "TIME": "10:05:00", "SYMBOL": "SPY",
            "MESSAGE": "Phantom Print Volume: 1,500 Spot: $520.00",
            "PRICE": "$520.50",
        }
        event = parse_event(row)
        self.assertIsNotNone(event)
        assert event is not None
        self.assertEqual(event.phantom_volume, 1500)

    def test_ignores_non_phantom(self):
        row = {
            "TIME": "10:00:00", "SYMBOL": "SPY",
            "MESSAGE": "Dark Pool Sweep Volume: 200 Spot: $520.00",
            "PRICE": "$521.00",
        }
        self.assertIsNone(parse_event(row))

    def test_empty_price_does_not_raise(self):
        row = {
            "TIME": "9:31:10", "SYMBOL": "QQQ",
            "MESSAGE": "Phantom Print Volume: 10 Spot: $612.22",
            "PRICE": "",
        }
        event = parse_event(row)
        self.assertIsNotNone(event)
        assert event is not None
        self.assertAlmostEqual(event.stream_price, 0.0)

    def test_fingerprint_stable_across_whitespace(self):
        base = {
            "TIME": "9:31:10", "SYMBOL": "QQQ",
            "MESSAGE": "Phantom Print Volume: 10 Spot: $612.22",
            "PRICE": "$613.11",
        }
        extra_ws = dict(base, MESSAGE="Phantom Print Volume:  10 Spot: $612.22")
        e1 = parse_event(base)
        e2 = parse_event(extra_ws)
        assert e1 and e2
        self.assertEqual(e1.fingerprint, e2.fingerprint)

    def test_fingerprint_differs_for_different_events(self):
        row1 = {
            "TIME": "9:31:10", "SYMBOL": "QQQ",
            "MESSAGE": "Phantom Print Volume: 10 Spot: $612.22",
            "PRICE": "$613.11",
        }
        row2 = dict(row1, SYMBOL="SPY")
        e1 = parse_event(row1)
        e2 = parse_event(row2)
        assert e1 and e2
        self.assertNotEqual(e1.fingerprint, e2.fingerprint)


class DeduperTests(unittest.TestCase):
    def test_context_manager_closes_connection(self):
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = Path(f.name)
        with Deduper(db_path) as d:
            self.assertTrue(d.is_new("abc"))
            self.assertFalse(d.is_new("abc"))
        self.assertIsNone(d.conn)

    def test_persists_across_instances(self):
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = Path(f.name)
        with Deduper(db_path) as d1:
            d1.is_new("xyz")
        with Deduper(db_path) as d2:
            self.assertFalse(d2.is_new("xyz"))


if __name__ == "__main__":
    unittest.main()
