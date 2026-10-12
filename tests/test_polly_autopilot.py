import base64
import os
import unittest
from datetime import datetime, timezone
from email import message_from_bytes
from random import Random
from unittest import mock

import routes.polly_autopilot as rpa
import services.polly_autopilot as pa
from services.autopilot_report import next_step, report_note, report_sections, report_subject
from services.polly_autopilot import (
    TARGET_MAX,
    TARGET_MIN,
    WEEK_BATCH,
    batch_size,
    clamp_target,
    compose_followup,
    drafts_note,
    next_slots,
    parse_send_days,
    refill_size,
    sends_per_day,
)
from services.polly_gmail import build_raw, gmail_configured, make_state, read_state


class SlotTests(unittest.TestCase):
    def test_one_weekday_slot_per_day_in_window(self):
        friday = datetime(2026, 10, 9, 18, 0, tzinfo=timezone.utc)
        slots = next_slots(3, friday, rng=Random(1))
        self.assertEqual([s.strftime("%a %d") for s in slots], ["Mon 12", "Tue 13", "Wed 14"])
        self.assertTrue(all(14 <= s.hour < 17 for s in slots))

    def test_today_counts_when_window_not_passed(self):
        tuesday_morning = datetime(2026, 10, 6, 8, 0, tzinfo=timezone.utc)
        self.assertEqual(next_slots(1, tuesday_morning, rng=Random(2))[0].day, 6)

    def test_only_chosen_send_days(self):
        friday = datetime(2026, 10, 9, 18, 0, tzinfo=timezone.utc)
        slots = next_slots(3, friday, rng=Random(1), days=[1, 3])
        self.assertEqual([s.strftime("%a") for s in slots], ["Tue", "Thu", "Tue"])

    def test_two_a_day_when_batch_does_not_fit(self):
        friday = datetime(2026, 10, 9, 18, 0, tzinfo=timezone.utc)
        slots = next_slots(4, friday, rng=Random(3), days=[0, 2], per_day=2)
        self.assertEqual(len(slots), 4)
        self.assertEqual(sorted(slots), slots)
        self.assertEqual({s.strftime("%a") for s in slots}, {"Mon", "Wed"})


class SettingsTests(unittest.TestCase):
    def test_target_is_clamped(self):
        self.assertEqual(clamp_target(2), TARGET_MIN)
        self.assertEqual(clamp_target(99), TARGET_MAX)
        self.assertEqual(clamp_target("20"), 20)
        self.assertEqual(clamp_target("x"), 24)

    def test_send_days_parse(self):
        self.assertEqual(parse_send_days("024"), [0, 2, 4])
        self.assertEqual(parse_send_days("56"), [0, 1, 2, 3, 4])
        self.assertEqual(parse_send_days(""), [0, 1, 2, 3, 4])

    def test_sends_per_day(self):
        self.assertEqual(sends_per_day(24, [0, 1, 2, 3, 4]), 2)
        self.assertEqual(sends_per_day(20, [0, 1, 2, 3, 4]), 1)
        self.assertEqual(sends_per_day(12, [0, 2]), 2)


class BatchTests(unittest.TestCase):
    def test_bigger_targets_get_bigger_weeks(self):
        self.assertEqual(batch_size({"sent": 0}, 30), 8)
        self.assertEqual(batch_size({"sent": 0}, 8), 2)

    def test_week_batch_capped_by_month_target(self):
        self.assertEqual(batch_size({"sent": 0, "scheduled": 0, "drafts": 0}, 24), WEEK_BATCH)
        self.assertEqual(batch_size({"sent": 18, "scheduled": 2, "drafts": 1}, 24), 3)
        self.assertEqual(batch_size({"sent": 24}, 24), 0)
        self.assertEqual(batch_size({"sent": 0}, 12), 3)
        self.assertEqual(batch_size({"sent": 11}, 12), 1)


