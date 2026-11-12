"""First run "I'll pitch brands myself": the lined-up brands as cards, no pitches written, credits untouched."""

import unittest
from types import SimpleNamespace
from unittest.mock import patch

from services import polly_tools as pt


class FakeState:
    def __init__(self):
        self.creator_id = 7
        self.notes = {}
        self.brands = []
        self.task_chips = []
        self.final_say = None

    def db(self):
        return None

    def is_pitched(self, row):
        return row.get("id") == 3


ROUTES = SimpleNamespace(
    _hydrate_brand_cards=lambda rows: [{**r, "hydrated": True} for r in rows],
    _unlock_balance=lambda cid: {"is_unlimited": False, "remaining": 3},
)


class ShowBrands(unittest.TestCase):
    def run_tool(self, ids, balance=None):
        state = FakeState()
        routes = SimpleNamespace(**{**ROUTES.__dict__, **({"_unlock_balance": lambda cid: balance} if balance else {})})
        with patch.object(pt, "_routes", return_value=routes), \
                patch.object(pt, "find_brand", side_effect=lambda st, brand_id=None: {"id": brand_id, "name": f"B{brand_id}"}), \
                patch.object(pt, "draft_pitch_batch") as batch, patch.object(pt, "draft_pitch") as single:
            out = pt.show_brands(state, {"brand_ids": ids})
            batch.assert_not_called()
            single.assert_not_called()
        return state, out

    def test_cards_only_skips_pitched_and_dupes(self):
        state, out = self.run_tool([1, 2, 2, 3, 4])
        self.assertTrue(out["ok"])
        self.assertEqual([b["id"] for b in state.brands], [1, 2, 4])
        self.assertTrue(all(b.get("hydrated") for b in state.brands))
        self.assertIn("one of your 3 free pitches", state.final_say)
        self.assertEqual(state.task_chips[0]["action"], "open_directory")

    def test_pro_copy_has_no_credits(self):
        state, _ = self.run_tool([1], balance={"is_unlimited": True})
        self.assertNotIn("free pitch", state.final_say)

    def test_nothing_left(self):
        _state, out = self.run_tool([3])
        self.assertFalse(out["ok"])

    def test_button_only(self):
        self.assertIs(pt._BY_NAME["show_brands"], pt.show_brands)
        self.assertNotIn("show_brands", [t["name"] for t in pt.TOOLS])


if __name__ == "__main__":
    unittest.main()
