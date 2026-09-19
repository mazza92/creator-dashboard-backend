"""Polly task tracker: lifecycle intents + session context (no DB)."""

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from services.polly_tracker import (
    classify_lifecycle_heuristic,
    session_context_text,
)


class LifecycleIntentTests(unittest.TestCase):
    def test_sent_it_is_pitch_sent(self):
        hit = classify_lifecycle_heuristic("sent it", last_pitch={"id": 9, "name": "Rhode"})
        self.assertEqual(hit["intent"], "pitch_sent")
        self.assertEqual(hit["brand_id"], 9)
        self.assertGreaterEqual(hit["confidence"], 0.7)

    def test_more_brands_is_not_pitch_sent(self):
        hit = classify_lifecycle_heuristic(
            "More brands",
            last_pitch={"id": 9, "name": "Nuria Beauty"},
        )
        self.assertEqual(hit["intent"], "ask_for_brands")
        self.assertNotEqual(hit["intent"], "pitch_sent")

    def test_never_sent_is_not_pitch_sent(self):
        hit = classify_lifecycle_heuristic(
            "I never sent it",
            last_pitch={"id": 9, "name": "Nuria Beauty"},
        )
        self.assertEqual(hit["intent"], "pitch_not_sent")
        self.assertEqual(
            classify_lifecycle_heuristic(
                "Not sent",
                last_pitch={"id": 9, "name": "Nuria Beauty"},
            )["intent"],
            "pitch_not_sent",
        )

    def test_bounced_email(self):
        hit = classify_lifecycle_heuristic(
            "email bounced",
            last_pitch={"id": 3, "name": "Sentix Cosmetics (US)"},
        )
        self.assertEqual(hit["intent"], "email_bounced")

    def test_still_quiet(self):
        hit = classify_lifecycle_heuristic(
            "still quiet",
            last_pitch={"id": 3, "name": "Sentix Cosmetics (US)"},
        )
        self.assertEqual(hit["intent"], "still_quiet")

    def test_they_said_yes(self):
        hit = classify_lifecycle_heuristic(
            "The Peace Labs said yes, they're sending PR",
            brands=[{"id": 3, "name": "The Peace Labs"}],
        )
        self.assertEqual(hit["intent"], "brand_replied_interested")
        self.assertEqual(hit["brand"], "The Peace Labs")

    def test_rejected(self):
        hit = classify_lifecycle_heuristic("they passed, not interested")
        self.assertEqual(hit["intent"], "brand_replied_rejected")

    def test_box_arrived(self):
        hit = classify_lifecycle_heuristic("the box is here")
        self.assertEqual(hit["intent"], "pr_received")

    def test_posted(self):
        hit = classify_lifecycle_heuristic("posted it, video is live")
        self.assertEqual(hit["intent"], "content_posted")

    def test_casual_is_not_a_task(self):
        hit = classify_lifecycle_heuristic("hey")
        self.assertEqual(hit["intent"], "casual_chat")

    def test_brand_from_notes_uses_last_pitched(self):
        from services.polly_tracker import brand_from_notes
        brand = brand_from_notes({
            "pitched_brand_ids": [11, 22],
            "pitched_brand_names": ["Acure", "Habelo Beauty, LLC"],
        })
        self.assertEqual(brand["id"], 22)
        self.assertEqual(brand["name"], "Habelo Beauty, LLC")
        text = session_context_text({
            "due_soon": [{"type": "follow_up_due", "brand_name": "Rhode"}],
            "active_tasks": [{"type": "content_due", "brand_name": "Acure", "status": "pending"}],
            "recent_timeline": [{"event_label": "Pitch sent · Rhode"}],
        })
        self.assertIn("Rhode", text)
        self.assertIn("Acure", text)
        self.assertIn("TASK TRACKER", text)

    def test_kit_view_leads_session_context(self):
        text = session_context_text({
            "recent_timeline": [{
                "event_type": "portfolio_viewed",
                "event_label": "Viewed your portfolio · IONIQ Skincare US",
                "brand_name": "IONIQ Skincare US",
            }],
        })
        self.assertIn("KIT VIEWS", text)
        self.assertIn("IONIQ Skincare US", text)
        self.assertIn("opened the kit", text)

    def test_morning_brief_leads_with_kit_view(self):
        from services.polly_tracker import morning_brief
        brief = morning_brief({
            "recent_timeline": [{
                "event_type": "portfolio_viewed",
                "brand_name": "Habelo Beauty, LLC",
                "brand_id": 44,
            }],
            "active_tasks": [],
            "due_soon": [],
        }, first_name="Sam")
        self.assertIsNotNone(brief)
        self.assertIn("Habelo Beauty", brief["summary"])
        self.assertIn("viewed your portfolio", brief["priority"])
        self.assertEqual(brief["chips"][0]["action"], "generate_pitch")

    def test_morning_brief_checkin_when_follow_up_due(self):
        from services.polly_tracker import morning_brief
        brief = morning_brief({
            "recent_timeline": [],
            "active_tasks": [{
                "id": 12,
                "type": "follow_up_due",
                "brand_name": "Sentix Cosmetics (US)",
                "brand_id": 3,
                "due_at": "2020-01-01T00:00:00+00:00",
            }],
            "due_soon": [{
                "id": 12,
                "type": "follow_up_due",
                "brand_name": "Sentix Cosmetics (US)",
                "brand_id": 3,
                "due_at": "2020-01-01T00:00:00+00:00",
            }],
        }, first_name="Megan")
        self.assertIsNotNone(brief)
        self.assertIn("Sentix", brief["summary"])
        ids = [c["id"] for c in brief["chips"]]
        self.assertIn("checkin_bounced", ids)
        self.assertIn("checkin_not_sent", ids)

    def test_morning_brief_asks_24h_after_pitch(self):
        from datetime import datetime, timedelta, timezone
        from services.polly_tracker import morning_brief
        now = datetime.now(timezone.utc)
        brief = morning_brief({
            "recent_timeline": [],
            "checkin_due": {
                "brand_id": 9,
                "brand_name": "CertaPet",
                "task_id": 51,
                "early": True,
            },
            "active_tasks": [{
                "id": 51,
                "type": "follow_up_due",
                "brand_name": "CertaPet",
                "brand_id": 9,
                "created_at": (now - timedelta(hours=25)).isoformat(),
                "due_at": (now + timedelta(days=3)).isoformat(),
            }],
            "due_soon": [],
        }, first_name="Mahery")
        self.assertIsNotNone(brief)
        self.assertIn("Got any reply from **CertaPet**", brief["summary"])
        self.assertEqual(brief["chips"][0]["id"], "checkin_quiet")
        self.assertEqual(brief["chips"][0]["label"], "Still quiet")

    def test_portfolio_view_alert_copy(self):
        from services.polly_tracker import portfolio_view_alert
        alert = portfolio_view_alert("Rhode", 9)
        self.assertIn("Rhode", alert["message"])
        self.assertIn("opened your kit", alert["message"])
        self.assertEqual(alert["chips"][0]["brand_id"], 9)

    def test_record_portfolio_viewed_writes_event(self):
        from unittest.mock import MagicMock, patch
        from services.polly_tracker import record_portfolio_viewed

        cur = MagicMock()
        cur.fetchone.return_value = None
        with patch("services.polly_tracker._cursor", return_value=cur), \
             patch("services.polly_tracker.lookup_brand", return_value={"id": 20, "name": "Glow Co"}), \
             patch("services.polly_tracker._open_tasks", return_value=[{"id": 8}]), \
             patch("services.polly_tracker.add_event", return_value=1) as add_event, \
             patch("services.polly_tracker._inject_portfolio_view_alert", return_value=True):
            out = record_portfolio_viewed(MagicMock(), 10, 20, brand_name="Glow Co")
        self.assertTrue(out["recorded"])
        self.assertTrue(out["alerted"])
        self.assertEqual(add_event.call_args[0][2], "portfolio_viewed")
        self.assertIn("Glow Co", add_event.call_args[0][3])

    def test_duplicate_kit_view_is_skipped(self):
        from unittest.mock import MagicMock, patch
        from services.polly_tracker import record_portfolio_viewed

        cur = MagicMock()
        cur.fetchone.return_value = {"id": 99}
        with patch("services.polly_tracker._cursor", return_value=cur), \
             patch("services.polly_tracker.lookup_brand", return_value={"id": 20, "name": "Glow Co"}), \
             patch("services.polly_tracker.add_event") as add_event:
            out = record_portfolio_viewed(MagicMock(), 10, 20, brand_name="Glow Co")
        self.assertFalse(out["recorded"])
        add_event.assert_not_called()


if __name__ == "__main__":
    unittest.main()
