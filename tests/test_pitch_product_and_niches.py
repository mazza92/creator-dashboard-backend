"""Niche-fit SKU picker, generic inboxes, and parenting category mapping."""

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from services.gemini_pitch_generator_v2 import is_generic_inbox, select_pitch_product
from services.fit_score_calculator import _mapped_category


class TestGenericInbox(unittest.TestCase):
    def test_help_and_info_are_generic(self):
        self.assertTrue(is_generic_inbox("help@eydology.com"))
        self.assertTrue(is_generic_inbox("info@brand.com"))
        self.assertTrue(is_generic_inbox("support@brand.com"))

    def test_pr_inbox_is_not_generic(self):
        self.assertFalse(is_generic_inbox("pr@brand.com"))
        self.assertFalse(is_generic_inbox("sarah@brand.com"))
        self.assertFalse(is_generic_inbox(None))


class TestSelectPitchProduct(unittest.TestCase):
    def test_skips_optical_sku_for_beauty_parenting_creator(self):
        sku = select_pitch_product(
            {
                "brand_name": "Eydology",
                "hero_product": "Prescription Glasses Collection",
                "hero_products": ["Niacinamide Serum", "Prescription Glasses Collection"],
            },
            {"niches": ["Beauty", "Baby & Parenting"], "primary_niche": "beauty"},
        )
        self.assertEqual(sku, "Niacinamide Serum")

    def test_falls_back_to_brand_products_when_only_off_niche_sku(self):
        sku = select_pitch_product(
            {"brand_name": "Eydology", "hero_product": "Prescription Glasses Collection"},
            {"niches": ["beauty", "parenting"]},
        )
        self.assertEqual(sku, "Eydology products")

    def test_keeps_optical_sku_for_fashion_creator(self):
        sku = select_pitch_product(
            {"brand_name": "Eydology", "hero_product": "Prescription Glasses Collection"},
            {"niches": ["fashion"], "primary_niche": "fashion"},
        )
        self.assertEqual(sku, "Prescription Glasses Collection")


class TestParentingCategoryMapping(unittest.TestCase):
    def test_parenting_does_not_collapse_to_lifestyle(self):
        self.assertEqual(_mapped_category("parenting"), "parenting")
        self.assertEqual(_mapped_category("family"), "parenting")
        self.assertEqual(_mapped_category("skincare"), "beauty")
        self.assertEqual(_mapped_category("Baby & Parenting"), "parenting")
        self.assertEqual(_mapped_category("baby & parenting"), "parenting")


if __name__ == "__main__":
    unittest.main()
