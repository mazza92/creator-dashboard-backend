import unittest

from routes.admin_creators import (
    _public_review_posts,
    _review_flags,
    _primary_social,
)


class TestCreatorApprovalReview(unittest.TestCase):
    def test_strips_posts_to_public_fields(self):
        posts = _public_review_posts([
            {
                'id': '1',
                'title': 'fit check',
                'cover_image_url': 'https://cdn/x.jpg',
                'share_url': 'https://tiktok.com/@x/video/1',
                'likes': 34,
                'views': 1151,
                'access_token': 'secret',
            },
            {
                'id': '2',
                'thumbnail_url': 'https://cdn/y.jpg',
                'like_count': 12,
                'view_count': 400,
            },
        ])
        self.assertEqual(len(posts), 2)
        self.assertEqual(posts[0]['likes'], 34)
        self.assertEqual(posts[0]['views'], 1151)
        self.assertEqual(posts[0]['url'], 'https://tiktok.com/@x/video/1')
        self.assertNotIn('access_token', posts[0])
        self.assertEqual(posts[1]['likes'], 12)

    def test_handle_prefers_oauth_over_display_name_username(self):
        platform, handle = _primary_social({
            'social_handle': 'cdcparis',
            'social_platform': 'tiktok',
            'username': 'Test',
            'social_links': [],
        })
        self.assertEqual(platform, 'tiktok')
        self.assertEqual(handle, 'cdcparis')

    def test_handle_falls_back_to_social_links(self):
        platform, handle = _primary_social({
            'social_handle': '',
            'username': 'fallback',
            'social_links': [{'platform': 'instagram', 'handle': '@naina'}],
        })
        self.assertEqual(platform, 'instagram')
        self.assertEqual(handle, 'naina')

    def test_flags_incomplete_and_pro_mismatch(self):
        flags = _review_flags({
            'username': '',
            'bio': '',
            'niche': '[]',
            'followers_count': 20,
            'tier': 'pro',
            'approval_status': 'pending',
        })
        self.assertIn('incomplete_profile', flags)
        self.assertIn('missing_bio', flags)
        self.assertIn('low_followers', flags)
        self.assertIn('pro_pending', flags)

    def test_complete_profile_has_no_incomplete_flag(self):
        flags = _review_flags({
            'username': 'cdcparis',
            'social_handle': 'cdcparis',
            'social_platform': 'tiktok',
            'bio': 'Paris streetwear',
            'niche': '["fashion"]',
            'followers_count': 7811,
            'tier': 'free',
            'approval_status': 'pending',
        })
        self.assertNotIn('incomplete_profile', flags)
        self.assertNotIn('low_followers', flags)
        self.assertNotIn('pro_pending', flags)


if __name__ == '__main__':
    unittest.main()
