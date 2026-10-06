import unittest
from datetime import datetime, timedelta, timezone

from services.polly_alerts import email_month_reports, kit_view_email, month_report_email, unsent_brief_email
from services.polly_persona import persona_followup_too_early
from services.polly_tracker import drop_early_followups, portfolio_view_alert
from services.polly_manager import (
    away_brief_message,
    away_items,
    career_context,
    career_from_counts,
    month_key,
    month_plan_message,
    month_report_lines,
    plan_open_note,
    send_draft_chip,
)


class CareerLadderTests(unittest.TestCase):
    def test_new_creator_starts_at_stage_one(self):
        career = career_from_counts({}, {}, pitch_cap=3)
        self.assertEqual(career["stage"], "starter")
        self.assertEqual(career["stage_index"], 0)
        self.assertEqual(career["month"]["pitches_goal"], 3)
        self.assertEqual(career["next"]["chip"]["action"], "suggest_brands")

    def test_pro_goal_is_uncapped(self):
        self.assertEqual(career_from_counts({}, {}, pitch_cap=None)["month"]["pitches_goal"], 10)

    def test_stages_climb_with_outcomes(self):
        self.assertEqual(career_from_counts({"pitched": 2})["stage"], "pitching")
        self.assertEqual(career_from_counts({"pitched": 4, "replies": 1})["stage"], "first_yes")
        self.assertEqual(career_from_counts({"posted": 1})["stage"], "gifted")
        self.assertEqual(career_from_counts({"posted": 3})["stage"], "paid_ready")
        self.assertEqual(career_from_counts({"posted": 1}, {"polly_track": "established"})["stage"], "paid_ready")
        self.assertEqual(career_from_counts({"posted": 5, "paid": 1})["stage"], "paid")

    def test_paid_ready_asks_for_rates_first(self):
        self.assertEqual(career_from_counts({"posted": 3})["next"]["chip"]["action"], "coach_rates")
        with_rates = career_from_counts({"posted": 3}, {"rate_card": {"reel": 150}})
        self.assertEqual(with_rates["next"]["chip"]["action"], "suggest_gigs")

    def test_gifted_counts_down_to_paid_ready(self):
        self.assertIn("2 more posted collabs", career_from_counts({"posted": 1})["next"]["label"])

    def test_context_names_stage_and_goal(self):
        text = career_context(career_from_counts({"pitched": 2, "pitched_month": 1}, {}, pitch_cap=3))
        self.assertIn("Pitching", text)
        self.assertIn("1/3 pitches sent", text)
        self.assertEqual(career_context(None), "")


class MonthPlanTests(unittest.TestCase):
    def test_plan_note_order(self):
        now = datetime(2026, 10, 6, 12, tzinfo=timezone.utc)
        self.assertIsNone(plan_open_note({}, has_thread=False, now=now))
        self.assertEqual(plan_open_note({}, has_thread=True, now=now), "month")
        fresh = (now - timedelta(hours=2)).isoformat()
        stale = (now - timedelta(hours=30)).isoformat()
        self.assertIsNone(plan_open_note({"month_plan_month": "2026-10", "away_brief_at": fresh}, True, now))
        self.assertEqual(plan_open_note({"month_plan_month": "2026-10", "away_brief_at": stale}, True, now), "away")
        self.assertEqual(month_key(now), "2026-10")

    def test_report_empty_when_nothing_happened(self):
        self.assertEqual(month_report_lines({}, is_pro=False, cap=3, matched_left=5), [])

    def test_report_locks_extra_matches_for_free_at_cap(self):
        lines = month_report_lines({"pitched": 3, "kit_views": 2}, is_pro=False, cap=3, matched_left=4)
        self.assertIn("**3 pitches**", lines[0])
        self.assertTrue(any(line.startswith("🔒") and "4 more brands" in line for line in lines))
        pro = month_report_lines({"pitched": 3, "kit_views": 2}, is_pro=True, matched_left=4)
        self.assertFalse(any("🔒" in line for line in pro))

    def test_plan_message_carries_goal_brands_and_next_chip(self):
        career = career_from_counts({"pitched": 1}, {}, pitch_cap=3)
        msg = month_plan_message("Ana", career, {"pitched": 1}, False, brands=[{"id": i, "name": f"B{i}"} for i in range(7)])
        self.assertEqual(msg["kind"], "brief")
        self.assertIn("New month, Ana", msg["content"])
        self.assertIn("**3 pitches**", msg["content"])
        self.assertEqual(len(msg["brands"]), 5)
        self.assertEqual(msg["task_chips"][0]["action"], "suggest_brands")


