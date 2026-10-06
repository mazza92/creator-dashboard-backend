"""Gifted-first defaults, preferences, state opener, email alerts, and Pro conversion copy."""

import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch
from urllib.parse import parse_qs, urlparse

from services.polly import is_cant_afford, reads_like_sentence, reset_label
from services.polly_alerts import chip_link, email_nudges, kit_view_email, nudge_email
from services.polly_discovery import starters_for
from services.polly_opener import looks_like_place, single_city_reply, state_opener
from services.polly_persona import (
    persona_cant_afford,
    persona_paywall_say,
    persona_pref_ack,
    scrub_polly_voice,
)
from services.polly_prefs import (
    brand_blocked,
    filter_brands_by_prefs,
    gifted_only,
    merge_prefs,
    parse_preferences,
)


def _iso(delta=timedelta(0)):
    return (datetime.now(timezone.utc) - delta).isoformat()


class PreferenceTests(unittest.TestCase):
    def test_no_ugc_means_gifted_only(self):
        for text in (
            "I don't want UGC yet",
            "i didnt want ugc, just gifted",
            "gifted only please",
            "not ready for paid yet",
        ):
            self.assertTrue(parse_preferences(text).get("gifted_only"), text)

    def test_complaints_are_not_preferences(self):
        for text in (
            "I didn't get paid for the last one",
            "that's not my target audience",
            "I'm not tech savvy",
            "Rare Beauty",
        ):
            self.assertNotIn("gifted_only", parse_preferences(text), text)
            self.assertNotIn("avoid_retailers", parse_preferences(text), text)
            self.assertNotIn("avoid_categories", parse_preferences(text), text)

    def test_categories_and_retailers(self):
        patch_ = parse_preferences("no nails or fashion, and nothing sold at Walmart or Shoppers")
        self.assertIn("nails", patch_["avoid_categories"])
        self.assertIn("fashion", patch_["avoid_categories"])
        self.assertIn("walmart", patch_["avoid_retailers"])
        self.assertIn("shoppers", patch_["avoid_retailers"])

    def test_wanting_paid_clears_gifted_only(self):
        self.assertIs(parse_preferences("I want paid work now")["gifted_only"], False)

    def test_merge_and_filter(self):
        notes = merge_prefs({"goal_30d": "paid UGC"}, {"gifted_only": True, "avoid_categories": ["nails"]})
        self.assertTrue(gifted_only(notes))
        self.assertEqual(notes["deal_intent"], "gifted")
        self.assertNotIn("goal_30d", notes)
        notes = merge_prefs(notes, {"avoid_retailers": ["walmart"]})
        brands = [
            {"id": 1, "name": "Olive & June", "category": "Nails"},
            {"id": 2, "name": "Glow Co", "category": "Skincare", "description": "Sold at Walmart"},
            {"id": 3, "name": "Rare Beauty", "category": "Makeup"},
        ]
        self.assertEqual([b["id"] for b in filter_brands_by_prefs(brands, notes)], [3])
        self.assertFalse(brand_blocked(brands[2], notes))
        self.assertEqual(filter_brands_by_prefs(brands, {}), brands)

    def test_pref_ack(self):
        self.assertIn("gifted PR only", persona_pref_ack({"gifted_only": True}))
        self.assertIn("no nails", persona_pref_ack({"avoid_categories": ["nails"]}))
        self.assertEqual(persona_pref_ack({}), "")


class GiftedFirstStarterTests(unittest.TestCase):
    def test_gifted_only_hides_paid_chip(self):
        ids = [c["id"] for c in starters_for({"prefs": {"gifted_only": True}})]
        self.assertEqual(ids[:2], ["line_up", "gifted_lists"])
        self.assertNotIn("paid_ugc", ids)

    def test_established_creator_keeps_paid_first(self):
        chips = starters_for({"polly_track": "established", "discovery_skipped_at": _iso()})
        self.assertEqual(chips[0]["id"], "paid_ugc")


class BrandLookupGateTests(unittest.TestCase):
    def test_sentences_are_not_brands(self):
        for text in (
            "find me paid collaborations",
            "I want paid opportunities now",
            "no I meant the one I pitched yesterday",
            "can you change my city to Lyon",
            "my number is 514 555 0199",
        ):
            self.assertTrue(reads_like_sentence(text), text)

    def test_brand_names_pass(self):
        for text in ("Rare Beauty", "Not Your Mother's", "Kiss My Face", "Glossier", "The Ordinary"):
            self.assertFalse(reads_like_sentence(text), text)


