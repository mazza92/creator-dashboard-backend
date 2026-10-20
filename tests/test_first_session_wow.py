import unittest
from unittest.mock import MagicMock, patch

import routes.polly as R
import services.polly_tools as pt
from app import app
from services.polly_persona import persona_first_matches, persona_rate_card, rate_band, rate_line
from services.polly_turn_state import TurnState

PLACEHOLDER = "[CITY, COUNTRY]"


def _state(**over):
    base = dict(
        creator_id=7, creator={}, first="Maya", scrape={}, kit={}, profile_context="", coach_moves=[],
        balance={"remaining": 3}, notes={}, messages=[], stored_messages=[], suggested=[], tracker_ctx={},
        early_fu={}, career=None, user_text="", chip={},
    )
    base.update(over)
    return TurnState(**base)


class RateTests(unittest.TestCase):
    def test_bands(self):
        small = rate_band(1500)
        self.assertEqual(small["video"], (50, 100))
        self.assertEqual(small["video_rate"], 75)
        self.assertEqual(small["photo_rate"], 45)
        self.assertEqual(rate_band(60000)["video"], (250, 500))
        self.assertIsNone(rate_band(0))
        self.assertIsNone(rate_band(None))

    def test_whitelisting_only_in_its_band(self):
        self.assertIn("whitelisting", rate_line(5000))
        self.assertNotIn("whitelisting", rate_line(1500))

    def test_rate_card_copy(self):
        self.assertIn("$50–$100 a video", persona_rate_card(1500))
        self.assertIn("won't invent one", persona_rate_card(None))


class BatchChipTests(unittest.TestCase):
    BRANDS = [{"id": 1, "name": "A"}, {"id": 2, "name": "B"}, {"id": 3, "name": "C"}]

    def test_offered_when_every_pitch_is_covered(self):
        chip = R._batch_chip(self.BRANDS, {"remaining": 3})
        self.assertEqual(chip["brand_ids"], [1, 2, 3])
        self.assertEqual(chip["label"], "Write all 3 pitches")
        self.assertEqual(chip["action"], "pitch_batch")

    def test_pro_always_gets_it(self):
        self.assertIsNotNone(R._batch_chip(self.BRANDS, {"is_unlimited": True, "remaining": None}))

    def test_not_offered_short_of_credits_or_brands(self):
        self.assertIsNone(R._batch_chip(self.BRANDS, {"remaining": 1}))
        self.assertIsNone(R._batch_chip(self.BRANDS[:1], {"remaining": 3}))

    def test_capped_to_remaining_credits(self):
        chip = R._batch_chip(self.BRANDS, {"remaining": 2})
        self.assertEqual(chip["brand_ids"], [1, 2])
        self.assertEqual(chip["label"], "Write 2 pitches")
        self.assertEqual(R._batch_chip(self.BRANDS[:2], {"remaining": 3})["label"], "Write both pitches")


class TypedBatchTests(unittest.TestCase):
    SHOWN = [{"role": "user", "content": "x"},
             {"role": "assistant", "brands": [{"id": 8051}, {"id": 2}, {"id": 3}]},
             {"role": "user", "content": "pitch all 3 for me"}]

    def _run(self, text):
        state = _state(user_text=text, messages=self.SHOWN)
        with patch.object(R, "run_tool", return_value={"ok": True}) as tool:
            R._run_chip(state, None, "", {})
        return tool

    def test_typed_pitch_all_runs_the_batch(self):
        for text in ("pitch all 3 for me", "write them all", "pitch these", "do all three"):
            tool = self._run(text)
            tool.assert_called_once()
            self.assertEqual(tool.call_args[0][1:], ("draft_pitch_batch", {"brand_ids": [8051, 2, 3]}), text)

    def test_questions_and_chat_go_to_the_brain(self):
        for text in ("how do I pitch them?", "thanks", "which one is best"):
            self._run(text).assert_not_called()


