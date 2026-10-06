import unittest
from datetime import datetime, timezone

from services.polly_usage import FUNNEL_STEPS, funnel_paywall_moment, polly_funnel


class FunnelPaywallMomentTests(unittest.TestCase):
    def test_explicit_moment_wins(self):
        moment = {"moment": "after_send", "brand_id": 4}
        self.assertEqual(funnel_paywall_moment(moment, True, [], []), moment)

    def test_hard_paywall_without_moment(self):
        self.assertEqual(funnel_paywall_moment(None, True), {"moment": "pitch"})

    def test_pro_chip_and_starter(self):
        chip = [{"action": "unlock_pro"}]
        self.assertEqual(funnel_paywall_moment(None, False, chip, []), {"moment": "chip"})
        self.assertEqual(funnel_paywall_moment(None, False, [], chip), {"moment": "starter"})

    def test_no_prompt(self):
        self.assertIsNone(funnel_paywall_moment(None, False, [{"action": "generate_pitch"}], None))


class _FakeCursor:
    def __init__(self, row, moments, upgrades, sources=None):
        self._results = [row, moments, upgrades, sources or []]
        self._last = None
        self.sql = []

    def execute(self, sql, params=None):
        self.sql.append(sql)
        if "information_schema.tables" in sql:
            self._last = {"?column?": 1}
            return
        self._last = self._results.pop(0)

    def fetchone(self):
        return self._last

    def fetchall(self):
        return self._last


class PollyFunnelTests(unittest.TestCase):
    def test_nested_steps_and_splits(self):
        row = {
            "already_pro": 9, "opened": 100, "matches_shown": 80, "pitch_or_apply": 40,
            "paywall_shown": 10, "upgraded": 2, "upgraded_any": 3, "paywall_any": 12, "kit_viewed": 5,
        }
        cursor = _FakeCursor(
            row,
            [{"moment": "pitch", "n": 8}, {"moment": "after_send", "n": 4}],
            [{"moment": "pitch", "n": 2}, {"moment": "no_polly_paywall", "n": 1}],
            [{"source": "polly", "n": 2}, {"source": "directory", "n": 1}],
        )
        out = polly_funnel(cursor, datetime(2026, 10, 1, tzinfo=timezone.utc))
        self.assertEqual([s["key"] for s in out["steps"]], list(FUNNEL_STEPS))
        counts = [s["creators"] for s in out["steps"]]
        self.assertEqual(counts, [100, 80, 40, 10, 2])
        self.assertEqual(counts, sorted(counts, reverse=True))
        self.assertEqual(out["already_pro"], 9)
        self.assertEqual(out["upgraded_any"], 3)
        self.assertEqual(out["paywall_any"], 12)
        self.assertEqual(out["kit_viewed"], 5)
        self.assertEqual(out["paywall_moments"], {"pitch": 8, "after_send": 4})
        self.assertEqual(out["upgrade_moments"], {"pitch": 2, "no_polly_paywall": 1})
        self.assertEqual(out["checkout_sources"], {"polly": 2, "directory": 1})
        funnel_sql = next(s for s in cursor.sql if "already_pro" in s)
        self.assertIn("pro_since >= first_open", funnel_sql)
        self.assertIn("matches AND pitched AND paywall AND upgraded_after", funnel_sql)


if __name__ == "__main__":
    unittest.main()
