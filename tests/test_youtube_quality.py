import json
import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from services.creator_profile_scraper import CreatorProfileScraper
from services.inhouse_social_scraper import diy_scrape_is_acceptable
from services.profile_quality import (
    ProfileQualityError,
    assert_onboarding_quality,
    raw_follower_count,
    visible_post_count,
)
from services.youtube_scraper import (
    extract_yt_initial_data,
    parse_compact_count,
    profile_from_yt_initial_data,
    relative_published_to_unix,
    video_from_renderer,
)


def _yt_payload():
    now = datetime(2026, 8, 15, tzinfo=timezone.utc)
    videos = []
    for i in range(12):
        videos.append({
            "videoRenderer": {
                "videoId": f"vid{i:02d}abcdefghijk",
                "title": {"runs": [{"text": f"Look {i}"}]},
                "publishedTimeText": {"simpleText": "2 days ago" if i == 0 else "1 week ago"},
                "viewCountText": {"simpleText": "1,200 views"},
                "thumbnail": {"thumbnails": [{"url": f"https://i.ytimg.com/vi/vid{i:02d}abcdefghijk/hqdefault.jpg"}]},
            }
        })
    return {
        "metadata": {
            "channelMetadataRenderer": {
                "title": "Maya Glow",
                "description": "Beauty creator. hello@example.com",
                "avatar": {"thumbnails": [{"url": "https://yt3.googleusercontent.com/avatar.jpg"}]},
                "vanityChannelUrl": "https://www.youtube.com/@mayaglow",
            }
        },
        "header": {
            "c4TabbedHeaderRenderer": {
                "title": {"simpleText": "Maya Glow"},
                "subscriberCountText": {"simpleText": "12.4K subscribers"},
                "videosCountText": {"runs": [{"text": "84 videos"}]},
            }
        },
        "contents": {
            "twoColumnBrowseResultsRenderer": {
                "tabs": [{
                    "tabRenderer": {
                        "title": "Videos",
                        "content": {
                            "richGridRenderer": {
                                "contents": [{"richItemRenderer": {"content": v}} for v in videos]
                            }
                        },
                    }
                }]
            }
        },
        "_now": now,
    }


def _lockup_video(i: int) -> dict:
    vid = f"{i:02d}abcdefghi"
    return {
        "lockupViewModel": {
            "contentId": vid,
            "contentType": "LOCKUP_CONTENT_TYPE_VIDEO",
            "contentImage": {
                "thumbnailViewModel": {
                    "image": {
                        "sources": [
                            {"url": f"https://i.ytimg.com/vi/{vid}/hqdefault.jpg", "width": 480},
                        ]
                    }
                }
            },
            "metadata": {
                "lockupMetadataViewModel": {
                    "title": {"content": f"Look {i}"},
                    "metadata": {
                        "contentMetadataViewModel": {
                            "metadataRows": [{
                                "metadataParts": [
                                    {"text": {"content": "3.3K views"}},
                                    {"text": {"content": "5 hours ago" if i == 0 else "2 days ago"}},
                                ]
                            }]
                        }
                    },
                }
            },
        }
    }


def _yt_lockup_payload():
    now = datetime(2026, 8, 17, tzinfo=timezone.utc)
    return {
        "metadata": {
            "channelMetadataRenderer": {
                "title": "LE COACH",
                "description": "Football tactics. hello@example.com",
                "avatar": {"thumbnails": [{"url": "https://yt3.googleusercontent.com/avatar.jpg"}]},
                "vanityChannelUrl": "https://www.youtube.com/@le_coach_tactic",
            }
        },
        "header": {
            "pageHeaderRenderer": {
                "content": {
                    "pageHeaderViewModel": {
                        "metadata": {
                            "contentMetadataViewModel": {
                                "metadataRows": [{
                                    "metadataParts": [
                                        {"text": {"content": "109K subscribers"}},
                                        {"text": {"content": "416 videos"}},
                                    ]
                                }]
                            }
                        }
                    }
                }
            }
        },
        "contents": {
            "twoColumnBrowseResultsRenderer": {
                "tabs": [{
                    "tabRenderer": {
                        "title": "Videos",
                        "content": {
                            "richGridRenderer": {
                                "contents": [
                                    {"richItemRenderer": {"content": _lockup_video(i)}}
                                    for i in range(12)
                                ]
                            }
                        },
                    }
                }]
            }
        },
        "_now": now,
    }


