"""The 'While you were away' activity card, live events, send receipts and tracked kit links."""
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import routes.polly as R
import services.polly_activity as act
from app import app
from services.kit_view_tracking import generate_kit_token, track_kit_links
from services.polly_manager import away_activity_message
from services.polly_memory import _clean_message
from tests.test_first_session_wow import FakeConn

NOW = datetime(2026, 11, 10, 12, tzinfo=timezone.utc)
SINCE = NOW - timedelta(days=2)


def fake_rows(conn, sql, params):
    if "polly_gmail_accounts" in sql:
        return [{"email": "maya@gmail.com"}]
    if "brand_replied" in str(params) and "DISTINCT ON" in sql:
        return [{"brand_id": 5, "event_type": "brand_replied_interested", "occurred_at": NOW - timedelta(hours=3),
                 "brand_name": "Fernhouse"}]
    if "portfolio_viewed" in sql and "GROUP BY" in sql:
        return [{"brand_id": 1, "at": NOW - timedelta(hours=5), "n": 2, "brand_name": "Saltwell",
                 "pitched_at": NOW - timedelta(hours=9)}]
    if "followup_status IN ('scheduled', 'headsup')" in sql and "SELECT brand_id FROM" in sql:
        return [{"brand_id": 3}]
    if "FROM polly_autopilot_queue" in sql:
        return [
            {"brand_id": 1, "brand_name": "Saltwell", "sent_at": NOW - timedelta(hours=9), "thread_id": "t1",
             "followup_status": "scheduled", "followup_due_at": NOW + timedelta(days=3), "followup_sent_at": None,
             "followups_sent": 0},
            {"brand_id": 2, "brand_name": "Lumi", "sent_at": NOW - timedelta(days=5), "thread_id": "t2",
             "followup_status": "scheduled", "followup_due_at": NOW + timedelta(hours=20),
             "followup_sent_at": NOW - timedelta(hours=30), "followups_sent": 1},
        ]
    return []


class CollectTests(unittest.TestCase):
    def _collect(self, **kw):
        with patch.object(act, "_rows", side_effect=fake_rows):
            return act.collect(MagicMock(), 7, since=SINCE, now=NOW, **kw)

    def test_proof_first_then_what_is_next(self):
        a = self._collect(tracker={"due_soon": [
            {"type": "follow_up_due", "brand_id": 3, "brand_name": "Handled"},
            {"type": "follow_up_due", "brand_id": 9, "brand_name": "Acure", "id": 44}]},
            drafts=[{"brand_id": 8, "brand_name": "e.l.f."}])
        types = [i["type"] for i in a["items"]]
        self.assertEqual(types, ["reply", "kit_view", "followup_sent", "sent", "next", "todo_followup"])
        kit = a["items"][1]
        self.assertIn("2 times", kit["text"])
        self.assertIn("4 hours after my pitch", kit["text"])
        self.assertIn("authuser=maya%40gmail.com#all/t2", a["items"][2]["gmail_url"])
        self.assertEqual(a["items"][3]["brand_name"], "Saltwell")
        self.assertIn("tomorrow", a["items"][4]["text"])
        self.assertEqual(a["items"][5]["brand_name"], "Acure")  # Polly-handled brand never nags
        self.assertEqual(a["counts"]["kit_views"], 1)

    def test_message_has_actions_and_headline(self):
        a = self._collect(tracker={"due_soon": [{"type": "follow_up_due", "brand_id": 9, "brand_name": "Acure"}]})
        msg = away_activity_message(a, {"month": {"pitches": 3, "pitches_goal": 10}})
        self.assertIn("Fernhouse", msg["content"])
        self.assertEqual(msg["activity"]["month"], {"pitches": 3, "goal": 10})
        todo = [i for i in msg["activity"]["items"] if i["type"] == "todo_followup"][0]
        self.assertEqual(todo["chip"]["action"], "generate_pitch")
        self.assertTrue(todo["chip"]["is_followup"])
        self.assertIsNone(away_activity_message({"items": []}))

    def test_memory_keeps_the_card(self):
        a = self._collect()
        msg = away_activity_message(a)
        kept = _clean_message(msg)
        self.assertEqual(len(kept["activity"]["items"]), len(msg["activity"]["items"]))


class LiveAndLinksTests(unittest.TestCase):
    def test_live_event_text(self):
        rows = [{"event_type": "portfolio_viewed", "occurred_at": NOW, "brand_id": 1, "brand_name": "Saltwell"}]
        with patch.object(act, "_rows", return_value=rows):
            ev = act.live_events(MagicMock(), 7, NOW.isoformat())
        self.assertEqual(ev[0]["text"], "Saltwell just opened your media kit")

    def test_untracked_kit_links_get_the_brand_ref(self):
        tok = generate_kit_token(7, 3)
        body = "My kit: https://newcollab.co/kit/maya\nThanks"
        self.assertIn(f"https://newcollab.co/kit/maya?ref={tok}\n", track_kit_links(body, 7, 3))
        tracked = "https://newcollab.co/kit/maya?ref=abc"
        self.assertEqual(track_kit_links(tracked, 7, 3), tracked)

    def test_gmail_url(self):
        self.assertIsNone(act.gmail_thread_url("a@b.c", ""))
        self.assertEqual(act.gmail_thread_url("", "t9"), "https://mail.google.com/mail/#all/t9")


class ReceiptTests(unittest.TestCase):
    def test_sent_results_carry_gmail_link_and_mark_handoff(self):
        notes = {"pitch_batch": {"items": [{"brand_id": 1, "brand_name": "Glow", "email": "pr@glow.com",
                                            "subject": "S", "body": "Kit https://newcollab.co/kit/maya"}]}}
        send = MagicMock(return_value={"gmail_id": "g", "thread_id": "th1", "message_id": "<m>"})
        with patch.object(R, "_creator_auth", return_value=(7, FakeConn(), {"first_name": "Maya"})), \
                patch("services.polly_gmail.gmail_configured", return_value=True), \
                patch("services.polly_gmail.load_account", return_value={"email": "maya@gmail.com"}), \
                patch("services.polly_gmail.send_email", send), \
                patch.object(R, "load_thread", return_value={"notes": notes}), \
                patch("services.polly_memory.save_notes"), \
                patch("services.polly_tracker.record_pitch_sent"), \
                patch.object(R, "_kit_rates", return_value=(1500, True)), \
                patch.object(R, "_creator_is_pro", return_value=True), \
                patch("services.polly_autopilot.track_gmail_send"), \
                patch("services.polly_alerts.record_handoff") as handoff, \
                patch("services.polly_usage.log_usage"):
            data = app.test_client().post("/api/polly/pitch-batch/send", json={}).get_json()
        r = data["results"][0]
        self.assertIn("#all/th1", r["gmail_url"])
        self.assertEqual(r["to"], "pr@glow.com")
        self.assertTrue(r["sent_at"])
        handoff.assert_called_once()
        self.assertIn("?ref=", send.call_args[0][4])
