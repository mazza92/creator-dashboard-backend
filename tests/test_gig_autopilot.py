import unittest
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
        self.assertIn("fitness", ga.gig_niches(_opp(brand_category="Home Workouts, Busy Parents, Lifestyle Fitness",
                                                      creator_niches=[])))
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
        mail = ga.compose(_opp(), {"first_name": "Wajeeha", "niches": ["fitness"], "followers": 12400},
                          {"avg_views": "8.2K", "top_posts": ["https://tiktok.com/@x/video/1"]},
                          "https://newcollab.co/kit/wajeeha")
        self.assertIn("paid UGC brief (Fitness videos for structured workouts)", mail["body"])
        self.assertIn("within your $300 budget", mail["body"])
        self.assertIn("12.4K followers", mail["body"])
        self.assertIn("https://newcollab.co/kit/wajeeha", mail["body"])
        self.assertTrue(mail["body"].strip().endswith("Wajeeha"))
        self.assertIn("FitFo", mail["subject"])


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
        self.assertIn("Kylie Cosmetics replied", mail["html"])

    def test_headsup_email_is_retired(self):
        from services.email_policy import RETIRED
        self.assertIn("autopilot_headsup", RETIRED)


if __name__ == "__main__":
    unittest.main()
