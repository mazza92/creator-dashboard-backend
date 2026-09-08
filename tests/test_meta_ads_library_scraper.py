# -*- coding: utf-8 -*-
import unittest

from services.meta_ads_library_scraper import (
    detect_creator_program_from_html,
    extract_candidate_domain,
    is_qualified_brand,
    is_skip_landing_host,
    unwrap_landing_url,
)


class TestMetaAdsScraper(unittest.TestCase):
    def test_unwrap_lphp(self):
        wrapped = (
            "https://l.facebook.com/l.php?u=https%3A%2F%2Fwww.solawave.co%2Fproducts%2Fmask"
            "&h=AT123"
        )
        self.assertEqual(
            unwrap_landing_url(wrapped),
            "https://www.solawave.co/products/mask",
        )

    def test_unwrap_plain_url(self):
        self.assertEqual(unwrap_landing_url("https://covebalm.com/"), "https://covebalm.com/")

    def test_skip_amazon_and_tiktok(self):
        self.assertIsNone(
            extract_candidate_domain(
                "https://l.facebook.com/l.php?u=https%3A%2F%2Fwww.amazon.com%2Fdp%2F123"
            )
        )
        self.assertIsNone(
            extract_candidate_domain(
                "https://l.facebook.com/l.php?u=https%3A%2F%2Fwww.tiktok.com%2F"
            )
        )
        self.assertTrue(is_skip_landing_host("play.google.com"))
        self.assertTrue(is_skip_landing_host("shop.mikmak.ai"))
        self.assertTrue(is_skip_landing_host("youtu.be"))

    def test_brand_name_from_shop_subdomain(self):
        from services.meta_ads_library_scraper import brand_name_from_domain, _clean_advertiser_name
        self.assertEqual(brand_name_from_domain("shop.noyskincare.com"), "Noyskincare")
        self.assertEqual(_clean_advertiser_name("Join", "join.quiabeauty.com"), "Quiabeauty")

    def test_keep_dtc_domain(self):
        domain = extract_candidate_domain(
            "https://l.facebook.com/l.php?u=https%3A%2F%2Fwww.solawave.co%2Fproducts%2Fmask"
        )
        self.assertEqual(domain, "solawave.co")

    def test_creator_script_and_page_from_html(self):
        html = '<script src="https://cdn.socialsnowball.io/app.js"></script>'
        info = detect_creator_program_from_html(html, "qureskincare.com")
        self.assertTrue(info["has_program"])
        self.assertEqual(info["platform"], "Social Snowball")

        html2 = '<a href="/pages/ambassador">Join</a>'
        info2 = detect_creator_program_from_html(html2, "example.com")
        self.assertTrue(info2["has_program"])
        self.assertEqual(info2["program_type"], "page")
        self.assertEqual(info2["program_url"], "https://example.com/pages/ambassador")

    def test_qualified_needs_email_or_ig(self):
        self.assertTrue(is_qualified_brand({"contact_email": "a@b.com"}))
        self.assertTrue(is_qualified_brand({"instagram_handle": "brand"}))
        self.assertFalse(is_qualified_brand({"tiktok_handle": "brand"}))
        self.assertFalse(is_qualified_brand({}))


if __name__ == "__main__":
    unittest.main()
