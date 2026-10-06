import base64
import os
import unittest
from datetime import datetime, timezone
from email import message_from_bytes
from random import Random
from unittest import mock

from services.polly_autopilot import WEEK_BATCH, batch_size, compose_followup, next_slots
from services.polly_gmail import build_raw, gmail_configured, make_state, read_state


class SlotTests(unittest.TestCase):
    def test_one_weekday_slot_per_day_in_window(self):
        friday = datetime(2026, 10, 9, 18, 0, tzinfo=timezone.utc)
        slots = next_slots(3, friday, rng=Random(1))
        self.assertEqual([s.strftime("%a %d") for s in slots], ["Mon 12", "Tue 13", "Wed 14"])
        self.assertTrue(all(14 <= s.hour < 17 for s in slots))

    def test_today_counts_when_window_not_passed(self):
        tuesday_morning = datetime(2026, 10, 6, 8, 0, tzinfo=timezone.utc)
        self.assertEqual(next_slots(1, tuesday_morning, rng=Random(2))[0].day, 6)


class BatchTests(unittest.TestCase):
    def test_week_batch_capped_by_month_target(self):
        self.assertEqual(batch_size({"sent": 0, "scheduled": 0, "drafts": 0}, 24), WEEK_BATCH)
        self.assertEqual(batch_size({"sent": 18, "scheduled": 2, "drafts": 1}, 24), 3)
        self.assertEqual(batch_size({"sent": 24}, 24), 0)
        self.assertEqual(batch_size({"sent": 0}, 12), 3)
        self.assertEqual(batch_size({"sent": 11}, 12), 1)


class FollowupCopyTests(unittest.TestCase):
    def test_reply_subject_and_signoff(self):
        mail = compose_followup("Ana", "Glow", "Glow x Ana collab")
        self.assertEqual(mail["subject"], "Re: Glow x Ana collab")
        self.assertIn("create for Glow", mail["body"])
        self.assertTrue(mail["body"].endswith("Ana"))
        self.assertEqual(compose_followup("", "Glow", "Re: hi")["subject"], "Re: hi")


class GmailMessageTests(unittest.TestCase):
    def test_raw_threads_follow_up(self):
        built = build_raw("ana@gmail.com", "Ana Lee", "pr@glow.com", "Re: Glow collab", "Hi",
                          in_reply_to="<abc@gmail.com>")
        msg = message_from_bytes(base64.urlsafe_b64decode(built["raw"]))
        self.assertEqual(msg["To"], "pr@glow.com")
        self.assertEqual(msg["From"], "Ana Lee <ana@gmail.com>")
        self.assertEqual(msg["In-Reply-To"], "<abc@gmail.com>")
        self.assertEqual(msg["References"], "<abc@gmail.com>")
        self.assertEqual(msg["Message-ID"], built["message_id"])

    def test_state_round_trip_and_tamper(self):
        with mock.patch.dict(os.environ, {"JWT_SECRET_KEY": "test-secret"}):
            state = make_state(42)
            self.assertEqual(read_state(state), 42)
            self.assertIsNone(read_state(state[:-2] + "xx"))
            self.assertIsNone(read_state(""))

    def test_not_configured_without_env(self):
        keys = ("GOOGLE_OAUTH_CLIENT_ID", "GOOGLE_OAUTH_CLIENT_SECRET", "GOOGLE_OAUTH_REDIRECT_URI",
                "POLLY_GMAIL_TOKEN_KEY", "SOCIAL_TOKEN_ENCRYPTION_KEY")
        with mock.patch.dict(os.environ, {k: "" for k in keys}):
            self.assertFalse(gmail_configured())


if __name__ == "__main__":
    unittest.main()
