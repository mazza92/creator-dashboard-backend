import unittest
from unittest.mock import patch

from services import kit_guard as kg

URL = "https://newcollab.co/kit/ana"


class AddKitLink(unittest.TestCase):
    def test_inserted_above_signoff(self):
        body = "Hi Glossier team,\n\nI'd love to create for you.\n\nThanks,\nAna"
        out = kg.add_kit_link(body, URL)
        self.assertIn(f"My media kit (rates, stats and recent videos): {URL}\n\nThanks,\nAna", out)

    def test_opener_starting_with_love_is_not_a_signoff(self):
        body = "Hi team,\nLove your new vitamin C serum launch and the way you film it.\n\nThanks,\nAna"
        out = kg.add_kit_link(body, URL)
        self.assertLess(out.index("Love your"), out.index(URL))
        self.assertLess(out.index(URL), out.index("Thanks,"))

    def test_appended_without_signoff(self):
        self.assertTrue(kg.add_kit_link("Hi team, quick idea.", URL).rstrip().endswith(URL))

    def test_existing_kit_link_untouched(self):
        body = "Hi\nhttps://newcollab.co/kit/ana?ref=abc\nBest,\nAna"
        self.assertEqual(kg.add_kit_link(body, URL), body)


class WithKit(unittest.TestCase):
    def test_builds_kit_when_missing(self):
        with patch.object(kg, "ensure_kit", return_value="ana") as ensure:
            out = kg.with_kit(object(), 7, "Hi team\n\nBest,\nAna")
        ensure.assert_called_once()
        self.assertIn("newcollab.co/kit/ana", out)

    def test_no_slug_leaves_body(self):
        with patch.object(kg, "ensure_kit", return_value=None):
            self.assertEqual(kg.with_kit(object(), 7, "Hi"), "Hi")

    def test_tracking_applies_to_added_link(self):
        from services.kit_view_tracking import track_kit_links
        with patch.object(kg, "ensure_kit", return_value="ana"):
            out = track_kit_links(kg.with_kit(object(), 7, "Hi\n\nThanks,\nAna"), 7, 42)
        self.assertRegex(out, r"newcollab\.co/kit/ana\?ref=[0-9a-f]{12}")


if __name__ == "__main__":
    unittest.main()


class DraftsAreTracked(unittest.TestCase):
    def test_finalize_for_adds_tracked_kit_and_rebuilds_mailto(self):
        from unittest.mock import MagicMock
        from services import pitch_writer as pw
        draft = {"subject": "UGC idea", "body": "Hi team\n\nWorth a try?\nAna", "email": "pr@brand.com",
                 "mailto": "mailto:pr@brand.com?subject=old&body=old"}
        with patch.object(pw, "finalize_pitch", return_value=dict(draft)), \
                patch.object(pw, "creator_stats", return_value={}), \
                patch.object(kg, "ensure_kit", return_value="ana"):
            out = pw.finalize_for(MagicMock(), 7, 42, draft, {})
        self.assertRegex(out["body"], r"newcollab\.co/kit/ana\?ref=[0-9a-f]{12}")
        self.assertIn("ref%3D", out["mailto"])
        self.assertNotIn("body=old", out["mailto"])
