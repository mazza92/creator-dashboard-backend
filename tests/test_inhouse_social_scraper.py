"""Unit tests for in-house IG/TikTok scrapers (no network)."""

import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from services.inhouse_social_scraper import (
    parse_instagram_user_payload,
    parse_instagram_imginn_html,
    parse_instagram_crawler_html,
    parse_instagram_search_snippets,
    parse_instagram_post_embed_owner,
    parse_instagram_profile_embed_html,
    parse_tiktok_rehydration,
    parse_tiktok_embed_frontity,
    parse_tiktok_embed_frontity_bundle,
    diy_scrape_is_acceptable,
    _ig_extract_pk_from_html,
    _ig_node_to_post,
)
from services.creator_profile_scraper import CreatorProfileScraper


FIXTURES = Path(__file__).parent / "fixtures"


def _load(name: str):
    with open(FIXTURES / name, encoding="utf-8") as f:
        return json.load(f)


class TestInstagramParse(unittest.TestCase):
    def test_profile_and_posts_shape(self):
        user = _load("instagram_user_with_posts.json")
        profile = parse_instagram_user_payload(user, results_limit=12)

        self.assertEqual(profile["username"], "humansofny")
        self.assertEqual(profile["followersCount"], 12500000)
        self.assertEqual(profile["postsCount"], 5400)
        self.assertTrue(profile["isVerified"])
        self.assertFalse(profile["isPrivate"])
        self.assertEqual(profile["externalUrl"], "https://www.humansofnewyork.com")
        self.assertIn("collab@example.com", profile["biography"])

        posts = profile["latestPosts"]
        self.assertEqual(len(posts), 3)
        self.assertEqual(posts[0]["likesCount"], 120000)
        self.assertEqual(posts[0]["commentsCount"], 3400)
        self.assertEqual(posts[0]["caption"], "A story from the subway.")
        self.assertTrue(posts[0]["displayUrl"].endswith("post1.jpg"))
        self.assertTrue(posts[0]["timestamp"])
        self.assertTrue(posts[2]["isPinnedItem"])

    def test_private_account_empty_posts_ok(self):
        user = _load("instagram_private_user.json")
        profile = parse_instagram_user_payload(user)
        self.assertTrue(profile["isPrivate"])
        self.assertEqual(profile["latestPosts"], [])
        self.assertTrue(diy_scrape_is_acceptable(profile, "instagram"))

    def test_imginn_html_bio_and_posts(self):
        html = (FIXTURES / "instagram_imginn.html").read_text(encoding="utf-8")
        profile = parse_instagram_imginn_html(html, handle="democreator", results_limit=12)
        self.assertEqual(profile["username"], "democreator")
        self.assertEqual(profile["followersCount"], 12500)
        self.assertEqual(profile["postsCount"], 84)
        self.assertIn("hello@example.com", profile["biography"])
        self.assertEqual(len(profile["latestPosts"]), 2)
        self.assertEqual(profile["latestPosts"][0]["likesCount"], 1200)
        self.assertTrue(profile["latestPosts"][0]["displayUrl"].endswith("post1.jpg"))
        self.assertTrue(profile["latestPosts"][0]["timestamp"])
        self.assertTrue(diy_scrape_is_acceptable(profile, "instagram"))

        processed = CreatorProfileScraper().process_scrape(profile, "instagram")
        self.assertLess(processed["latest_post_days_ago"], 2)
        self.assertEqual(processed["collab_email_extracted"], "hello@example.com")

    def test_crawler_meta_stats_and_bio(self):
        html = """
        <html><head>
        <meta property="og:description" content="554 Followers, 50 Following, 136 Posts - See Instagram photos and videos from Naina'sDairy.jpg (@nainadiary.jpg)" />
        <meta name="description" content="554 Followers, 50 Following, 136 Posts - Naina'sDairy.jpg (@nainadiary.jpg) on Instagram: &quot;Soft Girly Creator&#10;Skincare UGC&#10;hello@naina.test&quot;" />
        <meta property="og:title" content="Naina'sDairy.jpg (@nainadiary.jpg)" />
        </head></html>
        """
        profile = parse_instagram_crawler_html(html, handle="nainadiary.jpg")
        self.assertEqual(profile["followersCount"], 554)
        self.assertEqual(profile["followsCount"], 50)
        self.assertEqual(profile["postsCount"], 136)
        self.assertIn("hello@naina.test", profile["biography"])
        self.assertIn("Soft Girly Creator", profile["biography"])

    def test_crawler_meta_content_before_name_emoji_bio(self):
        """IG crawler HTML often puts content= before name=, with entity-encoded emoji bios."""
        html = """
        <html><head>
        <meta content="noarchive" />
        <meta name="robots" content="noarchive, noimageindex" />
        <meta property="og:description" content="591 Followers, 1,603 Following, 6 Posts - See Instagram photos and videos from Olivia Fraites (&#064;oliviafraites)" />
        <meta content="591 Followers, 1,603 Following, 6 Posts - Olivia Fraites (&#064;oliviafraites) on Instagram: &quot;&#x2764;&#xfe0f;&quot;" name="description" />
        <meta property="og:title" content="Olivia Fraites (&#064;oliviafraites)" />
        </head></html>
        """
        profile = parse_instagram_crawler_html(html, handle="oliviafraites")
        self.assertEqual(profile["followersCount"], 591)
        self.assertEqual(profile["postsCount"], 6)
        self.assertTrue(profile["biography"])
        self.assertIn("❤", profile["biography"])
        self.assertTrue(diy_scrape_is_acceptable(
            {**profile, "latestPosts": [{"displayUrl": "https://x", "caption": "hi"}]},
            "instagram",
        ))

    def test_search_snippets_followers_and_bio(self):
        html = (
            '552 followers · 51 following · 135 posts · @<b>nainadiary.jpg</b>: '
            '“ Soft Girly Beauty Creator Skincare • Makeup • UGC Aesthetic Reels & Honest Reviews ...'
        )
        profile = parse_instagram_search_snippets(html, handle="nainadiary.jpg")
        self.assertEqual(profile["followersCount"], 552)
        self.assertEqual(profile["followsCount"], 51)
        self.assertEqual(profile["postsCount"], 135)
        self.assertIn("Soft Girly Beauty Creator", profile["biography"])

    def test_profile_embed_followers_and_posts(self):
        html = (FIXTURES / "instagram_profile_embed.html").read_text(encoding="utf-8")
        profile = parse_instagram_profile_embed_html(html, handle="nainadiary.jpg", results_limit=6)
        self.assertEqual(profile["username"], "nainadiary.jpg")
        self.assertEqual(profile["followersCount"], 561)
        self.assertGreaterEqual(profile["postsCount"], 6)
        posts = profile["latestPosts"]
        self.assertGreaterEqual(len(posts), 3)
        self.assertTrue(all(p.get("shortCode") for p in posts[:3]))
        # Embed payload includes thumbs and/or captions for kit fill
        self.assertTrue(
            any(p.get("displayUrl") for p in posts) or any(p.get("caption") for p in posts)
        )

    def test_search_snippets_reject_instagram_2b_marketing(self):
        """SERP pages quote Instagram's '2 billion users' — must not become follower_count."""
        from services.inhouse_social_scraper import InHouseScrapeError

        html = (
            'Instagram has 2,000,000,000 followers worldwide. '
            'See @pl3th0ranina profile photos.'
        )
        html2 = '@pl3th0ranina 2000000000 Followers on Instagram'
        with self.assertRaises(InHouseScrapeError):
            parse_instagram_search_snippets(html, handle="pl3th0ranina")
        with self.assertRaises(InHouseScrapeError):
            parse_instagram_search_snippets(html2, handle="pl3th0ranina")

        # Plausible nearby count still wins when both appear
        mixed = (
            '@pl3th0ranina 41 Followers. Instagram has 2,000,000,000 users. '
            'Also @pl3th0ranina 2000000000 Followers spam.'
        )
        # First plausible match near handle should be 41
        profile = parse_instagram_search_snippets(mixed, handle="pl3th0ranina")
        self.assertEqual(profile["followersCount"], 41)

    def test_post_embed_owner_followers(self):
        html = (
            '<div class="HoverCardUserName"><span class="Username">nainadiary.jpg</span></div>'
            '<div class="HoverCardStatus"><span>136 posts · 554 followers</span></div>'
        )
        profile = parse_instagram_post_embed_owner(html, handle="nainadiary.jpg")
        self.assertEqual(profile["followersCount"], 554)
        self.assertEqual(profile["postsCount"], 136)
        self.assertEqual(profile["username"], "nainadiary.jpg")