class AwayBriefTests(unittest.TestCase):
    def test_nothing_to_report(self):
        self.assertIsNone(away_brief_message(away_items({}, [])))

    def test_views_since_last_brief_followups_and_drafts(self):
        since = datetime(2026, 10, 5, tzinfo=timezone.utc)
        tracker = {
            "recent_timeline": [
                {"event_type": "portfolio_viewed", "brand_id": 1, "brand_name": "Glow", "occurred_at": "2026-10-06T08:00:00+00:00"},
                {"event_type": "portfolio_viewed", "brand_id": 1, "brand_name": "Glow", "occurred_at": "2026-10-06T07:00:00+00:00"},
                {"event_type": "portfolio_viewed", "brand_id": 2, "brand_name": "Old", "occurred_at": "2026-10-01T07:00:00+00:00"},
            ],
            "due_soon": [{"id": 9, "type": "follow_up_due", "brand_id": 3, "brand_name": "Sol"}],
        }
        items = away_items(tracker, [{"draft_id": 4, "brand_id": 5, "brand_name": "Kora"}], since=since.isoformat())
        self.assertEqual([v["brand_name"] for v in items["views"]], ["Glow"])
        msg = away_brief_message(items, career_from_counts({"pitched_month": 1}, {}, pitch_cap=3))
        self.assertIn("**Glow** opened your kit", msg["content"])
        self.assertIn("**Sol**", msg["content"])
        self.assertIn("**Kora** pitch", msg["content"])
        self.assertIn("1/3 pitches", msg["content"])
        self.assertEqual([c["id"] for c in msg["task_chips"]], ["draft_followup", "draft_followup", "send_draft"])

    def test_send_draft_chip_reopens_pitch(self):
        chip = send_draft_chip({"brand_id": 5, "brand_name": "Kora"})
        self.assertEqual((chip["action"], chip["brand_id"], chip["label"]), ("generate_pitch", 5, "Send my Kora pitch"))


class FollowupGateTests(unittest.TestCase):
    ready = datetime(2026, 10, 9, 12, tzinfo=timezone.utc)

    def test_drops_followup_chips_inside_window(self):
        early = {"7": self.ready, "good molecules": self.ready}
        chips = [
            {"id": "draft_followup", "label": "Draft Good Molecules follow-up", "is_followup": True},
            {"id": "draft_followup", "brand_id": 7, "label": "Follow up"},
            {"id": "draft_followup", "brand_id": 8, "brand_name": "Sol", "label": "Draft Sol follow-up"},
            {"id": "line_up", "label": "More brands"},
        ]
        kept = drop_early_followups(chips, early)
        self.assertEqual([c.get("brand_name") or c["id"] for c in kept], ["Sol", "line_up"])
        self.assertEqual(len(drop_early_followups(chips, {})), 4)

    def test_too_early_copy_names_the_day(self):
        now = datetime(2026, 10, 6, 12, tzinfo=timezone.utc)
        text = persona_followup_too_early("Good Molecules", self.ready, now=now)
        self.assertIn("Too soon", text)
        self.assertIn("**Friday**", text)
        self.assertIn("**tomorrow**", persona_followup_too_early("X", now + timedelta(hours=5), now=now))

    def test_kit_view_inside_window_waits(self):
        alert = portfolio_view_alert("Glow", 3, followup_at=self.ready)
        self.assertIn("Friday", alert["message"])
        self.assertEqual(alert["chips"][0]["id"], "checkin_replied")
        self.assertEqual(portfolio_view_alert("Glow", 3)["chips"][0]["id"], "draft_followup")
        mail = kit_view_email("Ana", "Glow", 3, False, "beauty", followup_at=self.ready)
        self.assertNotIn("chip=draft_followup", mail["html"])
        self.assertIn("Friday", mail["html"])

    def test_away_brief_holds_early_followup(self):
        items = {"views": [{"brand_id": 3, "brand_name": "Glow"}], "drafts": [], "followups": []}
        msg = away_brief_message(items, early={"3": self.ready})
        self.assertIn("until **Friday**", msg["content"])
        self.assertEqual(msg["task_chips"], [])


class ManagerEmailTests(unittest.TestCase):
    def test_unsent_brief_email(self):
        mail = unsent_brief_email("Ana", [{"brand_id": 5, "brand_name": "Kora"}], career_from_counts({}, {}, 3))
        self.assertEqual(mail["subject"], "Your Kora pitch is still waiting")
        self.assertIn("chip=send_draft", mail["html"])
        self.assertIn("0 of 3 pitches sent", mail["html"])
        multi = unsent_brief_email("Ana", [{"brand_id": 5, "brand_name": "Kora"}, {"brand_id": 6, "brand_name": "Sol"}])
        self.assertEqual(multi["subject"], "2 pitches are written and waiting")

    def test_month_report_email(self):
        career = career_from_counts({"pitched": 3}, {}, 3)
        mail = month_report_email("Ana", {"pitched": 3, "kit_views": 1}, career, False, matched_left=6, month_label="September")
        self.assertEqual(mail["subject"], "Your September report card from Polly")
        self.assertIn("<strong>3 pitches</strong>", mail["html"])
        self.assertIn("6 more brands", mail["html"])

    def test_month_reports_only_run_early_in_month(self):
        self.assertEqual(email_month_reports(None, now=datetime(2026, 10, 6, tzinfo=timezone.utc)), [])


if __name__ == "__main__":
    unittest.main()
