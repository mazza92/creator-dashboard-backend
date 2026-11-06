"""Upgrade-moment emails: a brand replied, saved brands waiting, brands gifting in their niche."""

import unittest
from unittest import mock

from services import moment_emails as me


class _Conn:
    def rollback(self):
        pass


FACTS = {5: {"creators_30d": 4, "avg_product_value": 45, "logo_url": ""}}


class MomentEmailTests(unittest.TestCase):
    def test_reply_yes_email(self):
        mail = me.reply_email("Ana", "Glow Co", "brand_replied_interested", False, "https://x/reply", "u")
        self.assertEqual(mail["subject"], "Glow Co said yes 🎉 Here's your reply")
        self.assertIn("Write my reply to Glow Co", mail["html"])
        self.assertIn("On <strong>Pro ($19/mo)</strong> I draft every brand reply", mail["html"])
        pro = me.reply_email("Ana", "Glow Co", "brand_replied_question", True, "https://x/reply", "u")
        self.assertIn("replied with a question", pro["subject"])
        self.assertNotIn("$19/mo", pro["html"])

    def test_saved_brands_email_cards_and_pitch_all(self):
        rows = [{"brand_id": 5, "brand_name": "Glow Co"}, {"brand_id": 6, "brand_name": "Byoma"},
                {"brand_id": 7, "brand_name": "Calm"}, {"brand_id": 8, "brand_name": "Dewy"}]
        with mock.patch("services.polly_alerts.brief_brand_facts", return_value=FACTS):
            mail = me.saved_email(_Conn(), "Ana", rows, "u")
        self.assertEqual(mail["subject"], "Your 4 saved brands are ready to pitch")
        self.assertEqual(mail["html"].count(">Pitch it</a>"), 3)
        self.assertIn("Plus 1 more saved.", mail["html"])
        self.assertIn("chip=pitch_targets", mail["html"])
        self.assertIn("Gifted 4 creators this month", mail["html"])

    def test_niche_email(self):
        rows = [{"brand_id": 5, "brand_name": "Glow Co"}, {"brand_id": 6, "brand_name": "Byoma"}]
        with mock.patch("services.polly_alerts.brief_brand_facts", return_value=FACTS):
            mail = me.niche_email(_Conn(), "Ana", "skincare", rows, False, "u")
        self.assertEqual(mail["subject"], "2 skincare brands started gifting creators this week")
        self.assertIn("/creator/dashboard/pr-brands", mail["html"])
        self.assertIn("I pitch brands like these", mail["html"])


class FirstPitchTests(unittest.TestCase):
    def test_first_pitch_email(self):
        rows = [{"brand_id": 5, "brand_name": "Glow Co"}, {"brand_id": 6, "brand_name": "Byoma"}]
        with mock.patch("services.polly_alerts.brief_brand_facts", return_value=FACTS):
            mail = me.first_pitch_email(_Conn(), "Ana", rows, "u")
        self.assertEqual(mail["subject"], "Your 3 brand matches are still waiting")
        self.assertIn("utm_campaign=first_pitch", mail["html"])
        self.assertEqual(mail["html"].count(">Pitch it</a>"), 2)


class MomentEmailsInCronTests(unittest.TestCase):
    def test_registered_in_priority_order(self):
        import inspect
        from services import polly_alerts
        src = inspect.getsource(polly_alerts.run_alerts)
        self.assertLess(src.index("email_reply_wins"), src.index("email_unsent_briefs"))
        self.assertLess(src.index("email_saved_brands"), src.index("email_paywall_followups"))
        self.assertLess(src.index("email_niche_gifting"), src.index("email_paywall_followups"))


if __name__ == "__main__":
    unittest.main()
