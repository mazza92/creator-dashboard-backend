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

    def test_posts_public_proxies_selected_cdn(self):
        from brand_pr_roster_routes import _posts_public

        selected = [
            {
                "post_url": "https://www.tiktok.com/@nisha/video/1",
                "thumbnail_url": "https://p16-common-sign.tiktokcdn-us.com/x.jpg",
            }
        ]
        recent = [
            {
                "post_url": "https://www.tiktok.com/@nisha/video/2",
                "thumbnail_url": "https://kyawgtojxoglvlhzsotm.supabase.co/storage/v1/object/public/creators/thumbs/nisha/a.jpg",
            }
        ]
        out = _posts_public(selected, recent)
        self.assertEqual(len(out), 2)
        self.assertIn("/api/media-proxy?url=", out[0]["thumbnail_url"])
        self.assertIn("tiktokcdn-us.com", out[0]["thumbnail_url"])
        self.assertIn("supabase.co", out[1]["thumbnail_url"])
        self.assertNotIn("media-proxy", out[1]["thumbnail_url"])

    def test_posts_public_unwraps_hosted_proxy(self):
        from urllib.parse import quote
        from brand_pr_roster_routes import _posts_public

        hosted = "https://xyz.supabase.co/storage/v1/object/public/creators/thumbs/a.jpg"
        wrapped = "https://api.newcollab.co/api/media-proxy?url=" + quote(hosted, safe="")
        out = _posts_public(
            [{"post_url": "https://www.tiktok.com/@x/video/1", "thumbnail_url": wrapped}]
        )
        self.assertEqual(out[0]["thumbnail_url"], hosted)

    def test_socials_read_proxy_selected_posts(self):
        from brand_pr_roster_routes import _socials_public

        out = _socials_public(
            {
                "selected_posts": [
                    {
                        "post_url": "https://www.tiktok.com/@nisha.agr/video/1",
                        "thumbnail_url": "https://api.newcollab.co/api/media-proxy?url=https%3A%2F%2Fcdn.example%2Fx.jpg",
                    }
                ]
            }
        )
        self.assertEqual(out[0]["platform"], "tiktok")
        self.assertIn("nisha.agr", out[0]["url"])

    def test_hydrate_copies_recent_still_without_storage(self):
        from brand_pr_roster_routes import _hydrate_selected_thumbs

        row = {
            "username": "yany_pavon",
            "selected_posts": [
                {
                    "post_url": "https://www.instagram.com/p/DcZy6hsvAJu/",
                    "thumbnail_url": "",
                }
            ],
            "recent_posts": [
                {
                    "post_url": "https://www.instagram.com/p/DcZy6hsvAJu/",
                    "thumbnail_url": "https://scontent.cdninstagram.com/v/t51.2885-15/x.jpg",
                }
            ],
        }
        self.assertTrue(_hydrate_selected_thumbs(row, recover=False))
        thumb = row["selected_posts"][0]["thumbnail_url"]
        self.assertIn("/api/media-proxy?url=", thumb)
        self.assertIn("cdninstagram.com", thumb)
        self.assertNotIn("supabase.co", thumb)
