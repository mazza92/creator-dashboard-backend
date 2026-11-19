"""Autopilot keeps sending in small niches: when the matched pool runs dry, top up from the directory."""
import unittest
from unittest.mock import MagicMock, patch

from routes import polly_autopilot as ap


class Topup(unittest.TestCase):
    def test_own_niche_first_then_neighbours_and_card_shape(self):
        cur = MagicMock()
        cur.fetchall.return_value = [
            {"id": 1 + i, "brand_name": f"H{i}", "slug": "h", "category": "home", "logo_url": None, "min_followers": 0}
            for i in range(3)] + [
            {"id": 7, "brand_name": "Away", "slug": "away", "category": "travel", "logo_url": None, "min_followers": 0},
            {"id": 8, "brand_name": "Lux", "slug": "lux", "category": "luxury", "logo_url": None, "min_followers": 0}]
        conn = MagicMock()
        conn.cursor.return_value = cur
        out = ap._directory_topup(conn, ["luxury", "travel"], [1, 2], 5)
        params = cur.execute.call_args[0][1]
        self.assertEqual(params["cats"][:2], ["luxury", "travel"])
        self.assertIn("fashion", params["cats"])
        self.assertIn("lifestyle", params["cats"])
        self.assertEqual(params["skip"], [1, 2])
        self.assertEqual(params["n"], 40)
        # rotates across categories in the creator's order instead of filling up on one (home)
        self.assertEqual([b["category"] for b in out], ["luxury", "travel", "home", "home", "home"])
        self.assertEqual(out[0]["match_score"], ap.MIN_FIT)

    def test_no_categories_or_need_is_empty(self):
        self.assertEqual(ap._directory_topup(MagicMock(), [], [], 5), [])
        self.assertEqual(ap._directory_topup(MagicMock(), ["travel"], [], 0), [])

    def test_db_error_is_empty(self):
        conn = MagicMock()
        conn.cursor.return_value.execute.side_effect = RuntimeError("db")
        self.assertEqual(ap._directory_topup(conn, ["travel"], [], 5), [])


if __name__ == "__main__":
    unittest.main()