class TestTikTokParse(unittest.TestCase):
    def test_rehydration_to_latest_videos(self):
        data = _load("tiktok_rehydration.json")
        profile = parse_tiktok_rehydration(data, handle="nikkichavvez", results_limit=12)

        self.assertEqual(profile["uniqueId"], "nikkichavvez")
        self.assertEqual(profile["followerCount"], 7709)
        self.assertEqual(profile["videoCount"], 84)
        self.assertEqual(profile["bioLink"], "https://linktr.ee/nikki")
        self.assertFalse(profile["privateAccount"])

        videos = profile["latestVideos"]
        self.assertEqual(len(videos), 3)
        self.assertEqual(videos[0]["text"], "REBUILDING out loud?")
        self.assertEqual(videos[0]["diggCount"], 4200)
        self.assertEqual(videos[0]["commentCount"], 120)
        self.assertEqual(videos[0]["shareCount"], 40)
        self.assertEqual(videos[0]["videoMeta"]["coverUrl"], "https://cdn.example.com/tt/cover1.jpg")
        self.assertTrue(videos[2]["isPinnedItem"])
        self.assertTrue(diy_scrape_is_acceptable(profile, "tiktok"))

    def test_embed_frontity_video_list(self):
        data = _load("tiktok_embed_frontity.json")
        videos, embed_profile = parse_tiktok_embed_frontity_bundle(
            data, handle="gabcoderre", results_limit=12
        )
        self.assertEqual(len(videos), 2)
        self.assertEqual(videos[0]["text"], "Emailing brands for my birthday.")
        self.assertTrue(videos[0]["videoMeta"]["coverUrl"].endswith("cover1.jpg"))
        self.assertEqual(videos[0]["playCount"], 12000)
        self.assertGreater(videos[0]["createTime"], 0)  # snowflake from id
        self.assertIn("hello@example.com", embed_profile.get("signature", ""))
        self.assertEqual(embed_profile.get("nickname"), "Gab")
        profile = {
            "uniqueId": "gabcoderre",
            "followerCount": 33000,
            "videoCount": 882,
            "privateAccount": False,
            "signature": embed_profile.get("signature", ""),
            "latestVideos": videos,
        }
        self.assertTrue(diy_scrape_is_acceptable(profile, "tiktok"))
        # legacy helper still returns videos only
        self.assertEqual(len(parse_tiktok_embed_frontity(data, handle="gabcoderre")), 2)

    def test_embed_pinned_first_uses_newest_for_recency(self):
        """Embed omits createTime and lists pinned (older) clips first."""
        data = {
            "source": {
                "data": {
                    "/embed/@ingoodgracess": {
                        "videoList": [
                            {
                                "id": "7658013746459135245",  # ~2026-07-02
                                "desc": "pinned edit tips",
                                "coverUrl": "https://cdn.example.com/tt/old.jpg",
                                "playCount": 50000,
                            },
                            {
                                "id": "7664056196885744909",  # ~2026-07-19
                                "desc": "habits for talking on camera",
                                "coverUrl": "https://cdn.example.com/tt/new.jpg",
                                "playCount": 1200,
                            },
                        ]
                    }
                }
            }
        }
        videos = parse_tiktok_embed_frontity(data, handle="ingoodgracess")
        self.assertEqual(videos[0]["text"], "habits for talking on camera")
        self.assertGreater(videos[0]["createTime"], videos[1]["createTime"])
        self.assertTrue(videos[1]["isPinnedItem"])

        scraper = CreatorProfileScraper()
        processed = scraper.process_scrape(
            {
                "uniqueId": "ingoodgracess",
                "followerCount": 10000,
                "signature": "hi",
                "latestVideos": videos,
            },
            "tiktok",
        )
        self.assertLess(processed["latest_post_days_ago"], 30)


