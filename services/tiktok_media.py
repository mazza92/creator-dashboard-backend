"""Our own copy of the TikTok videos we showcase (homepage, portfolios, brand pages, reports).

TikTok's embedded player ("Player authentication failed") and signed CDN links break without warning, so pages
never depend on TikTok at view time: they ask GET /api/public/tiktok-media for the video ids they show, get our
stored MP4 + cover when we have them, and unknown ids are queued. A cron fetches queued ids once through the
Apify TikTok actor (video + cover downloaded into the run's store), copies both into Supabase storage, and keeps
them for good. A daily cap bounds what unknown ids can cost.
"""
from __future__ import annotations

import os
import re
import time
from typing import Any, Dict, Iterable, List, Optional, Tuple

import requests

DDL = """
CREATE TABLE IF NOT EXISTS tiktok_media (
    video_id TEXT PRIMARY KEY,
    handle TEXT,
    status TEXT NOT NULL DEFAULT 'queued',
    run_id TEXT,
    video_url TEXT,
    cover_url TEXT,
    attempts INTEGER NOT NULL DEFAULT 0,
    error TEXT,
    requested_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    stored_at TIMESTAMPTZ
)
"""
MAX_IDS_PER_REQUEST = 24
DAILY_NEW_CAP = int(os.getenv("TIKTOK_MEDIA_DAILY_CAP") or 150)
RUN_BATCH = 20
STORE_PER_RUN = 8
MAX_ATTEMPTS = 3
STALE_RUN_HOURS = 3

_ID = re.compile(r"^\d{8,25}$")
_HANDLE = re.compile(r"^[A-Za-z0-9._]{1,40}$")
_READY = False


def ensure_table(conn) -> None:
    global _READY
    if _READY:
        return
    cur = conn.cursor()
    cur.execute(DDL)
    cur.execute("CREATE INDEX IF NOT EXISTS idx_tiktok_media_status ON tiktok_media (status)")
    conn.commit()
    _READY = True


def clean_ids(ids: Iterable[Any], handles: Optional[Iterable[Any]] = None) -> List[Tuple[str, Optional[str]]]:
    """Valid (video_id, handle) pairs, de-duplicated, at most MAX_IDS_PER_REQUEST."""
    hs = list(handles or [])
    out, seen = [], set()
    for i, raw in enumerate(ids or []):
        vid = str(raw or "").strip()
        if not _ID.match(vid) or vid in seen:
            continue
        seen.add(vid)
        h = str(hs[i] if i < len(hs) else "" or "").strip().lstrip("@")
        out.append((vid, h if _HANDLE.match(h) else None))
        if len(out) >= MAX_IDS_PER_REQUEST:
            break
    return out


def _rows(cur) -> List[Dict[str, Any]]:
    cols = [d[0] for d in cur.description]
    return [r if isinstance(r, dict) else dict(zip(cols, r)) for r in cur.fetchall()]


def lookup(conn, pairs: List[Tuple[str, Optional[str]]], queue_missing: bool = True) -> Dict[str, Dict[str, Any]]:
    """{video_id: {status, video, cover}} for the ids asked; unknown ids are queued (within the daily cap)."""
    ensure_table(conn)
    ids = [p[0] for p in pairs]
    if not ids:
        return {}
    cur = conn.cursor()
    cur.execute("SELECT video_id, status, video_url, cover_url FROM tiktok_media WHERE video_id = ANY(%s)", (ids,))
    found = {r["video_id"]: r for r in _rows(cur)}
    if queue_missing:
        missing = [p for p in pairs if p[0] not in found]
        if missing:
            cur.execute("SELECT COUNT(*) FROM tiktok_media WHERE requested_at > NOW() - INTERVAL '1 day'")
            row = cur.fetchone()
            room = DAILY_NEW_CAP - int((row[0] if not isinstance(row, dict) else list(row.values())[0]) or 0)
            for vid, handle in missing[:max(0, room)]:
                cur.execute("INSERT INTO tiktok_media (video_id, handle) VALUES (%s, %s) ON CONFLICT DO NOTHING",
                            (vid, handle))
                found[vid] = {"status": "queued", "video_url": None, "cover_url": None}
            conn.commit()
    return {vid: {"status": r.get("status"), "video": r.get("video_url"), "cover": r.get("cover_url")}
            for vid, r in found.items()}


def _upload(content: bytes, filename: str, content_type: str) -> Optional[str]:
    from media_proxy_routes import _storage_public_base
    from services.supabase_headers import supabase_auth_headers

    base, bucket = _storage_public_base()
    key = os.getenv("SUPABASE_KEY") or ""
    if not base or not key or not content:
        return None
    resp = requests.post(f"{base}/storage/v1/object/{bucket}/{filename}",
                         headers={**supabase_auth_headers(key), "Content-Type": content_type, "x-upsert": "true"},
                         data=content, timeout=60)
    if resp.status_code not in (200, 201):
        print(f"[tiktok_media] upload {filename} -> {resp.status_code}: {resp.text[:160]}")
        return None
    return f"{base}/storage/v1/object/public/{bucket}/{filename}"


