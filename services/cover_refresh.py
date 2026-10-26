"""Fresh covers for videos whose TikTok cover link already expired.

TikTok blocks cover refreshes from our server and from browsers (no CORS on oEmbed), so the videos a report
shows with a dead cover are re-fetched through Apify by post URL, a few cents a batch, and their new covers are
stored for good. The report that spots them still shows placeholders; the next view has the thumbnails.
Runs are tracked in Redis; without Redis nothing is queued.
"""

from __future__ import annotations

import json
import time
from typing import Any, Dict, List

from services import radar_source as source

PENDING = "cover_refresh:pending"
BATCH = 30
RUNS_PER_DAY = 40
SEEN_TTL = 6 * 3600


def _dead(cover: str, now: float) -> bool:
    from services.brand_scan import _cover_expires

    if not cover:
        return True
    if "/storage/v1/object/public/" in cover:
        return False
    if "tiktokcdn" not in cover and "tiktok.com" not in cover:
        return False
    exp = _cover_expires(cover)
    return exp is not None and exp < now + 300


def queue(videos: List[Dict[str, Any]], redis_client, limit: int = BATCH) -> int:
    """Start one Apify run for shown videos with dead covers. Returns how many were queued."""
    if redis_client is None:
        return 0
    now = time.time()
    try:
        todo = []
        for v in videos:
            vid, url = str(v.get("tiktok_id") or ""), str(v.get("url") or "")
            if not vid or "tiktok.com/" not in url or not _dead(str(v.get("cover_url") or ""), now):
                continue
            if not redis_client.set(f"cover_refresh:seen:{vid}", 1, nx=True, ex=SEEN_TTL):
                continue
            todo.append((vid, url.split("?")[0]))
            if len(todo) >= limit:
                break
        if not todo:
            return 0
        day = f"cover_refresh:runs:{time.strftime('%Y%m%d')}"
        if int(redis_client.incr(day)) > RUNS_PER_DAY:
            return 0
        redis_client.expire(day, 2 * 86400)
        run_id = source.start_posts_run([u for _, u in todo])
        redis_client.hset(PENDING, run_id, json.dumps({"ids": [i for i, _ in todo], "at": now}))
        redis_client.expire(PENDING, 86400)
        print(f"[cover-refresh] run {run_id} for {len(todo)} covers")
        return len(todo)
    except Exception as err:
        print(f"[cover-refresh] queue skipped: {type(err).__name__}: {err}")
        return 0


def collect(conn, redis_client) -> int:
    """Store covers from finished refresh runs. Returns how many videos got a fresh cover."""
    if redis_client is None:
        return 0
    try:
        pending = redis_client.hgetall(PENDING) or {}
    except Exception:
        return 0
    fixed = 0
    for run_id, meta in pending.items():
        run_id = run_id.decode() if isinstance(run_id, bytes) else run_id
        try:
            info = json.loads(meta)
            state = source.run_state(run_id)
            if not state["finished"]:
                if time.time() - float(info.get("at") or 0) > 900:
                    redis_client.hdel(PENDING, run_id)
                continue
            if not redis_client.hdel(PENDING, run_id):
                continue  # another request took it
            items = source.fetch_items(state["dataset_id"]) if state["ok"] and state["dataset_id"] else []
            fresh = [v for v in (source.normalize(i) for i in items) if v and v.get("cover_url")]
            cur = conn.cursor()
            for v in fresh:
                cur.execute("""UPDATE tiktok_videos SET cover_url = %s WHERE tiktok_id = %s
                               AND COALESCE(cover_url, '') NOT LIKE '%%/storage/v1/object/public/%%'""",
                            (v["cover_url"], v["tiktok_id"]))
            conn.commit()
            from services.brand_scan import persist_covers

            fixed += persist_covers(conn, fresh, limit=BATCH, budget_s=15.0)
        except Exception as err:
            conn.rollback()
            print(f"[cover-refresh] collect {run_id} skipped: {type(err).__name__}: {err}")
    return fixed


def refresh(conn, videos: List[Dict[str, Any]], redis_client, shown: int = 36) -> None:
    """For a report about to show these videos (most viewed first): take in finished refreshes, store fresh
    covers, and queue a refresh for the dead ones. Mutates cover_url on the dicts it can fix now."""
    try:
        if collect(conn, redis_client):
            ids = [str(v["tiktok_id"]) for v in videos[:shown] if v.get("tiktok_id")]
            cur = conn.cursor()
            cur.execute("SELECT tiktok_id, cover_url FROM tiktok_videos WHERE tiktok_id = ANY(%s)", (ids,))
            covers = dict(cur.fetchall())
            for v in videos[:shown]:
                if covers.get(str(v.get("tiktok_id"))):
                    v["cover_url"] = covers[str(v["tiktok_id"])]
        from services.brand_scan import persist_covers

        persist_covers(conn, videos[:shown], budget_s=12.0)
        queue(videos[:shown], redis_client)
    except Exception as err:
        conn.rollback()
        print(f"[cover-refresh] skipped: {type(err).__name__}: {err}")
