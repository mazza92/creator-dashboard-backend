# -*- coding: utf-8 -*-
import unittest

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

    def test_handle_extract(self):
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

    def test_serpapi_organic_handles(self):
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
