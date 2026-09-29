import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from services.roster_waiting_email import (
    RosterEmailState,
    SUBJECT_A,
    SUBJECT_B,
    build_email,
    build_roster_link,
    choose_trigger,
    greeting_for_brand,
    html_subject_for,
    in_send_window,
    passes_caps,
    render_roster_waiting_html,
    safe_brand_name,
    state_from_row,
    subject_for,
    timezone_for_regions,
    variant_for_trigger,
)


def _state(**kwargs):
    base = dict(
        applicant_count=6,
        has_picks=False,
        campaign_active=True,
        engaged=False,
        last_roster_view_days=20,
        last_activity_days=20,
        last_email_days=20,
        waiting_days=20,
        trigger1_sent=False,
        trigger2_sent=False,
        weekly_sent_count=0,
        last_weekly_days=None,
        trigger4_sent=False,
        local_hour=10,
        local_weekday=1,
        has_contact_email=True,
    )
    base.update(kwargs)
    return RosterEmailState(**base)


class TestGreetingAndTimezone(unittest.TestCase):
    def test_uses_first_name(self):
        self.assertEqual(greeting_for_brand({"contact_first_name": "Sarah Chen"}), "Sarah")

    def test_falls_back_to_team(self):
        self.assertEqual(greeting_for_brand({}), "team")
        self.assertEqual(greeting_for_brand({"contact_first_name": "null"}), "team")

    def test_us_region_maps_to_eastern(self):
        self.assertEqual(timezone_for_regions(["US"]), "America/New_York")

    def test_unknown_region_is_utc(self):
        self.assertEqual(timezone_for_regions(["Mars"]), "UTC")


class TestRosterLinkAndSubjects(unittest.TestCase):
    def test_link_has_count_and_utm(self):
        url = build_roster_link("tok_abc", 18, 1, base="https://app.newcollab.co")
        self.assertTrue(url.startswith("https://app.newcollab.co/r/tok_abc?"))
        self.assertIn("count=18", url)
        self.assertIn("utm_source=email", url)
        self.assertIn("utm_campaign=roster_waiting", url)
        self.assertIn("utm_medium=trigger_1", url)

    def test_subject_a_default(self):
        self.assertEqual(
            subject_for(1, 18, "TALGH", "A"),
            "18 creators are waiting to work with TALGH",
        )
        self.assertEqual(SUBJECT_A.format(count=18, brand="TALGH"), subject_for(1, 18, "TALGH", "A"))

    def test_subject_b_second_batch(self):
        self.assertEqual(
            subject_for(3, 18, "TALGH", "B"),
            "Your TALGH roster has 18 creators ready to go",
        )
        self.assertEqual(SUBJECT_B.format(count=18, brand="TALGH"), subject_for(3, 18, "TALGH", "B"))

    def test_weekly_uses_b_when_default_is_a(self):
        self.assertEqual(variant_for_trigger(3, "A"), "B")
        self.assertEqual(variant_for_trigger(1, "A"), "A")

    def test_volume_and_archive_subjects(self):
        self.assertEqual(
            subject_for(2, 12, "TALGH"),
            "12 creators are waiting on TALGH - don't leave them hanging",
        )
        self.assertEqual(subject_for(4, 12, "TALGH"), "Closing your TALGH roster on Friday")

    def test_html_subject_bolds_count(self):
        html = html_subject_for(1, 18, "TALGH", "A")
        self.assertIn("<strong>18</strong>", html)
        self.assertIn("TALGH", html)

    def test_no_em_dash_in_subjects(self):
        for n in (1, 2, 3, 4):
            subj = subject_for(n, 9, "Acme")
            self.assertNotIn("\u2014", subj)
            self.assertNotIn("\u2013", subj)


