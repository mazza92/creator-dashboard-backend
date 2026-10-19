import unittest
from unittest.mock import patch

import public_routes
from app import app


class Cur:
    def __init__(self):
        self.sql = []

    def execute(self, sql, params=None):
        self.sql.append((" ".join(sql.split()), params))

    def fetchone(self):
        return {"id": 4528, "email": "x@example.com"}

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


class UnsubscribeTests(unittest.TestCase):
    def _call(self, method, token=None):
        conn = Conn()
        token = token or public_routes.make_unsubscribe_token("4528")
        with patch.object(public_routes, "get_db_connection", return_value=conn):
            resp = getattr(app.test_client(), method)(f"/api/public/unsubscribe?uid=4528&token={token}",
                                                      data="List-Unsubscribe=One-Click")
        return resp, conn

    def test_one_click_post_unsubscribes_with_2xx(self):
        resp, conn = self._call("post")
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(conn.committed)
        self.assertIn("SET unsubscribed_at = NOW()", conn.cur.sql[0][0])

    def test_footer_link_get_still_redirects(self):
        resp, conn = self._call("get")
        self.assertEqual(resp.status_code, 302)
        self.assertIn("/unsubscribed?status=ok", resp.headers["Location"])

    def test_bad_token_is_rejected(self):
        resp, conn = self._call("post", token="0" * 32)
        self.assertEqual(resp.status_code, 403)
        self.assertFalse(conn.committed)


if __name__ == "__main__":
    unittest.main()
