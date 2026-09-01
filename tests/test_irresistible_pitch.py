"""Irresistible gifted-PR pitch template."""

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from services.irresistible_pitch import (
    IRRESISTIBLE_SUBJECT,
    LOCATION_PLACEHOLDER,
    apply_location_to_body,
    format_followers,
    generate_irresistible_pitch,
    pitch_to_html,
    resolve_product,
)


class TestFormatFollowers(unittest.TestCase):
    def test_thousands(self):
        self.assertEqual(format_followers(12300), "12.3K")
        self.assertEqual(format_followers(12000), "12K")

    def test_empty(self):
        self.assertEqual(format_followers(0), "")
        self.assertEqual(format_followers(None), "")


class TestResolveProduct(unittest.TestCase):
    def test_hero_product(self):
        self.assertEqual(
            resolve_product({"hero_product": "Heartleaf 77% Toner"}, {}),
            "Heartleaf 77% Toner",
        )

    def test_generic_falls_back_to_pr_sample(self):
        self.assertEqual(
            resolve_product({"hero_product": "Glossier products", "brand_name": "Glossier"}, {}),
            "PR sample",
        )

    def test_missing_falls_back_to_pr_sample(self):
        self.assertEqual(resolve_product({"brand_name": "Acme"}, {}), "PR sample")


class TestIrresistiblePitch(unittest.TestCase):
    def _creator(self, **extra):
        data = {
            "username": "gabydky",
            "social_handle": "gabydky",
            "followers_count": 18400,
            "engagement_rate": 4.2,
            "niche": ["skincare"],
            "social_links": [{"platform": "tiktok", "followersCount": 18400, "handle": "gabydky"}],
            "primary_age_range": "18-24",
            "kit_published": True,
        }
        data.update(extra)
        return data

    def _brand(self, **extra):
        data = {
            "id": 99,
            "brand_name": "Anua",
            "hero_product": "Heartleaf 77% Soothing Toner",
            "price_point": 24,
            "category": "skincare",
        }
        data.update(extra)
        return data

    def test_filled_template(self):
        pitch = generate_irresistible_pitch(
            self._brand(),
            self._creator(),
            city="Lyon",
            country="France",
            proof_builder=lambda *a, **k: {
                "kind": "kit",
                "url": "https://newcollab.co/kit/gabydky?ref=abc",
                "kit_token": "abc",
            },
        )
        self.assertEqual(pitch["subject"], IRRESISTIBLE_SUBJECT)
        self.assertNotIn("Heartleaf", pitch["subject"])
        body = pitch["body"]
        self.assertIn("Hi Anua,", body)
        self.assertIn("I create skincare content on TikTok (https://www.tiktok.com/@gabydky) for 18.4K followers with 4.2% engagement.", body)
        self.assertIn("My audience is 18-24 skincare fans.", body)
        self.assertIn("Trade offer for a Heartleaf 77% Soothing Toner PR box:", body)
        self.assertNotIn("retail", body)
        self.assertIn("• 3 organic posts to my TikTok within 21 days", body)
        self.assertIn("• 1 raw UGC video file (yours to run as paid ads, 6-month rights)", body)
        self.assertIn("• 30-day performance report (views, saves, CTR, DMs)", body)
        self.assertIn("No fee. Just product + shipping to Lyon, France.", body)
        self.assertIn("Recent work: https://newcollab.co/kit/gabydky?ref=abc", body)
        self.assertIn("Worth a look?", body)
        self.assertTrue(body.strip().endswith("gabydky"))
        self.assertFalse(pitch["needs_location"])

    def test_placeholder_location_and_pr_sample(self):
        pitch = generate_irresistible_pitch(
            {"brand_name": "Acme", "category": "beauty"},
            self._creator(engagement_rate=None, followers_count=0, social_links=[]),
        )
        self.assertIn(f"shipping to {LOCATION_PLACEHOLDER}.", pitch["body"])
        self.assertEqual(pitch["subject"], IRRESISTIBLE_SUBJECT)
        self.assertTrue(pitch["needs_location"])
        self.assertNotIn("engagement", pitch["body"])
        self.assertNotIn("retail", pitch["body"])

    def test_social_handle_when_no_kit(self):
        pitch = generate_irresistible_pitch(
            self._brand(),
            self._creator(kit_published=False),
            proof_builder=lambda *a, **k: {
                "kind": "social",
                "url": "https://tiktok.com/@gabydky",
            },
        )
        self.assertIn("Recent work: https://tiktok.com/@gabydky", pitch["body"])

    def test_apply_location(self):
        body = "No fee. Just product + shipping to [CITY, COUNTRY].\n\nWorth a look?"
        updated = apply_location_to_body(body, "Austin, United States", "[CITY, COUNTRY]")
        self.assertIn("shipping to Austin, United States.", updated)
        self.assertNotIn(LOCATION_PLACEHOLDER, updated)

    def test_engagement_from_avg_when_primary_empty(self):
        pitch = generate_irresistible_pitch(
            self._brand(),
            self._creator(engagement_rate=0, avg_engagement_rate=3.8),
        )
        self.assertIn("with 3.8% engagement", pitch["body"])

    def test_instagram_intro_links_profile(self):
        pitch = generate_irresistible_pitch(
            self._brand(),
            self._creator(
                social_links=[{
                    "platform": "instagram",
                    "followersCount": 7000,
                    "handle": "yasia_a",
                    "url": "https://www.instagram.com/yasia_a/",
                }],
                followers_count=7000,
            ),
        )
        self.assertIn(
            "content on Instagram (https://www.instagram.com/yasia_a/) for 7K followers with 4.2% engagement.",
            pitch["body"],
        )
        html = pitch_to_html(pitch["body"])
        self.assertIn('<a href="https://www.instagram.com/yasia_a/">Instagram</a>', html)
        self.assertNotIn("to my Instagram (", pitch["body"])


if __name__ == "__main__":
    unittest.main()
