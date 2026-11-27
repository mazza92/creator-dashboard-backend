"""Out-of-free-pitches message: strongest real trigger first (services.upgrade_reasons), and every at-limit
creator gets the email (services.quota_email.quota_catchup)."""
import unittest
from unittest.mock import MagicMock

from services import quota_email as Q
from services import upgrade_reasons as U


class Compose(unittest.TestCase):
    def test_reply_leads(self):
        r = U.compose("reply", brand="Kosas", matched=24, niche="beauty", trial=True)
        self.assertEqual(r["headline"], "Kosas replied. Let's line up more like them.")
        self.assertIn("I've found 24 more that fit your beauty content", r["reason"])
        self.assertEqual(r["price"], "7 days free, then $19/mo. Cancel anytime.")
        self.assertEqual(r["cta"], "Let Polly take over")

    def test_kit_view(self):
        r = U.compose("kit_view", brand="Glow Recipe", matched=12, niche="skincare")
        self.assertEqual(r["headline"], "Glow Recipe just opened your portfolio.")
        self.assertEqual(r["price"], "$19/mo. Cancel anytime.")

    def test_hot_brands_running_ugc(self):
        r = U.compose("hot", hot=["Medicube", "Anua", "VT"], matched=20, niche="skincare")
        self.assertEqual(r["headline"], "Medicube and Anua are running UGC campaigns right now.")
        self.assertIn("lined up 18 more brands like them", r["reason"])
        one = U.compose("hot", hot=["Medicube"], matched=0)
        self.assertEqual(one["headline"], "Medicube is running UGC campaigns right now.")

    def test_general_line_matches_the_design(self):
        r = U.compose("matched", hot=["X"], matched=9, niche="travel")
        self.assertEqual(r["headline"], "Your next brand opportunities are waiting.")
        self.assertEqual(r["reason"], "I've found 9 brands that fit your travel content, including brands already "
                                      "running UGC campaigns.")
        self.assertEqual(r["body"], "You've used your free pitches. Let me take over your outreach so you can focus "
                                    "on creating.")

    def test_lead_without_its_fact_falls_back(self):
        self.assertEqual(U.compose("reply", brand=None, matched=3)["lead"], "matched")


class Subject(unittest.TestCase):
    def test_subject_is_the_headline(self):
        self.assertEqual(Q.quota_subject({"reasons": {"headline": "Glow Recipe just opened your portfolio."}}),
                         "Glow Recipe just opened your portfolio.")
        self.assertEqual(Q.quota_subject({}), "Your next brand opportunities are waiting.")


class Catchup(unittest.TestCase):
    def test_sends_to_everyone_the_trigger_missed(self):
        conn = MagicMock()
        conn.cursor.return_value.fetchall.return_value = [(11,), (12,), (13,)]
        calls = []

        def send(cid):
            calls.append(cid)
            return (cid != 12, "ok")

        out = Q.quota_catchup(conn, send=send)
        self.assertEqual(calls, [11, 12, 13])
        self.assertEqual(out, {"candidates": 3, "sent": 2})
        sql = conn.cursor.return_value.execute.call_args[0][0]
        self.assertIn("max_quota_hit", sql)
        self.assertIn("paywall_followup", sql)


if __name__ == "__main__":
    unittest.main()
