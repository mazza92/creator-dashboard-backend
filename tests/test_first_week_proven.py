"""New Pro creators' first week: niche brands that answer creators go out first (after their saved brands)."""

import os
import unittest
from datetime import datetime, timedelta, timezone
from unittest import mock

import psycopg2

import routes.polly_autopilot as ap

SCHEMA = "first_week_proven_t"
NOW = datetime.now(timezone.utc)


def _conn():
    return psycopg2.connect(host=os.getenv("DB_HOST"), port=os.getenv("DB_PORT"), dbname=os.getenv("DB_NAME"),
                            user=os.getenv("DB_USER"), password=os.getenv("DB_PASSWORD") or None,
                            options=f"-c search_path={SCHEMA}")


class FirstWeek(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            boot = psycopg2.connect(host=os.getenv("DB_HOST"), port=os.getenv("DB_PORT"), dbname=os.getenv("DB_NAME"),
                                    user=os.getenv("DB_USER"), password=os.getenv("DB_PASSWORD") or None)
        except Exception as err:
            raise unittest.SkipTest(f"no db: {err}")
        boot.autocommit = True
        boot.cursor().execute(f"""
            DROP SCHEMA IF EXISTS {SCHEMA} CASCADE; CREATE SCHEMA {SCHEMA}; SET search_path TO {SCHEMA};
            CREATE TABLE creators (id int PRIMARY KEY, subscription_started_at timestamptz);
            CREATE TABLE pr_brands (id int PRIMARY KEY, brand_name text, slug text, category text, logo_url text,
                min_followers int, contact_email text, email_status text, email_bounce_count int DEFAULT 0,
                accepting_pr boolean, status text, response_rate numeric);
            INSERT INTO creators VALUES (1, NOW() - INTERVAL '2 days'), (2, NOW() - INTERVAL '20 days'), (3, NULL);
            INSERT INTO pr_brands (id, brand_name, slug, category, min_followers, contact_email) VALUES
                (10, 'Proven Skin', 'p', 'skincare', 1000, 'pr@p.co'),
                (11, 'Proven Big', 'b', 'skincare', 500000, 'pr@b.co'),
                (12, 'Proven Fashion', 'f', 'fashion', NULL, 'pr@f.co'),
                (13, 'Proven No Inbox', 'n', 'skincare', NULL, ''),
                (14, 'Unproven Skin', 'u', 'skincare', NULL, 'pr@u.co');
        """)
        boot.close()

    @classmethod
    def tearDownClass(cls):
        c = _conn()
        c.autocommit = True
        c.cursor().execute(f"DROP SCHEMA IF EXISTS {SCHEMA} CASCADE")
        c.close()

    def test_first_week_window(self):
        c = _conn()
        self.assertTrue(ap.in_first_pro_week(c, 1))
        self.assertFalse(ap.in_first_pro_week(c, 2))
        self.assertFalse(ap.in_first_pro_week(c, 3))
        c.close()

    def test_proven_brands_in_niche_reach_and_inbox_only(self):
        c = _conn()
        with mock.patch("services.brand_reply_signal.id_sets", return_value={"replies": [10, 11, 12, 13], "cold": [0]}):
            got = ap.proven_niche_brands(c, ["Skincare"], [], 3000)
        c.close()
        self.assertEqual([b["id"] for b in got], [10])  # not 11 (out of reach), 12 (other niche), 13 (no inbox), 14
        self.assertTrue(got[0]["proven"])

    def test_order_saved_then_proven_then_rest(self):
        c = _conn()
        matched = [{"id": 50, "target": True}, {"id": 60}, {"id": 10}, {"id": 70}]
        with mock.patch("services.brand_reply_signal.id_sets", return_value={"replies": [10], "cold": [0]}):
            got = ap._first_week_proven_first(c, 1, {"followers_count": 3000}, matched, ["skincare"], [], {})
            later = ap._first_week_proven_first(c, 2, {"followers_count": 3000}, matched, ["skincare"], [], {})
        c.close()
        self.assertEqual([b["id"] for b in got], [50, 10, 60, 70])
        self.assertEqual(later, matched)  # after week one nothing changes


if __name__ == "__main__":
    unittest.main()
