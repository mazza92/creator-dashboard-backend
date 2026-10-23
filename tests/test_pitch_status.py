"""Pitch status tracker: one status per brand, who acts next, brand signals (no DB)."""

import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from services.pitch_status import (  # noqa: E402
    apply_stage,
    pitch_stage,
    relationship_status,
    signal_lines,
    stage_view,
)
from services.polly_fallback import status_reply  # noqa: E402

NOW = datetime(2026, 10, 23, 12, tzinfo=timezone.utc)


def ev(kind, days_ago=0):
    return {"event_type": kind, "occurred_at": NOW - timedelta(days=days_ago)}


class StageTests(unittest.TestCase):
    def test_nothing_logged_is_none(self):
        self.assertIsNone(pitch_stage([ev("portfolio_viewed")], now=NOW))

    def test_sent_then_reply(self):
        self.assertEqual(pitch_stage([ev("pitch_sent", 5), ev("brand_replied_interested", 1)], now=NOW),
                         "replied_interested")

    def test_not_sent_reverts_to_drafted(self):
        self.assertEqual(pitch_stage([ev("pitch_sent", 2), ev("pitch_not_sent", 1)], now=NOW), "drafted")

    def test_later_draft_does_not_undo_reply(self):
        self.assertEqual(pitch_stage([ev("pitch_sent", 9), ev("brand_replied_question", 3),
                                      ev("pitch_drafted", 1)], now=NOW), "replied_question")

    def test_quiet_for_14_days_is_dropped(self):
        self.assertEqual(pitch_stage([ev("pitch_sent", 20)], now=NOW), "dropped")

    def test_open_follow_up_keeps_it_sent(self):
        self.assertEqual(pitch_stage([ev("pitch_sent", 20)], [{"type": "follow_up_due"}], now=NOW), "sent")

    def test_shipping_received_posted(self):
        self.assertEqual(pitch_stage([ev("pitch_sent", 9), ev("pr_shipped", 4)], now=NOW), "shipping")
        self.assertEqual(pitch_stage([ev("pitch_sent", 9), ev("pr_received", 2)], now=NOW), "content_due")
        self.assertEqual(pitch_stage([ev("pitch_sent", 30), ev("content_posted", 1)], now=NOW), "posted")

    def test_apply_stages(self):
        self.assertEqual(apply_stage("review"), "applied")
        self.assertEqual(apply_stage("ships"), "picked")
        self.assertEqual(apply_stage("declined"), "not_picked")


class ViewTests(unittest.TestCase):
    def test_who_acts_next(self):
        self.assertEqual(stage_view("sent", "Rhode")["who_next"], "Brand")
        self.assertEqual(stage_view("replied_interested", "Rhode")["who_next"], "You")

    def test_applied_copy_uses_applicant_pool(self):
        copy = stage_view("applied", "Nuria")["copy"]
        self.assertIn("Nuria's applicant pool", copy)
        self.assertNotIn("gifted list", copy)

    def test_content_due_has_date(self):
        view = stage_view("content_due", "Rhode", NOW + timedelta(days=5))
        self.assertEqual(view["label"], "Content due Oct 28")
        self.assertIn("by Oct 28", view["copy"])

    def test_signals_are_counts(self):
        lines = signal_lines({"pitched_30d": 8, "last_reply_at": NOW - timedelta(days=3),
                              "kit_viewed_at": NOW - timedelta(days=1)}, "sent", now=NOW)
        self.assertEqual(lines, ["Opened your kit yesterday", "Pitched by 8 creators in the last 30 days",
                                 "Brand last replied to a creator 3 days ago"])

    def test_single_pitcher_is_not_a_signal(self):
        self.assertEqual(signal_lines({"pitched_30d": 1}, "sent", now=NOW), [])

    def test_no_reply_line_needs_sample(self):
        self.assertEqual(signal_lines({"pitched_90d": 2}, "sent", now=NOW), [])
        self.assertIn("No replies to creators on Newcollab yet",
                      signal_lines({"pitched_90d": 5}, "sent", now=NOW))

    def test_applicants_only_for_open_roster(self):
        sig = {"roster_open": True, "applicants": 14, "pitched_30d": 3}
        self.assertIn("1 of 14 creators who applied", signal_lines(sig, "applied", now=NOW))
        self.assertIn("Pitched by 3 creators in the last 30 days", signal_lines(sig, "sent", now=NOW))
        self.assertNotIn("1 of 14 creators who applied", signal_lines({"applicants": 14}, "applied", now=NOW))

    def test_relationship_status_carries_counts(self):
        out = relationship_status("applied", "Nuria", {"roster_open": True, "applicants": 4}, now=NOW)
        self.assertEqual(out["applicants"], 4)
        self.assertIn("2 to 3 weeks", out["timing"])


class AttachTests(unittest.TestCase):
    def test_rows_get_tracker_and_dropped_status(self):
        from services import polly_tracker
        rows = [{"brand_id": 1, "name": "Rhode", "status": "pitched", "source": "pitch"},
                {"brand_id": 2, "name": "Nuria", "status": "active", "source": "apply", "apply_status": "review"}]
        events = {1: [{"event_type": "pitch_sent", "occurred_at": datetime.now(timezone.utc) - timedelta(days=30)}]}
        with patch.object(polly_tracker, "_events_by_brand", return_value=events), \
             patch("services.pitch_status.load_brand_signals", return_value={1: {"pitched_30d": 4}}):
            polly_tracker.attach_pitch_status(None, 7, rows, [])
        self.assertEqual(rows[0]["tracker"]["stage"], "dropped")
        self.assertEqual(rows[0]["status"], "dropped")
        self.assertEqual(rows[1]["stage_label"], "Applied")
        self.assertEqual(rows[1]["tracker"]["who_next"], "Brand")


class ReplyTests(unittest.TestCase):
    def test_found(self):
        text = status_reply({"status": "found", "relationship": {
            "brand": "Rhode", "status": "Sent", "who_acts_next": "Brand",
            "what_it_means": "It's in their inbox.", "brand_signals": ["Pitched by 8 creators in the last 30 days"]}})
        self.assertIn("**Rhode**: Sent", text)
        self.assertIn("8 creators", text)

    def test_not_tracked_is_honest(self):
        self.assertIn("Nothing's logged", status_reply({"status": "not_tracked", "brand": "Gucci"}))


if __name__ == "__main__":
    unittest.main()
