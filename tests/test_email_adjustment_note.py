"""Every inbox adjustment lands on the brand: contact_email swapped, a dated note, and a flag for the admin."""

import os
import unittest

import psycopg2

from services import contact_repair as cr

SCHEMA = "email_adjust_t"


def _conn():
    return psycopg2.connect(host=os.getenv("DB_HOST"), port=os.getenv("DB_PORT"), dbname=os.getenv("DB_NAME"),
                            user=os.getenv("DB_USER"), password=os.getenv("DB_PASSWORD") or None,
                            options=f"-c search_path={SCHEMA}")


class AdjustmentNoteTests(unittest.TestCase):
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
            CREATE TABLE pr_brands (id int PRIMARY KEY, brand_name text, website text, contact_email text,
                                    email_status text, email_verified_at timestamp, email_verification_source text,
                                    email_bounce_count int, notes text);
        """)
        boot.close()

    @classmethod
    def tearDownClass(cls):
        c = _conn()
        c.autocommit = True
        c.cursor().execute(f"DROP SCHEMA IF EXISTS {SCHEMA} CASCADE")
        c.close()

    def setUp(self):
        cr._ADJ_READY = False
        self.conn = _conn()
        cr.ensure_tables(self.conn)
        cur = self.conn.cursor()
        cur.execute("TRUNCATE pr_brands; TRUNCATE brand_contact_repairs")
        cur.execute("INSERT INTO pr_brands (id, brand_name, contact_email, email_status, notes) VALUES "
                    "(1, 'JOVS', 'hailey@jovsbeauty.com', 'valid', 'Outreach batch Oct'), "
                    "(2, 'Dead', 'info@dead.co', NULL, NULL)")
        self.conn.commit()

    def tearDown(self):
        self.conn.close()

    def _row(self, bid):
        cur = self.conn.cursor()
        cur.execute("SELECT contact_email, notes, email_adjustment, email_adjusted_at FROM pr_brands WHERE id = %s",
                    (bid,))
        return cur.fetchone()

    def test_swap_updates_email_and_appends_a_dated_note(self):
        cr._swap(self.conn, 1, "hailey@jovsbeauty.com", "pr@jovsbeauty.com", "valid", "verify_invalid", 3975)
        email, notes, adj, at = self._row(1)
        self.assertEqual(email, "pr@jovsbeauty.com")
        self.assertTrue(notes.startswith("Outreach batch Oct\n[Email check "))
        self.assertIn("hailey@jovsbeauty.com -> pr@jovsbeauty.com: old address was invalid", notes)
        self.assertEqual(adj, "fixed:verify_invalid")
        self.assertIsNotNone(at)

    def test_unfixable_is_flagged_and_same_note_is_not_repeated(self):
        cr._log(self.conn, 2, "info@dead.co", None, "dns_dead_domain", None)
        cr._log(self.conn, 2, "info@dead.co", None, "dns_dead_domain", None)
        self.conn.commit()
        _, notes, adj, _ = self._row(2)
        self.assertEqual(notes.count("[Email check"), 1)
        self.assertIn("domain receives no email", notes)
        self.assertEqual(adj, "flagged:dns_dead_domain")
