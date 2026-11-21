"""Day-4 follow-up email: only free creators' hand-sent pitches, never Gmail/relay sends (Polly follows those up)."""

import os
import unittest
from datetime import datetime, timedelta, timezone

import psycopg2

from services import followup_ready as fr
from services.email_policy import decide

NOW = datetime(2026, 11, 21, 12, 0, tzinfo=timezone.utc)
SCHEMA = "followup_ready_t"


def _conn():
    return psycopg2.connect(host=os.getenv("DB_HOST"), port=os.getenv("DB_PORT"), dbname=os.getenv("DB_NAME"),
                            user=os.getenv("DB_USER"), password=os.getenv("DB_PASSWORD") or None,
                            options=f"-c search_path={SCHEMA}")


class Due(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            boot = psycopg2.connect(host=os.getenv("DB_HOST"), port=os.getenv("DB_PORT"), dbname=os.getenv("DB_NAME"),
                                    user=os.getenv("DB_USER"), password=os.getenv("DB_PASSWORD") or None)
        except Exception as err:
            raise unittest.SkipTest(f"no db: {err}")
        boot.autocommit = True
        boot.cursor().execute(f"""
            DROP SCHEMA IF EXISTS {SCHEMA} CASCADE; CREATE SCHEMA {SCHEMA}; SET search_path TO {SCHEMA};
            CREATE TABLE creators (id int PRIMARY KEY, unlocks_tier text, subscription_tier text);
            CREATE TABLE pr_brands (id int PRIMARY KEY, brand_name text);
            CREATE TABLE polly_timeline_events (id serial PRIMARY KEY, creator_id int, brand_id int, event_type text,
                occurred_at timestamptz);
            CREATE TABLE polly_autopilot_queue (id serial PRIMARY KEY, creator_id int, brand_id int, sent_at timestamptz);
            CREATE TABLE polly_email_log (id serial PRIMARY KEY, creator_id int, kind text, brand_id int, ok boolean,
                ref_id text, subject text, message_id text, error text,
                sent_at timestamptz DEFAULT NOW());
            INSERT INTO creators VALUES (1, 'free', NULL), (2, 'pro', 'pro');
            INSERT INTO pr_brands VALUES (10, 'Hand Sent'), (11, 'Via Gmail'), (12, 'Replied'), (13, 'Too New'),
                (14, 'Followed'), (15, 'Emailed'), (16, 'Too Old');
        """)
        c = _conn()
        cur = c.cursor()
        ev = [(1, 10, "pitch_sent", 5), (1, 10, "pitch_sent", 5), (1, 10, "portfolio_viewed", 2),
              (1, 11, "pitch_sent", 5), (1, 12, "pitch_sent", 5), (1, 12, "brand_replied_interested", 1),
              (1, 13, "pitch_sent", 2), (1, 14, "pitch_sent", 6), (1, 14, "pitch_sent", 1),
              (1, 15, "pitch_sent", 5), (1, 16, "pitch_sent", 9), (2, 10, "pitch_sent", 5)]
        for cid, bid, kind, ago in ev:
            cur.execute("INSERT INTO polly_timeline_events (creator_id, brand_id, event_type, occurred_at) "
                        "VALUES (%s, %s, %s, %s)", (cid, bid, kind, NOW - timedelta(days=ago)))
        cur.execute("INSERT INTO polly_autopilot_queue (creator_id, brand_id, sent_at) VALUES (1, 11, %s)",
                    (NOW - timedelta(days=5),))
        cur.execute("INSERT INTO polly_email_log (creator_id, kind, brand_id, ok) VALUES (1, 'followup_ready', 15, TRUE)")
        c.commit()
        c.close()
        boot.close()

    @classmethod
    def tearDownClass(cls):
        c = _conn()
        c.autocommit = True
        c.cursor().execute(f"DROP SCHEMA IF EXISTS {SCHEMA} CASCADE")
        c.close()

    def test_only_free_hand_sent_due_unanswered(self):
        c = _conn()
        due = fr.due_manual_pitches(c, NOW)
        c.close()
        self.assertEqual(list(due), [1])
        self.assertEqual([b["brand_name"] for b in due[1]], ["Hand Sent"])
        self.assertTrue(due[1][0]["opened"])

    def test_email_copy(self):
        brands = [{"brand_id": 10, "brand_name": "Elite Eleven", "sent_at": NOW - timedelta(days=4), "opened": False},
                  {"brand_id": 11, "brand_name": "Kinfit", "sent_at": NOW - timedelta(days=5), "opened": False}]
        mail = fr.followup_ready_email("Maya", brands, now=NOW)
        self.assertEqual(mail["subject"], "2 follow-ups are ready")
        self.assertIn("4 days ago", mail["html"])
        self.assertIn("Also ready: Kinfit", mail["html"])
        self.assertIn("never use a free pitch", mail["html"])
        self.assertIn("chip=draft_followup", mail["html"])
        self.assertIn("brand_id=10", mail["html"])
        self.assertEqual(fr.followup_ready_email("", brands[:1], now=NOW)["subject"], "Your Elite Eleven follow-up is ready")

    def test_dry_run_and_send_logs_once(self):
        from unittest import mock
        sent = []
        c = _conn()
        with mock.patch("services.polly_alerts.ensure_alert_tables"), \
             mock.patch("services.polly_alerts._recipient", return_value={"user_id": 1, "email": "m@x.co", "first": "Maya", "is_pro": False}), \
             mock.patch("services.polly_alerts._unsubscribe_url", return_value=""):
            out = fr.email_followups_ready(c, send_fn=lambda *a: sent.append(a) or {"success": True}, now=NOW)
            again = fr.email_followups_ready(c, send_fn=lambda *a: sent.append(a) or {"success": True}, now=NOW)
        c.cursor().execute("DELETE FROM polly_email_log WHERE brand_id = 10")
        c.commit()
        c.close()
        self.assertEqual(len(out), 1)
        self.assertEqual(again, [])
        self.assertEqual(len(sent), 1)


class Policy(unittest.TestCase):
    def test_free_only_and_gap(self):
        class Cur:
            def __init__(self, rows): self.rows = rows
            def execute(self, *a): pass
            def fetchall(self): return self.rows
        class Conn:
            def __init__(self, rows): self.rows = rows
            def cursor(self): return Cur(self.rows)
        self.assertEqual(decide(Conn([]), 1, "followup_ready", True, now=NOW), (False, "free_only"))
        self.assertEqual(decide(Conn([]), 1, "followup_ready", False, now=NOW), (True, "ok"))
        recent = [("followup_ready", NOW - timedelta(days=1))]
        self.assertEqual(decide(Conn(recent), 1, "followup_ready", False, now=NOW), (False, "kind_gap"))
        # Event: another email earlier today doesn't block it.
        self.assertEqual(decide(Conn([("first_pitch", NOW - timedelta(hours=2))]), 1, "followup_ready", False, now=NOW),
                         (True, "ok"))


if __name__ == "__main__":
    unittest.main()
