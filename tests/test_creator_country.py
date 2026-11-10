"""Creator country: the served list, validation, regions derived from the country, and the endpoints that take it."""

import json
import unittest
from unittest.mock import patch

from services import creator_country as cc
from services.creator_country import CountryError


class ServedListTests(unittest.TestCase):
    def test_served_countries(self):
        self.assertEqual(len(cc.SERVED_COUNTRIES), 34)  # US CA GB IE AU NZ + 26 other EU + NO + CH
        for code in cc.EU_MEMBERS + ("US", "CA", "GB", "AU", "NZ", "NO", "CH"):
            self.assertIn(code, cc.SERVED_COUNTRIES)
        for code in ("IN", "BR", "AE", "PH", "IS"):
            self.assertNotIn(code, cc.SERVED_COUNTRIES)

    def test_pinned_first_then_alphabetical(self):
        order = list(cc.SERVED_COUNTRIES)
        self.assertEqual(order[:4], ["US", "GB", "CA", "AU"])
        names = [cc.SERVED_COUNTRIES[c][0] for c in order[4:]]
        self.assertEqual(names, sorted(names))

    def test_names_and_flags(self):
        self.assertEqual(cc.SERVED_COUNTRIES["GB"], ("United Kingdom", "🇬🇧"))
        self.assertEqual(cc.SERVED_COUNTRIES["IE"], ("Ireland", "🇮🇪"))
        self.assertEqual(cc.flag_for("us"), "🇺🇸")
        self.assertEqual(cc.flag_for("OTHER"), "")


class RegionsTests(unittest.TestCase):
    def test_mapping_uses_existing_vocabulary(self):
        self.assertEqual(cc.regions_for("US"), ["US"])
        self.assertEqual(cc.regions_for("GB"), ["UK"])
        self.assertEqual(cc.regions_for("CA"), ["Canada"])
        self.assertEqual(cc.regions_for("AU"), ["Australia"])
        self.assertEqual(cc.regions_for("NZ"), ["NZ"])
        for code in ("IE", "FR", "DE", "NO", "CH"):
            self.assertEqual(cc.regions_for(code), ["Europe"])
        self.assertEqual(cc.regions_for(cc.OTHER), [])

    def test_regions_json_format(self):
        self.assertEqual(cc.regions_json("GB"), '["UK"]')
        self.assertEqual(json.loads(cc.regions_json("FR")), ["Europe"])

    def test_derived_regions_match_brand_shipping(self):
        from services.brand_shipping import ships_to_country
        from services.roster_fit import brand_targets

        # A brand shipping to "Australia" must reach an AU creator; "EU" reaches Ireland but "UK" does not.
        self.assertEqual(brand_targets(regions=["Australia"])["countries"], {"AU"})
        self.assertIn("IE", brand_targets(regions=["Europe"])["countries"])
        self.assertTrue(ships_to_country(["EU"], "IE"))
        self.assertFalse(ships_to_country(["UK"], "IE"))
        self.assertTrue(ships_to_country(cc.regions_for("NO"), "NO"))

    def test_country_from_regions_only_when_unambiguous(self):
        self.assertEqual(cc.country_from_regions('["US"]'), "US")
        self.assertEqual(cc.country_from_regions(["UK"]), "GB")
        self.assertEqual(cc.country_from_regions('["Canada"]'), "CA")
        self.assertIsNone(cc.country_from_regions('["US", "Canada"]'))
        self.assertIsNone(cc.country_from_regions('["Europe"]'))
        self.assertIsNone(cc.country_from_regions(None))