class TestAcceptable(unittest.TestCase):
    def test_public_without_posts_not_acceptable(self):
        profile = {
            "username": "publicbrand",
            "followersCount": 1000,
            "postsCount": 50,
            "isPrivate": False,
            "biography": "hello world brand",
            "latestPosts": [],
        }
        self.assertFalse(diy_scrape_is_acceptable(profile, "instagram"))

    def test_public_requires_followers_bio_and_latest_post(self):
        """Same required fields as TikTok DIY: followers + bio + latest post."""
        base = {
            "username": "nainadiary.jpg",
            "followersCount": 554,
            "postsCount": 12,
            "isPrivate": False,
            "biography": "Soft Girly Beauty Creator",
            "latestPosts": [{"displayUrl": "https://x", "caption": "hi"}],
        }
        self.assertTrue(diy_scrape_is_acceptable(base, "instagram"))
        no_fol = dict(base, followersCount=0)
        self.assertFalse(diy_scrape_is_acceptable(no_fol, "instagram"))
        no_bio = dict(base, biography="")
        self.assertFalse(diy_scrape_is_acceptable(no_bio, "instagram"))
        no_posts = dict(base, latestPosts=[])
        self.assertFalse(diy_scrape_is_acceptable(no_posts, "instagram"))

    def test_rejects_implausible_follower_counts(self):
        profile = {
            "username": "pl3th0ranina",
            "followersCount": 2_000_000_000,
            "isPrivate": False,
            "biography": "UGC creator @pl3th0ranina",
            "latestPosts": [],
            "_partial_scrape": True,
        }
        self.assertFalse(diy_scrape_is_acceptable(profile, "instagram", allow_partial=True))
        profile["followersCount"] = 41
        self.assertFalse(diy_scrape_is_acceptable(profile, "instagram", allow_partial=True))
        profile["biography"] = "Skincare UGC"
        self.assertTrue(diy_scrape_is_acceptable(profile, "instagram", allow_partial=True))


