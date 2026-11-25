"""Polly's Gemini orchestrator: tool loop, server-enforced tools, guardrails, fallback.

Gemini is scripted here; every reply it "writes" goes through the same tool runner,
guardrails and payload code as production.
"""

import json
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from routes import polly as routes_polly  # noqa: E402
import services.polly as polly  # noqa: E402
import services.polly_brain as brain  # noqa: E402
import services.polly_tools as tools  # noqa: E402
from services.polly_fallback import run_fallback, template_reply  # noqa: E402
from services.polly_turn_state import TurnState  # noqa: E402

BRANDS = {
    4: {"id": 4, "name": "NordVPN", "slug": "nordvpn", "category": "tech"},
    9: {"id": 9, "name": "BALLBOYZ", "slug": "ballboyz", "category": "fashion"},
    47: {"id": 47, "name": "Secretlab", "slug": "secretlab", "category": "tech"},
    811: {"id": 811, "name": "Gucci", "slug": "gucci", "category": "beauty"},
}


def fake_lookup(conn, brand_id=None, brand_name=None):
    if brand_id:
        return dict(BRANDS.get(int(brand_id))) if int(brand_id) in BRANDS else None
    for row in BRANDS.values():
        if (brand_name or "").strip().lower() == row["name"].lower():
            return dict(row)
    return None


def make_state(**over):
    base = dict(
        creator_id=7,
        creator={"id": 7},
        first="Abdullahi",
        scrape={"primary_niche": "tech"},
        kit={"published": True},
        profile_context="Primary niche: tech",
        coach_moves=[],
        balance={"remaining": 2, "limit": 3, "is_unlimited": False, "used": 1},
        notes={},
        messages=[{"role": "user", "content": "hi"}],
        stored_messages=[],
        suggested=[],
        tracker_ctx={},
        early_fu={},
        career=None,
        user_text="hi",
        chip={},
        conn=MagicMock(closed=0),
    )
    base.update(over)
    return TurnState(**base)


def call(tool_name, **args):
    return {"candidates": [{"content": {"role": "model", "parts": [
        {"functionCall": {"name": tool_name, "args": args}},
    ]}}]}


def text(value):
    return {"candidates": [{"content": {"role": "model", "parts": [{"text": value}]}}]}


class _Patched(unittest.TestCase):
    """Routes helpers the tools use, stubbed so no DB or HTTP is touched."""

    def setUp(self):
        self.patches = [
            patch.object(routes_polly, "_lookup_published_brand", side_effect=fake_lookup),
            patch.object(routes_polly, "_hydrate_brand_cards", side_effect=lambda rows: [dict(r) for r in rows or []]),
            patch.object(routes_polly, "_lookup_similar_brands", return_value=[dict(BRANDS[47])]),
            patch.object(routes_polly, "_unlock_balance",
                         return_value={"remaining": 2, "limit": 3, "is_unlimited": False}),
            patch.object(routes_polly, "_record_polly_draft"),
            patch.object(routes_polly, "_log_polly_event"),
            patch.object(routes_polly, "_pipeline_proof", return_value=(0, 0)),
            patch.object(tools.tracker, "record_pitch_sent"),
            patch("pr_crm_routes.cold_spend_warning", return_value={}),
            patch.object(polly, "llm_available", return_value=True),
            patch.object(polly, "get_gemini_key", return_value="k"),
        ]
        self.mocks = [p.start() for p in self.patches]
        self.record_sent = self.mocks[7]

    def tearDown(self):
        for p in self.patches:
            p.stop()

    def run_brain(self, state, script):
        replies = list(script)
        seen = []

        def fake_call(_self, payload, sink=None):
            seen.append(payload)
            return replies.pop(0)

        with patch.object(brain._Gemini, "call", fake_call):
            out = brain.run_turn(state)
        return out, seen


def _pkg(name, bid):
    return 200, {
        "success": True,
        "brand_email": "pr@x.example",
        "package": {
            "brand": {"id": bid, "name": name},
            "pitches": {"growing": {"subject": f"{name} collab", "body_plain": f"Hi {name}, shipping to Lyon."}},
        },
    }


