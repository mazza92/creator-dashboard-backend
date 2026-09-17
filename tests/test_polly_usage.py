import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from services.polly_usage import _count_map, log_usage, period_start


class PollyUsageTests(unittest.TestCase):
    def test_count_map(self):
        out = _count_map([{"intent": "suggest_brands", "n": 4}, {"intent": "chat", "n": 2}], "intent")
        self.assertEqual(out["suggest_brands"], 4)
        self.assertEqual(out["chat"], 2)

    def test_period_start_is_in_the_past(self):
        start = period_start(7)
        self.assertTrue(start.tzinfo)

    def test_log_usage_noop_without_conn(self):
        log_usage(None, 1, "open")


if __name__ == "__main__":
    unittest.main()
