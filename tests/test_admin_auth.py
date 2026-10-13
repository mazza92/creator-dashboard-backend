"""Admin token gate: static env token for agents, signed login token for admin pages."""

import os
import time
import unittest
from unittest.mock import patch

from flask import Flask

from services import admin_auth
from services.admin_auth import admin_auth_bp, admin_token_valid, issue_admin_token

ENV = {
    "ADMIN_TOKEN": "static-agent-token-for-tests",
    "ADMIN_PASSWORD": "correct horse battery staple",
    "JWT_SECRET_KEY": "jwt-secret-for-tests",
}


class FakeRedis:
    def __init__(self):
        self.store = {}

    def get(self, key):
        return self.store.get(key)

    def incr(self, key):
        self.store[key] = int(self.store.get(key) or 0) + 1

    def expire(self, key, ttl):
        pass


class TestAdminTokenValid(unittest.TestCase):
    def test_static_env_token_accepted(self):
        with patch.dict(os.environ, ENV):
            self.assertTrue(admin_token_valid("static-agent-token-for-tests"))

    def test_old_hardcoded_token_rejected(self):
        with patch.dict(os.environ, ENV):
            self.assertFalse(admin_token_valid("pr-hunter-admin-2026"))

    def test_empty_token_rejected_even_without_env(self):
        with patch.dict(os.environ, {"ADMIN_TOKEN": "", "JWT_SECRET_KEY": ""}):
            self.assertFalse(admin_token_valid(""))
            self.assertFalse(admin_token_valid(None))

    def test_signed_token_accepted_until_expiry(self):
        with patch.dict(os.environ, ENV):
            self.assertTrue(admin_token_valid(issue_admin_token()))
            stale = issue_admin_token(now=int(time.time()) - admin_auth.LOGIN_TTL_SECONDS - 10)
            self.assertFalse(admin_token_valid(stale))

    def test_signed_token_with_other_secret_rejected(self):
        with patch.dict(os.environ, ENV):
            token = issue_admin_token()
        with patch.dict(os.environ, {**ENV, "JWT_SECRET_KEY": "rotated"}):
            self.assertFalse(admin_token_valid(token))

    def test_user_jwt_without_admin_purpose_rejected(self):
        import jwt as pyjwt

        with patch.dict(os.environ, ENV):
            token = pyjwt.encode(
                {"sub": admin_auth.ADMIN_EMAIL, "exp": int(time.time()) + 60},
                ENV["JWT_SECRET_KEY"],
                algorithm="HS256",
            )
            if isinstance(token, bytes):
                token = token.decode()
            self.assertFalse(admin_token_valid(token))


class TestAdminLoginRoute(unittest.TestCase):
    def setUp(self):
        app = Flask(__name__)
        self.redis = FakeRedis()
        app.config["SESSION_REDIS"] = self.redis
        app.register_blueprint(admin_auth_bp)
        self.client = app.test_client()

    def _login(self, password, email="team@newcollab.co"):
        return self.client.post("/api/admin/login", json={"email": email, "password": password})

    def test_good_credentials_return_working_token(self):
        with patch.dict(os.environ, ENV):
            res = self._login(ENV["ADMIN_PASSWORD"])
            self.assertEqual(res.status_code, 200)
            body = res.get_json()
            self.assertTrue(body["success"])
            self.assertTrue(admin_token_valid(body["token"]))

    def test_bad_password_is_403_not_401(self):
        with patch.dict(os.environ, ENV):
            self.assertEqual(self._login("wrong").status_code, 403)

    def test_wrong_email_rejected(self):
        with patch.dict(os.environ, ENV):
            self.assertEqual(self._login(ENV["ADMIN_PASSWORD"], email="someone@else.co").status_code, 403)

    def test_unconfigured_password_is_503(self):
        with patch.dict(os.environ, {**ENV, "ADMIN_PASSWORD": ""}):
            self.assertEqual(self._login("").status_code, 503)

    def test_throttles_after_repeated_failures(self):
        with patch.dict(os.environ, ENV):
            for _ in range(admin_auth.MAX_FAILED_LOGINS):
                self.assertEqual(self._login("wrong").status_code, 403)
            self.assertEqual(self._login(ENV["ADMIN_PASSWORD"]).status_code, 429)


class TestCronGateIgnoresEmptySecrets(unittest.TestCase):
    def test_polly_cron_rejects_missing_secret_when_env_blank(self):
        from routes.polly import _cron_authorized

        app = Flask(__name__)
        with patch.dict(os.environ, {"CRON_SECRET": "", "ADMIN_TOKEN": ""}):
            with app.test_request_context("/cron"):
                self.assertFalse(_cron_authorized())
            with app.test_request_context("/cron", headers={"X-Admin-Token": "pr-hunter-admin-2026"}):
                self.assertFalse(_cron_authorized())

    def test_polly_cron_accepts_env_token(self):
        from routes.polly import _cron_authorized

        app = Flask(__name__)
        with patch.dict(os.environ, {"CRON_SECRET": "", "ADMIN_TOKEN": "agent-token"}):
            with app.test_request_context("/cron", headers={"X-Admin-Token": "agent-token"}):
                self.assertTrue(_cron_authorized())


if __name__ == "__main__":
    unittest.main()
