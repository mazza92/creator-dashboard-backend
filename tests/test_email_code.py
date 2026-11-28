"""Email verification by 6-digit code at signup (services.email_code + routes in app.py)."""
from datetime import timedelta
import unittest
from unittest.mock import patch

from services import email_code as ec
from tests.test_creator_country import FakeConn, FakeCursor, _cookie_sessions


class MemCursor:
    """The email_codes table in memory: just the statements services.email_code runs."""

    def __init__(self, db):
        self.db, self.last = db, None

    def execute(self, sql, params=None):
        s = " ".join(sql.split())
        self.last = None
        if s.startswith("CREATE TABLE"):
            return
        if s.startswith("SELECT email, code_hash"):
            row = self.db["codes"].get(params[0])
            self.last = dict(row) if row else None
        elif s.startswith("INSERT INTO email_codes"):
            uid, email, code_hash, expires, sent, window, sends = params
            self.db["codes"][uid] = {"email": email, "code_hash": code_hash, "expires_at": expires, "tries": 0,
                                     "sent_at": sent, "window_start": window, "sends_in_window": sends,
                                     "verified_at": (self.db["codes"].get(uid) or {}).get("verified_at")}
        elif s.startswith("UPDATE email_codes SET tries"):
            self.db["codes"][params[0]]["tries"] += 1
        elif s.startswith("UPDATE email_codes SET verified_at"):
            self.db["codes"][params[0]]["verified_at"] = ec._now()
        elif s.startswith("UPDATE users SET is_verified"):
            self.db["verified"].add(params[0])
        else:
            raise AssertionError(s)

    def fetchone(self):
        return self.last


class MemConn:
    def __init__(self):
        self.db = {"codes": {}, "verified": set()}

    def cursor(self, *a, **k):
        return MemCursor(self.db)

    def commit(self):
        pass

    def rollback(self):
        pass


def issued(conn, uid=7, code="123456", **kw):
    with patch.object(ec, "new_code", return_value=code), patch.object(ec, "_send", return_value=True) as send:
        out = ec.issue(conn, uid, "maya@example.com", "Maya", **kw)
    return out, send


def test_code_sent_then_right_code_verifies_the_account():
    conn = MemConn()
    out, send = issued(conn, force=True)
    assert out["sent"] and send.call_args[0][:2] == ("maya@example.com", "123456")
    assert ec.pending(conn, 7)
    assert ec.check(conn, 7, "123 456") == "ok"        # spaces / formatting ignored
    assert 7 in conn.db["verified"] and not ec.pending(conn, 7)


def test_code_is_stored_hashed_never_in_clear():
    conn = MemConn()
    issued(conn)
    assert "123456" not in conn.db["codes"][7]["code_hash"]


def test_wrong_code_counts_and_locks_after_five_tries():
    conn = MemConn()
    issued(conn)
    for _ in range(4):
        assert ec.check(conn, 7, "000000") == "wrong"
    assert ec.check(conn, 7, "000000") == "too_many"
    assert ec.check(conn, 7, "123456") == "too_many"   # even the right one, until a new code
    assert ec.pending(conn, 7)


def test_expired_code_refused():
    conn = MemConn()
    issued(conn)
    conn.db["codes"][7]["expires_at"] = ec._now() - timedelta(seconds=1)
    assert ec.check(conn, 7, "123456") == "expired"


def test_resend_cooldown_and_hourly_limit():
    conn = MemConn()
    issued(conn, force=True)
    out, send = issued(conn, code="222222")
    assert not out["sent"] and out["reason"] == "cooldown" and not send.called
    for i in range(4):
        conn.db["codes"][7]["sent_at"] = ec._now() - timedelta(minutes=1)
        out, _ = issued(conn, code=f"33333{i}")
        assert out["sent"], out
    conn.db["codes"][7]["sent_at"] = ec._now() - timedelta(minutes=1)
    out, _ = issued(conn, code="444444")
    assert out["reason"] == "hourly_limit"


