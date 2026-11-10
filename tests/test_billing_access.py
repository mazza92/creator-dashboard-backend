import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

from services import billing_access as ba

NOW = datetime(2026, 11, 10, 12, 0, tzinfo=timezone.utc)


class DecidePastDueTests(unittest.TestCase):
    def test_trial_that_never_paid_locks_at_once(self):
        self.assertEqual(ba.decide_past_due(False, NOW, NOW), "lock")

    def test_paying_member_keeps_pro_during_grace(self):
        self.assertEqual(ba.decide_past_due(True, NOW - timedelta(days=2), NOW), "grace")

    def test_paying_member_locks_after_grace(self):
        self.assertEqual(ba.decide_past_due(True, NOW - timedelta(days=3, minutes=1), NOW), "lock")


class HasPaidTests(unittest.TestCase):
    def test_zero_dollar_trial_invoice_is_not_a_payment(self):
        stripe = MagicMock()
        stripe.Invoice.list.return_value = {"data": [{"amount_paid": 0}]}
        self.assertFalse(ba.has_paid(stripe, "sub_1"))

    def test_real_payment_counts(self):
        stripe = MagicMock()
        stripe.Invoice.list.return_value = {"data": [{"amount_paid": 0}, {"amount_paid": 1900}]}
        self.assertTrue(ba.has_paid(stripe, "sub_1"))

    def test_stripe_error_gives_benefit_of_doubt(self):
        stripe = MagicMock()
        stripe.Invoice.list.side_effect = RuntimeError("down")
        self.assertTrue(ba.has_paid(stripe, "sub_1"))


class ApplyStatusTests(unittest.TestCase):
    def setUp(self):
        self.conn = MagicMock()
        self.creator = {"id": 7, "subscription_tier": "pro", "unlocks_tier": "free", "subscription_status": "active",
                        "past_due_since": None, "pro_locked_at": None}

    def test_active_and_already_pro_is_left_alone(self):
        with patch.object(ba, "restore") as restore, patch.object(ba, "lock") as lock:
            self.assertEqual(ba.apply_status(self.conn, MagicMock(), self.creator, {"status": "active"}, NOW), "ok")
            restore.assert_not_called()
            lock.assert_not_called()

    def test_canceled_subscription_still_marked_pro_is_locked(self):
        with patch.object(ba, "lock") as lock:
            self.assertEqual(ba.apply_status(self.conn, MagicMock(), self.creator, {"status": "canceled"}, NOW),
                             "locked")
            lock.assert_called_once()

    def test_failed_trial_is_locked(self):
        stripe = MagicMock()
        stripe.Invoice.list.return_value = {"data": []}
        self.creator.update(subscription_status="past_due", past_due_since=NOW)
        with patch.object(ba, "lock") as lock:
            self.assertEqual(ba.apply_status(self.conn, stripe, self.creator, {"id": "sub_1", "status": "past_due"},
                                             NOW), "locked")
            lock.assert_called_once_with(self.conn, 7, "payment_failed", "past_due")

    def test_renewal_failure_inside_grace_keeps_pro(self):
        stripe = MagicMock()
        stripe.Invoice.list.return_value = {"data": [{"amount_paid": 1900}]}
        self.creator.update(subscription_status="past_due", past_due_since=NOW - timedelta(days=1))
        with patch.object(ba, "lock") as lock:
            self.assertEqual(ba.apply_status(self.conn, stripe, self.creator, {"id": "sub_1", "status": "past_due"},
                                             NOW), "grace")
            lock.assert_not_called()

    def test_paid_again_restores_locked_creator(self):
        self.creator.update(subscription_tier="free", subscription_status="past_due", pro_locked_at=NOW)
        with patch.object(ba, "restore") as restore:
            self.assertEqual(ba.apply_status(self.conn, MagicMock(), self.creator, {"status": "active"}, NOW),
                             "restored")
            restore.assert_called_once()

    def test_retention_pause_is_not_undone(self):
        self.creator.update(subscription_tier="free", subscription_status="paused")
        with patch.object(ba, "restore") as restore:
            self.assertEqual(ba.apply_status(self.conn, MagicMock(), self.creator,
                                             {"status": "active", "pause_collection": {"behavior": "void"}}, NOW),
                             "paused")
            restore.assert_not_called()


class UnpaidSinceTests(unittest.TestCase):
    def test_old_past_due_uses_period_start_and_locks(self):
        stripe = MagicMock()
        stripe.Invoice.list.return_value = {"data": [{"amount_paid": 1900}]}
        start = int((NOW - timedelta(days=30)).timestamp())
        creator = {"id": 9, "subscription_tier": "pro", "unlocks_tier": "free", "subscription_status": "past_due",
                   "past_due_since": None, "pro_locked_at": None}
        conn = MagicMock()
        conn.cursor.return_value.fetchone.return_value = (datetime.fromtimestamp(start, tz=timezone.utc),)
        with patch.object(ba, "lock") as lock:
            out = ba.apply_status(conn, stripe, creator, {"id": "sub_9", "status": "past_due",
                                                          "current_period_start": start}, NOW)
        self.assertEqual(out, "locked")
        lock.assert_called_once()


class OnPaymentFailedTests(unittest.TestCase):
    def test_trial_conversion_failure_locks(self):
        stripe = MagicMock()
        stripe.Invoice.list.return_value = {"data": []}
        with patch.object(ba, "ensure_columns"), patch.object(ba, "mark_past_due", return_value=NOW), \
                patch.object(ba, "lock") as lock:
            self.assertEqual(ba.on_payment_failed(MagicMock(), stripe, 3, "sub_1", NOW), "locked")
            lock.assert_called_once()

    def test_renewal_failure_starts_grace(self):
        stripe = MagicMock()
        stripe.Invoice.list.return_value = {"data": [{"amount_paid": 1900}]}
        with patch.object(ba, "ensure_columns"), patch.object(ba, "mark_past_due", return_value=NOW), \
                patch.object(ba, "lock") as lock:
            self.assertEqual(ba.on_payment_failed(MagicMock(), stripe, 3, "sub_1", NOW), "grace")
            lock.assert_not_called()


if __name__ == "__main__":
    unittest.main()
