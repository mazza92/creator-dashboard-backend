from datetime import datetime, timedelta, timezone
from unittest import mock

from services import start_choice as sc
from services.pro_trial import trial_days_left

APPROVED = datetime(2026, 11, 26, tzinfo=timezone.utc)


class FakeCursor:
    """Answers the few queries start_choice runs, from a dict of creators and a recorded-choice table."""

    def __init__(self, creators, table=None):
        self.creators, self.table, self.last = creators, table, None

    def execute(self, sql, params=None):
        s = ' '.join(sql.split())
        if 'to_regclass' in s:
            self.last = [('creator_start_choice' if self.table is not None else None,)]
        elif s.startswith('SELECT arm, choice'):
            row = (self.table or {}).get(params[0])
            self.last = [(row['arm'], row['choice'])] if row else []
        elif s.startswith('SELECT approval_status'):
            c = self.creators.get(params[0])
            self.last = [c] if c else []
        elif s.startswith('CREATE TABLE'):
            if self.table is None:
                self.table = {}
            self.last = []
        elif s.startswith('INSERT INTO creator_start_choice'):
            self.table.setdefault(params[0], {'arm': params[1], 'choice': None})
            self.last = []
        elif s.startswith('UPDATE creator_start_choice'):
            self.table[params[1]]['choice'] = params[0]
            self.last = []
        else:
            raise AssertionError(s)

    def fetchone(self):
        return self.last[0] if self.last else None


class FakeConn:
    def __init__(self, cur):
        self.cur = cur

    def cursor(self):
        return self.cur

    def commit(self):
        pass


def creator(approved=APPROVED, paid=False, status='approved'):
    return (status, approved, 'sub_1' if paid else None, None, None)


def ids_in(arm, n=40):
    return [i for i in range(1, 400) if sc.arm_for(i) == arm][:n]


def test_split_is_roughly_half_and_stable():
    arms = [sc.arm_for(i) for i in range(1, 2001)]
    share = arms.count('choice') / len(arms)
    assert 0.45 < share < 0.55
    assert sc.arm_for(123) == sc.arm_for(123)


def test_choice_arm_sees_screen_once_then_choice_sticks():
    cid = ids_in('choice', 1)[0]
    cur = FakeCursor({cid: creator()})
    conn = FakeConn(cur)
    assert sc.status(conn, cid, flag=True) == {'show': True, 'arm': 'choice'}
    assert sc.record_choice(conn, cid, 'free') is True
    assert sc.status(conn, cid, flag=True) == {'show': False, 'arm': 'choice'}
    # Free path keeps the trial open at checkout while they never paid.
    assert sc.trial_eligible(cur, cid) is True


def test_control_arm_is_recorded_but_never_shown_or_offered_the_trial():
    cid = ids_in('control', 1)[0]
    cur = FakeCursor({cid: creator()})
    conn = FakeConn(cur)
    assert sc.status(conn, cid, flag=True) == {'show': False, 'arm': 'control'}
    assert cur.table[cid]['arm'] == 'control'
    assert sc.record_choice(conn, cid, 'trial') is False
    assert sc.trial_eligible(cur, cid) is False


def test_outside_the_test_nothing_is_recorded():
    cid = ids_in('choice', 1)[0]
    for c, flag in ((creator(), False),
                    (creator(approved=datetime(2026, 11, 20, tzinfo=timezone.utc)), True),
                    (creator(paid=True), True),
                    (creator(status='pending'), True)):
        cur = FakeCursor({cid: c})
        assert sc.status(FakeConn(cur), cid, flag=flag) == {'show': False, 'arm': None}
        assert cur.table is None


def test_unknown_choice_rejected():
    cid = ids_in('choice', 1)[0]
    cur = FakeCursor({cid: creator()})
    conn = FakeConn(cur)
    sc.status(conn, cid, flag=True)
    assert sc.record_choice(conn, cid, 'yearly') is False


def test_checkout_offers_trial_to_choice_arm():
    from services import quota_trial_test as qt
    with mock.patch('services.creator_offers.has_trial_offer', return_value=False), \
            mock.patch.object(qt, 'outreach_trial_eligible', return_value=False), \
            mock.patch.object(qt, 'trial_eligible', return_value=False), \
            mock.patch('services.start_choice.trial_eligible', return_value=True):
        assert qt.checkout_trial_eligible(object(), 1) is True


def test_trial_days_left():
    now = datetime(2026, 11, 26, 12, tzinfo=timezone.utc)
    assert trial_days_left('trialing', now - timedelta(hours=1), now) == 7
    assert trial_days_left('trialing', now - timedelta(days=6, hours=20), now) == 1
    assert trial_days_left('trialing', now - timedelta(days=8), now) is None
    assert trial_days_left('active', now, now) is None
