"""Social CDN media proxy + thumbnail rehost."""

import unittest
from unittest.mock import patch

from media_proxy_routes import (
    persist_profile_media,
    persist_social_thumbnails,
    to_proxied_media_url,
)


class TestToProxiedMediaUrl(unittest.TestCase):
    def test_leaves_supabase_urls_alone(self):
        url = "https://xyz.supabase.co/storage/v1/object/public/creators/thumbs/a.jpg"
        self.assertEqual(to_proxied_media_url(url), url)

    def test_wraps_tiktok_cdn(self):
        url = "https://p16-common-sign.tiktokcdn-eu.com/tos-useast2a-p-0037-euttp/cover~tplv-tiktokx-origin.image?x-expires=1"
        proxied = to_proxied_media_url(url, api_base="https://api.newcollab.co")
        self.assertIn("/api/media-proxy?url=", proxied)
        self.assertIn("tiktokcdn-eu.com", proxied)


class TestPersistThumbnails(unittest.TestCase):
    def test_skips_already_hosted(self):
        hosted = "https://xyz.supabase.co/storage/v1/object/public/creators/thumbs/a.jpg"
        with patch("media_proxy_routes.rehost_social_image") as rh:
            out = persist_social_thumbnails([hosted])
        rh.assert_not_called()
        self.assertEqual(out, [hosted])

    def test_profile_media_rewrites_thumbs_and_posts(self):
        profile = {
            "handle": "yasia_a",
            "recent_post_thumbnails": [
                "https://p16-common-sign.tiktokcdn-eu.com/tos/cover1.image?x-expires=1",
            ],
            "recent_posts": [
                {"thumbnail_url": "https://p16-common-sign.tiktokcdn-eu.com/tos/cover1.image?x-expires=1"},
            ],
        }
        durable = "https://xyz.supabase.co/storage/v1/object/public/creators/thumbs/yasia_a/abc.jpg"
        with patch("media_proxy_routes.rehost_social_image", return_value=durable):
            persist_profile_media(profile)
        self.assertEqual(profile["recent_post_thumbnails"], [durable])
        self.assertEqual(profile["recent_posts"][0]["thumbnail_url"], durable)


class TestCoercePostUrl(unittest.TestCase):
    def test_tiktok_from_video_id(self):
        from media_proxy_routes import coerce_post_url, format_snapshot_posts

        url = coerce_post_url(
            {"shortCode": "7550123456789012345", "thumbnail_url": "https://cdn.example/a.jpg"},
            handle="juliaklag",
            platform="tiktok",
        )
        self.assertEqual(url, "https://www.tiktok.com/@juliaklag/video/7550123456789012345")

        posts = format_snapshot_posts({
            "handle": "juliaklag",
            "primary_platform": "tiktok",
            "recent_posts": [
                {
                    "thumbnail_url": "https://example.com/thumb.jpg",
                    "shortCode": "7550123456789012345",
                }
            ],
            "recent_post_thumbnails": ["https://example.com/thumb.jpg"],
        })
        self.assertEqual(len(posts), 1)
        self.assertEqual(posts[0]["post_url"], "https://www.tiktok.com/@juliaklag/video/7550123456789012345")

    def test_skips_thumbs_without_url(self):
        from media_proxy_routes import format_snapshot_posts

        posts = format_snapshot_posts({
            "handle": "juliaklag",
            "primary_platform": "tiktok",
            "recent_posts": [],
            "recent_post_thumbnails": ["https://example.com/thumb.jpg"],
        })
        self.assertEqual(posts, [])


if __name__ == "__main__":
    unittest.main()
