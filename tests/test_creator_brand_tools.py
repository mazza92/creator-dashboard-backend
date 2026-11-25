"""Creator tools on the AI-assistant connectors: find brands to pitch, brand PR info."""

import json
import os
import unittest
from datetime import datetime, timedelta
from unittest.mock import patch

import psycopg2
from psycopg2.extras import RealDictCursor

from services import creator_brand_tools as t

SCHEMA = "creator_brand_tools_t"


class Helpers(unittest.TestCase):
    def test_niche_to_category(self):
        self.assertEqual(t.niche_category("Skincare"), "skincare")
        self.assertEqual(t.niche_category("makeup"), "beauty")
        self.assertEqual(t.niche_category("dog mom content"), "pet")
        self.assertEqual(t.niche_category("home decor"), "home")
        self.assertEqual(t.niche_category("mum life"), "baby")
        self.assertEqual(t.niche_category("postpartum fashion"), "fashion")
        self.assertIsNone(t.niche_category("crypto"))
        self.assertIsNone(t.niche_category(""))

    def test_shipping(self):
        self.assertTrue(t.ships_to([], "US"))
        self.assertTrue(t.ships_to({}, "US"))
        self.assertTrue(t.ships_to(["Worldwide"], "FR"))
        self.assertTrue(t.ships_to(["UK"], "GB"))
        self.assertFalse(t.ships_to(["US"], "AU"))
        self.assertTrue(t.ships_to(["US"], None))

    def test_limit_and_cta(self):
        self.assertEqual(t.clean_limit(99), 8)
        self.assertEqual(t.clean_limit(None), 5)
        cta = t.creator_cta("claude", "skincare")
        self.assertIn("/register/creator?", cta["url"])
        self.assertIn("utm_source=claude", cta["url"])
        self.assertIn("niche=skincare", cta["url"])


def _conn():
    return psycopg2.connect(host=os.getenv("DB_HOST"), port=os.getenv("DB_PORT"), dbname=os.getenv("DB_NAME"),
                            user=os.getenv("DB_USER"), password=os.getenv("DB_PASSWORD") or None,
                            options=f"-c search_path={SCHEMA}")


class Tools(unittest.TestCase):
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
            CREATE TABLE pr_brands (id serial PRIMARY KEY, brand_name text, slug text, category text, logo_url text,
                website text, description text, pr_description text, instagram_handle text, tiktok_handle text,
                regions jsonb, accepting_pr boolean, response_rate float, updated_at timestamp, gifting_confirmed_at timestamp, source text, status text,
                contact_email text, application_form_url text, created_at timestamp DEFAULT NOW());
            CREATE TABLE brand_ad_signals (brand_id int, ads_last_seen date, ugc_style boolean, ad_samples jsonb);
            CREATE TABLE opportunities (id serial PRIMARY KEY, pr_brand_id int, product_name text, status text,
                listing_brief jsonb, published_at timestamp, closes_at timestamp);
        """)
        now = datetime.utcnow()
        cur.execute("""INSERT INTO pr_brands (brand_name, slug, category, source, status, regions, accepting_pr,
                         gifting_confirmed_at, contact_email, instagram_handle, description, created_at) VALUES
            ('Glow Co', 'glow-co', 'skincare', 'meta_ads_library', 'published', '["US","Worldwide"]', true, NULL,
             'pr@glow.co', '@glowco', 'Clean skincare.', %s),
            ('Dew Labs', 'dew-labs', 'skincare', 'manual', 'published', '["US"]', true, %s, NULL, NULL, NULL, %s),
            ('Quiet Brand', 'quiet', 'skincare', 'manual', 'published', '{}', false, NULL, NULL, NULL, NULL, %s),
            ('Draft Skin', 'draft-skin', 'skincare', 'meta_ads_library', 'draft', '{}', true, NULL, NULL, NULL, NULL, %s),
            ('Brief Co', 'brief-co', 'skincare', 'manual', 'published', '["AU"]', true, NULL, NULL, NULL, NULL, %s)
        """, (now, now, now - timedelta(days=200), now, now, now))
        cur.execute("""INSERT INTO brand_ad_signals VALUES
            (1, %s, true, '["Glow Co Sponsored I tried this serum for 30 days and my skin changed.",
                           "\u200b \u200b \u2728 BLACK FRIDAY SALE! 40%% off everything this week"]')""",
                    (now.date(),))
        cur.execute("""INSERT INTO opportunities (pr_brand_id, product_name, status, listing_brief, published_at)
                       VALUES (5, 'Summer SPF UGC', 'live', '{"pay": "$150"}', %s)""", (now,))
        cls.conn.commit()

    @classmethod
    def tearDownClass(cls):
        cls.conn.close()
        boot = psycopg2.connect(host=os.getenv("DB_HOST"), port=os.getenv("DB_PORT"), dbname=os.getenv("DB_NAME"),
                                user=os.getenv("DB_USER"), password=os.getenv("DB_PASSWORD") or None)
        boot.autocommit = True
        boot.cursor().execute(f"DROP SCHEMA IF EXISTS {SCHEMA} CASCADE")
        boot.close()

    def cur(self):
        return self.conn.cursor(cursor_factory=RealDictCursor)

    def test_find_brands_ranked_and_filtered(self):
        out = t.find_brands(self.cur(), {"niche": "skin care", "limit": 8}, source="chatgpt")
        names = [b["name"] for b in out["brands"]]
        # Thanos orders them; drafts and brands not accepting PR never appear, and every brand says why
        self.assertEqual(set(names), {"Brief Co", "Glow Co", "Dew Labs"})
        self.assertNotIn("Quiet Brand", names)
        brief = next(b for b in out["brands"] if b["name"] == "Brief Co")
        self.assertIn("Has an open paid UGC brief on Newcollab", brief["why_now"])
        glow = next(b for b in out["brands"] if b["name"] == "Glow Co")
        self.assertTrue(glow["brand_page"].startswith("https://newcollab.co/brand/glow-co?"))
        self.assertNotIn("pr@glow.co", json.dumps(out))
        self.cur().execute("UPDATE pr_brands SET accepting_pr = true WHERE slug = 'quiet'")
        topped = t.find_brands(self.cur(), {"niche": "skincare", "limit": 8})
        quiet = next(b for b in topped["brands"] if b["name"] == "Quiet Brand")
        self.assertEqual(quiet["why_now"], ["Listed as open to creator collabs"])
        self.conn.rollback()
        us = t.find_brands(self.cur(), {"niche": "skincare", "country": "US"}, source="claude")
        self.assertEqual({b["name"] for b in us["brands"]}, {"Glow Co", "Dew Labs"})
        self.assertIn("utm_source=claude", us["creator_cta"]["url"])

    def test_unknown_niche_and_missing(self):
        out = t.find_brands(self.cur(), {"niche": "crypto"})
        self.assertEqual(out["brands"], [])
        self.assertIn("Categories", out["note"])
        with self.assertRaises(ValueError):
            t.find_brands(self.cur(), {})

    def test_brand_info(self):
        out = t.get_brand_ugc_info(self.cur(), {"brand": "glow co"})
        self.assertTrue(out["found"])
        self.assertEqual(out["socials"], {"instagram": "glowco"})
        self.assertTrue(out["ugc_signals"]["seen_running_ads_in_last_30_days"])
        self.assertEqual(out["ugc_signals"]["creator_ad_opening_lines"],
                         ["I tried this serum for 30 days and my skin changed."])
        self.assertTrue(out["contact"]["creator_contact_on_newcollab"])
        self.assertNotIn("pr@glow.co", json.dumps(out))
        self.assertTrue(t.get_brand_ugc_info(self.cur(), {"brand": "Dew"})["works_with_creators"]["confirmed_gifted_collabs"])
        self.assertEqual(t.get_brand_ugc_info(self.cur(), {"brand": "Brief Co"})["ugc_signals"]["open_paid_ugc_briefs"],
                         [{"title": "Summer SPF UGC", "pay": "$150"}])
        self.assertFalse(t.get_brand_ugc_info(self.cur(), {"brand": "Draft Skin"})["found"])
        self.assertFalse(t.get_brand_ugc_info(self.cur(), {"brand": "Nope Inc"})["found"])


