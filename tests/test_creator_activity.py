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
            CREATE TABLE pr_brands (id int PRIMARY KEY, brand_name text, category text, status text DEFAULT 'published',
                source text, created_at timestamptz DEFAULT '2026-01-01');
            CREATE TABLE brand_ad_signals (brand_id int PRIMARY KEY, ads_first_seen date, ugc_style boolean);
            CREATE TABLE opportunities (id serial PRIMARY KEY, brand_name text, brand_category text, product_name text,
                campaign_description text, creator_niches text[], listing_brief jsonb, status text, closes_at timestamptz,
                published_at timestamptz, created_at timestamptz, additional_notes text, pr_brand_id int,
                spots_total int, spots_filled int);
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
                       TRUNCATE creators, pr_brands, brand_ad_signals, opportunities, creator_deals, polly_timeline_events, polly_autopilot_queue,
                                creator_pipeline, ugc_pulse_items""")
        y = NOW - timedelta(days=1)
        cur.execute("INSERT INTO creators VALUES (1, 'skincare', '[\"skincare\"]', 'past_due', %s)", (NOW - timedelta(days=2),))
        cur.execute("INSERT INTO pr_brands (id, brand_name) VALUES (5, 'Kylie'), (6, 'Glossier')")
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
        cur.execute("INSERT INTO ugc_pulse_items (day, niche, kind, text, score, created_at) "
                    "VALUES (%s, 'skincare', 'hook', '“Hook”', 4, %s)", (NOW.date(), NOW - timedelta(hours=8)))
        self.conn.commit()

    def tearDown(self):
        self.conn.close()

    def test_feed(self):
        out = ca.feed(self.conn, 1, now=NOW)
        kinds = [i["kind"] for i in out["items"]]
        self.assertEqual(kinds[0], "billing")  # pinned
        texts = [i["text"] for i in out["items"]]
        self.assertIn("Glossier offered you $250", texts)
        self.assertIn("Kylie viewed your portfolio 2×", texts)
        self.assertIn("5 brands pitched from your Gmail", texts)
        self.assertIn("Polly applied to 1 paid gig for you", texts)
        self.assertIn("1 follow-up sent", texts)
        self.assertTrue(any(k == "pulse" for k in kinds))
        self.assertEqual(out["pulse"]["niche"], "skincare")
        self.assertGreaterEqual(out["unread"], 5)
        ca.mark_seen(self.conn, 1, at=NOW)
        after = ca.feed(self.conn, 1, now=NOW)
        self.assertEqual(after["unread"], 1)  # only the pinned payment issue stays unread

    def _discoveries(self):
        cur = self.conn.cursor()
        d = NOW - timedelta(hours=5)
        cur.execute("""INSERT INTO pr_brands (id, brand_name, category, status, source, created_at) VALUES
            (10, 'Rhode', 'skincare', 'published', 'manual', %(d)s),
            (11, 'Bubble', 'Beauty', 'published', 'manual', %(d)s),
            (12, 'Gymshark', 'fitness', 'published', 'manual', %(d)s),
            (13, 'Draft Co', 'skincare', 'draft', 'manual', %(d)s),
            (14, 'Medicube', 'skincare', 'published', 'meta_ads_library', %(d)s),
            (15, 'Cerave', 'skincare', 'published', 'manual', '2025-01-01'),
            (16, 'Old Co', 'skincare', 'published', 'manual', '2025-01-01')""", {"d": d})
        cur.execute("INSERT INTO brand_ad_signals VALUES (15, %s, true), (16, '2025-02-01', true)", (NOW.date(),))
        live = dict(status="live", closes=None)
        for name, cat, niches, at in (("Skin Gig", "Skincare", ["skincare"], NOW - timedelta(hours=3)),
                                      ("Gym Gig", "Gym Enthusiasts", ["fitness"], NOW - timedelta(hours=3)),
                                      ("Old Gig", "Skincare", ["skincare"], NOW - timedelta(days=20))):
            cur.execute("""INSERT INTO opportunities (brand_name, brand_category, product_name, campaign_description,
                           creator_niches, status, published_at, created_at, spots_total, spots_filled)
                           VALUES (%s, %s, 'Serum', 'UGC video', %s, 'live', %s, %s, 5, 0)""",
                        (name, cat, niches, at, at))
        self.conn.commit()

    def test_new_brands_hot_brands_and_gigs(self):
        self._discoveries()
        items = {i["kind"]: i for i in ca.build_items(self.conn, 1, now=NOW)}
        self.assertEqual(items["new_brands"]["text"], "2 new brands in skincare added")
        self.assertEqual(items["new_brands"]["sub"], "Rhode, Bubble")
        self.assertEqual(items["hot_brands"]["text"], "2 skincare brands started running UGC ads")
        self.assertEqual(sorted(items["hot_brands"]["sub"].split(", ")), ["Cerave", "Medicube"])
        self.assertEqual(items["new_gigs"]["text"], "1 new paid UGC gig in skincare")
        self.assertEqual(items["new_gigs"]["sub"], "Skin Gig")
        self.assertEqual(items["new_gigs"]["href"], "/creator/dashboard/paid-gigs")

    def test_gigs_badge_clears_when_page_opened(self):
        self._discoveries()
        self.assertEqual(ca.feed(self.conn, 1, now=NOW)["gigs"]["new"], 1)
        before = ca.feed(self.conn, 1, now=NOW)["unread"]
        ca.mark_gigs_seen(self.conn, 1, at=NOW)
        self.assertEqual(ca.gigs_badge(self.conn, 1, now=NOW)["new"], 0)
        self.assertEqual(ca.feed(self.conn, 1, now=NOW)["unread"], before)  # the bell is untouched


if __name__ == "__main__":
    unittest.main()
