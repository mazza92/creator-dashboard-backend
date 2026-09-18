import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from services.polly_discovery import (
    advance,
    apply_answer,
    assign_track,
    discovery_complete,
    field_from_history,
    next_field,
    normalize_stage,
    required_categories_for_match,
    skip_discovery,
    starters_for,
)


class DiscoveryTests(unittest.TestCase):
    def test_first_session_starters(self):
        from services.polly_discovery import opener
        chips = starters_for({})
        ids = [c["id"] for c in chips]
        self.assertEqual(ids, ["line_up", "name_a_brand", "more_replies"])
        self.assertEqual(chips[0]["action"], "suggest_brands")
        self.assertTrue(chips[0].get("skip_discovery"))
        self.assertEqual(chips[1]["action"], "ask_brand")
        self.assertEqual(chips[2]["action"], "coach_profile")
        self.assertEqual(chips[2]["label"], "Help me get more replies from brands")
        say = opener("Jined")
        self.assertIn("Creator Assistant", say)
        self.assertIn("What do you want to land first?", say)
        self.assertNotIn("Ready?", say)
        self.assertNotIn("2 minutes", say)

    def test_starters_adapt_after_setup_tap(self):
        chips = starters_for({"discovery_step": 1, "setup_continues": 1})
        ids = [c["id"] for c in chips]
        self.assertEqual(ids[0], "skip_setup")
        self.assertIn("kit_done", ids)
        self.assertEqual(chips[0]["label"], "Skip, show me brands")
        self.assertTrue(chips[0].get("skip_discovery"))

    def test_starters_drop_continue_after_two_taps(self):
        chips = starters_for({"discovery_step": 1, "setup_continues": 2})
        ids = [c["id"] for c in chips]
        self.assertEqual(ids, ["skip_setup", "kit_done"])
        self.assertNotIn("continue_setup", ids)

    def test_pending_draft_starters_lead_with_sent(self):
        chips = starters_for({
            "discovery_skipped_at": "2026-09-18T09:27:06+00:00",
            "pending_pitch": {"id": 129, "name": "Naturium"},
        })
        ids = [c["id"] for c in chips]
        self.assertEqual(ids[0], "i_sent_it")
        self.assertIn("more_brands", ids)
        self.assertNotIn("line_up", ids)

    def test_second_setup_chip_skips_discovery(self):
        from services.polly_discovery import bump_setup_continues, should_auto_skip_setup
        notes = bump_setup_continues({}, "Get me set up", "get_set_up", "discovery")
        self.assertFalse(should_auto_skip_setup(notes))
        notes = bump_setup_continues(notes, "Continue setup", "continue_setup", "discovery")
        self.assertTrue(should_auto_skip_setup(notes))
        self.assertEqual(notes["setup_continues"], 2)

    def test_continue_chip_is_not_a_discovery_answer(self):
        notes, say, pull = advance({"discovery_step": 1}, "Continue setup", first_name="Iza")
        self.assertIsNone(notes.get("goal_30d"))
        self.assertIn("30-60 days", say)
        self.assertFalse(pull)

    def test_pitch_chip_skips_already_done_brand(self):
        chips = starters_for(
            {
                "discovery_completed_at": "2026-09-16T14:32:00+00:00",
                "pitched_brand_names": ["Acure"],
            },
            top_brand="Acure",
        )
        labels = [c["label"] for c in chips]
        self.assertNotIn("Help me pitch Acure", labels)
        self.assertIn("Review my kit", labels)

    def test_advance_starts_with_goal_question(self):
        notes, say, pull = advance({}, "Get me set up", first_name="Mahery")
        self.assertFalse(pull)
        self.assertIn("30-60 days", say)
        self.assertEqual(next_field(notes), "goal_30d")

    def test_answers_complete_discovery(self):
        notes = {}
        notes = apply_answer(notes, "goal_30d", "first gifted PR")
        notes = apply_answer(notes, "stage", "b) 1-3 gifted")
        notes = apply_answer(notes, "niche", "beauty, lifestyle")
        notes = apply_answer(notes, "location", "London")
        notes = apply_answer(notes, "biggest_challenge", "don't know which brands are realistic")
        notes = apply_answer(notes, "dream_brands", "Rhode, Anua")
        self.assertTrue(discovery_complete(notes))
        self.assertEqual(notes["stage"], "early_stage")
        self.assertIn("Rhode", notes["dream_brands"])

    def test_skip_allows_matching(self):
        notes = skip_discovery({})
        self.assertTrue(discovery_complete(notes))

    def test_letter_a_is_just_starting(self):
        self.assertEqual(normalize_stage("a"), "just_starting")
        self.assertEqual(normalize_stage("a)"), "just_starting")

    def test_history_owns_the_field(self):
        history = [{
            "role": "assistant",
            "content": "And where are you based? Country + city if you're happy sharing",
        }]
        notes = {
            "goal_30d": "getting paid",
            "stage": "just_starting",
            "niche": ["skincare"],
            "discovery_step": 4,
        }
        notes, say, pull = advance(notes, "paris france", first_name="Mahery", history=history)
        self.assertEqual(notes.get("location"), "paris france")
        self.assertNotEqual(notes.get("biggest_challenge"), "paris france")
        self.assertIn("frustration", say.lower())
        self.assertFalse(pull)

    def test_none_completes_dreams(self):
        notes = {}
        notes = apply_answer(notes, "goal_30d", "first gifted PR")
        notes = apply_answer(notes, "stage", "a")
        notes = apply_answer(notes, "niche", "skincare")
        notes = apply_answer(notes, "location", "Paris, France")
        notes = apply_answer(notes, "biggest_challenge", "none")
        notes = apply_answer(notes, "dream_brands", "none")
        self.assertTrue(discovery_complete(notes))
        self.assertEqual(notes["dream_brands"], [])
        self.assertEqual(notes["stage"], "just_starting")

    def test_track_defaults_aspiring_and_locks_from_stage(self):
        notes = assign_track({})
        self.assertEqual(notes["polly_track"], "aspiring")
        self.assertTrue(notes.get("polly_track_provisional"))
        notes = apply_answer(notes, "stage", "d) established")
        notes = assign_track(notes)
        self.assertEqual(notes["polly_track"], "established")
        self.assertFalse(notes.get("polly_track_provisional"))
        locked = assign_track({**notes, "stage": "just_starting"})
        self.assertEqual(locked["polly_track"], "established")

    def test_field_from_history_reads_last_question(self):
        field = field_from_history([{
            "role": "assistant",
            "content": "What brands are you dreaming of working with? Names or types",
        }])
        self.assertEqual(field, "dream_brands")

    def test_skincare_wellness_does_not_pull_fitness(self):
        cats = required_categories_for_match(
            {"niche": ["skincare", "wellness"]},
            {"primary_niche": "fitness & wellness", "follower_count": 202},
        )
        self.assertIn("skincare", cats)
        self.assertNotIn("fitness", cats)
        self.assertNotIn("activewear", cats)
        from_scrape = required_categories_for_match(
            {},
            {"primary_niche": "skincare", "secondary_niches": ["wellness", "fitness"]},
        )
        self.assertIn("skincare", from_scrape)
        self.assertNotIn("fitness", from_scrape)


if __name__ == "__main__":
    unittest.main()
