"""Gifting-confirmed brands: only evidence earns the badge, and only brand-run lists take box requests."""

import os
import unittest
from unittest import mock

import psycopg2

from services import gifting_confirmed as gc
from services import gifting_sweep as sweep

SCHEMA = "gifting_t"

DDL = f"""
DROP SCHEMA IF EXISTS {SCHEMA} CASCADE;
CREATE SCHEMA {SCHEMA};
SET search_path TO {SCHEMA};
CREATE TABLE pr_brands (id int PRIMARY KEY, brand_name text, status text DEFAULT 'published',
                        source_opportunity_id int);
CREATE TABLE opportunities (id int PRIMARY KEY, status text, closes_at timestamptz, pr_brand_id int,
                            additional_notes text);
CREATE TABLE brand_pr_campaigns (id serial PRIMARY KEY, brand_id int, status text, shipped_at timestamptz,
                                 locked_at timestamptz, updated_at timestamptz DEFAULT NOW(),
                                 creator_spotlighted_at timestamptz);
CREATE TABLE brand_billing (brand_id int, status text);
CREATE TABLE brand_pr_applications (id serial PRIMARY KEY, creator_id int, brand_id int, status text,
                                    shipped_at timestamptz, updated_at timestamptz DEFAULT NOW());
CREATE TABLE discovered_brands (id int PRIMARY KEY, brand_id int, last_signal_at timestamptz);
CREATE TABLE brand_signals (discovered_brand_id int, tiktok_id text, signal_type text, niche text);
"""


def _conn():
    return psycopg2.connect(host=os.getenv("DB_HOST"), port=os.getenv("DB_PORT"), dbname=os.getenv("DB_NAME"),
                            user=os.getenv("DB_USER"), password=os.getenv("DB_PASSWORD") or None,
                            options=f"-c search_path={SCHEMA}")


class GiftingConfirmedDbTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            boot = psycopg2.connect(host=os.getenv("DB_HOST"), port=os.getenv("DB_PORT"),
                                    dbname=os.getenv("DB_NAME"), user=os.getenv("DB_USER"),
                                    password=os.getenv("DB_PASSWORD") or None)
        except Exception as err:  # no local database
            raise unittest.SkipTest(f"no db: {err}")
        boot.autocommit = True
        boot.cursor().execute(DDL)
        boot.close()

    @classmethod
    def tearDownClass(cls):
        try:
            c = _conn()
            c.autocommit = True
            c.cursor().execute(f"DROP SCHEMA IF EXISTS {SCHEMA} CASCADE")
            c.close()
        except Exception:
            pass

    def setUp(self):
        gc._READY = False
        self.conn = _conn()
        cur = self.conn.cursor()
        cur.execute("""
            TRUNCATE pr_brands, opportunities, brand_pr_campaigns, brand_billing, brand_pr_applications,
                     discovered_brands, brand_signals;
            INSERT INTO pr_brands (id, brand_name, source_opportunity_id) VALUES
              (1, 'Listed', 10), (2, 'Shipped', NULL), (3, 'Seen on TikTok', NULL), (4, 'Guess', NULL),
              (5, 'Old TikTok', NULL), (6, 'Minted roster only', NULL), (7, 'Affiliate only', NULL);
            INSERT INTO opportunities (id, status, pr_brand_id) VALUES (10, 'live', 1);
            INSERT INTO brand_pr_applications (creator_id, brand_id, status, shipped_at) VALUES
              (1, 2, 'ships', NOW() - INTERVAL '5 days'),
              (1, 4, 'review', NULL), (2, 6, 'review', NULL), (3, 1, 'review', NULL);
            INSERT INTO brand_pr_campaigns (brand_id, status) VALUES (6, 'active');
            INSERT INTO discovered_brands (id, brand_id, last_signal_at) VALUES
              (100, 3, NOW() - INTERVAL '3 days'), (101, 5, NOW() - INTERVAL '200 days'),
              (102, 7, NOW() - INTERVAL '3 days');
            INSERT INTO brand_signals VALUES (100, 'v1', 'gifted', 'skincare'), (101, 'v2', 'gifted', 'skincare'),
                                             (102, 'v3', 'affiliate', 'skincare');
        """)
        cur.execute("ALTER TABLE pr_brands DROP COLUMN IF EXISTS gifting_signal, "
                    "DROP COLUMN IF EXISTS gifting_confirmed_at")
        self.conn.commit()

    def tearDown(self):
        self.conn.close()

    def signals(self):
        cur = self.conn.cursor()
        cur.execute("SELECT id, gifting_signal FROM pr_brands ORDER BY id")
        return dict(cur.fetchall())

    def test_only_evidence_confirms_a_brand(self):
        counts = gc.refresh(self.conn)
        self.assertEqual(self.signals(), {1: "brand_list", 2: "shipped", 3: "tiktok", 4: None, 5: None,
                                          6: None, 7: None})
        self.assertEqual(counts, {"brand_list": 1, "shipped": 1, "tiktok": 1})

    def test_signal_clears_when_evidence_goes_stale(self):
        gc.refresh(self.conn)
        cur = self.conn.cursor()
        cur.execute("UPDATE discovered_brands SET last_signal_at = NOW() - INTERVAL '90 days' WHERE id = 100")
        self.conn.commit()
        gc.refresh(self.conn)
        self.assertIsNone(self.signals()[3])

    def test_box_requests_only_where_the_brand_reads_them(self):
        gc.refresh(self.conn)
        cur = self.conn.cursor()
        self.assertTrue(gc.takes_box_requests(cur, 1))
        for bid in (2, 3, 4, 6):
            self.assertFalse(gc.takes_box_requests(cur, bid), bid)

    def test_unread_requests_come_off_the_list(self):
        gc.refresh(self.conn)
        self.assertEqual(gc.hide_unread_requests(self.conn), 2)
        cur = self.conn.cursor()
        cur.execute("SELECT brand_id, status FROM brand_pr_applications ORDER BY brand_id")
        self.assertEqual(cur.fetchall(), [(1, "review"), (2, "ships"), (4, "hidden"), (6, "hidden")])


