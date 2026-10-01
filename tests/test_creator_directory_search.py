import json
import unittest

from services.creator_directory_search import (
    build_response,
    clean_country,
    clean_limit,
    clean_platform,
    follower_band,
    niche_score,
    rate_limited,
    search_creators,
    serialize,
)


def _row(cid, niches, kit=None, platform="tiktok", country="US", er=0.042, followers=4200, pro=False):
    return {
        "id": cid,
        "username": f"user{cid}",
        "social_platform": platform,
        "niche": json.dumps(niches),
        "creator_niches": [],
        "kit_slug": kit or f"user{cid}",
        "kit_niches": None,
        "kit_published": bool(kit),
        "followers": followers,
        "engagement": er,
        "total_views": 49000,
        "total_posts": 2,
        "country_raw": country,
        "gifted_collabs": 3,
        "is_pro": pro,
    }


class TestCleaning(unittest.TestCase):
    def test_limits_and_platform(self):
        self.assertEqual(clean_limit(None), 5)
        self.assertEqual(clean_limit(99), 8)
        self.assertEqual(clean_limit(0), 1)
        self.assertEqual(clean_limit(99, 24), 24)
        self.assertEqual(clean_platform("Instagram"), "instagram")
        self.assertEqual(clean_platform("snapchat"), "tiktok")

    def test_country(self):
        self.assertEqual(clean_country("uk"), "GB")
        self.assertEqual(clean_country("fr"), "FR")
        self.assertIsNone(clean_country(""))

    def test_follower_band(self):
        self.assertEqual(follower_band(4200), "1K-5K")
        self.assertEqual(follower_band(0), "Under 1K")
        self.assertEqual(follower_band(250000), "100K+")


class TestNiche(unittest.TestCase):
    def test_direct_beats_lane(self):
        self.assertEqual(niche_score("skincare", ["skincare", "fashion"]), 2)
        self.assertEqual(niche_score("skincare", ["makeup"]), 1)
        self.assertEqual(niche_score("skincare", ["gaming"]), 0)

    def test_word_level_matches(self):
        self.assertEqual(niche_score("pet products", ["pet"]), 2)
        self.assertEqual(niche_score("skin care", ["skincare"]), 2)
        self.assertEqual(niche_score("supplements", ["food & nutrition", "supplement"]), 2)
        self.assertEqual(niche_score("tech accessories", ["gaming"]), 0)


class TestSerialize(unittest.TestCase):
    def test_public_kit_gets_handle_and_utm(self):
        card = serialize(_row(1, ["skincare"], kit="sarah_skinfix"), "skincare")
        self.assertEqual(card["handle"], "@sarah_skinfix")
        self.assertIn("/kit/sarah_skinfix?utm_source=chatgpt&utm_medium=plugin", card["preview_url"])
        self.assertEqual(card["engagement_rate"], "4.2%")
        self.assertEqual(card["avg_views"], "24,500")

    def test_private_creator_is_anonymous(self):
        card = serialize(_row(2, ["skincare"]), "skincare")
        self.assertIsNone(card["handle"])
        self.assertNotIn("preview_url", card)
        blob = json.dumps(card)
        for leak in ("user2", "@", "email", "phone", "address"):
            self.assertNotIn(leak, blob)

    def test_media_only_for_public_kits_on_landing(self):
        public = _row(1, ["skincare"], kit="sarah")
        public["thumbnails"] = ["https://x.supabase.co/a.jpg", "http://insecure.jpg"]
        public["worked_with"] = ["Glow Co", "glow co", "Rhode"]
        private = _row(2, ["skincare"])
        private["thumbnails"] = ["https://x.supabase.co/b.jpg"]
        self.assertNotIn("thumbnails", serialize(public, "skincare"))
        card = serialize(public, "skincare", media=True)
        self.assertEqual(card["thumbnails"], ["https://x.supabase.co/a.jpg"])
        self.assertEqual(card["worked_with"], ["Glow Co", "Rhode"])
        self.assertNotIn("thumbnails", serialize(private, "skincare", media=True))

    def test_primary_niche_ranks_first(self):
        side = _row(1, ["beauty", "pet"], kit="side", followers=90000)
        main = _row(2, ["pet", "lifestyle"], kit="main", followers=1900)
        out = build_response([side, main], "pet", "all", None, 5)
        self.assertEqual([c["handle"] for c in out["creators"]], ["@main", "@side"])

    def test_micro_reach_beats_nano_engagement(self):
        nano = _row(1, ["skincare"], kit="nano", followers=400, er=0.2)
        micro = _row(2, ["skincare"], kit="micro", followers=8000, er=0.03)
        out = build_response([nano, micro], "skincare", "all", None, 5)
        self.assertEqual(out["creators"][0]["handle"], "@micro")
        self.assertIn("preview_url", out["how_to_present"])

    def test_platform_falls_back_to_posts(self):
        row = _row(3, ["skincare"], platform=None)
        row["post_platform"] = "instagram"
        self.assertEqual(serialize(row, "skincare")["platform"], "Instagram")


