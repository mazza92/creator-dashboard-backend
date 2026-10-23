"""Polly P0 trust fixes: real answers instead of the canned menu, pitch edits that really
happen, statuses only from logged sends, and no repeated nudges or test brands."""

import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import services.polly_brain as brain  # noqa: E402
import services.polly_tools as tools  # noqa: E402
import services.polly_tracker as tracker  # noqa: E402
from services.new_brand_push import looks_like_test_brand  # noqa: E402
from services.polly_faq import faq_answer, faq_key  # noqa: E402
from services.polly_fallback import CANNED_MENU, no_tool_reply, template_reply  # noqa: E402
from services.polly_memory import repeats_recent_note  # noqa: E402
from services.polly_pitch_edit import (  # noqa: E402
    apply_pitch_prefs,
    revise_pitch,
    strip_engagement,
    wants_no_engagement,
)
from services.polly_prefs import merge_prefs, parse_preferences, prefs_context  # noqa: E402
from tests.test_polly_brain import BRANDS, _Patched, make_state, text  # noqa: E402

APPENDIX_A = [
    "After applying, if it says I'm on their gifted list, what does that mean?",
    "Will they reach out via email or on here?",
    "When should I hear back?",
    "How did MERIT add me to their list?",
    "When I apply to a campaign, is my application sent automatically or do I need to email the brand too?",
    "If I still need to copy each pitch myself, what exactly is automated on Pro?",
    "i dont think it sent",
    "Where do I see if they replied?",
    "Can I remove an application and get my credit back?",
    "How do I cancel my subscription?",
]

PITCH = {
    "brand_name": "Glossier", "brand_id": 811, "subject": "Glossier x Mia",
    "email": "pr@glossier.example",
    "body": "Hi Glossier team,\n\nI make skincare content on TikTok for 4.2K followers with 6.2% engagement. "
            "My audience is 18-24.\n\nBest,\nMia",
}


class FaqTests(unittest.TestCase):
    def test_every_appendix_question_has_its_own_answer(self):
        keys = [faq_key(q) for q in APPENDIX_A]
        self.assertTrue(all(keys), dict(zip(APPENDIX_A, keys)))
        self.assertEqual(len(set(keys)), len(keys))

    def test_cancel_answer_gives_the_real_path(self):
        self.assertIn("Account Settings", faq_answer("how do i cancel my subscription"))

    def test_real_messages_never_get_the_canned_menu(self):
        for q in APPENDIX_A + ["I just want to make sure I fully understand how the application process works"]:
            self.assertNotEqual(no_tool_reply(q), CANNED_MENU, q)
        self.assertEqual(no_tool_reply("hi"), CANNED_MENU)
        self.assertEqual(no_tool_reply(""), CANNED_MENU)

    def test_template_with_no_tools_answers_the_question(self):
        state = make_state(user_text="Where do I see if they replied?")
        self.assertIn("inbox", template_reply(state))

    def test_faq_is_in_the_brain_prompt(self):
        self.assertIn("HOW NEWCOLLAB WORKS", brain._system_prompt(make_state(), ""))


class GuardrailTests(unittest.TestCase):
    def test_explaining_the_timeline_is_not_a_send_claim(self):
        state = make_state(user_text="Where do I see if they replied?")
        self.assertEqual(brain.reply_problems(state, "Replies land in your inbox, and I track them on your Timeline."), [])
        self.assertTrue(brain.reply_problems(state, "Done, it's on your Timeline now."))

    def test_salvage_keeps_the_answer_and_drops_the_bad_line(self):
        state = make_state(user_text="how do i cancel my subscription")
        reply = ("Tap your profile picture, open **Account Settings**, then **Cancel subscription**. "
                 "Or pitch **Zorblax** instead.")
        kept = brain.salvage_reply(state, reply)
        self.assertIn("Cancel subscription", kept)
        self.assertNotIn("Zorblax", kept)

    def test_check_reply_salvages_instead_of_canned_menu(self):
        state = make_state(user_text="how do i cancel my subscription")
        gemini = MagicMock()
        gemini.call.return_value = text("Open **Account Settings** and cancel there. Try **Zorblax** too.")
        out = brain.check_reply(state, "Open **Account Settings** and cancel there. Try **Zorblax** too.",
                                gemini=gemini, system="s", contents=[])
        self.assertIn("Account Settings", out)
        self.assertNotIn(CANNED_MENU, out)

    def test_edit_claim_without_an_edit_is_flagged(self):
        state = make_state(user_text="remove my engagement")
        self.assertTrue(brain.reply_problems(state, "Done, I've updated the pitch without engagement."))
        self.assertEqual(brain.reply_problems(state, "I couldn't change the pitch yet."), [])
        state.pitch_update = dict(PITCH)
        state.pitch = dict(PITCH)
        self.assertEqual(brain.reply_problems(state, "Done, I've updated the pitch without engagement."), [])