class OrchestratorLoopTests(_Patched):
    def test_gemini_searches_directory_before_answering(self):
        state = make_state(user_text="do you have Gucci?")
        out, seen = self.run_brain(state, [
            call("search_directory", name="Gucci"),
            text("Yes, **Gucci** is in the directory. Want me to draft your pitch?"),
        ])
        self.assertIn("Gucci", out)
        self.assertEqual([b["name"] for b in state.brands], ["Gucci"])
        self.assertEqual(state.tool_log[0]["name"], "search_directory")
        fn_response = seen[1]["contents"][-1]["parts"][0]["functionResponse"]
        self.assertTrue(fn_response["response"]["found"])
        self.assertEqual(seen[0]["toolConfig"]["functionCallingConfig"]["mode"], "AUTO")

    def test_last_round_forbids_more_tools(self):
        state = make_state()
        script = [call("search_directory", name="Gucci")] * brain.MAX_TOOL_ROUNDS + [text("Done.")]
        _, seen = self.run_brain(state, script)
        self.assertEqual(seen[-1]["toolConfig"]["functionCallingConfig"]["mode"], "NONE")

    def test_server_owned_reply_skips_further_model_calls(self):
        state = make_state(user_text="pitch Secretlab")
        with patch.object(routes_polly, "_invoke_generate_pr_package",
                          return_value=(402, {"paywall": True, "remaining": 0})):
            out, seen = self.run_brain(state, [call("draft_pitch", brand_name="Secretlab")])
        self.assertEqual(len(seen), 1)
        self.assertTrue(state.paywall)
        self.assertIn("Secretlab", out)
        self.assertEqual(state.notes["paywall_brand"]["name"], "Secretlab")
        self.assertTrue(any(c.get("brand_name") == "Secretlab" or "Secretlab" in (c.get("label") or "")
                            for c in state.task_chips))

    def test_unavailable_before_tools_raises_for_fallback(self):
        state = make_state()

        def down(_self, payload, sink=None):
            raise brain.BrainUnavailable("down")

        with patch.object(brain._Gemini, "call", down):
            with self.assertRaises(brain.BrainUnavailable):
                brain.run_turn(state)


def _ranking(cards, note="", level=0):
    from services.thanos import Ranking
    return Ranking(brands=[dict(c) for c in cards], level=level, note=note)


class SuggestUsesThanosTests(_Patched):
    """suggest_brands shows Thanos picks: no other pool, reach order or reply-tier sort."""
    NORD = {"id": 4, "name": "NordVPN", "slug": "nordvpn", "category": "tech", "roster_is_open": 0,
            "reply_band": "high", "reply_reason": "3 creators heard back", "thanos_level": 0}
    SECRET = {"id": 47, "name": "Secretlab", "slug": "secretlab", "category": "tech", "roster_is_open": 1,
              "reply_band": "medium", "reply_reason": "Recruiting creators right now", "thanos_level": 0}

    def test_cards_come_from_thanos_and_live_rosters_lead(self):
        state = make_state(notes={"shown_brand_ids": [90]})
        with patch("services.thanos.rank", return_value=_ranking([self.NORD, self.SECRET])) as rank:
            res = tools.run_tool(state, "suggest_brands", {"deal": "gifted"})
        kw = rank.call_args.kwargs
        self.assertEqual(kw["surface"], "polly")
        self.assertIn(90, kw["exclude_ids"])                       # shown brands stay out
        self.assertEqual([b["name"] for b in state.brands], ["Secretlab", "NordVPN"])
        self.assertEqual(state.brands[0]["source"], "recruiting")
        self.assertEqual(state.brands[1]["reply_band"], "high")
        self.assertEqual(res["live_rosters"], 1)
        self.assertTrue(res["cards_shown"])
        self.assertEqual(state.notes["shown_brand_ids"][-2:], [47, 4])

    def test_first_matches_use_their_surface_and_cap_at_three(self):
        cards = [dict(self.NORD, id=i, name=f"B{i}", roster_is_open=0) for i in range(1, 7)]
        state = make_state(chip={"id": "first_matches"})
        with patch("services.thanos.rank", return_value=_ranking(cards)) as rank:
            tools.run_tool(state, "suggest_brands", {"deal": "gifted"})
        self.assertEqual(rank.call_args.kwargs["surface"], "first_matches")
        self.assertEqual([b["id"] for b in state.brands], [1, 2, 3])

    def test_widened_ladder_is_said_honestly(self):
        state = make_state()
        with patch("services.thanos.rank", return_value=_ranking([self.NORD], note="close to your niche", level=1)):
            res = tools.run_tool(state, "suggest_brands", {})
        self.assertEqual(res["match_note"], "close to your niche")
        self.assertIn("close to your niche", res["note"])

    def test_already_shown_brands_come_back_oldest_first(self):
        state = make_state(notes={"shown_brand_ids": [47, 4]})
        with patch("services.thanos.rank", side_effect=[_ranking([]), _ranking([self.NORD, self.SECRET])]) as rank:
            res = tools.run_tool(state, "suggest_brands", {})
        self.assertNotIn(47, rank.call_args.kwargs["exclude_ids"])
        self.assertTrue(res["repeated"])
        self.assertEqual([b["name"] for b in state.brands], ["Secretlab", "NordVPN"])

    def test_pitched_by_name_and_pending_draft_stay_out(self):
        state = make_state(notes={"pitched_brand_names": ["NordVPN"], "pending_pitch": {"id": 47, "name": "Secretlab"}})
        glow = dict(self.NORD, id=90, name="Glow Recipe")
        with patch("services.thanos.rank", return_value=_ranking([self.NORD, self.SECRET, glow])) as rank:
            tools.run_tool(state, "suggest_brands", {})
        self.assertIn(47, rank.call_args.kwargs["exclude_ids"])
        self.assertEqual([b["name"] for b in state.brands], ["Glow Recipe"])

    def test_nothing_left_says_so(self):
        state = make_state()
        with patch("services.thanos.rank", return_value=_ranking([])):
            res = tools.run_tool(state, "suggest_brands", {})
        self.assertEqual(res["brands"], [])
        self.assertIn("never invent", res["note"])


