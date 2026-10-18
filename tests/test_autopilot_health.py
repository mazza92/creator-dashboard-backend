import inspect
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

from services import autopilot_health as ah
from services import polly_alerts

NOW = datetime(2026, 10, 20, 12, 0, tzinfo=timezone.utc)


def _row(**kw):
    base = {"creator_id": 4, "subscription_status": "active", "pro_since": NOW - timedelta(days=10),
            "enabled": True, "paused_since": None, "gmail": False, "revoked_at": None, "waiting": 0,
            "gmail_warned": [], "paused_warned": []}
    base.update(kw)
    return base


class TestDueKind(unittest.TestCase):
    def test_gmail_missing_on_day_one(self):
        self.assertEqual(ah.due_kind(_row(), NOW), ah.GMAIL_KIND)
        self.assertEqual(ah.due_kind(_row(pro_since=NOW - timedelta(hours=3)), NOW), ah.GMAIL_KIND)
        self.assertIsNone(ah.due_kind(_row(pro_since=NOW - timedelta(hours=1)), NOW))

    def test_gmail_reminders_widen_then_repeat(self):
        first = NOW - timedelta(days=1)
        self.assertIsNone(ah.due_kind(_row(gmail_warned=[first]), NOW))
        self.assertEqual(ah.due_kind(_row(gmail_warned=[NOW - timedelta(days=2, hours=1)]), NOW), ah.GMAIL_KIND)
        three = [NOW - timedelta(days=30), NOW - timedelta(days=27), NOW - timedelta(days=5)]
        self.assertIsNone(ah.due_kind(_row(pro_since=NOW - timedelta(days=40), gmail_warned=three), NOW))
        four = three[:2] + [NOW - timedelta(days=20), NOW - timedelta(days=8)]
        self.assertEqual(ah.due_kind(_row(pro_since=NOW - timedelta(days=40), gmail_warned=four), NOW),
                         ah.GMAIL_KIND)

    def test_revoke_starts_a_new_episode(self):
        old = [NOW - timedelta(days=60)] * 5
        row = _row(revoked_at=NOW - timedelta(days=2), gmail_warned=old)
        self.assertEqual(ah.due_kind(row, NOW), ah.GMAIL_KIND)

    def test_trial_gmail_left_to_trial_journey(self):
        self.assertIsNone(ah.due_kind(_row(subscription_status="trialing"), NOW))
        paused_trial = _row(subscription_status="trialing", enabled=False,
                            paused_since=NOW - timedelta(days=3))
        self.assertEqual(ah.due_kind(paused_trial, NOW), ah.PAUSED_KIND)

    def test_paused_reminds_a_few_times_then_stops(self):
        since = NOW - timedelta(days=40)
        row = _row(gmail=True, enabled=False, paused_since=since)
        self.assertEqual(ah.due_kind(row, NOW), ah.PAUSED_KIND)
        self.assertIsNone(ah.due_kind(_row(gmail=True, enabled=False, paused_since=NOW - timedelta(days=1)), NOW))
        three = [since + timedelta(days=2), since + timedelta(days=9), since + timedelta(days=23)]
        self.assertIsNone(ah.due_kind(_row(gmail=True, enabled=False, paused_since=since,
                                           paused_warned=three), NOW))

    def test_healthy_or_gmail_unconfigured(self):
        self.assertIsNone(ah.due_kind(_row(gmail=True), NOW))
        self.assertIsNone(ah.due_kind(_row(), NOW, gmail_available=False))


class TestCopy(unittest.TestCase):
    def test_gmail_email(self):
        mail = ah.gmail_email("Ana", {"waiting": 7, "enabled": False})
        self.assertIn("until Gmail is connected", mail["subject"])
        self.assertIn("7 pitches</strong> are already written", mail["html"])
        self.assertIn("Autopilot is also paused", mail["html"])
        self.assertIn("autopilot=connect_gmail", mail["html"])
        revoked = ah.gmail_email("Ana", {"revoked": True})
        self.assertIn("disconnected", revoked["subject"])

    def test_paused_email(self):
        mail = ah.paused_email("Ana", {"waiting": 1, "paused_since": datetime(2026, 10, 14, tzinfo=timezone.utc)})
        self.assertIn("Autopilot is paused", mail["subject"])
        self.assertIn("Wednesday, Oct 14", mail["html"])
        self.assertIn("1 pitch</strong> is already written", mail["html"])
        self.assertIn("autopilot=resume", mail["html"])

    def test_chat_note_opens_autopilot(self):
        note = ah.chat_note(ah.GMAIL_KIND, {"waiting": 2})
        self.assertIn("Gmail isn't connected", note["text"])
        self.assertEqual(note["chips"][0]["action"], "autopilot")
        self.assertEqual(ah.chat_note(ah.PAUSED_KIND, {})["chips"][0]["label"], "Turn Autopilot back on")


class TestJob(unittest.TestCase):
    def _run(self, rows, recipient=True, recent=False, dry_run=False):
        who = {"user_id": 9, "email": "a@x.co", "first": "Ana"}
        send = MagicMock(return_value={"success": True, "message_id": "re_1"})
        note = MagicMock()
        with patch.object(ah, "_now", return_value=NOW), \
                patch.object(ah, "_candidates", return_value=rows), \
                patch.object(polly_alerts, "ensure_alert_tables"), \
                patch("services.polly_autopilot.ensure_autopilot_tables"), \
                patch("services.polly_gmail.ensure_gmail_table"), \
                patch("services.polly_gmail.gmail_configured", return_value=True), \
                patch.object(polly_alerts, "_recipient", return_value=who if recipient else None), \
                patch("services.pro_trial._billing_recipient", return_value=who) as billing, \
                patch.object(polly_alerts, "_recently_emailed", return_value=recent), \
                patch.object(polly_alerts, "_unsubscribe_url", return_value=""), \
                patch.object(polly_alerts, "_log_email") as log:
            out = ah.email_autopilot_blocked(MagicMock(), send_fn=send, note_fn=note, dry_run=dry_run)
        return out, send, note, log, billing

    def test_sends_email_and_chat_note(self):
        out, send, note, log, _ = self._run([_row(waiting=3)])
        self.assertEqual([o["kind"] for o in out], [ah.GMAIL_KIND])
        send.assert_called_once()
        note.assert_called_once()
        self.assertEqual(log.call_args[0][2], ah.GMAIL_KIND)

    def test_reaches_marketing_opt_outs(self):
        out, send, _, _, billing = self._run([_row()], recipient=False)
        self.assertEqual(len(out), 1)
        billing.assert_called_once()

    def test_respects_other_alert_gap_and_dry_run(self):
        self.assertEqual(self._run([_row()], recent=True)[0], [])
        out, send, note, _, _ = self._run([_row()], dry_run=True)
        self.assertEqual(len(out), 1)
        send.assert_not_called()
        note.assert_not_called()

    def test_in_alert_cron(self):
        self.assertIn("email_autopilot_blocked", inspect.getsource(polly_alerts.run_alerts))


if __name__ == "__main__":
    unittest.main()
