"""
Fill pr_brands.pr_example_posts with 2-3 recent brand posts.

Sources, in order:
  1) Brand TikTok handle (player-ready video IDs)
  2) Brand Instagram handle (thumbnail + public URL)

Used by apply-pack (lazy, first miss) and scripts/enrich_pr_examples.py.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

from psycopg2.extras import Json

_HANDLE_RE = re.compile(r"^[A-Za-z0-9._]+$")


def _clean_handle(raw: Optional[str]) -> str:
    if not raw:
        return ""
    value = str(raw).strip()
    if "instagram.com/" in value or "tiktok.com/@" in value:
        value = value.rstrip("/").split("/")[-1]
    value = value.lstrip("@").split("?")[0].strip()
    return value if _HANDLE_RE.match(value) else ""


def _caption(text: Optional[str], fallback: str) -> str:
    line = re.sub(r"\s+", " ", str(text or "")).strip()
    if not line:
        return fallback
    return line[:72] + ("…" if len(line) > 72 else "")


def _from_tiktok(handle: str, limit: int = 3) -> List[Dict[str, Any]]:
    from services.inhouse_social_scraper import scrape_tiktok

    profile = scrape_tiktok(handle, results_limit=max(limit, 6))
    videos = profile.get("latestVideos") or []
    out = []
    for video in videos:
        vid = str(video.get("id") or "").strip()
        if not vid.isdigit() or len(vid) < 8:
            continue
        cover = ""
        meta = video.get("videoMeta") or {}
        if isinstance(meta, dict):
            cover = meta.get("coverUrl") or ""
        covers = video.get("covers") or []
        if not cover and covers:
            cover = covers[0]
        out.append(
            {
                "id": vid,
                "platform": "tiktok",
                "url": f"https://www.tiktok.com/@{handle}/video/{vid}",
                "thumbnail_url": cover or "",
                "title": _caption(video.get("text"), "Recent TikTok"),
                "handle": f"@{handle}",
            }
        )
        if len(out) >= limit:
            break
    return out


def _from_instagram(handle: str, limit: int = 3) -> List[Dict[str, Any]]:
    from services.inhouse_social_scraper import scrape_instagram, diy_scrape_is_acceptable

    profile = scrape_instagram(handle, results_limit=max(limit, 8))
    if not diy_scrape_is_acceptable(profile, "instagram"):
        return []
    posts = profile.get("latestPosts") or []
    out = []
    for post in posts:
        if not isinstance(post, dict):
            continue
        code = str(post.get("shortCode") or post.get("shortcode") or "").strip()
        thumb = post.get("displayUrl") or post.get("thumbnail_url") or ""
        if not code and not thumb:
            continue
        is_video = bool(post.get("isVideo") or post.get("type") == "Video")
        path = "reel" if is_video else "p"
        url = post.get("url") or (f"https://www.instagram.com/{path}/{code}/" if code else "")
        if not url:
            continue
        out.append(
            {
                "id": code or url,
                "platform": "instagram",
                "url": url,
                "thumbnail_url": thumb,
                "title": _caption(post.get("caption"), "Recent Instagram"),
                "handle": f"@{handle}",
            }
        )
        if len(out) >= limit:
            break
    return out


def collect_example_posts(
    instagram_handle: Optional[str] = None,
    tiktok_handle: Optional[str] = None,
    limit: int = 3,
) -> List[Dict[str, Any]]:
    """Prefer TikTok (embeddable), then Instagram thumbnails."""
    examples: List[Dict[str, Any]] = []
    tt = _clean_handle(tiktok_handle)
    ig = _clean_handle(instagram_handle)
    if tt:
        try:
            examples.extend(_from_tiktok(tt, limit=limit))
        except Exception as exc:
            print(f"[pr-examples] TikTok @{tt} failed: {exc}")
    if len(examples) < limit and ig:
        try:
            examples.extend(_from_instagram(ig, limit=limit - len(examples)))
        except Exception as exc:
            print(f"[pr-examples] Instagram @{ig} failed: {exc}")
    return examples[:limit]


def enrich_brand_examples(cursor, brand: Dict[str, Any], limit: int = 3) -> List[Dict[str, Any]]:
    """Scrape and persist examples for one brand row. Returns saved list."""
    existing = brand.get("pr_example_posts")
    if isinstance(existing, str):
        try:
            import json

            existing = json.loads(existing)
        except Exception:
            existing = None
    if isinstance(existing, list) and existing:
        return existing

    examples = collect_example_posts(
        instagram_handle=brand.get("instagram_handle"),
        tiktok_handle=brand.get("tiktok_handle"),
        limit=limit,
    )
    if examples:
        cursor.execute(
            "UPDATE pr_brands SET pr_example_posts = %s WHERE id = %s",
            (Json(examples), brand.get("id")),
        )
    return examples
