"""The email rulebook: retired kinds, audience, daily and weekly caps, one offer a week, transactional always."""

import os
import unittest
from datetime import datetime, timedelta, timezone

import psycopg2

from services import email_policy as ep

SCHEMA = "email_policy_t"
NOW = datetime(2026, 11, 6, 12, 0, tzinfo=timezone.utc)


def _conn():
    return psycopg2.connect(host=os.getenv("DB_HOST"), port=os.getenv("DB_PORT"), dbname=os.getenv("DB_NAME"),
                            user=os.getenv("DB_USER"), password=os.getenv("DB_PASSWORD") or None,
                            options=f"-c search_path={SCHEMA}")


class EmailPolicyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            boot = psycopg2.connect(host=os.getenv("DB_HOST"), port=os.getenv("DB_PORT"),
                                    dbname=os.getenv("DB_NAME"), user=os.getenv("DB_USER"),
                                    password=os.getenv("DB_PASSWORD") or None)
        except Exception as err:
            raise unittest.SkipTest(f"no db: {err}")
        boot.autocommit = True
        boot.cursor().execute(f"""
            DROP SCHEMA IF EXISTS {SCHEMA} CASCADE; CREATE SCHEMA {SCHEMA}; SET search_path TO {SCHEMA};
            CREATE TABLE polly_email_log (creator_id int, kind text, ok boolean DEFAULT true, sent_at timestamptz);
            CREATE TABLE lifecycle_email_sends (creator_id int, template_slug text, status text DEFAULT 'sent',
                                                sent_at timestamptz);
        """)
        boot.close()

    @classmethod
    def tearDownClass(cls):
        c = _conn()
        c.autocommit = True
        c.cursor().execute(f"DROP SCHEMA IF EXISTS {SCHEMA} CASCADE")
        c.close()

    def setUp(self):
        self.conn = _conn()
        self.conn.cursor().execute("TRUNCATE polly_email_log, lifecycle_email_sends")
        self.conn.commit()

    def tearDown(self):
        self.conn.close()

    def sent(self, kind, hours_ago, lifecycle=False):
        cur = self.conn.cursor()
        if lifecycle:
            cur.execute("INSERT INTO lifecycle_email_sends (creator_id, template_slug, sent_at) VALUES (1, %s, %s)",
                        (kind, NOW - timedelta(hours=hours_ago)))
        else:
            cur.execute("INSERT INTO polly_email_log (creator_id, kind, sent_at) VALUES (1, %s, %s)",
                        (kind, NOW - timedelta(hours=hours_ago)))
        self.conn.commit()

    def check(self, kind, pro=False):
        return ep.decide(self.conn, 1, kind, pro, now=NOW)

    def test_retired_and_audience(self):
        self.assertEqual(self.check("pro_week"), (False, "retired"))
        self.assertEqual(self.check("polly_brief", pro=True), (False, "free_only"))
        self.assertEqual(self.check("paywall_followup", pro=True), (False, "free_only"))
        self.assertEqual(self.check("autopilot_headsup"), (False, "pro_only"))
        self.assertEqual(self.check("polly_brief"), (True, "ok"))

    def test_one_a_day_but_news_still_lands(self):
        self.sent("polly_brief", 5)
        self.assertEqual(self.check("month_report"), (False, "daily_cap"))
        self.assertEqual(self.check("kit_view"), (True, "ok"))
        self.assertEqual(self.check("month_report")[0], False)
        self.sent("polly_brief", 30)
        self.assertEqual(ep.decide(self.conn, 1, "month_report", False, now=NOW + timedelta(hours=16))[0], True)

    def test_weekly_cap_counts_lifecycle_emails_too(self):
        self.sent("weekly_digest", 30, lifecycle=True)
        self.sent("polly_brief", 60)
        self.sent("kit_view", 90)
        self.assertEqual(self.check("new_brand_push"), (False, "weekly_cap"))
        self.assertEqual(self.check("new_brand_push", pro=True), (True, "ok"))

    def test_one_offer_a_week_across_both_systems(self):
        self.sent("max_quota_hit", 48, lifecycle=True)
        self.assertEqual(self.check("paywall_followup"), (False, "offer_gap"))
        self.assertEqual(self.check("free_week"), (False, "offer_gap"))

    def test_transactional_always_goes(self):
        for k in ("polly_brief", "kit_view", "polly_intro"):
            self.sent(k, 2)
        self.assertEqual(self.check("checkout_recovery"), (True, "transactional"))
        self.assertEqual(self.check("trial_ending", pro=True), (True, "transactional"))

    def test_recipient_applies_the_rulebook(self):
        from unittest import mock
        from services import polly_alerts as pa
        row = {"creator_id": 1, "user_id": 2, "email": "a@b.co", "first_name": "ana", "subscription_tier": "free",
               "unlocks_tier": "", "unsubscribed_at": None, "approval_status": "approved"}
        cur = mock.MagicMock()
        cur.fetchone.return_value = row
        with mock.patch.object(pa, "_cursor", return_value=cur), \
                mock.patch.object(ep, "allowed", side_effect=lambda c, cid, kind, pro: kind != "pro_week") as gate:
            self.assertIsNotNone(pa._recipient(self.conn, 1, kind="polly_brief"))
            self.assertIsNone(pa._recipient(self.conn, 1, kind="pro_week"))
            self.assertIsNotNone(pa._recipient(self.conn, 1))
        self.assertEqual(gate.call_count, 2)

    def test_chatty_pro_emails_are_spaced(self):
        self.sent("autopilot_headsup", 30)
        self.assertEqual(self.check("autopilot_headsup", pro=True), (False, "kind_gap"))


if __name__ == "__main__":
    unittest.main()


class MomentKindsPolicyTests(unittest.TestCase):
    def test_kinds(self):
        self.assertIn("reply_win", ep.EVENT)
        self.assertIn("saved_brands", ep.FREE_ONLY)
        self.assertEqual(ep.MIN_GAP["niche_gifting"].days, 6)
