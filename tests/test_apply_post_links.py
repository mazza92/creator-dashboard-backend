import unittest

from brand_apply_routes import post_link_error


class PostLinkTests(unittest.TestCase):
    def test_own_posts_pass(self):
        self.assertIsNone(post_link_error("https://www.tiktok.com/@laurafinoia/video/7431", "laurafinoia"))
        self.assertIsNone(post_link_error("https://www.tiktok.com/@LauraFinoia/photo/7431", "@laurafinoia"))
        self.assertIsNone(post_link_error("https://www.instagram.com/reel/AbC123/", "laurafinoia"))
        self.assertIsNone(post_link_error("https://www.youtube.com/shorts/xyz", ""))

    def test_someone_elses_tiktok_is_refused(self):
        err = post_link_error("https://www.tiktok.com/@otherbeauty/video/7431", "laurafinoia")
        self.assertIn("@otherbeauty", err)

    def test_non_social_links_are_refused(self):
        self.assertIsNotNone(post_link_error("https://example.com/my-video", ""))
        self.assertIsNotNone(post_link_error("javascript:alert(1)", ""))

    def test_unknown_handle_does_not_block(self):
        self.assertIsNone(post_link_error("https://www.tiktok.com/@laurafinoia/video/7431", ""))


if __name__ == "__main__":
    unittest.main()
