"""Paid gigs older than 20 days are stale."""

from datetime import datetime, timedelta, timezone

from services.gig_freshness import FRESH_DAYS, gig_posted_too_old, posted_too_old


NOW = datetime(2026, 10, 16, 12, 0, tzinfo=timezone.utc)


def test_fresh_window_is_20_days():
    assert FRESH_DAYS == 20


def test_iso_date_older_than_20_days_is_stale():
    assert posted_too_old("2026-09-20T08:00:00Z", NOW) is True


def test_iso_date_inside_the_window_stays():
    assert posted_too_old("2026-10-01 09:00:00", NOW) is False


def test_relative_age_is_stale_past_20_days():
    assert posted_too_old("21 days ago", NOW) is True
    assert posted_too_old("3 days ago", NOW) is False


def test_missing_or_unknown_date_is_kept():
    assert posted_too_old(None, NOW) is False
    assert posted_too_old("yesterday-ish", NOW) is False


def test_gig_uses_the_source_posted_date():
    assert gig_posted_too_old({"posted_at": (NOW - timedelta(days=21)).isoformat()}, NOW) is True
    assert gig_posted_too_old({"title": "UGC creator"}, NOW) is False
