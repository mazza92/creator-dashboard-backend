"""Pain diagnosis + check-in chips (no DB)."""

import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from services.polly_pain import (
    FIX_FOLLOW_UP,
    FIX_NEW_IN_NICHE,
    FIX_SEND_ONE,
    FIX_VERIFY_EMAIL,
    PAIN_BOUNCED,
    PAIN_NOT_SENT,
    PAIN_QUIET_AFTER_VIEW,
    PAIN_REJECTED,
    checkin_due,
    diagnose_pain,
    pain_context,
    stamp_pain,
)


def _iso(days_ago=4):
    return (datetime.now(timezone.utc) - timedelta(days=days_ago)).isoformat()


class PainDiagnosisTests(unittest.TestCase):
    def test_bounce_beats_generic_silence(self):
        pain = diagnose_pain(
            {},
            {
                "recent_timeline": [{
                    "event_type": "email_bounced",
                    "event_label": "Email bounced · Sentix",
                    "brand_name": "Sentix Cosmetics (US)",
                    "brand_id": 3,
                }],
                "active_tasks": [{"type": "follow_up_due", "brand_name": "Sentix Cosmetics (US)"}],
            },
        )
        self.assertEqual(pain["code"], PAIN_BOUNCED)
        self.assertEqual(pain["fix"], FIX_VERIFY_EMAIL)

    def test_kit_view_then_quiet(self):
        pain = diagnose_pain(
            {},
            {
                "recent_timeline": [{
                    "event_type": "portfolio_viewed",
                    "brand_name": "Nuria Beauty",
                    "brand_id": 8,
                }],
                "active_tasks": [{
                    "type": "follow_up_due",
                    "brand_id": 8,
                    "brand_name": "Nuria Beauty",
                }],
            },
        )
        self.assertEqual(pain["code"], PAIN_QUIET_AFTER_VIEW)
        self.assertEqual(pain["fix"], FIX_FOLLOW_UP)

    def test_rejection_points_to_better_matches(self):
        pain = diagnose_pain(
            {},
            {
                "recent_timeline": [{
                    "event_type": "brand_replied_rejected",
                    "event_label": "Reply: passed · Anua",
                    "brand_name": "Anua",
                }],
                "active_tasks": [],
            },
        )
        self.assertEqual(pain["code"], PAIN_REJECTED)
        self.assertEqual(pain["fix"], FIX_NEW_IN_NICHE)

    def test_unsent_draft_is_activation(self):
        pain = diagnose_pain(
            {"pending_pitch": {"id": 1, "name": "Nuria Beauty"}},
            {"active_tasks": [], "recent_timeline": []},
        )
        self.assertEqual(pain["code"], PAIN_NOT_SENT)
        self.assertEqual(pain["fix"], FIX_SEND_ONE)

    def test_kit_incomplete_only_when_no_outreach(self):
        pain = diagnose_pain(
            {},
            {"active_tasks": [], "recent_timeline": []},
            kit={"published": False},
        )
        self.assertEqual(pain["code"], "kit_incomplete")
        self.assertEqual(pain["fix"], "publish_kit")

    def test_pain_context_tells_polly_not_to_spray(self):
        notes = stamp_pain({}, diagnose_pain(
            {"pending_pitch": {"id": 1, "name": "Nuria Beauty"}},
            {},
        ))
        text = pain_context(notes)
        self.assertIn("ACTIVE PAIN", text)
        self.assertIn("draft", text.lower())

    def test_checkin_due_on_stale_follow_up(self):
        pulse = checkin_due({
            "active_tasks": [{
                "id": 44,
                "type": "follow_up_due",
                "brand_name": "Sentix Cosmetics (US)",
                "brand_id": 3,
                "due_at": _iso(1),
            }],
        })
        self.assertIsNotNone(pulse)
        self.assertEqual(pulse["brand_name"], "Sentix Cosmetics (US)")
        self.assertEqual(pulse["task_id"], 44)
        self.assertFalse(pulse["early"])

    def test_checkin_due_24h_before_follow_up_deadline(self):
        now = datetime.now(timezone.utc)
        pulse = checkin_due({
            "active_tasks": [{
                "id": 51,
                "type": "follow_up_due",
                "brand_name": "CertaPet",
                "brand_id": 9,
                "created_at": (now - timedelta(hours=25)).isoformat(),
                "due_at": (now + timedelta(days=3)).isoformat(),
                "metadata": {"wave": 4, "brand_name": "CertaPet"},
            }],
        })
        self.assertIsNotNone(pulse)
        self.assertEqual(pulse["brand_name"], "CertaPet")
        self.assertTrue(pulse["early"])

    def test_checkin_due_not_before_24h(self):
        now = datetime.now(timezone.utc)
        pulse = checkin_due({
            "active_tasks": [{
                "id": 52,
                "type": "follow_up_due",
                "brand_name": "CertaPet",
                "brand_id": 9,
                "created_at": (now - timedelta(hours=6)).isoformat(),
                "due_at": (now + timedelta(days=4)).isoformat(),
                "metadata": {"wave": 4},
            }],
        })
        self.assertIsNone(pulse)

    def test_checkin_skips_day10_wave(self):
        now = datetime.now(timezone.utc)
        pulse = checkin_due({
            "active_tasks": [{
                "id": 53,
                "type": "follow_up_due",
                "brand_name": "CertaPet",
                "brand_id": 9,
                "created_at": (now - timedelta(hours=30)).isoformat(),
                "due_at": (now + timedelta(days=9)).isoformat(),
                "metadata": {"wave": 10},
            }],
        })
        self.assertIsNone(pulse)

    def test_checkin_ask_copy(self):
        from services.polly_pain import checkin_ask
        self.assertEqual(
            checkin_ask("CertaPet"),
            "Got any reply from **CertaPet** since you contacted them?",
        )


if __name__ == "__main__":
    unittest.main()
