import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from services.polly_gigs import (
    gig_card_from_opp,
    gig_dedupe_key,
    gig_fingerprints_from_history,
    is_paid_listing,
    last_assistant_had_gigs,
    source_platform_label,
    wants_more_gigs,
)
from services.polly_persona import persona_gigs_intro, persona_kit_after_gigs
from services.polly_discovery import starters_for


class PollyGigsTests(unittest.TestCase):
    def test_source_labels(self):
        self.assertEqual(source_platform_label("aspireiq"), "AspireIQ")
        self.assertEqual(source_platform_label("linkedin"), "LinkedIn")
        self.assertIsNone(source_platform_label(""))

    def test_paid_vs_gifted_listings(self):
        self.assertTrue(is_paid_listing({"is_sourced": True, "pay_label": "$150"}))
        self.assertTrue(is_paid_listing({"is_sourced": True, "pay_label": "Paid"}))
        self.assertTrue(is_paid_listing({"is_sourced": True}))
        self.assertFalse(is_paid_listing({"is_sourced": True, "pay_label": "Gifted product"}))
        self.assertFalse(is_paid_listing({"is_sourced": False, "pay_label": "Unpaid"}))
        self.assertTrue(is_paid_listing({"is_sourced": False, "pr_value_usd": 200}))

    def test_pay_amount_beats_generic_paid(self):
        from services.gig_listing import format_pay_label, prefer_amount_pay
        self.assertEqual(prefer_amount_pay("Paid", "$250"), "$250")
        self.assertEqual(format_pay_label(250, "Paid"), "$250")
        self.assertEqual(format_pay_label(None, "Hourly: $20.00-$45.00"), "$20–$45/hr")
        card = gig_card_from_opp({
            "id": 77,
            "brand_name": "ēma",
            "product_name": "Creators Wanted: Ēma Solid Perfume",
            "campaign_description": "Short UGC videos (approx. 30-60 seconds each) with voiceover.",
            "pr_value_usd": 400,
            "pay_label": "Paid",
            "is_sourced": True,
            "source_platform": "upwork",
            "listing_brief": {
                "brand": "ēma",
                "headline": "Solid Perfume UGC",
                "summary": "Short UGC videos with voiceover.",
                "pay": "Paid",
            },
        })
        self.assertEqual(card["pay_label"], "$400")

    def test_placeholder_listing_gets_a_readable_structure(self):
        from services.gig_listing import apply_llm_rewrite, rewrite_is_grounded
        perfume = gig_card_from_opp({
            "id": 21,
            "brand_name": "Unknown brand",
            "product_name": "UGC TikTok Perfume Ad",
            "campaign_description": (
                "12-20 second, 9:16 TikTok video, 'get-ready-with-me' style, shot on phone\n"
                "$1500-$3000 · paid"
            ),
            "pr_value_usd": 3000,
            "pay_label": "$3000",
            "is_sourced": True,
            "source_platform": "freelancer",
            "display_niche": "Beauty",
            "shipping_regions": ["US"],
        })
        self.assertTrue(perfume["brand_unknown"])
        self.assertIn("perfume", perfume["headline"].lower())
        self.assertNotEqual(perfume["name"].lower(), "unknown brand")
        self.assertEqual(perfume["pay_label"], "$1,500–$3,000")
        self.assertIn("GRWM", perfume["deliverable"])
        self.assertIn("TikTok", perfume["deliverable"])
        self.assertNotIn("$1500", perfume["summary"])
        self.assertNotIn("Unknown brand", perfume["summary"])

        health = gig_card_from_opp({
            "id": 22,
            "brand_name": "Unknown brand",
            "product_name": "Healthy Lifestyle UGC Content Creation",
            "campaign_description": (
                "Fresh, authentic product reviews for a health-focused blog\n"
                "$750-$1500 · paid"
            ),
            "pr_value_usd": 1500,
            "is_sourced": True,
            "source_platform": "freelancer",
            "display_niche": "Healthy Lifestyle",
        })
        self.assertEqual(health["pay_label"], "$750–$1,500")
        self.assertIn("product reviews", health["summary"].lower())

        redbarn = gig_card_from_opp({
            "id": 23,
            "brand_name": "Welcome UGC Creators",
            "product_name": "Welcome UGC Creators - Welcome!",
            "campaign_description": (
                "featuring Redbarn products across Instagram, TikTok, and/or YouTube. "
                "We're looking for passionate dog owners to create authentic content."
            ),
            "pr_value_usd": 1000,
            "is_sourced": True,
            "source_platform": "aspireiq",
            "display_niche": "Lifestyle",
        })
        self.assertEqual(redbarn["brand_name"], "Redbarn")
        self.assertFalse(redbarn["brand_unknown"])
        self.assertEqual(redbarn["name"], "Redbarn")
        self.assertNotIn("Welcome", redbarn["headline"])
        self.assertIn("dog owners", redbarn["summary"].lower())

        grounded = apply_llm_rewrite(dict(perfume), {
            "brand": "",
            "headline": "Perfume GRWM TikTok",
            "summary": "Film a 12–20s get-ready-with-me TikTok on your phone.",
            "deliverable": "12–20s, 9:16, GRWM, TikTok",
            "pay": "$1,500–$3,000",
        })
        self.assertEqual(grounded["headline"], "Perfume GRWM TikTok")
        self.assertEqual(grounded["pay_label"], "$1,500–$3,000")
        self.assertTrue(rewrite_is_grounded(
            {"brand": "MadeUp Co", "pay": "$9,999"},
            perfume["raw_listing"],
        ) is False)

    def test_aspire_program_pages_do_not_dump_chrome(self):
        ryl = gig_card_from_opp({
            "id": 31,
            "brand_name": "The Ryl Collective",
            "product_name": "The Ryl Collective - Affiliate Program Overview",
            "campaign_description": (
                "and daily life. $250 · paid+gift The Ryl Collective The Ryl Co."
            ),
            "pr_value_usd": 250,
            "is_sourced": True,
            "source_platform": "aspireiq",
            "display_niche": "Lifestyle",
            "shipping_regions": ["US"],
        })
        self.assertEqual(ryl["name"], "The Ryl Collective")
        self.assertEqual(ryl["headline"], "The Ryl Collective")
        self.assertNotIn("Affiliate", ryl["headline"])
        self.assertNotIn("$250", ryl["summary"])
        self.assertNotIn("paid+gift", ryl["summary"].lower())
        self.assertFalse(ryl["summary"][:1].islower())

        love = gig_card_from_opp({
            "id": 32,
            "brand_name": "Be LOVE™ Clear Protein UGC Community",
            "product_name": "Be LOVE™ Clear Protein UGC Community - What We're Looking For",
            "campaign_description": (
                "for Be LOVE's organic and paid media efforts. Share it in your own style - "
                "through lifestyle moments, storytelling, or whatever feels authentic."
            ),
            "pr_value_usd": 250,
            "is_sourced": True,
            "source_platform": "aspireiq",
        })
        self.assertNotIn("What We're Looking For", love["headline"])
        self.assertTrue(love["summary"].startswith("Share it"))
        self.assertFalse(love["summary"].startswith("for "))

        joy = gig_card_from_opp({
            "id": 33,
            "brand_name": "The Joy Edit",
            "product_name": "The Joy Edit - Why Partner With Shop LC",
            "campaign_description": (
                "for a purpose-driven jewelry brand where every purchase helps feed a child "
                "$250 - paid The Joy Edit Join the Shop LC Creator Community Join The Joy Edit"
            ),
            "pr_value_usd": 250,
            "is_sourced": True,
            "source_platform": "aspireiq",
        })
        self.assertEqual(joy["headline"], "The Joy Edit")
        self.assertNotIn("Why Partner", joy["headline"])
        self.assertNotIn("$250", joy["summary"])
        self.assertFalse(joy["summary"][:1].islower())

    def test_slogans_and_long_job_titles_become_short_cards(self):
        unruly = gig_card_from_opp({
            "id": 41,
            "brand_name": "UNRULY wants to become a daily habit that builds on activities you already love.",
            "product_name": "What To Expect",
            "campaign_description": (
                "UNRULY wants to become a daily habit that builds on activities you already love. "
                "Film everyday workouts and lifestyle moments with the product."
            ),
            "pr_value_usd": 200,
            "is_sourced": True,
            "source_platform": "aspireiq",
            "display_niche": "Lifestyle",
        })
        self.assertEqual(unruly["name"], "UNRULY")
        self.assertNotIn("wants to", unruly["name"].lower())
        self.assertNotEqual(unruly["headline"].lower(), "what to expect")
        self.assertNotIn("wants to", unruly["summary"].lower())
        self.assertIn("workouts", unruly["summary"].lower())

        travel = gig_card_from_opp({
            "id": 42,
            "brand_name": "On-Camera Video Presenter / Spokesperson For Senior Travel & Leisure",
            "product_name": "On-Camera Video Presenter / Spokesperson For Senior Travel & Leisure",
            "campaign_description": (
                "20 short-form videos (30-60 seconds each) covering travel tips, lifestyle advice, "
                "leisure guides for seniors."
            ),
            "pr_value_usd": 10,
            "is_sourced": True,
            "source_platform": "upwork",
        })
        self.assertLessEqual(len(travel["name"].split()), 6)
        self.assertNotIn("/", travel["name"])
        self.assertIn("travel tips", travel["summary"].lower())
        self.assertIn("30–60s", travel["deliverable"])

        cat = gig_card_from_opp({
            "id": 43,
            "brand_name": "Cat Butler",
            "product_name": "Cat Litter UGC Ad",
            "campaign_description": (
                "30-60 second vertical UGC video featuring creator and cat, demonstrating product use."
            ),
            "pr_value_usd": 200,
            "is_sourced": True,
            "source_platform": "upwork",
        })
        self.assertEqual(cat["name"], "Cat Butler")
        self.assertIn("litter", cat["headline"].lower())

    def test_gig_card_strips_email_and_keeps_apply_path(self):
        card = gig_card_from_opp({
            "id": 9,
            "brand_name": "GLO",
            "campaign_description": "Paid UGC. Email producer@brand.test for details.",
            "pay_label": "$250",
            "is_sourced": True,
            "source_platform": "aspireiq",
            "apply_mode": "url",
            "external_apply_url": "https://app.aspireiq.com/jobs/glo",
            "apply_email": "producer@brand.test",
            "display_niche": "Beauty",
        })
        self.assertEqual(card["name"], "GLO")
        self.assertEqual(card["source_label"], "AspireIQ")
        self.assertIn("Paid UGC", card["blurb"])
        self.assertNotIn("@", card["blurb"])
        self.assertEqual(card["external_apply_url"], "https://app.aspireiq.com/jobs/glo")
        self.assertIsNone(card["apply_email"])
        self.assertIsNone(card.get("website"))

    def test_gig_card_uses_site_favicon_when_logo_missing(self):
        from services.polly_gigs import brand_logo_src
        self.assertIsNone(brand_logo_src(None, "https://www.virealapps.com"))
        self.assertIsNone(brand_logo_src(None, "https://app.sideshift.app/jobs/x"))
        self.assertEqual(
            brand_logo_src("https://cdn.example.com/logo.png", "https://smoothspeak.ai"),
            "https://cdn.example.com/logo.png",
        )
        card = gig_card_from_opp({
            "id": 13,
            "brand_name": "Vireal Apps",
            "brand_website": "https://www.virealapps.com",
            "is_sourced": True,
            "source_platform": "sideshift",
        })
        self.assertIsNone(card.get("logo"))

    def test_gig_card_keeps_readable_blurb_and_brand_site(self):
        from services.polly_gigs import public_brand_site
        self.assertEqual(public_brand_site("https://www.bigo.tv"), "https://www.bigo.tv")
        self.assertIsNone(public_brand_site("https://newyork.craigslist.org/foo"))
        card = gig_card_from_opp({
            "id": 11,
            "brand_name": "BIGO Live",
            "product_name": "Live-stream hosts",
            "campaign_description": (
                "Live-stream content on BIGO Live.\n"
                "Monthly paid role for lifestyle creators.\n"
                "Apply here: https://newyork.craigslist.org/foo"
            ),
            "brand_website": "https://www.bigo.tv",
            "pay_label": "$510-32K",
            "is_sourced": True,
            "source_platform": "craigslist",
            "apply_mode": "url",
            "external_apply_url": "https://newyork.craigslist.org/foo",
            "display_niche": "Lifestyle",
        })
        self.assertIn("Monthly paid role", card["blurb"])
        self.assertNotIn("Apply here", card["blurb"])
        self.assertEqual(card["website"], "https://www.bigo.tv")
        self.assertEqual(card["product_name"], "Live-stream hosts")
        self.assertEqual(card["location"], "New York")
        craig = gig_card_from_opp({
            "id": 12,
            "brand_name": "BIGO Live",
            "campaign_description": "Live-stream content on BIGO Live.",
            "brand_website": "https://www.craigslist.org/view/d/los-angeles-earn-monthly-to-live-stream/abc",
            "is_sourced": True,
            "source_platform": "craigslist",
            "external_apply_url": "https://www.craigslist.org/view/d/los-angeles-earn-monthly-to-live-stream/abc",
        })
        self.assertIsNone(craig["website"])
        self.assertEqual(craig["location"], "Los Angeles")

    def test_city_reposts_share_a_dedupe_key(self):
        a = gig_card_from_opp({
            "id": 1,
            "brand_name": "BIGO Live",
            "product_name": "Earn $510-32K Monthly to Live-stream on BIGO Live",
            "campaign_description": "Live-stream content on BIGO Live. Monthly · paid",
            "is_sourced": True,
            "source_platform": "craigslist",
            "external_apply_url": "https://www.craigslist.org/view/d/los-angeles-earn-monthly/a",
        })
        b = gig_card_from_opp({
            "id": 2,
            "brand_name": "BIGO Live",
            "product_name": "Earn $510-32K Monthly to Live-stream on BIGO Live",
            "campaign_description": "Live-streaming content on BIGO Live $510-32K Monthly · paid",
            "is_sourced": True,
            "source_platform": "craigslist",
            "external_apply_url": "https://www.craigslist.org/view/d/brooklyn-earn-monthly/b",
        })
        other = gig_card_from_opp({
            "id": 3,
            "brand_name": "GLO",
            "product_name": "Paid UGC for skincare",
            "campaign_description": "Beauty UGC",
            "is_sourced": True,
            "source_platform": "aspireiq",
            "external_apply_url": "https://app.aspireiq.com/jobs/glo",
        })
        self.assertEqual(gig_dedupe_key(a), gig_dedupe_key(b))
        self.assertNotEqual(gig_dedupe_key(a), gig_dedupe_key(other))
        seen = gig_fingerprints_from_history([{"role": "assistant", "gigs": [a]}])
        self.assertIn(gig_dedupe_key(b), seen)
        self.assertNotIn(gig_dedupe_key(other), seen)

    def test_persona_does_not_mix_directory_contact(self):
        text = persona_gigs_intro([
            {
                "name": "GLO",
                "source_label": "AspireIQ",
                "is_sourced": True,
                "pay_label": "$250",
            },
            {"name": "Future Society", "source_label": "LinkedIn", "is_sourced": True},
        ])
        self.assertIn("AspireIQ", text)
        self.assertIn("LinkedIn", text)
        self.assertIn("Apply here", text)
        self.assertIn("one place", text.lower())
        self.assertIn("indeed", text.lower())
        self.assertNotIn("Contact", text)
        self.assertNotIn("gifted Directory pitch", text)
        empty = persona_gigs_intro([])
        self.assertIn("one list", empty.lower())
        self.assertIn("Pitch Directory brands instead", empty)
        self.assertNotIn("Contact", empty)

    def test_kit_after_gigs_is_lever_not_gate(self):
        nudge = persona_kit_after_gigs({"has_rates": False})
        self.assertIn("My Kit", nudge)
        self.assertIn("Apply here", nudge)
        self.assertNotIn("Contact", nudge)
        self.assertEqual(persona_kit_after_gigs({"has_rates": True}), "")

    def test_more_after_gigs_stays_on_scanner(self):
        history = [{"role": "assistant", "gigs": [{"id": 1, "name": "GLO"}]}]
        self.assertTrue(last_assistant_had_gigs(history))
        self.assertTrue(wants_more_gigs("more", history))
        self.assertTrue(wants_more_gigs("find more", history))
        self.assertTrue(wants_more_gigs("Show more offers", history))
        self.assertTrue(wants_more_gigs("Find more offers", history))
        self.assertTrue(wants_more_gigs("more offers", history))
        self.assertFalse(wants_more_gigs("more brands", history))
        self.assertFalse(wants_more_gigs("more", [{"role": "assistant", "brands": [{"id": 1}]}]))
        from services.polly import classify_intent_heuristic
        self.assertEqual(
            classify_intent_heuristic("find more", history=history)["intent"],
            "suggest_gigs",
        )
        self.assertTrue(wants_more_gigs("find more", [], notes={"saw_gigs": True}))
        drop = [{
            "role": "assistant",
            "content": "I pull paid UGC gigs into one place. Same idea as Indeed. Tap Apply here.",
        }]
        self.assertTrue(last_assistant_had_gigs(drop))
        self.assertTrue(wants_more_gigs("find more", drop))

    def test_shown_gigs_paginate(self):
        from services.polly_gigs import GIG_PAGE_SIZE, gig_ids_from_history, mark_shown_gigs, shown_gig_ids
        self.assertEqual(GIG_PAGE_SIZE, 6)
        notes = mark_shown_gigs({}, [{"id": 1}, {"id": 2}, {"id": 3}])
        self.assertEqual(shown_gig_ids(notes), [1, 2, 3])
        notes = mark_shown_gigs(notes, [{"id": 2}, {"id": 4}])
        self.assertEqual(shown_gig_ids(notes), [1, 2, 3, 4])
        self.assertEqual(
            gig_ids_from_history([
                {"role": "assistant", "gigs": [{"id": "1"}, {"id": 2}]},
                {"role": "user", "content": "find more"},
            ]),
            [1, 2],
        )

    def test_more_copy_does_not_repeat_indeed_pitch(self):
        text = persona_gigs_intro(
            [{"name": "GLO", "source_label": "AspireIQ", "is_sourced": True}],
            more=True,
        )
        self.assertIn("more", text.lower())
        self.assertIn("Apply here", text)
        self.assertNotIn("Indeed", text)
        empty_more = persona_gigs_intro([], more=True)
        self.assertIn("already seen", empty_more.lower())

    def test_starters_after_gigs_offer_directory_fallback(self):
        chips = starters_for({"saw_gigs": True})
        ids = [c["id"] for c in chips]
        self.assertEqual(ids[0], "paid_ugc")
        self.assertNotIn("more_gigs", ids)
        self.assertIn("directory_pitch", ids)
        directory = next(c for c in chips if c["id"] == "directory_pitch")
        self.assertEqual(directory["action"], "suggest_brands")
        wanted = [c["id"] for c in starters_for({"wanted_gigs": True})]
        self.assertIn("directory_pitch", wanted)
        self.assertNotIn("more_gigs", wanted)
        pending = starters_for({
            "saw_gigs": True,
            "pending_pitch": {"id": 9, "name": "Tarte Cosmetics"},
        })
        self.assertNotIn("more_gigs", [c["id"] for c in pending])
        self.assertEqual(pending[0]["id"], "i_sent_it")


if __name__ == "__main__":
    unittest.main()