class FirstMatchesCopyTests(unittest.TestCase):
    ANSWERS = {"tier": "replies", "label": "Replies to creators"}

    def test_claims_answers_only_when_cards_back_it(self):
        brands = [{"name": n, "reply_signal": self.ANSWERS} for n in ("A", "B", "C")]
        say = persona_first_matches("Ava", {}, brands, batch=True)
        self.assertIn("answer Newcollab creators who pitch them", say)
        self.assertIn("**Write all 3 pitches**", say)
        self.assertNotIn("?", say)

    def test_partial_answers(self):
        brands = [{"name": "A", "reply_signal": self.ANSWERS}, {"name": "B"}, {"name": "C"}]
        self.assertIn("1 of them answer", persona_first_matches("Ava", {}, brands))

    def test_no_replies_pain_stays_honest_without_evidence(self):
        say = persona_first_matches("Ava", {"pain": ["no_replies"]}, [{"name": "A"}, {"name": "B"}])
        self.assertNotIn("actually answer", say)
        self.assertIn("work with creators your size", say)
        self.assertIn("Tap **Contact**", say)


class DraftBatchTests(unittest.TestCase):
    def test_writes_every_pitch_and_stores_the_batch(self):
        state = _state(notes={"pitched_brand_ids": [4]})
        bodies = {1: "Hi A", 2: f"Hi B, ship to {PLACEHOLDER}", 3: "Hi C"}
        emails = {1: "pr@a.com", 2: "pr@b.com", 3: None}

        def fake_draft(st, args):
            bid = args["brand_id"]
            self.assertTrue(args["confirm_cold"])
            self.assertEqual(args["_prefetched"], ("ok", bid))
            st.pitch = {"brand_id": bid, "brand_name": f"B{bid}", "email": emails[bid],
                        "subject": f"S{bid}", "body": bodies[bid]}
            st.task_chips.append({"id": "i_sent_it"})

        routes = MagicMock()
        routes._invoke_generate_pr_packages.return_value = {1: ("ok", 1), 2: ("ok", 2), 3: ("ok", 3)}
        routes._unlock_balance.return_value = {"remaining": 0}
        find = lambda st, brand_id=None, brand_name=None: {"id": brand_id, "name": f"B{brand_id}"}
        with patch.object(pt, "_routes", return_value=routes), \
             patch.object(pt, "find_brand", side_effect=find), \
             patch.object(pt, "_pitch_location", return_value=({}, "")), \
             patch.object(pt, "draft_pitch", side_effect=fake_draft):
            out = pt.draft_pitch_batch(state, {"brand_ids": [1, 2, 3, 4, 1]})

        self.assertEqual(out["count"], 3)
        jobs = routes._invoke_generate_pr_packages.call_args[0][0]
        self.assertEqual([j["id"] for j in jobs], [1, 2, 3])
        self.assertEqual(len(state.pitch_batch), 3)
        self.assertTrue(state.pitch_batch[1]["needs_location"])
        self.assertEqual(state.task_chips, [])
        self.assertIsNone(state.pitch)
        stored = state.notes["pitch_batch"]["items"]
        self.assertEqual([i["brand_id"] for i in stored], [1, 2, 3])
        self.assertIn("**Send it from my Gmail**", state.final_say)
        self.assertIn("city and country", state.final_say)

    def test_nothing_to_write(self):
        state = _state(notes={"pitched_brand_ids": [1]})
        find = lambda st, brand_id=None, brand_name=None: {"id": brand_id, "name": "A"}
        with patch.object(pt, "find_brand", side_effect=find), patch.object(pt, "_routes"):
            out = pt.draft_pitch_batch(state, {"brand_ids": [1]})
        self.assertFalse(out["ok"])
        self.assertEqual(state.pitch_batch, [])


