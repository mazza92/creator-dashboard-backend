import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

from services import pro_delivery_watch as pdw

NOW = datetime(2026, 10, 20, 12, 0, tzinfo=timezone.utc)


def _row(**kw):
    base = {"creator_id": 7, "pro_since": NOW - timedelta(days=10), "enabled": True, "gmail": True,
            "revoked_at": None, "last_sent": None, "next_slot": None, "drafts": 0}
    base.update(kw)
    return base


class TestStallReason(unittest.TestCase):
    def test_no_gmail_flags_after_grace(self):
        self.assertEqual(pdw.stall_reason(_row(gmail=False), NOW), "no_gmail")
        self.assertIsNone(pdw.stall_reason(_row(gmail=False, pro_since=NOW - timedelta(minutes=30)), NOW))
        self.assertEqual(pdw.stall_reason(_row(gmail=False, revoked_at=NOW - timedelta(days=1)), NOW),
                         "gmail_revoked")

    def test_recent_send_is_on_track(self):
        self.assertIsNone(pdw.stall_reason(_row(gmail=False, last_sent=NOW - timedelta(days=2)), NOW))

    def test_paused_and_empty_queue(self):
        self.assertEqual(pdw.stall_reason(_row(enabled=False), NOW), "paused")
        self.assertEqual(pdw.stall_reason(_row(), NOW), "nothing_queued")

    def test_queued_pitches_are_on_track(self):
        self.assertIsNone(pdw.stall_reason(_row(next_slot=NOW + timedelta(days=1)), NOW))
        self.assertIsNone(pdw.stall_reason(_row(drafts=3), NOW))


class TestFounderDigest(unittest.TestCase):
    def _conn(self, already_sent=False):
        conn = MagicMock()
        conn.cursor.return_value.fetchone.return_value = (1,) if already_sent else None
        return conn

    def test_waits_for_digest_hour(self):
        early = NOW.replace(hour=pdw.DIGEST_HOUR_UTC - 1)
        self.assertEqual(pdw.email_founder_delivery(self._conn(), now=early), [])

    def test_once_per_day(self):
        with patch("services.polly_alerts.ensure_alert_tables"), \
                patch.object(pdw, "stalled_creators") as stalled:
            self.assertEqual(pdw.email_founder_delivery(self._conn(already_sent=True), now=NOW), [])
            stalled.assert_not_called()

    def test_sends_list_to_founder(self):
        rows = [{"creator_id": 7, "reason": "no_gmail", "status": "active", "days_on_pro": 10,
                 "renews_in": 3, "ever_sent": False, "waiting": 0, "reminders": 2}]
        send = MagicMock(return_value={"success": True})
        with patch("services.polly_alerts.ensure_alert_tables"), \
                patch("services.polly_alerts._log_email") as log, \
                patch.object(pdw, "stalled_creators", return_value=rows), \
                patch.object(pdw, "founder_email", return_value="founder@example.com"):
            out = pdw.email_founder_delivery(self._conn(), send_fn=send, now=NOW)
        self.assertEqual(out[0]["stalled"], 1)
        to, subject, html = send.call_args[0][:3]
        self.assertEqual(to, "founder@example.com")
        self.assertIn("1 paying Pro creator with nothing sent", subject)
        self.assertIn("#7", html)
        self.assertIn("renews in 3d", html)
        log.assert_called_once()


if __name__ == "__main__":
    unittest.main()
