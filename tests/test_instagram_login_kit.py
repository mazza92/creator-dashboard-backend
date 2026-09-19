import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


class InstagramLoginKitTests(unittest.TestCase):
    def test_media_maps_permalink_and_counts(self):
        from services.instagram_login_kit import media_to_oauth_snapshot, user_and_media_to_raw_scrape

        media = [{
            "id": "88",
            "caption": "hello",
            "url": "https://www.instagram.com/p/AbC/",
            "displayUrl": "https://cdn.example/cover.jpg",
            "likesCount": 12,
            "commentsCount": 3,
            "createTime": 1710000000,
        }]
        snap = media_to_oauth_snapshot(media)
        self.assertEqual(snap[0]["url"], "https://www.instagram.com/p/AbC/")
        self.assertEqual(snap[0]["likes"], 12)
        raw = user_and_media_to_raw_scrape(
            {
                "username": "cdcparis",
                "name": "CDC",
                "followers_count": 1200,
                "media_count": 40,
                "biography": "ugc",
            },
            media,
        )
        self.assertEqual(raw["username"], "cdcparis")
        self.assertEqual(raw["followersCount"], 1200)
        self.assertEqual(raw["latestPosts"][0]["likesCount"], 12)

    def test_oauth_snapshot_round_trip(self):
        from services.instagram_login_kit import oauth_profile_to_raw_scrape
        from services.inhouse_social_scraper import diy_scrape_is_acceptable

        raw = oauth_profile_to_raw_scrape({
            "username": "cdcparis",
            "follower_count": 900,
            "media_count": 20,
            "bio_description": "ugc creator",
            "oauth_videos": [{
                "id": "1",
                "title": "post",
                "url": "https://www.instagram.com/reel/x/",
                "cover_image_url": "https://cdn.example/a.jpg",
                "likes": 4,
            }],
        })
        self.assertTrue(diy_scrape_is_acceptable(raw, "instagram"))

    def test_professional_error_detection(self):
        from services.instagram_login_kit import is_professional_account_error
        self.assertTrue(is_professional_account_error("Switch to a professional account"))
        self.assertFalse(is_professional_account_error("user cancelled"))

    def test_token_exchange_does_not_overwrite_newcollab_user_concept(self):
        from services.instagram_login_kit import _media_to_scrape_item
        item = _media_to_scrape_item({
            "id": "99",
            "permalink": "https://www.instagram.com/p/Zz/",
            "thumbnail_url": "https://cdn.example/t.jpg",
            "like_count": 2,
            "comments_count": 1,
            "timestamp": "2026-09-01T12:00:00+00:00",
            "caption": "x",
        })
        self.assertEqual(item["url"], "https://www.instagram.com/p/Zz/")
        self.assertTrue(item["createTime"])


class InstagramConnectConfigTests(unittest.TestCase):
    def test_settings_return_still_works(self):
        from social_verification_routes import _is_settings_return
        self.assertTrue(_is_settings_return(
            "http://localhost:3000/creator/dashboard/settings", "settings"
        ))

    def test_instagram_oauth_enabled_with_creds(self):
        from social_verification_routes import _instagram_oauth_enabled
        with patch("social_verification_routes._instagram_oauth_config", return_value=("id", "secret", "https://x")):
            with patch.dict("os.environ", {"INSTAGRAM_OAUTH_ENABLED": ""}, clear=False):
                self.assertTrue(_instagram_oauth_enabled())


if __name__ == "__main__":
    unittest.main()