class PitchEditTests(unittest.TestCase):
    def test_engagement_requests(self):
        self.assertTrue(wants_no_engagement("I don't think I should be sharing my engagement numbers"))
        self.assertTrue(wants_no_engagement("remove my engagement rate"))
        self.assertTrue(wants_no_engagement("take the engagement out"))
        self.assertFalse(wants_no_engagement("how can I grow my engagement? I don't use ads"))

    def test_strip_keeps_the_rest_of_the_sentence(self):
        self.assertEqual(
            strip_engagement("I'm a beauty creator (3,900 followers, 8.1% engagement rate) and I love your serum."),
            "I'm a beauty creator (3,900 followers) and I love your serum.",
        )
        out = strip_engagement(PITCH["body"])
        self.assertNotIn("engagement", out.lower())
        self.assertIn("4.2K followers", out)

    def test_revise_returns_a_new_version_with_a_real_diff(self):
        res = revise_pitch(PITCH, "remove my engagement rate")
        self.assertTrue(res["changed"])
        self.assertEqual(res["pitch"]["version"], 2)
        self.assertNotEqual(res["pitch"]["body"], PITCH["body"])
        self.assertNotIn("6.2%", res["pitch"]["mailto"])
        self.assertEqual(res["pref"], {"show_engagement_rate": False})

    def test_nothing_to_remove_is_an_honest_no(self):
        res = revise_pitch({"subject": "Hi", "body": "Hi team, I make beauty content."}, "remove engagement")
        self.assertFalse(res["changed"])
        self.assertTrue(res["why_not"])

    def test_llm_edit_cannot_invent_numbers(self):
        with patch("services.polly_pitch_edit._llm_edit",
                   return_value={"subject": PITCH["subject"], "changed": True,
                                 "body": PITCH["body"].replace("4.2K", "40K")}):
            res = revise_pitch(PITCH, "make it sound bigger")
        self.assertFalse(res["changed"])
        with patch("services.polly_pitch_edit._llm_edit",
                   return_value={"subject": PITCH["subject"], "changed": True,
                                 "body": PITCH["body"].replace("4.2K", "5K")}):
            self.assertTrue(revise_pitch(PITCH, "I have 5K followers now")["changed"])

    def test_preference_applies_to_future_pitches(self):
        notes = merge_prefs({}, parse_preferences("please don't mention my engagement rate in pitches"))
        self.assertIs(notes["prefs"]["show_engagement_rate"], False)
        self.assertIn("engagement", prefs_context(notes))
        self.assertNotIn("engagement", apply_pitch_prefs(dict(PITCH), notes)["body"].lower())
        self.assertEqual(apply_pitch_prefs(dict(PITCH), {})["body"], PITCH["body"])


class EditPitchToolTests(_Patched):
    def test_edit_pitch_updates_the_card_and_saves_the_rule(self):
        state = make_state(user_text="remove my engagement",
                           messages=[{"role": "assistant", "content": "Here.", "pitch": dict(PITCH)},
                                     {"role": "user", "content": "remove my engagement"}])
        res = tools.run_tool(state, "edit_pitch", {"instruction": "remove my engagement"})
        self.assertEqual(res["status"], "pitch_updated")
        self.assertTrue(res["saved_for_future_pitches"])
        self.assertNotIn("engagement", state.pitch_update["body"].lower())
        self.assertTrue(state.pitch_update["edited_at"])
        self.assertIs(state.notes["prefs"]["show_engagement_rate"], False)

    def test_no_card_is_reported(self):
        state = make_state(user_text="change my pitch")
        self.assertEqual(tools.run_tool(state, "edit_pitch", {"instruction": "shorter"})["status"],
                         "no_draft_on_screen")
        self.assertIn("no pitch", template_reply(state).lower())

    def test_fake_edit_claim_is_repaired(self):
        state = make_state(user_text="its the same nothing changed")
        out, _ = self.run_brain(state, [
            text("Sorry! I've updated the pitch now."),
            text("I haven't changed it yet. Tell me what to change."),
        ])
        self.assertNotIn("updated", out.lower())