class BatchLocationTests(unittest.TestCase):
    FIND = staticmethod(lambda st, brand_id=None, brand_name=None: {"id": brand_id, "name": f"B{brand_id}"})

    def test_asks_city_before_spending_credits(self):
        state = _state()
        routes = MagicMock()
        with patch.object(pt, "_routes", return_value=routes), \
             patch.object(pt, "find_brand", side_effect=self.FIND), \
             patch.object(pt, "_pitch_location", return_value=({"needs_location": True}, "")), \
             patch.object(pt, "draft_pitch") as draft:
            out = pt.draft_pitch_batch(state, {"brand_ids": [1, 2, 3]})
        self.assertEqual(out["status"], "needs_location_first")
        draft.assert_not_called()
        routes._invoke_generate_pr_packages.assert_not_called()
        self.assertEqual(state.notes["pending_batch"]["brand_ids"], [1, 2, 3])
        self.assertIn("**Paris, France**", state.final_say)
        self.assertTrue(pt.batch_waits_on_location(state.notes))

    def test_city_reply_writes_the_waiting_batch(self):
        state = _state(notes={"pending_batch": {"brand_ids": [1, 2]}})

        def fake_batch(st, args):
            self.assertEqual(args["brand_ids"], [1, 2])
            st.final_say = "Done. I wrote 2 pitches."
            return {"ok": True, "status": "drafted"}

        with patch.object(pt, "_routes", return_value=MagicMock()), \
             patch.object(pt, "draft_pitch_batch", side_effect=fake_batch):
            out = pt.save_profile_facts(state, {"city": "Paris", "country": "France"})
        self.assertTrue(out["draft_city_filled"])
        self.assertNotIn("pending_batch", state.notes)
        self.assertEqual(state.final_say, "Got it, Paris, France. I wrote 2 pitches.")

    def test_city_reply_fixes_every_written_pitch(self):
        items = [
            {"brand_id": 1, "brand_name": "A", "email": "pr@a.com", "subject": "S",
             "body": f"Hi A\n\nI'm shipping to {PLACEHOLDER}."},
            {"brand_id": 2, "brand_name": "B", "email": "pr@b.com", "subject": "S",
             "body": f"Hi B\n\nI'm shipping to {PLACEHOLDER}."},
            {"brand_id": 3, "brand_name": "C", "email": "pr@c.com", "subject": "S", "body": "x", "sent_at": "t"},
        ]
        state = _state(notes={"pitch_batch": {"items": items}})
        with patch.object(pt, "_routes", return_value=MagicMock()):
            pt.save_profile_facts(state, {"city": "Paris", "country": "France"})
        stored = state.notes["pitch_batch"]["items"]
        self.assertTrue(all(PLACEHOLDER not in i["body"] for i in stored))
        self.assertIn("Paris, France", stored[0]["body"])
        self.assertEqual([i["brand_id"] for i in state.pitch_batch], [1, 2])
        self.assertFalse(any(i["needs_location"] for i in state.pitch_batch))
        self.assertIsNone(state.pitch_update)
        self.assertIn("**Send all 2 from my Gmail**", state.final_say)
        self.assertFalse(pt.batch_waits_on_location(state.notes))

    def test_typed_city_skips_the_brain_while_a_batch_waits(self):
        state = _state(notes={"pending_batch": {"brand_ids": [1]}}, user_text="Paris, France")
        with patch.object(R, "run_tool", return_value={"ok": True}) as tool:
            R._run_chip(state, None, "", {})
        tool.assert_called_once_with(state, "save_profile_facts", {"city": "Paris", "country": "France"})

    def test_typed_text_left_alone_without_a_waiting_batch(self):
        state = _state(user_text="Paris, France")
        with patch.object(R, "run_tool") as tool:
            R._run_chip(state, None, "", {})
        tool.assert_not_called()


class FakeConn:
    def cursor(self, cursor_factory=None):
        return MagicMock()

    def commit(self):
        pass

    def rollback(self):
        pass

    def close(self):
        pass