class TestCapsAndTriggers(unittest.TestCase):
    def test_first_nudge_at_three_unopened(self):
        choice = choose_trigger(_state(applicant_count=3, last_roster_view_days=None, trigger1_sent=False))
        self.assertIsNotNone(choice)
        self.assertEqual(choice.number, 1)

    def test_does_not_nudge_at_two(self):
        self.assertIsNone(choose_trigger(_state(applicant_count=2, last_roster_view_days=None)))

    def test_skips_if_opened_within_five_days(self):
        self.assertIsNone(
            choose_trigger(_state(applicant_count=4, last_roster_view_days=4, trigger1_sent=False))
        )

    def test_volume_after_first_nudge(self):
        choice = choose_trigger(
            _state(
                applicant_count=10,
                trigger1_sent=True,
                trigger2_sent=False,
                last_roster_view_days=8,
            )
        )
        self.assertEqual(choice.number, 2)

    def test_prefers_first_nudge_over_volume_if_never_sent(self):
        choice = choose_trigger(
            _state(applicant_count=12, trigger1_sent=False, last_roster_view_days=None)
        )
        self.assertEqual(choice.number, 1)

    def test_weekly_after_fourteen_days_no_picks(self):
        choice = choose_trigger(
            _state(
                applicant_count=5,
                trigger1_sent=True,
                waiting_days=14,
                weekly_sent_count=0,
            )
        )
        self.assertEqual(choice.number, 3)

    def test_weekly_caps_at_three(self):
        choice = choose_trigger(
            _state(
                applicant_count=5,
                trigger1_sent=True,
                waiting_days=30,
                weekly_sent_count=3,
                last_weekly_days=8,
                trigger4_sent=False,
            )
        )
        self.assertEqual(choice.number, 4)

    def test_weekly_spacing(self):
        self.assertIsNone(
            choose_trigger(
                _state(
                    applicant_count=6,
                    trigger1_sent=True,
                    waiting_days=21,
                    weekly_sent_count=1,
                    last_weekly_days=2,
                )
            )
        )

    def test_frequency_cap(self):
        ok, reason = passes_caps(_state(last_email_days=4))
        self.assertFalse(ok)
        self.assertEqual(reason, "frequency_cap")

    def test_recent_activity_skip(self):
        ok, reason = passes_caps(_state(last_activity_days=2))
        self.assertFalse(ok)
        self.assertEqual(reason, "recent_roster_activity")

    def test_stops_when_engaged(self):
        ok, reason = passes_caps(_state(engaged=True))
        self.assertFalse(ok)
        self.assertEqual(reason, "engaged")

    def test_stops_when_picked(self):
        ok, reason = passes_caps(_state(has_picks=True))
        self.assertFalse(ok)
        self.assertEqual(reason, "already_picked")

    def test_ten_am_window(self):
        self.assertTrue(in_send_window(_state(local_hour=10), 1))
        self.assertFalse(in_send_window(_state(local_hour=9), 1))
        self.assertTrue(in_send_window(_state(local_hour=9), 1, skip_time_checks=True))

    def test_final_nudge_friday_only(self):
        self.assertFalse(in_send_window(_state(local_hour=10, local_weekday=2), 4))
        self.assertTrue(in_send_window(_state(local_hour=10, local_weekday=4), 4))


class TestTemplateRender(unittest.TestCase):
    def test_default_body_and_ps(self):
        html = render_roster_waiting_html(1, "TALGH", "team", 18, "https://app.newcollab.co/r/x?count=18", "subj")
        self.assertIn("Hi team", html)
        self.assertIn("<strong>18 creators</strong>", html)
        self.assertIn("<strong>18", html)
        self.assertIn("TALGH", html)
        self.assertIn("P.S. Applicants typically lose interest", html)
        self.assertIn("Mahery", html)
        self.assertIn("team@newcollab.co", html)
        self.assertIn("Review your roster", html)
        self.assertNotIn("\u2014", html)
        self.assertNotIn("\u2013", html)

    def test_weekly_opening(self):
        html = render_roster_waiting_html(3, "TALGH", "Sarah", 7, "https://app.newcollab.co/r/x", "subj")
        self.assertIn("Just checking back", html)
        self.assertIn("hasn't been reviewed yet", html)
        self.assertIn("Hi Sarah", html)

    def test_archive_copy_no_ps(self):
        html = render_roster_waiting_html(4, "TALGH", "team", 9, "https://app.newcollab.co/r/x", "subj")
        self.assertIn("archive your Newcollab roster on Friday", html)
        self.assertIn("No hard feelings either way", html)
        self.assertNotIn("P.S.", html)
        self.assertIn("Keep this roster open", html)

    def test_escapes_brand_name(self):
        html = render_roster_waiting_html(
            1, "<script>alert(1)</script>", "team", 3, "https://app.newcollab.co/r/x", "subj"
        )
        self.assertNotIn("<script>", html)
        self.assertIn("&lt;script&gt;", html)

    def test_build_email_wires_utm_and_count(self):
        payload = build_email(1, "TALGH", "team", 18, "abc123", variant="A", base_url="https://app.newcollab.co")
        self.assertEqual(payload["trigger_number"], 1)
        self.assertEqual(payload["email_type"], "first_nudge")
        self.assertIn("count=18", payload["roster_link"])
        self.assertIn("utm_medium=trigger_1", payload["html"])
        self.assertIn("18 creators are waiting to work with TALGH", payload["subject"])
        self.assertNotIn("\u2014", payload["html"])
        self.assertNotIn("\u2014", payload["subject"])


