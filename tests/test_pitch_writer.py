import json
import unittest
from unittest import mock

import services.pitch_writer as pw
from services.polly_autopilot import add_draft  # noqa: F401  (import check)

BASE = {
    "subject": "1 post + 2 UGC files for a PR/gifting sample · gifted trial",
    "body": ("Hi Saltwell,\n\nI create skincare content on TikTok for 8.3K followers with 0.4% engagement.\n\n"
             "No fee. Just product + shipping to Austin, United States.\n\n"
             "Recent work: https://newcollab.co/kit/maya?ref=abc123\n\nWorth a look?\n\nmaya.makes"),
    "product_name": "Sea Mineral Cleanser", "niche": "skincare", "platform": "TikTok",
    "followers": "8.3K", "engagement": "0.4%", "email": "partnerships@saltwell.com",
}
BRAND = {"id": 9, "brand_name": "Saltwell", "hero_product": "Sea Mineral Cleanser", "category": "skincare"}
STATS = {"avg_views": "14.8K", "avg_views_n": 14800,
         "top_posts": ["https://www.tiktok.com/@maya/video/1", "https://www.tiktok.com/@maya/video/2"]}


def fake_llm(opener="I have an idea for Sea Mineral Cleanser: a 15-second texture and first-wash video in natural light.",
             ready=True):
    calls = []

    def llm(system, user, max_tokens=300):
        calls.append(system)
        if system == pw.REVIEW_SYSTEM:
            return {"send_ready": ready, "issues": [] if ready else ["confusing"]}
        if system == pw.STYLE_SYSTEM:
            return {"rules": ["Casual, first person", "Short sentences"], "summary": "casual and short"}
        return {"opener": opener}
    llm.calls = calls
    return llm


class FinalizeTests(unittest.TestCase):
    def run_it(self, llm, notes=None, pitch=None):
        return pw.finalize_pitch(dict(pitch or BASE), brand=BRAND, notes=notes or {}, first_name="maya chen",
                                 stats=STATS, seed="7:9", llm=llm)

    def test_short_brand_specific_send_ready(self):
        out = self.run_it(fake_llm())
        body = out["body"]
        self.assertTrue(body.startswith("Hi Saltwell team,"))
        self.assertIn("Sea Mineral Cleanser", body)
        self.assertIn("average 14.8K views", body)
        self.assertNotIn("0.4%", body)                     # weak engagement hidden
        self.assertIn("No fee. Just product + shipping to Austin, United States.", body)  # ask kept
        self.assertIn("https://newcollab.co/kit/maya?ref=abc123", body)                   # tracked kit kept
        self.assertIn("https://www.tiktok.com/@maya/video/1", body)
        self.assertRegex(body, r"(?m)^Maya$")                                              # real first name
        self.assertLessEqual(pw.words(body), pw.MAX_WORDS)
        self.assertNotIn("gifted trial", out["subject"])
        self.assertLessEqual(len(out["subject"].split()), pw.MAX_SUBJECT_WORDS)
        self.assertEqual(out["pitch_meta"]["writer"], "v2")
        self.assertEqual(out["pitch_meta"]["source"], "gemini")
        self.assertIn("mailto:partnerships@saltwell.com", out["mailto"])

    def test_invented_numbers_fall_back_to_template(self):
        out = self.run_it(fake_llm(opener="Sea Mineral Cleanser would crush it: my last video got 2M views."))
        self.assertEqual(out["pitch_meta"]["source"], "template")
        self.assertNotIn("2M", out["body"])
        self.assertIn("Sea Mineral Cleanser", out["body"])

    def test_review_rejects_then_template(self):
        out = self.run_it(fake_llm(ready=False))
        self.assertEqual(out["pitch_meta"]["source"], "template")

    def test_no_gemini_still_sends_template(self):
        out = self.run_it(lambda *a, **k: {})
        self.assertEqual(out["pitch_meta"]["source"], "template")
        self.assertLessEqual(pw.words(out["body"]), pw.MAX_WORDS)

    def test_tone_and_style_rules_reach_gemini(self):
        seen = {}

        def llm(system, user, max_tokens=300):
            if system == pw.OPENER_SYSTEM:
                seen.update(json.loads(user))
                return {"opener": "Sea Mineral Cleanser deserves a slow-mo lather shot, so I'd film that first."}
            return {"send_ready": True}
        notes = {"pitch_profile": {"tone": "warm", "voice": "sample", "style_rules": ["Casual, first person"]}}
        out = self.run_it(llm, notes=notes)
        self.assertEqual(seen["tone"], "Warm & relatable")
        self.assertEqual(seen["style_rules"], ["Casual, first person"])
        self.assertEqual(out["pitch_meta"]["tone"], "warm")
        self.assertIn("Would love to make this for you.", out["body"])

    def test_custom_template_untouched(self):
        notes = {"prefs": {"custom_template": "Hi {brand}, ..."}}
        out = self.run_it(fake_llm(), notes=notes)
        self.assertEqual(out["body"], BASE["body"])

    def test_no_ask_keeps_original(self):
        pitch = dict(BASE, body=BASE["body"].replace("No fee. Just product + shipping to Austin, United States.\n\n", ""))
        out = self.run_it(fake_llm(), pitch=pitch)
        self.assertEqual(out["body"], pitch["body"])

    def test_engagement_shown_only_when_strong(self):
        facts = {"lead_metric": "engagement", "engagement": "4.2%", "niche": "skincare"}
        self.assertIn("4.2% engagement", pw.proof_line(facts))
        self.assertEqual(pw.proof_line({**facts, "engagement": "1.1%"}), "")

    def test_paid_ask_kept(self):
        pitch = dict(BASE, body=BASE["body"].replace(
            "No fee. Just product + shipping to Austin, United States.",
            "Rate: $150 for 1 organic post + 2 UGC files (6-month paid usage)."))
        out = self.run_it(fake_llm(), pitch=pitch)
        self.assertIn("Rate: $150", out["body"])


