import sys
import unittest
from pathlib import Path
from unittest.mock import Mock

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


class TestLiveCampaignsForEmail(unittest.TestCase):
    def test_formats_open_rosters(self):
        from lifecycle_email_engine import get_live_campaigns_for_email

        cursor = Mock()
        cursor.fetchall.return_value = [
            {
                "name": "Glow Co",
                "category": "skincare",
                "reason": "Pick 5 creators",
                "slot_limit": 5,
                "fill_count": 2,
            }
        ]
        out = get_live_campaigns_for_email(cursor, limit=3)
        self.assertEqual(out[0]["name"], "Glow Co")
        self.assertIn("open now", out[0]["reason"])
        self.assertEqual(out[0]["spots"], 3)
