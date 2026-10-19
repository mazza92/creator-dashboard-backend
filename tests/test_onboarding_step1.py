import unittest
from unittest.mock import patch

import app as app_module
from app import app


class AutocommitCursor:
    """Behaves like the shared autocommit connection: SAVEPOINT outside a transaction errors."""

    def __init__(self):
        self.executed = []
        self._next = None

    def execute(self, sql, params=None):
        s = ' '.join(sql.split())
        self.executed.append(s)
        if s.startswith('SAVEPOINT') or s.startswith('ROLLBACK TO SAVEPOINT'):
            raise Exception('SAVEPOINT can only be used in transaction blocks')
        if s.startswith('SELECT id FROM users'):
            self._next = {'id': 5338}
        elif s.startswith('SELECT id FROM creators WHERE user_id'):
            self._next = {'id': 3860}
        elif s.startswith('UPDATE creators SET username'):
            self._next = {'id': 3860}
        else:
            self._next = None

    def fetchone(self):
        return self._next


class AutocommitConn:
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


class OnboardingStep1Tests(unittest.TestCase):
    def test_step1_succeeds_on_autocommit_connection(self):
        cursor = AutocommitCursor()
        client = app.test_client()
        with client.session_transaction() as sess:
            sess['user_id'] = 5338
            sess['onboarding_quality'] = {'handle': 'left.the.room', 'platform': 'tiktok', 'followers': 1200}
        with patch.object(app_module, 'get_db_connection', return_value=AutocommitConn(cursor)), \
             patch.object(app_module, 'apply_pending_oauth_to_creator'):
            resp = client.post('/api/user/onboarding/step1', json={
                'username': 'left.the.room', 'platform': 'tiktok', 'followers': 1200,
            })
        self.assertEqual(resp.status_code, 200, resp.get_json())
        self.assertEqual(resp.get_json()['creator_id'], 3860)
        self.assertFalse([s for s in cursor.executed if 'SAVEPOINT' in s])


if __name__ == '__main__':
    unittest.main()