class SimilarBrandsUseThanosOrderTests(unittest.TestCase):
    ROWS = [
        {"id": 1, "slug": "a", "name": "Keyboard Co", "category": "tech", "description": "keyboard maker"},
        {"id": 2, "slug": "b", "name": "Laptop Stands", "category": "tech", "description": "laptop risers"},
        {"id": 3, "slug": "c", "name": "Glow", "category": "skincare", "description": "serum"},
    ]

    def test_token_matches_in_thanos_order(self):
        scored = [{"id": 2, "reply_band": "high", "match_score": 70}, {"id": 1, "reply_band": "low"}]
        with patch.object(routes_polly, "_fetch_brands_by_tokens", return_value=[dict(r) for r in self.ROWS]), \
                patch.object(routes_polly, "_thanos_order", return_value=scored) as order:
            out = routes_polly._lookup_similar_brands("Dell laptop", creator_id=7)
        self.assertEqual(sorted(order.call_args[0][1]), [1, 2])     # only brands that matched the name
        self.assertEqual([b["id"] for b in out], [2, 1])
        self.assertEqual(out[0]["reply_band"], "high")


class ToolRuleTests(_Patched):
    def test_typed_name_beats_a_wrong_brand_id(self):
        state = make_state(user_text="yes Secretlab")
        with patch.object(routes_polly, "_invoke_generate_pr_package", return_value=_pkg("Secretlab", 47)):
            res = tools.run_tool(state, "draft_pitch", {"brand_name": "Secretlab", "brand_id": 9})
        self.assertEqual(res["status"], "drafted")
        self.assertEqual(state.pitch["brand_name"], "Secretlab")
        self.assertEqual(state.notes["pending_pitch"]["name"], "Secretlab")

    def test_unknown_brand_shows_similar_not_a_pitch(self):
        state = make_state()
        res = tools.run_tool(state, "draft_pitch", {"brand_name": "Zorblax"})
        self.assertEqual(res["status"], "not_in_directory")
        self.assertIsNone(state.pitch)
        self.assertEqual([b["name"] for b in state.brands], ["Secretlab"])

    def test_off_directory_brand_with_email_gets_a_pitch_to_that_inbox(self):
        state = make_state(user_text="pitch Shake Shack at pr@shakeshack.com")
        manual = {"subject": "Gifted collab", "body": "Hi Shake Shack,\n\nNo fee. Just product + shipping to Hayward, United States."}
        with patch.object(routes_polly, "_manual_pitch", return_value=manual) as built, \
                patch.object(routes_polly, "_invoke_generate_pr_package") as gen:
            res = tools.run_tool(state, "draft_pitch",
                                 {"brand_name": "Shake Shack", "to_email": " PR@ShakeShack.com "})
        gen.assert_not_called()
        built.assert_called_once()
        self.assertEqual(res["status"], "drafted")
        self.assertTrue(res["off_directory"])
        self.assertEqual(state.pitch["email"], "pr@shakeshack.com")
        self.assertTrue(state.pitch["mailto"].startswith("mailto:pr@shakeshack.com?"))
        self.assertIsNone(state.pitch["brand_id"])
        self.assertEqual(state.notes["pending_pitch"]["name"], "Shake Shack")

    def test_off_directory_brand_without_email_writes_nothing(self):
        state = make_state()
        with patch.object(routes_polly, "_manual_pitch") as built:
            res = tools.run_tool(state, "draft_pitch", {"brand_name": "Shake Shack"})
        built.assert_not_called()
        self.assertEqual(res["status"], "not_in_directory")
        self.assertIn("to_email", res["explain"])
        self.assertIsNone(state.pitch)

    def test_creator_inbox_overrides_the_directory_email(self):
        state = make_state()
        with patch.object(routes_polly, "_invoke_generate_pr_package", return_value=_pkg("Secretlab", 47)):
            res = tools.run_tool(state, "draft_pitch",
                                 {"brand_name": "Secretlab", "to_email": "social@secretlab.example"})
        self.assertEqual(res["status"], "drafted")
        self.assertEqual(state.pitch["email"], "social@secretlab.example")
        self.assertIn("mailto:social@secretlab.example", state.pitch["mailto"])
        self.assertEqual(res["directory_inbox"], "pr@x.example")
        self.assertEqual(state.pitch["brand_id"], 47)

    def test_bad_email_is_refused(self):
        state = make_state()
        res = tools.run_tool(state, "draft_pitch", {"brand_name": "Secretlab", "to_email": "social@"})
        self.assertEqual(res["status"], "invalid_email")
        self.assertIsNone(state.pitch)

    def test_sent_to_an_off_directory_brand_is_logged(self):
        state = make_state(user_text="I emailed Shake Shack")
        res = tools.run_tool(state, "log_pitch_sent", {"brand_name": "Shake Shack"})
        self.assertEqual(res["status"], "logged")
        self.assertIn("Shake Shack", state.notes["pitched_brand_names"])
        self.record_sent.assert_called_once()
        again = tools.run_tool(state, "log_pitch_sent", {"brand_name": "Shake Shack"})
        self.assertEqual(again["status"], "already_logged")

    def test_status_chip_is_not_saved_as_a_location(self):
        state = make_state()
        with patch.object(routes_polly, "_persist_shipping_location") as persist:
            res = tools.run_tool(state, "save_profile_facts", {"city": "They", "country": "replied"})
        persist.assert_not_called()
        self.assertNotIn("location", state.notes)
        self.assertIn("location_not_saved", res)

    def test_search_not_found_lists_similar(self):
        state = make_state()
        res = tools.run_tool(state, "search_directory", {"name": "Zorblax"})
        self.assertFalse(res["found"])
        self.assertEqual(res["similar_in_directory"][0]["name"], "Secretlab")

    def test_sent_after_locked_pitch_asks_instead_of_logging_stale_draft(self):
        state = make_state(
            user_text="I sent it",
            notes={"pending_pitch": {"id": 9, "name": "BALLBOYZ"},
                   "paywall_brand": {"id": 47, "name": "Secretlab"}},
            stored_messages=[{"role": "assistant", "content": "On Pro",
                              "locked_pitch": {"brand_name": "Secretlab", "lines": ["Hi"]}}],
        )
        res = tools.run_tool(state, "log_pitch_sent", {})
        self.assertEqual(res["status"], "latest_pitch_is_locked")
        self.assertEqual(res["unsent_draft"], "BALLBOYZ")
        self.record_sent.assert_not_called()
        self.assertNotIn("pitched_brand_names", state.notes)
        labels = [c.get("brand_name") for c in state.task_chips]
        self.assertIn("BALLBOYZ", labels)
        self.assertIn("Secretlab", labels)

    def test_sent_logs_the_draft_and_pro_chip_sells_the_paywall_brand(self):
        state = make_state(
            notes={"pending_pitch": {"id": 9, "name": "BALLBOYZ"},
                   "paywall_brand": {"id": 47, "name": "Secretlab"}},
            suggested=[dict(BRANDS[4])],
        )
        with patch.object(routes_polly, "_unlock_balance",
                          return_value={"remaining": 0, "limit": 3, "is_unlimited": False}):
            res = tools.run_tool(state, "log_pitch_sent", {})
        self.assertEqual(res["status"], "logged")
        self.record_sent.assert_called_once()
        self.assertIn("BALLBOYZ", state.notes["pitched_brand_names"])
        self.assertNotIn("pending_pitch", state.notes)
        self.assertTrue(state.sent_now)
        self.assertEqual(state.paywall_moment["brand_id"], 47)

    def test_sent_with_city_placeholder_is_held(self):
        state = make_state(
            notes={"pending_pitch": {"id": 9, "name": "BALLBOYZ"}},
            messages=[{"role": "assistant", "content": "Here", "pitch": {
                "brand_name": "BALLBOYZ", "brand_id": 9, "body": "shipping to [CITY, COUNTRY]."}},
                {"role": "user", "content": "done"}],
        )
        res = tools.run_tool(state, "log_pitch_sent", {})
        self.assertEqual(res["status"], "draft_still_has_city_placeholder")
        self.record_sent.assert_not_called()

    def test_location_fills_the_draft(self):
        state = make_state(messages=[{"role": "assistant", "content": "Here", "pitch": {
            "brand_name": "BALLBOYZ", "brand_id": 9, "body": "shipping to [CITY, COUNTRY]."}}])
        with patch.object(routes_polly, "_persist_shipping_location"):
            res = tools.run_tool(state, "save_profile_facts", {"city": "Lyon", "country": "France"})
        self.assertTrue(res["draft_city_filled"])
        self.assertIn("Lyon", state.pitch_update["body"])
        self.assertEqual(state.notes["location"], "Lyon, France")

    def test_bad_tool_input_is_reported_not_raised(self):
        state = make_state()
        self.assertFalse(tools.run_tool(state, "nope", {})["ok"])
        self.assertFalse(tools.run_tool(state, "log_brand_update", {"event": "danced"})["ok"])


