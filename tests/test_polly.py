import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from services.polly import (
    POLLY_BCC,
    build_mailto,
    build_profile_context,
    classify_intent_heuristic,
    flatten_for_you,
    pitch_from_package_response,
    profile_aware_chat,
    public_profile_summary,
    resolve_brand,
    sanitize_brand_card,
    unpack_view_result,
)


class ProfileContextTests(unittest.TestCase):
    def test_uses_scrape_followers_only(self):
        text = build_profile_context(
            {"handle": "maya", "follower_count": 12000, "primary_niche": "skincare"},
            {"followers_count": 999999},
        )
        self.assertIn("12000", text)
        self.assertNotIn("999999", text)

    def test_omits_missing_followers(self):
        text = build_profile_context({"handle": "maya", "primary_niche": "beauty"})
        self.assertNotIn("Followers", text)

    def test_omits_creator_email(self):
        text = build_profile_context(
            {"handle": "maya", "collab_email_extracted": "secret@example.com", "raw_bio": "nyc beauty"},
        )
        self.assertNotIn("secret@example.com", text)

    def test_public_summary_has_no_email(self):
        summary = public_profile_summary(
            {"handle": "maya", "collab_email_extracted": "secret@example.com", "follower_count": 0},
            {"first_name": "Maya", "id": 65},
        )
        self.assertEqual(summary["handle"], "maya")
        self.assertIsNone(summary["follower_count"])
        self.assertEqual(summary["creator_id"], 65)
        dumped = str(summary)
        self.assertNotIn("secret@", dumped)


class BrandPoolTests(unittest.TestCase):
    def test_sanitize_drops_contact_email(self):
        card = sanitize_brand_card({
            "id": 7,
            "name": "Glow Co",
            "slug": "glow-co",
            "contact_email": "pr@glow.example",
            "category": "beauty",
        })
        self.assertEqual(card["id"], 7)
        self.assertNotIn("contact_email", card)

    def test_flatten_prefers_matched_and_caps(self):
        payload = {
            "matched": [{"id": 1, "name": "A"}, {"id": 2, "name": "B"}],
            "hot": [{"id": 1, "name": "A"}, {"id": 3, "name": "C"}],
            "newest": [{"id": i, "name": str(i)} for i in range(4, 20)],
        }
        brands = flatten_for_you(payload, limit=6)
        ids = [b["id"] for b in brands]
        self.assertEqual(ids[0], 1)
        self.assertEqual(len(ids), 6)
        self.assertEqual(len(set(ids)), 6)

    def test_flatten_prefers_open_campaigns(self):
        payload = {
            "matched": [{"id": 1, "name": "Pool Fit", "category": "skincare", "match_score": 95, "description": "A generic pool match."}],
            "recruiting": [{
                "id": 2,
                "name": "Live Roster",
                "category": "skincare",
                "match_score": 40,
                "roster_is_open": 1,
                "description": "Plant-first body care made in small batches.",
            }],
        }
        brands = flatten_for_you(payload, limit=3, scrape={"primary_niche": "skincare"})
        self.assertEqual(brands[0]["id"], 2)
        self.assertEqual(brands[0]["why"], "Plant-first body care made in small batches.")
        self.assertNotIn("already sit", (brands[0].get("why") or "").lower())

    def test_card_blurb_is_description_not_overlap_why(self):
        payload = {
            "matched": [{
                "id": 1,
                "name": "Wholesome Hippy",
                "category": "skincare",
                "description": "Small-batch plant skincare from Portland.",
                "why": "Your skincare posts already sit in beauty, free, skincare.",
            }],
        }
        brands = flatten_for_you(payload, limit=1)
        self.assertEqual(brands[0]["why"], "Small-batch plant skincare from Portland.")
        self.assertNotIn("already sit", brands[0]["why"])

    def test_flatten_drops_mens_brand_for_lifestyle_scrape(self):
        payload = {
            "matched": [
                {
                    "id": 1,
                    "name": "Benji Mens",
                    "category": "skincare",
                    "match_score": 82,
                    "description": "We're looking for men 35-55 to talk about our face wash & moisturizer.",
                },
                {
                    "id": 2,
                    "name": "Beauty Pie",
                    "category": "beauty",
                    "match_score": 85,
                    "description": "Treat yourself better. Join hundreds of thousands of beauty insiders.",
                },
                {
                    "id": 3,
                    "name": "The Good Mineral",
                    "category": "beauty",
                    "match_score": 85,
                    "description": "We are a super clean makeup brand with prices per unit ranging between $25 to $45.",
                },
            ],
        }
        scrape = {
            "primary_niche": "lifestyle",
            "raw_bio": "UGC lifestyle and self-care",
            "content_themes": ["lifestyle", "daily routine"],
            "recent_captions": ["sunday reset in the kitchen", "self care evening"],
        }
        brands = flatten_for_you(payload, limit=6, scrape=scrape)
        names = [b["name"] for b in brands]
        self.assertNotIn("Benji Mens", names)
        self.assertEqual(names[0], "Beauty Pie")
        self.assertTrue(brands[0].get("why"))

    def test_resolve_brand_by_name(self):
        suggested = [{"id": 9, "name": "Rhode", "slug": "rhode"}]
        self.assertEqual(resolve_brand(suggested, brand_name="rhode")["id"], 9)
        self.assertIsNone(resolve_brand(suggested, brand_name="Invented Brand"))


