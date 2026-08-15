"""Public brand APIs must not leak gated contact fields to scrapers."""

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from services.public_brand_guard import (
    is_scraper_ua,
    strip_gated_brand_fields,
)


class TestStripGatedFields(unittest.TestCase):
    def test_removes_email_and_application_url(self):
        payload = strip_gated_brand_fields({
            'name': 'Glow Recipe',
            'pr_contact_email': 'pr@glowrecipe.com',
            'pr_manager_name': 'Jane',
            'application_url': 'https://secret.form',
            'structuredData': {
                'applicationContact': {
                    'email': 'pr@glowrecipe.com',
                    'name': 'Jane',
                }
            },
        })
        self.assertNotIn('pr_contact_email', payload)
        self.assertNotIn('application_url', payload)
        self.assertNotIn('pr_manager_name', payload)
        self.assertTrue(payload['hasEmailContact'])
        self.assertTrue(payload['hasApplication'])
        self.assertIsNone(payload['structuredData']['applicationContact']['email'])


class TestScraperUa(unittest.TestCase):
    def test_python_requests_is_scraper(self):
        with patch('services.public_brand_guard._headers', return_value={'User-Agent': 'python-requests/2.31.0'}):
            self.assertTrue(is_scraper_ua())

    def test_googlebot_is_not_scraper(self):
        with patch('services.public_brand_guard._headers', return_value={'User-Agent': 'Mozilla/5.0 (compatible; Googlebot/2.1)'}):
            self.assertFalse(is_scraper_ua())

    def test_our_site_origin_is_not_scraper(self):
        with patch('services.public_brand_guard._headers', return_value={
            'User-Agent': 'python-requests/2.31.0',
            'Origin': 'https://newcollab.co',
        }):
            self.assertFalse(is_scraper_ua())


if __name__ == '__main__':
    unittest.main()
