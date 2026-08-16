"""Duplicate creator-to-brand outreach should be blocked after the first send."""

import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from services.outreach_dedupe import duplicate_outreach_block


NOW = datetime(2026, 8, 16, 12, 0, tzinfo=timezone.utc)


class TestDuplicateOutreachBlock(unittest.TestCase):
    def test_first_send_allowed_with_empty_pipeline(self):
        self.assertIsNone(duplicate_outreach_block(None, now=NOW))
        self.assertIsNone(duplicate_outreach_block({}, now=NOW))

    def test_blocks_return_from_mail_within_24h(self):
        block = duplicate_outreach_block(
            {'mailto_opened_at': NOW - timedelta(minutes=3)},
            now=NOW,
        )
        self.assertIsNotNone(block)
        self.assertEqual(block['code'], 'duplicate_outreach')
        self.assertFalse(block['can_followup'])

    def test_allows_retry_if_mail_was_cancelled_yesterday(self):
        self.assertIsNone(duplicate_outreach_block(
            {'mailto_opened_at': NOW - timedelta(hours=25)},
            now=NOW,
        ))

    def test_confirmed_first_pitch_cannot_be_resent(self):
        block = duplicate_outreach_block(
            {
                'send_confirmed': True,
                'pitched_at': NOW - timedelta(days=2),
            },
            now=NOW,
        )
        self.assertIsNotNone(block)
        self.assertTrue(block['can_followup'])

    def test_pitched_without_confirm_still_blocks_initial(self):
        block = duplicate_outreach_block(
            {'pitched_at': NOW - timedelta(hours=1)},
            now=NOW,
        )
        self.assertIsNotNone(block)
        self.assertTrue(block['can_followup'])

    def test_followup_allowed_after_initial_pitch(self):
        self.assertIsNone(duplicate_outreach_block(
            {
                'send_confirmed': True,
                'pitched_at': NOW - timedelta(days=8),
            },
            is_followup=True,
            now=NOW,
        ))

    def test_followup_blocked_when_just_sent(self):
        block = duplicate_outreach_block(
            {
                'send_confirmed': True,
                'pitched_at': NOW - timedelta(days=8),
                'followup_sent_at': NOW - timedelta(hours=1),
            },
            is_followup=True,
            now=NOW,
        )
        self.assertIsNotNone(block)
        self.assertFalse(block['can_followup'])

    def test_followup_uses_mailto_timestamp_too(self):
        block = duplicate_outreach_block(
            {'mailto_opened_at': NOW - timedelta(hours=2)},
            is_followup=True,
            now=NOW,
        )
        self.assertIsNotNone(block)


if __name__ == '__main__':
    unittest.main()
