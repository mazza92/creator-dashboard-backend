import unittest
from unittest.mock import MagicMock


class AdminCountryFilter(unittest.TestCase):
    def test_codes_names_other_none(self):
        from routes.admin_creators import _country_codes
        self.assertEqual(_country_codes("FR,United Kingdom,other,none"), ["FR", "GB", "OTHER", "NONE"])
        self.assertEqual(_country_codes("Mars"), [])

    def test_country_fields(self):
        from routes.admin_creators import _country_fields
        self.assertEqual(_country_fields({"country": "FR"})["country_name"], "France")
        self.assertIsNone(_country_fields({"country": None})["country_code"])


class RosterCountry(unittest.TestCase):
    def test_picked_country_beats_shipping_and_users_country(self):
        from brand_pr_roster_routes import _creator_country
        self.assertEqual(_creator_country({"creator_country": "FR", "user_country": "United States"},
                                          {"country": "Canada"}), ("France", "fr"))

    def test_falls_back_when_not_picked(self):
        from brand_pr_roster_routes import _creator_country
        self.assertEqual(_creator_country({"creator_country": None, "user_country": "United States"}, {})[1], "us")
        self.assertEqual(_creator_country({"creator_country": "OTHER", "user_country": "Canada"}, {})[1], "ca")


class SearchFilterSql(unittest.TestCase):
    def test_no_regions_in_country_filter(self):
        from services.creator_country import COUNTRY_FILTER_SQL, country_filter_params
        self.assertNotIn("regions", COUNTRY_FILTER_SQL)
        self.assertEqual(COUNTRY_FILTER_SQL.count("%s"), len(country_filter_params("FR")))


if __name__ == "__main__":
    unittest.main()
