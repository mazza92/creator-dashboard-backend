# -*- coding: utf-8 -*-
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# Route modules import Flask; keep these tests runnable without the API venv.
sys.modules.setdefault("flask", MagicMock())
sys.modules.setdefault("pr_crm_routes", MagicMock())
sys.modules.setdefault("social_verification_routes", MagicMock())
sys.modules.setdefault("services.roster_demand", MagicMock())

import brand_apply_routes
import brand_pr_roster_routes
from services.brand_billing import ensure_brand_billing_schema
from services.pg_hotpath_schema import columns_ready, tables_ready, _value


def _sql_blob(cursor) -> str:
    return " ".join(str(call.args[0]) for call in cursor.execute.call_args_list).upper()


class TestPgHotpathHelpers(unittest.TestCase):
    def test_value_reads_dict_and_tuple(self):
        self.assertEqual(_value({"n": 4}, "n"), 4)
        self.assertEqual(_value((4,), "n"), 4)

    def test_tables_ready_counts(self):
        cursor = MagicMock()
        cursor.fetchone.return_value = {"n": 2}
        self.assertTrue(tables_ready(cursor, ("a", "b")))
        cursor.fetchone.return_value = {"n": 1}
        self.assertFalse(tables_ready(cursor, ("a", "b")))

    def test_columns_ready_counts(self):
        cursor = MagicMock()
        cursor.fetchone.return_value = {"n": 2}
        self.assertTrue(columns_ready(cursor, [("t", "a"), ("t", "b")]))


class TestBrandApplyEnsureSchema(unittest.TestCase):
    def setUp(self):
        brand_apply_routes._SCHEMA_READY = False

    def test_skips_alter_when_catalog_says_ready(self):
        cursor = MagicMock()
        cursor.fetchone.side_effect = [{"n": 2}, {"n": 4}]
        conn = MagicMock()
        brand_apply_routes._ensure_schema(cursor, conn)
        sql = _sql_blob(cursor)
        self.assertNotIn("ALTER TABLE", sql)
        self.assertNotIn("CREATE TABLE", sql)
        self.assertTrue(brand_apply_routes._SCHEMA_READY)
        conn.commit.assert_not_called()

    def test_memory_flag_avoids_second_catalog_roundtrip(self):
        cursor = MagicMock()
        cursor.fetchone.side_effect = [{"n": 2}, {"n": 4}]
        brand_apply_routes._ensure_schema(cursor, None)
        calls = cursor.execute.call_count
        brand_apply_routes._ensure_schema(cursor, None)
        self.assertEqual(cursor.execute.call_count, calls)


class TestBrandRosterEnsureSchema(unittest.TestCase):
    def setUp(self):
        brand_pr_roster_routes._SCHEMA_READY = False

    def test_skips_alter_when_catalog_says_ready(self):
        cursor = MagicMock()
        cursor.fetchone.side_effect = [
            {"ok": 1},
            {"n": 3},
            {"is_nullable": "YES"},
        ]
        conn = MagicMock()
        brand_pr_roster_routes._ensure_schema(cursor, conn)
        sql = _sql_blob(cursor)
        self.assertNotIn("ALTER TABLE", sql)
        self.assertNotIn("DROP NOT NULL", sql)
        self.assertTrue(brand_pr_roster_routes._SCHEMA_READY)
        conn.commit.assert_not_called()


class TestBrandBillingEnsureSchema(unittest.TestCase):
    def setUp(self):
        import services.brand_billing as billing

        billing._SCHEMA_READY = False

    def test_skips_create_when_table_exists(self):
        cursor = MagicMock()
        cursor.fetchone.return_value = {"ok": 1}
        conn = MagicMock()
        ensure_brand_billing_schema(cursor, conn)
        sql = _sql_blob(cursor)
        self.assertNotIn("CREATE TABLE", sql)
        conn.commit.assert_not_called()


if __name__ == "__main__":
    unittest.main()
