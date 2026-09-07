import unittest

from services.opportunity_gifted_pr import (
    apply_url_for_brand,
    category_from_opportunity,
    is_brand_submission,
    min_followers_from_ranges,
    niches_from_opportunity,
    platforms_from_opportunity,
    slugify_brand,
    _website_host,
)


class TestBrandSubmissionGate(unittest.TestCase):
    def test_form_submit_is_inbound(self):
        self.assertTrue(is_brand_submission({
            "additional_notes": "apply_url=https://us.glo910.com/ | email=ixone@glo910.com",
        }))

    def test_scanner_gig_stays_on_open_gigs(self):
        self.assertFalse(is_brand_submission({
            "additional_notes": "[scanner:aspireiq:abc] | apply_url=https://x.com",
        }))


class TestOpportunityMapping(unittest.TestCase):
    def test_slugify(self):
        self.assertEqual(slugify_brand("GLO 910"), "glo-910")

    def test_min_followers(self):
        self.assertEqual(min_followers_from_ranges(["1K-10K", "10K-50K"]), 1000)
        self.assertEqual(min_followers_from_ranges(["50K+"]), 50000)
        self.assertEqual(min_followers_from_ranges(None), 0)

    def test_niches_include_category(self):
        niches = niches_from_opportunity({
            "creator_niches": ["skincare", "beauty"],
            "brand_category": "Beauty",
        })
        self.assertIn("skincare", niches)
        self.assertTrue(any(n.lower() == "beauty" for n in niches))

    def test_platforms_from_content(self):
        self.assertEqual(
            platforms_from_opportunity({"content_types": ["TikTok", "Reel"]}),
            ["tiktok", "instagram"],
        )

    def test_category_fallback(self):
        self.assertEqual(
            category_from_opportunity({"brand_category": "other", "creator_niches": ["Parenting"]}),
            "Parenting",
        )

    def test_apply_url(self):
        self.assertEqual(
            apply_url_for_brand("glo"),
            "https://app.newcollab.co/creator/dashboard/for-you?brand=glo",
        )

    def test_website_host_skips_marketplaces(self):
        self.assertEqual(_website_host("https://us.glo910.com/shop"), "us.glo910.com")
        self.assertEqual(_website_host("https://www.amazon.com/stores/page/abc"), "")


if __name__ == "__main__":
    unittest.main()
