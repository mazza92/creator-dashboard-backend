"""UGC Pulse: hooks grounded in real ads, free/Pro split, saves, Hermes ingest."""

import json
import os
import unittest
from datetime import date

import psycopg2

from services import ugc_pulse as up

SCHEMA = "ugc_pulse_t"
DAY = date(2026, 11, 13)

ADS = [
    "I stopped doing this one step and my skin finally calmed down. Meet the barrier serum.",
    "I stopped double cleansing and my skin calmed down in a week.",
    "POV: you finally found a moisturizer that doesn't pill under makeup. Shop now.",
    "If your skin gets tight after cleansing, watch this before you buy another cleanser.",
    "20% off this weekend only. Free shipping.",
    "Day 1 vs day 14 with the same lighting and no filter.",
    "Dermatologists don't want you to know this about retinol.",
]


def fake_llm(system, user, max_tokens=0):
    return {
        "hooks": [
            {"hook": "I stopped doing this one step and my skin finally calmed down.", "pattern": "Confession",
             "why": "A confession plus a removal stops the scroll.", "sources": [0, 1]},
            {"hook": "POV: you finally found a moisturizer that doesn't pill under makeup.", "pattern": "POV",
             "why": "Relatable frustration.", "sources": [2]},
            {"hook": "If your skin gets tight after cleansing, watch this.", "pattern": "Problem → fix",
             "why": "Names the problem.", "sources": [3]},
            {"hook": "Day 1 vs day 14. Same lighting, no filter.", "pattern": "Results", "why": "Proof.",
             "sources": [5]},
            {"hook": "This invented line about collagen gummies changes everything.", "pattern": "Myth",
             "why": "x", "sources": [6]},
        ],
        "trend": {"title": "Raw POV texture shots", "body": "Phone-close, natural light, no music first."},
        "tip": "Cut on the product touch, not after it.",
    }


class Pure(unittest.TestCase):
    def test_clean_ad(self):
        self.assertEqual(up.clean_ad("SAENSKIN.COM Plump Without Needles, the topical. Shop now"),
                         "Plump Without Needles, the topical.")
        self.assertEqual(up.clean_ad("Mud. Organics Sponsored Pigmentation. Dry skin."), "Pigmentation. Dry skin.")
        self.assertEqual(up.clean_ad("https://try-derm.com/pages/x"), "")

    def test_first_line(self):
        self.assertEqual(up.first_line("POV: it works. Buy now"), "POV: it works.")

    def test_invented_hooks_are_dropped(self):
        samples = [{"i": i, "brand_id": 10 + i, "brand_name": f"B{i}", "logo": None, "hot": i % 2 == 0,
                    "ad_url": None, "ugc": True, "text": t, "opener": up.first_line(t)} for i, t in enumerate(ADS)]
        items = up.build_items("skincare", DAY, samples, fake_llm("", ""))
        hooks = [i for i in items if i["kind"] == "hook"]
        texts = [h["text"] for h in hooks]
        self.assertEqual(len(hooks), 4)
        self.assertFalse(any("collagen" in t for t in texts))
        self.assertEqual(hooks[0]["score"], 2)
        self.assertEqual([s["brand_id"] for s in hooks[0]["sources"]], [10, 11])
        self.assertTrue(any(i["kind"] == "trend" for i in items))
        self.assertTrue(any(i["kind"] == "tip" for i in items))


class WithDB(unittest.TestCase):
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
            CREATE TABLE pr_brands (id int PRIMARY KEY, brand_name text, logo_url text, category text, source text,
                status text, created_at timestamptz DEFAULT NOW());
            CREATE TABLE brand_ad_signals (brand_id int PRIMARY KEY, ad_samples jsonb, ad_library_url text,
                ugc_style boolean, ads_last_seen date);
            CREATE TABLE creators (id int PRIMARY KEY, niche text, creator_niches jsonb, approval_status text);
        """)
        boot.close()

    @classmethod
    def tearDownClass(cls):
        c = self_conn()
        c.autocommit = True
        c.cursor().execute(f"DROP SCHEMA IF EXISTS {SCHEMA} CASCADE")
        c.close()

    def setUp(self):
        up._READY = False
        self.conn = self_conn()
        cur = self.conn.cursor()
        cur.execute("DROP TABLE IF EXISTS creator_pulse_saves, ugc_pulse_items; TRUNCATE pr_brands, brand_ad_signals, creators")
        for i, t in enumerate(ADS):
            cur.execute("INSERT INTO pr_brands VALUES (%s, %s, NULL, 'skincare', 'meta_ads_library', 'published', NOW())",
                        (10 + i, f"B{i}"))
            cur.execute("INSERT INTO brand_ad_signals VALUES (%s, %s, %s, TRUE, %s)",
                        (10 + i, json.dumps([t]), f"https://ads/{i}", DAY))
        cur.execute("INSERT INTO creators VALUES (1, 'skincare', '[\"skincare\"]', 'approved')")
        self.conn.commit()

    def tearDown(self):
        self.conn.close()

    def test_build_read_save_and_ingest(self):
        out = up.build_day(self.conn, day=DAY, llm=fake_llm)
        self.assertEqual(out["built"], {"skincare": 6})
        self.assertEqual(up.build_day(self.conn, day=DAY, llm=fake_llm)["skipped"], {"skincare": "done"})

        free = up.day_view(self.conn, "skincare", creator_id=1, is_pro=False, day=DAY)
        self.assertIn("calmed down", free["hero"]["text"])
        self.assertEqual(len(free["hooks"]), 2)
        self.assertEqual(free["locked_count"], 1)
        pro = up.day_view(self.conn, "skincare", creator_id=1, is_pro=True, day=DAY)
        self.assertEqual((len(pro["hooks"]), pro["locked_count"]), (3, 0))
        self.assertEqual(pro["trend"]["text"], "Raw POV texture shots")

        hero_id = pro["hero"]["id"]
        up.set_saved(self.conn, 1, hero_id, True)
        self.assertTrue(up.day_view(self.conn, "skincare", creator_id=1, is_pro=True, day=DAY)["hero"]["saved"])
        self.assertEqual([i["id"] for i in up.saved_view(self.conn, 1)["items"]], [hero_id])
        up.set_saved(self.conn, 1, hero_id, False)
        self.assertEqual(up.saved_view(self.conn, 1)["items"], [])

        res = up.ingest(self.conn, [
            {"niche": "all", "kind": "news", "text": "TikTok Shop now lets brands run creator videos as Spark Ads.",
             "sources": [{"url": "https://example.com/a", "label": "TikTok"}]},
            {"niche": "skincare", "kind": "nonsense", "text": "nope nope nope"},
            {"niche": "mars", "kind": "news", "text": "Off-world UGC is booming."},
        ], day=DAY)
        self.assertEqual(res, {"saved": 1, "rejected": 2})
        news = up.day_view(self.conn, "skincare", creator_id=1, is_pro=False, day=DAY)["news"]
        self.assertEqual(len(news), 1)


def self_conn():
    return psycopg2.connect(host=os.getenv("DB_HOST"), port=os.getenv("DB_PORT"), dbname=os.getenv("DB_NAME"),
                            user=os.getenv("DB_USER"), password=os.getenv("DB_PASSWORD") or None,
                            options=f"-c search_path={SCHEMA}")


if __name__ == "__main__":
    unittest.main()