class TestMobileFeedHelpers(unittest.TestCase):
    def test_extract_pk_from_profile_page_marker(self):
        html = '<script>window._sharedData={"entry_data":{"ProfilePage":[{"logging_page_id":"profilePage_75919202554"}]}}</script>'
        self.assertEqual(_ig_extract_pk_from_html(html), "75919202554")

    def test_mobile_feed_item_maps_thumb_and_code(self):
        item = {
            "code": "DXMxOe7CAg1",
            "like_count": 42,
            "comment_count": 3,
            "play_count": 1200,
            "taken_at": 1710000000,
            "caption": {"text": "Soft launch"},
            "image_versions2": {
                "candidates": [{"url": "https://scontent.cdninstagram.com/v/t51/real.jpg"}]
            },
        }
        post = _ig_node_to_post(item)
        self.assertEqual(post["shortCode"], "DXMxOe7CAg1")
        self.assertEqual(post["likesCount"], 42)
        self.assertEqual(post["commentsCount"], 3)
        self.assertEqual(post["videoViewCount"], 1200)
        self.assertEqual(post["caption"], "Soft launch")
        self.assertIn("scontent.cdninstagram.com", post["displayUrl"])
        self.assertTrue(post["timestamp"])

    def test_rejects_locale_junk_shortcode(self):
        post = _ig_node_to_post({"code": "en_US", "caption": "x"})
        self.assertEqual(post["shortCode"], "")


