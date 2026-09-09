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

    def test_default_html_is_match_invite_not_unpaid_brief(self):
        html = personalize(default_onboarding_html(), {
            "handle": "hannahs.ugccorner",
            "niche": "hairstylist",
        })
        self.assertIn(SIGNUP_URL, html)
        self.assertIn("@hannahs.ugccorner", html)
        self.assertIn("PR / gifting campaigns", html)
        self.assertIn("good match", html)
        self.assertIn("If you're interested", html)
        self.assertIn("Worth a look?", html)
        self.assertIn("Mazza", html)
        self.assertNotIn("1 organic", html.lower())
        self.assertNotIn("forever", html.lower())
        self.assertNotIn("perpetual", html.lower())
        self.assertNotIn("6 months", html.lower())


if __name__ == "__main__":
    unittest.main()