class FollowupCopyTests(unittest.TestCase):
    def test_reply_subject_and_signoff(self):
        mail = compose_followup("Ana", "Glow", "Glow x Ana collab")
        self.assertEqual(mail["subject"], "Re: Glow x Ana collab")
        self.assertIn("create for Glow", mail["body"])
        self.assertTrue(mail["body"].endswith("Ana"))
        self.assertEqual(compose_followup("", "Glow", "Re: hi")["subject"], "Re: hi")

    def test_second_follow_up_is_a_last_nudge(self):
        mail = compose_followup("Ana", "Glow", "Glow x Ana collab", wave=2)
        self.assertIn("One last nudge", mail["body"])
        self.assertEqual(mail["subject"], "Re: Glow x Ana collab")


class WeeklyReportTests(unittest.TestCase):
    def _facts(self, **kw):
        base = {"sent": [], "followups": [], "scheduled": [], "drafts": [], "replies": [],
                "month": {"sent": 0, "replied": 0}, "target": 24, "enabled": True}
        base.update(kw)
        return base

    def test_reply_leads_subject_and_next_step(self):
        facts = self._facts(
            sent=[{"brand_name": "Glow"}, {"brand_name": "Bloom"}],
            replies=[{"brand_name": "Glow", "event_type": "brand_replied_interested"}],
            month={"sent": 6, "replied": 1},
        )
        self.assertEqual(report_subject(facts), "Your Autopilot week: 1 brand replied")
        labels = [s["label"] for s in report_sections(facts)]
        self.assertEqual(labels, ["Replies", "Pitched", "This month"])
        self.assertIn("6 of 24 brands pitched, 1 reply", report_note(facts))
        self.assertIn("said yes", next_step(facts))

    def test_drafts_waiting_ask_for_ok(self):
        facts = self._facts(drafts=[{"brand_name": "A"}, {"brand_name": "B"}])
        self.assertIn("2 pitches written", report_note(facts))
        self.assertIn("tap OK", next_step(facts))
        self.assertEqual(report_subject(facts), "Your Autopilot week: next pitches are ready")


class GmailMessageTests(unittest.TestCase):
    def test_raw_threads_follow_up(self):
        built = build_raw("ana@gmail.com", "Ana Lee", "pr@glow.com", "Re: Glow collab", "Hi",
                          in_reply_to="<abc@gmail.com>")
        msg = message_from_bytes(base64.urlsafe_b64decode(built["raw"]))
        self.assertEqual(msg["To"], "pr@glow.com")
        self.assertEqual(msg["From"], "Ana Lee <ana@gmail.com>")
        self.assertEqual(msg["In-Reply-To"], "<abc@gmail.com>")
        self.assertEqual(msg["References"], "<abc@gmail.com>")
        self.assertEqual(msg["Message-ID"], built["message_id"])

    def test_state_round_trip_and_tamper(self):
        with mock.patch.dict(os.environ, {"JWT_SECRET_KEY": "test-secret"}):
            state = make_state(42)
            self.assertEqual(read_state(state), 42)
            self.assertIsNone(read_state(state[:-2] + "xx"))
            self.assertIsNone(read_state(""))

    def test_not_configured_without_env(self):
        keys = ("GOOGLE_OAUTH_CLIENT_ID", "GOOGLE_OAUTH_CLIENT_SECRET", "GOOGLE_OAUTH_REDIRECT_URI",
                "POLLY_GMAIL_TOKEN_KEY", "SOCIAL_TOKEN_ENCRYPTION_KEY")
        with mock.patch.dict(os.environ, {k: "" for k in keys}):
            self.assertFalse(gmail_configured())


class RefillTests(unittest.TestCase):
    def test_keeps_a_week_lined_up(self):
        self.assertEqual(refill_size({"sent": 4, "scheduled": 2, "drafts": 0}, 24), 4)
        self.assertEqual(refill_size({"sent": 0, "scheduled": 6, "drafts": 0}, 24), 0)
        self.assertEqual(refill_size({"sent": 0, "scheduled": 2, "drafts": 5}, 24), 0)

    def test_never_past_the_month_target(self):
        self.assertEqual(refill_size({"sent": 21, "scheduled": 1, "drafts": 0}, 24), 2)
        self.assertEqual(refill_size({"sent": 24}, 24), 0)

    def test_drafts_note_names_brands(self):
        note = drafts_note(["Glow", "Kind"])
        self.assertIn("2 new pitches", note)
        self.assertIn("**Glow**, **Kind**", note)
        self.assertIn("skip", note)


