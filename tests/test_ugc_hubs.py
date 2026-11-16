"""Category hubs of brands with real UGC signals."""

import os
import unittest
from datetime import datetime, timedelta

import psycopg2
from psycopg2.extras import RealDictCursor

from services import brand_ugc as bu

SCHEMA = "ugc_hubs_t"


def _connect(**kw):
    return psycopg2.connect(host=os.getenv("DB_HOST"), port=os.getenv("DB_PORT"), dbname=os.getenv("DB_NAME"),
                            user=os.getenv("DB_USER"), password=os.getenv("DB_PASSWORD") or None, **kw)


class Hubs(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            boot = _connect()
        except Exception as e:  # pragma: no cover
            raise unittest.SkipTest(f"no database: {e}")
        boot.autocommit = True
        boot.cursor().execute(f"DROP SCHEMA IF EXISTS {SCHEMA} CASCADE; CREATE SCHEMA {SCHEMA}")
        boot.close()
        cls.conn = _connect(options=f"-c search_path={SCHEMA}")
        cur = cls.conn.cursor()
        cur.execute("""
            CREATE TABLE pr_brands (id serial PRIMARY KEY, brand_name text, slug text, logo_url text, category text,
                status text, source text, created_at timestamp, gifting_confirmed_at timestamp);
            CREATE TABLE brand_ad_signals (brand_id int, ads_last_seen date, ugc_style boolean);
            CREATE TABLE opportunities (id serial PRIMARY KEY, pr_brand_id int, status text, closes_at timestamp);
        """)
        now = datetime.utcnow()
        rows = [
            ("Gifter", "gifter", "skincare", "manual", now - timedelta(days=200), now),      # 1 gifting only
            ("Hot New", "hot-new", "skincare", "meta_ads_library", now, None),               # 2 hot
            ("Creator Ads", "creator-ads", "skincare", "meta_ads_library", now, None),       # 3 hot + creator ads
            ("Briefed", "briefed", "skincare", "manual", now - timedelta(days=200), None),   # 4 open brief
            ("Old Ads", "old-ads", "skincare", "meta_ads_library", now - timedelta(days=90), None),  # 5 stale: none
            ("Nothing", "nothing", "skincare", "manual", now, None),                         # 6 no signal
        ]
        for name, slug, cat, src, created, gifted in rows:
            cur.execute("INSERT INTO pr_brands (brand_name, slug, category, status, source, created_at, "
                        "gifting_confirmed_at) VALUES (%s, %s, %s, 'published', %s, %s, %s)",
                        (name, slug, cat, src, created, gifted))
        cur.execute("INSERT INTO brand_ad_signals VALUES (3, NULL, TRUE)")
        cur.execute("INSERT INTO opportunities (pr_brand_id, status) VALUES (4, 'live')")
        for i in range(10):
            cur.execute("INSERT INTO pr_brands (brand_name, slug, category, status, source, created_at, "
                        "gifting_confirmed_at) VALUES (%s, %s, 'beauty', 'published', 'manual', %s, %s)",
                        (f"B{i}", f"b{i}", now, now))
        cls.conn.commit()

    @classmethod
    def tearDownClass(cls):
        cls.conn.close()
        boot = _connect()
        boot.autocommit = True
        boot.cursor().execute(f"DROP SCHEMA IF EXISTS {SCHEMA} CASCADE")
        boot.close()

    def test_hub_orders_by_signal_strength_and_skips_no_signal(self):
        hub = bu.ugc_hub(self.conn.cursor(cursor_factory=RealDictCursor), "skincare")
        self.assertEqual([b["slug"] for b in hub["brands"]], ["briefed", "creator-ads", "hot-new", "gifter"])
        self.assertEqual(hub["counts"], {"hot": 2, "creatorAds": 1, "gifting": 1, "briefs": 1})
        self.assertFalse(hub["indexable"])  # 4 brands < HUB_MIN_BRANDS
        self.assertIsNone(hub["brands"][-1]["seenOn"])

    def test_index_lists_only_categories_with_enough_brands(self):
        self.assertEqual(bu.ugc_hub_index(self.conn.cursor(cursor_factory=RealDictCursor)),
                         [{"category": "beauty", "total": 10}])


if __name__ == "__main__":
    unittest.main()
