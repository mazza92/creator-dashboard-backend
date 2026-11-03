import unittest
from unittest.mock import MagicMock, patch

from app import app


class TrialOfferInvitedTests(unittest.TestCase):
    def _get(self, eligible, offer=False, outreach=False):
        conn = MagicMock()
        with patch("subscription_routes.get_creator_id_from_session", return_value=7), \
                patch("subscription_routes.get_db_connection", return_value=conn), \
                patch("services.quota_trial_test.checkout_trial_eligible", return_value=eligible), \
                patch("services.creator_offers.has_trial_offer", return_value=offer), \
                patch("services.quota_trial_test.outreach_trial_eligible", return_value=outreach):
            return app.test_client().get("/api/subscription/trial-offer").get_json()

    def test_link_signup_is_invited(self):
        self.assertEqual(self._get(True, offer=True), {"trial_days": 7, "invited": True})

    def test_cold_email_match_is_invited(self):
        self.assertTrue(self._get(True, outreach=True)["invited"])

    def test_experiment_trial_is_not_invited(self):
        self.assertEqual(self._get(True), {"trial_days": 7, "invited": False})

    def test_no_trial(self):
        self.assertEqual(self._get(False, offer=True), {"trial_days": 0, "invited": False})


if __name__ == "__main__":
    unittest.main()