class FollowupTests(unittest.TestCase):
    def test_day4_new_idea_day10_smaller_ask(self):
        meta = {"writer": "v2", "product": "Sea Mineral Cleanser", "ideas": ["idea one", "a night routine video"]}
        d4 = pw.compose_followup_v2("Maya", "Saltwell", "Video idea for Sea Mineral Cleanser", 1, meta=meta)
        self.assertIn("a night routine video for Sea Mineral Cleanser", d4["body"])
        self.assertTrue(d4["subject"].startswith("Re: "))
        d10 = pw.compose_followup_v2("Maya", "Saltwell", "x", 2, meta=meta)
        self.assertIn("raw UGC clips", d10["body"])
        paid = pw.compose_followup_v2("Maya", "Saltwell", "x", 2, meta=meta, paid=True)
        self.assertIn("test video", paid["body"])
        for mail in (d4, d10, paid):
            self.assertLessEqual(pw.words(mail["body"]), pw.MAX_FOLLOWUP_WORDS)


class StyleTests(unittest.TestCase):
    def test_sample_to_rules(self):
        out = pw.style_from_sample("honestly obsessed with how this serum layers, i've tried like 10 of these and this "
                                   "is the only one that doesn't pill under my spf", llm=fake_llm())
        self.assertTrue(out["ok"])
        self.assertEqual(out["rules"][0], "Casual, first person")

    def test_too_short(self):
        self.assertFalse(pw.style_from_sample("love it", llm=fake_llm())["ok"])


if __name__ == "__main__":
    unittest.main()


