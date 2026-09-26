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
        self.assertFalse(
            is_professional_account_error("Unsupported request - method type: get")
        )
        from services.instagram_login_kit import is_unsupported_graph_method_error
        self.assertTrue(
            is_unsupported_graph_method_error("Unsupported request - method type: get")
        )
        self.assertTrue(
            is_unsupported_graph_method_error(
                "Unsupported request - method type: get (code=100 type=IGApiException)"
            )
        )

    def test_unwraps_nested_token_payload(self):
        from services.instagram_login_kit import _unwrap_token_payload
        nested = {"data": [{"access_token": "IGQ", "user_id": "9"}]}
        self.assertEqual(_unwrap_token_payload(nested)["access_token"], "IGQ")

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
        self.assertEqual(item["shortCode"], "Zz")
        self.assertTrue(item["createTime"])

    def test_oauth_snapshot_keeps_permalink_shortcode_and_unix_dates(self):
        from datetime import datetime, timezone
        from services.creator_profile_scraper import CreatorProfileScraper
        from services.instagram_login_kit import oauth_profile_to_raw_scrape

        five_days_ago = int(datetime.now(timezone.utc).timestamp()) - (5 * 86400)
        raw = oauth_profile_to_raw_scrape({
            "username": "mlz1192",
            "display_name": "m01",
            "follower_count": 215,
            "following_count": 554,
            "media_count": 5,
            "likes_count": 0,
            "account_type": "BUSINESS",
            "oauth_videos": [{
                "id": "17864011972910241",
                "title": "Bali 2020",
                "url": "https://www.instagram.com/p/CClrwNTo8-x/",
                "cover_image_url": "https://cdn.example/cover.jpg",
                "create_time": five_days_ago,
                "likes": 33,
                "comments": 7,
            }],
        })
        self.assertTrue(raw["isBusinessAccount"])
        self.assertEqual(raw["latestPosts"][0]["shortCode"], "CClrwNTo8-x")
        processed = CreatorProfileScraper().process_scrape(raw, "instagram")
        post = processed["recent_posts"][0]
        self.assertEqual(post["post_url"], "https://www.instagram.com/p/CClrwNTo8-x/")
        self.assertEqual(post["shortCode"], "CClrwNTo8-x")
        self.assertEqual(post["likes"], 33)
        self.assertEqual(processed["like_count"], 33)
        self.assertLessEqual(processed["latest_post_days_ago"], 6)
        self.assertNotEqual(processed["latest_post_days_ago"], 999)

    def test_instagram_graph_offset_without_colon_parses(self):
        from services.creator_profile_scraper import CreatorProfileScraper
        from services.instagram_login_kit import _iso_to_unix

        self.assertEqual(_iso_to_unix("2020-07-13T16:22:29+0000"), 1594657349)
        scraper = CreatorProfileScraper()
        parsed = scraper._parse_post_date({"timestamp": "2020-07-13T16:22:29+0000"}, "instagram")
        self.assertIsNotNone(parsed)
        self.assertEqual(int(parsed.timestamp()), 1594657349)
        old = CreatorProfileScraper().process_scrape(
            {
                "username": "mlz1192",
                "followersCount": 215,
                "postsCount": 1,
                "latestPosts": [{
                    "shortCode": "CClrwNTo8-x",
                    "url": "https://www.instagram.com/p/CClrwNTo8-x/",
                    "displayUrl": "https://cdn.example/cover.jpg",
                    "likesCount": 33,
                    "timestamp": "2020-07-13T16:22:29+0000",
                }],
            },
            "instagram",
        )
        self.assertGreater(old["latest_post_days_ago"], 999)


class InstagramConnectConfigTests(unittest.TestCase):
    def test_settings_return_still_works(self):
        from social_verification_routes import _is_settings_return
        self.assertTrue(_is_settings_return(
            "http://localhost:3000/creator/dashboard/settings", "settings"
        ))

    def test_local_quality_skip_stays_off_in_production(self):
        from social_verification_routes import _skip_onboarding_quality_locally
        self.assertTrue(_skip_onboarding_quality_locally("http://localhost:3000/onboarding"))
        self.assertFalse(_skip_onboarding_quality_locally("https://app.newcollab.co/onboarding"))

    def test_instagram_oauth_enabled_with_creds(self):
        from social_verification_routes import _instagram_oauth_enabled
        with patch("social_verification_routes._instagram_oauth_config", return_value=("id", "secret", "https://x")):
            with patch.dict("os.environ", {"INSTAGRAM_OAUTH_ENABLED": ""}, clear=False):
                self.assertTrue(_instagram_oauth_enabled())

    def test_graph_error_includes_meta_code(self):
        from services.instagram_login_kit import _graph_error
        self.assertIn(
            "code=100",
            _graph_error({
                "error": {
                    "message": "Unsupported request - method type: get",
                    "type": "IGApiException",
                    "code": 100,
                }
            }),
        )


if __name__ == "__main__":
    unittest.main()
