"""
Fill pr_brands.pr_example_posts with 2-3 recent brand posts.

Sources, in order:
  1) Brand TikTok handle (player-ready video IDs)
  2) Brand Instagram handle (thumbnail + public URL)

Used by apply-pack (lazy, first miss) and scripts/enrich_pr_examples.py.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Tuple

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


def _from_tiktok_profile(profile: Dict[str, Any], handle: str, limit: int = 3) -> List[Dict[str, Any]]:
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


def _from_tiktok(handle: str, limit: int = 3) -> List[Dict[str, Any]]:
    from services.inhouse_social_scraper import scrape_tiktok

    profile = scrape_tiktok(handle, results_limit=max(limit, 6))
    return _from_tiktok_profile(profile, handle, limit)


def _from_instagram_profile(profile: Dict[str, Any], handle: str, limit: int = 3) -> List[Dict[str, Any]]:
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


def _from_instagram(handle: str, limit: int = 3) -> List[Dict[str, Any]]:
    from services.inhouse_social_scraper import scrape_instagram, diy_scrape_is_acceptable

    profile = scrape_instagram(handle, results_limit=max(limit, 8))
    if not diy_scrape_is_acceptable(profile, "instagram"):
        return []
    return _from_instagram_profile(profile, handle, limit)


def _overview_from_tiktok(profile: Dict[str, Any], handle: str) -> Optional[Dict[str, Any]]:
    followers = int(profile.get("followerCount") or 0)
    bio = str(profile.get("signature") or "").strip()[:280]
    if followers <= 0 and not bio:
        return None
    return {
        "platform": "tiktok",
        "handle": profile.get("uniqueId") or handle,
        "nickname": profile.get("nickname") or "",
        "followers": followers,
        "following": int(profile.get("followingCount") or 0),
        "likes": int(profile.get("heartCount") or 0),
        "posts": int(profile.get("videoCount") or 0),
        "bio": bio,
        "verified": bool(profile.get("verified")),
        "avatar_url": profile.get("avatarUrl") or "",
    }


def _overview_from_instagram(profile: Dict[str, Any], handle: str) -> Optional[Dict[str, Any]]:
    followers = int(profile.get("followersCount") or 0)
    bio = str(profile.get("biography") or "").strip()[:280]
    if followers <= 0 and not bio:
        return None
    return {
        "platform": "instagram",
        "handle": profile.get("username") or handle,
        "nickname": profile.get("fullName") or "",
        "followers": followers,
        "following": int(profile.get("followsCount") or 0),
        "likes": 0,
        "posts": int(profile.get("postsCount") or 0),
        "bio": bio,
        "verified": bool(profile.get("isVerified")),
        "avatar_url": profile.get("profile_pic_url") or "",
    }


def social_overview_usable(social: Optional[Dict[str, Any]]) -> bool:
    if not isinstance(social, dict):
        return False
    try:
        return int(social.get("followers") or 0) > 0
    except (TypeError, ValueError):
        return False


def collect_examples_and_social(
    instagram_handle: Optional[str] = None,
    tiktok_handle: Optional[str] = None,
    limit: int = 3,
) -> Tuple[List[Dict[str, Any]], Optional[Dict[str, Any]]]:
    """One scrape pass for example posts + real profile stats. Never invents counts."""
    examples: List[Dict[str, Any]] = []
    social: Optional[Dict[str, Any]] = None
    tt = _clean_handle(tiktok_handle)
    ig = _clean_handle(instagram_handle)
    if tt:
        try:
            from services.inhouse_social_scraper import scrape_tiktok

            profile = scrape_tiktok(tt, results_limit=max(limit, 6)) or {}
            social = _overview_from_tiktok(profile, tt)
            examples.extend(_from_tiktok_profile(profile, tt, limit=limit))
        except Exception as exc:
            print(f"[pr-examples] TikTok @{tt} failed: {exc}")
    if len(examples) < limit and ig:
        try:
            from services.inhouse_social_scraper import scrape_instagram, diy_scrape_is_acceptable

            profile = scrape_instagram(ig, results_limit=max(limit, 8)) or {}
            ig_social = _overview_from_instagram(profile, ig)
            if not social:
                social = ig_social
            if diy_scrape_is_acceptable(profile, "instagram"):
                examples.extend(_from_instagram_profile(profile, ig, limit=limit - len(examples)))
        except Exception as exc:
            print(f"[pr-examples] Instagram @{ig} failed: {exc}")
    return examples[:limit], social


def collect_social_overview(
    tiktok_handle: Optional[str] = None,
    instagram_handle: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """Profile stats only. Prefer TikTok (following / followers / likes)."""
    tt = _clean_handle(tiktok_handle)
    ig = _clean_handle(instagram_handle)
    if tt:
        try:
            from services.inhouse_social_scraper import scrape_tiktok

            profile = scrape_tiktok(tt, results_limit=1) or {}
            social = _overview_from_tiktok(profile, tt)
            if social_overview_usable(social) or (social and social.get("bio")):
                return social
        except Exception as exc:
            print(f"[pr-social] TikTok @{tt} failed: {exc}")
    if ig:
        try:
            from services.inhouse_social_scraper import scrape_instagram

            profile = scrape_instagram(ig, results_limit=1) or {}
            social = _overview_from_instagram(profile, ig)
            if social_overview_usable(social) or (social and social.get("bio")):
                return social
        except Exception as exc:
            print(f"[pr-social] Instagram @{ig} failed: {exc}")
    return None


def collect_example_posts(
    instagram_handle: Optional[str] = None,
    tiktok_handle: Optional[str] = None,
    limit: int = 3,
) -> List[Dict[str, Any]]:
    """Prefer TikTok (embeddable), then Instagram thumbnails."""
    examples, _ = collect_examples_and_social(
        instagram_handle=instagram_handle,
        tiktok_handle=tiktok_handle,
        limit=limit,
    )
    return examples


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