class GeminiCallTests(unittest.TestCase):
    def test_pitch_calls_turn_thinking_off_with_room_for_json(self):
        from unittest import mock
        import services.pitch_writer as pw
        with mock.patch("services.brand_pipeline._gemini_json", return_value={"opener": "x"}) as call:
            pw.gemini_json("sys", "user", max_tokens=200)
        self.assertEqual(call.call_args.kwargs["thinking_budget"], 0)
        self.assertGreaterEqual(call.call_args.kwargs["max_tokens"], 600)

    def test_thinking_config_sent_to_gemini(self):
        from unittest import mock
        import services.brand_pipeline as bp
        resp = mock.MagicMock(status_code=200)
        resp.json.return_value = {"candidates": [{"content": {"parts": [{"text": '{"opener": "hi"}'}]}}]}
        with mock.patch("services.polly.get_gemini_key", return_value="k"), \
                mock.patch("services.polly.llm_disabled", return_value=False), \
                mock.patch("services.polly.get_polly_model", return_value="gemini-2.5-flash"), \
                mock.patch("requests.post", return_value=resp) as post:
            out = bp._gemini_json("s", "u", max_tokens=600, thinking_budget=0)
        self.assertEqual(out, {"opener": "hi"})
        self.assertEqual(post.call_args.kwargs["json"]["generationConfig"]["thinkingConfig"], {"thinkingBudget": 0})


class AskLineTests(unittest.TestCase):
    def test_capitals_city_softened(self):
        from services.pitch_writer import find_ask
        self.assertEqual(find_ask("x\nNo fee. Just product + shipping to NEW YORK, United States.\ny"),
                         "No fee. Just product + shipping to New York, United States.")
        self.assertEqual(find_ask("No fee. Just product + shipping to Austin, USA."),
                         "No fee. Just product + shipping to Austin, USA.")


class NameCleanupTests(unittest.TestCase):
    def test_brand_region_tag_dropped(self):
        from services.pitch_writer import clean_brand
        self.assertEqual(clean_brand("Nutrisense (US)"), "Nutrisense")
        self.assertEqual(clean_brand("burnd"), "burnd")

    def test_category_reads_as_your(self):
        from services.pitch_writer import clean_product, subject_options
        self.assertEqual(clean_product("Lighting fixtures and home hardware"), "your lighting fixtures and home hardware")
        self.assertEqual(clean_product("Sea Mineral Cleanser"), "Sea Mineral Cleanser")
        self.assertEqual(clean_product("Salt & Stone"), "Salt & Stone")
        self.assertEqual(clean_product(""), "your products")
        self.assertEqual([o["id"] for o in subject_options("Schoolhouse", "your products", "Maya")], ["ugc_for_brand"])

    def test_review_prompt_only_blocks_real_problems(self):
        from services.pitch_writer import REVIEW_SYSTEM
        self.assertIn("do NOT judge style", REVIEW_SYSTEM)


class ProofAndAskTests(unittest.TestCase):
    def test_tiny_view_counts_are_left_out(self):
        from services.pitch_writer import proof_line
        self.assertEqual(proof_line({"avg_views": "256", "avg_views_n": 256}), "")
        self.assertIn("14.8K", proof_line({"avg_views": "14.8K", "avg_views_n": 14800}))
        self.assertEqual(proof_line({"followers": "202"}), "")

    def test_ask_found_mid_paragraph(self):
        from services.pitch_writer import find_ask
        body = "I'd love to make a post for 3 months. No fee. Just product + shipping to NEW YORK, United States.\n\nThanks"
        self.assertEqual(find_ask(body), "No fee. Just product + shipping to New York, United States.")
        self.assertEqual(find_ask("x\nRate: $150 per video\ny"), "Rate: $150 per video")


class RosterReminderPauseTests(unittest.TestCase):
    def test_only_first_nudge_can_go_out(self):
        from services import roster_waiting_email as rw
        self.assertEqual(rw.PAUSED_TRIGGERS, frozenset({2, 3, 4}))
        with mock.patch.object(rw, "passes_caps", return_value=(True, "")):
            with mock.patch.object(rw, "_choose_trigger", return_value=rw.TriggerChoice(3, "weekly_reminder")):
                self.assertIsNone(rw.choose_trigger(object()))
            with mock.patch.object(rw, "_choose_trigger", return_value=rw.TriggerChoice(1, "first_nudge")):
                self.assertEqual(rw.choose_trigger(object()).number, 1)
