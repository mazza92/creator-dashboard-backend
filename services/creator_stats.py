"""Live creator stats for pitches: followers, average views, engagement, and how fresh they are.

Stats older than a day trigger a background re-scrape (it takes 10-60s, too slow to block a
pitch). The pitch card always says how old its numbers are, and when a refresh failed.
A number the creator typed wins until a later sync shows a higher one.
"""

from __future__ import annotations

import threading
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional

SYNC_EVERY = timedelta(hours=24)
FAIL_KEY = "stats_sync_failed"
TRY_KEY = "stats_sync_try"
_IN_FLIGHT: set = set()
_LOCK = threading.Lock()


def _aware(value: Any) -> Optional[datetime]:
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    if not isinstance(value, datetime):
        return None
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def _int(value: Any) -> int:
    try:
        return max(0, int(float(str(value).replace(",", ""))))
    except (TypeError, ValueError):
        return 0


def avg_views(scrape: Optional[Dict]) -> int:
    views = [_int(p.get("views") or p.get("video_view_count") or p.get("play_count"))
             for p in (scrape or {}).get("recent_posts") or [] if isinstance(p, dict)]
    views = [v for v in views if v > 0]
    return int(round(sum(views) / len(views))) if views else 0


def snapshot(scrape: Optional[Dict], manual_followers: int = 0, override: Optional[Dict] = None,
             failed_recently: bool = False, now: Optional[datetime] = None) -> Dict[str, Any]:
    """{followers, avg_views, engagement_rate, synced_at, stale, source, failed}."""
    scrape = scrape or {}
    now = now or datetime.now(timezone.utc)
    synced = _aware(scrape.get("scraped_at") or scrape.get("last_refresh_at"))
    scraped = _int(scrape.get("follower_count") or scrape.get("followers"))
    override = override or {}
    manual = _int(override.get("followers")) or _int(manual_followers)
    manual_at = _aware(override.get("at"))
    use_manual = bool(manual) and not (scraped > manual and synced and (not manual_at or synced > manual_at))
    eng = scrape.get("engagement_rate")
    return {
        "followers": manual if use_manual else scraped,
        "avg_views": avg_views(scrape),
        "engagement_rate": round(float(eng), 1) if eng not in (None, "") and float(eng or 0) > 0 else None,
        "synced_at": synced.isoformat() if synced else None,
        "stale": not synced or now - synced > SYNC_EVERY,
        "source": "manual" if use_manual else "sync",
        "failed": bool(failed_recently),
        "manual_superseded": bool(manual) and not use_manual,
    }


def stats_line(snap: Dict[str, Any], now: Optional[datetime] = None) -> str:
    """The freshness line under the pitch card."""
    now = now or datetime.now(timezone.utc)
    if snap.get("source") == "manual":
        return f"Followers you gave me: {snap['followers']:,}"
    synced = _aware(snap.get("synced_at"))
    if not synced:
        return "Stats not synced yet"
    hours = (now - synced).total_seconds() / 3600
    if hours < 1:
        when = "just now"
    elif hours < 24:
        when = f"{int(hours)}h ago"
    else:
        when = f"{synced.strftime('%b')} {synced.day}"
    if not snap.get("stale"):
        return f"Stats updated {when}"
    if snap.get("failed"):
        return f"Stats from {when} · couldn't refresh, so these are the last numbers I have"
    return f"Stats from {when} · refreshing now"


def _manual_followers(conn, creator_id: int) -> int:
    cur = conn.cursor()
    try:
        cur.execute("SELECT creator_followers FROM creators WHERE id = %s", (creator_id,))
        row = cur.fetchone()
    except Exception:
        conn.rollback()
        return 0
    if not row:
        return 0
    return _int(row["creator_followers"] if isinstance(row, dict) else row[0])