def media_from_item(item: Dict[str, Any]) -> Tuple[Optional[str], Optional[str]]:
    """(video download url, cover url) from one actor item."""
    meta = item.get("videoMeta") or {}
    urls = [u for u in (item.get("mediaUrls") or []) if isinstance(u, str) and u.startswith("http")]
    video = urls[0] if urls else meta.get("downloadAddr")
    cover = meta.get("originalCoverUrl") or meta.get("coverUrl")
    return video, cover


def _store_one(vid: str, item: Dict[str, Any]) -> Tuple[Optional[str], Optional[str]]:
    from services import radar_source as source

    video_src, cover_src = media_from_item(item)
    video = None
    if video_src:
        data = source.download(video_src, timeout=90)
        if data and len(data) > 10_000:
            video = _upload(data, f"tiktok/{vid}.mp4", "video/mp4")
    cover = None
    if cover_src:
        try:
            from media_proxy_routes import rehost_social_image
            cover = rehost_social_image(cover_src, dest_filename=f"tiktok/{vid}.jpg")
        except Exception as err:
            print(f"[tiktok_media] cover failed {vid}: {err}")
    return video, cover


def collect(conn, budget_s: float = 200.0) -> Dict[str, int]:
    """Cron: store finished runs' media, then start a run for queued ids. Returns counts."""
    from services import radar_source as source

    ensure_table(conn)
    started = time.monotonic()
    out = {"stored": 0, "failed": 0, "started": 0}
    cur = conn.cursor()
    cur.execute("""SELECT DISTINCT run_id FROM tiktok_media WHERE status = 'running' AND run_id IS NOT NULL
                   ORDER BY run_id LIMIT 5""")
    for (run_id,) in [tuple(r.values()) if isinstance(r, dict) else r for r in cur.fetchall()]:
        if time.monotonic() - started > budget_s:
            break
        try:
            state = source.run_state(run_id)
        except source.SourceUnavailable as err:
            print(f"[tiktok_media] run lookup failed {run_id}: {err}")
            continue
        if not state.get("finished"):
            continue
        items = {}
        if state.get("dataset_id"):
            try:
                items = {str(i.get("id")): i for i in source.fetch_items(state["dataset_id"], limit=100)}
            except source.SourceUnavailable as err:
                print(f"[tiktok_media] items failed {run_id}: {err}")
        cur.execute("SELECT video_id FROM tiktok_media WHERE run_id = %s AND status = 'running'", (run_id,))
        vids = [r["video_id"] if isinstance(r, dict) else r[0] for r in cur.fetchall()]
        for vid in vids[:STORE_PER_RUN] if items else vids:
            if time.monotonic() - started > budget_s:
                break
            item = items.get(vid)
            video, cover = _store_one(vid, item) if item else (None, None)
            if video or cover:
                cur.execute("""UPDATE tiktok_media SET status = %s, video_url = %s, cover_url = %s, stored_at = NOW(),
                               error = NULL WHERE video_id = %s""",
                            ("stored" if video else "cover_only", video, cover, vid))
                out["stored"] += 1
            else:
                cur.execute("""UPDATE tiktok_media SET status = CASE WHEN attempts >= %s THEN 'failed' ELSE 'queued' END,
                               run_id = NULL, error = %s WHERE video_id = %s""",
                            (MAX_ATTEMPTS, "not in run results" if not item else "download failed", vid))
                out["failed"] += 1
            conn.commit()
    # Runs that never finished: put their ids back in the queue.
    cur.execute(f"""UPDATE tiktok_media SET status = 'queued', run_id = NULL
                    WHERE status = 'running' AND requested_at < NOW() - INTERVAL '{STALE_RUN_HOURS} hours'
                      AND stored_at IS NULL AND attempts < %s""", (MAX_ATTEMPTS,))
    conn.commit()
    cur.execute("""SELECT video_id, handle FROM tiktok_media WHERE status = 'queued' AND attempts < %s
                   ORDER BY requested_at LIMIT %s""", (MAX_ATTEMPTS, RUN_BATCH))
    queued = [(r["video_id"], r["handle"]) if isinstance(r, dict) else (r[0], r[1]) for r in cur.fetchall()]
    if queued:
        urls = [f"https://www.tiktok.com/@{h or 'tiktok'}/video/{v}" for v, h in queued]
        try:
            run_id = source.start_media_run(urls)
            cur.execute("""UPDATE tiktok_media SET status = 'running', run_id = %s, attempts = attempts + 1,
                           requested_at = NOW() WHERE video_id = ANY(%s)""", (run_id, [v for v, _ in queued]))
            conn.commit()
            out["started"] = len(queued)
        except source.SourceUnavailable as err:
            print(f"[tiktok_media] start failed: {err}")
    return out
