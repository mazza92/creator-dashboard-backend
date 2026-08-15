import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from services.onboarding_scrape_errors import onboarding_scrape_user_error


class TestOnboardingScrapeUserError(unittest.TestCase):
    def test_incomplete_latest_post(self):
        payload = onboarding_scrape_user_error(
            Exception(
                "Incomplete Instagram profile for @4lla.pam (missing latest_post). "
                "Public profiles need followers and a latest post."
            ),
            "4lla.pam",
            "instagram",
        )
        self.assertFalse(payload["success"])
        self.assertEqual(payload["error_code"], "incomplete")
        self.assertFalse(payload["is_private"])
        self.assertIn("4lla.pam", payload["error"])
        self.assertIn("instagram.com/4lla.pam", payload["profile_url"])
        self.assertTrue(any("public" in tip.lower() for tip in payload["tips"]))
        blob = " ".join([payload["error"], payload["message"]] + payload["tips"]).lower()
        self.assertNotIn("proxy", blob)
        self.assertNotIn("sessionid", blob)

    def test_private(self):
        payload = onboarding_scrape_user_error(
            ValueError("Account @hidden is private"), "hidden", "instagram"
        )
        self.assertEqual(payload["error_code"], "private")
        self.assertTrue(payload["is_private"])

    def test_not_found(self):
        payload = onboarding_scrape_user_error(
            Exception("Instagram account @nope not found"), "nope", "instagram"
        )
        self.assertEqual(payload["error_code"], "not_found")

    def test_rate_limit_does_not_leak_ops(self):
        payload = onboarding_scrape_user_error(
            Exception(
                "Instagram is rate-limiting this server (HTTP 429). "
                "Set IG_PROXY or INSTAGRAM_SESSIONID."
            ),
            "foo",
            "instagram",
        )
        self.assertEqual(payload["error_code"], "unavailable")
        blob = " ".join([payload["error"], payload["message"]] + payload["tips"])
        self.assertNotIn("IG_PROXY", blob)
        self.assertNotIn("SESSIONID", blob)

    def test_youtube_url_and_subscribers(self):
        from services.profile_quality import ProfileQualityError

        payload = onboarding_scrape_user_error(
            ProfileQualityError("below_follower_min", "mayaglow", follower_count=20),
            "mayaglow",
            "youtube",
        )
        self.assertIn("youtube.com/@mayaglow", payload["profile_url"])
        self.assertIn("subscribers", payload["error"])
        self.assertIn("20", payload["message"])

    def test_tiktok_post_min_says_videos(self):
        from services.profile_quality import ProfileQualityError

        payload = onboarding_scrape_user_error(
            ProfileQualityError("below_post_min", "glowtok", follower_count=800, post_count=4),
            "glowtok",
            "tiktok",
        )
        self.assertIn("videos", payload["error"])
        self.assertIn("tiktok.com/@glowtok", payload["profile_url"])

    def test_below_follower_min(self):
        from services.profile_quality import ProfileQualityError

        payload = onboarding_scrape_user_error(
            ProfileQualityError("below_follower_min", "4lla.pam", follower_count=20),
            "4lla.pam",
            "instagram",
        )
        self.assertEqual(payload["error_code"], "below_follower_min")
        self.assertEqual(payload["follower_count"], 20)
        self.assertEqual(payload["min_followers"], 500)
        self.assertIn("20", payload["message"])
        self.assertIn("500", payload["error"])
        self.assertFalse(payload["is_private"])

    def test_below_post_min(self):
        from services.profile_quality import ProfileQualityError

        payload = onboarding_scrape_user_error(
            ProfileQualityError("below_post_min", "thin", follower_count=800, post_count=4),
            "thin",
            "instagram",
        )
        self.assertEqual(payload["error_code"], "below_post_min")
        self.assertIn("12", payload["error"])
        self.assertIn("4", payload["message"])

    def test_inactive(self):
        from services.profile_quality import ProfileQualityError

        payload = onboarding_scrape_user_error(
            ProfileQualityError("inactive", "old", latest_post_days_ago=80),
            "old",
            "tiktok",
        )
        self.assertEqual(payload["error_code"], "inactive")
        self.assertIn("80", payload["message"])

    def test_content_quality(self):
        from services.profile_quality import ProfileQualityError

        payload = onboarding_scrape_user_error(
            ProfileQualityError("below_content_quality", "messy"),
            "messy",
            "instagram",
        )
        self.assertEqual(payload["error_code"], "below_content_quality")
        blob = (payload["error"] + " " + payload["message"]).lower()
        self.assertTrue("brand-ready" in blob or "ugc" in blob)


if __name__ == "__main__":
    unittest.main()
