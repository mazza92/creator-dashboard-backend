"""POST /login tells Google-only accounts to use Google, and keeps the generic error for everyone else."""

import unittest
from unittest.mock import MagicMock, patch

import bcrypt

import app as app_module
from app import app


def _conn(rows):
    cur = MagicMock()
    cur.fetchall.return_value = rows
    conn = MagicMock()
    conn.cursor.return_value = cur
    return conn


class LoginGoogleAccountTests(unittest.TestCase):
    def setUp(self):
        self.client = app.test_client()

    def _post(self, rows, password='whatever'):
        with patch.object(app_module, 'get_db_connection', return_value=_conn(rows)):
            return self.client.post('/login', json={'email': 'a@b.co', 'password': password})

    def test_google_only_account_gets_google_hint(self):
        r = self._post([{'id': 5, 'password': 'GOOGLE_AUTH_abc', 'role': 'creator', 'country': 'US'}])
        self.assertEqual(r.status_code, 401)
        self.assertEqual(r.get_json()['code'], 'google_account')
        self.assertIn('Google', r.get_json()['error'])

    def test_wrong_password_keeps_generic_error(self):
        real = bcrypt.hashpw(b'right', bcrypt.gensalt(4)).decode()
        r = self._post([{'id': 6, 'password': real, 'role': 'creator', 'country': 'US'}], password='wrong')
        self.assertEqual(r.status_code, 401)
        self.assertEqual(r.get_json(), {'error': 'Invalid email or password'})

    def test_unknown_email_keeps_generic_error(self):
        r = self._post([])
        self.assertEqual(r.status_code, 401)
        self.assertNotIn('code', r.get_json())

    def test_session_lasts_30_days(self):
        self.assertEqual(app.config['PERMANENT_SESSION_LIFETIME'].days, 30)


if __name__ == '__main__':
    unittest.main()
