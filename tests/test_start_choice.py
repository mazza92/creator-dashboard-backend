from datetime import datetime, timedelta, timezone
from unittest import mock

from services import start_choice as sc
from services.pro_trial import trial_days_left

APPROVED = datetime(2026, 11, 24, 18, 3, tzinfo=timezone.utc)


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


def test_everyone_eligible_gets_the_choice():
    assert {sc.arm_for(i) for i in range(1, 500)} == {'choice'}


def test_choice_arm_sees_screen_once_then_choice_sticks():
    cid = ids_in('choice', 1)[0]
    cur = FakeCursor({cid: creator()})
    conn = FakeConn(cur)
    assert sc.status(conn, cid, flag=True) == {'show': True, 'arm': 'choice'}
    assert sc.record_choice(conn, cid, 'free') is True
    assert sc.status(conn, cid, flag=True) == {'show': False, 'arm': 'choice'}
    # Free path keeps the trial open at checkout while they never paid.
    assert sc.trial_eligible(cur, cid) is True


def test_old_control_rows_stay_hidden():
    """Creators recorded as 'control' during the first hour of the split keep today's dashboard."""
    cur = FakeCursor({7: creator()}, table={7: {'arm': 'control', 'choice': None}})
    conn = FakeConn(cur)
    assert sc.status(conn, 7, flag=True) == {'show': False, 'arm': 'control'}
    assert sc.record_choice(conn, 7, 'trial') is False


def test_outside_the_test_nothing_is_recorded():
    cid = ids_in('choice', 1)[0]
    for c, flag in ((creator(), False),
                    (creator(approved=datetime(2026, 11, 24, 17, 0, tzinfo=timezone.utc)), True),
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


def test_trial_ramp_and_weekends():
    from services import polly_autopilot as pa
    start = datetime(2026, 11, 27, 14, tzinfo=timezone.utc)  # a Friday
    assert [pa.trial_daily_sends(start, start + timedelta(days=d, hours=1)) for d in range(7)] == \
        [5, 8, 12, 18, 20, 20, 20]
    slots = pa.next_slots(6, start, days=list(pa.ALL_DAYS), per_day=2)
    assert {s.weekday() for s in slots} & {5, 6}  # Saturday and Sunday get sends during the trial


def test_trial_settings_use_20_a_day_on_all_days():
    from services import polly_autopilot as pa
    started = datetime.now(timezone.utc) - timedelta(days=1)
    with mock.patch.object(pa, 'ensure_autopilot_tables'), \
            mock.patch.object(pa, 'trial_started_at', return_value=started):
        cur = mock.MagicMock()
        cur.fetchone.return_value = {'enabled': True, 'monthly_target': 60, 'deal_focus': 'auto',
                                     'send_days': '01234', 'followups': 2, 'send_via': 'gmail'}
        conn = mock.MagicMock()
        conn.cursor.return_value = cur
        prefs = pa.settings(conn, 5)
    assert prefs['monthly_target'] == pa.TARGET_MAX
    assert prefs['send_days'] == list(pa.ALL_DAYS)
    with mock.patch.object(pa, 'gmail_connected_at', return_value=started):
        assert pa.daily_sends(conn, 5, prefs, started + timedelta(hours=2)) == 5


def test_pro_balance_carries_trial_days_left():
    import pr_crm_routes
    started = datetime.now(timezone.utc) - timedelta(hours=2)
    cur = mock.MagicMock()
    cur.fetchone.return_value = {'unlocks_tier': None, 'unlocks_remaining': 0, 'unlocks_reset_at': None,
                                 'subscription_tier': 'pro', 'subscription_status': 'trialing',
                                 'subscription_started_at': started}
    conn = mock.MagicMock()
    conn.cursor.return_value = cur
    bal = pr_crm_routes.get_creator_unlock_balance(5, conn=conn)
    assert bal['is_unlimited'] is True and bal['trial_days_left'] == 7


def test_trial_slots_follow_the_ramp_day_by_day():
    import random
    from collections import Counter
    from services import polly_autopilot as pa
    since = datetime(2026, 11, 25, 0, 27, tzinfo=timezone.utc)
    now = datetime(2026, 11, 25, 0, 30, tzinfo=timezone.utc)
    slots = pa.trial_slots(70, now, since, {}, first=True, rng=random.Random(1))
    per_day = Counter(s.date() for s in slots)
    days = sorted(per_day)
    assert [per_day[d] for d in days[:6]] == [5, 8, 12, 18, 20, 7]
    # The first pitches go out minutes after connecting.
    assert slots[0] - now <= pa.FIRST_SEND_GAP


def test_trial_slots_count_what_a_day_already_holds():
    import random
    from collections import Counter
    from services import polly_autopilot as pa
    since = datetime(2026, 11, 25, 0, 27, tzinfo=timezone.utc)
    now = datetime(2026, 11, 25, 9, 0, tzinfo=timezone.utc)
    booked = {now.date(): 5, (now + timedelta(days=1)).date(): 6}
    slots = pa.trial_slots(10, now, since, booked, rng=random.Random(2))
    per_day = Counter(s.date() for s in slots)
    assert now.date() not in per_day            # day 1 already has its 5
    assert per_day[(now + timedelta(days=1)).date()] == 2   # day 2 cap 8, 6 booked
    assert per_day[(now + timedelta(days=2)).date()] == 8   # day 3: rest of this batch (cap 12)
    assert all(pa.SEND_WINDOW_UTC[0] <= s.hour < pa.SEND_WINDOW_UTC[1] for s in slots)
