"""Brand audience vs scrape: drop men's-only brands without men's content proof."""

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from services.audience_fit import (
    audience_mismatch,
    brand_gender_lane,
    category_mismatch,
    creator_gender_lane,
    match_why,
    opportunity_mismatch,
    too_big_to_reply,
)


BENJI = {
    "name": "Benji Mens",
    "category": "skincare",
    "description": "We're looking for men 35-55 to talk about our face wash & moisturizer.",
    "target_audience": "men 35-55",
    "hero_product": "face wash",
}

BEAUTY_PIE = {
    "name": "Beauty Pie",
    "category": "beauty",
    "description": "Treat yourself better. Join hundreds of thousands of beauty insiders already enjoying premium skincare.",
}

UNISEX = {
    "name": "Clean Formula",
    "category": "skincare",
    "description": "Face wash for men and women who want a simple routine.",
    "target_audience": "men and women 25-45",
}

LIFESTYLE_SCRAPE = {
    "primary_niche": "lifestyle",
    "raw_bio": "UGC creator. Lifestyle and self-care.",
    "content_themes": ["lifestyle", "daily routine"],
    "recent_captions": ["sunday reset", "evening wind down"],
}

MENS_SCRAPE = {
    "primary_niche": "fitness",
    "raw_bio": "he/him. Dad and husband sharing gym and grooming.",
    "content_themes": ["fitness", "men's grooming"],
    "recent_captions": ["beard trim after the gym"],
}


class AudienceFitTests(unittest.TestCase):
    def test_benji_is_mens_brand(self):
        self.assertEqual(brand_gender_lane(BENJI), "men")
        self.assertIsNone(creator_gender_lane(LIFESTYLE_SCRAPE))

    def test_drops_benji_for_lifestyle_creator(self):
        self.assertTrue(audience_mismatch(BENJI, LIFESTYLE_SCRAPE))
        self.assertFalse(audience_mismatch(BEAUTY_PIE, LIFESTYLE_SCRAPE))

    def test_keeps_benji_for_mens_creator(self):
        self.assertFalse(audience_mismatch(BENJI, MENS_SCRAPE))

    def test_unisex_not_dropped(self):
        self.assertEqual(brand_gender_lane(UNISEX), "both")
        self.assertFalse(audience_mismatch(UNISEX, LIFESTYLE_SCRAPE))

    def test_name_alone_is_enough(self):
        self.assertTrue(audience_mismatch({"name": "Benji Mens", "category": "skincare"}, LIFESTYLE_SCRAPE))

    def test_why_does_not_copy_men_targeting(self):
        why = match_why(BEAUTY_PIE, LIFESTYLE_SCRAPE)
        self.assertTrue(why)
        self.assertNotIn("men", why.lower())
        self.assertNotIn("already sit in", why.lower())

    def test_why_is_brand_specific_not_token_dump(self):
        scrape = {"primary_niche": "skincare", "raw_bio": "skincare journey beauty texture"}
        a = match_why({
            "name": "The Peace Labs",
            "category": "skincare",
            "description": "Gentle, fragrance-free formulas for reactive skin.",
            "hero_product": "oat cleanser",
        }, scrape)
        b = match_why({
            "name": "Wholesome Hippy",
            "category": "skincare",
            "description": "Plant-first body care made in small batches.",
            "hero_product": "body oil",
        }, scrape)
        self.assertNotEqual(a, b)
        self.assertNotIn("journey", a.lower())
        self.assertNotIn("already sit in", a.lower())
        self.assertIn("oat cleanser", a.lower())

    def test_why_flags_open_roster(self):
        why = match_why({
            "name": "Acure",
            "category": "skincare",
            "source": "recruiting",
        }, {"primary_niche": "skincare"})
        self.assertIn("open pr roster", why.lower())

    def test_for_you_skip_uses_audience_fit(self):
        from pr_crm_routes import _for_you_should_skip_brand
        self.assertTrue(_for_you_should_skip_brand(BENJI, ["lifestyle"], LIFESTYLE_SCRAPE))
        self.assertFalse(_for_you_should_skip_brand(BEAUTY_PIE, ["lifestyle", "beauty"], LIFESTYLE_SCRAPE))
        self.assertFalse(_for_you_should_skip_brand(BENJI, ["fitness"], MENS_SCRAPE))

    def test_skincare_micro_does_not_match_on_running(self):
        scrape = {
            "primary_niche": "skincare",
            "secondary_niches": ["wellness"],
            "follower_count": 202,
            "raw_bio": "skincare and wellness UGC",
        }
        on_running = {
            "name": "On Running",
            "category": "fitness",
            "description": "Born in the Swiss Alps, On running shoes feature patented cushioning.",
            "match_score": 66,
            "source": "recruiting",
            "roster_is_open": 1,
        }
        sentix = {
            "name": "Sentix Cosmetics (US)",
            "category": "skincare",
            "description": "Clean, vegan, cruelty-free beauty products.",
            "micro_friendly": True,
        }
        self.assertTrue(category_mismatch(on_running, scrape, ["skincare", "wellness"], ["skincare", "beauty"]))
        self.assertTrue(too_big_to_reply(on_running, scrape))
        self.assertTrue(opportunity_mismatch(on_running, scrape, ["skincare", "wellness"], ["skincare", "beauty"]))
        self.assertFalse(opportunity_mismatch(sentix, scrape, ["skincare", "wellness"], ["skincare", "beauty"]))

    def test_established_runner_can_see_on_running(self):
        scrape = {"primary_niche": "fitness", "follower_count": 85000}
        on_running = {"name": "On Running", "category": "fitness"}
        self.assertFalse(opportunity_mismatch(on_running, scrape, ["fitness"], ["fitness", "activewear"]))


if __name__ == "__main__":
    unittest.main()