class TestInhouseScraperWithApifyFallback(unittest.TestCase):
    @patch("services.creator_profile_scraper.diy_scrape_instagram")
    def test_instagram_uses_inhouse_scraper_when_ok(self, mock_diy):
        mock_diy.return_value = {
            "username": "okuser",
            "followersCount": 5000,
            "postsCount": 20,
            "isPrivate": False,
            "biography": "creator bio",
            "latestPosts": [{"likesCount": 1, "displayUrl": "https://x", "caption": "hi"}],
        }
        scraper = CreatorProfileScraper()
        with patch.object(scraper, "_apify_scrape_instagram") as mock_apify:
            result = scraper.scrape_instagram_profile("okuser")
            self.assertEqual(result["username"], "okuser")
            mock_diy.assert_called_once()
            mock_apify.assert_not_called()

    @patch("services.creator_profile_scraper.diy_scrape_instagram")
    def test_instagram_falls_back_to_apify(self, mock_diy):
        mock_diy.side_effect = RuntimeError("No Instagram data")
        scraper = CreatorProfileScraper()
        with patch.object(
            scraper,
            "_apify_scrape_instagram",
            return_value={
                "username": "okuser",
                "followersCount": 5000,
                "postsCount": 20,
                "isPrivate": False,
                "biography": "creator bio",
                "latestPosts": [{"likesCount": 1, "displayUrl": "https://x", "caption": "hi"}],
            },
        ) as mock_apify:
            result = scraper.scrape_instagram_profile("okuser")
            self.assertEqual(result["username"], "okuser")
            self.assertEqual(result["_scrape_source"], "apify")
            mock_apify.assert_called_once_with("okuser")

    @patch("services.creator_profile_scraper.diy_scrape_instagram")
    def test_instagram_rejects_partial_and_uses_apify(self, mock_diy):
        mock_diy.return_value = {
            "username": "pl3th0ranina",
            "followersCount": 41,
            "isPrivate": False,
            "biography": "Skincare UGC",
            "latestPosts": [],
            "_partial_scrape": True,
        }
        scraper = CreatorProfileScraper()
        scraper.apify_token = "test-token"
        with patch.object(
            scraper,
            "_apify_scrape_instagram",
            return_value={
                "username": "pl3th0ranina",
                "followersCount": 41,
                "postsCount": 5,
                "isPrivate": False,
                "biography": "Skincare UGC · Canada",
                "latestPosts": [{"likesCount": 1, "displayUrl": "https://x", "caption": "hi"}],
            },
        ) as mock_apify:
            result = scraper.scrape_instagram_profile("pl3th0ranina")
            self.assertEqual(result["_scrape_source"], "apify")
            self.assertEqual(len(result["latestPosts"]), 1)
            mock_apify.assert_called_once()

    @patch("services.creator_profile_scraper.diy_scrape_tiktok")
    def test_tiktok_uses_inhouse_scraper_when_ok(self, mock_diy):
        mock_diy.return_value = {
            "uniqueId": "ttuser",
            "followerCount": 1000,
            "videoCount": 10,
            "privateAccount": False,
            "signature": "tiktok bio",
            "latestVideos": [{"text": "hi", "createTime": 1784400000, "diggCount": 1}],
        }
        scraper = CreatorProfileScraper()
        with patch.object(scraper, "_apify_scrape_tiktok") as mock_apify:
            result = scraper.scrape_tiktok_profile("ttuser")
            self.assertEqual(result["uniqueId"], "ttuser")
            mock_diy.assert_called_once()
            mock_apify.assert_not_called()


if __name__ == "__main__":
    unittest.main()
