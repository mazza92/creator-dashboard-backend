"""Nov 2026: most Polly 'chat errors' were sends from Google accounts with no Gmail inbox. Those now go out via
Newcollab (reply-to the creator), and only sends that actually broke count as errors."""
import unittest
from unittest.mock import MagicMock, patch

import routes.polly as R
from app import app
from services.polly_gmail import NO_INBOX, GmailError
from tests.test_first_session_wow import FakeConn


def _notes():
    return {"pitch_batch": {"items": [{"brand_id": 1, "brand_name": "Glow", "email": "pr@glow.com",
                                       "subject": "S", "body": "Hi"}]}}


class NoInboxRelayTests(unittest.TestCase):
    def _post(self, account, gmail_send, relay_send, relay_on=True, via="gmail"):
        with patch.object(R, "_creator_auth", return_value=(7, FakeConn(), {"first_name": "Maya"})), \
                patch("services.polly_gmail.gmail_configured", return_value=True), \
                patch("services.polly_gmail.load_account", return_value=account), \
                patch("services.polly_gmail.send_email", gmail_send), \
                patch("services.pitch_relay.send_email", relay_send), \
                patch("services.pitch_relay.relay_available", return_value=relay_on), \
                patch("services.pitch_relay.send_via", return_value=via), \
                patch("services.pitch_relay.creator_reply_to", return_value="maya@aol.com"), \
                patch.object(R, "load_thread", return_value={"notes": _notes()}), \
                patch("services.polly_memory.save_notes"), \
                patch("services.polly_tracker.record_pitch_sent"), \
                patch.object(R, "_kit_rates", return_value=(1500, True)), \
                patch.object(R, "_creator_is_pro", return_value=True), \
                patch("services.polly_autopilot.track_gmail_send"), \
                patch("services.polly_alerts.record_handoff"), \
                patch("services.polly_usage.log_usage") as log:
            res = app.test_client().post("/api/polly/pitch-batch/send", json={})
        return res, log

    def test_no_inbox_error_mid_send_falls_back_to_newcollab(self):
        gmail = MagicMock(side_effect=GmailError("This Google account has no Gmail inbox", revoked=True))
        relay = MagicMock(return_value={"gmail_id": "relay:x", "thread_id": "", "message_id": "<m>"})
        res, log = self._post({"email": "maya@aol.com"}, gmail, relay)
        data = res.get_json()
        self.assertEqual(data["results"][0]["status"], "sent")
        self.assertEqual(data["results"][0]["via"], "newcollab")
        relay.assert_called_once()
        self.assertTrue(log.call_args.kwargs["ok"])
        self.assertEqual(log.call_args.kwargs["meta"]["via"], "newcollab")

    def test_account_already_marked_no_inbox_skips_gmail(self):
        gmail = MagicMock()
        relay = MagicMock(return_value={"gmail_id": "relay:x", "thread_id": "", "message_id": "<m>"})
        res, _log = self._post({"email": "maya@aol.com", "revoked_at": "2026-11-24", "last_error": NO_INBOX},
                               gmail, relay)
        self.assertEqual(res.status_code, 200)
        gmail.assert_not_called()
        relay.assert_called_once()

    def test_revoked_gmail_without_relay_still_asks_to_connect(self):
        res, _log = self._post({"email": "maya@gmail.com", "revoked_at": "2026-11-24", "last_error": "expired"},
                               MagicMock(), MagicMock())
        self.assertEqual(res.status_code, 409)

    def test_held_pitches_are_not_errors(self):
        gmail = MagicMock()
        with patch("services.polly_autopilot.send_room_today", return_value=0):
            res, log = self._post({"email": "maya@gmail.com"}, gmail, MagicMock())
        self.assertEqual(res.get_json()["results"][0]["status"], "daily_cap")
        self.assertTrue(log.call_args.kwargs["ok"])
        self.assertEqual(log.call_args.kwargs["meta"]["statuses"], {"daily_cap": 1})

    def test_a_broken_send_is_an_error(self):
        gmail = MagicMock(side_effect=GmailError("send failed: 500"))
        res, log = self._post({"email": "maya@gmail.com"}, gmail, MagicMock())
        self.assertEqual(res.get_json()["results"][0]["status"], "failed")
        self.assertFalse(log.call_args.kwargs["ok"])


if __name__ == "__main__":
    unittest.main()
