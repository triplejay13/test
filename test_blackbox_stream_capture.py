import unittest

from blackbox_stream_capture import extract_stream_rows


class FakePage:
    def __init__(self, lines):
        self._lines = lines

    def evaluate(self, _script):
        return self._lines


class CaptureFilterTests(unittest.TestCase):
    def test_phantom_only_filter(self):
        page = FakePage(
            [
                "9:31:10 QQQ Phantom Print Volume: 1 Spot: $612.56 $582.18",
                "10:00:00 SPY 100,000 BLOCK $60.0M $600.00",
            ]
        )
        rows = extract_stream_rows(page, phantom_only=True)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0][1], "QQQ")

    def test_all_alerts_mode(self):
        page = FakePage(
            [
                "9:31:10 QQQ Phantom Print Volume: 1 Spot: $612.56 $582.18",
                "10:00:00 SPY 100,000 BLOCK $60.0M $600.00",
            ]
        )
        rows = extract_stream_rows(page, phantom_only=False)
        self.assertEqual(len(rows), 2)


if __name__ == "__main__":
    unittest.main()
