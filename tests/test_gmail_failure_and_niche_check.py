import unittest
from unittest.mock import MagicMock, patch

import routes.polly as R
import routes.polly_autopilot as rpa
import services.autopilot_health as ah
import services.niche_check as nc
import services.polly_tools as pt
from app import app
from services.polly_gmail import GmailError, make_state
from services.polly_turn_state import TurnState

PET_SCRAPE = {"primary_niche": "pet", "primary_niche_confidence": 95}
PICKS = {"creator_niches": ["skincare", "wellness", "fashion"]}


def _state(**over):
    base = dict(
        creator_id=4083, creator=dict(PICKS), first="Brianna", scrape=dict(PET_SCRAPE), kit={}, profile_context="",
        coach_moves=[], balance={"remaining": 3}, notes={}, messages=[], stored_messages=[], suggested=[],
        tracker_ctx={}, early_fu={}, career=None, user_text="", chip={},
    )
    base.update(over)
    return TurnState(**base)


class NicheConflictTests(unittest.TestCase):
    def test_posts_and_signup_disagree(self):
        found = nc.conflict(PET_SCRAPE, PICKS, {})
        self.assertEqual(found, {"scraped": "pet", "picked": ["skincare", "wellness", "fashion"]})

    def test_no_question_when_they_agree_or_scrape_is_unsure(self):
        self.assertIsNone(nc.conflict({"primary_niche": "supplements", "primary_niche_confidence": 95}, PICKS, {}))
        self.assertIsNone(nc.conflict({"primary_niche": "pet", "primary_niche_confidence": 50}, PICKS, {}))
        self.assertIsNone(nc.conflict({"primary_niche": "lifestyle", "primary_niche_confidence": 99}, PICKS, {}))
        self.assertIsNone(nc.conflict(PET_SCRAPE, {}, {}))

    def test_skincare_content_vs_wellness_pick_is_asked(self):
        # Wellness and skincare are separate lanes: a skincare feed with wellness picks gets the one-time question.
        self.assertIsNotNone(nc.conflict({"primary_niche": "skincare", "primary_niche_confidence": 95}, PICKS, {}))

    def test_asked_once_or_told_in_chat(self):
        self.assertIsNone(nc.conflict(PET_SCRAPE, PICKS, {"niche_check": {"asked_at": "x"}}))
        self.assertIsNone(nc.conflict(PET_SCRAPE, PICKS, {"niche": ["pet"]}))

    def test_question_has_three_answers(self):
        q = nc.question({"scraped": "pet", "picked": ["skincare", "wellness"]})
        self.assertIn("**Pets**", q["say"])
        self.assertIn("**Skincare, Wellness**", q["say"])
        self.assertEqual([c["niche"] for c in q["chips"]], ["pet", "skincare,wellness", "pet,skincare,wellness"])
        self.assertTrue(all(c["action"] == "set_niche" for c in q["chips"]))


class SuggestBrandsAsksFirstTests(unittest.TestCase):
    def test_first_matches_wait_for_the_answer(self):
        state = _state()
        with patch.object(pt, "_routes") as routes:
            out = pt.suggest_brands(state, {})
        routes.assert_not_called()
        self.assertEqual(out["status"], "niche_question")
        self.assertIn("Which should I find brands for?", state.final_say)
        self.assertEqual({c["id"] for c in state.task_chips}, {"niche_scraped", "niche_picked", "niche_both"})
        self.assertIn("niche_check", state.notes)

    def test_not_asked_twice(self):
        state = _state(notes={"niche_check": {"asked_at": "x"}})
        state.conn = MagicMock(closed=0)
        with patch.object(pt, "_routes") as routes:
            routes.return_value._suggest_payload.return_value = (200, [], None)
            out = pt.suggest_brands(state, {})
        self.assertNotEqual(out.get("status"), "niche_question")

    def test_answer_is_saved_then_brands_follow(self):
        state = _state(notes={"niche_check": {"asked_at": "x"}})
        state.conn = MagicMock(closed=0)
        with patch("services.creator_niches.save_creator_niches", return_value=["pet"]) as save, \
                patch.object(R, "run_tool", return_value={"ok": True}) as run:
            prior = R._run_chip(state, "set_niche", "niche_scraped", {"niche": "pet"})
        save.assert_called_once()
        self.assertEqual(save.call_args[0][2], ["pet"])
        self.assertEqual(state.notes["niche"], ["pet"])
        self.assertEqual(state.notes["niche_check"]["answered"], ["pet"])
        self.assertEqual(run.call_args[0][1], "suggest_brands")
        self.assertEqual(prior[0]["tool"], "set_niche")


class GmailFailureTests(unittest.TestCase):
    def setUp(self):
        self.client = app.test_client()

    def test_denied_goes_to_chat_right_away(self):
        with patch.object(rpa, "_note_gmail_failure") as noted:
            resp = self.client.get(f"/api/polly/autopilot/gmail/callback?state={make_state(4083)}&error=access_denied")
        self.assertEqual(resp.status_code, 302)
        self.assertIn("autopilot=gmail_denied", resp.headers["Location"])
        noted.assert_called_once_with(4083, "gmail_denied")

    def test_scope_unticked_goes_to_chat_right_away(self):
        conn = MagicMock()
        with patch("pr_crm_routes.get_db_connection", return_value=conn), \
                patch.object(rpa, "exchange_code", side_effect=GmailError("gmail.send not granted")), \
                patch.object(rpa, "_note_gmail_failure") as noted:
            resp = self.client.get(f"/api/polly/autopilot/gmail/callback?state={make_state(4083)}&code=abc")
        self.assertIn("autopilot=gmail_scope", resp.headers["Location"])
        self.assertEqual(noted.call_args[0][:2], (4083, "gmail_scope"))

    def test_note_has_retry_and_tick_box_steps(self):
        conn = MagicMock()
        with patch("services.autopilot_health.record_gmail_failure") as rec, \
                patch("services.polly_autopilot.post_thread_note") as post:
            rpa._note_gmail_failure(4083, "gmail_scope", conn)
        rec.assert_called_once_with(conn, 4083, "gmail_scope")
        text, chips = post.call_args[0][2], post.call_args[0][3]
        self.assertIn("Send email on your behalf", text)
        self.assertEqual(chips[0]["action"], "autopilot")
        self.assertEqual(post.call_args[1]["dedupe_key"], "gmail_connect_failed")

    def test_reminder_email_repeats_the_tick_box_step(self):
        mail = ah.gmail_email("Brianna", {"failed": "gmail_scope"})
        self.assertIn("Send email on your behalf", mail["html"])
        plain = ah.gmail_email("Brianna", {})
        self.assertNotIn("Send email on your behalf", plain["html"])


if __name__ == "__main__":
    unittest.main()
