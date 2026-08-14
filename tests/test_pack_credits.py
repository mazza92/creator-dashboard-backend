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
        pack_credits._SCHEMA_READY = False
    def test_grants_once_per_stripe_session(self):
        conn = MagicMock()
        cursor = MagicMock()
        conn.cursor.return_value = cursor
        cursor.fetchone.side_effect = [
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


if __name__ == '__main__':
    unittest.main()
