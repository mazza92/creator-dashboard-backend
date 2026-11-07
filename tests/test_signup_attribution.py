"""First-touch attribution: cleaned keys only, saved once, only for fresh accounts, login required."""

import unittest
from unittest import mock

from flask import Flask

from routes import signup_attribution as sa


class CleanTests(unittest.TestCase):
    def test_keeps_known_keys_only(self):
        out = sa.clean({"utm_source": "tiktok", "ttclid": "E.C.P.abc", "evil": "x", "utm_campaign": "<script>",
                        "utm_id": {"a": 1}})
        self.assertEqual(out, {"utm_source": "tiktok", "ttclid": "E.C.P.abc", "utm_campaign": "script"})


class RouteTests(unittest.TestCase):
    def setUp(self):
        app = Flask(__name__)
        app.secret_key = "t"
        app.register_blueprint(sa.signup_attribution_bp)
        self.client = app.test_client()

    def test_requires_login(self):
        self.assertEqual(self.client.post("/api/me/attribution", json={"utm_source": "tiktok"}).status_code, 401)

    def test_saves_once_for_fresh_accounts(self):
        conn = mock.MagicMock()
        conn.cursor.return_value.rowcount = 1
        with self.client.session_transaction() as s:
            s["user_id"] = 7
        with mock.patch.dict("sys.modules", {"pr_crm_routes": mock.MagicMock(get_db_connection=lambda: conn)}):
            r = self.client.post("/api/me/attribution", json={"utm_source": "tiktok", "utm_id": "1881207742217314"})
        self.assertEqual(r.get_json(), {"saved": True})
        sql, params = conn.cursor.return_value.execute.call_args.args
        self.assertIn("signup_meta IS NULL", sql)
        self.assertIn("INTERVAL '3 days'", sql)
        self.assertEqual(params[1], 7)
