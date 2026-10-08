import unittest
from datetime import datetime, timezone

from services.polly_usage import FUNNEL_STEPS, autopilot_snapshot, funnel_paywall_moment, polly_funnel


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


class _AutopilotCursor:
    def __init__(self, tables):
        self.tables = set(tables)
        self._one = None
        self._many = []

    def execute(self, sql, params=None):
        if "information_schema.tables" in sql:
            name = (params or [None])[0]
            self._one = {"ok": 1} if name in self.tables else None
            self._many = []
            return
        if "COUNT(*) FILTER (WHERE a.enabled)" in sql:
            self._one = {
                "settings_rows": 2, "enabled": 1, "paused": 1,
                "gmail_live": 1, "ready": 1,
            }
            return
        if "c.username" in sql:
            self._many = [{
                "creator_id": 9, "username": "ada", "enabled": True, "gmail": True,
                "pro": True, "monthly_target": 24, "drafts": 0, "approved": 3, "sent": 1,
            }]
            return
        if "SELECT status, COUNT" in sql:
            self._many = [{"status": "approved", "n": 6}, {"status": "sent", "n": 2}]
            self._one = None
            return
        if "sent_at >=" in sql:
            self._one = {"sent": 1, "sent_creators": 1}
            return
        self._one = None
        self._many = []

    def fetchone(self):
        return self._one

    def fetchall(self):
        return self._many


class AutopilotSnapshotTests(unittest.TestCase):
    def test_missing_table_is_empty(self):
        out = autopilot_snapshot(_AutopilotCursor([]), datetime(2026, 10, 1, tzinfo=timezone.utc))
        self.assertEqual(out["enabled"], 0)
        self.assertEqual(out["creators"], [])

    def test_counts_ready_senders_and_lists_accounts(self):
        out = autopilot_snapshot(
            _AutopilotCursor(["polly_autopilot", "polly_gmail_accounts", "polly_autopilot_queue"]),
            datetime(2026, 10, 1, tzinfo=timezone.utc),
        )
        self.assertEqual(out["enabled"], 1)
        self.assertEqual(out["paused"], 1)
        self.assertEqual(out["gmail_live"], 1)
        self.assertEqual(out["ready"], 1)
        self.assertEqual(out["sent"], 1)
        self.assertEqual(out["queue"], {"approved": 6, "sent": 2})
        self.assertEqual(out["creators"][0]["username"], "ada")
        self.assertTrue(out["creators"][0]["gmail"])


if __name__ == "__main__":
    unittest.main()
