"""Gmail nudges offer 'Send without Gmail' only once the relay domain is configured."""

import unittest
from datetime import datetime, timezone
from unittest import mock

from services.autopilot_health import gmail_email
from services.pro_trial import no_gmail_option, trial_gmail_email


class NoGmailOption(unittest.TestCase):
    def test_hidden_until_relay_configured(self):
        with mock.patch.dict("os.environ", {"PITCH_RELAY_DOMAIN": "", "RESEND_API_KEY": "k"}):
            self.assertIsNone(no_gmail_option("x"))
            self.assertNotIn("Send without Gmail", gmail_email("Maya", {"waiting": 3})["html"])
            self.assertNotIn("Send without Gmail", trial_gmail_email("Maya", datetime(2026, 11, 30, tzinfo=timezone.utc))["html"])

    def test_offered_in_both_nudges_when_configured(self):
        with mock.patch.dict("os.environ", {"PITCH_RELAY_DOMAIN": "send.newcollab.co", "RESEND_API_KEY": "k"}):
            opt = no_gmail_option("autopilot_gmail")
            self.assertIn("autopilot=no_gmail", opt["button"]["href"])
            for html in (gmail_email("Maya", {"waiting": 3})["html"],
                         trial_gmail_email("Maya", datetime(2026, 11, 30, tzinfo=timezone.utc))["html"]):
                self.assertIn("Send without Gmail", html)
                self.assertIn("Connect Gmail", html)
                self.assertIn("via Newcollab", html)


if __name__ == "__main__":
    unittest.main()