class ValidationTests(unittest.TestCase):
    def test_codes_and_aliases(self):
        self.assertEqual(cc.validate("fr"), ("FR", None))
        self.assertEqual(cc.validate("UK"), ("GB", None))
        self.assertEqual(cc.validate("United Kingdom"), ("GB", None))
        self.assertEqual(cc.validate(None), (None, None))

    def test_unknown_code_rejected(self):
        for bad in ("IN", "XX", "India", "Narnia"):
            with self.assertRaises(CountryError):
                cc.validate(bad)

    def test_required(self):
        with self.assertRaises(CountryError):
            cc.validate("", required=True)

    def test_other_needs_a_name(self):
        self.assertEqual(cc.validate("OTHER", "  Brazil "), (cc.OTHER, "Brazil"))
        with self.assertRaises(CountryError):
            cc.validate("OTHER", "")
        # Typed a country we serve after all: keep the real code.
        self.assertEqual(cc.validate("other", "ireland"), ("IE", None))

    def test_other_text_is_cleaned(self):
        code, other = cc.validate("OTHER", "<b>" + "x" * 200)
        self.assertEqual(code, cc.OTHER)
        self.assertNotIn("<", other)
        self.assertLessEqual(len(other), cc.OTHER_MAX_LEN)

    def test_response_fields(self):
        self.assertEqual(cc.response_fields("GB"), {
            "country_code": "GB", "country_name": "United Kingdom", "country_flag": "🇬🇧", "country_other": None})
        self.assertEqual(cc.response_fields(None, None, "United States")["country_code"], "US")
        other = cc.response_fields("OTHER", "Brazil")
        self.assertEqual((other["country_code"], other["country_name"], other["country_other"]),
                         ("OTHER", "Brazil", "Brazil"))
        self.assertIsNone(cc.response_fields(None)["country_code"])

    def test_display_country_for_pitches(self):
        self.assertEqual(cc.display_country("GB"), "United Kingdom")
        self.assertEqual(cc.display_country("OTHER"), "")
        self.assertEqual(cc.display_country("Lyon, France"), "Lyon, France")

    def test_country_matches_falls_back_to_regions(self):
        self.assertTrue(cc.country_matches("US", "US"))
        self.assertFalse(cc.country_matches("US", "GB", '["US"]'))
        self.assertTrue(cc.country_matches("GB", None, '["UK"]'))
        self.assertTrue(cc.country_matches("FR", None, '["Europe"]'))
        self.assertTrue(cc.country_matches("CA", None, None, "Canada"))
        self.assertFalse(cc.country_matches("US", None, None))


class BackfillTests(unittest.TestCase):
    def test_decide(self):
        from scripts.backfill_creator_country import decide

        self.assertEqual(decide({"user_country": "United States", "regions": None})[0], "US")
        self.assertEqual(decide({"user_country": None, "regions": '["UK"]'})[0], "GB")
        self.assertEqual(decide({"user_country": "", "regions": '["Canada"]'})[0], "CA")
        self.assertIsNone(decide({"user_country": "Canada", "regions": '["US"]'})[0])
        self.assertIsNone(decide({"user_country": None, "regions": '["US", "UK"]'})[0])
        self.assertIsNone(decide({"user_country": "India", "regions": None})[0])
        self.assertIsNone(decide({"user_country": "France", "regions": None, "country": "FR"})[0])


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

class FakeCursor:
    """Answers the few lookups these routes make; records every statement."""

    def __init__(self):
        self.executed = []
        self._next = None
        self.rowcount = 1

    def execute(self, sql, params=None):
        s = " ".join(sql.split())
        self.executed.append((s, params))
        if s.startswith("SELECT id FROM users WHERE LOWER(email)"):
            self._next = None
        elif s.startswith("SELECT pr_list_signup"):
            self._next = {"pr_list_signup": False}
        elif "INSERT INTO users" in s:
            self._next = {"id": 901}
        elif s.startswith("SELECT id FROM users"):
            self._next = {"id": 901}
        elif s.startswith("SELECT id FROM creators WHERE user_id"):
            self._next = {"id": 701}
        elif s.startswith("UPDATE creators SET username"):
            self._next = {"id": 701}
        else:
            self._next = None

    def fetchone(self):
        return self._next

    def fetchall(self):
        return []

    def close(self):
        pass

    def statements(self, prefix):
        return [(s, p) for s, p in self.executed if s.startswith(prefix)]