class MailtoTests(unittest.TestCase):
    def test_includes_bcc_subject_body(self):
        url = build_mailto("pr@brand.example", "Collab?", "Hi there")
        self.assertTrue(url.startswith("mailto:pr@brand.example?"))
        self.assertIn("bcc=", url)
        self.assertIn(POLLY_BCC.replace("@", "%40"), url)
        self.assertIn("subject=Collab%3F", url)

    def test_rejects_empty_email(self):
        self.assertIsNone(build_mailto("", "Hi", "Body"))

    def test_pitch_from_package(self):
        pitch = pitch_from_package_response({
            "brand_email": "pr@brand.example",
            "package": {
                "brand": {"id": 3, "name": "Rhode"},
                "pitches": {
                    "growing": {"subject": "Collab", "body_plain": "Hi Rhode"},
                },
            },
        })
        self.assertEqual(pitch["subject"], "Collab")
        self.assertIn("mailto:pr@brand.example", pitch["mailto"])

    def test_followup_from_generate_pitch(self):
        from services.polly import pitch_from_followup_response, wants_followup_pitch
        pitch = pitch_from_followup_response({
            "subject": "Quick follow-up - Rhode collab",
            "body": "Just bumping this in case it got buried.",
            "brand_email": "pr@brand.example",
            "brand_name": "Rhode",
            "is_followup": True,
        }, {"id": 3, "name": "Rhode"})
        self.assertTrue(pitch["is_followup"])
        self.assertEqual(pitch["subject"], "Quick follow-up - Rhode collab")
        self.assertIn("buried", pitch["body"])
        self.assertNotIn("3 posts", pitch["body"])
        self.assertTrue(wants_followup_pitch({"chip_id": "draft_followup"}, "Draft Rhode follow-up"))
        self.assertTrue(wants_followup_pitch({"is_followup": True}, "yes"))
        self.assertFalse(wants_followup_pitch({}, "Suggest brands I should reach out to"))


