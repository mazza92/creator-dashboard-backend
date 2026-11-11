"""The lifecycle preference/unsubscribe endpoints must not trust the creator_id in the URL."""
import unittest
from unittest.mock import patch

import lifecycle_email_routes as ler
import public_routes
from app import app


class Cur:
    def __init__(self):
        self.sql = []

    def execute(self, sql, params=None):
        self.sql.append((" ".join(sql.split()), params))

    def fetchone(self):
        return None

    def close(self):
        pass


class Conn:
    def __init__(self):
        self.cur = Cur()
        self.committed = False

    def cursor(self, cursor_factory=None):
        return self.cur

    def commit(self):
        self.committed = True

    def close(self):
        pass


ME = 41
OTHER = 42


class LifecyclePrefsAuthTests(unittest.TestCase):
    def _call(self, method, path, session_creator=None, json=None):
        conn = Conn()
        with patch.object(ler, "get_db_connection", return_value=conn), \
                patch.object(ler, "_session_creator_id", return_value=session_creator):
            client = app.test_client()
            kwargs = {"json": json} if json is not None else {}
            resp = getattr(client, method)(path, **kwargs)
        return resp, conn

    # --- preferences ---------------------------------------------------------

    def test_get_prefs_requires_login(self):
        resp, conn = self._call("get", f"/api/email/preferences/{ME}")
        self.assertEqual(resp.status_code, 401)
        self.assertEqual(conn.cur.sql, [])

    def test_get_prefs_of_another_creator_is_forbidden(self):
        resp, conn = self._call("get", f"/api/email/preferences/{OTHER}", session_creator=ME)
        self.assertEqual(resp.status_code, 403)
        self.assertEqual(conn.cur.sql, [])

    def test_get_own_prefs(self):
        resp, conn = self._call("get", f"/api/email/preferences/{ME}", session_creator=ME)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(conn.cur.sql[0][1], (ME,))

    def test_get_prefs_without_path_uses_session(self):
        resp, conn = self._call("get", "/api/email/preferences", session_creator=ME)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(conn.cur.sql[0][1], (ME,))

    def test_post_prefs_requires_login(self):
        resp, conn = self._call("post", f"/api/email/preferences/{ME}", json={"timezone": "UTC"})
        self.assertEqual(resp.status_code, 401)
        self.assertFalse(conn.committed)

    def test_post_prefs_of_another_creator_is_forbidden(self):
        resp, conn = self._call("post", f"/api/email/preferences/{OTHER}", session_creator=ME,
                                json={"timezone": "UTC"})
        self.assertEqual(resp.status_code, 403)
        self.assertFalse(conn.committed)
        self.assertEqual(conn.cur.sql, [])

    def test_post_own_prefs(self):
        resp, conn = self._call("post", "/api/email/preferences", session_creator=ME,
                                json={"timezone": "Europe/Paris"})
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(conn.committed)
        params = conn.cur.sql[0][1]
        self.assertEqual(params[0], ME)
        self.assertEqual(params[-1], "Europe/Paris")

    # --- unsubscribe ---------------------------------------------------------

    def test_unsubscribe_without_token_or_login_is_401(self):
        for method in ("get", "post"):
            resp, conn = self._call(method, f"/api/email/unsubscribe/{OTHER}")
            self.assertEqual(resp.status_code, 401)
            self.assertFalse(conn.committed)

    def test_unsubscribe_another_creator_while_logged_in_is_403(self):
        resp, conn = self._call("post", f"/api/email/unsubscribe/{OTHER}", session_creator=ME)
        self.assertEqual(resp.status_code, 403)
        self.assertFalse(conn.committed)

    def test_unsubscribe_with_bad_token_is_403(self):
        resp, conn = self._call("get", f"/api/email/unsubscribe/{OTHER}?token={'0' * 32}")
        self.assertEqual(resp.status_code, 403)
        self.assertFalse(conn.committed)

    def test_token_for_one_creator_does_not_work_for_another(self):
        token = ler.make_lifecycle_unsubscribe_token(ME)
        resp, conn = self._call("get", f"/api/email/unsubscribe/{OTHER}?token={token}")
        self.assertEqual(resp.status_code, 403)
        self.assertFalse(conn.committed)

    def test_user_footer_token_is_not_a_creator_token(self):
        # /api/public/unsubscribe tokens are keyed on user id; they must not unlock this route.
        token = public_routes.make_unsubscribe_token(str(OTHER))
        resp, conn = self._call("get", f"/api/email/unsubscribe/{OTHER}?token={token}")
        self.assertEqual(resp.status_code, 403)
        self.assertFalse(conn.committed)

    def test_signed_link_unsubscribes(self):
        token = ler.make_lifecycle_unsubscribe_token(OTHER)
        resp, conn = self._call("get", f"/api/email/unsubscribe/{OTHER}?token={token}")
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(conn.committed)
        self.assertEqual(conn.cur.sql[0][1], (OTHER,))

    def test_logged_in_creator_can_unsubscribe_self(self):
        resp, conn = self._call("post", f"/api/email/unsubscribe/{ME}", session_creator=ME)
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(conn.committed)
        self.assertEqual(conn.cur.sql[0][1], (ME,))


class SessionCreatorIdTests(unittest.TestCase):
    def test_no_session_means_no_creator(self):
        with app.test_request_context("/api/email/preferences"):
            self.assertIsNone(ler._session_creator_id())


if __name__ == "__main__":
    unittest.main()
