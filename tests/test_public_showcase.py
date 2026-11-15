"""Public showcase teasers for the newcollab.co feature pages."""

import os
import unittest
from datetime import date, datetime, timedelta

import psycopg2

from services import public_showcase as sc

SCHEMA = "public_showcase_t"


def _conn():
    return psycopg2.connect(host=os.getenv("DB_HOST"), port=os.getenv("DB_PORT"), dbname=os.getenv("DB_NAME"),
                            user=os.getenv("DB_USER"), password=os.getenv("DB_PASSWORD") or None,
                            options=f"-c search_path={SCHEMA}")


class CleanText(unittest.TestCase):
    def test_drops_source_names_and_trims(self):
        self.assertEqual(sc._clean("SideShift indicates $10/mo"), "indicates $10/mo")
        self.assertTrue(sc._clean("word " * 100, 20).endswith("…"))


class Tidy(unittest.TestCase):
    def test_titles_quotes_pay_and_names(self):
        self.assertEqual(sc._tidy_title("Weight Loss UGC | Hearty ($500"), "Weight Loss UGC | Hearty")
        self.assertEqual(sc._tidy_title("Vembit Creators · USA · Reels,"), "Vembit Creators · USA · Reels")
        self.assertEqual(sc._unquote("“The protein bar that tastes like dessert.”"), "The protein bar that tastes like dessert.")
        self.assertEqual(sc._pay_value("$1,200/mo"), 1200.0)
        self.assertEqual(sc._pay_value("$0.75"), 0.75)
        self.assertTrue(sc._named_like_a_brand("Ruby Hammer", "ruby-hammer"))
        self.assertFalse(sc._named_like_a_brand("Chosensupps", "chosensupps"))


class Showcase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            boot = psycopg2.connect(host=os.getenv("DB_HOST"), port=os.getenv("DB_PORT"), dbname=os.getenv("DB_NAME"),
                                    user=os.getenv("DB_USER"), password=os.getenv("DB_PASSWORD") or None)
        except Exception as e:  # pragma: no cover
            raise unittest.SkipTest(f"no database: {e}")
        boot.autocommit = True
        boot.cursor().execute(f"DROP SCHEMA IF EXISTS {SCHEMA} CASCADE; CREATE SCHEMA {SCHEMA}")
        boot.close()
        cls.conn = _conn()
        cur = cls.conn.cursor()
        cur.execute("""
            CREATE TABLE opportunities (id serial PRIMARY KEY, brand_name text, product_name text, status text,
                content_types text[], shipping_regions text[], published_at timestamp, closes_at timestamp,
                listing_brief jsonb, brand_email text);
            CREATE TABLE ugc_pulse_items (id serial PRIMARY KEY, day date, niche text, kind text, pattern text,
                text text, why text, score int);
            CREATE TABLE pr_brands (id serial PRIMARY KEY, brand_name text, slug text, category text, logo_url text,
                source text, status text, contact_email text);
            CREATE TABLE brand_ad_signals (brand_id int, ads_last_seen date, ad_library_url text);
        """)
        now = datetime.utcnow()
        cur.execute("""INSERT INTO opportunities (brand_name, product_name, status, content_types, shipping_regions,
                       published_at, listing_brief, brand_email) VALUES
            ('Clyper', 'Clyper Creator Campaign', 'live', '{TikTok,Reel}', '{US,UK}', %s, '{"pay": "$600"}', 'x@c.com'),
            ('Unknown brand', 'Mystery gig', 'live', '{}', '{}', %s, '{"pay": "$50"}', NULL),
            ('Brain Rush', 'Installs push', 'live', '{}', '{US}', %s, '{"pay": "$0.75"}', NULL),
            ('Old Co', 'Closed one', 'paused', '{}', '{}', %s, '{"pay": "$5"}', NULL)""",
                    (now, now, now - timedelta(minutes=1), now))
        today = date.today()
        cur.execute("""INSERT INTO ugc_pulse_items (day, niche, kind, pattern, text, score) VALUES
            (%s, 'skincare', 'hook', 'Problem first', 'My skin was a mess until this', 90),
            (%s, 'skincare', 'hook', 'Older', 'old hook', 99),
            (%s, 'fitness', 'hook', 'Proof', 'I tried it for 30 days', 70),
            (%s, 'fitness', 'format', 'x', 'not a hook', 100)""",
                    (today, today - timedelta(days=1), today, today))
        cur.execute("""INSERT INTO pr_brands (brand_name, slug, category, source, status, contact_email) VALUES
            ('GlowCo', 'glowco', 'Skincare', 'meta_ads_library', 'published', 'pr@glowco.com'),
            ('Draft Co', 'draftco', 'skincare', 'meta_ads_library', 'draft', NULL),
            ('Plain Co', 'plainco', 'fashion', 'manual', 'published', NULL)""")
        cur.execute("INSERT INTO brand_ad_signals (brand_id, ads_last_seen, ad_library_url) VALUES (1, %s, 'https://x')",
                    (today,))
        cls.conn.commit()

    @classmethod
    def tearDownClass(cls):
        cls.conn.close()
        boot = psycopg2.connect(host=os.getenv("DB_HOST"), port=os.getenv("DB_PORT"), dbname=os.getenv("DB_NAME"),
                                user=os.getenv("DB_USER"), password=os.getenv("DB_PASSWORD") or None)
        boot.autocommit = True
        boot.cursor().execute(f"DROP SCHEMA IF EXISTS {SCHEMA} CASCADE")
        boot.close()

    def test_showcase_is_safe_and_fresh(self):
        data = sc.showcase(self.conn)
        # unknown brand + paused skipped; a real fee leads, a CPM figure is not shown as pay
        self.assertEqual([g["brand"] for g in data["gigs"]], ["Clyper", "Brain Rush"])
        self.assertEqual(data["gigs"][0]["pay"], "$600")
        self.assertIsNone(data["gigs"][1]["pay"])
        self.assertNotIn("brand_email", data["gigs"][0])
        hooks = {h["niche"]: h["hook"] for h in data["hooks"]}
        self.assertEqual(hooks, {"skincare": "My skin was a mess until this", "fitness": "I tried it for 30 days"})
        self.assertEqual([b["slug"] for b in data["hot_brands"]], ["glowco"])
        self.assertNotIn("ad_library_url", data["hot_brands"][0])
        self.assertEqual(data["counts"], {"live_gigs": 3, "hot_brands": 1, "brands": 2})


if __name__ == "__main__":
    unittest.main()
