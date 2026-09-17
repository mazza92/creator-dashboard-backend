"""Apply transparency stats + Timeline application merge (no DB)."""

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from services.apply_transparency import resolve_apply_transparency
from services.polly_tracker import (
    application_relationship,
    application_timeline_events,
    merge_application_relationships,
)


class ResolveTransparencyTests(unittest.TestCase):
    def test_uses_stored_brand_rate(self):
        out = resolve_apply_transparency(
            {"applicants": 12, "decided": 2, "selected": 1, "in_review": 10},
            {"response_rate": 41.2, "avg_response_time_days": 6},
        )
        self.assertEqual(out["applicants"], 12)
        self.assertEqual(out["response_rate"], 41.2)
        self.assertEqual(out["response_rate_source"], "brand")
        self.assertEqual(out["avg_response_days"], 6)

    def test_computes_campaign_rate_with_enough_sample(self):
        out = resolve_apply_transparency(
            {"applicants": 20, "decided": 10, "selected": 4, "in_review": 10},
            {"response_rate": None},
        )
        self.assertEqual(out["response_rate"], 50.0)
        self.assertEqual(out["response_rate_source"], "campaign")

    def test_does_not_invent_rate_on_thin_history(self):
        out = resolve_apply_transparency(
            {"applicants": 3, "decided": 1, "selected": 0, "in_review": 2},
            {},
        )
        self.assertEqual(out["applicants"], 3)
        self.assertIsNone(out["response_rate"])
        self.assertIsNone(out["response_rate_source"])

    def test_zero_stored_rate_is_missing(self):
        out = resolve_apply_transparency(
            {"applicants": 8},
            {"response_rate": 0},
        )
        self.assertIsNone(out["response_rate"])


class ApplicationTimelineTests(unittest.TestCase):
    def test_relationship_from_review_application(self):
        rel = application_relationship({
            "id": 4,
            "brand_id": 22,
            "brand_name": "Nuria Beauty",
            "logo_url": "https://cdn/nuria.png",
            "status": "review",
            "applied_at": "2026-09-17T12:00:00+00:00",
        })
        self.assertEqual(rel["source"], "apply")
        self.assertEqual(rel["status"], "active")
        self.assertEqual(rel["stage_label"], "Applied · in review")
        self.assertIn("Nuria Beauty", rel["last_event"])

    def test_merge_adds_application_only_brands(self):
        rows = [{
            "brand_id": 1,
            "name": "Rhode",
            "status": "pitched",
            "stage_label": "Pitched",
            "last_event": "Pitch sent · Rhode",
            "last_event_at": "2026-09-01T00:00:00+00:00",
            "source": "pitch",
        }]
        merged = merge_application_relationships(rows, [{
            "id": 9,
            "brand_id": 22,
            "brand_name": "Nuria Beauty",
            "status": "review",
            "applied_at": "2026-09-17T12:00:00+00:00",
        }])
        ids = {r["brand_id"] for r in merged}
        self.assertEqual(ids, {1, 22})

    def test_merge_promotes_selected_application(self):
        rows = [{
            "brand_id": 22,
            "name": "Nuria Beauty",
            "status": "active",
            "stage_label": "Applied · in review",
            "source": "apply",
            "last_event_at": "2026-09-17T12:00:00+00:00",
        }]
        merged = merge_application_relationships(rows, [{
            "id": 9,
            "brand_id": 22,
            "brand_name": "Nuria Beauty",
            "status": "ships",
            "applied_at": "2026-09-17T12:00:00+00:00",
            "updated_at": "2026-09-18T12:00:00+00:00",
        }])
        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0]["status"], "won")
        self.assertEqual(merged[0]["stage_label"], "Selected · shipping")

    def test_synthetic_events_skip_existing_apply(self):
        extra = application_timeline_events(
            {
                "id": 9,
                "brand_name": "Nuria Beauty",
                "status": "review",
                "applied_at": "2026-09-17T12:00:00+00:00",
            },
            {"campaign_applied"},
        )
        self.assertEqual(extra, [])

    def test_declined_adds_passed_event(self):
        extra = application_timeline_events({
            "id": 9,
            "brand_name": "Nuria Beauty",
            "status": "declined",
            "applied_at": "2026-09-17T12:00:00+00:00",
            "updated_at": "2026-09-19T12:00:00+00:00",
        })
        types = {e["event_type"] for e in extra}
        self.assertIn("campaign_applied", types)
        self.assertIn("brand_replied_rejected", types)


if __name__ == "__main__":
    unittest.main()