class SweepPlanTests(unittest.TestCase):
    def test_next_niche_picks_never_swept_then_oldest(self):
        conn = mock.MagicMock()
        cur = conn.cursor.return_value
        from datetime import datetime
        cur.fetchall.side_effect = [
            [("skincare",)],
            [("skincare", datetime(2026, 11, 5)), ("makeup", datetime(2026, 11, 1)),
             ("tech", datetime(2026, 11, 3))],
        ]
        with mock.patch.object(sweep, "NICHE_WORDS", {"skincare": [], "makeup": [], "tech": [], "pet": []}):
            self.assertEqual(sweep.next_niche(conn), "pet")

    def test_next_niche_none_when_all_swept_today(self):
        conn = mock.MagicMock()
        conn.cursor.return_value.fetchall.side_effect = [[("skincare",), ("makeup",)], []]
        with mock.patch.object(sweep, "NICHE_WORDS", {"skincare": [], "makeup": []}):
            self.assertIsNone(sweep.next_niche(conn))

    def test_start_respects_daily_budget(self):
        conn = mock.MagicMock()
        plan = [{"key": f"hashtag:t{i}", "type": "hashtag", "query": f"t{i}"} for i in range(10)]
        with mock.patch.object(sweep.radar, "stale_queries", return_value=plan), \
                mock.patch.object(sweep, "sweep_spend_today", return_value=14.9), \
                mock.patch.object(sweep.radar, "_start_round") as start:
            res = sweep.start_niche(conn, "pet")
        self.assertEqual(res["status"], "budget")
        start.assert_not_called()

    def test_start_records_a_sweep_run(self):
        conn = mock.MagicMock()
        plan = [{"key": "hashtag:gifted", "type": "hashtag", "query": "gifted"}]
        with mock.patch.object(sweep.radar, "stale_queries", return_value=plan), \
                mock.patch.object(sweep, "sweep_spend_today", return_value=0), \
                mock.patch.object(sweep.radar, "_start_round", return_value="apify1"), \
                mock.patch.object(sweep.radar, "_record", return_value=77) as rec:
            res = sweep.start_niche(conn, "pet")
        self.assertEqual((res["status"], res["run_id"]), ("started", 77))
        self.assertEqual(rec.call_args.kwargs["served_from"], "sweep")
        self.assertIsNone(rec.call_args.args[1]["creator_id"])


if __name__ == "__main__":
    unittest.main()
