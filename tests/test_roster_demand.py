import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from services.roster_demand import fill_target, prefer_hungry_rosters


class TestFillTarget(unittest.TestCase):
    def test_five_slots_needs_fifteen(self):
        self.assertEqual(fill_target(5), 15)

    def test_uses_pad_when_larger(self):
        self.assertEqual(fill_target(2), 10)


class TestPreferHungryRosters(unittest.TestCase):
    def test_hungry_ranked_brands_jump_the_queue(self):
        ranked = [
            {"id": 1, "name": "Fit", "roster_hunger": 0, "match_score": 90},
            {"id": 2, "name": "Also fit", "roster_hunger": 8, "match_score": 88},
        ]
        out = prefer_hungry_rosters(ranked, limit=8, max_hungry=4)
        self.assertEqual(out[0]["id"], 2)
        self.assertEqual([b["id"] for b in out], [2, 1])

    def test_does_not_inject_unranked_or_zero_fit(self):
        ranked = [
            {"id": 1, "name": "Fit", "roster_hunger": 0, "match_score": 90},
        ]
        pool = ranked + [
            {"id": 9, "name": "Off niche", "roster_hunger": 12, "match_score": 0},
        ]
        out = prefer_hungry_rosters(ranked, pool=pool, limit=8, max_hungry=4)
        self.assertEqual([b["id"] for b in out], [1])

    def test_drops_below_min_fit(self):
        ranked = [
            {"id": 1, "roster_hunger": 20, "match_score": 0},
            {"id": 2, "roster_hunger": 0, "match_score": 81},
        ]
        out = prefer_hungry_rosters(ranked, limit=8, max_hungry=4, min_fit=35)
        self.assertEqual([b["id"] for b in out], [2])

    def test_caps_hungry_slots(self):
        ranked = [{"id": i, "roster_hunger": 10 if i <= 6 else 0, "match_score": 70} for i in range(1, 9)]
        out = prefer_hungry_rosters(ranked, limit=8, max_hungry=4)
        self.assertEqual([b["id"] for b in out[:4]], [1, 2, 3, 4])
        self.assertEqual(len(out), 8)

    def test_filled_rosters_stay_in_fit_order(self):
        ranked = [
            {"id": 1, "roster_hunger": 0, "match_score": 91},
            {"id": 2, "roster_hunger": 0, "match_score": 80},
        ]
        out = prefer_hungry_rosters(ranked, pool=ranked, limit=8, max_hungry=4)
        self.assertEqual([b["id"] for b in out], [1, 2])


if __name__ == "__main__":
    unittest.main()
