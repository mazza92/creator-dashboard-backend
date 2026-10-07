import unittest
from unittest.mock import MagicMock, patch

from services import polly_alerts
from services.waitlist_gate import is_waitlisted_email, is_waitlisted_status


def _conn_returning(row):
    cur = MagicMock()
    cur.fetchone.return_value = row
    conn = MagicMock()
    conn.cursor.return_value = cur
    return conn, cur


def _creator(status):
    return {
        "creator_id": 7, "user_id": 70, "email": "c@example.com", "first_name": "seynab urur",
        "subscription_tier": "free", "unlocks_tier": None, "unsubscribed_at": None,
        "approval_status": status,
    }


class WaitlistStatusTests(unittest.TestCase):
    def test_pending_and_rejected_are_waitlisted(self):
        self.assertTrue(is_waitlisted_status("pending"))
        self.assertTrue(is_waitlisted_status("Rejected"))

    def test_approved_and_legacy_creators_are_not(self):
        for status in ("approved", "pro_approved", None, ""):
            self.assertFalse(is_waitlisted_status(status))

    def test_email_lookup(self):
        _, cur = _conn_returning((1,))
        self.assertTrue(is_waitlisted_email(cur, "C@example.com "))
        self.assertIn("approval_status IN ('pending', 'rejected')", cur.execute.call_args[0][0])
        _, cur = _conn_returning(None)
        self.assertFalse(is_waitlisted_email(cur, "c@example.com"))
        self.assertFalse(is_waitlisted_email(cur, ""))


class PollyRecipientTests(unittest.TestCase):
    def test_pending_creator_gets_no_polly_email(self):
        conn, _ = _conn_returning(_creator("pending"))
        self.assertIsNone(polly_alerts._recipient(conn, 7))

    def test_rejected_creator_gets_no_polly_email(self):
        conn, _ = _conn_returning(_creator("rejected"))
        self.assertIsNone(polly_alerts._recipient(conn, 7))

    def test_approved_creator_still_emailed(self):
        conn, _ = _conn_returning(_creator("approved"))
        who = polly_alerts._recipient(conn, 7)
        self.assertEqual(who["email"], "c@example.com")
        self.assertEqual(who["first"], "Seynab")

    def test_intro_query_excludes_waitlist(self):
        conn = MagicMock()
        cur = MagicMock()
        cur.fetchall.return_value = []
        conn.cursor.return_value = cur
        with patch("services.polly_alerts.ensure_alert_tables"), \
             patch("services.polly_usage._cursor"):
            polly_alerts.email_never_opened(conn, dry_run=True)
        sql = cur.execute.call_args[0][0]
        self.assertIn("NOT IN ('pending', 'rejected')", sql)


class CronTemplateEmailTests(unittest.TestCase):
    def test_waitlisted_recipient_is_skipped_before_sending(self):
        import email_cron_routes

        with patch.object(email_cron_routes, "_recipient_waitlisted", return_value=True), \
             patch.object(email_cron_routes.smtplib, "SMTP") as smtp:
            ok, err = email_cron_routes.send_template_email("c@example.com", "x.html", "Hi", {})
        self.assertFalse(ok)
        self.assertIn("Waitlisted", err)
        smtp.assert_not_called()


if __name__ == "__main__":
    unittest.main()
