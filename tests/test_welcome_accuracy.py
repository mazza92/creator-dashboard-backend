"""Welcome accuracy: clean brand names, pitches follow the plan's paid/gifted offer."""
import unittest
from unittest.mock import MagicMock, patch

from services.brand_names import clean_brand_name, clean_names_in_pitch


class Names(unittest.TestCase):
    def test_suffixes(self):
        self.assertEqual(clean_brand_name("Recharge Co., Ltd."), "Recharge")
        self.assertEqual(clean_brand_name("Benigna Parfums LLC"), "Benigna Parfums")
        self.assertEqual(clean_brand_name("TOMI (US)"), "TOMI")
        self.assertEqual(clean_brand_name("Dutch Pet, Inc."), "Dutch Pet")
        self.assertEqual(clean_brand_name("Co."), "Co.")  # never empties a name
        self.assertEqual(clean_brand_name("Coco & Eve"), "Coco & Eve")
        self.assertEqual(clean_brand_name("Anua"), "Anua")

    def test_pitch_uses_clean_name(self):
        out = clean_names_in_pitch({"subject": "UGC for Recharge Co., Ltd.?", "body": "Hi Recharge Co., Ltd. team,",
                                    "brand_name": "Recharge Co., Ltd."}, "Recharge Co., Ltd.")
        self.assertEqual(out["subject"], "UGC for Recharge?")
        self.assertEqual(out["body"], "Hi Recharge team,")
        self.assertEqual(out["brand_name"], "Recharge")


class PlannedOffer(unittest.TestCase):
    def _run(self, planned, notes):
        from services import polly_tools as pt
        from tests.test_first_session_wow import _state
        state = _state(notes=notes)
        with patch("services.creator_tracker.planned_offer", return_value=planned), \
             patch.object(pt, "apply_paid_ask_to_pitch", side_effect=lambda p, **k: {**p, "ask": "paid"}) as paid, \
             patch.object(pt, "apply_gifted_ask_to_pitch", side_effect=lambda p, **k: {**p, "ask": "gifted"}) as gifted, \
             patch.object(pt, "pitch_pref_inputs", return_value={"slots": {}, "facts": {}}), \
             patch.object(pt, "apply_pitch_prefs", side_effect=lambda p, *a, **k: p):
            out = pt._dress_pitch(state, {"brand_id": 5, "body": "Hi"}, {}, "")
        return out[0] if isinstance(out, tuple) else out

    def test_planned_paid_gets_paid_ask(self):
        self.assertEqual(self._run("paid", {})["ask"], "paid")

    def test_planned_gifted_gets_gifted_ask_even_if_creator_prefers_paid(self):
        self.assertEqual(self._run("gifted", {"deal_intent": "paid"})["ask"], "gifted")

    def test_gifted_only_creator_never_gets_paid(self):
        self.assertNotEqual(self._run("paid", {"prefs": {"gifted_only": True}}).get("ask"), "paid")


if __name__ == "__main__":
    unittest.main()


class WelcomeCardLines(unittest.TestCase):
    """The welcome card shows which of their niches a brand is in and one true line about it."""

    def test_matched_niche_from_category_or_gemini_tag(self):
        from services.creator_tracker import matched_niche
        self.assertEqual(matched_niche("travel", [], ["travel"]), "travel")
        self.assertEqual(matched_niche("fashion", ["fashion", "travel"], ["travel"]), "travel")
        self.assertEqual(matched_niche("beauty", None, ["travel"]), "beauty")

    def test_why_prefers_evidence_then_product_then_niche(self):
        from services.creator_tracker import welcome_why
        self.assertEqual(welcome_why({"reply_reason": "3 creators your size heard back"}, "travel"),
                         "3 creators your size heard back")
        self.assertEqual(welcome_why({"reply_reason": "Fits your travel content", "hero_product": "neck pillow"},
                                     "travel"), "Their neck pillow fits your travel content")
        self.assertEqual(welcome_why({}, "travel"), "Fits your travel content")
        self.assertEqual(welcome_why({}, "travel", hot_reason="Running 14 creator-style ads"),
                         "Running 14 creator-style ads")


class WelcomeTags(unittest.TestCase):
    def test_tags_lead_with_the_matched_niche(self):
        from services.creator_tracker import welcome_tags
        self.assertEqual(welcome_tags("fashion", ["fashion", "travel"], "travel"), ["Travel", "Fashion"])
        self.assertEqual(welcome_tags("other", [], None), [])