class GuardrailTests(_Patched):
    def test_fake_send_claim_is_repaired(self):
        state = make_state(user_text="I sent it", notes={"pending_pitch": {"id": 9, "name": "BALLBOYZ"}})
        out, seen = self.run_brain(state, [
            text("Got it, I've logged your pitch to **BALLBOYZ** on your Timeline."),
            text("Which brand did you email? Tap **I sent it** on that pitch."),
        ])
        self.assertEqual(len(seen), 2)
        self.assertNotIn("logged", out.lower())
        self.assertIn("SERVER CHECK", seen[1]["contents"][-1]["parts"][0]["text"])

    def test_brand_not_in_results_falls_back_to_template(self):
        state = make_state(suggested=[dict(BRANDS[4])])
        out, _ = self.run_brain(state, [
            call("suggest_brands"),
            text("I'd start with **Secretlab**, honestly."),
            text("Still **Secretlab** for me."),
        ])
        self.assertNotIn("Secretlab", out)

    def test_named_brand_from_results_passes(self):
        state = make_state()
        problems = brain.reply_problems(state, "Nothing to name.")
        self.assertEqual(problems, [])
        state.brands = [dict(BRANDS[47])]
        self.assertEqual(brain.reply_problems(state, "Pitch **Secretlab** next."), [])
        self.assertTrue(brain.reply_problems(state, "Email them at boss@fake.example"))
        state.user_text = "do you have Zorblax Cosmetics?"
        self.assertEqual(brain.reply_problems(state, "No, **Zorblax Cosmetics** isn't in the directory."), [])

    def test_credit_line_comes_from_server_after_send(self):
        state = make_state(notes={"pending_pitch": {"id": 9, "name": "BALLBOYZ"}})
        out, _ = self.run_brain(state, [
            call("log_pitch_sent"),
            text("Logged **BALLBOYZ**. You have 2 free pitches left this month."),
        ])
        self.assertNotIn("2 free pitches left", out)
        self.assertTrue(state.say_tail)


