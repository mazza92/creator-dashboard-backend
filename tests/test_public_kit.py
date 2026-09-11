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


if __name__ == '__main__':
    unittest.main()