class Mcp(unittest.TestCase):
    def setUp(self):
        from flask import Flask

        from routes import integrations as integ
        self.integ = integ
        app = Flask(__name__)
        app.register_blueprint(integ.integrations_bp)
        self.client = app.test_client()

    def rpc(self, path, method, params=None):
        r = self.client.post(path, json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params or {}})
        return r.get_json()

    def test_tools_listed_on_every_variant(self):
        for path in ("/api/v1/integrations/mcp", "/api/v1/integrations/claude/mcp", "/api/v1/integrations/gemini/mcp"):
            names = [x["name"] for x in self.rpc(path, "tools/list")["result"]["tools"]]
            self.assertEqual(names, ["search_ugc_creators", "find_brands_to_pitch", "get_brand_ugc_info"])
        claude = self.rpc("/api/v1/integrations/claude/mcp", "tools/list")["result"]["tools"]
        for tool in claude:
            self.assertNotIn("Link each", tool["description"])
            self.assertNotIn("end with", tool["description"].lower())

    def test_call_dispatch_and_logging(self):
        fake = {"brands": [{"name": "Glow Co"}], "creator_cta": {}}
        with patch.object(self.integ, "_run_creator_tool", return_value=fake) as run, \
                patch.object(self.integ, "log_tool_call") as log:
            out = self.rpc("/api/v1/integrations/claude/mcp", "tools/call",
                           {"name": "find_brands_to_pitch", "arguments": {"niche": "skincare"}})
        self.assertEqual(out["result"]["structuredContent"], fake)
        self.assertEqual(run.call_args[0][2], "claude")
        log.assert_called_once_with("claude", "find_brands_to_pitch", {"niche": "skincare"}, 1)
        with patch.object(self.integ, "_run_creator_tool", return_value={"found": True}) as run, \
                patch.object(self.integ, "log_tool_call"):
            legacy = self.rpc("/api/v1/integrations/mcp", "tools/call",
                              {"name": "get_brand_pr_info", "arguments": {"brand": "Glossier"}})
        self.assertFalse(legacy["result"]["isError"])
        self.assertEqual(run.call_args[0][0].__name__, "get_brand_ugc_info")
        for tool in self.rpc("/api/v1/integrations/claude/mcp", "tools/list")["result"]["tools"]:
            self.assertNotIn(" PR ", f" {tool['description']} ")
        bad = self.rpc("/api/v1/integrations/mcp", "tools/call", {"name": "nope", "arguments": {}})
        self.assertEqual(bad["error"]["code"], -32602)

    def test_value_error_is_tool_error(self):
        with patch.object(self.integ, "_run_creator_tool", side_effect=ValueError("brand is required")), \
                patch.object(self.integ, "log_tool_call"):
            out = self.rpc("/api/v1/integrations/mcp", "tools/call", {"name": "get_brand_ugc_info", "arguments": {}})
        self.assertTrue(out["result"]["isError"])


if __name__ == "__main__":
    unittest.main()
