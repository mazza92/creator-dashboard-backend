"""Nov 25 2026: three Pro creators sent nothing on a Wednesday while 55 pitches waited on later days.
Pitches approved one at a time were booked "after the last booking", so whole send days stayed empty.
Now each send day fills up to its daily number, and the send cron pulls later pitches into today's gaps."""
import random
from datetime import date, datetime, timezone
from unittest import mock

from services import autopilot_delivery as ad
from services import polly_autopilot as pa

WED_MORNING = datetime(2026, 11, 25, 9, 0, tzinfo=timezone.utc)   # before the 13-21 UTC window
WED_AFTERNOON = datetime(2026, 11, 25, 15, 0, tzinfo=timezone.utc)
WEEKDAYS = [0, 1, 2, 3, 4]


def _by_day(slots):
    out = {}
    for s in slots:
        out[s.date()] = out.get(s.date(), 0) + 1
    return out


def test_fills_each_send_day_up_to_the_daily_number():
    slots = pa.capacity_slots(20, WED_MORNING, WEEKDAYS, 7, {}, rng=random.Random(1))
    days = _by_day(slots)
    assert days[date(2026, 11, 25)] == 7  # today is used
    assert days[date(2026, 11, 26)] == 7 and days[date(2026, 11, 27)] == 6
    assert date(2026, 11, 28) not in days and date(2026, 11, 29) not in days  # weekend
    assert all(13 <= s.hour < 21 for s in slots)


def test_one_at_a_time_approvals_never_leave_a_day_empty():
    """The old bug: approving single pitches back to back skipped days. Simulate 15 single approvals."""
    booked = {}
    rng = random.Random(7)
    for _ in range(15):
        [slot] = pa.capacity_slots(1, WED_MORNING, WEEKDAYS, 5, booked, rng=rng)
        booked[slot.date()] = booked.get(slot.date(), 0) + 1
    assert booked == {date(2026, 11, 25): 5, date(2026, 11, 26): 5, date(2026, 11, 27): 5}


def test_counts_what_a_day_already_holds_and_only_future_minutes_today():
    slots = pa.capacity_slots(3, WED_AFTERNOON, WEEKDAYS, 4, {date(2026, 11, 25): 3}, rng=random.Random(2))
    days = _by_day(slots)
    assert days == {date(2026, 11, 25): 1, date(2026, 11, 26): 2}
    assert min(slots) > WED_AFTERNOON


def test_after_the_window_today_is_skipped():
    late = datetime(2026, 11, 25, 22, 0, tzinfo=timezone.utc)
    slots = pa.capacity_slots(2, late, WEEKDAYS, 5, {}, rng=random.Random(3))
    assert all(s.date() == date(2026, 11, 26) for s in slots)


class _Cur:
    def __init__(self, later_ids):
        self.later_ids, self.updates, self.last = later_ids, [], []

    def execute(self, sql, params=None):
        s = " ".join(sql.split())
        if s.startswith("SELECT id FROM polly_autopilot_queue"):
            self.last = [{"id": i} for i in self.later_ids[:params[2]]]
        elif s.startswith("UPDATE polly_autopilot_queue SET scheduled_for"):
            self.updates.append(params)
        else:
            raise AssertionError(s)

    def fetchall(self):
        return self.last


class _Conn:
    def __init__(self, cur):
        self.cur = cur

    def commit(self):
        pass


def test_pull_forward_fills_todays_gap_from_later_days():
    cur = _Cur([11, 12, 13, 14])
    with mock.patch.object(pa, "settings", return_value={"send_days": WEEKDAYS}), \
            mock.patch.object(pa, "daily_sends", return_value=5), \
            mock.patch.object(pa, "booked_per_day", return_value={date(2026, 11, 25): 2}), \
            mock.patch.object(pa, "_cursor", return_value=cur):
        moved = pa.pull_forward(_Conn(cur), 7, WED_AFTERNOON)
    assert moved == 3
    assert [p[1] for p in cur.updates] == [11, 12, 13]
    assert all(p[0].date() == date(2026, 11, 25) and p[0] > WED_AFTERNOON for p in cur.updates)


def test_pull_forward_does_nothing_on_a_non_send_day_or_full_day():
    cur = _Cur([11])
    with mock.patch.object(pa, "settings", return_value={"send_days": [0, 1]}), \
            mock.patch.object(pa, "_cursor", return_value=cur):
        assert pa.pull_forward(_Conn(cur), 7, WED_AFTERNOON) == 0
    with mock.patch.object(pa, "settings", return_value={"send_days": WEEKDAYS}), \
            mock.patch.object(pa, "daily_sends", return_value=5), \
            mock.patch.object(pa, "booked_per_day", return_value={date(2026, 11, 25): 5}), \
            mock.patch.object(pa, "_cursor", return_value=cur):
        assert pa.pull_forward(_Conn(cur), 7, WED_AFTERNOON) == 0
    assert cur.updates == []


class _DCur:
    def __init__(self, target):
        self.target, self.last = target, None

    def execute(self, sql, params=None):
        s = " ".join(sql.split())
        if s.startswith("SELECT error"):
            self.last = None
        elif s.startswith("SELECT COUNT(*) FILTER"):
            self.last = {"later": 0, "queued": 0}
        elif s.startswith("SELECT monthly_target"):
            self.last = {"monthly_target": self.target}

    def fetchone(self):
        return self.last


class _DConn:
    def __init__(self, cur):
        self.c = cur

    def cursor(self, cursor_factory=None):
        return self.c


def test_target_reached_is_not_a_miss():
    with mock.patch("services.polly_autopilot.month_counts", return_value={"sent": 60}):
        c = ad._cause(_DConn(_DCur(24)), 3264, date(2026, 11, 25), None)
    assert c["cause"] == "target_reached"
    with mock.patch("services.polly_autopilot.month_counts", return_value={"sent": 3}):
        c = ad._cause(_DConn(_DCur(24)), 3264, date(2026, 11, 25), None)
    assert c["cause"] == "no_brands"
