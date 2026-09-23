# -*- coding: utf-8 -*-
import os
import unittest
from unittest import mock

import requests

from services.tiktok_ugc_profile_scraper import (
    default_search_queries,
    detect_location,
    detect_niche,
    extract_handles_from_ddg_html,
    extract_handles_from_serpapi,
    extract_mentioned_handles,
    extract_tiktok_handles,
    has_ugc_signal,
    qualify_profile,
    tiktok_profile_url,
    unwrap_ddg_url,
)

HANNAH_BIO = (
    "✨ Wifey | Dog mom | hairstylist | UGC creator ✨\n"
    "Midwest girlie 📍 Illinois\n"
    "💌 hannahlindooinquiries@gmail.com"
)


class TestTikTokUgcQualifier(unittest.TestCase):
    def test_hannah_shape_qualifies(self):
        flags = qualify_profile(
            handle="hannahs.ugccorner",
            nickname="Hannah | UGC creator",
            signature=HANNAH_BIO,
            bio_link="https://beacons.ai/hannahlindoougc",
            followers=4195,
        )
        self.assertTrue(flags["has_ugc"])
        self.assertEqual(flags["niche"], "hairstylist")
        self.assertEqual(flags["location"], "Illinois")
        self.assertEqual(flags["contact_email"], "hannahlindooinquiries@gmail.com")
        self.assertTrue(flags["qualified"])

    def test_ugc_niche_email_qualifies_without_location(self):
        flags = qualify_profile(
            handle="createwithvic",
            nickname="Victoria | UGC Creator",
            signature="UGC Creator\nBeauty | Lifestyle\ncontentwithvictoria@gmail.com",
            followers=1200,
        )
        self.assertTrue(flags["qualified"])
        self.assertEqual(flags["niche"], "beauty")
        self.assertEqual(flags["contact_email"], "contentwithvictoria@gmail.com")
        self.assertIsNone(flags["location"])

    def test_below_1k_followers_fails(self):
        flags = qualify_profile(
            handle="createwithvic",
            nickname="Victoria | UGC Creator",
            signature="UGC Creator\nBeauty | Lifestyle\ncontentwithvictoria@gmail.com",
            followers=839,
        )
        self.assertTrue(flags["has_ugc"])
        self.assertEqual(flags["niche"], "beauty")
        self.assertEqual(flags["contact_email"], "contentwithvictoria@gmail.com")
        self.assertFalse(flags["has_min_followers"])
        self.assertFalse(flags["qualified"])

    def test_placeholder_email_fails(self):
        flags = qualify_profile(
            handle="fakeugc",
            nickname="Sam | UGC creator",
            signature="UGC creator beauty example@example.com",
            followers=5000,
        )
        self.assertTrue(flags["has_ugc"])
        self.assertIsNone(flags["contact_email"])
        self.assertFalse(flags["qualified"])

    def test_missing_email_fails(self):
        flags = qualify_profile(
            handle="hannahs.ugccorner",
            nickname="Hannah | UGC creator",
            signature="hairstylist 📍 Illinois",
        )
        self.assertFalse(flags["qualified"])
        self.assertIsNone(flags["contact_email"])

    def test_missing_niche_fails(self):
        flags = qualify_profile(
            handle="justugc",
            nickname="Sam | UGC creator",
            signature="UGC creator 📍 Illinois  a@b.com",
        )
        self.assertTrue(flags["has_ugc"])
        self.assertIsNone(flags["niche"])
        self.assertFalse(flags["qualified"])

    def test_missing_ugc_fails(self):
        flags = qualify_profile(
            handle="somehairgirl",
            nickname="Hannah hair",
            signature="hairstylist 📍 Illinois  a@b.com",
        )
        self.assertFalse(flags["has_ugc"])
        self.assertFalse(flags["qualified"])

    def test_tiktok_profile_url(self):
        self.assertEqual(
            tiktok_profile_url("hannahs.ugccorner"),
            "https://www.tiktok.com/@hannahs.ugccorner",
        )
        self.assertEqual(
            tiktok_profile_url("@Hannahs.UgcCorner"),
            "https://www.tiktok.com/@hannahs.ugccorner",
        )
        self.assertEqual(tiktok_profile_url(""), "")
        self.assertEqual(
            extract_tiktok_handles("https://www.tiktok.com/@hannahs.ugccorner"),
            ["hannahs.ugccorner"],
        )
        self.assertEqual(
            extract_tiktok_handles("https://www.tiktok.com/@api https://www.tiktok.com/@discover"),
            [],
        )

    def test_ddg_unwrap_and_handles(self):
        url = unwrap_ddg_url(
            "https://duckduckgo.com/l/?uddg=https%3A%2F%2Fwww.tiktok.com%2F%40hannahs.ugccorner"
        )
        self.assertIn("tiktok.com/@hannahs.ugccorner", url)
        html = '<a class="result__a" href="https://duckduckgo.com/l/?uddg=https%3A%2F%2Fwww.tiktok.com%2F%40hannahs.ugccorner">x</a>'
        self.assertEqual(extract_handles_from_ddg_html(html), ["hannahs.ugccorner"])

    def test_inhouse_tiktok_html_handles(self):
        html = (
            '<script id="SIGI_STATE">{"UserModule":{"users":{'
            '"hannahs.ugccorner":{"uniqueId":"hannahs.ugccorner"}'
            '}}}</script>'
            '<a href="https://www.tiktok.com/@createwithvic">x</a>'
        )
        from services.tiktok_ugc_profile_scraper import extract_handles_from_tiktok_html
        self.assertEqual(
            set(extract_handles_from_tiktok_html(html)),
            {"hannahs.ugccorner", "createwithvic"},
        )

    def test_nested_unique_id_from_search_json(self):
        html = (
            '<script id="__UNIVERSAL_DATA_FOR_REHYDRATION__">'
            '{"__DEFAULT_SCOPE__":{"webapp.search-user":{"user_list":['
            '{"user_info":{"unique_id":"hannahs.ugccorner"}},'
            '{"user_info":{"uniqueId":"createdby_charli"}}'
            ']}}}</script>'
        )
        from services.tiktok_ugc_profile_scraper import extract_handles_from_tiktok_html
        self.assertEqual(
            set(extract_handles_from_tiktok_html(html)),
            {"hannahs.ugccorner", "createdby_charli"},
        )

    def test_escaped_slash_profile_url(self):
        from services.tiktok_ugc_profile_scraper import extract_handles_from_tiktok_html
        html = r'{"share_url":"https:\/\/www.tiktok.com\/@ugcbyallana"}'
        self.assertIn("ugcbyallana", extract_handles_from_tiktok_html(html))

    def test_chrome_handles_skipped(self):
        from services.tiktok_ugc_profile_scraper import extract_handles_from_tiktok_html
        html = '<a href="https://www.tiktok.com/@tiktok">x</a><a href="https://www.tiktok.com/@discover">y</a>'
        self.assertEqual(extract_handles_from_tiktok_html(html), [])

    def test_serpapi_off_by_default(self):
        from services.tiktok_ugc_profile_scraper import _use_serpapi
        env = {k: v for k, v in os.environ.items() if k != "UGC_USE_SERPAPI"}
        env["SERPAPI_API_KEY"] = "x"
        with mock.patch.dict(os.environ, env, clear=True):
            self.assertFalse(_use_serpapi())
        env["UGC_USE_SERPAPI"] = "1"
        with mock.patch.dict(os.environ, env, clear=True):
            self.assertTrue(_use_serpapi())

    def test_serpapi_429_stops_and_keeps_inhouse_seeds(self):
        from services import tiktok_ugc_profile_scraper as mod

        def boom(*_a, **_k):
            raise requests.HTTPError("Too Many Requests")

        with mock.patch.object(mod, "discover_handles_from_tiktok", return_value=["seed.one"]):
            with mock.patch.object(mod, "_serpapi_search", side_effect=boom):
                with mock.patch.object(mod, "discover_handles_html", return_value=[]):
                    handles = mod.discover_handles(
                        ['site:tiktok.com "UGC"'],
                        max_handles=80,
                        serp_pages=1,
                        use_serpapi=True,
                    )
        self.assertIn("seed.one", handles)
        data = {
            "organic_results": [
                {
                    "link": "https://www.tiktok.com/@hannahs.ugccorner",
                    "title": "Hannah | UGC creator",
                    "snippet": "hairstylist Illinois",
                }
            ]
        }
        self.assertEqual(extract_handles_from_serpapi(data), ["hannahs.ugccorner"])

    def test_mentioned_handles_not_emails(self):
        text = "collab @hannahs.ugccorner  email hello@gmail.com"
        self.assertEqual(extract_mentioned_handles(text), ["hannahs.ugccorner"])

    def test_volume_query_set(self):
        queries = default_search_queries()
        self.assertGreaterEqual(len(queries), 40)
        self.assertTrue(any("skincare" in q for q in queries))
        self.assertTrue(any("gmail" in q for q in queries))

    def test_niche_and_location(self):
        self.assertEqual(detect_niche(HANNAH_BIO), "hairstylist")
        self.assertEqual(detect_location(HANNAH_BIO), "Illinois")
        self.assertTrue(has_ugc_signal("UGC creator"))


if __name__ == "__main__":
    unittest.main()