def save_override(conn, creator_id: int, notes: Dict[str, Any], followers: int) -> Dict[str, Any]:
    """Creator typed their follower count: it goes into every pitch until a sync shows more."""
    followers = _int(followers)
    if not followers:
        return notes
    cur = conn.cursor()
    cur.execute("UPDATE creators SET creator_followers = %s WHERE id = %s", (followers, creator_id))
    conn.commit()
    out = dict(notes or {})
    out["stats_override"] = {"followers": followers, "at": datetime.now(timezone.utc).isoformat()}
    return out


def reconcile(conn, creator_id: int, notes: Dict[str, Any], snap: Dict[str, Any]) -> Dict[str, Any]:
    """A sync beat the typed number: pitches go back to the synced count."""
    if not snap.get("manual_superseded"):
        return notes
    try:
        cur = conn.cursor()
        cur.execute("UPDATE creators SET creator_followers = %s WHERE id = %s",
                    (int(snap["followers"]), creator_id))
        conn.commit()
    except Exception as err:
        print(f"[stats] reconcile skipped: {err}")
        conn.rollback()
        return notes
    out = dict(notes or {})
    out.pop("stats_override", None)
    return out


def _failed_recently(conn, creator_id: int) -> bool:
    from services.polly_memory import note_sent_within
    try:
        return note_sent_within(conn, creator_id, FAIL_KEY, SYNC_EVERY)
    except Exception:
        conn.rollback()
        return False


def _run_refresh(creator_id: int, user_id: int, handle: str, platform: str) -> None:
    from pr_crm_routes import get_db_connection
    from services.polly_memory import record_note_sent
    from services.profile_refresh import refresh_profile

    conn = get_db_connection()
    try:
        item = refresh_profile(conn, user_id, handle, platform, vision=False)
        if not item.get("ok"):
            record_note_sent(conn, creator_id, FAIL_KEY)
        print(f"[stats] refresh creator={creator_id} ok={item.get('ok')}")
    except Exception as err:
        print(f"[stats] refresh crashed creator={creator_id}: {err}")
    finally:
        conn.close()
        with _LOCK:
            _IN_FLIGHT.discard(creator_id)


def maybe_refresh(conn, creator_id: int, user_id: Any, scrape: Optional[Dict], snap: Dict[str, Any]) -> bool:
    """Start a background re-scrape when stats are over a day old. At most one try a day."""
    from services.polly_memory import note_sent_within, record_note_sent

    scrape = scrape or {}
    handle = str(scrape.get("handle") or "").strip().lstrip("@")
    platform = str(scrape.get("primary_platform") or scrape.get("platform") or "").lower()
    if not snap.get("stale") or not user_id or not handle or platform not in ("instagram", "tiktok"):
        return False
    with _LOCK:
        if creator_id in _IN_FLIGHT:
            return True
    try:
        if note_sent_within(conn, creator_id, TRY_KEY, SYNC_EVERY):
            return False
        record_note_sent(conn, creator_id, TRY_KEY)
    except Exception as err:
        print(f"[stats] refresh gate skipped: {err}")
        conn.rollback()
        return False
    with _LOCK:
        _IN_FLIGHT.add(creator_id)
    threading.Thread(target=_run_refresh, args=(creator_id, int(user_id), handle, platform),
                     daemon=True).start()
    return True


def pitch_stats(conn, creator_id: int, user_id: Any, scrape: Optional[Dict],
                notes: Dict[str, Any]) -> Dict[str, Any]:
    """Everything a pitch needs: {"snap", "line", "notes"} (notes may drop a superseded override)."""
    snap = snapshot(scrape, _manual_followers(conn, creator_id), (notes or {}).get("stats_override"),
                    _failed_recently(conn, creator_id))
    notes = reconcile(conn, creator_id, notes, snap)
    refreshing = maybe_refresh(conn, creator_id, user_id, scrape, snap)
    if snap["stale"] and not refreshing and not snap["failed"]:
        snap["failed"] = _failed_recently(conn, creator_id)
    return {"snap": snap, "line": stats_line(snap), "notes": notes}
