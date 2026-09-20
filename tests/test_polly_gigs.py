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
        from services.polly_gigs import gig_ids_from_history, mark_shown_gigs, shown_gig_ids
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
        self.assertIn("more_gigs", ids)
        self.assertIn("directory_pitch", ids)
        directory = next(c for c in chips if c["id"] == "directory_pitch")
        self.assertEqual(directory["action"], "suggest_brands")
        wanted = [c["id"] for c in starters_for({"wanted_gigs": True})]
        self.assertIn("directory_pitch", wanted)
        self.assertIn("more_gigs", wanted)
        pending = starters_for({
            "saw_gigs": True,
            "pending_pitch": {"id": 9, "name": "Tarte Cosmetics"},
        })
        self.assertEqual(pending[0]["id"], "i_sent_it")
        self.assertIn("more_gigs", [c["id"] for c in pending])


if __name__ == "__main__":
    unittest.main()