class FallbackTests(_Patched):
    def test_directory_question_without_gemini(self):
        state = make_state(user_text="do you have Gucci?")
        out = run_fallback(state)
        self.assertEqual(state.tool_log[0]["name"], "search_directory")
        self.assertIn("Gucci", out)

    def test_sent_without_gemini_logs_draft(self):
        state = make_state(user_text="I sent it", notes={"pending_pitch": {"id": 9, "name": "BALLBOYZ"}})
        out = run_fallback(state)
        self.assertIn("BALLBOYZ", out)
        self.record_sent.assert_called_once()

    def test_template_with_no_tools(self):
        self.assertTrue(template_reply(make_state()))


class StreamRoundTests(unittest.TestCase):
    def _resp(self, chunks):
        lines = ["data: " + json.dumps({"candidates": [{"content": {"parts": [p]}}]}) for p in chunks]

        class FakeResp:
            status_code = 200

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def iter_lines(self, decode_unicode=True):
                if getattr(self, "encoding", None) != "utf-8":
                    raise AssertionError("Gemini stream must be decoded as utf-8")
                return iter(lines)

        return FakeResp()

    def test_text_streams_as_deltas(self):
        events = []
        with patch.object(brain.requests, "post", return_value=self._resp([{"text": "Hey "}, {"text": "there"}])):
            data = brain._stream_round("m", {}, {}, events.append)
        self.assertEqual("".join(e["text"] for e in events if e["type"] == "delta"), "Hey there")
        self.assertEqual(brain._text(brain._parts(data)), "Hey there")

    def test_function_call_parts_are_kept_whole(self):
        part = {"functionCall": {"name": "suggest_brands", "args": {}}, "thoughtSignature": "sig"}
        with patch.object(brain.requests, "post", return_value=self._resp([part])):
            data = brain._stream_round("m", {}, {}, lambda e: None)
        self.assertEqual(brain._parts(data), [part])


