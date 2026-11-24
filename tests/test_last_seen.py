from services.last_seen import should_touch, touch_last_seen, SEEN_EVERY_SECONDS


class _Cur:
    def __init__(self, log, fail=False):
        self.log, self.fail = log, fail

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def execute(self, sql, params):
        if self.fail:
            raise RuntimeError('no column')
        self.log.append((sql, params))


class _Conn:
    autocommit = True

    def __init__(self, log, fail=False):
        self.log, self.fail, self.closed = log, fail, False

    def cursor(self):
        return _Cur(self.log, self.fail)

    def close(self):
        self.closed = True


def test_signed_out_never_touches():
    assert should_touch({}) is False
    log = []
    assert touch_last_seen({}, lambda: _Conn(log)) is False
    assert log == []


def test_stamps_once_per_hour():
    s, log = {'user_id': 7}, []
    assert touch_last_seen(s, lambda: _Conn(log), now=1000) is True
    assert log == [("UPDATE users SET last_seen_at = NOW() WHERE id = %s", (7,))]
    assert touch_last_seen(s, lambda: _Conn(log), now=1000 + SEEN_EVERY_SECONDS - 1) is False
    assert len(log) == 1
    assert touch_last_seen(s, lambda: _Conn(log), now=1000 + SEEN_EVERY_SECONDS) is True
    assert len(log) == 2


def test_db_failure_is_swallowed_and_backs_off():
    s = {'user_id': 7}
    assert touch_last_seen(s, lambda: _Conn([], fail=True), now=5000) is False
    assert should_touch(s, now=5001) is False


def test_bad_session_value_treated_as_never():
    assert should_touch({'user_id': 1, 'last_seen_ts': 'x'}, now=10000) is True
