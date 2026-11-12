"""Free pitches are spent when sent (or taken out), not when Polly writes them."""

import os
import unittest

import psycopg2

from services import unlock_quota as uq

SCHEMA = "pending_send_t"


def _conn():
    return psycopg2.connect(host=os.getenv("DB_HOST"), port=os.getenv("DB_PORT"), dbname=os.getenv("DB_NAME"),
                            user=os.getenv("DB_USER"), password=os.getenv("DB_PASSWORD") or None,
                            options=f"-c search_path={SCHEMA},public")


class PendingSend(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            boot = psycopg2.connect(host=os.getenv("DB_HOST"), port=os.getenv("DB_PORT"),
                                    dbname=os.getenv("DB_NAME"), user=os.getenv("DB_USER"),
                                    password=os.getenv("DB_PASSWORD") or None)
        except Exception as err:
            raise unittest.SkipTest(f"no db: {err}")
        boot.autocommit = True
        boot.cursor().execute(f"""
            DROP SCHEMA IF EXISTS {SCHEMA} CASCADE; CREATE SCHEMA {SCHEMA}; SET search_path TO {SCHEMA};
            CREATE TABLE pr_brands (id int PRIMARY KEY);
            CREATE TABLE brand_unlocks (creator_id int, brand_id int, unlocked_at timestamptz DEFAULT NOW(),
                                        UNIQUE (creator_id, brand_id));
            CREATE TABLE pr_packages (creator_id int, brand_id int, generated_at timestamptz DEFAULT NOW());
            CREATE TABLE brand_pr_applications (creator_id int, brand_id int, applied_at timestamptz);
            INSERT INTO pr_brands VALUES (1), (2), (3), (4);
        """)
        boot.close()

    @classmethod
    def tearDownClass(cls):
        c = _conn()
        c.autocommit = True
        c.cursor().execute(f"DROP SCHEMA IF EXISTS {SCHEMA} CASCADE")
        c.close()

    def setUp(self):
        self.conn = _conn()
        cur = self.conn.cursor()
        cur.execute(f"DROP TABLE IF EXISTS {SCHEMA}.credit_pending_send; TRUNCATE brand_unlocks, pr_packages")
        # credit_pending_send is looked up as public.credit_pending_send: create it in public for the test
        cur.execute("DROP TABLE IF EXISTS public.credit_pending_send")
        self.conn.commit()

    def tearDown(self):
        self.conn.cursor().execute("DROP TABLE IF EXISTS public.credit_pending_send")
        self.conn.commit()
        self.conn.close()

    def write(self, *bids):
        cur = self.conn.cursor()
        for b in bids:
            cur.execute("INSERT INTO brand_unlocks (creator_id, brand_id) VALUES (7, %s)", (b,))
            cur.execute("INSERT INTO pr_packages (creator_id, brand_id) VALUES (7, %s)", (b,))
        self.conn.commit()

    def count(self):
        return uq.count_delivered_unlocks_this_month(self.conn.cursor(), 7)

    def test_written_batch_costs_nothing_until_sent(self):
        self.write(1, 2, 3)
        self.assertEqual(self.count(), 3)                       # old behaviour: spent on writing
        cur = self.conn.cursor()
        cur.execute("SET search_path TO public")
        uq.mark_pending_send(cur, 7, [1, 2, 3])
        cur.execute(f"SET search_path TO {SCHEMA},public")
        self.conn.commit()
        self.assertEqual(self.count(), 0)                       # 3 free pitches still there
        self.assertEqual(uq.pending_send_ids(self.conn.cursor(), 7), {1, 2, 3})
        uq.charge_pending(self.conn.cursor(), 7, [1])           # one sent
        self.conn.commit()
        self.assertEqual(self.count(), 1)
        uq.charge_pending(self.conn.cursor(), 7, [2, 3])        # the rest sent / taken out
        self.conn.commit()
        self.assertEqual(self.count(), 3)
        self.assertEqual(uq.pending_send_count(self.conn.cursor(), 7), 0)

    def test_no_table_no_change(self):
        self.write(1, 2)
        self.assertEqual(self.count(), 2)
        self.assertEqual(uq.pending_send_count(self.conn.cursor(), 7), 0)
        self.assertEqual(uq.charge_pending(self.conn.cursor(), 7, [1]), 0)


if __name__ == "__main__":
    unittest.main()