class BatchSendRouteTests(unittest.TestCase):
    def _items(self):
        return [
            {"brand_id": 1, "brand_name": "Glow", "email": "pr@glow.com", "subject": "S", "body": "Hi Glow"},
            {"brand_id": 2, "brand_name": "Sol", "email": None, "subject": "S", "body": "Hi Sol"},
            {"brand_id": 3, "brand_name": "Byoma", "email": "pr@byoma.com", "subject": "S",
             "body": f"Ship to {PLACEHOLDER}"},
        ]

    def _post(self, account={"email": "maya@gmail.com"}, notes=None):
        notes = notes if notes is not None else {"pitch_batch": {"items": self._items()}}
        send = MagicMock(return_value={"gmail_id": "g1"})
        saved = MagicMock()
        with patch.object(R, "_creator_auth", return_value=(7, FakeConn(), {"first_name": "Maya", "last_name": "Chen"})), \
             patch("services.polly_gmail.gmail_configured", return_value=True), \
             patch("services.polly_gmail.load_account", return_value=account), \
             patch("services.polly_gmail.send_email", send), \
             patch.object(R, "load_thread", return_value={"notes": notes}), \
             patch("services.polly_memory.save_notes", saved), \
             patch("services.polly_tracker.record_pitch_sent") as tracked, \
             patch.object(R, "_kit_rates", return_value=(1500, False)), \
             patch("services.polly_usage.log_usage"):
            resp = app.test_client().post("/api/polly/pitch-batch/send", json={})
        return resp, send, saved, tracked

    def test_sends_only_ready_pitches_to_stored_inboxes(self):
        resp, send, saved, tracked = self._post()
        self.assertEqual(resp.status_code, 200, resp.get_json())
        data = resp.get_json()
        send.assert_called_once()
        args, kwargs = send.call_args
        self.assertEqual(args[2], "pr@glow.com")
        self.assertEqual(kwargs["from_name"], "Maya Chen")
        self.assertEqual([r["status"] for r in data["results"]], ["sent", "no_inbox", "needs_location"])
        tracked.assert_called_once()
        notes = saved.call_args[0][2]
        self.assertIn(1, notes["pitched_brand_ids"])
        self.assertTrue(notes["pitch_batch"]["items"][0]["sent_at"])
        self.assertIn("maya@gmail.com", data["message"])
        self.assertIn("$50–$100 a video", data["message"])
        self.assertEqual(data["task_chips"][0]["action"], "save_rates")

    def test_resend_skips_what_already_went(self):
        items = self._items()
        items[0]["sent_at"] = "2026-10-20T10:00:00+00:00"
        resp, send, _, _ = self._post(notes={"pitch_batch": {"items": items}})
        send.assert_not_called()
        self.assertEqual(resp.get_json()["results"][0]["status"], "already_sent")

    def test_needs_gmail(self):
        resp, send, _, _ = self._post(account=None)
        self.assertEqual(resp.status_code, 409)
        self.assertEqual(resp.get_json()["error"], "gmail_not_connected")
        send.assert_not_called()


class SaveRatesTests(unittest.TestCase):
    def _run(self, has_rates, followers=1500):
        state = _state(scrape={"follower_count": followers})
        cursor = MagicMock()
        conn = MagicMock()
        conn.cursor.return_value = cursor
        state.conn = conn
        conn.closed = 0
        with patch.object(R, "_kit_rates", return_value=(followers, has_rates)):
            say = R._apply_suggested_rates(state)
        return say, cursor, state

    def test_fills_empty_kit_rates(self):
        say, cursor, state = self._run(False)
        sql, params = cursor.execute.call_args[0]
        self.assertIn("UPDATE creators SET rates_tiktok", sql)
        self.assertEqual(params[:3], (75, 75, 45))
        self.assertIn("$75", say)
        self.assertIn("rates_set_at", state.notes)

    def test_never_overwrites_creator_rates(self):
        say, cursor, _ = self._run(True)
        cursor.execute.assert_not_called()
        self.assertIn("already has rates", say)

    def test_no_follower_count_no_guess(self):
        say, cursor, _ = self._run(False, followers=0)
        cursor.execute.assert_not_called()
        self.assertIn("won't guess", say)


if __name__ == "__main__":
    unittest.main()
