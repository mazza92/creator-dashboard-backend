"""Fixes from the Nov 2026 audit of Polly's conversations: real pauses, an honest AI, one send ceiling for every
Gmail send, follow-ups that don't retry a dead thread forever, $ only, and no false "out of free pitches"."""

import os
import unittest
from datetime import datetime, timedelta, timezone
from unittest import mock

import psycopg2

from services import polly_autopilot as pa
from services import polly_brain as brain
from services.polly_gmail import GmailError
from services.polly_persona import scrub_polly_voice
from tests.test_polly_brain import make_state

NOW = datetime(2026, 11, 19, 15, tzinfo=timezone.utc)


# ----- guardrails ---------------------------------------------------------------------------------------------

class GuardrailTests(unittest.TestCase):
    def test_a_pause_claim_needs_the_pause_tool(self):
        state = make_state(user_text="don't send emails now")
        self.assertTrue(any("pause_outreach" in p for p in
                            brain.reply_problems(state, "Got it. I've paused all pitches and follow-ups for you.")))
        state.tool_log.append({"name": "pause_outreach", "status": "paused", "brand_names": [], "result": {}})
        self.assertFalse(brain.reply_problems(state, "Got it. I've paused all pitches and follow-ups for you."))

    def test_polly_never_claims_to_be_human(self):
        state = make_state()
        for text in ("I'm not an AI. I'm Polly, your talent manager.", "Look, I'm a real person who joined Newcollab."):
            self.assertTrue(any("AI manager" in p for p in brain.reply_problems(state, text)), text)
        self.assertFalse(brain.reply_problems(state, "I'm Newcollab's AI talent manager. Let's get you replies."))

    def test_no_false_out_of_free_pitches(self):
        state = make_state(balance={"remaining": 2, "limit": 3, "is_unlimited": False})
        self.assertTrue(brain.reply_problems(state, "You're out of free pitches, so upgrade to send this one."))
        pro = make_state(balance={"is_unlimited": True})
        self.assertTrue(brain.reply_problems(pro, "Each one uses one of your 0 free pitches."))
        out = make_state(balance={"remaining": 0, "limit": 3, "is_unlimited": False})
        self.assertFalse(brain.reply_problems(out, "You're out of free pitches this month."))

    def test_prices_are_dollars(self):
        self.assertEqual(scrub_polly_voice("Pro is £19/mo, cancel anytime."), "Pro is $19/mo, cancel anytime.")
        self.assertIn("$19", scrub_polly_voice("It's €19 a month."))


# ----- the pause tool (real database) -------------------------------------------------------------------------

SCHEMA = "audit_fixes_t"


def _conn():
    return psycopg2.connect(host=os.getenv("DB_HOST"), port=os.getenv("DB_PORT"), dbname=os.getenv("DB_NAME"),
                            user=os.getenv("DB_USER"), password=os.getenv("DB_PASSWORD") or None,
                            options=f"-c search_path={SCHEMA}")


class PauseTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            boot = psycopg2.connect(host=os.getenv("DB_HOST"), port=os.getenv("DB_PORT"), dbname=os.getenv("DB_NAME"),
                                    user=os.getenv("DB_USER"), password=os.getenv("DB_PASSWORD") or None)
        except Exception as err:
            raise unittest.SkipTest(f"no db: {err}")
        boot.autocommit = True
        boot.cursor().execute(f"DROP SCHEMA IF EXISTS {SCHEMA} CASCADE; CREATE SCHEMA {SCHEMA};")
        boot.close()

    @classmethod
    def tearDownClass(cls):
        c = _conn()
        c.autocommit = True
        c.cursor().execute(f"DROP SCHEMA IF EXISTS {SCHEMA} CASCADE")
        c.close()

    def setUp(self):
        pa._TABLES_READY = False
        self.conn = _conn()
        pa.ensure_autopilot_tables(self.conn)
        cur = self.conn.cursor()
        for table, col, ddl in pa._ADDED_COLUMNS:
            cur.execute(f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS {col} {ddl}")
        cur.execute("TRUNCATE polly_autopilot, polly_autopilot_queue")
        cur.execute("INSERT INTO polly_autopilot (creator_id, enabled) VALUES (9, TRUE)")
        for i, (status, fu) in enumerate((("sent", "scheduled"), ("sent", "headsup"), ("approved", None))):
            cur.execute("""INSERT INTO polly_autopilot_queue (creator_id, brand_id, brand_name, to_email, subject, body,
                           status, followup_status) VALUES (9, %s, %s, 'pr@b.com', 's', 'b', %s, %s)""",
                        (100 + i, f"B{i}", status, fu))
        self.conn.commit()

    def tearDown(self):
        self.conn.close()

    def _tool(self, action):
        from services import polly_tools as tools
        state = make_state(creator_id=9, conn=self.conn)
        return tools.run_tool(state, "pause_outreach", {"action": action}), state

    def test_pause_all_switches_autopilot_off_and_resume_puts_it_back(self):
        res, state = self._tool("pause_all")
        self.assertEqual(res["status"], "paused")
        self.assertFalse(pa.settings(self.conn, 9)["enabled"])
        self.assertTrue(state.tools_ran("pause_outreach"))
        res, _ = self._tool("resume")
        self.assertEqual(res["status"], "resumed")
        self.assertTrue(pa.settings(self.conn, 9)["enabled"])

    def test_stop_followups_cancels_the_scheduled_ones(self):
        res, _ = self._tool("stop_followups")
        self.assertEqual(res["followups_cancelled"], 2)
        self.assertEqual(res["followups_scheduled"], 0)
        self.assertEqual(pa.settings(self.conn, 9)["followups"], 0)
        res, _ = self._tool("resume")
        self.assertEqual(pa.settings(self.conn, 9)["followups"], pa.DEFAULT_FOLLOWUPS)

    def test_send_room_counts_every_gmail_send_in_24h(self):
        cur = self.conn.cursor()
        for i in range(18):
            cur.execute("""INSERT INTO polly_autopilot_queue (creator_id, brand_id, brand_name, to_email, subject, body,
                           status, sent_at) VALUES (9, %s, 'x', 'a@b.com', 's', 'b', 'sent', %s)""",
                        (500 + i, NOW - timedelta(hours=3)))
        cur.execute("""INSERT INTO polly_autopilot_queue (creator_id, brand_id, brand_name, to_email, subject, body,
                       status, sent_at) VALUES (9, 999, 'x', 'a@b.com', 's', 'b', 'sent', %s)""", (NOW - timedelta(days=2),))
        self.conn.commit()
        self.assertEqual(pa.send_room_today(self.conn, 9, NOW), pa.MAX_SENDS_PER_DAY - 18)


# ----- follow-ups to a thread Gmail can't find -----------------------------------------------------------------

def _follow(error=None):
    return {"id": 1, "creator_id": 9, "brand_id": 11, "brand_name": "B1", "to_email": "pr@b.com", "subject": "s",
            "body": "b", "followups": 2, "followups_sent": 0, "sent_at": NOW - timedelta(days=5),
            "followup_due_at": NOW, "thread_id": "gone", "message_id": "m", "pitch_meta": {}, "error": error}


def _run_followup(send_fn, item):
    sets = []
    batches = iter([[], [], [item]])
    patches = [
        mock.patch.object(pa, "ensure_autopilot_tables"), mock.patch("services.polly_gmail.ensure_gmail_table"),
        mock.patch.object(pa, "auto_approve_due", return_value=0),
        mock.patch.object(pa, "_due", side_effect=lambda *a, **k: next(batches, [])),
        mock.patch.object(pa, "_set", side_effect=lambda conn, item_id, **f: sets.append(f)),
        mock.patch.object(pa, "_brand_closed", return_value=False),
        mock.patch.object(pa, "_followed_up_by_hand", return_value=False),
        mock.patch.object(pa, "_creator_names", return_value={"first": "Nat", "full": "Nat R"}),
        mock.patch.object(pa, "_close_day4_task"),
        mock.patch("services.deliverability.gate", return_value={"send": True}),
        mock.patch("services.polly_tracker.add_event"),
    ]
    for p in patches:
        p.start()
    try:
        out = pa.run_autopilot(None, now=NOW, send_fn=send_fn, headsup_fn=lambda *a: None,
                               daily_fn=lambda *a, **k: None, note_fn=lambda *a: None, verify_fn=lambda *a: None)
    finally:
        for p in patches:
            p.stop()
    return out, sets


class FollowupFailureTests(unittest.TestCase):
    def test_missing_thread_is_sent_as_a_fresh_message(self):
        calls = []

        def send(conn, cid, to, subject, body, **kw):
            calls.append(kw.get("thread_id"))
            if kw.get("thread_id"):
                raise GmailError("send failed: Requested entity was not found.")
            return {"gmail_id": "g2"}

        out, sets = _run_followup(send, _follow())
        self.assertEqual(calls, ["gone", None])
        self.assertEqual(out["followups"], 1)

    def test_repeated_failures_cancel_instead_of_retrying_forever(self):
        def send(conn, cid, to, subject, body, **kw):
            raise GmailError("send failed: quota")

        _, sets = _run_followup(send, _follow())
        self.assertIn("followup_failed:1", sets[-1]["error"])
        self.assertNotIn("followup_status", sets[-1])
        _, sets = _run_followup(send, _follow(error="followup_failed:2 send failed: quota"))
        self.assertEqual(sets[-1]["followup_status"], "cancelled")

    def test_revoked_gmail_waits_for_reconnect(self):
        def send(conn, cid, to, subject, body, **kw):
            raise GmailError("Gmail access was revoked", revoked=True)

        _, sets = _run_followup(send, _follow(error="followup_failed:2 x"))
        self.assertNotIn("followup_status", sets[-1])


class MatchingTests(unittest.TestCase):
    def test_creators_with_rates_or_a_big_audience_ask_for_paid_more(self):
        from services.creator_tracker import paid_share
        base = paid_share(20000, 1500)
        self.assertGreaterEqual(paid_share(20000, 1500, has_rates=True), 0.5)
        self.assertGreaterEqual(paid_share(20000, 84000), 0.6)
        self.assertLessEqual(base, paid_share(20000, 1500, has_rates=True))

    def test_spf_never_gets_a_night_routine_idea(self):
        from services.pitch_writer import build_facts
        facts = build_facts({"body": "Hi", "product_name": "Unseen Sunscreen SPF 40", "niche": "skincare"},
                            brand={"brand_name": "Supergoop!", "category": "skincare"},
                            profile={"tone": "brief"}, seed="x")
        self.assertFalse(any("night" in i for i in facts["ideas"]))

    def test_each_niche_has_six_ideas(self):
        from services.pitch_writer import DEFAULT_IDEAS, IDEA_BANK
        self.assertTrue(all(len(v) >= 6 for v in IDEA_BANK.values()))
        self.assertGreaterEqual(len(DEFAULT_IDEAS), 6)


if __name__ == "__main__":
    unittest.main()
