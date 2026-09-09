# -*- coding: utf-8 -*-
import unittest

from routes.ugc_supply import (
    SIGNUP_URL,
    _json_ready,
    default_onboarding_html,
    personalize,
)


class TestTikTokUgcLeadsApiHelpers(unittest.TestCase):
    def test_personalize_tokens(self):
        lead = {
            "display_name": "Hannah | UGC creator",
            "handle": "hannahs.ugccorner",
            "niche": "hairstylist",
            "contact_email": "hannahlindooinquiries@gmail.com",
            "followers": 4195,
        }
        text = personalize("Hi {{display_name}} (@{{handle}}) {{niche}} {{signup_url}} {{profile_url}}", lead)
        self.assertIn("Hannah | UGC creator", text)
        self.assertIn("hannahs.ugccorner", text)
        self.assertIn("hairstylist", text)
        self.assertIn(SIGNUP_URL, text)
        self.assertIn("https://www.tiktok.com/@hannahs.ugccorner", text)

    def test_json_ready_fills_profile_url(self):
        row = _json_ready({"handle": "hannahs.ugccorner", "profile_url": None})
        self.assertEqual(row["profile_url"], "https://www.tiktok.com/@hannahs.ugccorner")

    def test_default_html_has_signup_and_no_perpetual_copyright(self):
        html = default_onboarding_html({
            "display_name": "Hannah | UGC creator",
            "handle": "hannahs.ugccorner",
            "niche": "hairstylist",
        })
        self.assertIn(SIGNUP_URL, html)
        self.assertIn("hairstylist", html)
        self.assertNotIn("forever", html.lower())
        self.assertIn("6 months", html.lower())


if __name__ == "__main__":
    unittest.main()
