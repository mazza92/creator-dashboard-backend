"""Creator activity feed: replies, kit opens, grouped sends, follow-ups, gigs, pulse, payment issue, unread."""

import os
import unittest
from datetime import datetime, timedelta, timezone

import psycopg2

from services import creator_activity as ca

SCHEMA = "creator_activity_t"
NOW = datetime(2026, 11, 13, 15, 0, tzinfo=timezone.utc)


def _conn():
    return psycopg2.connect(host=os.getenv("DB_HOST"), port=os.getenv("DB_PORT"), dbname=os.getenv("DB_NAME"),
                            user=os.getenv("DB_USER"), password=os.getenv("DB_PASSWORD") or None,
                            options=f"-c search_path={SCHEMA}")


class Feed(unittest.TestCase):
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
            CREATE TABLE creators (id int PRIMARY KEY, niche text, creator_niches jsonb, subscription_status text,
                past_due_since timestamptz);
            CREATE TABLE pr_brands (id int PRIMARY KEY, brand_name text);
            CREATE TABLE creator_deals (id serial PRIMARY KEY, creator_id int, brand_id int, brand_name text, stage text,
                deal_type text, value_cents int, created_at timestamptz);
            CREATE TABLE polly_timeline_events (id serial PRIMARY KEY, creator_id int, brand_id int, event_type text,
                event_data jsonb, occurred_at timestamptz);
            CREATE TABLE polly_autopilot_queue (id serial PRIMARY KEY, creator_id int, brand_id int, brand_name text,
                status text, sent_at timestamptz, followup_sent_at timestamptz, pitch_meta jsonb DEFAULT '{{}}');
            CREATE TABLE creator_pipeline (id serial PRIMARY KEY, creator_id int, brand_id int, pitched_at timestamptz);
            CREATE TABLE ugc_pulse_items (id serial PRIMARY KEY, day date, niche text, kind text, text text, score int,
                created_at timestamptz DEFAULT NOW());
        """)
        boot.close()

    @classmethod
    def tearDownClass(cls):
        c = _conn()
        c.autocommit = True
        c.cursor().execute(f"DROP SCHEMA IF EXISTS {SCHEMA} CASCADE")
        c.close()

    def setUp(self):
        ca._READY = False
        self.conn = _conn()
        cur = self.conn.cursor()
        cur.execute("""DROP TABLE IF EXISTS creator_activity_seen;
                       TRUNCATE creators, pr_brands, creator_deals, polly_timeline_events, polly_autopilot_queue,
                                creator_pipeline, ugc_pulse_items""")
        y = NOW - timedelta(days=1)
        cur.execute("INSERT INTO creators VALUES (1, 'skincare', '[\"skincare\"]', 'past_due', %s)", (NOW - timedelta(days=2),))
        cur.execute("INSERT INTO pr_brands VALUES (5, 'Kylie'), (6, 'Glossier')")
        cur.execute("INSERT INTO creator_deals (creator_id, brand_id, brand_name, stage, deal_type, value_cents, created_at) "
                    "VALUES (1, 6, 'Glossier', 'negotiating', 'paid', 25000, %s)", (NOW - timedelta(hours=2),))
        for h in (3, 4):
            cur.execute("INSERT INTO polly_timeline_events (creator_id, brand_id, event_type, occurred_at) "
                        "VALUES (1, 5, 'portfolio_viewed', %s)", (NOW - timedelta(hours=h),))
        for i, name in enumerate(["Saie", "Merit", "Tower 28", "Kosas", "Ilia"]):
            cur.execute("INSERT INTO polly_autopilot_queue (creator_id, brand_id, brand_name, status, sent_at) "
                        "VALUES (1, %s, %s, 'sent', %s)", (100 + i, name, y))
        cur.execute("INSERT INTO polly_autopilot_queue (creator_id, brand_id, brand_name, status, sent_at, pitch_meta) "
                    "VALUES (1, 200, 'Bubble', 'sent', %s, '{\"origin\": \"paid_gig\", \"pay\": \"$150\"}')", (y,))
        cur.execute("UPDATE polly_autopilot_queue SET followup_sent_at = %s WHERE brand_name = 'Saie'", (NOW - timedelta(hours=6),))
        cur.execute("INSERT INTO ugc_pulse_items (day, niche, kind, text, score) VALUES (%s, 'skincare', 'hook', '“Hook”', 4)",
                    (NOW.date(),))
        self.conn.commit()

    def tearDown(self):
        self.conn.close()

    def test_feed(self):
        out = ca.feed(self.conn, 1, now=NOW)
        kinds = [i["kind"] for i in out["items"]]
        self.assertEqual(kinds[0], "billing")  # pinned
        texts = [i["text"] for i in out["items"]]
        self.assertIn("Glossier offered you $250", texts)
        self.assertIn("Kylie opened your kit 2×", texts)
        self.assertIn("5 brands pitched from your Gmail", texts)
        self.assertIn("Polly applied to 1 paid gig for you", texts)
        self.assertIn("1 follow-up sent", texts)
        self.assertTrue(any(k == "pulse" for k in kinds))
        self.assertEqual(out["pulse"]["niche"], "skincare")
        self.assertGreaterEqual(out["unread"], 5)
        ca.mark_seen(self.conn, 1, at=NOW)
        after = ca.feed(self.conn, 1, now=NOW)
        self.assertEqual(after["unread"], 1)  # only the pinned payment issue stays unread


if __name__ == "__main__":
    unittest.main()
