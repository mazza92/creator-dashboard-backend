import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


class TestRosterEngagement(unittest.TestCase):
    def test_falls_back_to_scraped_rate(self):
        from brand_pr_roster_routes import _engagement

        n, label = _engagement(
            {
                "engagement_rate": 0,
                "avg_engagement_rate": None,
                "scraped_engagement_rate": 4.2,
            }
        )
        self.assertEqual(n, 4.2)
        self.assertEqual(label, "4.2%")

    def test_reads_social_links(self):
        from brand_pr_roster_routes import _engagement

        n, label = _engagement(
            {
                "engagement_rate": None,
                "social_links": [{"platform": "tiktok", "engagementRate": 3.1}],
            }
        )
        self.assertEqual(n, 3.1)
        self.assertEqual(label, "3.1%")

    def test_blank_when_nothing_usable(self):
        from brand_pr_roster_routes import _engagement

        self.assertEqual(_engagement({"engagement_rate": 0}), (None, None))

    def test_computes_from_tiktok_recent_posts(self):
        from brand_pr_roster_routes import _engagement

        n, label = _engagement(
            {
                "engagement_rate": 0,
                "followers_count": 1000,
                "recent_posts": [
                    {"likes": 40, "comments": 5, "shares": 5},
                    {"likes": 30, "comments": 0, "shares": 0},
                ],
            }
        )
        # avg 40 eng / 1000 followers = 4.0%
        self.assertEqual(n, 4.0)
        self.assertEqual(label, "4%")

    def test_computes_from_totals_when_no_posts(self):
        from brand_pr_roster_routes import _engagement

        n, label = _engagement(
            {
                "followers_count": 1000,
                "total_posts": 10,
                "total_likes": 300,
                "total_comments": 0,
                "total_shares": 0,
            }
        )
        self.assertEqual(n, 3.0)
        self.assertEqual(label, "3%")

    def test_computes_from_instagram_like_keys(self):
        from brand_pr_roster_routes import _engagement

        n, label = _engagement(
            {
                "followers_count": 2000,
                "recent_posts": [
                    {"likesCount": 80, "commentsCount": 20},
                ],
            }
        )
        self.assertEqual(n, 5.0)
        self.assertEqual(label, "5%")

    def test_reads_nested_tiktok_stats_and_compact_counts(self):
        from brand_pr_roster_routes import _engagement

        n, label = _engagement(
            {
                "engagement_rate": 0,
                "followers_count": 10000,
                "recent_posts": [
                    {"stats": {"diggCount": "1.2K", "commentCount": 0, "shareCount": 0}},
                    {"statistics": {"like_count": 800}},
                ],
            }
        )
        # avg 1000 eng / 10000 followers = 10%
        self.assertEqual(n, 10.0)
        self.assertEqual(label, "10%")

    def test_brand_public_maps_website_and_socials(self):
        from brand_pr_roster_routes import _brand_public

        out = _brand_public(
            {
                "brand_id": 9,
                "brand_name": "Harbor & Co.",
                "logo_url": "https://cdn.example/logo.png",
                "brand_slug": "harbor-co",
                "brand_category": "Lifestyle",
                "hero_product": "Everyday tote",
                "website": "https://www.harborandco.com",
                "instagram_handle": "@harborandco",
                "tiktok_handle": "https://www.tiktok.com/@harborandco",
                "cover_image_url": "https://cdn.example/tote.jpg",
                "brand_regions": ["US", "UK", "AU"],
            }
        )
        self.assertEqual(out["website"], "https://www.harborandco.com")
        self.assertEqual(out["instagram"], "harborandco")
        self.assertEqual(out["tiktok"], "harborandco")
        self.assertEqual(out["cover_image"], "https://cdn.example/tote.jpg")
        self.assertEqual(out["regions"], ["US", "UK", "AU"])
        self.assertEqual(out["hero_product"], "Everyday tote")
