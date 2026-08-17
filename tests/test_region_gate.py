"""Login/signup region gate: allowlist US/UK/AU/NZ/Europe, fail-open on geo miss."""

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from social_verification_routes import (
    country_value_is_restricted,
    region_code_is_allowed,
    stored_country_is_allowed,
    should_block_auth_for_region,
)


class TestCountryValue(unittest.TestCase):
    def test_iso_restricted(self):
        self.assertTrue(country_value_is_restricted('IN'))
        self.assertTrue(country_value_is_restricted('ng'))
        self.assertTrue(country_value_is_restricted('PK'))
        self.assertTrue(country_value_is_restricted('ZA'))
        self.assertTrue(country_value_is_restricted('IQ'))
        self.assertTrue(country_value_is_restricted('SG'))
        self.assertTrue(country_value_is_restricted('RU'))

    def test_iso_allowed(self):
        self.assertFalse(country_value_is_restricted('US'))
        self.assertFalse(country_value_is_restricted('GB'))
        self.assertFalse(country_value_is_restricted('UK'))
        self.assertFalse(country_value_is_restricted('AU'))
        self.assertFalse(country_value_is_restricted('CA'))
        self.assertFalse(country_value_is_restricted('NZ'))
        self.assertFalse(country_value_is_restricted('DE'))
        self.assertFalse(country_value_is_restricted('FR'))
        self.assertFalse(country_value_is_restricted('IE'))
        self.assertFalse(country_value_is_restricted('NL'))

    def test_stored_full_names(self):
        self.assertTrue(country_value_is_restricted('India'))
        self.assertTrue(country_value_is_restricted('Nigeria'))
        self.assertTrue(country_value_is_restricted('South Africa'))
        self.assertFalse(country_value_is_restricted('United States'))
        self.assertFalse(country_value_is_restricted('United Kingdom'))
        self.assertFalse(country_value_is_restricted('Canada'))
        self.assertFalse(country_value_is_restricted('Germany'))
        self.assertFalse(country_value_is_restricted('New Zealand'))

    def test_empty_is_not_restricted(self):
        self.assertFalse(country_value_is_restricted(None))
        self.assertFalse(country_value_is_restricted(''))


class TestRegionCodeIsAllowed(unittest.TestCase):
    def test_unknown_fail_open(self):
        self.assertTrue(region_code_is_allowed(None))
        self.assertTrue(region_code_is_allowed(''))

    def test_serve_zone(self):
        self.assertTrue(region_code_is_allowed('US'))
        self.assertTrue(region_code_is_allowed('GB'))
        self.assertTrue(region_code_is_allowed('UK'))
        self.assertTrue(region_code_is_allowed('CA'))
        self.assertTrue(region_code_is_allowed('Canada'))
        self.assertTrue(region_code_is_allowed('France'))

    def test_outside_zone(self):
        self.assertFalse(region_code_is_allowed('IQ'))
        self.assertFalse(region_code_is_allowed('Singapore'))


class TestStoredCountryIsAllowed(unittest.TestCase):
    def test_known_allowed_skips_ip(self):
        self.assertTrue(stored_country_is_allowed('US'))
        self.assertTrue(stored_country_is_allowed('United States'))
        self.assertTrue(stored_country_is_allowed('GB'))
        self.assertTrue(stored_country_is_allowed('Germany'))
        self.assertTrue(stored_country_is_allowed('CA'))
        self.assertTrue(stored_country_is_allowed('Canada'))

    def test_unknown_or_restricted_needs_ip(self):
        self.assertFalse(stored_country_is_allowed(None))
        self.assertFalse(stored_country_is_allowed(''))
        self.assertFalse(stored_country_is_allowed('IN'))
        self.assertFalse(stored_country_is_allowed('India'))
        self.assertFalse(stored_country_is_allowed('ZA'))


class TestShouldBlockAuthForRegion(unittest.TestCase):
    def test_allowed_stored_country_never_blocks(self):
        with patch(
            'social_verification_routes.is_request_from_restricted_region',
            return_value=True,
        ) as ip_check:
            self.assertFalse(should_block_auth_for_region('US'))
            self.assertFalse(should_block_auth_for_region('United States'))
            self.assertFalse(should_block_auth_for_region('France'))
            self.assertFalse(should_block_auth_for_region('Canada'))
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