class HeuristicIntentTests(unittest.TestCase):
    def test_profile_aware_chat_uses_name_and_niche(self):
        text = profile_aware_chat(
            "Display name: Maya Lopez\nPrimary niche: skincare",
            first_name="Maya",
        )
        self.assertIn("Maya", text)
        self.assertIn("skincare", text)
        self.assertNotIn("Here are", text)
        self.assertNotIn("fit your profile", text.lower())

    def test_suggest(self):
        d = classify_intent_heuristic("Suggest brands I should reach out to")
        self.assertEqual(d["intent"], "suggest_brands")

    def test_flatten_drops_fashion_when_skincare_required(self):
        payload = {
            "matched": [
                {"id": 1, "name": "Clementine Sleepwear", "category": "fashion", "match_score": 90},
                {"id": 2, "name": "SHENZHEN SREC TECHNOLOGY CO., LTD", "category": "tech", "match_score": 80},
                {"id": 3, "name": "Beauty Pie", "category": "beauty", "match_score": 70},
                {"id": 4, "name": "The Inkey List", "category": "skincare", "match_score": 65},
            ],
        }
        scrape = {"primary_niche": "skincare", "raw_bio": "skincare and texture"}
        brands = flatten_for_you(
            payload,
            scrape=scrape,
            niches=["skincare", "beauty"],
            required_categories=["skincare", "beauty"],
        )
        names = [b["name"] for b in brands]
        self.assertNotIn("Clementine Sleepwear", names)
        self.assertNotIn("SHENZHEN SREC TECHNOLOGY CO., LTD", names)
        self.assertLessEqual(len(names), 3)
        self.assertTrue({"Beauty Pie", "The Inkey List"} & set(names))

    def test_flatten_drops_on_running_for_skincare_micro(self):
        payload = {
            "recruiting": [{
                "id": 9,
                "name": "On Running",
                "category": "fitness",
                "match_score": 66,
                "roster_is_open": 1,
                "description": "Born in the Swiss Alps, On running shoes.",
            }],
            "matched": [
                {
                    "id": 1,
                    "name": "Sentix Cosmetics (US)",
                    "category": "skincare",
                    "match_score": 70,
                    "description": "Clean, vegan, cruelty-free beauty products.",
                    "micro_friendly": True,
                },
                {
                    "id": 2,
                    "name": "GLO",
                    "category": "beauty",
                    "match_score": 62,
                    "description": "Show how the product works on day one, then keep using it.",
                },
            ],
        }
        scrape = {
            "primary_niche": "skincare",
            "secondary_niches": ["wellness"],
            "follower_count": 202,
            "raw_bio": "skincare and wellness",
        }
        brands = flatten_for_you(
            payload,
            scrape=scrape,
            niches=["skincare", "wellness"],
            required_categories=["skincare", "beauty"],
        )
        names = [b["name"] for b in brands]
        self.assertNotIn("On Running", names)
        self.assertIn("Sentix Cosmetics (US)", names)
        self.assertEqual(names[0], "Sentix Cosmetics (US)")

    def test_done_after_pitch_is_chat_not_suggest(self):
        d = classify_intent_heuristic(
            "done",
            history=[{
                "role": "assistant",
                "content": "Right, here's your pitch for Clementine Sleepwear. Open your mail.",
            }],
            suggested_brands=[{"id": 1, "name": "Clementine Sleepwear"}],
        )
        self.assertEqual(d["intent"], "chat")

    def test_more_is_suggest_not_pitch(self):
        from services.polly import is_done_turn, is_more_brands_turn
        self.assertTrue(is_more_brands_turn("more"))
        self.assertTrue(is_more_brands_turn("More brands"))
        self.assertFalse(is_more_brands_turn("Contact Sourced"))
        self.assertTrue(is_done_turn("I sent it"))
        d = classify_intent_heuristic("more")
        self.assertEqual(d["intent"], "suggest_brands")

    def test_more_does_not_claim_draft_was_sent(self):
        from services.polly import drop_pending_draft, mark_draft_pending, say_claims_unconfirmed_send
        from services.polly_persona import persona_more_brands_intro
        self.assertTrue(say_claims_unconfirmed_send(
            "Now that Nuria Beauty pitch is out, I've got another strong one for you."
        ))
        self.assertFalse(say_claims_unconfirmed_send(
            "Nuria Beauty is still a draft — tap I sent it when it's actually out."
        ))
        notes = mark_draft_pending({}, {"id": 1, "name": "Nuria Beauty"})
        left = drop_pending_draft(
            [{"id": 1, "name": "Nuria Beauty"}, {"id": 2, "name": "Sentix Cosmetics (US)"}],
            notes,
        )
        self.assertEqual([b["name"] for b in left], ["Sentix Cosmetics (US)"])
        say = persona_more_brands_intro(left, "Nuria Beauty")
        self.assertIn("still a draft", say.lower())
        self.assertNotIn("pitch is out", say.lower())
        self.assertIn("Sentix", say)

    def test_logged_hint_is_not_duplicated(self):
        from services.polly_persona import say_already_logged
        self.assertTrue(say_already_logged(
            "Brilliant, Iza! I've logged that Naturium pitch for you."
        ))
        self.assertTrue(say_already_logged("Logged. **Naturium** is on the board."))
        self.assertFalse(say_already_logged("Right, here's your pitch for Naturium."))

    def test_draft_is_not_already_pitched(self):
        from services.polly import drop_pitched, mark_draft_pending, mark_pitched
        notes = mark_draft_pending({}, {"id": 1, "name": "Grace & Stella"})
        left = drop_pitched(
            [{"id": 1, "name": "Grace & Stella"}, {"id": 2, "name": "Sourced"}],
            notes,
        )
        self.assertEqual([b["name"] for b in left], ["Grace & Stella", "Sourced"])
        sent = mark_pitched(notes, {"id": 1, "name": "Grace & Stella"})
        self.assertNotIn("pending_pitch", sent)
        left2 = drop_pitched(
            [{"id": 1, "name": "Grace & Stella"}, {"id": 2, "name": "Sourced"}],
            sent,
        )
        self.assertEqual([b["name"] for b in left2], ["Sourced"])

    def test_unmark_pitched_restores_draft(self):
        from services.polly import mark_draft_pending, mark_pitched, unmark_pitched
        notes = mark_pitched(
            mark_draft_pending({}, {"id": 1, "name": "Nuria Beauty"}),
            {"id": 1, "name": "Nuria Beauty"},
        )
        back = unmark_pitched(notes, {"id": 1, "name": "Nuria Beauty"})
        self.assertNotIn(1, back.get("pitched_brand_ids") or [])
        self.assertEqual(back["pending_pitch"]["name"], "Nuria Beauty")

    def test_drop_pitched_moves_queue(self):
        from services.polly import drop_pitched, mark_pitched
        notes = mark_pitched({}, {"id": 1, "name": "Acure"})
        left = drop_pitched(
            [{"id": 1, "name": "Acure"}, {"id": 2, "name": "NatPat AU"}],
            notes,
        )
        self.assertEqual([b["name"] for b in left], ["NatPat AU"])

    def test_contact_known_brand(self):
        d = classify_intent_heuristic(
            "Contact Glow Co for me",
            suggested_brands=[{"id": 4, "name": "Glow Co"}],
        )
        self.assertEqual(d["intent"], "generate_pitch")
        self.assertEqual(d["brand_id"], 4)

    def test_contact_without_list_fetches_matches(self):
        d = classify_intent_heuristic("Help me contact one of my matches")
        self.assertEqual(d["intent"], "suggest_brands")

    def test_yes_after_match_offer(self):
        d = classify_intent_heuristic(
            "yes",
            history=[{
                "role": "assistant",
                "content": "Want matches first from our pool?",
            }],
        )
        self.assertEqual(d["intent"], "suggest_brands")

    def test_explicit_brand_id(self):
        d = classify_intent_heuristic(
            "ok",
            suggested_brands=[{"id": 4, "name": "Glow Co"}],
            brand_id=4,
        )
        self.assertEqual(d["intent"], "generate_pitch")

    def test_named_brand_outside_match_list(self):
        from services.polly import looks_like_brand_request, requested_brand_name
        history = [{
            "role": "assistant",
            "content": "I've got **Rare Beauty**, The Body Shop, and Thrive Causemetics.",
        }]
        d = classify_intent_heuristic("Rare Beauty", history=history)
        self.assertEqual(d["intent"], "generate_pitch")
        self.assertEqual(d["brand_name"], "Rare Beauty")
        self.assertTrue(looks_like_brand_request("Rare Beauty", history))
        self.assertEqual(requested_brand_name("Rare Beauty", history=history), "Rare Beauty")
        self.assertFalse(looks_like_brand_request("Continue setup", history))
        self.assertFalse(looks_like_brand_request("Coquitlam bc Canada", history + [{
            "role": "assistant",
            "content": "could you tell me where you're based?",
        }]))

    def test_hit_up_named_brand(self):
        d = classify_intent_heuristic("Let's hit up Grace & Stella")
        self.assertEqual(d["intent"], "generate_pitch")
        self.assertIn("Grace", d.get("brand_name") or "")

    def test_i_want_dell_is_a_brand_ask(self):
        from services.polly import requested_brand_name, strip_brand_ask
        from services.polly_persona import persona_park_draft
        self.assertEqual(strip_brand_ask("I want DELL"), "DELL")
        self.assertEqual(requested_brand_name("I want DELL"), "DELL")
        self.assertEqual(
            requested_brand_name("i want DELL", brand_name="Squarespace"),
            "DELL",
        )
        d = classify_intent_heuristic("I want DELL")
        self.assertEqual(d["intent"], "generate_pitch")
        self.assertEqual(d["brand_name"], "DELL")
        parked = persona_park_draft("Squarespace", "DELL")
        self.assertIn("Squarespace", parked)
        self.assertIn("unsent draft", parked.lower())
        self.assertEqual(persona_park_draft("DELL", "DELL"), "")

    def test_similar_brand_ties_do_not_crash_sort(self):
        ranked = [
            (-2, 0, 0, {"id": 1, "name": "Elgato"}),
            (-2, 0, 1, {"id": 2, "name": "Logitech G"}),
        ]
        ranked.sort()
        self.assertEqual(ranked[0][-1]["name"], "Elgato")

    def test_fin_dell_is_a_brand_ask_not_more_brands(self):
        from services.polly import asked_brand_query, looks_like_brand_request, requested_brand_name, strip_brand_ask
        history = [{
            "role": "assistant",
            "content": "**Squarespace** is still a draft. Elgato is my top pick.",
            "brands": [{"id": 1, "name": "Elgato"}, {"id": 2, "name": "Grow Fit Club"}],
            "pitch": {"brand_id": 9, "brand_name": "Squarespace"},
        }]
        suggested = [{"id": 1, "name": "Elgato"}, {"id": 2, "name": "Grow Fit Club"}]
        for text in ("fin Dell", "find Dell", "Dell"):
            self.assertTrue(looks_like_brand_request(text, history), text)
            self.assertEqual(asked_brand_query(text).lower(), "dell")
            self.assertEqual(
                requested_brand_name(text, suggested, history, brand_name="Squarespace").lower(),
                "dell",
            )
            d = classify_intent_heuristic(text, suggested, history=history)
            self.assertEqual(d["intent"], "generate_pitch", text)
            self.assertEqual((d.get("brand_name") or "").lower(), "dell", text)
        self.assertEqual(strip_brand_ask("find Dell"), "Dell")


