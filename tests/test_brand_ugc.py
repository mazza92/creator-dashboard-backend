"""UGC block on public brand pages: real signals only."""

import os
import unittest
from datetime import date, datetime, timedelta

import psycopg2
from psycopg2.extras import RealDictCursor

from services.brand_ugc import _first_line, brand_ugc

SCHEMA = "brand_ugc_t"


def _connect(**kw):
    return psycopg2.connect(host=os.getenv("DB_HOST"), port=os.getenv("DB_PORT"), dbname=os.getenv("DB_NAME"),
                            user=os.getenv("DB_USER"), password=os.getenv("DB_PASSWORD") or None, **kw)


class FirstLine(unittest.TestCase):
    def test_cuts_at_first_sentence_and_strips_markers(self):
        self.assertEqual(_first_line("I tried this serum for 30 days. Then my skin changed. [OUTREACH_SENT 2026-09-08]"),
                         "I tried this serum for 30 days.")


    def test_drops_brand_name_and_sponsored_label(self):
        self.assertEqual(_first_line("Solent Beard Co Sponsored I used to hate my own beard. Not anymore.",
                                     brand_name="Solent Beard Co"), "I used to hate my own beard.")
        self.assertEqual(_first_line("Sponsorisé Mon rituel du matin a changé grâce à ce sérum."),
                         "Mon rituel du matin a changé grâce à ce sérum.")


class BrandUgc(unittest.TestCase):
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
            CREATE TABLE pr_brands (id serial PRIMARY KEY, brand_name text, source text, created_at timestamp);
            CREATE TABLE brand_ad_signals (brand_id int, ads_last_seen date, ugc_style boolean, ad_samples jsonb);
            CREATE TABLE opportunities (id serial PRIMARY KEY, pr_brand_id int, product_name text, status text,
                listing_brief jsonb, published_at timestamp, closes_at timestamp);
        """)
        now = datetime.utcnow()
        cur.execute("INSERT INTO pr_brands (brand_name, source, created_at) VALUES ('GlowCo', 'meta_ads_library', %s),"
                    " ('OldPromo', 'meta_ads_library', %s), ('Plain', 'manual', %s)",
                    (now, now - timedelta(days=90), now))
        cur.execute("""INSERT INTO brand_ad_signals VALUES
            (1, NULL, TRUE, '["I tried this serum for 30 days. Wow.", "My skin was a mess until this cleanser."]'),
            (2, NULL, FALSE, '["Buy 1 Get 1 Free! Shop now"]')""")
        cur.execute("""INSERT INTO opportunities (pr_brand_id, product_name, status, listing_brief, published_at) VALUES
            (3, 'Plain Creator Campaign', 'live', '{"pay": "$300"}', %s),
            (3, 'Plain CPM push (', 'live', '{"pay": "$1"}', %s),
            (3, 'Closed one', 'paused', '{"pay": "$500"}', %s)""", (now, now, now))
        cls.conn.commit()

    @classmethod
    def tearDownClass(cls):
        cls.conn.close()
        boot = _connect()
        boot.autocommit = True
        boot.cursor().execute(f"DROP SCHEMA IF EXISTS {SCHEMA} CASCADE")
        boot.close()

    def cur(self):
        return self.conn.cursor(cursor_factory=RealDictCursor)

    def test_fresh_creator_style_brand(self):
        u = brand_ugc(self.cur(), {"id": 1, "source": "meta_ads_library"})
        self.assertTrue(u["hot"] and u["creatorStyleAds"])
        self.assertEqual(u["seenRunningAdsOn"], date.today().isoformat())
        self.assertEqual(u["adExamples"], ["I tried this serum for 30 days.", "My skin was a mess until this cleanser."])

    def test_old_promo_only_brand_shows_no_examples_and_no_fire(self):
        u = brand_ugc(self.cur(), {"id": 2, "source": "meta_ads_library"})
        self.assertFalse(u["hot"])
        self.assertIsNotNone(u["seenRunningAdsOn"])
        self.assertEqual(u["adExamples"], [])

    def test_live_briefs_only_open_ones_and_real_pay(self):
        u = brand_ugc(self.cur(), {"id": 3, "source": "manual"})
        self.assertIsNone(u["seenRunningAdsOn"])
        self.assertEqual(u["liveBriefs"], [{"title": "Plain Creator Campaign", "pay": "$300"},
                                           {"title": "Plain CPM push", "pay": None}])


if __name__ == "__main__":
    unittest.main()