class ProConversionTests(unittest.TestCase):
    def test_cant_afford_detection(self):
        for text in ("I can't afford Pro", "pro is too expensive for me", "I don't have money for that"):
            self.assertTrue(is_cant_afford(text), text)
        self.assertFalse(is_cant_afford("how much is Pro?"))

    def test_reset_label(self):
        self.assertEqual(reset_label({"reset_at": "2026-10-01T00:00:00+00:00"}), "Oct 1")
        self.assertRegex(reset_label({}), r"^[A-Z][a-z]{2} 1$")

    def test_cant_afford_gives_free_path_with_reset(self):
        say = persona_cant_afford("Oct 1", follow_brand="Glow Co", kit_live=False)
        self.assertIn("Glow Co", say)
        self.assertIn("Publish My Kit", say)
        self.assertIn("Oct 1", say)
        self.assertIn("7 days", say)

    def test_paywall_names_brand_and_shows_proof_without_reset(self):
        say = persona_paywall_say("Glow Co", kit_views=2)
        self.assertIn("Glow Co", say)
        self.assertIn("$19/mo", say)
        self.assertLess(say.index("autopilot"), say.index("unlimited roster applications"))
        self.assertIn("**2** brands opened your kit", say)
        self.assertNotIn("reset", say.lower())

    def test_scrub_removes_nonexistent_buttons(self):
        out = scrub_polly_voice("Here you go.\n\nTap Save Draft to keep it in your Drafts tab.")
        self.assertNotIn("Save Draft", out)
        self.assertIn("Here you go.", out)


class StateOpenerTests(unittest.TestCase):
    def test_selected_beats_everything(self):
        out = state_opener(
            "Ana",
            tracker={"recent_timeline": [{"event_type": "portfolio_viewed", "brand_name": "Glow", "occurred_at": _iso()}]},
            applications=[{"brand_id": 5, "brand_name": "Sol", "status": "ships"}],
        )
        self.assertEqual(out["state"], "selected")
        self.assertIn("Sol", out["greeting"])

    def test_recent_kit_view_offers_followup_and_replied(self):
        out = state_opener("Ana", tracker={"recent_timeline": [
            {"event_type": "portfolio_viewed", "brand_id": 9, "brand_name": "Glow", "occurred_at": _iso(timedelta(days=1))},
        ]})
        self.assertEqual(out["state"], "kit_view")
        ids = [c["id"] for c in out["starters"]]
        self.assertEqual(ids[:2], ["draft_followup", "checkin_replied"])

    def test_old_kit_view_ignored(self):
        out = state_opener("Ana", tracker={"recent_timeline": [
            {"event_type": "portfolio_viewed", "brand_name": "Glow", "occurred_at": _iso(timedelta(days=12))},
        ]})
        self.assertIsNone(out)

    def test_unsent_draft_reshows_pitch(self):
        out = state_opener("Ana", notes={"pending_pitch": {"id": 3, "name": "Naturium"}})
        self.assertEqual(out["state"], "unsent_draft")
        self.assertEqual(out["starters"][0]["action"], "generate_pitch")
        self.assertEqual(out["starters"][0]["brand_id"], 3)

    def test_review_is_not_selected(self):
        out = state_opener("Ana", applications=[{"brand_id": 1, "brand_name": "Sol", "status": "review"}])
        self.assertEqual(out["state"], "applications")
        self.assertIn("still in review", out["greeting"])
        self.assertNotIn("picked you", out["greeting"])

    def test_kit_not_live(self):
        self.assertEqual(state_opener("Ana", kit={"published": False})["state"], "kit_not_live")
        self.assertIsNone(state_opener("Ana", kit={"published": True}))

    def test_location_parsing(self):
        self.assertTrue(looks_like_place("Lyon, France"))
        self.assertFalse(looks_like_place("show me brands"))
        self.assertFalse(looks_like_place("what city?"))
        self.assertEqual(single_city_reply("lyon", "France"), {"city": "Lyon", "country": "France"})
        self.assertIsNone(single_city_reply("lyon", ""))


