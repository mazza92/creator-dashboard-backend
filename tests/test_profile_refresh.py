from datetime import datetime
from unittest.mock import MagicMock, patch

import social_verification_routes as svr
from services import profile_refresh as pr


class _Cur:
    def __init__(self, rows):
        self.rows = list(rows)
        self.sql = []
        self.rowcount = 1

    def execute(self, sql, params=None):
        self.sql.append((sql, params))

    def fetchone(self):
        return self.rows.pop(0) if self.rows else None

    def fetchall(self):
        return self.rows

    def close(self):
        pass


class _Conn:
    def __init__(self, *cursors):
        self.cursors = list(cursors)
        self.commits = 0

    def cursor(self, **_):
        return self.cursors.pop(0) if len(self.cursors) > 1 else self.cursors[0]

    def commit(self):
        self.commits += 1

    def rollback(self):
        pass

    def close(self):
        pass


def test_reconnect_falls_back_to_the_scraped_handle():
    conn = _Conn(_Cur([(None, "bhawna", None, 3378)]), _Cur([("bhawnaa.choudhary",)]))
    with patch.object(svr, "get_db_connection", return_value=conn):
        assert svr._lookup_creator_instagram_handle(creator_id=2385) == "bhawnaa.choudhary"


def test_reconnect_keeps_the_creator_handle_when_present():
    conn = _Conn(_Cur([("real.handle", "x", "instagram", 1)]))
    with patch.object(svr, "get_db_connection", return_value=conn):
        assert svr._lookup_creator_instagram_handle(creator_id=1) == "real.handle"


def test_backfill_only_fills_empty_handles_for_one_user():
    cur = _Cur([])
    pr.backfill_social_handle(cur, 3378)
    sql, params = cur.sql[0]
    assert "COALESCE(TRIM(c.social_handle), '') = ''" in sql
    assert params == (3378,)


def _scraper(followers=7400, thumbs=("a",)):
    s = MagicMock()
    s.scrape_instagram_profile.return_value = {"postsCount": 30}
    s.process_scrape.return_value = {"follower_count": followers, "recent_post_thumbnails": list(thumbs)}
    s.run_vision_analysis.return_value = {}
    return s


def test_refresh_saves_a_good_scrape():
    row = {"user_id": 3378, "handle": "bhawnaa.choudhary", "platform": "instagram",
           "follower_count": 6953, "scraped_at": datetime(2026, 8, 12)}
    scraper = _scraper()
    with patch.object(pr, "stale_profiles", return_value=[row]), \
            patch("services.creator_profile_scraper.CreatorProfileScraper", return_value=scraper):
        out = pr.refresh_stale_profiles(_Conn(_Cur([])), limit=1)
    assert out["refreshed"] == 1
    assert out["items"][0]["followers"] == 7400 and out["items"][0]["before"] == 6953
    scraper.save_creator_profile.assert_called_once()


def test_blocked_scrape_never_overwrites_and_is_retried_later():
    row = {"user_id": 3378, "handle": "h", "platform": "instagram", "follower_count": 6953}
    scraper = _scraper(followers=0, thumbs=())
    cur = _Cur([])
    with patch.object(pr, "stale_profiles", return_value=[row]), \
            patch("services.creator_profile_scraper.CreatorProfileScraper", return_value=scraper):
        out = pr.refresh_stale_profiles(_Conn(cur), limit=1)
    scraper.save_creator_profile.assert_not_called()
    assert out["deferred"] == 1
    assert any("next_refresh_at" in sql for sql, _ in cur.sql)


def test_dry_run_scrapes_nothing():
    with patch.object(pr, "stale_profiles", return_value=[{"user_id": 1, "handle": "h", "platform": "tiktok"}]), \
            patch("services.creator_profile_scraper.CreatorProfileScraper") as cls:
        out = pr.refresh_stale_profiles(_Conn(_Cur([])), dry_run=True)
    cls.return_value.scrape_tiktok_profile.assert_not_called()
    assert out["due"] == 1
