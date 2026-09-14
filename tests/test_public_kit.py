# -*- coding: utf-8 -*-
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from services.public_kit import (
    build_public_socials,
    parse_kit_niches,
    serialize_public_recent_posts,
    serialize_tiktok_picker_videos,
)


class TestParseKitNiches(unittest.TestCase):
    def test_json_string(self):
        self.assertEqual(
            parse_kit_niches('["beauty", "fashion", "haircare"]'),
            ['beauty', 'fashion', 'haircare'],
        )

    def test_broken_comma_split_fragments(self):
        self.assertEqual(
            parse_kit_niches(['["beauty"', '"fashion"', '"haircare"]']),
            ['beauty', 'fashion', 'haircare'],
        )

    def test_plain_list(self):
        self.assertEqual(parse_kit_niches(['skincare', 'lifestyle']), ['skincare', 'lifestyle'])


class TestBuildPublicSocials(unittest.TestCase):
    def test_builds_url_from_handle(self):
        socials, profiles = build_public_socials(
            [{'platform': 'instagram', 'handle': '@mary__solkan', 'followersCount': 37300}],
        )
        self.assertEqual(socials['instagram'], 'https://instagram.com/mary__solkan')
        self.assertEqual(profiles[0]['handle'], '@mary__solkan')
        self.assertEqual(profiles[0]['followers'], 37300)

    def test_falls_back_to_primary_social(self):
        socials, profiles = build_public_socials(
            [],
            social_handle='bapickler',
            social_platform='instagram',
        )
        self.assertEqual(socials['instagram'], 'https://instagram.com/bapickler')
        self.assertEqual(len(profiles), 1)

    def test_username_fallback_when_links_empty(self):
        socials, profiles = build_public_socials([], username='mary__solkan')
        self.assertEqual(profiles[0]['handle'], '@mary__solkan')
        self.assertIn('instagram.com/mary__solkan', socials.get('instagram', ''))


class TestSerializeRecentPosts(unittest.TestCase):
    def test_strips_tokens_and_keeps_public_fields(self):
        posts = serialize_public_recent_posts([
            {
                'id': '1',
                'cover_image_url': 'https://cdn/x.jpg',
                'share_url': 'https://tiktok.com/@x/video/1',
                'likes': 12,
                'views': 400,
                'access_token': 'secret',
                'platform': 'tiktok',
            }
        ])
        self.assertEqual(len(posts), 1)
        self.assertEqual(posts[0]['views'], 400)
        self.assertEqual(posts[0]['post_url'], 'https://tiktok.com/@x/video/1')
        self.assertNotIn('access_token', posts[0])
        self.assertEqual(posts[0]['source'], 'scrape')

    def test_onboarding_instagram_payload(self):
        posts = serialize_public_recent_posts(
            [{
                'thumbnail_url': 'https://cdn/ig.jpg',
                'post_url': 'https://www.instagram.com/p/abc/',
                'likes': 40,
                'views': 0,
            }],
            default_platform='instagram',
        )
        self.assertEqual(len(posts), 1)
        self.assertEqual(posts[0]['platform'], 'instagram')
        self.assertEqual(posts[0]['thumbnail_url'], 'https://cdn/ig.jpg')
        self.assertEqual(posts[0]['post_url'], 'https://www.instagram.com/p/abc/')


class TestTikTokPickerVideos(unittest.TestCase):
    def test_maps_login_kit_snapshot(self):
        from services.public_kit import serialize_tiktok_picker_videos

        rows = serialize_tiktok_picker_videos(
            [{
                'id': '123',
                'title': 'GRWM',
                'cover_image_url': 'https://cdn/cover.jpg',
                'share_url': 'https://www.tiktok.com/@cdcparis/video/123',
                'likes': 40,
                'views': 900,
            }],
            handle='cdcparis',
        )
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['url'], 'https://www.tiktok.com/@cdcparis/video/123')
        self.assertEqual(rows[0]['title'], 'GRWM')
        self.assertEqual(rows[0]['likes'], 40)
        self.assertEqual(rows[0]['views'], 900)

    def test_builds_url_from_id_and_dedupes(self):
        from services.public_kit import serialize_tiktok_picker_videos

        rows = serialize_tiktok_picker_videos(
            [{'id': '9', 'title': 'one'}],
            [{'id': '9', 'post_url': 'https://www.tiktok.com/@cdcparis/video/9', 'caption': 'one'}],
            handle='cdcparis',
        )
        self.assertEqual(len(rows), 1)
        self.assertIn('/video/9', rows[0]['url'])


class TestSocialKitStats(unittest.TestCase):
    def test_uses_login_kit_totals_and_avg_views(self):
        from services.public_kit import social_kit_stats

        stats = social_kit_stats(
            creator={'total_likes': 109195, 'social_media_count': 153, 'first_name': 'Tesdt'},
            scrape={'full_name': 'CDC', 'engagement_rate': 0.51, 'post_count': 153},
            oauth_videos=[
                {'id': '1', 'views': 1000, 'share_url': 'https://www.tiktok.com/@cdcparis/video/1'},
                {'id': '2', 'views': 2000, 'share_url': 'https://www.tiktok.com/@cdcparis/video/2'},
            ],
            handle='cdcparis',
        )
        self.assertEqual(stats['likes_count'], 109195)
        self.assertEqual(stats['video_count'], 153)
        self.assertEqual(stats['avg_views'], 1500)
        self.assertEqual(stats['display_name'], 'CDC')
        self.assertEqual(stats['engagement_rate'], 0.51)


if __name__ == '__main__':
    unittest.main()
