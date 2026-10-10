"""Pro manager: Monday board, renewal note, and the paid usage reply on a yes."""

import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import services.polly_tools as tools  # noqa: E402
from services.checkin_links import ONE_TAP_NEXT  # noqa: E402
from services.paid_ask import (  # noqa: E402
    ADDRESS_LINE, ad_usage_ask, free_teaser, interested_reply, usage_rate,
)
from services.polly import last_thread_pitch  # noqa: E402
from services.polly_memory import sanitize_thread  # noqa: E402
from services.pro_manager import (  # noqa: E402
    board_due, board_from_facts, board_summary, next_renewal, renewal_due, renewal_lines,
    week_key, week_start,
)
from tests.test_polly_brain import _Patched, make_state  # noqa: E402

THU = datetime(2026, 10, 8, 15, 0, tzinfo=timezone.utc)


def _facts(**over):
    base = {
        "gmail_available": True,
        "gmail_connected": True,
        "kit_published": True,
        "autopilot": {"weekly_target": 6, "sent_week": 0, "scheduled": 0, "drafts": 0,
                      "next_batch": 6, "month_sent": 4, "monthly_target": 24},
        "paid_applied_week": 0,
        "replies_open": [],
        "posted_open": [],
        "stalled": False,
        "week": {"pitched": 2, "replies": 0, "kit_views": 1},
    }
    base.update(over)
    return base


def _ids(board):
    return [t["id"] for t in board["tasks"]]


class WeekTests(unittest.TestCase):
    def test_week_starts_monday_utc(self):
        self.assertEqual(week_start(THU), datetime(2026, 10, 5, tzinfo=timezone.utc))
        self.assertEqual(week_key(THU), "2026-10-05")

    def test_board_due_once_a_week(self):
        self.assertTrue(board_due({}, THU))
        self.assertFalse(board_due({"board_week": "2026-10-05"}, THU))
        self.assertTrue(board_due({"board_week": "2026-09-28"}, THU))


class BoardTests(unittest.TestCase):
    def test_setup_first_then_money_tasks(self):
        board = board_from_facts(_facts(gmail_connected=False, kit_published=False), THU)
        self.assertEqual(_ids(board)[:3], ["gmail", "kit", "paid_briefs"])
        self.assertNotIn("approve", _ids(board))
        self.assertEqual(board["tasks"][0]["chip"]["action"], "autopilot")

    def test_yes_and_posted_brands_get_the_paid_moves(self):
        board = board_from_facts(_facts(
            replies_open=[{"brand_id": 4, "brand_name": "Glow Co"}],
            posted_open=[{"brand_id": 9, "brand_name": "Sol"}],
        ), THU)
        reply = next(t for t in board["tasks"] if t["id"] == "reply_4")
        self.assertEqual(reply["chip"]["action"], "paid_reply")
        self.assertEqual(reply["chip"]["brand_id"], 4)
        ad = next(t for t in board["tasks"] if t["id"] == "ad_9")
        self.assertEqual(ad["chip"]["action"], "ad_usage")

    def test_stalled_pitching_offers_a_swap(self):
        board = board_from_facts(_facts(stalled=True), THU)
        swap = next(t for t in board["tasks"] if t["id"] == "swap")
        self.assertEqual(swap["chip"]["action"], "suggest_brands")

    def test_left_counts_open_tasks_and_summary_states_the_goal(self):
        board = board_from_facts(_facts(paid_applied_week=2,
                                        autopilot={"weekly_target": 6, "sent_week": 6, "next_batch": 3}), THU)
        self.assertEqual(board["left"], 0)
        self.assertEqual(board["week_label"], "Oct 5")
        text = board_summary(board, "Ana")
        self.assertIn("Ana", text)
        self.assertIn("one yes", text)

    def test_board_message_survives_thread_save(self):
        board = board_from_facts(_facts(), THU)
        clean = sanitize_thread([{"role": "assistant", "kind": "board", "content": "x", "board": board}])
        self.assertEqual(clean["messages"][0]["kind"], "board")
        self.assertEqual(clean["messages"][0]["board"]["week_of"], "2026-10-05")


