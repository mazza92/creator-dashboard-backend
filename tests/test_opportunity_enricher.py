# -*- coding: utf-8 -*-
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from services.opportunity_enricher import (
    clean_opportunity_row,
    clean_scanner_gig,
    restore_opportunity_row,
)
from services.polly_gigs import gig_card_from_opp, is_paid_listing, public_brand_site


OGEE_RAW = {
    "title": "Ogee Sculpted Face Stick - Mega Seeding",
    "buyer_name": "Ogee",
    "apply_url": (
        "https://ogee.aspireiq.com/join/sculpted-face-stick-meg-copy"
        "?utmSource=marketplace&clientId=ZDibF2yveKXLdoJuzFfLvvUs4Xxx0bx8"
    ),
    "source_platform": "aspireiq",
    "source_post_id": "ogee-sculpted-face-stick",
    "deliverable_summary": "UGC content for Ogee Sculpted Face Stick",
    "compensation_display": "Gifted PR (seeding)",
    "compensation_type": "gifted",
    "category": "Beauty, Skincare, Clean Beauty",
    "brand_website": (
        "https://ogee.aspireiq.com/join/sculpted-face-stick-meg-copy"
        "?utmSource=marketplace&clientId=ZDibF2yveKXLdoJuzFfLvvUs4Xxx0bx8"
    ),
}