class StatusGateTests(_Patched):
    def test_reply_without_a_send_asks_first(self):
        state = make_state(user_text="Gisou said they're interested")
        with patch.object(tracker, "contact_logged", return_value=False), \
                patch.object(tracker, "create_task") as create, \
                patch.object(tracker, "add_event") as event, \
                patch.object(tracker, "log_intent"), \
                patch.object(tools, "find_brand", return_value={"id": 5, "name": "Gisou EU"}):
            res = tools.run_tool(state, "log_brand_update", {"event": "replied_interested", "brand_name": "Gisou"})
        self.assertEqual(res["status"], "needs_sent_first")
        create.assert_not_called()
        event.assert_not_called()
        self.assertEqual(state.notes["pending_brand_update"]["event"], "replied_interested")
        self.assertIn("go out from your email", template_reply(state))

    def test_send_then_logs_the_pending_reply(self):
        state = make_state(user_text="Yes, I sent Gisou EU",
                           notes={"pending_brand_update": {"event": "replied_interested", "brand_id": 5,
                                                           "brand_name": "Gisou EU"}})
        applied = []
        with patch.object(tools, "find_brand", return_value={"id": 5, "name": "Gisou EU"}), \
                patch.object(tracker, "apply_lifecycle_intent",
                             side_effect=lambda conn, cid, life, brand: applied.append(life["intent"]) or
                             {"action": "interested+awaiting_pr", "say_hint": "x"}), \
                patch.object(tracker, "log_intent"), \
                patch.object(tracker, "load_creator_context", return_value={}):
            res = tools.run_tool(state, "log_pitch_sent", {"brand_name": "Gisou EU", "brand_id": 5})
        self.assertEqual(res["status"], "logged")
        self.assertEqual(res["then_logged"], "replied_interested")
        self.assertEqual(applied, ["brand_replied_interested"])
        self.assertNotIn("pending_brand_update", state.notes)

    def test_apply_lifecycle_intent_blocks_without_contact(self):
        with patch.object(tracker, "contact_logged", return_value=False), \
                patch.object(tracker, "close_tasks") as close:
            out = tracker.apply_lifecycle_intent(MagicMock(), 7, {"intent": "brand_replied_interested"},
                                                 {"id": 5, "name": "Gisou EU"})
        self.assertEqual(out["action"], "needs_sent_first")
        close.assert_not_called()


class DedupeTests(unittest.TestCase):
    def test_same_copy_twice_is_a_repeat(self):
        msgs = [{"role": "assistant", "content": "Ok last one for **YTTP**. Day 10, still quiet."},
                {"role": "user", "content": "ok"}]
        self.assertTrue(repeats_recent_note(msgs, "Ok last one for **YTTP**.  Day 10, still quiet."))
        self.assertFalse(repeats_recent_note(msgs, "Quick pulse on **YTTP**"))

    def _deliver(self, thread_msgs, logged):
        nudge = {"task_id": 1, "brand_id": 9, "brand_name": "YTTP", "key": "follow_up_d10",
                 "message": "Ok last one for **YTTP**. Day 10, still quiet."}
        saved = []
        with patch.object(tracker, "mark_nudged"), patch.object(tracker, "add_event"), \
                patch("services.polly_memory.load_thread", return_value={"messages": thread_msgs, "notes": {}}), \
                patch("services.polly_memory.save_thread", side_effect=lambda *a, **k: saved.append(a)), \
                patch("services.polly_memory.note_sent_within", return_value=logged), \
                patch("services.polly_memory.record_note_sent"):
            return tracker.deliver_nudge(MagicMock(), 7, nudge), saved

    def test_replayed_cron_posts_the_nudge_once(self):
        ok, saved = self._deliver([], logged=False)
        self.assertTrue(ok)
        self.assertEqual(len(saved), 1)
        again, saved = self._deliver([], logged=True)
        self.assertFalse(again)
        self.assertEqual(saved, [])
        repeat, saved = self._deliver([{"role": "assistant", "content": "Ok last one for **YTTP**. Day 10, still quiet."}],
                                      logged=False)
        self.assertFalse(repeat)

    def test_test_brands_never_push(self):
        self.assertTrue(looks_like_test_brand({"brand_name": "Testing", "hero_product": "Test product"}))
        self.assertTrue(looks_like_test_brand({"brand_name": "Acme", "slug": "test-acme"}))
        for real in ("Testo Charge", "YorkTest (US)", "Pass A Drug Test", "SneakPeek Test"):
            self.assertFalse(looks_like_test_brand({"brand_name": real}), real)


if __name__ == "__main__":
    unittest.main()
