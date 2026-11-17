"""Cancelling during the free trial ends it now (no Pro until the trial date); paid plans still end at period end."""
import unittest
from unittest.mock import MagicMock, patch

from flask import Flask

import subscription_routes as sr


class TrialCancelFlowTests(unittest.TestCase):
    def setUp(self):
        app = Flask(__name__)
        app.register_blueprint(sr.subscription_bp, url_prefix="/api/subscription")
        self.client = app.test_client()
        self.conn, self.cursor = MagicMock(), MagicMock()

    def _post(self, status):
        creator = {"id": 7, "stripe_subscription_id": "sub_9", "subscription_status": status}
        with patch.object(sr, "_require_paid_creator", return_value=((self.conn, self.cursor, creator), None)), \
                patch("services.retention_context.load", side_effect=RuntimeError("skip")), \
                patch("services.subscription_retention.log_retention_event") as log, \
                patch("services.billing_access.end_cancelled_trial", return_value=True) as end, \
                patch.object(sr.stripe.Subscription, "modify") as modify:
            r = self.client.post("/api/subscription/cancel-flow/confirm", json={"reason": "too_expensive"})
        return r, end, modify, log

    def test_trial_ends_now(self):
        r, end, modify, log = self._post("trialing")
        body = r.get_json()
        self.assertEqual(r.status_code, 200)
        self.assertTrue(body["canceled_now"])
        self.assertFalse(body["cancel_scheduled"])
        self.assertIn("Free plan", body["message"])
        end.assert_called_once()
        self.assertEqual(end.call_args[0][2:], ("sub_9", 7))
        modify.assert_not_called()
        self.assertTrue(log.call_args.kwargs["context"]["trial_cancel"])

    def test_paid_plan_still_ends_at_period_end(self):
        with patch("services.subscription_retention.period_end_datetime", return_value=None), \
                patch("services.billing_access.set_cancel_flag"):
            r, end, modify, _ = self._post("active")
        self.assertTrue(r.get_json()["cancel_scheduled"])
        end.assert_not_called()
        modify.assert_called_once_with("sub_9", cancel_at_period_end=True)


if __name__ == "__main__":
    unittest.main()
