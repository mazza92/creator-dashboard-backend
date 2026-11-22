"""The out-of-free-pitches card shows in the same response (last free send, first out-of-credits ask), not only on
the next visit; and tracker follow-ups that aren't due yet are labelled so Polly doesn't call them due."""

import unittest
from datetime import datetime, timedelta, timezone
from unittest import mock

import routes.polly as R
from services.polly_tracker import _due_note, session_context_text

ROOM = {"momentum": {"sent": 3, "opened": 1, "replied": 0}, "followup": None, "checklist": [], "drafts": [],
        "plan_count": 0, "reset": "Dec 1"}


class FollowOn(unittest.TestCase):
    def _run(self, room=ROOM, due=True):
        saved = []
        with mock.patch.object(R, "load_thread", return_value={"messages": [{"role": "user", "content": "hi"}],
                                                               "notes": {}}), \
             mock.patch.object(R, "credits_out_offer_due", return_value=due), \
             mock.patch.object(R, "_waiting_room", return_value=room), \
             mock.patch.object(R, "_upsell_payload", return_value={"trial": False}), \
             mock.patch.object(R, "save_thread", side_effect=lambda *a, **k: saved.append((a, k))), \
             mock.patch.object(R, "_log_polly_event"), \
             mock.patch("services.quota_trial_test.trial_eligible", return_value=False):
            msg = R._deliver_credits_out_offer(mock.MagicMock(), 7, "Maya", {"remaining": 0}, None,
                                               want_message=True)
        return msg, saved

    def test_posts_and_returns_the_card_without_a_tracker(self):
        msg, saved = self._run()
        self.assertEqual(msg["kind"], "upsell")
        self.assertEqual(msg["waiting_room"]["momentum"]["sent"], 3)
        self.assertTrue(msg["content"].startswith("Maya, your free pitches are out"))
        self.assertEqual(len(saved), 1)
        notes = saved[0][1].get("notes") if saved[0][1] else saved[0][0][3]
        self.assertIn("credits_out_offer_month", notes)

    def test_nothing_sent_yet_nothing_posted(self):
        msg, saved = self._run(room={**ROOM, "momentum": {"sent": 0}})
        self.assertIsNone(msg)
        self.assertEqual(saved, [])

    def test_once_a_month(self):
        msg, saved = self._run(due=False)
        self.assertIsNone(msg)


class DueNote(unittest.TestCase):
    def test_future_follow_up_is_not_due(self):
        later = (datetime.now(timezone.utc) + timedelta(days=4)).isoformat()
        self.assertIn("not yet", _due_note({"due_at": later}))
        text = session_context_text({"active_tasks": [{"type": "follow_up_due", "brand_name": "Cozy Earth",
                                                       "status": "pending", "due_at": later}]})
        self.assertIn("Cozy Earth (pending, due", text)
        self.assertIn("not yet", text)

    def test_past_due(self):
        past = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
        self.assertNotIn("not yet", _due_note({"due_at": past}))
        self.assertEqual(_due_note({}), "")


if __name__ == "__main__":
    unittest.main()
