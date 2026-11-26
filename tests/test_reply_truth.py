"""Nov 26 audit: Polly told creators brands replied when they hadn't, or said more than the creator told it."""
import unittest
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import services.polly_tools as pt
from services import polly_tracker as tracker


class OutgoingEmail(unittest.TestCase):
    def test_creator_email_to_the_brand(self):
        text = ("Hi BeKynd Beauty Team,\n\nThank you for considering me. My rate for 1 post and 2 UGC videos with "
                "6-month usage would be $100 USD.")
        self.assertTrue(pt.outgoing_email_text(text, "BeKynd Beauty"))
        self.assertTrue(pt.outgoing_email_text("Dear The Good Patch team,\n\n" + "x" * 80, "The Good Patch"))
        self.assertTrue(pt.outgoing_email_text("Hey!\n\nMy kit: https://newcollab.co/kit/maya " + "x" * 50, "Rhode"))

    def test_brand_writing_is_not_outgoing(self):
        self.assertFalse(pt.outgoing_email_text(
            "Hi! Thanks for reaching out to BeKynd Beauty. We'd love to send you our scalp scrub, what's your address?",
            "BeKynd Beauty"))
        self.assertFalse(pt.outgoing_email_text("Hi Maya,\n\nWe'd love to send you a PR box. " + "x" * 40, "Rhode"))
        self.assertFalse(pt.outgoing_email_text("They said yes", "Rhode"))


class VagueHeardBack(unittest.TestCase):
    def test_heard_back_is_vague(self):
        for text in ("I heard back from ZEAL PET", "I heard back", "I got back from Rhode!"):
            self.assertTrue(pt.vague_reply_text(text), text)
        self.assertFalse(pt.vague_reply_text("I heard back from ZEAL PET, they want my address"))


class ReplyFacts(unittest.TestCase):
    def test_event_data_keeps_the_creators_words(self):
        data = tracker.reply_event_data({"source": "creator", "said": "  They passed my email to  marketing "})
        self.assertEqual(data, {"source": "creator", "said": "They passed my email to marketing"})

    def test_replies_on_record_quote_the_creator_and_never_claim_an_inbox(self):
        line = tracker.replies_on_record_text([
            {"event_type": "brand_replied_interested", "brand_name": "P. Louise", "occurred_at": "2026-11-13T19:19",
             "how_we_know": "the creator told Polly", "creator_said": "They passed my email to marketing"},
            {"event_type": "pitch_sent", "brand_name": "Rhode"},
        ])
        self.assertIn("P. Louise: interested (logged 2026-11-13, the creator told Polly)", line)
        self.assertIn('"They passed my email to marketing"', line)
        self.assertIn("never reads their inbox", line)
        self.assertNotIn("Rhode", line)
        self.assertEqual(tracker.replies_on_record_text([{"event_type": "pitch_sent"}]), "")


class Dedupe(unittest.TestCase):
    def test_same_reply_within_minutes_is_not_logged_twice(self):
        with patch.object(tracker, "contact_logged", return_value=True), \
             patch.object(tracker, "_same_reply_logged_recently", return_value=True), \
             patch.object(tracker, "add_event") as add:
            out = tracker.apply_lifecycle_intent(MagicMock(), 7, {"intent": "brand_replied_question", "brand_id": 9},
                                                 {"id": 9, "name": "BeKynd"})
        self.assertEqual(out["action"], "already_logged")
        add.assert_not_called()

    def test_reply_is_logged_with_what_they_said(self):
        with patch.object(tracker, "contact_logged", return_value=True), \
             patch.object(tracker, "_same_reply_logged_recently", return_value=False), \
             patch.object(tracker, "close_tasks"), patch.object(tracker, "create_task", return_value=1), \
             patch.object(tracker, "_open_tasks", return_value=[]), \
             patch.object(tracker, "add_event") as add:
            out = tracker.apply_lifecycle_intent(
                MagicMock(), 7, {"intent": "brand_replied_interested", "brand_id": 9, "said": "they want my address"},
                {"id": 9, "name": "Rhode"})
        self.assertNotIn("said yes", out["say_hint"])
        self.assertEqual(add.call_args.kwargs["event_data"], {"source": "creator", "said": "they want my address"})


class OwnEmailNotLogged(unittest.TestCase):
    def test_pasted_own_email_logs_nothing(self):
        state = SimpleNamespace(user_text="Hi BeKynd Beauty team,\n\nThank you so much! I'd love to collaborate. " * 2,
                                notes={}, task_chips=[], creator_id=7)
        state.add_chips = lambda chips: state.task_chips.extend(chips)
        with patch.object(pt, "find_brand", return_value={"id": 9, "name": "BeKynd Beauty"}), \
             patch.object(pt.tracker, "apply_lifecycle_intent") as apply:
            out = pt.log_brand_update(state, {"event": "replied_question", "brand_name": "BeKynd Beauty"})
        self.assertEqual(out["status"], "creator_own_email")
        apply.assert_not_called()
        self.assertEqual(state.task_chips[0]["id"], "reply_sent")


class Labels(unittest.TestCase):
    def test_no_chip_reads_like_a_status(self):
        from services.polly_pain import CHECKIN_CHIPS
        self.assertIn("I heard back", [c["label"] for c in CHECKIN_CHIPS])
        self.assertNotIn("They replied", [c["label"] for c in CHECKIN_CHIPS])


if __name__ == "__main__":
    unittest.main()
