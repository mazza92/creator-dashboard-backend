"""P1 pitch preferences: stored once, applied to every pitch (no DB, no Gemini)."""

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from services import polly_pitch_edit as edit  # noqa: E402
from services.polly_discovery import paid_first  # noqa: E402
from services.polly_prefs import (  # noqa: E402
    clean_pitch_prefs,
    merge_prefs,
    parse_preferences,
    pitch_prefs_line,
    prefs_context,
)

PITCH = {
    "brand_id": 5, "brand_name": "Rhode", "email": "pr@rhode.com",
    "subject": "Rhode x Ana",
    "body": "Hi Rhode team,\n\nI make skincare content on TikTok for 12,400 followers, with 8.1% engagement.\n\n"
            "Would you be open to sending a peptide lip tint? I ship to Lyon, France.\n\nBest,\nAna",
}


class CleanTests(unittest.TestCase):
    def test_valid_values_kept_bad_dropped(self):
        out = clean_pitch_prefs({"lead_metric": "views", "tone": "loud", "platforms": "TikTok, yt, myspace",
                                 "usage_rights_months": "3", "offer_type": "barter", "deliverables": "  1 TikTok  "})
        self.assertEqual(out, {"lead_metric": "views", "platforms": ["tiktok", "youtube"],
                               "usage_rights_months": 3, "deliverables": "1 TikTok"})

    def test_empty_clears(self):
        notes = merge_prefs({}, {"tone": "professional"})
        self.assertEqual(notes["prefs"]["tone"], "professional")
        self.assertNotIn("tone", merge_prefs(notes, {"tone": None})["prefs"])

    def test_short_template_ignored(self):
        self.assertEqual(clean_pitch_prefs({"custom_template": "hi {{brand}}"}), {})

    def test_paid_only_switches_deal_intent(self):
        notes = merge_prefs({"prefs": {"gifted_only": True}}, {"offer_type": "paid_only"})
        self.assertEqual(notes["deal_intent"], "paid")
        self.assertFalse(notes["prefs"]["gifted_only"])
        self.assertTrue(paid_first(notes))
        self.assertIn("paid only", prefs_context(notes))


class ParseTests(unittest.TestCase):
    def test_lead_tone_usage(self):
        out = parse_preferences("Always lead with views, keep it professional and offer 3 months usage")
        self.assertEqual(out["lead_metric"], "views")
        self.assertEqual(out["tone"], "professional")
        self.assertEqual(out["usage_rights_months"], 3)

    def test_engagement_still_parsed(self):
        self.assertFalse(parse_preferences("please don't mention engagement")["show_engagement_rate"])


class LineTests(unittest.TestCase):
    def test_card_line(self):
        line = pitch_prefs_line({"lead_metric": "views", "show_engagement_rate": False,
                                 "deliverables": "1 TikTok + 2 UGC", "usage_rights_months": 3})
        self.assertEqual(line, "Leads with views · no engagement · 1 TikTok + 2 UGC · 3-month usage")

    def test_template_line(self):
        self.assertEqual(pitch_prefs_line({"custom_template": "x" * 50}), "Your template")


