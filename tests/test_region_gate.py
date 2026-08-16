"""Login/signup region gate: fail-open, skip IP for known allowed countries."""

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from social_verification_routes import (
    country_value_is_restricted,
    stored_country_is_allowed,
    should_block_auth_for_region,
)


class TestCountryValue(unittest.TestCase):
    def test_iso_restricted(self):
        self.assertTrue(country_value_is_restricted('IN'))
        self.assertTrue(country_value_is_restricted('ng'))
        self.assertTrue(country_value_is_restricted('PK'))

    def test_iso_allowed(self):
        self.assertFalse(country_value_is_restricted('US'))
        self.assertFalse(country_value_is_restricted('GB'))
        self.assertFalse(country_value_is_restricted('AU'))
        self.assertFalse(country_value_is_restricted('CA'))
        self.assertFalse(country_value_is_restricted('ZA'))

    def test_stored_full_names(self):
        self.assertTrue(country_value_is_restricted('India'))
        self.assertTrue(country_value_is_restricted('Nigeria'))
        self.assertFalse(country_value_is_restricted('United States'))
        self.assertFalse(country_value_is_restricted('South Africa'))

    def test_empty_is_not_restricted(self):
        self.assertFalse(country_value_is_restricted(None))
        self.assertFalse(country_value_is_restricted(''))


class TestStoredCountryIsAllowed(unittest.TestCase):
    def test_known_allowed_skips_ip(self):
        self.assertTrue(stored_country_is_allowed('US'))
        self.assertTrue(stored_country_is_allowed('United States'))
        self.assertTrue(stored_country_is_allowed('ZA'))

    def test_unknown_or_restricted_needs_ip(self):
        self.assertFalse(stored_country_is_allowed(None))
        self.assertFalse(stored_country_is_allowed(''))
        self.assertFalse(stored_country_is_allowed('IN'))
        self.assertFalse(stored_country_is_allowed('India'))


class TestShouldBlockAuthForRegion(unittest.TestCase):
    def test_allowed_stored_country_never_blocks(self):
        with patch(
            'social_verification_routes.is_request_from_restricted_region',
            return_value=True,
        ) as ip_check:
            self.assertFalse(should_block_auth_for_region('US'))
            self.assertFalse(should_block_auth_for_region('United States'))
            ip_check.assert_not_called()

    def test_unknown_country_blocks_when_ip_restricted(self):
        with patch(
            'social_verification_routes.is_request_from_restricted_region',
            return_value=True,
        ):
            self.assertTrue(should_block_auth_for_region(None))
            self.assertTrue(should_block_auth_for_region('India'))

    def test_unknown_country_fail_open_on_geo_miss(self):
        with patch(
            'social_verification_routes.is_request_from_restricted_region',
            return_value=False,
        ):
            self.assertFalse(should_block_auth_for_region(None))
            self.assertFalse(should_block_auth_for_region('India'))


if __name__ == '__main__':
    unittest.main()