class TestResponse(unittest.TestCase):
    def test_rank_filters_and_cta(self):
        rows = [
            _row(1, ["makeup"], kit="lanefit"),
            _row(2, ["skincare"]),
            _row(3, ["skincare"], kit="direct_kit"),
            _row(4, ["skincare"], platform="instagram"),
            _row(5, ["skincare"], country="FR"),
            _row(6, ["gaming"], kit="nope"),
        ]
        out = build_response(rows, "skincare", "tiktok", "US", 5)
        self.assertEqual(out["total_creators_found"], 3)
        self.assertEqual(out["creators"][0]["handle"], "@direct_kit")
        url = out["brand_cta"]["url"]
        self.assertIn("/brands/launch?niche=skincare", url)
        self.assertIn("utm_source=chatgpt", url)
        self.assertIn("utm_medium=plugin", url)
        self.assertIn("country=US", url)

    def test_missing_niche_raises(self):
        with self.assertRaises(ValueError):
            search_creators({"niche": "  "})


class _FakeRedis:
    def __init__(self):
        self.store = {}

    def incr(self, key):
        self.store[key] = self.store.get(key, 0) + 1
        return self.store[key]

    def expire(self, key, ttl):
        return True


class TestRateLimit(unittest.TestCase):
    def test_blocks_after_limit_and_fails_open(self):
        r = _FakeRedis()
        hits = [rate_limited(r, "1.2.3.4", limit=2) for _ in range(3)]
        self.assertEqual(hits, [False, False, True])
        self.assertFalse(rate_limited(None, "1.2.3.4"))


class TestMcp(unittest.TestCase):
    def setUp(self):
        try:
            from routes import integrations
        except Exception as exc:
            self.skipTest(f"flask unavailable: {exc}")
        self.mod = integrations

    def test_initialize_and_list(self):
        init = self.mod._handle_rpc({"jsonrpc": "2.0", "id": 1, "method": "initialize",
                                     "params": {"protocolVersion": "2025-03-26"}})
        self.assertEqual(init["result"]["protocolVersion"], "2025-03-26")
        self.assertIn("tools", init["result"]["capabilities"])
        tools = self.mod._handle_rpc({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
        tool = tools["result"]["tools"][0]
        self.assertEqual(tool["name"], "search_ugc_creators")
        self.assertEqual(tool["inputSchema"]["required"], ["niche"])
        self.assertTrue(tool["annotations"]["readOnlyHint"])

    def test_notification_has_no_reply(self):
        self.assertIsNone(self.mod._handle_rpc({"jsonrpc": "2.0", "method": "notifications/initialized"}))

    def test_unknown_method(self):
        out = self.mod._handle_rpc({"jsonrpc": "2.0", "id": 3, "method": "nope"})
        self.assertEqual(out["error"]["code"], -32601)


if __name__ == "__main__":
    unittest.main()
