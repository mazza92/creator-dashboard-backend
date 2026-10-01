import json
import unittest

from services.polly import coaching_moves, pitch_coach_line, pitch_coaching_context
from services.polly_persona import persona_profile_audit, persona_week_plan

ROWS = [
    {
        "brand_name": "Corsair",
        "ai_status": "poor_fit",
        "ai_coaching": json.dumps({
            "observation": "Your recent posts are fashion, not gaming setups.",
            "action": "Post one desk-setup video",
            "coach_note": "Show the product in use.",
        }),
        "ai_quick_wins": json.dumps([{"action_title": "Pin your best brand collaboration"}]),
    },
    {
        "brand_name": "Glow Co",
        "ai_status": "almost",
        "ai_coaching": None,
        "ai_quick_wins": [{"action_title": "Pin your best brand collaboration"}],
    },
]


class CoachingMovesTests(unittest.TestCase):
    def test_most_repeated_first(self):
        self.assertEqual(
            coaching_moves(ROWS),
            ["Pin your best brand collaboration", "Post one desk-setup video"],
        )

    def test_empty(self):
        self.assertEqual(coaching_moves(None), [])
        self.assertEqual(coaching_moves([{"ai_coaching": "not json"}]), [])


class CoachingContextTests(unittest.TestCase):
    def test_renders_notes(self):
        text = pitch_coaching_context(ROWS)
        self.assertIn("Pitch analyses", text)
        self.assertIn("- Corsair (stretch match) | gap: Your recent posts are fashion", text)
        self.assertIn("next move: Post one desk-setup video", text)
        self.assertIn("- Glow Co (good match) | next move: Pin your best brand collaboration", text)
        self.assertIn("Recurring next move: Pin your best brand collaboration (in 2 reviews)", text)

    def test_groups_reworded_moves_and_trims_on_words(self):
        rows = [
            {"brand_name": "A", "ai_coaching": {"action": "Reactivate your profile by posting tips weekly"}},
            {"brand_name": "B", "ai_coaching": {"action": "Reactivate your profile by showing a fitness journey"}},
            {"brand_name": "C", "ai_coaching": {"coach_note": "word " * 60}},
        ]
        text = pitch_coaching_context(rows)
        self.assertIn("Recurring next move: Reactivate your profile by posting tips weekly (in 2 reviews)", text)
        self.assertTrue(text.splitlines()[3].endswith("word…"))

    def test_empty_when_no_rows(self):
        self.assertEqual(pitch_coaching_context([]), "")


class PitchCoachLineTests(unittest.TestCase):
    def test_good_fit_is_silent(self):
        self.assertEqual(pitch_coach_line({"status": "almost"}, "Glow Co"), "")
        self.assertEqual(pitch_coach_line(None), "")

    def test_stretch_names_fix_and_alternatives(self):
        line = pitch_coach_line({
            "status": "poor_fit",
            "coaching": {"action": "Post one desk-setup video."},
            "better_matches": [{"brand_name": "Aritzia"}, {"brand_name": "Corsair"}, {"brand_name": "Reformation"}],
        }, "Corsair")
        self.assertIn("**Corsair** is a stretch match", line)
        self.assertIn("Next move: Post one desk-setup video.", line)
        self.assertIn("Better odds right now: **Aritzia**, **Reformation**.", line)

    def test_not_yet_uses_quick_win_without_alternatives(self):
        line = pitch_coach_line({
            "status": "not_yet",
            "quick_win": {"action_title": "Lead the kit with a real number"},
            "better_matches": [{"brand_name": "Aritzia"}],
        }, "Glow Co")
        self.assertIn("Worth sending", line)
        self.assertIn("Next move: Lead the kit with a real number.", line)
        self.assertNotIn("Aritzia", line)


class PersonaCoachingTests(unittest.TestCase):
    def test_audit_ranks_fix_after_unpublished_kit(self):
        say = persona_profile_audit("", kit={"found": True, "published": False}, coaching=["Post one desk-setup video"])
        self.assertIn("1. Publish **My Kit**", say)
        self.assertIn("2. From your last pitch reviews: Post one desk-setup video.", say)

    def test_audit_unchanged_without_coaching(self):
        say = persona_profile_audit("", kit={"found": True, "published": True, "has_rates": True})
        self.assertNotIn("pitch reviews", say)

    def test_week_plan_uses_fix(self):
        say = persona_week_plan("", {}, coaching=["Post one desk-setup video"])
        self.assertIn("**Post one desk-setup video**", say)
        self.assertNotIn("pitch reviews", persona_week_plan("", {}))


if __name__ == "__main__":
    unittest.main()
