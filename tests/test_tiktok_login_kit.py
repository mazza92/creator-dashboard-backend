import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from services.tiktok_login_kit import (
    fetch_videos,
    oauth_profile_to_raw_scrape,
    user_and_videos_to_raw_scrape,
    videos_to_oauth_snapshot,
)
from services.creator_profile_scraper import CreatorProfileScraper


class TestTikTokLoginKitMapping(unittest.TestCase):
    def test_oauth_snapshot_maps_into_scrape_shape(self):
        raw = oauth_profile_to_raw_scrape({
            "username": "cdcparis",
            "display_name": "CDC Paris",
            "bio_description": "Paris UGC",
            "follower_count": 12000,
            "media_count": 40,
            "likes_count": 90000,
            "avatar_url": "https://cdn/avatar.jpg",
            "oauth_videos": [
                {
                    "id": "123",
                    "title": "GRWM",
                    "cover_image_url": "https://cdn/cover.jpg",
                    "create_time": 1784400000,
                    "share_url": "https://www.tiktok.com/@cdcparis/video/123",
                    "likes": 40,
                    "comments": 2,
                    "shares": 1,
                    "views": 900,
                }
            ],
        })
        self.assertEqual(raw["uniqueId"], "cdcparis")
        self.assertEqual(raw["followerCount"], 12000)
        self.assertEqual(raw["_source"], "login_kit")
        video = raw["latestVideos"][0]
        self.assertEqual(video["diggCount"], 40)
        self.assertEqual(video["playCount"], 900)
        self.assertEqual(video["videoMeta"]["coverUrl"], "https://cdn/cover.jpg")
        self.assertEqual(video["createTime"], 1784400000)

    def test_user_info_plus_videos_round_trip_snapshot(self):
        videos = user_and_videos_to_raw_scrape(
            {"username": "nisha", "follower_count": 800, "video_count": 12},
            [{
                "id": "9",
                "text": "unbox",
                "diggCount": 3,
                "commentCount": 1,
                "shareCount": 0,
                "playCount": 50,
                "createTime": 1784400000,
                "videoMeta": {"coverUrl": "https://x"},
                "covers": ["https://x"],
                "share_url": "https://tiktok.com/@nisha/video/9",
            }],
        )["latestVideos"]
        snapshot = videos_to_oauth_snapshot(videos)
        self.assertEqual(snapshot[0]["likes"], 3)
        self.assertEqual(snapshot[0]["views"], 50)

    def test_paginated_video_list(self):
        class _Resp:
            def __init__(self, payload):
                self.content = b"{}"
                self._payload = payload

            def json(self):
                return self._payload

        payloads = [
            {
                "error": {"code": "ok"},
                "data": {
                    "videos": [{"id": "1", "title": "a", "like_count": 1, "view_count": 10}],
                    "has_more": True,
                    "cursor": 20,
                },
            },
            {
                "error": {"code": "ok"},
                "data": {
                    "videos": [{"id": "2", "title": "b", "like_count": 2, "view_count": 20}],
                    "has_more": False,
                    "cursor": 40,
                },
            },
        ]

        with patch("services.tiktok_login_kit.requests.post", side_effect=[_Resp(p) for p in payloads]):
            videos = fetch_videos("token", max_videos=40)
        self.assertEqual([v["id"] for v in videos], ["1", "2"])


class TestTikTokScrapePrefersLoginKit(unittest.TestCase):
    def test_uses_oauth_snapshot_instead_of_html(self):
        scraper = CreatorProfileScraper()
        with patch("services.creator_profile_scraper.diy_scrape_tiktok") as mock_diy:
            result = scraper.scrape_tiktok_profile(
                "cdcparis",
                oauth_profile={
                    "username": "cdcparis",
                    "follower_count": 1200,
                    "media_count": 20,
                    "bio_description": "hello",
                    "oauth_videos": [
                        {"id": "1", "title": "hi", "likes": 4, "views": 80, "create_time": 1784400000}
                    ],
                },
            )
            mock_diy.assert_not_called()
        self.assertEqual(result["_source"], "login_kit")
        self.assertEqual(result["followerCount"], 1200)

    def test_falls_back_to_html_without_tokens(self):
        scraper = CreatorProfileScraper()
        with patch(
            "services.creator_profile_scraper.diy_scrape_tiktok",
            return_value={
                "uniqueId": "ttuser",
                "followerCount": 1000,
                "videoCount": 10,
                "privateAccount": False,
                "signature": "tiktok bio",
                "latestVideos": [{"text": "hi", "createTime": 1784400000, "diggCount": 1}],
            },
        ) as mock_diy:
            result = scraper.scrape_tiktok_profile("ttuser")
            mock_diy.assert_called_once()
        self.assertEqual(result["uniqueId"], "ttuser")

    def test_connected_token_never_falls_back_to_html(self):
        from services.tiktok_login_kit import TikTokLoginKitError

        scraper = CreatorProfileScraper()
        with patch(
            "services.creator_profile_scraper.diy_scrape_tiktok"
        ) as mock_diy, patch(
            "services.tiktok_login_kit.fetch_raw_scrape",
            side_effect=TikTokLoginKitError("token dead"),
        ):
            with self.assertRaises(ValueError) as ctx:
                scraper.scrape_tiktok_profile("cdcparis", access_token="dead-token")
            mock_diy.assert_not_called()
        self.assertIn("Reconnect", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