class ChatRouteTests(unittest.TestCase):
    def test_sent_button_runs_tool_then_brain_and_keeps_payload_shape(self):
        from flask import Flask

        app = Flask(__name__)
        app.secret_key = "t"
        app.register_blueprint(routes_polly.polly_bp)
        conn = MagicMock(closed=0)
        stored = {"messages": [], "notes": {"pending_pitch": {"id": 9, "name": "BALLBOYZ"}}}

        def fake_brain(state, discovery_hint="", prior_tools=None):
            self.assertEqual(prior_tools[0]["tool"], "log_pitch_sent")
            return "Logged **BALLBOYZ**. I'll check on day 4."

        with patch.multiple(
            routes_polly,
            _creator_auth=MagicMock(return_value=(7, conn, {"id": 7})),
            _load_scrape=MagicMock(return_value={}),
            load_kit_snapshot=MagicMock(return_value={}),
            build_profile_context=MagicMock(return_value=""),
            kit_context=MagicMock(return_value=""),
            _load_pitch_coaching=MagicMock(return_value=[]),
            _unlock_balance=MagicMock(return_value={"remaining": 2, "limit": 3, "is_unlimited": False}),
            load_thread=MagicMock(return_value=stored),
            career_snapshot=MagicMock(return_value=None),
            early_followup_brands=MagicMock(return_value={}),
            load_onboarding_survey=MagicMock(return_value={}),
            load_creator_context=MagicMock(return_value={}),
            save_thread=MagicMock(),
            _log_polly_event=MagicMock(),
            run_turn=fake_brain,
        ), patch.object(tools.tracker, "record_pitch_sent") as sent, \
                patch.object(routes_polly, "_hydrate_brand_cards", side_effect=lambda rows: list(rows or [])):
            resp = app.test_client().post("/api/polly/chat", json={
                "messages": [{"role": "user", "content": "I sent it"}],
                "action": "chat", "chip_id": "i_sent_it", "brand_id": 9, "brand_name": "BALLBOYZ",
            })
        body = resp.get_json()
        self.assertEqual(resp.status_code, 200, body)
        sent.assert_called_once()
        for key in ("message", "brands", "pitch", "task_chips", "starters", "credits", "discovery", "paywall"):
            self.assertIn(key, body)
        self.assertIn("BALLBOYZ", body["message"])


if __name__ == "__main__":
    unittest.main()