class PersonaTests(unittest.TestCase):
    def test_greeting_does_not_use_handle_as_name(self):
        from services.polly_persona import creator_first_name, persona_greeting
        self.assertIsNone(creator_first_name(
            {"first_name": "mervozkn"},
            {"handle": "mervozkn"},
        ))
        text = persona_greeting("Primary niche: lifestyle")
        self.assertTrue(text.lower().startswith("hey"))
        self.assertNotRegex(text.lower(), r"\blove\b")
        self.assertNotIn("Here are", text)

    def test_scrubs_pet_names(self):
        from services.polly_persona import scrub_polly_voice
        self.assertEqual(
            scrub_polly_voice("Alright, love, happy to take another look!"),
            "Alright, happy to take another look!",
        )
        self.assertEqual(
            scrub_polly_voice("Clear stats, darling!"),
            "Clear stats!",
        )
        self.assertEqual(
            scrub_polly_voice("Morning superstar, quick one."),
            "Morning, quick one.",
        )
        self.assertIn("love to", scrub_polly_voice("I'd love to draft that pitch.").lower())

    def test_profile_audit_is_not_kit_only(self):
        from services.polly_persona import persona_ask_brand, persona_profile_audit
        say = persona_profile_audit(
            "Primary niche: beauty",
            kit={"found": True, "published": True, "has_rates": False, "gaps": [], "bio_missing_kit_url": True, "url": "https://newcollab.co/kit/jined"},
            scrape={"primary_niche": "beauty"},
            notes={"active_pain": {"code": "no_replies"}},
        )
        self.assertIn("reply", say.lower())
        self.assertIn("rates", say.lower())
        ask = persona_ask_brand()
        self.assertIn("Name the brand", ask)

    def test_off_match_and_unknown_brand_copy(self):
        from services.polly_persona import persona_off_match_pitch, persona_unknown_brand
        stretch = persona_off_match_pitch("Rare Beauty")
        self.assertIn("not a strong match", stretch.lower())
        self.assertIn("here's the pitch", stretch.lower())
        self.assertIn("Rare Beauty", stretch)
        missing = persona_unknown_brand("Rare Beauty", [
            {"name": "Pixi Beauty"},
            {"name": "Grace & Stella"},
        ])
        self.assertIn("isn't in our directory", missing.lower())
        self.assertIn("Pixi Beauty", missing)
        self.assertNotIn("next screen", missing.lower())
        self.assertNotIn("Pitches", missing)

    def test_strips_embedded_pitch_when_card_exists(self):
        from services.polly_persona import strip_embedded_pitch
        dumped = (
            "Right, here's your pitch for Habelo Beauty, LLC! Get this sent off today.\n\n"
            "Make sure you pop this into an email:\n\n"
            "**Subject:** 3 posts + 1 UGC file\n\n"
            "Hey team at Habelo Beauty, LLC,\n\n"
            "My name is @newcollabco and I create authentic UGC content.\n"
        )
        cleaned = strip_embedded_pitch(dumped)
        self.assertIn("here's your pitch", cleaned.lower())
        self.assertNotIn("Hey team", cleaned)
        self.assertNotIn("Subject:", cleaned)

    def test_brand_intro_uses_description_not_token_dump(self):
        from services.polly_persona import persona_brand_intro
        text = persona_brand_intro(
            [
                {
                    "name": "Beauty Pie",
                    "category": "beauty",
                    "description": "Treat yourself better. Join hundreds of thousands of beauty insiders.",
                    "why": "Your lifestyle posts already sit in beauty",
                },
                {"name": "The Good Mineral", "category": "beauty"},
            ],
            "Primary niche: lifestyle",
        )
        self.assertIn("Beauty Pie", text)
        self.assertIn("Treat yourself better", text)
        self.assertNotIn("already sit", text)
        self.assertNotIn("Benji", text)

    def test_done_after_pitch_does_not_greet(self):
        import os
        from unittest.mock import patch
        from services.polly import chat_reply
        with patch.dict(os.environ, {"POLLY_DISABLE_LLM": "1"}):
            text = chat_reply(
                "Display name: Mahery\nPrimary niche: skincare",
                "done",
                history=[{
                    "role": "assistant",
                    "content": "Right, here's your pitch for Clementine Sleepwear. Open your mail.",
                }],
                first_name="Mahery",
            )
        self.assertNotIn("how's your week going", text.lower())
        self.assertNotIn("Want me to line up some brands", text)

    def test_brand_intro_picks_a_favourite(self):
        from services.polly_persona import persona_brand_intro
        text = persona_brand_intro(
            [
                {"name": "Beauty Pie", "category": "beauty"},
                {"name": "Benji Mens", "category": "skincare"},
                {"name": "The Good Mineral", "category": "beauty"},
            ],
            "Primary niche: lifestyle",
        )
        self.assertIn("Beauty Pie", text)
        self.assertIn("top pick", text.lower())
        self.assertFalse(text.startswith("Here are"))

    def test_sanitize_thread_drops_junk(self):
        from services.polly_memory import sanitize_thread
        out = sanitize_thread(
            [{"role": "system", "content": "x"}, {"role": "user", "content": "hi"}],
            [{"id": 1, "name": "Rhode"}],
        )
        self.assertEqual(len(out["messages"]), 1)
        self.assertEqual(out["suggested_brands"][0]["name"], "Rhode")


