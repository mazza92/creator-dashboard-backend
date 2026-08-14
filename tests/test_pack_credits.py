"""Paid $9 pack credits sit on top of the 3 free monthly unlocks."""

import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from services.pack_credits import (
    PACK_BUNDLE_CENTS,
    PACK_BUNDLE_SIZE,
    PACK_PRODUCT,
    grant_pack_bundle,
    pack_credits_of,
    total_unlocks_left,
)


class TestPackCreditMath(unittest.TestCase):
    def test_bundle_is_nine_dollars_for_three(self):
        self.assertEqual(PACK_BUNDLE_SIZE, 3)
        self.assertEqual(PACK_BUNDLE_CENTS, 900)
        self.assertEqual(PACK_PRODUCT, 'pack_bundle_3')

    def test_total_includes_paid_packs(self):
        self.assertEqual(total_unlocks_left(0, 3), 3)
        self.assertEqual(total_unlocks_left(1, 3), 4)
        self.assertEqual(total_unlocks_left(3, 0), 3)

    def test_pack_credits_of_handles_missing(self):
        self.assertEqual(pack_credits_of(None), 0)
        self.assertEqual(pack_credits_of({}), 0)
        self.assertEqual(pack_credits_of({'pack_credits': 3}), 3)


class TestGrantPackBundle(unittest.TestCase):
    def setUp(self):
        import services.pack_credits as pack_credits
        pack_credits._COLUMN_READY = None
        pack_credits._TABLE_READY = None

    def test_grants_once_per_stripe_session(self):
        conn = MagicMock()
        cursor = MagicMock()
        conn.cursor.return_value = cursor
        # catalog checks say schema already exists, then grant INSERT/UPDATE
        cursor.fetchone.side_effect = [
            (1,),  # pack_credits column exists
            (1,),  # pack_purchases table exists
            {'id': 1},
            {'pack_credits': 3},
        ]

        first = grant_pack_bundle(conn, 42, 'cs_test_1')
        self.assertTrue(first['granted'])
        self.assertEqual(first['packs'], 3)
        self.assertEqual(first['pack_credits'], 3)

        cursor.fetchone.side_effect = [
            None,
            {'pack_credits': 3},
        ]
        second = grant_pack_bundle(conn, 42, 'cs_test_1')
        self.assertFalse(second['granted'])
        self.assertTrue(second['already'])
        self.assertEqual(second['pack_credits'], 3)

    def test_column_check_does_not_alter_table(self):
        import services.pack_credits as pack_credits
        conn = MagicMock()
        cursor = MagicMock()
        conn.cursor.return_value = cursor
        cursor.fetchone.return_value = (1,)
        self.assertTrue(pack_credits.pack_credits_column_exists(conn))
        sql = ' '.join(str(call.args[0]) for call in cursor.execute.call_args_list)
        self.assertNotIn('ALTER TABLE', sql.upper())
        self.assertIn('information_schema.columns', sql)


class TestPackReportStats(unittest.TestCase):
    def test_missing_table_returns_zeros(self):
        from datetime import datetime, timedelta
        from routes.admin_reports import fetch_pack_report_stats

        cursor = MagicMock()
        cursor.fetchone.return_value = None
        cursor.fetchall.return_value = []
        now = datetime(2026, 8, 14)
        stats = fetch_pack_report_stats(
            cursor, now - timedelta(days=7), now, now - timedelta(days=14), now - timedelta(days=7)
        )
        self.assertEqual(stats['price_cents'], 900)
        self.assertEqual(stats['all_time']['purchases'], 0)
        self.assertEqual(stats['period']['revenue_cents'], 0)
        self.assertEqual(stats['recent'], [])
        sql = str(cursor.execute.call_args[0][0])
        self.assertIn("table_name = 'pack_purchases'", sql)
        self.assertNotIn('COUNT(*)', sql)
        self.assertEqual(cursor.execute.call_count, 1)


if __name__ == '__main__':
    unittest.main()
