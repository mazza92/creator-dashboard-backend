"""Autopilot targeting: brands likely to reply to this creator's size go first; none are dropped."""
import unittest
from unittest.mock import MagicMock

from routes import polly_autopilot as ap


def conn_with(rows):
    conn = MagicMock()
    conn.cursor.return_value.fetchall.return_value = rows
    return conn


class Reach(unittest.TestCase):
    def test_orders_by_reach_then_proven_keeps_every_brand(self):
        brands = [{"id": i} for i in (1, 2, 3, 4, 5)]
        rows = [(1, 50000, True),    # out of reach (Lancome for a 1.9k creator)
                (2, 10000, False),   # stretch (5.3x is out; 10k > 9.5k) -> out of reach
                (3, 500, False),     # in reach
                (4, None, True),     # in reach, works with creators
                (5, 5000, True)]     # stretch, proven
        out = [b["id"] for b in ap._rank_by_reach(conn_with(rows), brands, 1900)]
        self.assertEqual(out, [4, 3, 5, 1, 2])
        self.assertEqual(sorted(out), [1, 2, 3, 4, 5])

    def test_big_creators_keep_match_order_within_tier(self):
        brands = [{"id": 1}, {"id": 2}]
        rows = [(1, 0, False), (2, 0, True)]
        self.assertEqual([b["id"] for b in ap._rank_by_reach(conn_with(rows), brands, 50000)], [1, 2])

    def test_creator_inboxes_first_customer_service_last_companies_at_the_back(self):
        brands = [{"id": i} for i in (1, 2, 3, 4)]
        rows = [(1, 500, False, "support@one.com", "One"),
                (2, 500, False, "hello@two.com", "Two"),
                (3, 500, False, "pr@three.com", "Three"),
                (4, 500, False, "pr@four.com", "Shenzhen Four Trading Co., Ltd.")]
        out = [b["id"] for b in ap._rank_by_reach(conn_with(rows), brands, 1900)]
        self.assertEqual(out, [3, 2, 1, 4])

    def test_unknown_followers_or_error_keep_order(self):
        brands = [{"id": 2}, {"id": 1}]
        self.assertEqual(ap._rank_by_reach(MagicMock(), brands, None), brands)
        broken = MagicMock()
        broken.cursor.return_value.execute.side_effect = RuntimeError("db")
        self.assertEqual(ap._rank_by_reach(broken, brands, 1000), brands)


if __name__ == "__main__":
    unittest.main()