class AutoApproveTests(unittest.TestCase):
    def test_old_drafts_are_scheduled(self):
        now = datetime(2026, 10, 12, 9, 0, tzinfo=timezone.utc)
        with mock.patch.object(pa, "_due", return_value=[{"creator_id": 7, "ids": [3, 4]}]) as due, \
                mock.patch.object(pa, "approve", return_value=[{"id": 3}, {"id": 4}]) as appr:
            self.assertEqual(pa.auto_approve_due(object(), now), 2)
        self.assertEqual(due.call_args[0][2], (now - pa.AUTO_APPROVE_AFTER,))
        appr.assert_called_once_with(mock.ANY, 7, [3, 4], now=now)


class AutoFillTests(unittest.TestCase):
    NOW = datetime(2026, 10, 12, 9, 0, tzinfo=timezone.utc)

    def setUp(self):
        from flask import Flask

        self.app = Flask(__name__)
        self.app.secret_key = "test"
        self.notes, self.emails, self.marked = [], [], []
        self.drafted = []
        patches = [
            mock.patch.object(rpa, "_is_pro", return_value=True),
            mock.patch.object(rpa, "_load_creator", return_value={"id": 7, "user_id": 70}),
            mock.patch.object(rpa, "_creator_context", return_value=({}, {})),
            mock.patch("services.polly.resolve_pitch_location", return_value={"city": "Paris"}),
            mock.patch.object(rpa, "_pick_brands",
                              return_value=([{"id": i, "name": f"B{i}"} for i in range(1, 9)], None)),
            mock.patch.object(rpa, "_write_draft", side_effect=self._write),
            mock.patch.object(rpa, "mark_filled", side_effect=lambda c, cid, at, b: self.marked.append((cid, at, b))),
            mock.patch.object(rpa, "claim_unannounced_drafts",
                              side_effect=lambda c, cid, now: [f"B{i}" for i in self.drafted]),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)

    def _write(self, conn, cid, creator, brand_id, notes, scrape, announced=False):
        from flask import session

        self.assertEqual(session["creator_id"], cid)
        self.drafted.append(brand_id)
        return {"brand_name": f"B{brand_id}"}, None

    def _run(self, rows, budget=2):
        with self.app.app_context(), mock.patch.object(rpa, "fill_candidates", return_value=rows):
            return rpa.auto_fill(object(), now=self.NOW, budget=budget,
                                 note_fn=lambda c, cid, text, chips: self.notes.append(text),
                                 email_fn=lambda c, cid, names: self.emails.append(names))

    def test_budget_spreads_a_big_refill_and_waits_to_announce(self):
        out = self._run([{"creator_id": 7, "need": 4, "fill_blocked": None}])
        self.assertEqual(out["drafted"], 2)
        self.assertEqual(self.drafted, [1, 2])
        self.assertEqual(self.marked, [])
        self.assertEqual(self.notes, [])

    def test_finished_refill_announces_once(self):
        out = self._run([{"creator_id": 7, "need": 2, "fill_blocked": None}], budget=4)
        self.assertEqual(out["drafted"], 2)
        self.assertEqual(self.marked, [(7, self.NOW, None)])
        self.assertEqual(self.emails, [["B1", "B2"]])
        self.assertIn("**B1**, **B2**", self.notes[0])

    def test_missing_location_asks_once(self):
        with mock.patch("services.polly.resolve_pitch_location", return_value={"needs_location": True}):
            self._run([{"creator_id": 7, "need": 6, "fill_blocked": None}])
            self._run([{"creator_id": 7, "need": 6, "fill_blocked": "needs_location"}])
        self.assertEqual(self.drafted, [])
        self.assertEqual(len(self.notes), 1)
        self.assertIn("city", self.notes[0])
        self.assertEqual(self.marked[0], (7, self.NOW + pa.FILL_BLOCKED_WAIT, "needs_location"))


if __name__ == "__main__":
    unittest.main()