class AlertEmailTests(unittest.TestCase):
    def test_chip_link(self):
        url = chip_link("checkin_replied", task_id=7, brand_id=9, brand_name="Glow Co", kind="follow_up_d4")
        q = parse_qs(urlparse(url).query)
        self.assertTrue(urlparse(url).path.endswith("/creator/dashboard/for-you"))
        self.assertEqual(q["chip"], ["checkin_replied"])
        self.assertEqual(q["task_id"], ["7"])
        self.assertEqual(q["brand_name"], ["Glow Co"])
        self.assertEqual(q["utm_campaign"], ["follow_up_d4"])

    def test_kit_view_email_pro_names_brand(self):
        mail = kit_view_email("Ana", "Glow Co", 9, is_pro=True)
        self.assertIn("Glow Co", mail["subject"])
        self.assertIn("They already replied", mail["html"])

    def test_kit_view_email_free_hides_brand(self):
        mail = kit_view_email("Ana", "Glow Co", 9, is_pro=False, category="Accessories")
        self.assertEqual(mail["subject"], "An accessories brand just opened your kit")
        self.assertNotIn("Glow", mail["html"])
        self.assertEqual(kit_view_email("Ana", "Glow Co", 9, False)["subject"], "A brand just opened your kit")

    def test_nudge_email_buttons_are_deep_links(self):
        mail = nudge_email("Ana", {
            "key": "follow_up_d4", "brand_name": "Glow Co", "brand_id": 9, "task_id": 7,
            "message": "Day 4 on **Glow Co**.",
            "chips": [
                {"id": "checkin_replied", "label": "They replied"},
                {"id": "unknown_chip", "label": "Nope"},
            ],
        })
        self.assertIn("Glow Co", mail["subject"])
        self.assertIn("<strong>Glow Co</strong>", mail["html"])
        self.assertIn("chip=checkin_replied", mail["html"])
        self.assertNotIn("Nope", mail["html"])

    def test_email_nudges_sends_and_skips_unsubscribed(self):
        who = {"creator_id": 1, "user_id": 11, "email": "a@x.co", "first": "Ana", "is_pro": False}
        sent = []

        def fake_send(to, subject, html, unsub, kind):
            sent.append((to, subject, kind))
            return {"success": True, "message_id": "m1"}

        delivered = [
            {"creator_id": 1, "nudge": {"key": "follow_up_d4", "brand_name": "Glow Co", "message": "Hi", "chips": []}},
            {"creator_id": 2, "nudge": {"key": "follow_up_d4", "brand_name": "Sol", "message": "Hi", "chips": []}},
        ]
        with patch("services.polly_alerts.ensure_alert_tables"), \
             patch("services.polly_alerts._recently_emailed", return_value=False), \
             patch("services.polly_alerts._recipient", side_effect=lambda conn, cid: who if cid == 1 else None), \
             patch("services.polly_alerts._unsubscribe_url", return_value="https://u"), \
             patch("services.polly_alerts._log_email") as log:
            out = email_nudges(MagicMock(), delivered, send_fn=fake_send)
        self.assertEqual(len(out), 1)
        self.assertEqual(sent[0][0], "a@x.co")
        self.assertEqual(sent[0][2], "follow_up_d4")
        self.assertTrue(out[0]["ok"])
        log.assert_called_once()


class SurveyFirstMessageTests(unittest.TestCase):
    SURVEY = {"segment": "just_starting", "intent": ["gifted_pr"], "pain": ["finding_brands"]}

    def test_seed_from_survey_skips_discovery(self):
        from services.polly_discovery import discovery_complete, seed_from_survey
        notes = seed_from_survey({}, self.SURVEY, {"primary_niche": "skincare"})
        self.assertTrue(discovery_complete(notes))
        self.assertTrue(notes.get("survey_seeded_at"))
        self.assertEqual(notes["survey"]["segment"], "just_starting")
        again = seed_from_survey(dict(notes, stage="changed"), {"segment": "growing"}, {})
        self.assertEqual(again["stage"], "changed")

    def test_first_matches_uses_survey_and_asks_nothing(self):
        from services.polly_persona import persona_first_matches
        brands = [{"name": "Glow Co"}, {"name": "Byoma"}, {"name": "Tower 28"}]
        say = persona_first_matches("Ava", self.SURVEY, brands, niche="skincare", live=True)
        self.assertTrue(say.startswith("Hey Ava"))
        self.assertIn("You told me you're just starting", say)
        self.assertIn("who to pitch", say)
        self.assertIn("picking creators in skincare this week", say)
        self.assertNotIn("?", say)

    def test_paywall_preview_is_two_lines(self):
        from services.polly_persona import paywall_preview_lines
        lines = paywall_preview_lines("Glow Co", "Ava", "asmr")
        self.assertEqual(len(lines), 2)
        self.assertEqual(lines[0], "Hi Glow Co team,")
        self.assertIn("an asmr creator", lines[1])

    def test_intro_email_links_into_polly(self):
        from services.polly_alerts import intro_email
        mail = intro_email("Ava", self.SURVEY)
        self.assertIn("3 gifted brands", mail["subject"])
        self.assertIn("just starting", mail["html"])
        self.assertIn("/creator/dashboard/for-you?", mail["html"])
        self.assertIn("polly_intro", mail["html"])


if __name__ == "__main__":
    unittest.main()
