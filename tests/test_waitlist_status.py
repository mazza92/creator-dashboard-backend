"""Waitlist status payload: name, masked email and application date for the review tracker."""
import unittest

from creator_approval_routes import mask_email


class MaskEmailTests(unittest.TestCase):
    def test_masks_local_part(self):
        self.assertEqual(mask_email("liv.gabrielle@gmail.com"), "liv***@gmail.com")
        self.assertEqual(mask_email("ab@x.co"), "ab***@x.co")

    def test_bad_input(self):
        self.assertIsNone(mask_email(None))
        self.assertIsNone(mask_email("not-an-email"))
        self.assertIsNone(mask_email("@x.co"))


if __name__ == "__main__":
    unittest.main()
