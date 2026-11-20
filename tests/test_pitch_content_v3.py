"""Nov 2026 pitch content audit: never an invented product, subjects that reply best, who the creator is, no old
template leaking out, and paid / gifted asks given to the brands likely to say yes to them."""

import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock

from services import pitch_writer as pw
from services import product_truth as pt


class ProductTruthTests(unittest.TestCase):
    def test_trusted_sources(self):
        conn = MagicMock()
        shop = {"id": 1, "hero_product": "Glow Serum", "source": "meta_ads_library"}
        self.assertEqual(pt.verified_product(conn, shop), "Glow Serum")
        desc = {"id": 2, "hero_product": "Peptide Lip Tint", "description": "Rhode makes the Peptide Lip Tint."}
        self.assertEqual(pt.verified_product(conn, desc), "Peptide Lip Tint")
        generic = {"id": 3, "hero_product": "Designer Bikinis and One-Piece Swimwear", "source": "meta_ads_library"}
        self.assertEqual(pt.verified_product(conn, generic), "")

    def test_website_check_and_cache(self):
        conn = MagicMock()
        cur = conn.cursor.return_value
        cur.fetchone.return_value = None
        brand = {"id": 4, "hero_product": "Cloud Cream", "website": "brand.com", "description": "Skincare."}
        found = MagicMock(return_value=SimpleNamespace(status_code=200, text='{"title": "Cloud Cream 50ml"}'))
        self.assertEqual(pt.verified_product(conn, brand, http=found), "Cloud Cream")
        missing = MagicMock(return_value=SimpleNamespace(status_code=200, text="<html>Shop serums</html>"))
        self.assertEqual(pt.verified_product(conn, {**brand, "id": 5}, http=missing), "")

    def test_fallback_is_the_range(self):
        self.assertEqual(pt.range_phrase("Skincare"), "your skincare range")
        self.assertEqual(pt.range_phrase(""), "your products")
        self.assertEqual(pt.pitch_product(MagicMock(), {"hero_product": "", "category": "fashion"}),
                         {"name": "your fashion range", "verified": False})


class WriterTests(unittest.TestCase):
    def test_subjects_never_name_an_unverified_product(self):
        opts = [o["text"] for o in pw.subject_options("Rhode", "", "Maya", "skincare")]
        self.assertTrue(all("Rhode" in o for o in opts))
        self.assertNotIn("UGC for Rhode?", opts)
        with_product = [o["text"] for o in pw.subject_options("Rhode", "Peptide Lip Tint", "Maya")]
        self.assertEqual(with_product.count("Video idea for Peptide Lip Tint"), 2)  # the best replier, weighted

    def test_opener_may_not_invent_a_product(self):
        facts = {"brand": "Rhode", "product": "your skincare range", "product_verified": False}
        self.assertTrue(pw.invents_product("A 15-second video of the Barrier Repair Cream on dry skin.", facts))
        self.assertTrue(pw.invents_product("Your Glazing Fluid™ in a morning routine.", facts))
        self.assertFalse(pw.invents_product("A 15-second texture video with your skincare range.", facts))

    def test_intro_says_who_and_only_strong_numbers(self):
        self.assertEqual(pw.intro_line({"niche": "skincare", "platform": "tiktok", "followers": "2.6K"}),
                         "I'm a skincare creator on TikTok.")
        self.assertEqual(pw.intro_line({"niche": "beauty", "platform": "instagram", "followers": "12K"}),
                         "I'm a beauty creator on Instagram (12K followers).")

    def test_legacy_draft_is_scrubbed(self):
        out = pw.scrub_legacy({"subject": "Collaboration Proposal",
                               "body": "Hi X,\n\nI create beauty content for an audience of 13-17 beauty fans.\n\nWorth a look?"},
                              "Purity Woods")
        self.assertEqual(out["subject"], "Video idea for Purity Woods")
        self.assertNotIn("13-17", out["body"])

    def test_subject_must_match_the_ask(self):
        facts = {"ask": "No fee. Just product + shipping.", "first": ""}
        body = "Hi X team,\n\nIdea.\n\nNo fee. Just product + shipping.\n\nWorth a try?"
        self.assertIn("subject says paid but the ask is gifted", pw.body_issues("Paid UGC for X", body, facts))

    def test_openings_vary(self):
        facts = {"product": "your products", "product_verified": False, "avoid_openings": ["i have an idea for"]}
        self.assertIn("start differently from the creator's recent pitches",
                      pw.opener_issues("I have an idea for your products: a quick video.", facts))


class OfferAllocationTests(unittest.TestCase):
    def test_paid_asks_go_to_brands_likely_to_pay(self):
        from services import creator_tracker as ct
        orig_focus, orig_scores = ct.offer_focus, ct.brand_paid_scores
        ct.offer_focus = lambda conn, cid: "auto"
        ct.brand_paid_scores = lambda conn, ids, followers=0: {1: 3, 2: -2, 3: 0, 4: 2}
        try:
            out = ct.allocate_offers(MagicMock(), 7, [1, 2, 3, 4], share=0.5)
            self.assertEqual(out, {1: "paid", 2: "gifted", 3: "gifted", 4: "paid"})
            ct.offer_focus = lambda conn, cid: "gifted"
            self.assertEqual(set(ct.allocate_offers(MagicMock(), 7, [1, 4], share=0.9).values()), {"gifted"})
        finally:
            ct.offer_focus, ct.brand_paid_scores = orig_focus, orig_scores


if __name__ == "__main__":
    unittest.main()
