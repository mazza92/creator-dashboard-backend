"""Polly calls the creator by one name: the account first name, even when the scraped display name differs."""

import unittest

from services.polly import build_profile_context


class CallName(unittest.TestCase):
    def test_account_name_wins_over_display_name(self):
        ctx = build_profile_context({"full_name": "Raya", "handle": "rayaaa_hussain"}, {"first_name": "Nadia"})
        self.assertIn("Call them: Nadia", ctx)

    def test_no_name_no_line(self):
        self.assertNotIn("Call them", build_profile_context({}, {}))


if __name__ == "__main__":
    unittest.main()
