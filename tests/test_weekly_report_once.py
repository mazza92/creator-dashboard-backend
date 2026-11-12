"""Weekly report: once a week (stamp survives thread saves), only when something happened, never in signup week."""

import unittest
from unittest.mock import MagicMock, patch

from services import pro_manager as pm


class FreeReportTrigger(unittest.TestCase):
    def build(self, last, followups=(), ready=3):
        with patch.object(pm, "last_week_facts", return_value=last), \
                patch.object(pm, "followups_due", return_value=list(followups)), \
                patch("services.polly.matched_waiting", return_value={"count": ready}):
            return pm.free_report_message(MagicMock(), 7, {"messages": []}, "Maya")

    def test_ready_brands_alone_is_not_a_report(self):
        self.assertIsNone(self.build({"pitched": 0}))

    def test_activity_makes_a_report(self):
        self.assertIsNotNone(self.build({"pitched": 2}))
        self.assertIsNotNone(self.build({"pitched": 0, "kit_names": ["Glow"]}))
        self.assertIsNotNone(self.build({"pitched": 0}, followups=[{"brand_name": "Glow", "brand_id": 1}]))


class BoardOncePerWeek(unittest.TestCase):
    def deliver(self, shown=None, signup=False):
        import routes.polly as rp
        with patch.object(rp, "_note_shown", return_value=shown), \
                patch.object(rp, "_signup_week", return_value=signup), \
                patch.object(rp, "_stamp_note") as stamp, \
                patch.object(rp, "load_thread", return_value={"messages": [], "notes": {}}) as load:
            out = rp._deliver_monday_board(MagicMock(), 7, "Maya", {}, {"is_unlimited": False})
        return out, stamp, load

    def test_already_shown_this_week(self):
        out, stamp, load = self.deliver(shown="2026-11-09")
        self.assertFalse(out)
        load.assert_not_called()

    def test_signup_week_skips_and_stamps(self):
        out, stamp, load = self.deliver(signup=True)
        self.assertFalse(out)
        stamp.assert_called_once()
        load.assert_not_called()


if __name__ == "__main__":
    unittest.main()


class UpsellTiming(unittest.TestCase):
    def test_offer_waits_until_a_pitch_is_sent(self):
        import routes.polly as rp
        with patch.object(rp, "load_thread", return_value={"messages": [], "notes": {}}), \
                patch.object(rp, "credits_out_offer_due", return_value=True), \
                patch.object(rp, "_pipeline_proof", return_value=(0, 0)), \
                patch.object(rp, "save_thread") as save:
            self.assertFalse(rp._deliver_credits_out_offer(MagicMock(), 7, "Maya", {"remaining": 0}, {}))
            save.assert_not_called()

    def test_offer_is_an_upsell_card(self):
        import routes.polly as rp
        payload = {"trial": True, "waiting": [{"brand_id": 1, "brand_name": "Mode", "meta_ads": True}],
                   "waiting_count": 24}
        with patch.object(rp, "load_thread", return_value={"messages": [], "notes": {}}), \
                patch.object(rp, "credits_out_offer_due", return_value=True), \
                patch.object(rp, "_pipeline_proof", return_value=(1, 3)), \
                patch.object(rp, "_upsell_payload", return_value=payload), \
                patch("services.quota_trial_test.trial_eligible", return_value=True), \
                patch.object(rp, "save_thread") as save:
            self.assertTrue(rp._deliver_credits_out_offer(MagicMock(), 7, "Maya", {"remaining": 0}, {}))
            msg = save.call_args.args[2][-1]
            self.assertEqual(msg["kind"], "upsell")
            self.assertEqual(msg["upsell"]["waiting_count"], 24)