class ApplyTests(unittest.TestCase):
    def test_no_prefs_is_untouched(self):
        self.assertIs(edit.apply_pitch_prefs(PITCH, {}), PITCH)

    def test_template_verbatim_with_slots(self):
        tpl = "Hello {{brand}} team! I'm {{first_name}} ({{handle}}) and I'd love to try {{product}}. Ships to {{city}}."
        notes = {"prefs": {"custom_template": tpl, "show_engagement_rate": False}}
        out = edit.apply_pitch_prefs(PITCH, notes, slots={"first_name": "Ana", "handle": "@ana",
                                                          "product": "Peptide Lip Tint", "city": "Lyon, France"})
        self.assertEqual(out["body"], "Hello Rhode team! I'm Ana (@ana) and I'd love to try Peptide Lip Tint. "
                                      "Ships to Lyon, France.")
        self.assertEqual(out["prefs_line"], "Your template")
        self.assertIn("mailto:", out["mailto"])

    def test_template_missing_city_keeps_placeholder(self):
        tpl = "Hello {{brand}} team, this is my long enough template. Ship to {{city}} please."
        out = edit.apply_pitch_prefs(PITCH, {"prefs": {"custom_template": tpl}}, slots={})
        self.assertIn("[CITY, COUNTRY]", out["body"])

    def test_followups_skip_template_and_rewrite(self):
        notes = {"prefs": {"custom_template": "T" * 60, "tone": "professional"}}
        with patch.object(edit, "_llm_apply_prefs") as llm:
            out = edit.apply_pitch_prefs(PITCH, notes, rewrite=False)
        llm.assert_not_called()
        self.assertEqual(out["body"], PITCH["body"])

    def test_rewrite_applied(self):
        new_body = PITCH["body"].replace("Hi Rhode team", "Dear Rhode team")
        with patch.object(edit, "_llm_apply_prefs", return_value={"subject": "Rhode x Ana", "body": new_body}):
            out = edit.apply_pitch_prefs(PITCH, {"prefs": {"tone": "professional"}})
        self.assertTrue(out["body"].startswith("Dear Rhode team"))
        self.assertTrue(out["prefs_applied"])
        self.assertEqual(out["prefs_line"], "professional")

    def test_rewrite_with_invented_number_rejected(self):
        bad = PITCH["body"].replace("12,400 followers", "50,000 followers")
        with patch.object(edit, "_llm_apply_prefs", return_value={"body": bad}):
            out = edit.apply_pitch_prefs(PITCH, {"prefs": {"lead_metric": "followers"}})
        self.assertIn("12,400", out["body"])

    def test_rewrite_may_use_facts_numbers(self):
        body = PITCH["body"].replace("12,400 followers", "videos reaching 48,000 views")
        with patch.object(edit, "_llm_apply_prefs", return_value={"body": body}):
            out = edit.apply_pitch_prefs(PITCH, {"prefs": {"lead_metric": "views"}},
                                         facts={"top_post_views": [48000]})
        self.assertIn("48,000 views", out["body"])

    def test_engagement_stripped_after_rewrite(self):
        with patch.object(edit, "_llm_apply_prefs", return_value={"body": PITCH["body"]}):
            out = edit.apply_pitch_prefs(PITCH, {"prefs": {"tone": "friendly", "show_engagement_rate": False}})
        self.assertNotIn("engagement", out["body"].lower())

    def test_rewrite_losing_handle_rejected(self):
        body = PITCH["body"].replace("on TikTok", "on TikTok (@ana)")
        src = {**PITCH, "body": body}
        with patch.object(edit, "_llm_apply_prefs", return_value={"body": PITCH["body"]}):
            out = edit.apply_pitch_prefs(src, {"prefs": {"tone": "professional"}})
        self.assertIn("@ana", out["body"])

    def test_numbers_compare_across_formats(self):
        self.assertEqual(edit._numbers("12,400 and 12.4K and 3 months and 8.1%"), {"12400", "3", "8.1%"})

    def test_gemini_down_keeps_pitch(self):
        with patch.object(edit, "_llm_apply_prefs", return_value={}):
            out = edit.apply_pitch_prefs(PITCH, {"prefs": {"tone": "friendly"}})
        self.assertEqual(out["body"], PITCH["body"])


class EditSavesGeneralPrefsTests(unittest.TestCase):
    def test_always_instruction_saves_pref(self):
        with patch.object(edit, "_llm_edit", return_value={"changed": False, "why_not": "x"}):
            res = edit.revise_pitch(PITCH, "Always keep it professional from now on")
        self.assertEqual(res["pref"].get("tone"), "professional")

    def test_one_off_instruction_saves_nothing(self):
        with patch.object(edit, "_llm_edit", return_value={"changed": False, "why_not": "x"}):
            res = edit.revise_pitch(PITCH, "make this one more professional")
        self.assertEqual(res["pref"], {})


if __name__ == "__main__":
    unittest.main()
