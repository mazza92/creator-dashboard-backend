"""Free users get exactly 3 PR pack unlocks per calendar month."""

import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from services.unlock_quota import (
    DELIVERED_THIS_MONTH_SQL,
    FREE_UNLOCK_LIMIT,
    count_delivered_unlocks_this_month,
    free_unlock_usage,
    usage_from_delivered,
)


class TestUsageFromDelivered(unittest.TestCase):
    def test_new_free_user_has_three(self):
        used, remaining_free, remaining = usage_from_delivered(0, 0)
        self.assertEqual(used, 0)
        self.assertEqual(remaining_free, 3)
        self.assertEqual(remaining, 3)

    def test_one_and_two_unlocks(self):
        self.assertEqual(usage_from_delivered(1, 0), (1, 2, 2))
        self.assertEqual(usage_from_delivered(2, 0), (2, 1, 1))

    def test_three_unlocks_is_capped(self):
        used, remaining_free, remaining = usage_from_delivered(3, 0)
        self.assertEqual(used, 3)
        self.assertEqual(remaining_free, 0)
        self.assertEqual(remaining, 0)

    def test_leaked_fourth_pack_does_not_show_negative(self):
        used, remaining_free, remaining = usage_from_delivered(4, 0)
        self.assertEqual(used, 3)
        self.assertEqual(remaining_free, 0)
        self.assertEqual(remaining, 0)

    def test_paid_pack_credits_sit_on_top(self):
        used, remaining_free, remaining = usage_from_delivered(3, 3)
        self.assertEqual(used, 3)
        self.assertEqual(remaining_free, 0)
        self.assertEqual(remaining, 3)

    def test_two_free_plus_credits(self):
        used, remaining_free, remaining = usage_from_delivered(2, 1)
        self.assertEqual(used, 2)
        self.assertEqual(remaining_free, 1)
        self.assertEqual(remaining, 2)

    def test_limit_is_three(self):
        self.assertEqual(FREE_UNLOCK_LIMIT, 3)


class TestDeliveredCountQuery(unittest.TestCase):
    def test_sql_is_calendar_month_and_pr_brands_only(self):
        sql = ' '.join(DELIVERED_THIS_MONTH_SQL.split())
        self.assertIn("date_trunc('month', NOW())", sql)
        self.assertIn('pr_brands', sql)
        self.assertIn('pr_packages', sql)
        self.assertIn('brand_unlocks', sql)
        self.assertNotIn('COALESCE(generated_at', sql)

    def test_count_reads_n(self):
        cursor = MagicMock()
        cursor.fetchone.return_value = {'n': 2}
        self.assertEqual(count_delivered_unlocks_this_month(cursor, 2848), 2)
        cursor.execute.assert_called_once()
        self.assertEqual(cursor.execute.call_args[0][1], (2848, 2848))

    def test_free_unlock_usage_uses_delivered_not_counter(self):
        cursor = MagicMock()
        cursor.fetchone.return_value = {'n': 2}
        used, remaining = free_unlock_usage(cursor, 1, unlocks_remaining=2, pack_credits=0)
        self.assertEqual(used, 2)
        self.assertEqual(remaining, 1)


if __name__ == '__main__':
    unittest.main()
