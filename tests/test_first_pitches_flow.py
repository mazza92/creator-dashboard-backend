"""First session: one tap writes the 3 free pitches, Gmail sends them, Polly follows up, Gmail access is cleaned up."""
import unittest
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import routes.polly as R
import services.polly_autopilot as pa
import services.polly_gmail as pg
from app import app
from services.polly_turn_state import TurnState
from tests.test_first_session_wow import FakeConn


def _state(**over):
    base = dict(
        creator_id=7, creator={}, first="Maya", scrape={}, kit={}, profile_context="", coach_moves=[],
        balance={"remaining": 3}, notes={}, messages=[], stored_messages=[], suggested=[], tracker_ctx={},
        early_fu={}, career=None, user_text="", chip={},
    )
    base.update(over)
    return TurnState(**base)


BRANDS = [{"id": 1, "name": "Glow"}, {"id": 2, "name": "Sol"}, {"id": 3, "name": "Byoma"}]
ITEMS = [{"brand_id": i, "brand_name": n, "email": f"pr@{n.lower()}.com", "needs_location": False}
         for i, n in ((1, "Glow"), (2, "Sol"), (3, "Byoma"))]


class WelcomeWritesTheBatchTests(unittest.TestCase):
    def _run(self, data, drafted_status="drafted"):
        state = _state()
        calls = []

        def tool(st, name, args):
            calls.append((name, args))
            if name == "suggest_brands":
                st.brands = list(BRANDS)
                return {"ok": True}
            if name == "draft_pitch_batch":
                if drafted_status == "drafted":
                    st.pitch_batch = list(ITEMS)
                    st.brands = []
                return {"ok": True, "status": drafted_status}
            return {}

        with patch.object(R, "run_tool", side_effect=tool), \
                patch.object(R, "resolve_pitch_location", return_value={}), \
                patch.object(R, "persona_first_matches", return_value="Here are your matches"):
            R._run_chip(state, "suggest_brands", "first_matches", data)
        return state, calls

    def test_welcome_tap_writes_the_three_free_pitches(self):
        state, calls = self._run({"then_batch": True})
        self.assertEqual(calls[1], ("draft_pitch_batch", {"brand_ids": [1, 2, 3]}))
        self.assertIn("3 free pitches", state.final_say)
        self.assertIn("day 4 and day 10", state.final_say)
        self.assertIn("**Glow**", state.final_say)

    def test_without_the_flag_it_still_offers_the_chip(self):
        state, calls = self._run({})
        self.assertEqual([c[0] for c in calls], ["suggest_brands"])
        self.assertEqual(state.task_chips[0]["action"], "pitch_batch")

    def test_failed_draft_falls_back_to_the_brand_list(self):
        state, _ = self._run({"then_batch": True}, drafted_status="failed")
        self.assertEqual([b["id"] for b in state.brands], [1, 2, 3])
        self.assertEqual(state.final_say, "Here are your matches")

    def test_pro_copy_does_not_mention_free_pitches(self):
        say = R.first_batch_say("Maya", ITEMS, {"is_unlimited": True})
        self.assertNotIn("free", say)
        self.assertIn("one tap", say)

    def test_location_first(self):
        say = R.first_batch_say("Maya", [{**ITEMS[0], "needs_location": True}], {"remaining": 3})
        self.assertIn("city and country", say)


class BatchSendFollowUpTests(unittest.TestCase):
    def _post(self, pro=False):
        notes = {"pitch_batch": {"items": [
            {"brand_id": 1, "brand_name": "Glow", "email": "pr@glow.com", "subject": "S", "body": "Hi Glow"}]}}
        send = MagicMock(return_value={"gmail_id": "g1", "thread_id": "t1", "message_id": "<m1>"})
        with patch.object(R, "_creator_auth", return_value=(7, FakeConn(), {"first_name": "Maya"})), \
                patch("services.polly_gmail.gmail_configured", return_value=True), \
                patch("services.polly_gmail.load_account", return_value={"email": "maya@gmail.com"}), \
                patch("services.polly_gmail.send_email", send), \
                patch.object(R, "load_thread", return_value={"notes": notes}), \
                patch("services.polly_memory.save_notes"), \
                patch("services.polly_tracker.record_pitch_sent"), \
                patch.object(R, "_kit_rates", return_value=(1500, True)), \
                patch.object(R, "_creator_is_pro", return_value=pro), \
                patch("services.polly_autopilot.track_gmail_send") as track, \
                patch("services.polly_usage.log_usage"):
            resp = app.test_client().post("/api/polly/pitch-batch/send", json={})
        return resp.get_json(), track

    def test_sent_pitches_get_tracked_for_follow_ups(self):
        data, track = self._post()
        track.assert_called_once()
        args = track.call_args[0]
        self.assertEqual(args[1:5], (7, 1, "Glow", "pr@glow.com"))
        self.assertEqual(args[7]["thread_id"], "t1")
        self.assertIn("day 4 and day 10", data["message"])
        self.assertIn("Gmail inbox", data["message"])

    def test_free_creator_gets_the_keep_going_offer(self):
        data, _ = self._post(pro=False)
        self.assertEqual(data["task_chips"][-1]["action"], "unlock_pro")

    def test_pro_creator_does_not(self):
        data, _ = self._post(pro=True)
        self.assertFalse(any(c["action"] == "unlock_pro" for c in data["task_chips"]))