class RenewalTests(unittest.TestCase):
    def test_stale_period_end_rolls_forward_monthly(self):
        started = datetime(2026, 7, 13, 9, 0, tzinfo=timezone.utc)
        self.assertEqual(next_renewal(started, None, THU), datetime(2026, 10, 13, 9, 0, tzinfo=timezone.utc))
        ends = datetime(2026, 9, 13, 9, 0)
        self.assertEqual(next_renewal(None, ends, THU), datetime(2026, 10, 13, 9, 0, tzinfo=timezone.utc))
        self.assertIsNone(next_renewal(None, None, THU))

    def test_month_end_anchor_clamps(self):
        started = datetime(2026, 1, 31, tzinfo=timezone.utc)
        self.assertEqual(next_renewal(started, None, datetime(2026, 2, 10, tzinfo=timezone.utc)),
                         datetime(2026, 2, 28, tzinfo=timezone.utc))

    def test_note_window_is_one_to_six_days_out(self):
        self.assertTrue(renewal_due(datetime(2026, 10, 13, tzinfo=timezone.utc), THU))
        self.assertFalse(renewal_due(datetime(2026, 10, 9, tzinfo=timezone.utc), THU))
        self.assertFalse(renewal_due(datetime(2026, 10, 20, tzinfo=timezone.utc), THU))
        self.assertFalse(renewal_due(None, THU))

    def test_lines_show_the_month_and_whats_queued(self):
        lines = renewal_lines({"pitched": 12, "followups": 5, "kit_views": 3, "replies": 0},
                              datetime(2026, 10, 13, tzinfo=timezone.utc),
                              ["Glow Co", "Sol", "Kin", "Vela", "Oro", "Extra"])
        text = "\n".join(lines)
        self.assertIn("October 13", text)
        self.assertIn("**12 brands** pitched, 5 follow-ups sent", text)
        self.assertIn("**3** kit views", text)
        self.assertIn("2–4 weeks", text)
        self.assertIn("**Oro**", text)
        self.assertNotIn("Extra", text)

    def test_idle_month_says_why_instead_of_zero_stats(self):
        renews = datetime(2026, 10, 13, tzinfo=timezone.utc)
        lines = renewal_lines({"pitched": 0, "kit_views": 2}, renews, ["Glow Co"], gmail_connected=False)
        text = "\n".join(lines)
        self.assertIn("Gmail isn't connected", text)
        self.assertIn("**2** times", text)
        self.assertIn("**Glow Co**", text)
        self.assertNotIn("0 brands", text)
        self.assertNotIn("still live", text)
        waiting = "\n".join(renewal_lines({"pitched": 0}, renews, [], gmail_connected=True))
        self.assertIn("no pitches have been OK'd", waiting)
        self.assertIn("Monday board", waiting)

    def test_replies_point_at_the_paid_ask(self):
        lines = renewal_lines({"pitched": 8, "replies": 1, "kit_views": 1},
                              datetime(2026, 10, 13, tzinfo=timezone.utc), [])
        self.assertTrue(any("1 reply" in l and "paid usage" in l for l in lines))


