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
    "POV: you finally found a moisturizer that doesn't pill under makeup.",
    "If your skin gets tight after cleansing, watch this before you buy another cleanser.",
    "My skin after one week of this toner, honestly shocked.",
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
            CREATE TABLE tiktok_videos (tiktok_id text PRIMARY KEY, url text, caption text, hashtags text[],
                views int, creator_handle text, creator_followers int, language text, is_ad boolean,
                created_at timestamptz, fetched_at timestamptz);
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
        cur.execute("DROP TABLE IF EXISTS creator_pulse_saves, ugc_pulse_items, pulse_video_notes; "
                    "TRUNCATE pr_brands, brand_ad_signals, creators, tiktok_videos")
        from services import pulse_sources
        pulse_sources._READY = False
        for i, t in enumerate(ADS):  # same lines, now as real creator posts (lift falls with i, so index = i)
            cur.execute("INSERT INTO tiktok_videos VALUES (%s, %s, %s, %s, 500000, %s, %s, 'en', FALSE, %s, %s)",
                        (f"v{i}", f"https://www.tiktok.com/@c{i}/video/{i}", t + " #skincare", ["skincare"],
                         f"c{i}", 2000 + 1000 * i, DAY, DAY))
        cur.execute("INSERT INTO tiktok_videos VALUES ('ad', 'https://x', 'Brand ad #skincare serum', %s, 900000, "
                    "'brand', 1, 'en', TRUE, %s, %s)", (["skincare"], DAY, DAY))
        for i, t in enumerate(ADS):
            cur.execute("INSERT INTO pr_brands VALUES (%s, %s, NULL, 'skincare', 'meta_ads_library', 'published', NOW())",
                        (10 + i, f"B{i}"))
            cur.execute("INSERT INTO brand_ad_signals VALUES (%s, %s, %s, TRUE, %s)",
                        (10 + i, json.dumps([t]), f"https://ads/{i}", DAY))
        cur.execute("INSERT INTO creators VALUES (1, 'skincare', '[\"skincare\"]', 'approved')")
        self.conn.commit()

    def tearDown(self):
        self.conn.close()

    def test_watched_short_becomes_a_hook_with_its_opening_shot(self):
        from types import SimpleNamespace
        from services import pulse_sources as ps
        conn = self.conn
        if True:
            ps._READY = False
            notes = {"is_ugc": True, "spoken_hook": "I tested this for 7 days so you don't have to",
                     "on_screen_text": "", "opening_shot": "Bare face, product held to camera", "pattern": "Results",
                     "why": "A time-boxed test promises a verdict."}
            n = ps.refresh_youtube(conn, "skincare", "skincare", list(up.PATTERNS),
                                   http=lambda url, **kw: SimpleNamespace(status_code=200, text=YT_HTML),
                                   gemini=lambda url: notes)
            self.assertEqual(n, 1)
            items = ps.youtube_hook_items(conn, "skincare", date.today(), list(up.PATTERNS))
            self.assertEqual(items[0]["text"], "“I tested this for 7 days so you don't have to”")
            self.assertIn("Opening shot: Bare face", items[0]["why"])
            self.assertEqual(items[0]["sources"][0]["url"], "https://www.youtube.com/shorts/abcdefghijk")
            # cached: a second run watches nothing new
            self.assertEqual(ps.refresh_youtube(conn, "skincare", "skincare", list(up.PATTERNS),
                                                http=lambda url, **kw: SimpleNamespace(status_code=200, text=YT_HTML),
                                                gemini=lambda url: notes), 0)

    def test_build_read_save_and_ingest(self):
        out = up.build_day(self.conn, day=DAY, llm=fake_llm, http=no_http)
        self.assertEqual(out["built"], {"skincare": 6})
        self.assertEqual(up.build_day(self.conn, day=DAY, llm=fake_llm, http=no_http)["skipped"], {"skincare": "done"})

        free = up.day_view(self.conn, "skincare", creator_id=1, is_pro=False, day=DAY)
        self.assertIn("calmed down", free["hero"]["text"])
        self.assertEqual(free["hero"]["source_type"], "creators")
        self.assertTrue(free["hero"]["sources"][0]["url"].startswith("https://www.tiktok.com/@c0"))
        self.assertIn("views", free["hero"]["sources"][0]["label"])
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


def no_http(url, **kw):
    from types import SimpleNamespace
    return SimpleNamespace(status_code=503, text="")


YT_HTML = ('<html><script>var ytInitialData = {"contents": {"a": [{"reelItemRenderer": {"videoId": "abcdefghijk", '
           '"headline": {"simpleText": "my honest skincare review"}, "viewCountText": {"simpleText": "1.2M views"}}}, '
           '{"videoRenderer": {"videoId": "longvideo01", "title": {"simpleText": "10 min routine"}, '
           '"lengthText": {"simpleText": "10:02"}, "viewCountText": {"simpleText": "2M views"}}}]}};</script></html>')


class YouTubeShorts(unittest.TestCase):
    def test_creator_samples_skip_ads_resellers_and_profanity(self):
        from services import pulse_sources as ps
        pool = [
            {"url": "u1", "caption": "I bought pimple patches to fix my acne, so why does this pimple look worse? #skincare",
             "hashtags": ["skincare"], "views": 7900000, "creator_handle": "a", "creator_followers": 1936},
            {"url": "u2", "caption": "Wholesale sheet masks, DM to order #skincare", "hashtags": ["skincare"],
             "views": 150000, "creator_handle": "b", "creator_followers": 1900},
            {"url": "u3", "caption": "Don't fucking touch my serum routine #skincare", "hashtags": ["skincare"],
             "views": 900000, "creator_handle": "c", "creator_followers": 1600},
            {"url": "u4", "caption": "Best leggings for squats #gym", "hashtags": ["gym"], "views": 900000,
             "creator_handle": "d", "creator_followers": 1600},
            {"url": "u5", "caption": "Transform your style with our versatile bracelet! Shop now #skincare",
             "hashtags": ["skincare"], "views": 26000000, "creator_handle": "e", "creator_followers": 1000},
        ]
        got = ps.niche_video_samples(pool, "skincare")
        self.assertEqual([s["handle"] for s in got], ["a"])
        self.assertTrue(got[0]["text"].startswith("I bought pimple patches"))

    def test_search_keeps_shorts_only(self):
        from types import SimpleNamespace
        from services import pulse_sources as ps
        got = ps.search_shorts("skincare ugc", http=lambda url, **kw: SimpleNamespace(status_code=200, text=YT_HTML))
        self.assertEqual([v["video_id"] for v in got], ["abcdefghijk"])
        self.assertEqual(got[0]["views"], 1200000)

def self_conn():
    return psycopg2.connect(host=os.getenv("DB_HOST"), port=os.getenv("DB_PORT"), dbname=os.getenv("DB_NAME"),
                            user=os.getenv("DB_USER"), password=os.getenv("DB_PASSWORD") or None,
                            options=f"-c search_path={SCHEMA}")


if __name__ == "__main__":
    unittest.main()