def test_new_code_replaces_the_old_one_and_resets_tries():
    conn = MemConn()
    issued(conn, force=True)
    ec.check(conn, 7, "000000")
    issued(conn, code="654321", force=True)
    assert conn.db["codes"][7]["tries"] == 0
    assert ec.check(conn, 7, "123456") == "wrong"
    assert ec.check(conn, 7, "654321") == "ok"


def test_accounts_never_issued_a_code_are_not_gated():
    assert not ec.pending(MemConn(), 7)          # older accounts, Google sign-ups
    assert not ec.pending(MemConn(), None)


def test_email_names_the_code():
    html = ec.email_html("123456", "Maya")
    assert "123456" in html and "Hi Maya" in html and "10 minutes" in html


class Routes(unittest.TestCase):
    def setUp(self):
        import app as app_module

        self.app_module = app_module
        self._session_interface = app_module.app.session_interface
        app_module.app.session_interface = _cookie_sessions()
        self.client = app_module.app.test_client()
        self.cursor = FakeCursor()

    def tearDown(self):
        self.app_module.app.session_interface = self._session_interface

    def _db(self):
        return patch.object(self.app_module, "get_db_connection", return_value=FakeConn(self.cursor))

    def _login(self):
        with self.client.session_transaction() as sess:
            sess["user_id"] = 901
            sess["user_role"] = "creator"

    def _signup(self, sent):
        m = self.app_module
        with self._db(), \
             patch.object(m, "is_request_from_restricted_region", return_value=False), \
             patch.object(m, "_record_promo_signup"), patch.object(m, "_record_signup_offer"), \
             patch.object(m, "_email_codes_exist", return_value=True), \
             patch("services.email_code.issue", return_value={"sent": sent}) as issue, \
             patch.object(m, "create_access_token", return_value="tok"), patch.object(m, "set_access_cookies"):
            resp = self.client.post("/register/creator/account", json={
                "email": "maya@example.com", "password": "longenough1", "firstName": "Maya",
                "terms_accepted": True})
        return resp, issue

    def test_signup_sends_a_code_and_goes_to_the_code_screen(self):
        resp, issue = self._signup(sent=True)
        self.assertEqual(resp.status_code, 201, resp.get_json())
        body = resp.get_json()
        self.assertTrue(body["redirect_url"].endswith("/verify-code"))
        self.assertFalse(body["email_verified"])
        self.assertEqual(issue.call_args.kwargs.get("force"), True)
        self.assertFalse(self.cursor.statements("UPDATE users SET is_verified"))

    def test_signup_goes_through_when_the_code_email_fails(self):
        resp, _ = self._signup(sent=False)
        body = resp.get_json()
        self.assertTrue(body["redirect_url"].endswith("/onboarding"))
        self.assertTrue(body["email_verified"])
        self.assertTrue(self.cursor.statements("UPDATE users SET is_verified = true"))

    def test_onboarding_waits_for_the_code(self):
        self._login()
        with self._db(), patch.object(self.app_module, "_email_code_pending", return_value=True):
            step1 = self.client.post("/api/user/onboarding/step1", json={"username": "maya", "platform": "tiktok"})
            scrape = self.client.post("/api/user/onboarding/scrape", json={"handle": "maya", "platform": "tiktok"})
        self.assertEqual(step1.status_code, 403)
        self.assertEqual(step1.get_json()["error"], "email_code_required")
        self.assertEqual(scrape.status_code, 403)

    def test_verify_route(self):
        self._login()
        with self._db(), patch("services.email_code.check", return_value="ok"):
            ok = self.client.post("/api/auth/email-code/verify", json={"code": "123456"})
        self.assertEqual(ok.status_code, 200)
        self.assertEqual(ok.get_json()["redirect_url"], "/onboarding")
        with self._db(), patch("services.email_code.check", return_value="wrong"):
            bad = self.client.post("/api/auth/email-code/verify", json={"code": "000000"})
        self.assertEqual(bad.status_code, 400)
        self.assertIn("isn't right", bad.get_json()["message"])

    def test_verify_requires_login(self):
        resp = self.client.post("/api/auth/email-code/verify", json={"code": "123456"})
        self.assertEqual(resp.status_code, 401)