class FakeConn:
    autocommit = True

    def __init__(self, cursor):
        self._cursor = cursor

    def cursor(self, cursor_factory=None):
        return self._cursor

    def commit(self):
        pass

    def rollback(self):
        pass

    def close(self):
        pass


def _cookie_sessions():
    """Signed-cookie sessions with the .sid attribute the Redis session has (register sets the SID cookie)."""
    from flask.sessions import SecureCookieSessionInterface

    class _Interface(SecureCookieSessionInterface):
        def open_session(self, app, request):
            sess = super().open_session(app, request)
            if sess is not None:
                sess.sid = "test-sid"
            return sess

    return _Interface()


class EndpointTests(unittest.TestCase):
    def setUp(self):
        import app as app_module

        self.app_module = app_module
        # Without Redis the app falls back to a null session that can't hold a login; tests use signed cookies.
        self._session_interface = app_module.app.session_interface
        app_module.app.session_interface = _cookie_sessions()
        self.client = app_module.app.test_client()
        self.cursor = FakeCursor()

    def tearDown(self):
        self.app_module.app.session_interface = self._session_interface

    def _db(self):
        return patch.object(self.app_module, "get_db_connection", return_value=FakeConn(self.cursor))

    def _login(self, **extra):
        with self.client.session_transaction() as sess:
            sess["user_id"] = 901
            sess["user_role"] = "creator"
            sess.update(extra)

    def _creator_update(self):
        rows = [(s, p) for s, p in self.cursor.statements("UPDATE creators SET country")]
        self.assertEqual(len(rows), 1, self.cursor.executed)
        return rows[0][1]

    def test_step2_accepts_country_and_derives_regions(self):
        self._login()
        with self._db():
            resp = self.client.post("/api/user/onboarding/step2", json={
                "bio": "Skincare UGC from Leeds", "primary_age_range": "25-34", "country": "GB"})
        self.assertEqual(resp.status_code, 200, resp.get_json())
        body = resp.get_json()
        self.assertEqual((body["country_code"], body["country_name"]), ("GB", "United Kingdom"))
        self.assertEqual(self._creator_update(), ("GB", None, '["UK"]', 901))
        users = self.cursor.statements("UPDATE users SET country")
        self.assertEqual(users[0][1], ("United Kingdom", 901))

    def test_step2_rejects_unknown_country(self):
        self._login()
        with self._db():
            resp = self.client.post("/api/user/onboarding/step2", json={
                "bio": "Hi there, I make UGC", "primary_age_range": "25-34", "country": "IN"})
        self.assertEqual(resp.status_code, 400)
        self.assertIn("don't support", resp.get_json()["error"])
        self.assertFalse(self.cursor.executed)

    def test_step2_other_country_keeps_users_country_untouched(self):
        self._login()
        with self._db():
            resp = self.client.post("/api/user/onboarding/step2", json={
                "bio": "Hi there, I make UGC", "primary_age_range": "25-34",
                "country": "OTHER", "country_other": "Brazil"})
        self.assertEqual(resp.status_code, 200, resp.get_json())
        self.assertEqual(self._creator_update(), ("OTHER", "Brazil", "[]", 901))
        self.assertFalse(self.cursor.statements("UPDATE users SET country"))

    def test_step2_still_accepts_regions_from_old_clients(self):
        self._login()
        with self._db():
            resp = self.client.post("/api/user/onboarding/step2", json={
                "bio": "Hi there, I make UGC", "primary_age_range": "25-34", "regions": ["US"]})
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(self.cursor.statements("UPDATE creators SET country"))

    def test_step2_requires_country_or_regions(self):
        self._login()
        with self._db():
            resp = self.client.post("/api/user/onboarding/step2", json={
                "bio": "Hi there, I make UGC", "primary_age_range": "25-34"})
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.get_json()["error"], "Please choose your country")

    def test_register_account_accepts_country(self):
        m = self.app_module
        with self._db(), \
             patch.object(m, "is_request_from_restricted_region", return_value=False), \
             patch.object(m, "email_verification_on_hold", return_value=True), \
             patch.object(m, "_record_promo_signup"), patch.object(m, "_record_signup_offer"), \
             patch.object(m, "create_access_token", return_value="tok"), patch.object(m, "set_access_cookies"):
            resp = self.client.post("/register/creator/account", json={
                "email": "maya@example.com", "password": "longenough1", "firstName": "Maya",
                "terms_accepted": True, "country": "ie"})
            self.assertEqual(resp.status_code, 201, resp.get_json())
            with self.client.session_transaction() as sess:
                self.assertEqual(sess.get("signup_country"), ["IE", None])
        self.assertEqual(self.cursor.statements("UPDATE users SET country")[0][1], ("Ireland", 901))

    def test_register_account_rejects_unknown_country(self):
        m = self.app_module
        with self._db(), patch.object(m, "is_request_from_restricted_region", return_value=False):
            resp = self.client.post("/register/creator/account", json={
                "email": "maya@example.com", "password": "longenough1", "country": "ZZ"})
        self.assertEqual(resp.status_code, 400)
        self.assertFalse(self.cursor.statements("INSERT INTO users"))

    def test_step1_applies_country_from_signup(self):
        self._login(signup_country=["CA", None],
                    onboarding_quality={"handle": "maya.ugc", "platform": "tiktok", "followers": 2100})
        with self._db(), patch.object(self.app_module, "apply_pending_oauth_to_creator"):
            resp = self.client.post("/api/user/onboarding/step1", json={
                "username": "maya.ugc", "platform": "tiktok", "followers": 2100})
        self.assertEqual(resp.status_code, 200, resp.get_json())
        self.assertEqual(self._creator_update(), ("CA", None, '["Canada"]', 901))
        with self.client.session_transaction() as sess:
            self.assertNotIn("signup_country", sess)

    def test_profile_patch_country(self):
        self._login()
        with self._db():
            resp = self.client.patch("/api/user/profile", json={"country": "NZ", "regions": ["US"]})
        self.assertEqual(resp.status_code, 200, resp.get_json())
        self.assertEqual(resp.get_json()["country_flag"], "🇳🇿")
        self.assertEqual(self._creator_update(), ("NZ", None, '["NZ"]', 901))
        # regions sent alongside a country are ignored: they come from the country.
        self.assertFalse([s for s, _ in self.cursor.executed if s.startswith("UPDATE creators SET regions")])

    def test_profile_patch_rejects_unknown_country(self):
        self._login()
        with self._db():
            resp = self.client.patch("/api/user/profile", json={"country": "Atlantis"})
        self.assertEqual(resp.status_code, 400)

    def test_google_signup_rejects_unknown_country_before_token_check(self):
        with patch.object(self.app_module, "is_request_from_restricted_region", return_value=False):
            resp = self.client.post("/google-signup", json={"idToken": "x" * 60, "country": "IN"})
        self.assertEqual(resp.status_code, 400)


class DirectorySearchCountryTests(unittest.TestCase):
    def _row(self, cid, country_raw=None, regions_raw=None):
        return {"id": cid, "username": f"user{cid}", "social_platform": "tiktok", "niche": '["skincare"]',
                "creator_niches": ["skincare"], "kit_slug": f"kit{cid}", "kit_published": True, "followers": 4200,
                "engagement": 0.04, "country_raw": country_raw, "regions_raw": regions_raw}

    def test_country_filter_uses_country_never_regions(self):
        from services.creator_directory_search import build_response

        rows = [self._row(1, "GB"), self._row(2, None, '["UK"]'), self._row(3, None, '["US"]'),
                self._row(4, None, '["Europe"]')]
        out = build_response(rows, "skincare", "tiktok", "GB", 8)
        # Only the creator who picked the UK: an old ["UK"] regions value is not a country.
        self.assertEqual(out["total_creators_found"], 1)
        card = out["creators"][0]
        self.assertEqual((card["country"], card["country_name"], card["country_flag"]),
                         ("GB", "United Kingdom", "🇬🇧"))


if __name__ == "__main__":
    unittest.main()