class TestStateFromRow(unittest.TestCase):
    def test_reply_marks_engaged(self):
        now = datetime(2026, 9, 29, 8, 0, tzinfo=timezone.utc)
        state = state_from_row(
            {
                "applicant_count": 6,
                "contact_email": "pr@brand.com",
                "campaign_status": "active",
                "outreach_status": "replied",
                "regions": ["UK"],
            },
            now,
        )
        self.assertTrue(state.engaged)
        self.assertEqual(timezone_for_regions(["UK"]), "Europe/London")

    def test_selected_ids_count_as_picks(self):
        now = datetime(2026, 9, 29, 8, 0, tzinfo=timezone.utc)
        state = state_from_row(
            {
                "applicant_count": 6,
                "contact_email": "pr@brand.com",
                "campaign_status": "active",
                "selected_application_ids": [11, 12],
                "regions": [],
            },
            now,
        )
        self.assertTrue(state.has_picks)

    def test_days_ago(self):
        now = datetime(2026, 9, 29, 12, 0, tzinfo=timezone.utc)
        state = state_from_row(
            {
                "applicant_count": 6,
                "contact_email": "pr@brand.com",
                "campaign_status": "active",
                "last_roster_view_at": now - timedelta(days=6),
                "regions": ["US"],
            },
            now,
        )
        self.assertAlmostEqual(state.last_roster_view_days, 6.0, places=4)


class TestBrandNameSanitize(unittest.TestCase):
    def test_strips_newlines(self):
        self.assertEqual(safe_brand_name("TALGH\nPromo"), "TALGH Promo")


class TestProcessDryRun(unittest.TestCase):
    def test_dry_run_sends_nothing(self):
        from services import roster_waiting_email as mod

        now = datetime(2026, 9, 29, 8, 0, tzinfo=timezone.utc)  # 10:00 Europe/Paris in late Sep is UTC+2
        row = {
            "brand_id": 1,
            "brand_name": "TALGH",
            "contact_email": "pr@talgh.test",
            "regions": ["FR"],
            "contact_first_name": None,
            "campaign_id": 9,
            "token": "tok_test",
            "campaign_status": "active",
            "selected_application_ids": [],
            "locked_at": None,
            "applicant_count": 4,
            "picked_count": 0,
            "oldest_waiting_at": now - timedelta(days=8),
            "last_roster_view_at": None,
            "last_roster_activity_at": None,
            "last_email_at": None,
            "trigger1_count": 0,
            "trigger2_count": 0,
            "weekly_count": 0,
            "last_weekly_at": None,
            "trigger4_count": 0,
            "email_engaged": False,
            "outreach_status": None,
        }
        sent = []
        cursor = MagicMock()
        conn = MagicMock()
        conn.cursor.return_value = cursor

        orig_ensure = mod.ensure_brand_emails_table
        orig_load = mod.load_candidates
        orig_sync = mod._sync_opens_and_engagement
        mod.ensure_brand_emails_table = lambda *a, **k: None
        mod.load_candidates = lambda *a, **k: [row]
        mod._sync_opens_and_engagement = lambda *a, **k: None
        try:
            stats = mod.process_roster_waiting_emails(
                dry_run=True,
                limit=8,
                skip_time_checks=True,
                send_fn=lambda *a, **k: sent.append(a) or {"success": True, "message_id": "x"},
                now=now,
                conn=conn,
            )
        finally:
            mod.ensure_brand_emails_table = orig_ensure
            mod.load_candidates = orig_load
            mod._sync_opens_and_engagement = orig_sync

        self.assertEqual(sent, [])
        self.assertEqual(stats["eligible"], 1)
        self.assertEqual(stats["would_send"][0]["trigger"], 1)
        self.assertEqual(stats["would_send"][0]["to"], "pr@talgh.test")


class TestResendSendPath(unittest.TestCase):
    @patch("services.resend_mail.send_resend_email")
    def test_default_mailer_uses_resend_not_smtp(self, mock_resend):
        mock_resend.return_value = {"success": True, "message_id": "re_abc", "error": None}
        with patch.dict("os.environ", {"RESEND_API_KEY": "re_test"}):
            from services.roster_waiting_email import _default_send_fn

            result = _default_send_fn("pr@brand.test", "subj", "<p>hi</p>", 2)
        self.assertTrue(result["success"])
        mock_resend.assert_called_once()
        args, kwargs = mock_resend.call_args
        self.assertEqual(args[0], "pr@brand.test")
        tags = kwargs.get("tags") or []
        self.assertEqual(tags[0]["value"], "roster_waiting")
        self.assertEqual(tags[1]["value"], "2")

    def test_default_mailer_does_not_import_gmail(self):
        import inspect
        from services.roster_waiting_email import _default_send_fn

        source = inspect.getsource(_default_send_fn)
        self.assertIn("send_resend_email", source)
        self.assertNotIn("gmail", source.lower())
        self.assertNotIn("smtp", source.lower())


if __name__ == "__main__":
    unittest.main()
