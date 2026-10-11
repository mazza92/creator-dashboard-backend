import unittest
from unittest.mock import patch

import app as app_module
from app import app, _placeholder_username


class FakeCursor:
    def __init__(self, taken=(), user=None, creator=None):
        self.taken = {t.lower() for t in taken}
        self.user = user
        self.creator = creator
        self.executed = []
        self._next = None

    def execute(self, sql, params=None):
        self.executed.append((sql, params))
        s = ' '.join(sql.split())
        if s.startswith('SELECT 1 FROM creators WHERE LOWER(username)'):
            self._next = {'?column?': 1} if params[0].lower() in self.taken else None
        elif s.startswith('SELECT id, email, role FROM users'):
            self._next = self.user
        elif s.startswith('SELECT id, username, niche, approval_status FROM creators'):
            self._next = self.creator
        elif 'INSERT INTO users' in s:
            self._next = {'id': 900}
        elif 'INSERT INTO creators' in s:
            self.taken.add(params[1].lower())
            self._next = {'id': 700}
        else:
            self._next = None

    def fetchone(self):
        return self._next


class FakeConn:
    def __init__(self, cursor):
        self._cursor = cursor
        self.committed = False

    def cursor(self, cursor_factory=None):
        return self._cursor

    def commit(self):
        self.committed = True

    def rollback(self):
        pass

    def close(self):
        pass


class PlaceholderUsernameTests(unittest.TestCase):
    def test_uses_email_prefix_when_free(self):
        self.assertEqual(_placeholder_username(FakeCursor(), 'Sarah.M@gmail.com'), 'sarah.m')

    def test_adds_suffix_when_prefix_taken(self):
        name = _placeholder_username(FakeCursor(taken=['sarah']), 'sarah@gmail.com')
        self.assertTrue(name.startswith('sarah_'))
        self.assertNotEqual(name, 'sarah')

    def test_strips_characters_handles_cannot_have(self):
        self.assertEqual(_placeholder_username(FakeCursor(), 'john+promo@x.com'), 'johnpromo')


class GoogleSignupRouteTests(unittest.TestCase):
    def _post(self, cursor):
        conn = FakeConn(cursor)
        idinfo = {'iss': 'https://securetoken.google.com/auth-app-feed3', 'aud': 'auth-app-feed3',
                  'email': 'Sarah@gmail.com'}
        with patch.object(app_module, 'is_request_from_restricted_region', return_value=False), \
             patch.object(app_module.id_token, 'verify_firebase_token', return_value=idinfo), \
             patch.object(app_module, 'get_db_connection', return_value=conn), \
             patch.object(app_module, '_establish_auth_session'):
            resp = app.test_client().post('/google-signup', json={
                'idToken': 'x' * 60, 'email': 'sarah@gmail.com', 'name': 'Sarah M',
            })
        return resp, conn

    def test_new_user_with_taken_prefix_still_signs_up(self):
        cursor = FakeCursor(taken=['sarah'])
        resp, conn = self._post(cursor)
        self.assertEqual(resp.status_code, 201, resp.get_json())
        self.assertTrue(conn.committed)
        inserted = [p for s, p in cursor.executed if 'INSERT INTO creators' in s][0]
        self.assertNotEqual(inserted[1], 'sarah')
        self.assertEqual(resp.get_json()['redirect_url'], '/onboarding')

    def test_email_signup_without_creator_row_goes_to_onboarding(self):
        cursor = FakeCursor(user={'id': 42, 'email': 'sarah@gmail.com', 'role': 'creator'}, creator=None)
        resp, conn = self._post(cursor)
        self.assertEqual(resp.status_code, 200, resp.get_json())
        body = resp.get_json()
        self.assertEqual(body['redirect_url'], '/onboarding')
        self.assertEqual(body['approval_status'], 'pending')
        self.assertEqual(body['creator_id'], 700)
        self.assertTrue(conn.committed)


if __name__ == '__main__':
    unittest.main()
