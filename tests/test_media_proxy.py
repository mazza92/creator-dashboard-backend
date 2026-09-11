"""Social CDN media proxy. Post stills are not uploaded to storage."""

import unittest
from unittest.mock import patch

from media_proxy_routes import (
    persist_post_thumbnail,
    persist_profile_media,
    persist_social_thumbnails,
    to_proxied_media_url,
    unwrap_proxied_media_url,
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


class TestUnwrapAndRecoverThumbs(unittest.TestCase):
    def test_unwraps_media_proxy(self):
        from urllib.parse import quote

        inner = "https://p16-common-sign.tiktokcdn-us.com/tos/x.jpg?x-expires=1"
        wrapped = "https://api.newcollab.co/api/media-proxy?url=" + quote(inner, safe="")
        self.assertEqual(unwrap_proxied_media_url(wrapped), inner)
        self.assertEqual(unwrap_proxied_media_url(inner), inner)

    def test_persist_proxies_fresh_cdn_and_does_not_upload(self):
        fresh = "https://p16-common-sign.tiktokcdn-us.com/fresh.jpg"
        with patch("media_proxy_routes.rehost_social_image") as rh, patch(
            "media_proxy_routes.fresh_thumb_from_post_url",
            return_value=fresh,
        ):
            out = persist_post_thumbnail(
                "https://api.newcollab.co/api/media-proxy?url=https%3A%2F%2Fp16-common-sign.tiktokcdn-us.com%2Fold.jpg",
                "https://www.tiktok.com/@nisha/video/1",
            )
        rh.assert_not_called()
        self.assertIn("/api/media-proxy?url=", out)
        self.assertIn("fresh.jpg", out)

    def test_persist_leaves_existing_storage_url(self):
        existing = "https://xyz.supabase.co/storage/v1/object/public/creators/thumbs/posts/abc.jpg"
        with patch("media_proxy_routes.rehost_social_image") as rh, patch(
            "media_proxy_routes.fresh_thumb_from_post_url"
        ) as fresh:
            out = persist_post_thumbnail(
                existing,
                "https://www.tiktok.com/@nisha/video/1",
            )
        rh.assert_not_called()
        fresh.assert_not_called()
        self.assertEqual(out, existing)


class TestPersistThumbnails(unittest.TestCase):
    def test_skips_already_hosted(self):
        hosted = "https://xyz.supabase.co/storage/v1/object/public/creators/thumbs/a.jpg"
        with patch("media_proxy_routes.rehost_social_image") as rh:
            out = persist_social_thumbnails([hosted])
        rh.assert_not_called()
        self.assertEqual(out, [hosted])

    def test_profile_media_does_not_rehost_galleries(self):
        cdn = "https://p16-common-sign.tiktokcdn-eu.com/tos/cover1.image?x-expires=1"
        profile = {
            "handle": "yasia_a",
            "recent_post_thumbnails": [cdn],
            "recent_posts": [{"thumbnail_url": cdn}],
        }
        with patch("media_proxy_routes.rehost_social_image") as rh:
            persist_profile_media(profile)
        rh.assert_not_called()
        self.assertEqual(profile["recent_post_thumbnails"], [cdn])
        self.assertEqual(profile["recent_posts"][0]["thumbnail_url"], cdn)


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