class TestYoutubeParsers(unittest.TestCase):
    def test_compact_counts(self):
        self.assertEqual(parse_compact_count("12.4K subscribers"), 12400)
        self.assertEqual(parse_compact_count("1.5M"), 1500000)
        self.assertEqual(parse_compact_count("84 videos"), 84)
        self.assertEqual(parse_compact_count("1,234"), 1234)

    def test_relative_dates(self):
        now = datetime(2026, 8, 15, tzinfo=timezone.utc)
        two_days = relative_published_to_unix("2 days ago", now=now)
        self.assertIsNotNone(two_days)
        self.assertEqual(int((now.timestamp() - two_days) / 86400), 2)

    def test_profile_from_initial_data(self):
        data = _yt_payload()
        profile = profile_from_yt_initial_data(
            data, "mayaglow", results_limit=12, now=data["_now"]
        )
        self.assertEqual(profile["uniqueId"], "mayaglow")
        self.assertEqual(profile["subscriberCount"], 12400)
        self.assertEqual(profile["followerCount"], 12400)
        self.assertEqual(profile["videoCount"], 84)
        self.assertEqual(len(profile["latestVideos"]), 12)
        self.assertTrue(profile["latestVideos"][0]["videoMeta"]["coverUrl"].startswith("https://i.ytimg.com/"))
        self.assertTrue(diy_scrape_is_acceptable(profile, "youtube"))

    def test_extract_from_html(self):
        payload = {"header": {"c4TabbedHeaderRenderer": {"title": {"simpleText": "X"}}}}
        html = f"<script>var ytInitialData = {json.dumps(payload)};</script>"
        self.assertEqual(extract_yt_initial_data(html)["header"]["c4TabbedHeaderRenderer"]["title"]["simpleText"], "X")

    def test_lockup_view_model_video(self):
        now = datetime(2026, 8, 17, 20, 0, tzinfo=timezone.utc)
        item = video_from_renderer(_lockup_video(0)["lockupViewModel"], now=now)
        self.assertIsNotNone(item)
        self.assertEqual(item["videoId"], "00abcdefghi")
        self.assertEqual(item["title"], "Look 0")
        self.assertEqual(item["viewCount"], 3300)
        self.assertIsNotNone(item["createTime"])
        self.assertLess(now.timestamp() - item["createTime"], 6 * 3600)
        self.assertIn("ytimg.com", item["thumbnail"])

    def test_shorts_lockup_view_model(self):
        renderer = {
            "entityId": "shorts-shelf-item-WacGmZz3xvg",
            "overlayMetadata": {
                "primaryText": {"content": "Why Luis Enrique is the BEST COACH"},
                "secondaryText": {"content": "14K views"},
            },
            "onTap": {
                "innertubeCommand": {
                    "reelWatchEndpoint": {"videoId": "WacGmZz3xvg"}
                }
            },
            "thumbnailViewModel": {
                "thumbnailViewModel": {
                    "image": {
                        "sources": [{"url": "https://i.ytimg.com/vi/WacGmZz3xvg/hq720.jpg"}]
                    }
                }
            },
        }
        item = video_from_renderer(renderer)
        self.assertEqual(item["videoId"], "WacGmZz3xvg")
        self.assertIn("Luis Enrique", item["title"])
        self.assertEqual(item["viewCount"], 14000)
        self.assertIn("WacGmZz3xvg", item["thumbnail"])

    def test_playlist_lockup_is_ignored(self):
        renderer = {
            "contentId": "PLabcdefghijklmnopqrstuvwxyz0123",
            "contentType": "LOCKUP_CONTENT_TYPE_PLAYLIST",
            "metadata": {"lockupMetadataViewModel": {"title": {"content": "Best of"}}},
        }
        self.assertIsNone(video_from_renderer(renderer))

    def test_profile_from_lockup_grid(self):
        data = _yt_lockup_payload()
        profile = profile_from_yt_initial_data(
            data, "le_coach_tactic", results_limit=12, now=data["_now"]
        )
        self.assertEqual(profile["subscriberCount"], 109000)
        self.assertEqual(profile["videoCount"], 416)
        self.assertEqual(len(profile["latestVideos"]), 12)
        self.assertEqual(profile["latestVideos"][0]["title"], "Look 0")
        self.assertTrue(diy_scrape_is_acceptable(profile, "youtube"))


