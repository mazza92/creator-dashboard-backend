import unittest
from unittest.mock import MagicMock
from datetime import datetime, timezone

from services import gig_autopilot as ga


def _opp(**kw):
    base = {"id": 1, "brand_name": "FitFo", "brand_email": "support@fitfo.app", "brand_category": "Fitness",
            "creator_niches": ["Fitness"], "campaign_description": "Fitness videos for structured workouts.",
            "pr_value_usd": 300, "listing_brief": {"pay": "$300", "summary": "Fitness videos for structured workouts"},
            "follower_ranges": [], "product_name": None}
    base.update(kw)
    return base


class GigNiches(unittest.TestCase):
    def test_free_text_maps_to_our_niches(self):
        niches = ga.gig_niches(_opp(brand_category="Home Workouts, Busy Parents, Lifestyle Fitness", creator_niches=[]))
        self.assertIn("fitness", niches)
        self.assertNotIn("baby", niches)

    def test_description_words_and_vague_labels_dont_match(self):
        tech = _opp(brand_category="tech", creator_niches=[], campaign_description="using your own account and style")
        self.assertFalse(ga.fits(tech, ["beauty", "fashion"], 5000))
        boat = _opp(brand_category="lifestyle", creator_niches=[], campaign_description="boat transport")
        self.assertFalse(ga.fits(boat, ["beauty", "lifestyle"], 5000))
        scanner = _opp(brand_category="Fitness, Nutrition, Body Recomposition Content", creator_niches=[])
        self.assertFalse(ga.fits(scanner, ["wellness", "skincare"], 5000))
        self.assertIn("haircare", ga.gig_niches(_opp(brand_category="Haircare, Scalp Health", creator_niches=[],
                                                      campaign_description="")))

    def test_strict_niche_fit(self):
        self.assertTrue(ga.fits(_opp(), ["fitness"], 5000))
        self.assertFalse(ga.fits(_opp(), ["skincare", "beauty"], 5000))

    def test_sensitive_briefs_never(self):
        self.assertFalse(ga.fits(_opp(brand_name="Cornblock: Quit Adult Content", brand_category="wellness",
                                      creator_niches=[], campaign_description="daily videos"), ["wellness"], 5000))


class Compose(unittest.TestCase):
    def test_pitch_names_brief_budget_and_proof(self):
        mail = ga.compose(_opp(), {"first_name": "wajeeha", "niches": ["fitness"], "followers": 12400},
                          {"avg_views": "8.2K", "avg_views_n": 8200, "top_posts": ["https://tiktok.com/@x/video/1"]},
                          "https://newcollab.co/kit/wajeeha")
        self.assertIn("I saw your paid UGC brief for FitFo", mail["body"])
        self.assertIn("within your $300 budget", mail["body"])
        self.assertIn("12.4K followers", mail["body"])
        self.assertIn("https://newcollab.co/kit/wajeeha", mail["body"])
        self.assertTrue(mail["body"].strip().endswith("Wajeeha"))
        self.assertIn("FitFo", mail["subject"])
        self.assertIn("8.2K median views", mail["body"])

    def test_weak_views_are_left_out(self):
        mail = ga.compose(_opp(), {"first_name": "jess", "niches": ["fitness"], "followers": 7200},
                          {"avg_views": "22", "avg_views_n": 22, "top_posts": ["https://tiktok.com/@x/video/1"]})
        self.assertNotIn("median views", mail["body"])
        self.assertNotIn("Recent video", mail["body"])
        self.assertTrue(mail["body"].strip().endswith("Jess"))


class Reports(unittest.TestCase):
    def test_renewal_note_shows_hours_gigs_kit_and_asks_about_replies(self):
        from services.pro_manager import renewal_lines
        lines = renewal_lines({"pitched": 70, "followups": 95, "kit_views": 5, "replies": 0},
                              datetime(2026, 11, 16, tzinfo=timezone.utc), ["Velthentic"],
                              gigs=[{"brand_name": "FitFo", "pay": "$300"}], kit_names=["Drunk Elephant", "Torriden"])
        text = "\n".join(lines)
        self.assertIn("about **19 hours**", text)
        self.assertIn("**FitFo** ($300)", text)
        self.assertIn("Drunk Elephant", text)
        self.assertIn("did any brand reply?", text)
        self.assertNotIn("No replies yet", text)

    def test_weekly_report_lists_followups_with_reply_buttons(self):
        from services.pro_manager import board_email
        mail = board_email("Ana", {"report": [], "tasks": []}, followups=[
            {"brand_id": 4, "brand_name": "Kylie Cosmetics"}, {"brand_id": 5, "brand_name": "Biossance"}])
        self.assertIn("This week I follow up with", mail["html"])
        self.assertIn("I heard back from Kylie Cosmetics", mail["html"])

    def test_headsup_email_is_retired(self):
        from services.email_policy import RETIRED
        self.assertIn("autopilot_headsup", RETIRED)


if __name__ == "__main__":
    unittest.main()


class GigBrandNameClash(unittest.TestCase):
    """Nov 26: gig 2013 'Cloud Nine' (cloudnine.mn) failed every cron run for every creator because the directory
    already has Cloud Nine (cloudnine.co.uk) and brand_name is unique."""

    def _cur(self, existing):
        cur = MagicMock()
        cur.fetchone.side_effect = [existing, (99,)]
        return cur

    def test_other_company_same_name_gets_its_own_hidden_row(self):
        from services.gig_autopilot import _insert_gig_brand
        cur = self._cur((715, "hello@cloudnine.co.uk"))
        bid = _insert_gig_brand(cur, {"id": 2013, "brand_name": "Cloud Nine", "brand_email": "hr@cloudnine.mn"})
        self.assertEqual(bid, 99)
        self.assertEqual(cur.execute.call_args_list[-1][0][1][0], "Cloud Nine (gig 2013)")

    def test_same_company_reuses_the_directory_brand(self):
        from services.gig_autopilot import _insert_gig_brand
        cur = self._cur((715, "pr@cloudnine.co.uk"))
        bid = _insert_gig_brand(cur, {"id": 7, "brand_name": "Cloud Nine", "brand_email": "ugc@cloudnine.co.uk"})
        self.assertEqual(bid, 715)
        self.assertEqual(cur.execute.call_count, 1)

    def test_new_name_inserts_as_is(self):
        from services.gig_autopilot import _insert_gig_brand
        cur = self._cur(None)
        _insert_gig_brand(cur, {"id": 8, "brand_name": "Lume", "brand_email": "a@lume.com"})
        self.assertEqual(cur.execute.call_args_list[-1][0][1][0], "Lume")
