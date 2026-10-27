import inspect
import unittest
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

from services import polly_alerts
from services import pro_upsell as pu

FACTS = {"pitched": 2, "left": 1, "matched": 5, "brand_names": ["Glossier", "Tower 28", "Rhode"]}


class TestCopy(unittest.TestCase):
    def test_pro_offer(self):
        mail = pu.pro_week_email("Ana", FACTS)
        self.assertEqual(mail["subject"], "Want me to pitch 3 brands for you every day?")
        self.assertIn("3 new matched brands every weekday", mail["html"])
        self.assertIn("about 20 hours a month", mail["html"])
        self.assertIn("write 2 pitches so far", mail["html"])
        self.assertIn("<strong>Glossier</strong> and <strong>Tower 28</strong>, plus 3 other brands,", mail["html"])
        self.assertIn("Autopilot", mail["html"])
        self.assertIn("$19/mo", mail["html"])
        self.assertIn("utm_campaign=pro_week_pro", mail["html"])
        self.assertIn("Or use your 1 free pitch left this month", mail["html"])
        self.assertNotIn("5 real reasons", mail["html"])

    def test_trial_offer(self):
        mail = pu.pro_week_email("Ana", FACTS, trial=True)
        self.assertIn("free", mail["subject"])
        self.assertIn("Start my free week", mail["html"])
        self.assertIn("Cancel before day 7", mail["html"])
        self.assertIn("utm_campaign=pro_week_trial", mail["html"])

    def test_no_pitch_yet_and_no_free_left(self):
        mail = pu.pro_week_email("Ana", {"pitched": 0, "left": 0, "matched": 0, "brand_names": []})
        self.assertIn("no pitch has gone out yet", mail["html"])
        self.assertNotIn("matched to you and waiting", mail["html"])
        self.assertNotIn("free pitch left", mail["html"])


class TestJob(unittest.TestCase):
    def _run(self, who, trial=False, dry_run=False):
        send = MagicMock(return_value={"success": True, "message_id": "re_1"})
        with patch.object(pu, "_candidates", return_value=[{"creator_id": 4}]), \
                patch.object(pu, "pro_week_facts", return_value=FACTS), \
                patch("services.quota_trial_test.trial_eligible", return_value=trial), \
                patch.object(polly_alerts, "ensure_alert_tables"), \
                patch.object(polly_alerts, "_recipient", return_value=who), \
                patch.object(polly_alerts, "_unsubscribe_url", return_value="u"), \
                patch.object(polly_alerts, "_log_email") as log:
            out = pu.email_pro_week(MagicMock(), send_fn=send, dry_run=dry_run)
        return out, send, log

    def test_sends_once_and_logs(self):
        out, send, log = self._run({"user_id": 9, "email": "a@x.co", "first": "Ana", "is_pro": False}, trial=True)
        self.assertEqual(out[0]["kind"], pu.KIND)
        self.assertTrue(out[0]["trial"])
        send.assert_called_once()
        self.assertEqual(log.call_args[0][2], pu.KIND)

    def test_skips_pro_unsubscribed_and_dry_run(self):
        self.assertEqual(self._run({"user_id": 9, "email": "a@x.co", "first": "", "is_pro": True})[0], [])
        self.assertEqual(self._run(None)[0], [])
        out, send, _ = self._run({"user_id": 9, "email": "a@x.co", "first": "", "is_pro": False}, dry_run=True)
        self.assertEqual(len(out), 1)
        send.assert_not_called()

    def test_candidate_query_guards(self):
        conn = MagicMock()
        cur = conn.cursor.return_value
        cur.fetchall.return_value = []
        pu._candidates(conn, 10, datetime(2026, 10, 15, tzinfo=timezone.utc))
        sql = cur.execute.call_args[0][0]
        for guard in ("e.event = 'open'", "checkout_completed", "max_quota_hit", "paywall_followup",
                      "approval_status", "unlocks_tier"):
            self.assertIn(guard, sql)

    def test_in_alert_cron(self):
        src = inspect.getsource(polly_alerts.run_alerts)
        self.assertIn("email_pro_week", src)
        self.assertIn("email_weekly_digests", src)


class TestWeeklyDigestHandoff(unittest.TestCase):
    MONDAY = datetime(2026, 10, 19, 9, tzinfo=timezone.utc)

    def test_runs_on_monday_only(self):
        with patch("lifecycle_email_engine.process_weekly_digest", return_value={"sent": 3}) as digest:
            self.assertEqual(polly_alerts.email_weekly_digests(MagicMock(), now=self.MONDAY),
                             [{"kind": "weekly_digest", "sent": 3}])
            self.assertEqual(polly_alerts.email_weekly_digests(
                MagicMock(), now=datetime(2026, 10, 20, tzinfo=timezone.utc)), [])
        digest.assert_called_once()

    def test_not_from_test_sends(self):
        with patch("lifecycle_email_engine.process_weekly_digest") as digest:
            polly_alerts.email_weekly_digests(MagicMock(), now=self.MONDAY, test_email="t@x.co")
            polly_alerts.email_weekly_digests(MagicMock(), now=self.MONDAY, send_fn=MagicMock())
        digest.assert_not_called()


if __name__ == "__main__":
    unittest.main()


def test_pro_offer_is_daily_and_never_promises_a_yes():
    from services import pro_offer
    from services.polly_persona import PRO_VALUE_LINE

    assert pro_offer.DAILY_PITCHES == 3
    for text in (pro_offer.PROMISE, pro_offer.SHORT, PRO_VALUE_LINE):
        assert "every weekday" in text.lower() and "one yes" not in text
    assert "Never promise" in pro_offer.RULE