class PaidAskTemplateTests(unittest.TestCase):
    def test_rate_scales_with_followers(self):
        self.assertEqual(usage_rate(None), 150)
        self.assertEqual(usage_rate(1500), 150)
        self.assertEqual(usage_rate(5000), 200)
        self.assertEqual(usage_rate(12000), 250)
        self.assertEqual(usage_rate(40000), 300)

    def test_interested_reply_has_address_and_usage_rate(self):
        mail = interested_reply("Glow Co", "Ana", "Ana Ruiz", "Austin, United States", 5000,
                                subject="Glow Co x Ana")
        self.assertEqual(mail["subject"], "Re: Glow Co x Ana")
        self.assertIn("Ana Ruiz\n" + ADDRESS_LINE + "\nAustin, United States", mail["body"])
        self.assertIn("30 days of paid usage for $200", mail["body"])
        self.assertIn("Whitelisting", mail["body"])

    def test_saved_address_replaces_the_placeholder(self):
        addr = {"full_name": "Ana Ruiz", "address_line1": "12 Oak St", "city": "Austin", "state": "TX",
                "postal_code": "78701", "country": "United States"}
        mail = interested_reply("Glow Co", "Ana", address=addr)
        self.assertIn("12 Oak St\nAustin TX 78701\nUnited States", mail["body"])
        self.assertNotIn(ADDRESS_LINE, mail["body"])

    def test_after_post_ask(self):
        mail = ad_usage_ask("Sol", "Ana", 30000)
        self.assertIn("run it as an ad", mail["body"])
        self.assertIn("$300", mail["body"])
        self.assertEqual(mail["subject"], "Re: Sol collab")

    def test_free_teaser_gives_the_line_and_names_pro(self):
        say = free_teaser("Glow Co", 5000)
        self.assertIn("$200", say)
        self.assertIn("On Pro", say)

    def test_reply_cards_never_count_as_the_unsent_pitch(self):
        msgs = [{"role": "assistant", "pitch": {"brand_name": "A", "body": "pitch"}},
                {"role": "assistant", "pitch": {"brand_name": "A", "body": "reply", "is_reply": True}}]
        self.assertEqual(last_thread_pitch(msgs)["body"], "pitch")

    def test_one_tap_yes_opens_the_reply(self):
        self.assertEqual(ONE_TAP_NEXT["checkin_interested"], "paid_reply")


def _conn():
    conn = MagicMock(closed=0)
    conn.cursor.return_value.fetchone.return_value = None
    return conn


class DraftBrandReplyToolTests(_Patched):
    PRO = {"remaining": None, "limit": None, "is_unlimited": True}

    def test_free_gets_the_teaser_and_upgrade_chip(self):
        state = make_state(scrape={"follower_count": 5000}, conn=_conn())
        with patch.object(tools.tracker, "add_event") as add:
            res = tools.run_tool(state, "draft_brand_reply", {"brand_name": "NordVPN"})
        self.assertEqual(res["status"], "pro_only")
        self.assertIsNone(state.pitch)
        self.assertIn("$200", state.final_say)
        self.assertTrue(any(c["id"] == "unlock_pro" for c in state.task_chips))
        add.assert_not_called()

    def test_pro_gets_a_reply_card_to_the_pitched_inbox(self):
        stored = [{"role": "assistant", "pitch": {"brand_id": 4, "brand_name": "NordVPN",
                                                   "email": "pr@nord.example", "subject": "NordVPN x Abdullahi",
                                                   "body": "Hi"}}]
        state = make_state(balance=self.PRO, stored_messages=stored, conn=_conn(),
                           creator={"id": 7, "first_name": "Abdullahi", "last_name": "Musa", "country": "UK"})
        with patch.object(tools.tracker, "add_event") as add:
            res = tools.run_tool(state, "draft_brand_reply", {"brand_name": "NordVPN"})
        self.assertEqual(res["status"], "reply_drafted")
        self.assertTrue(state.pitch["is_reply"])
        self.assertEqual(state.pitch["email"], "pr@nord.example")
        self.assertEqual(state.pitch["subject"], "Re: NordVPN x Abdullahi")
        self.assertTrue(state.pitch["mailto"].startswith("mailto:pr@nord.example"))
        self.assertIn("$150", state.pitch["body"])
        self.assertIn("NordVPN", state.final_say)
        self.assertEqual(add.call_args[0][2], "paid_ask_drafted")

    def test_pro_posted_moment_logs_the_ad_ask(self):
        state = make_state(balance=self.PRO, conn=_conn())
        with patch.object(tools.tracker, "add_event") as add:
            tools.run_tool(state, "draft_brand_reply", {"brand_name": "Secretlab", "moment": "posted"})
        self.assertEqual(state.pitch["reply_kind"], "ad_usage")
        self.assertEqual(add.call_args[0][2], "ad_usage_asked")

    def test_pro_week_question_returns_the_board(self):
        state = make_state(balance=self.PRO, conn=_conn())
        board = board_from_facts(_facts(), THU)
        with patch("services.pro_manager.load_board", return_value=board):
            res = tools.run_tool(state, "get_coaching_facts", {"topic": "week"})
        self.assertTrue(res["board_card_shown"])
        self.assertIs(state.board, board)
        self.assertIn("Monday board", state.final_say)


if __name__ == "__main__":
    unittest.main()
