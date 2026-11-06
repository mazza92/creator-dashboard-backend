"""ensure_columns: no ALTER (and so no table lock) when the columns already exist."""

import os
import unittest

import psycopg2

from services.db_ddl import ensure_columns


class EnsureColumnsTests(unittest.TestCase):
    def setUp(self):
        try:
            self.conn = psycopg2.connect(host=os.getenv("DB_HOST"), port=os.getenv("DB_PORT"),
                                         dbname=os.getenv("DB_NAME"), user=os.getenv("DB_USER"),
                                         password=os.getenv("DB_PASSWORD") or None)
        except Exception as err:
            raise unittest.SkipTest(f"no db: {err}")
        self.conn.autocommit = True
        cur = self.conn.cursor()
        cur.execute("DROP TABLE IF EXISTS ddl_cols_t; CREATE TABLE ddl_cols_t (id int)")

    def tearDown(self):
        self.conn.cursor().execute("DROP TABLE IF EXISTS ddl_cols_t")
        self.conn.close()

    def _cols(self):
        cur = self.conn.cursor()
        cur.execute("SELECT column_name FROM information_schema.columns WHERE table_name = 'ddl_cols_t' ORDER BY 1")
        return [r[0] for r in cur.fetchall()]

    def test_adds_missing_columns_once(self):
        cur = self.conn.cursor()
        ensure_columns(cur, "ALTER TABLE ddl_cols_t ADD COLUMN IF NOT EXISTS a INT, ADD COLUMN IF NOT EXISTS b TEXT")
        self.assertEqual(self._cols(), ["a", "b", "id"])

    def test_skips_alter_when_columns_exist_even_while_table_is_locked(self):
        cur = self.conn.cursor()
        ensure_columns(cur, "ALTER TABLE ddl_cols_t ADD COLUMN IF NOT EXISTS a INT")
        other = psycopg2.connect(host=os.getenv("DB_HOST"), port=os.getenv("DB_PORT"), dbname=os.getenv("DB_NAME"),
                                 user=os.getenv("DB_USER"), password=os.getenv("DB_PASSWORD") or None)
        try:
            other.cursor().execute("LOCK TABLE ddl_cols_t IN ACCESS SHARE MODE")  # an open reader
            cur.execute("SET lock_timeout = '500ms'")
            ensure_columns(cur, "ALTER TABLE ddl_cols_t ADD COLUMN IF NOT EXISTS a INT")  # would block if it ALTERed
        finally:
            other.rollback()
            other.close()
            cur.execute("SET lock_timeout = 0")

    def test_other_statements_in_the_block_still_run(self):
        cur = self.conn.cursor()
        ensure_columns(cur, "ALTER TABLE ddl_cols_t ADD COLUMN IF NOT EXISTS a INT")
        ensure_columns(cur, """CREATE TABLE IF NOT EXISTS ddl_cols_t2 (id int);
                               ALTER TABLE ddl_cols_t ADD COLUMN IF NOT EXISTS a INT""")
        cur.execute("SELECT to_regclass('public.ddl_cols_t2')")
        self.assertIsNotNone(cur.fetchone()[0])
        cur.execute("DROP TABLE ddl_cols_t2")


if __name__ == "__main__":
    unittest.main()


class MatchedWaitingTests(unittest.TestCase):
    def test_drafted_brands_are_not_waiting(self):
        from services.polly import matched_waiting
        thread = {
            "suggested_brands": [{"id": 1, "name": "Native"}, {"id": 2, "name": "Activeskin"},
                                 {"id": 3, "name": "Bare"}, {"id": 4, "name": "Glow"}],
            "notes": {"pitch_batch": {"items": [{"brand_id": 1}, {"brand_id": 2}]},
                      "pitch_outbox": {"3": {"brand_name": "Bare"}}},
        }
        self.assertEqual(matched_waiting(thread), {"names": ["Glow"], "count": 1})
