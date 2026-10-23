"""Keep creators' social data current.

Onboarding scrapes before the creators row exists, so the scrape's handle and follower count
never reached creators. Refresh/reconnect then had no handle and failed. Separately, nothing
re-scraped profiles after signup, so follower counts and posts froze at the first scan.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from psycopg2.extras import RealDictCursor

STALE_DAYS = 14
ACTIVE_DAYS = 30
RETRY_DAYS = 3
SCRAPE_PLATFORMS = ("instagram", "tiktok")


def backfill_social_handle(cursor, user_id: Optional[int] = None) -> int:
    """Copy handle/platform/followers from creator_profile_data onto creators that have none."""
    sql = """
        UPDATE creators c SET
            social_handle = p.handle,
            social_platform = COALESCE(NULLIF(c.social_platform, ''), p.primary_platform),
            social_follower_count = CASE WHEN COALESCE(c.social_follower_count, 0) > 0
                                         THEN c.social_follower_count ELSE p.follower_count END
        FROM creator_profile_data p
        WHERE p.user_id = c.user_id
          AND COALESCE(TRIM(c.social_handle), '') = ''
          AND COALESCE(TRIM(p.handle), '') <> ''
    """
    params: tuple = ()
    if user_id is not None:
        sql += " AND c.user_id = %s"
        params = (int(user_id),)
    cursor.execute(sql, params)
    return cursor.rowcount or 0


def scraped_handle(conn, user_id: Optional[int], platform: str = "instagram") -> str:
    """The handle from the last scrape, when creators.social_handle is empty."""
    if not user_id:
        return ""
    cur = conn.cursor()
    try:
        cur.execute(
            """
            SELECT handle FROM creator_profile_data
            WHERE user_id = %s AND COALESCE(primary_platform, %s) = %s
            """,
            (int(user_id), platform, platform),
        )
        row = cur.fetchone()
    finally:
        cur.close()
    if not row:
        return ""
    value = row["handle"] if isinstance(row, dict) else row[0]
    return str(value or "").strip().lstrip("@")


def stale_profiles(conn, limit: int) -> List[Dict[str, Any]]:
    cur = conn.cursor(cursor_factory=RealDictCursor)
    cur.execute(
        """
        SELECT p.user_id, p.handle, p.primary_platform AS platform, p.follower_count, p.scraped_at
        FROM creator_profile_data p
        JOIN users u ON u.id = p.user_id
        WHERE COALESCE(TRIM(p.handle), '') <> ''
          AND p.primary_platform = ANY(%s)
          AND p.scraped_at < NOW() - make_interval(days => %s)
          AND COALESCE(p.next_refresh_at, p.scraped_at) < NOW()
          AND u.last_login > NOW() - make_interval(days => %s)
        ORDER BY u.last_login DESC
        LIMIT %s
        """,
        (list(SCRAPE_PLATFORMS), STALE_DAYS, ACTIVE_DAYS, int(limit)),
    )
    return [dict(r) for r in cur.fetchall() or []]


def _usable(profile: Dict[str, Any]) -> bool:
    """A blocked or soft scrape must not overwrite a good one."""
    followers = int(profile.get("follower_count") or 0)
    posts = profile.get("recent_post_thumbnails") or profile.get("recent_posts") or []
    return followers > 0 and bool(posts)


def _defer(conn, user_id: int) -> None:
    cur = conn.cursor()
    cur.execute(
        "UPDATE creator_profile_data SET next_refresh_at = NOW() + make_interval(days => %s) WHERE user_id = %s",
        (RETRY_DAYS, user_id),
    )
    conn.commit()
    cur.close()


def refresh_stale_profiles(conn, limit: int = 4, dry_run: bool = False) -> Dict[str, Any]:
    """Cron: re-scrape a few active creators whose data is older than STALE_DAYS."""
    from services.creator_profile_scraper import CreatorProfileScraper

    rows = stale_profiles(conn, limit)
    out: Dict[str, Any] = {"due": len(rows), "refreshed": 0, "deferred": 0, "items": []}
    if dry_run:
        out["items"] = [{"user_id": r["user_id"], "platform": r["platform"]} for r in rows]
        return out
    for row in rows:
        item = refresh_profile(conn, row["user_id"], row["handle"], row["platform"],
                               before=int(row.get("follower_count") or 0))
        out["refreshed" if item.get("ok") else "deferred"] += 1
        out["items"].append(item)
    return out


def refresh_profile(conn, uid: int, handle: str, platform: str, before: int = 0,
                    vision: bool = True) -> Dict[str, Any]:
    """Re-scrape one creator. A blocked or thin scrape keeps the last good data."""
    from services.creator_profile_scraper import CreatorProfileScraper

    item: Dict[str, Any] = {"user_id": uid, "platform": platform}
    try:
        scraper = CreatorProfileScraper(conn)
        if platform == "instagram":
            raw = scraper.scrape_instagram_profile(handle, user_id=uid, allow_html_fallback=True)
        else:
            raw = scraper.scrape_tiktok_profile(handle, user_id=uid, allow_html_fallback=True)
        if raw.get("isPrivate") or raw.get("privateAccount") or raw.get("private"):
            raise ValueError("private")
        profile = scraper.process_scrape(raw, platform)
        if not _usable(profile):
            raise ValueError("thin scrape")
        analysis = None
        if vision:
            try:
                analysis = scraper.run_vision_analysis(
                    thumbnail_urls=profile.get("recent_post_thumbnails", []),
                    bio=profile.get("raw_bio", ""),
                    captions=profile.get("recent_captions", []),
                    handle=profile.get("handle", ""),
                    followers=profile.get("follower_count", 0),
                )
            except Exception as err:
                print(f"[profile-refresh] vision skipped user={uid}: {err}")
        scraper.save_creator_profile(uid, profile, analysis)
        item.update(ok=True, followers=int(profile.get("follower_count") or 0), before=before)
    except Exception as err:
        try:
            conn.rollback()
            _defer(conn, uid)
        except Exception:
            conn.rollback()
        item.update(ok=False, error=str(err)[:120])
    return item
