"""Free creators: written-but-unsent pitches hold their credit at the write gate (creator 4496 wrote 6 on 3)."""
import unittest
from unittest.mock import MagicMock, patch

from services import unlock_quota as uq


class Reserved(unittest.TestCase):
    def _cur(self, n):
        cur = MagicMock()
        cur.fetchone.return_value = {"n": n}
        return cur

    def test_unsent_first_pitches_hold_their_credits(self):
        # 3 first pitches written (unlock rows) and not sent: charged is 0, but the gate sees 3 of 3.
        with patch.object(uq, "pending_send_count", side_effect=lambda c, i, fresh_only=False: 3):
            self.assertEqual(uq.count_reserved_this_month(self._cur(3), 1), 3)
            self.assertEqual(uq.count_delivered_unlocks_this_month(self._cur(3), 1), 0)

    def test_week_old_unsent_pitches_give_credit_back(self):
        # 3 pending, 1 of them older than a week: that one frees its credit.
        with patch.object(uq, "pending_send_count", side_effect=lambda c, i, fresh_only=False: 2 if fresh_only else 3):
            self.assertEqual(uq.count_reserved_this_month(self._cur(3), 1), 2)

    def test_no_pending(self):
        with patch.object(uq, "pending_send_count", return_value=0):
            self.assertEqual(uq.count_reserved_this_month(self._cur(2), 1), 2)


if __name__ == "__main__":
    unittest.main()


class WaitingNotPaywall(unittest.TestCase):
    def test_gate_reports_waiting_pitches(self):
        import pr_crm_routes as R
        cur = MagicMock()
        cur.fetchone.side_effect = [{"unlocks_tier": "free", "subscription_tier": "free", "unlocks_remaining": 0,
                                     "unlocks_reset_at": None}]
        conn = MagicMock()
        conn.cursor.return_value = cur
        with patch.object(R, "brand_already_delivered", return_value=False), \
                patch.object(R, "count_reserved_this_month", return_value=3), \
                patch.object(R, "free_limit", return_value=3), \
                patch("services.unlock_quota.pending_send_count", return_value=3):
            out = R.attempt_unlock(1, 9, conn)
        self.assertEqual(out["status"], "paywall")
        self.assertEqual(out["waiting"], 3)
