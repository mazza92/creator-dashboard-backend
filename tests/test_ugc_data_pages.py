import unittest
from datetime import date
from unittest import mock

import services.brand_ugc as bu


class FakeCursor:
    def __init__(self, rows):
        self.rows = rows
        self.description = [("id",), ("brand_name",), ("slug",), ("logo_url",), ("category",)]

    def execute(self, sql, params=None):
        self.params = params

    def fetchall(self):
        return self.rows


class RunningAds(unittest.TestCase):
    def test_only_ad_signals_freshest_first(self):
        rows = [
            {"brand_name": "Gift Only", "slug": "g", "category": "beauty", "hot": False, "creator_ads": False,
             "gifting": True, "brief": False, "seen_on": None},
            {"brand_name": "Old Ads", "slug": "o", "category": "beauty", "hot": True, "creator_ads": False,
             "gifting": False, "brief": False, "seen_on": date(2026, 11, 2)},
            {"brand_name": "New Ads", "slug": "n", "category": "skincare", "hot": True, "creator_ads": False,
             "gifting": False, "brief": False, "seen_on": date(2026, 11, 22)},
            {"brand_name": "Creator Ads", "slug": "c", "category": "home", "hot": False, "creator_ads": True,
             "gifting": False, "brief": False, "seen_on": None},
        ]
        with mock.patch.object(bu, "_signal_rows", return_value=rows):
            out = bu.brands_running_ads(None)
        self.assertEqual([b["slug"] for b in out["brands"]], ["c", "n", "o"])
        self.assertEqual(out["total"], 3)
        self.assertEqual(out["counts"], {"hot": 2, "creatorAds": 1, "briefs": 0})
        self.assertEqual(out["brands"][1]["seenOn"], "2026-11-22")


class Replying(unittest.TestCase):
    def test_needs_two_replies_or_a_pick_and_never_names_creators(self):
        stats = {1: {"pitched": 20, "replied": 4, "selected": 0},   # 20% -> in
                 2: {"pitched": 10, "replied": 1, "selected": 0},   # one reply -> out
                 3: {"pitched": 5, "replied": 0, "selected": 1},    # picked from roster -> in
                 4: {"pitched": 100, "replied": 2, "selected": 0}}  # 2% -> not the replies tier -> out
        cur = FakeCursor([{"id": 1, "brand_name": "A", "slug": "a", "logo_url": None, "category": "beauty"},
                          {"id": 3, "brand_name": "C", "slug": "c", "logo_url": None, "category": "skincare"}])
        with mock.patch("services.brand_reply_signal.load_stats", return_value=stats):
            out = bu.brands_replying(cur)
        self.assertEqual(sorted(cur.params[0]), [1, 3])
        self.assertEqual([b["slug"] for b in out["brands"]], ["c", "a"])
        self.assertEqual(out["brands"][1]["replyRate"], 20)
        self.assertEqual(set(out["brands"][0]), {"name", "slug", "logo", "category", "replied", "pitched", "selected",
                                                 "replyRate"})


if __name__ == "__main__":
    unittest.main()