class FollowUpScopeTests(unittest.TestCase):
    def test_follow_ups_include_free_first_pitches(self):
        self.assertIn("first_pitches", pa._FOLLOWUP_JOIN)
        self.assertIn(pa.PRO_SQL, pa._FOLLOWUP_JOIN)
        import inspect
        src = inspect.getsource(pa.run_autopilot)
        self.assertEqual(src.count("{_FOLLOWUP_JOIN}"), 2)

    def test_track_gmail_send_schedules_day_four(self):
        cur = MagicMock()
        conn = MagicMock()
        conn.cursor.return_value = cur
        now = datetime(2026, 11, 5, 9, tzinfo=timezone.utc)
        with patch.object(pa, "ensure_autopilot_tables"), patch.object(pa, "_hold_day4_nudge") as hold:
            pa.track_gmail_send(conn, 7, 1, "Glow", "pr@glow.com", "S", "B",
                                {"gmail_id": "g", "thread_id": "t", "message_id": "<m>"}, now=now)
        params = cur.execute.call_args_list[0][0][1]
        self.assertEqual(params[11], now + pa.FOLLOWUP_GAPS[0])
        day10 = cur.execute.call_args_list[-1][0]
        self.assertIn("'10'", day10[0])
        self.assertEqual(day10[1][0], now + pa.timedelta(days=11))
        self.assertIn('"first_pitches"', params[12])
        hold.assert_called_once()


class FreeGmailCleanupTests(unittest.TestCase):
    def test_revokes_idle_free_accounts_only(self):
        cur = MagicMock()
        cur.fetchall.return_value = [{"creator_id": 11}, {"creator_id": 12}]
        conn = MagicMock()
        with patch.object(pg, "ensure_gmail_table"), patch.object(pg, "_cursor", return_value=cur), \
                patch.object(pg, "disconnect") as disc:
            n = pg.revoke_idle_free_accounts(conn)
        self.assertEqual(n, 2)
        self.assertEqual([c[0][1] for c in disc.call_args_list], [11, 12])
        sql = cur.execute.call_args[0][0]
        self.assertIn("'pro'", sql)
        self.assertIn("followup_status IN ('scheduled', 'headsup')", sql)
        self.assertEqual(cur.execute.call_args[0][1][0], 30)


class CountryOnlyFirstBatchTests(unittest.TestCase):
    def test_signup_country_is_enough_for_the_first_pitches(self):
        import services.polly_tools as pt
        state = _state()
        seen = []

        def fake_draft(st, args):
            seen.append(pt._pitch_location(st))
            st.pitch = {"brand_id": args["brand_id"], "brand_name": "B", "email": "pr@b.com", "subject": "S",
                        "body": "Hi, shipping to United States"}

        routes = MagicMock()
        routes._invoke_generate_pr_packages.return_value = {}
        routes._unlock_balance.return_value = {"remaining": 1}
        us = {"city": "", "country": "United States", "display": "United States", "needs_location": False}
        with patch.object(pt, "_routes", return_value=routes), \
                patch.object(pt, "find_brand", side_effect=lambda st, brand_id=None, brand_name=None: {"id": brand_id, "name": "B"}), \
                patch.object(pt, "resolve_pitch_location", return_value={"needs_location": True}), \
                patch.object(pt, "_country_only_location", return_value=us), \
                patch.object(pt, "draft_pitch", side_effect=fake_draft):
            out = pt.draft_pitch_batch(state, {"brand_ids": [1, 2]})
        self.assertEqual(out["status"], "drafted")
        self.assertEqual(routes._invoke_generate_pr_packages.call_args[1]["country"], "United States")
        self.assertEqual(seen[0][1], "United States")
        self.assertNotIn("pending_batch", state.notes)

    def test_first_open_never_spends_credits(self):
        import inspect
        src = inspect.getsource(R)
        i = src.index('"starter": "first_matches",\n                    "chip_id": "first_matches"')
        self.assertNotIn('"then_batch": True', src[i:i + 500])