class TestYoutubeQualityPipeline(unittest.TestCase):
    def test_process_scrape_maps_youtube_fields(self):
        data = _yt_payload()
        raw = profile_from_yt_initial_data(data, "mayaglow", results_limit=12, now=data["_now"])
        processed = CreatorProfileScraper().process_scrape(raw, "youtube")
        self.assertEqual(processed["primary_platform"], "youtube")
        self.assertEqual(processed["follower_count"], 12400)
        self.assertEqual(processed["post_count"], 84)
        self.assertLess(processed["latest_post_days_ago"], 30)
        self.assertGreaterEqual(len(processed["recent_post_thumbnails"]), 3)
        self.assertIn("hello@example.com", processed["collab_email_extracted"])
        assert_onboarding_quality(processed, "mayaglow")

    def test_process_scrape_maps_lockup_grid(self):
        data = _yt_lockup_payload()
        raw = profile_from_yt_initial_data(
            data, "le_coach_tactic", results_limit=12, now=data["_now"]
        )
        processed = CreatorProfileScraper().process_scrape(raw, "youtube")
        self.assertEqual(processed["follower_count"], 109000)
        self.assertEqual(processed["post_count"], 416)
        self.assertLess(processed["latest_post_days_ago"], 30)
        self.assertGreaterEqual(len(processed["recent_post_thumbnails"]), 3)
        assert_onboarding_quality(processed, "le_coach_tactic")

    def test_quality_reads_subscriber_count(self):
        self.assertEqual(raw_follower_count({"subscriberCount": 800}), 800)
        self.assertEqual(visible_post_count({"videoCount": 40, "latestVideos": [{}] * 8}), 40)

    def test_youtube_below_subscriber_floor(self):
        with self.assertRaises(ProfileQualityError) as ctx:
            assert_onboarding_quality(
                {
                    "subscriberCount": 20,
                    "videoCount": 40,
                    "latest_post_days_ago": 2,
                    "recent_posts": [{}] * 12,
                },
                "tinyyt",
            )
        self.assertEqual(ctx.exception.code, "below_follower_min")

    def test_hidden_subscribers_with_videos_is_acceptable_scrape(self):
        profile = {
            "uniqueId": "hiddenyt",
            "subscriberCount": 0,
            "videoCount": 20,
            "latestVideos": [{"videoId": "a", "text": "hi"}] * 4,
        }
        self.assertTrue(diy_scrape_is_acceptable(profile, "youtube"))


class TestTiktokQualityMapping(unittest.TestCase):
    def test_process_scrape_tiktok_feeds_quality_bar(self):
        from datetime import timezone as tz
        now = datetime.now(tz.utc).timestamp()
        raw = {
            "uniqueId": "glowtok",
            "followerCount": 2200,
            "videoCount": 30,
            "signature": "ugc beauty",
            "latestVideos": [
                {
                    "id": f"v{i}",
                    "text": f"clip {i}",
                    "createTime": int(now) - 86400,
                    "diggCount": 10,
                    "commentCount": 1,
                    "shareCount": 0,
                    "videoMeta": {"coverUrl": f"https://p16-sign.tiktokcdn.com/{i}.jpg"},
                }
                for i in range(12)
            ],
        }
        processed = CreatorProfileScraper().process_scrape(raw, "tiktok")
        self.assertEqual(processed["follower_count"], 2200)
        self.assertEqual(processed["post_count"], 30)
        self.assertLess(processed["latest_post_days_ago"], 30)
        self.assertEqual(len(processed["recent_post_thumbnails"]), 9)
        assert_onboarding_quality(processed, "glowtok")

    def test_tiktok_cover_referer(self):
        from services.profile_quality import _thumb_headers
        headers = _thumb_headers("https://p16-sign.tiktokcdn.com/cover.jpg")
        self.assertEqual(headers["Referer"], "https://www.tiktok.com/")


if __name__ == "__main__":
    unittest.main()