class KitReviewTests(unittest.TestCase):
    def test_strip_kit_editor_paths(self):
        from services.polly_kit import strip_kit_editor_paths
        raw = (
            "Can you open your kit and publish it? "
            "Here's the link: __/creator/dashboard/my-kit__"
        )
        out = strip_kit_editor_paths(raw)
        self.assertNotIn("/creator/dashboard/my-kit", out)
        self.assertNotIn("Here's the link", out)
        self.assertIn("open your kit", out.lower())

    def test_kit_actions_uses_portfolio_button(self):
        from services.polly_kit import kit_actions
        actions = kit_actions({"published": False})
        self.assertEqual(actions[0]["label"], "My portfolio")
        self.assertEqual(actions[0]["href"], "/creator/dashboard/my-kit")
        self.assertFalse(actions[0]["external"])

    def test_unpublished_review_sends_them_to_my_kit(self):
        from services.polly_kit import persona_kit_review
        text = persona_kit_review({
            "found": True,
            "published": False,
            "url": "https://newcollab.co/kit/mahery",
            "gaps": ["Kit is not published — brands cannot open it yet.", "No rates on the kit."],
            "posts": [],
        })
        self.assertIn("My Kit", text)
        self.assertIn("https://newcollab.co/kit/mahery", text)
        self.assertNotIn("Linktree", text)
        self.assertNotIn("love", text.lower())

    def test_generic_bio_link_is_called_out(self):
        from services.polly_kit import persona_kit_review
        text = persona_kit_review({
            "found": True,
            "published": True,
            "url": "https://newcollab.co/kit/mahery",
            "post_count": 3,
            "posts": [{"platform": "tiktok", "views": 12000, "brand_name": ""}],
            "has_rates": True,
            "about_chars": 200,
            "bio_has_generic_newcollab": True,
            "bio_missing_kit_url": True,
            "gaps": ["TikTok bio links to newcollab.co, not the live kit URL."],
        })
        self.assertIn("newcollab.co/kit/mahery", text)
        self.assertIn("newcollab.co", text.lower())
        self.assertNotIn("Linktree", text)

    def test_kit_url_classifies_as_portfolio(self):
        d = classify_intent_heuristic("https://newcollab.co/kit/mahery")
        self.assertEqual(d["intent"], "coach_portfolio")

    def test_grounded_reply_rejects_linktree(self):
        from services.polly_kit import kit_reply_grounded
        kit = {"url": "https://newcollab.co/kit/mahery"}
        self.assertFalse(kit_reply_grounded("Do you have a Linktree?", kit))
        self.assertTrue(kit_reply_grounded(
            "I opened My Kit. Paste https://newcollab.co/kit/mahery in your bio.",
            kit,
        ))


class UnpackViewTests(unittest.TestCase):
    def test_tuple_paywall(self):
        status, data = unpack_view_result(({"success": False, "paywall": True, "error": "out"}, 402))
        self.assertEqual(status, 402)
        self.assertTrue(data["paywall"])

    def test_plain_dict(self):
        status, data = unpack_view_result({"success": True, "package": {}})
        self.assertEqual(status, 200)
        self.assertTrue(data["success"])


if __name__ == "__main__":
    unittest.main()
