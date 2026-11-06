"""Waitlist sequence: three emails on a schedule, value first, one way in (skip with Pro)."""

import unittest
from datetime import datetime, timedelta, timezone
from unittest import mock

from services import waitlist_sequence as ws

NOW = datetime(2026, 11, 6, 12, tzinfo=timezone.utc)


def row(days_old, sent=None):
    return {"created_at": NOW - timedelta(days=days_old), "sent": sent or {}}


class ScheduleTests(unittest.TestCase):
    def test_steps_follow_age_and_spacing(self):
        self.assertIsNone(ws._next_step(row(1), NOW))
        self.assertEqual(ws._next_step(row(3), NOW), "waitlist_1")
        w1 = (NOW - timedelta(days=1)).isoformat()
        self.assertIsNone(ws._next_step(row(6, {"waitlist_1": w1}), NOW))
        w1_old = (NOW - timedelta(days=4)).isoformat()
        self.assertEqual(ws._next_step(row(6, {"waitlist_1": w1_old}), NOW), "waitlist_2")
        both = {"waitlist_1": (NOW - timedelta(days=9)).isoformat(), "waitlist_2": (NOW - timedelta(days=6)).isoformat()}
        self.assertEqual(ws._next_step(row(12, both), NOW), "waitlist_3")
        done = dict(both, waitlist_3=NOW.isoformat())
        self.assertIsNone(ws._next_step(row(40, done), NOW))

    def test_old_backlog_starts_at_step_one(self):
        self.assertEqual(ws._next_step(row(45), NOW), "waitlist_1")


class _Conn:
    def rollback(self):
        pass


class CopyTests(unittest.TestCase):
    def test_first_email_place_brands_and_skip(self):
        brands = [{"brand_id": 5, "brand_name": "Glow Co"}, {"brand_id": 6, "brand_name": "Byoma"}]
        with mock.patch("services.polly_alerts.brief_brand_facts", return_value={}):
            mail = ws.waitlist_email(_Conn(), "waitlist_1", "Ana", 42, "skincare", brands, "u")
        self.assertEqual(mail["subject"], "You're #42 in line. 2 brands are already lined up for you")
        self.assertIn("/creator/waitlist?skip=1", mail["html"])
        self.assertIn("Skip the line with Pro", mail["html"])
        self.assertEqual(mail["html"].count(">Unlock</a>"), 2)
        self.assertIn("Check my place in line", mail["html"])

    def test_third_email_two_ways_in(self):
        mail = ws.waitlist_email(_Conn(), "waitlist_3", "Ana", 7, "", [], "u")
        self.assertEqual(mail["subject"], "Still #7 in line? Here's how to get in faster")
        self.assertIn("Post this week", mail["html"])


class PolicyTests(unittest.TestCase):
    def test_waitlist_kinds_are_free_only(self):
        from services.email_policy import FREE_ONLY
        self.assertTrue(set(ws.KINDS) <= FREE_ONLY)


if __name__ == "__main__":
    unittest.main()
