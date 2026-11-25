"""Weekly Brand Hotlist: Thanos-ranked hot brands (services.hot_brands.hot_picks), no repeats for 4 weeks, >= 3 or
no post."""

import os
import unittest
from datetime import date
from unittest.mock import patch

import psycopg2

from services import weekly_hotlist as wh

SCHEMA = "weekly_hotlist_t"


def _conn():
    return psycopg2.connect(host=os.getenv("DB_HOST"), port=os.getenv("DB_PORT"), dbname=os.getenv("DB_NAME"),
                            user=os.getenv("DB_USER"), password=os.getenv("DB_PASSWORD") or None,
                            options=f"-c search_path={SCHEMA}")


def ranked(ids, **extra):
    return [{"brand_id": i, "brand_name": f"B{i}", "category": "skincare", "reason": "Running Meta ads",
             "active_ad_count": 4, "gifted_your_size": 1, "ugc_style": True, "found_on": date.today(), **extra}
            for i in ids]


class Hotlist(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            boot = psycopg2.connect(host=os.getenv("DB_HOST"), port=os.getenv("DB_PORT"),
                                    dbname=os.getenv("DB_NAME"), user=os.getenv("DB_USER"),
                                    password=os.getenv("DB_PASSWORD") or None)
        except Exception as err:
            raise unittest.SkipTest(f"no db: {err}")
        boot.autocommit = True
        boot.cursor().execute(f"DROP SCHEMA IF EXISTS {SCHEMA} CASCADE; CREATE SCHEMA {SCHEMA};")
        boot.close()

    @classmethod
    def tearDownClass(cls):
        c = _conn()
        c.autocommit = True
        c.cursor().execute(f"DROP SCHEMA IF EXISTS {SCHEMA} CASCADE")
        c.close()

    def setUp(self):
        wh._READY = False
        self.conn = _conn()
        self.conn.cursor().execute("DROP TABLE IF EXISTS creator_hotlist_shown")
        self.conn.commit()

    def tearDown(self):
        self.conn.close()

    def run_msg(self, ids, is_pro=False):
        seen = {}

        def picks(conn, creator_id, excluded, limit, **kw):
            seen["excluded"] = set(excluded)
            return ranked([i for i in ids if i not in excluded][:limit])

        with patch("services.hot_brands.load_creators", return_value={7: {"id": 7, "creator_niches": ["skincare"]}}), \
                patch("services.creator_niches.recorded_niches", return_value=["skincare"]), \
                patch("services.hot_brands.exclusions", return_value={7: {99}}), \
                patch("services.hot_brands.hot_picks", side_effect=picks), \
                patch("services.pro_manager.last_week_facts", return_value={"pitched": 5, "reply_names": ["X"], "kit_names": []}):
            msg = wh.hotlist_message(self.conn, 7, "Maya", is_pro=is_pro)
        self.assertIn(99, seen.get("excluded", {99}))
        return msg

    def test_hot_picks_in_order(self):
        msg = self.run_msg([1, 2, 3, 4, 10, 11, 12])
        ids = [b["brand_id"] for b in msg["hotlist"]["brands"]]
        self.assertEqual(ids, [1, 2, 3, 4, 10, 11])
        self.assertEqual(msg["kind"], "hotlist")
        self.assertEqual(msg["task_chips"][0]["action"], "unlock_pro")
        self.assertTrue(msg["hotlist"]["brands"][0]["fresh"])

    def test_no_repeats_within_four_weeks(self):
        self.run_msg([1, 2, 3, 4, 5, 6])
        msg = self.run_msg([1, 2, 3, 4, 5, 6, 7, 8, 9])
        self.assertEqual([b["brand_id"] for b in msg["hotlist"]["brands"]], [7, 8, 9])

    def test_too_few_brands_no_post(self):
        self.assertIsNone(self.run_msg([1, 2]))

    def test_pro_gets_autopilot_line_and_batch(self):
        msg = self.run_msg([1, 2, 3, 4], is_pro=True)
        self.assertEqual(msg["hotlist"]["pro_line"], "Last week Autopilot: 5 pitches sent · 1 reply")
        self.assertEqual(msg["task_chips"][0]["brand_ids"], [1, 2, 3])

    def test_survives_thread_sanitizer(self):
        from services.polly_memory import _clean_message
        clean = _clean_message(self.run_msg([1, 2, 3]))
        self.assertEqual(clean["kind"], "hotlist")
        self.assertEqual(len(clean["hotlist"]["brands"]), 3)


if __name__ == "__main__":
    unittest.main()
