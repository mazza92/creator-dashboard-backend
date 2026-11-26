"""Nov 23 Polly audit: vague replies aren't guessed, drafts holding every credit offer a swap, a repeat paywall
is one line, and a free creator's logged reply gets one Pro line."""
import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from services.polly_persona import persona_paywall_say, persona_reply_moment
from services.polly_tools import _paywall_seen_recently, _waiting_swap_chips, vague_reply_text


class VagueReply(unittest.TestCase):
    def test_bare_replied_is_vague(self):
        for text in ("They replied", "Rhode Skin replied", "They replied!", "Mcobeauty got back to me",
                     "Rare Beauty by Selena Gomez replied"):
            self.assertTrue(vague_reply_text(text), text)

    def test_replies_with_content_are_not_vague(self):
        for text in ("They passed", "They said yes, sending PR", "They replied asking my rates",
                     "Saje Wellness replied with a new email", ""):
            self.assertFalse(vague_reply_text(text), text)


class WaitingSwap(unittest.TestCase):
    def _state(self, items):
        return SimpleNamespace(notes={"pitch_batch": {"items": items}})

    def test_offers_one_swap_per_unsent_pitch(self):
        state = self._state([
            {"brand_id": 1, "brand_name": "Nopalera"},
            {"brand_id": 2, "brand_name": "Cue Company", "sent_at": "2026-11-23T10:00:00+00:00"},
            {"brand_id": 3, "brand_name": "The Pelvic People"},
        ])
        chips = _waiting_swap_chips(state, 9, "The Good Patch")
        self.assertEqual([c["replace_brand_id"] for c in chips], [1, 3])
        self.assertTrue(all(c["action"] == "pitch_batch" and c["brand_ids"] == [9] for c in chips))
        self.assertEqual(chips[0]["label"], "Swap out Nopalera")

    def test_no_batch_no_swap(self):
        self.assertEqual(_waiting_swap_chips(self._state([]), 9, "X"), [])
        self.assertEqual(_waiting_swap_chips(self._state([{"brand_id": 1, "brand_name": "A"}]), None, "X"), [])


class RepeatPaywall(unittest.TestCase):
    def test_recent_paywall_is_repeat(self):
        now = datetime.now(timezone.utc)
        self.assertTrue(_paywall_seen_recently({"paywall_at": (now - timedelta(days=2)).isoformat()}))
        self.assertFalse(_paywall_seen_recently({"paywall_at": (now - timedelta(days=9)).isoformat()}))
        self.assertFalse(_paywall_seen_recently({}))

    def test_repeat_copy_is_one_line(self):
        say = persona_paywall_say("Mcobeauty", repeat=True)
        self.assertIn("**Mcobeauty** is on Pro too", say)
        self.assertNotIn("\n", say)
        self.assertIn("$19/mo", say)
        self.assertIn("\n", persona_paywall_say("Mcobeauty"))


class ReplyMoment(unittest.TestCase):
    def test_copy_names_the_brand(self):
        say = persona_reply_moment("Rhode Skin")
        self.assertIn("**Rhode Skin**", say)
        self.assertIn("$19/mo", say)

    def test_added_once_for_free_creators(self):
        from routes.polly import _reply_moment

        state = SimpleNamespace(reply_logged=True, paywall=False, balance={"is_unlimited": False}, notes={},
                                chip={"brand_name": "Rhode Skin"}, paywall_moment=None)
        say, chips = _reply_moment(state, "Here's your reply.", [], "")
        self.assertIn("Rhode Skin", say)
        self.assertEqual(chips[-1]["action"], "unlock_pro")
        self.assertEqual(state.paywall_moment, {"moment": "reply"})
        again, chips2 = _reply_moment(state, "Ok.", [], "checkin_interested")
        self.assertEqual(again, "Ok.")
        self.assertEqual(chips2, [])

    def test_never_for_pro(self):
        from routes.polly import _reply_moment

        state = SimpleNamespace(reply_logged=True, paywall=False, balance={"is_unlimited": True}, notes={},
                                chip=None, paywall_moment=None)
        self.assertEqual(_reply_moment(state, "Hi.", [], "")[0], "Hi.")


if __name__ == "__main__":
    unittest.main()


class OneProLinePerReply(unittest.TestCase):
    def test_reply_moment_skips_when_turn_already_sells_pro(self):
        from routes.polly import _reply_moment

        state = SimpleNamespace(reply_logged=True, paywall=False, balance={"is_unlimited": False}, notes={},
                                chip=None, paywall_moment=None)
        say = "Here's your reply.\n\nOn Pro I keep this going for you: up to 20 brand pitches daily."
        self.assertEqual(_reply_moment(state, say, [], "")[0], say)
        self.assertIsNone(state.paywall_moment)

    def test_reply_moment_reads_well(self):
        self.assertIn("I send up to 20 brand pitches daily to brands that fit you", persona_reply_moment("X"))


class OneFreeSwap(unittest.TestCase):
    """Nov 26 audit: free creators swapped up to 10 times, each swap showing a new brand email, never paywalled."""

    def test_first_swap_free_then_spent(self):
        from services.polly_tools import free_swap_used, record_free_swap

        notes = {}
        self.assertFalse(free_swap_used(notes))
        record_free_swap(notes)
        self.assertTrue(free_swap_used(notes))

    def test_resets_each_month(self):
        from services.polly_tools import free_swap_used, record_free_swap

        notes = {}
        record_free_swap(notes, datetime(2026, 11, 30, tzinfo=timezone.utc))
        self.assertTrue(free_swap_used(notes, datetime(2026, 11, 30, tzinfo=timezone.utc)))
        self.assertFalse(free_swap_used(notes, datetime(2026, 12, 1, tzinfo=timezone.utc)))
        record_free_swap(notes, datetime(2026, 12, 1, tzinfo=timezone.utc))
        self.assertEqual(notes["free_swaps"], {"month": "2026-12", "n": 1})

    def test_bad_record_is_unused(self):
        from services.polly_tools import free_swap_used

        self.assertFalse(free_swap_used({"free_swaps": "x"}))
        self.assertFalse(free_swap_used({"free_swaps": {"month": "2026-11", "n": "?"}},
                                        datetime(2026, 11, 2, tzinfo=timezone.utc)))
