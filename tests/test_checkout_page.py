import os
import unittest
from datetime import datetime, timezone
from unittest import mock

from services.checkout_page import checkout_submit_text, embedded_ready, trial_end_date

NOW = datetime(2026, 11, 24, 10, 0, tzinfo=timezone.utc)


class SubmitText(unittest.TestCase):
    def test_trial_says_zero_today_and_the_end_date(self):
        text = checkout_submit_text(7, now=NOW)
        self.assertIn("$0 today", text)
        self.assertIn("December 1", text)
        self.assertLess(len(text), 1200)

    def test_paid_says_cancel_anytime(self):
        self.assertIn("Cancel anytime", checkout_submit_text(0, now=NOW))

    def test_trial_end_date(self):
        self.assertEqual(trial_end_date(7, now=NOW), "2026-12-01")
        self.assertIsNone(trial_end_date(0, now=NOW))


class Ready(unittest.TestCase):
    def test_needs_a_matching_publishable_key(self):
        with mock.patch.dict(os.environ, {"STRIPE_PUBLISHABLE_KEY": "", "STRIPE_SECRET_KEY": "sk_live_x"}):
            self.assertFalse(embedded_ready())
        with mock.patch.dict(os.environ, {"STRIPE_PUBLISHABLE_KEY": "pk_live_x", "STRIPE_SECRET_KEY": "sk_live_x"}):
            self.assertTrue(embedded_ready())
        with mock.patch.dict(os.environ, {"STRIPE_PUBLISHABLE_KEY": "pk_test_x", "STRIPE_SECRET_KEY": "sk_live_x"}):
            self.assertFalse(embedded_ready())

    def test_reads_the_key_from_stripe_api_key_but_never_a_secret(self):
        from services.checkout_page import publishable_key
        with mock.patch.dict(os.environ, {"STRIPE_PUBLISHABLE_KEY": "", "STRIPE_API_KEY": "pk_live_abc",
                                          "STRIPE_SECRET_KEY": "sk_live_x"}):
            self.assertEqual(publishable_key(), "pk_live_abc")
            self.assertTrue(embedded_ready())
        with mock.patch.dict(os.environ, {"STRIPE_PUBLISHABLE_KEY": "", "STRIPE_API_KEY": "sk_live_secret"}):
            self.assertEqual(publishable_key(), "")
            self.assertFalse(embedded_ready())


if __name__ == "__main__":
    unittest.main()
