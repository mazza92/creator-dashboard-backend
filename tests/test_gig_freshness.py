"""Paid gigs older than 10 days are stale (Nov 2026: was 20)."""

from datetime import datetime, timedelta, timezone

from services.gig_freshness import FRESH_DAYS, gig_posted_too_old, posted_too_old


NOW = datetime(2026, 10, 16, 12, 0, tzinfo=timezone.utc)


def test_fresh_window_is_10_days():
    assert FRESH_DAYS == 10


def test_iso_date_older_than_10_days_is_stale():
    assert posted_too_old("2026-10-05T08:00:00Z", NOW) is True


def test_iso_date_inside_the_window_stays():
    assert posted_too_old("2026-10-10 09:00:00", NOW) is False


def test_relative_age_is_stale_past_10_days():
    assert posted_too_old("11 days ago", NOW) is True
    assert posted_too_old("3 days ago", NOW) is False


def test_missing_or_unknown_date_is_kept():
    assert posted_too_old(None, NOW) is False
    assert posted_too_old("yesterday-ish", NOW) is False


def test_gig_uses_the_source_posted_date():
    assert gig_posted_too_old({"posted_at": (NOW - timedelta(days=11)).isoformat()}, NOW) is True
    assert gig_posted_too_old({"title": "UGC creator"}, NOW) is False


def test_parse_gig_deadline_keeps_real_dates_and_drops_text():
    from datetime import date, datetime, timezone
    from services.gig_freshness import parse_gig_deadline
    now = datetime(2026, 10, 25, tzinfo=timezone.utc)
    assert parse_gig_deadline("2026-11-15", now) == date(2026, 11, 15)
    assert parse_gig_deadline("Nov 30, 2026", now) == date(2026, 11, 30)
    for junk in ("ASAP", "Rolling", "Ongoing until filled", "", None, "2025-01-01", "2099-01-01"):
        assert parse_gig_deadline(junk, now) is None, junk


def test_listing_filter_only_ages_out_scanner_gigs():
    from services.gig_freshness import fresh_gig_sql
    sql = fresh_gig_sql("o")
    assert "o.additional_notes NOT ILIKE" in sql and "INTERVAL '10 days'" in sql


def test_pause_job_uses_the_window_and_tags_it():
    from services.gig_freshness import pause_stale_paid_gigs

    class Cur:
        def execute(self, sql, params):
            self.sql, self.params = sql, params
        def fetchall(self):
            return [(1,), (2,)]
        def close(self):
            pass

    class Conn:
        def __init__(self):
            self.cur = Cur()
        def cursor(self):
            return self.cur
        def commit(self):
            pass

    conn = Conn()
    assert pause_stale_paid_gigs(conn) == 2
    assert conn.cur.params == ("10", 10)
    assert "recycled=stale_" in conn.cur.sql
