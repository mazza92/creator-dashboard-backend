"""Pitch sign-off must use the public social name, not a legal first name."""

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from services.pitch_identity import resolve_pitch_identity
from services.gemini_pitch_generator_v2 import (
    VariantIds,
    build_user_prompt,
    generate_variant_ids,
)


class TestResolvePitchIdentity(unittest.TestCase):
    def test_prefers_handle_over_legal_first_name(self):
        ident = resolve_pitch_identity({
            "first_name": "Matio",
            "username": "xoitsariel",
            "social_handle": "xoitsariel",
        })
        self.assertEqual(ident["signoff_name"], "xoitsariel")
        self.assertEqual(ident["handle"], "xoitsariel")
        self.assertNotEqual(ident["signoff_name"], "Matio")

    def test_username_beats_first_name_when_no_handle(self):
        ident = resolve_pitch_identity({
            "first_name": "Matio",
            "username": "xoitsariel",
        })
        self.assertEqual(ident["signoff_name"], "xoitsariel")

    def test_strips_at_prefix(self):
        ident = resolve_pitch_identity({
            "first_name": "Matio",
            "social_handle": "@xoitsariel",
        })
        self.assertEqual(ident["signoff_name"], "xoitsariel")
        self.assertEqual(ident["handle"], "xoitsariel")

    def test_handle_from_social_links(self):
        ident = resolve_pitch_identity({
            "first_name": "Matio",
            "social_links": [{"platform": "instagram", "handle": "@xoitsariel"}],
        })
        self.assertEqual(ident["signoff_name"], "xoitsariel")

    def test_falls_back_to_first_name(self):
        ident = resolve_pitch_identity({"first_name": "Priya"})
        self.assertEqual(ident["signoff_name"], "Priya")
        self.assertEqual(ident["handle"], "")

    def test_ignores_generic_creator_label(self):
        ident = resolve_pitch_identity({"first_name": "Creator", "username": "priyaskincloset"})
        self.assertEqual(ident["signoff_name"], "priyaskincloset")


class TestPitchPromptIdentity(unittest.TestCase):
    def test_subject_variant_skips_creator_interest(self):
        ids = {generate_variant_ids().subject_id for _ in range(250)}
        self.assertNotIn(3, ids)
        self.assertTrue(ids.issubset({1, 2, 4, 5, 6, 7, 8}))

    def test_prompt_signs_with_handle_not_legal_name(self):
        prompt = build_user_prompt(
            {"brand_name": "Tarte", "hero_product": "shape tape", "category": "makeup"},
            {
                "first_name": "Matio",
                "social_handle": "xoitsariel",
                "username": "xoitsariel",
                "creator_niches": ["beauty"],
            },
            VariantIds(1, 1, 1, 1),
        )
        self.assertIn("- Name: xoitsariel", prompt)
        self.assertNotIn("- Name: Matio", prompt)
        self.assertIn("@xoitsariel", prompt)
        self.assertIn("never a different legal first name", prompt)
        self.assertIn("Only pitch shape tape if it fits", prompt)


if __name__ == "__main__":
    unittest.main()