class OpportunityEnricherTests(unittest.TestCase):
    def test_ogee_ingest_strips_board_chrome(self):
        cleaned = clean_scanner_gig(OGEE_RAW)
        self.assertEqual(cleaned["brand_name"], "Ogee")
        self.assertIn("Sculpted Face Stick", cleaned["product_name"])
        self.assertNotIn("Mega Seeding", cleaned["product_name"])
        self.assertIsNone(cleaned["brand_website"])
        self.assertIsNone(cleaned["pr_value_usd"])
        self.assertEqual(cleaned["pay_label"], "Gifted product")
        self.assertTrue(cleaned["gifted"])
        self.assertNotIn("Apply here", cleaned["campaign_description"])
        self.assertNotIn("aspireiq.com", cleaned["campaign_description"])
        self.assertNotIn("Pay:", cleaned["campaign_description"])
        self.assertNotIn("Gifted product", cleaned["campaign_description"])
        self.assertIn("raw", cleaned["listing_brief"])
        self.assertIn("Gifted", cleaned["listing_brief"]["raw"])

    def test_existing_raw_row_is_cleaned(self):
        apply_url = OGEE_RAW["apply_url"]
        cleaned = clean_opportunity_row({
            "id": 88,
            "brand_name": "Ogee",
            "brand_website": apply_url,
            "brand_category": "Beauty, Skincare, Clean Beauty",
            "product_name": "Ogee Sculpted Face Stick - Mega Seeding",
            "campaign_description": (
                "UGC content for Ogee Sculpted Face Stick\n\n"
                "Pay: Gifted PR (seeding) · gifted\n\n"
                f"Apply here: {apply_url}"
            ),
            "pr_value_usd": None,
            "additional_notes": (
                f"[scanner:aspireiq:{apply_url}] | source=aspireiq | apply_url={apply_url}"
            ),
        })
        self.assertEqual(cleaned["brand_name"], "Ogee")
        self.assertIsNone(cleaned["brand_website"])
        self.assertEqual(cleaned["pay_label"], "Gifted product")
        self.assertNotIn("Apply here", cleaned["campaign_description"])
        self.assertNotIn("aspireiq", cleaned["campaign_description"].lower())

    def test_paid_range_keeps_money_and_real_site(self):
        cleaned = clean_scanner_gig({
            "title": "Cat litter UGC ad",
            "buyer_name": "PrettyLitter",
            "apply_url": "https://www.upwork.com/jobs/~0123",
            "brand_website": "https://prettylitter.com",
            "deliverable_summary": "15 second TikTok review of the litter box.",
            "compensation_min_usd": 150,
            "compensation_max_usd": 300,
            "compensation_type": "paid",
        })
        self.assertEqual(cleaned["brand_website"], "https://prettylitter.com")
        self.assertEqual(cleaned["pr_value_usd"], 300)
        self.assertEqual(cleaned["pay_label"], "$150–$300")
        self.assertFalse(cleaned["gifted"])
        self.assertTrue(is_paid_listing({
            "is_sourced": True,
            "pay_label": cleaned["pay_label"],
            "pr_value_usd": cleaned["pr_value_usd"],
        }))

    def test_generic_paid_display_still_keeps_min_max(self):
        cleaned = clean_scanner_gig({
            "title": "Short Form Social Media Video Editor",
            "buyer_name": "Unknown brand",
            "apply_url": "https://www.upwork.com/jobs/~editor",
            "deliverable_summary": "Edit long-form content into short-form videos for TikTok.",
            "compensation_display": "Paid",
            "compensation_min_usd": 20,
            "compensation_max_usd": 45,
            "compensation_type": "hourly",
        })
        self.assertEqual(cleaned["pay_label"], "$20–$45/hr")
        self.assertIn("$", cleaned["listing_brief"]["pay"])

    def test_hourly_rate_in_body(self):
        cleaned = clean_scanner_gig({
            "title": "NEED VIDEOGRAPHER",
            "buyer_name": "Unknown brand",
            "apply_url": "https://newyork.craigslist.org/x/123.html",
            "deliverable_summary": "Videography and social media content creation for a fashion shoot. Hourly: $20.00-$45.00",
            "compensation_display": "paid",
        })
        self.assertEqual(cleaned["pay_label"], "$20–$45/hr")

    def test_duration_is_not_pay(self):
        from services.gig_listing import format_pay_label
        self.assertEqual(
            format_pay_label(None, "Short UGC videos (approx. 30-60 seconds each) with voiceover. paid"),
            "Paid",
        )

    def test_board_hosts_are_not_brand_sites(self):
        self.assertIsNone(public_brand_site(OGEE_RAW["apply_url"]))
        self.assertIsNone(public_brand_site("https://www.upwork.com/jobs/~abc"))
        self.assertEqual(public_brand_site("https://ogee.com"), "https://ogee.com")

    def test_polly_prefers_stored_brief(self):
        card = gig_card_from_opp({
            "id": 88,
            "brand_name": "Ogee",
            "product_name": "Ogee Sculpted Face Stick - Mega Seeding",
            "campaign_description": "Pay: Gifted PR (seeding)\n\nApply here: https://ogee.aspireiq.com/join/x",
            "brand_website": OGEE_RAW["apply_url"],
            "is_sourced": True,
            "source_platform": "aspireiq",
            "listing_brief": {
                "brand": "Ogee",
                "headline": "Sculpted Face Stick",
                "summary": "Gifted seeding for the sculpted face stick.",
                "pay": "Gifted product",
                "src": "cached",
            },
        })
        self.assertEqual(card["headline"], "Sculpted Face Stick")
        self.assertEqual(card["summary"], "Gifted seeding for the sculpted face stick.")
        self.assertEqual(card["pay_label"], "Gifted product")
        self.assertIsNone(card.get("website"))
        self.assertTrue(card.get("listing_ready"))
        self.assertFalse(is_paid_listing(card))

    def test_restore_puts_dollar_pay_back(self):
        restored = restore_opportunity_row({
            "id": 1216,
            "brand_name": "Unknown brand",
            "product_name": "NYC Sports Content Creator",
            "campaign_description": "Sports content creation, videography, editing $60/hr",
            "pr_value_usd": None,
            "listing_brief": {
                "pay": "Paid",
                "raw": "Sports content creation, videography, editing\n\n$60/hr + Paid Editing · paid",
            },
        })
        self.assertEqual(restored["pay_label"], "$60/hr")
        self.assertEqual(restored["pr_value_usd"], 60)
        self.assertNotIn("$60", restored["campaign_description"])
        self.assertIn("$60/hr", restored["listing_brief"]["raw"])

    def test_restore_unmashes_gifted_blurb(self):
        restored = restore_opportunity_row({
            "id": 1227,
            "brand_name": "Ogee",
            "product_name": "Ogee Sculpted Face Stick",
            "campaign_description": "UGC content for Sculpted Face Stick Gifted PR (seeding) · gifted Gifted product",
            "listing_brief": {
                "pay": "Gifted product",
                "raw": "UGC content for Ogee Sculpted Face Stick\n\nGifted PR (seeding) · gifted\n\nGifted product",
            },
        })
        self.assertEqual(restored["pay_label"], "Gifted product")
        self.assertNotIn("Gifted product", restored["campaign_description"])
        self.assertIn("Sculpted Face Stick", restored["campaign_description"])


if __name__ == "__main__":
    unittest.main()
