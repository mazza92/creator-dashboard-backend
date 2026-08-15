"""In-house YouTube channel scrape for onboarding quality.

Returns a process_scrape-compatible profile:
  uniqueId / username, subscriberCount / followerCount, videoCount,
  description / signature, latestVideos (createTime + cover thumbnails).
"""

from __future__ import annotations

import json
import random
import re
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional
from urllib.parse import quote

import requests

from services.inhouse_social_scraper import InHouseScrapeError

_UA_POOL = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
]

_RELATIVE_UNITS = (
    (r"(\d+)\s*second", 1 / 86400),
    (r"(\d+)\s*minute", 1 / 1440),
    (r"(\d+)\s*hour", 1 / 24),
    (r"(\d+)\s*day", 1),
    (r"(\d+)\s*week", 7),
    (r"(\d+)\s*month", 30),
    (r"(\d+)\s*year", 365),
)


def parse_compact_count(text: Any) -> int:
    """Parse YouTube counts like '1.2K subscribers' or '12,345'."""
    if text is None:
        return 0
    if isinstance(text, (int, float)):
        return int(text)
    raw = str(text).strip()
    if not raw:
        return 0
    lowered = raw.lower().replace(",", "")
    lowered = re.sub(
        r"\b(subscribers?|videos?|views?|subscribers hidden)\b",
        "",
        lowered,
    ).strip()
    if "no " in lowered or lowered in ("hidden", "none"):
        return 0
    match = re.search(r"([0-9]*\.?[0-9]+)\s*([kmb])?", lowered)
    if not match:
        digits = re.sub(r"[^\d]", "", raw)
        return int(digits) if digits else 0
    number = float(match.group(1))
    suffix = (match.group(2) or "").lower()
    multiplier = {"k": 1_000, "m": 1_000_000, "b": 1_000_000_000}.get(suffix, 1)
    return int(number * multiplier)


def relative_published_to_unix(text: Any, *, now: Optional[datetime] = None) -> Optional[int]:
    """Convert '2 days ago' / 'Streamed 3 hours ago' into a unix timestamp."""
    if not text:
        return None
    blob = str(text).strip().lower()
    if not blob:
        return None
    now = now or datetime.now(timezone.utc)
    if any(token in blob for token in ("just now", "moments ago", "today")):
        return int(now.timestamp())
    for pattern, days in _RELATIVE_UNITS:
        match = re.search(pattern, blob)
        if match:
            delta = timedelta(days=float(match.group(1)) * days)
            return int((now - delta).timestamp())
    return None


def yt_text(node: Any) -> str:
    if node is None:
        return ""
    if isinstance(node, str):
        return node
    if isinstance(node, (int, float)):
        return str(node)
    if not isinstance(node, dict):
        return ""
    if node.get("simpleText"):
        return str(node["simpleText"])
    if isinstance(node.get("content"), str):
        return node["content"]
    runs = node.get("runs")
    if isinstance(runs, list):
        return "".join(str(run.get("text") or "") for run in runs if isinstance(run, dict))
    for key in ("text", "label", "accessibility"):
        nested = node.get(key)
        if nested and nested is not node:
            found = yt_text(nested)
            if found:
                return found
    return ""


def extract_yt_initial_data(html: str) -> Dict[str, Any]:
    if not html:
        return {}
    marker_idx = html.find("ytInitialData")
    if marker_idx < 0:
        return {}
    start = html.find("{", marker_idx)
    if start < 0:
        return {}
    depth = 0
    in_str = False
    escape = False
    for i, ch in enumerate(html[start:], start):
        if in_str:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                try:
                    parsed = json.loads(html[start : i + 1])
                except json.JSONDecodeError:
                    return {}
                return parsed if isinstance(parsed, dict) else {}
    return {}


def _iter_video_renderers(obj: Any, out: Optional[List[dict]] = None) -> List[dict]:
    if out is None:
        out = []
    if isinstance(obj, dict):
        for key in ("videoRenderer", "gridVideoRenderer", "reelItemRenderer"):
            node = obj.get(key)
            if isinstance(node, dict):
                out.append(node)
        for value in obj.values():
            _iter_video_renderers(value, out)
    elif isinstance(obj, list):
        for item in obj:
            _iter_video_renderers(item, out)
    return out


def _video_id(renderer: dict) -> str:
    vid = str(renderer.get("videoId") or "").strip()
    if vid:
        return vid
    nav = renderer.get("navigationEndpoint") or {}
    if isinstance(nav, dict):
        reel = nav.get("reelWatchEndpoint") or nav.get("watchEndpoint") or {}
        if isinstance(reel, dict):
            return str(reel.get("videoId") or "").strip()
    return ""


def _thumbnail_url(renderer: dict, video_id: str) -> str:
    thumbs = ((renderer.get("thumbnail") or {}).get("thumbnails")) or []
    if isinstance(thumbs, list) and thumbs:
        last = thumbs[-1] if isinstance(thumbs[-1], dict) else {}
        url = str(last.get("url") or "").strip()
        if url:
            return url
    if video_id:
        return f"https://i.ytimg.com/vi/{video_id}/hqdefault.jpg"
    return ""


def video_from_renderer(renderer: dict, *, now: Optional[datetime] = None) -> Optional[Dict[str, Any]]:
    if not isinstance(renderer, dict):
        return None
    vid = _video_id(renderer)
    if not vid:
        return None
    title = yt_text(renderer.get("title") or renderer.get("headline"))
    published = yt_text(renderer.get("publishedTimeText"))
    views = parse_compact_count(yt_text(renderer.get("viewCountText") or renderer.get("viewCount")))
    thumb = _thumbnail_url(renderer, vid)
    create_time = relative_published_to_unix(published, now=now)
    return {
        "id": vid,
        "videoId": vid,
        "text": title,
        "title": title,
        "createTime": create_time,
        "publishedTimeText": published,
        "timestamp": create_time or "",
        "thumbnail": thumb,
        "displayUrl": thumb,
        "videoMeta": {"coverUrl": thumb},
        "viewCount": views,
        "playCount": views,
        "likesCount": 0,
        "likeCount": 0,
        "commentCount": 0,
        "diggCount": 0,
    }


def _walk_strings_for_stats(obj: Any, subscribers: List[int], videos: List[int], depth: int = 0) -> None:
    if depth > 12:
        return
    if isinstance(obj, str):
        lowered = obj.lower()
        if "subscriber" in lowered:
            n = parse_compact_count(obj)
            if n:
                subscribers.append(n)
        elif re.search(r"\bvideos?\b", lowered) and "view" not in lowered:
            n = parse_compact_count(obj)
            if n:
                videos.append(n)
        return
    if isinstance(obj, dict):
        text = yt_text(obj)
        if text and text != str(obj):
            _walk_strings_for_stats(text, subscribers, videos, depth + 1)
        for value in obj.values():
            _walk_strings_for_stats(value, subscribers, videos, depth + 1)
    elif isinstance(obj, list):
        for item in obj[:80]:
            _walk_strings_for_stats(item, subscribers, videos, depth + 1)


def _channel_meta(data: dict) -> Dict[str, Any]:
    meta = (data.get("metadata") or {}).get("channelMetadataRenderer") or {}
    header = data.get("header") or {}
    title = (
        meta.get("title")
        or yt_text((header.get("c4TabbedHeaderRenderer") or {}).get("title"))
        or ""
    )
    description = meta.get("description") or ""
    avatar = ""
    thumbs = ((meta.get("avatar") or {}).get("thumbnails")) or []
    if thumbs and isinstance(thumbs[-1], dict):
        avatar = thumbs[-1].get("url") or ""
    verified = False
    badges = (header.get("c4TabbedHeaderRenderer") or {}).get("badges") or []
    if isinstance(badges, list):
        verified = any("verified" in json.dumps(badge).lower() for badge in badges)

    subscribers: List[int] = []
    videos: List[int] = []
    c4 = header.get("c4TabbedHeaderRenderer") or {}
    sub_text = yt_text(c4.get("subscriberCountText"))
    vid_text = yt_text(c4.get("videosCountText"))
    if sub_text:
        subscribers.append(parse_compact_count(sub_text))
    if vid_text:
        videos.append(parse_compact_count(vid_text))
    _walk_strings_for_stats(header.get("pageHeaderRenderer") or header, subscribers, videos)

    return {
        "title": title,
        "description": description,
        "avatar": avatar,
        "verified": verified,
        "subscriberCount": max(subscribers) if subscribers else 0,
        "videoCount": max(videos) if videos else 0,
        "vanity": (meta.get("vanityChannelUrl") or meta.get("channelUrl") or ""),
    }


def _page_unavailable(html: str) -> bool:
    blob = (html or "").lower()
    return any(
        token in blob
        for token in (
            "this page isn't available",
            "this page isn&#39;t available",
            "404 not found",
            "channel does not exist",
            "this channel doesn't exist",
        )
    )


def profile_from_yt_initial_data(
    data: dict,
    handle: str,
    *,
    results_limit: int = 12,
    now: Optional[datetime] = None,
) -> Dict[str, Any]:
    handle = (handle or "").lstrip("@").strip()
    meta = _channel_meta(data or {})
    renderers = _iter_video_renderers(data or {})
    videos = []
    seen = set()
    for renderer in renderers:
        item = video_from_renderer(renderer, now=now)
        if not item or item["videoId"] in seen:
            continue
        seen.add(item["videoId"])
        videos.append(item)
        if len(videos) >= max(1, int(results_limit or 12)):
            break

    subscriber_count = int(meta.get("subscriberCount") or 0)
    video_count = int(meta.get("videoCount") or 0) or len(videos)
    return {
        "username": handle,
        "uniqueId": handle,
        "fullName": meta.get("title") or handle,
        "nickname": meta.get("title") or handle,
        "description": meta.get("description") or "",
        "signature": meta.get("description") or "",
        "subscriberCount": subscriber_count,
        "followerCount": subscriber_count,
        "followersCount": subscriber_count,
        "videoCount": video_count,
        "postsCount": video_count,
        "isPrivate": False,
        "privateAccount": False,
        "verified": bool(meta.get("verified")),
        "isVerified": bool(meta.get("verified")),
        "avatarUrl": meta.get("avatar") or "",
        "externalUrl": meta.get("vanity") or f"https://www.youtube.com/@{handle}",
        "latestVideos": videos,
    }


def _clean_yt_handle(handle: str) -> str:
    h = (handle or "").strip()
    h = re.sub(r"^https?://(www\.)?youtube\.com/", "", h, flags=re.I)
    h = h.split("?")[0].strip("/")
    if h.lower().startswith("@"):
        h = h[1:]
    if "/" in h and not h.startswith("channel/"):
        h = h.split("/")[0]
    return h.strip()


def _channel_urls(handle: str) -> List[str]:
    h = _clean_yt_handle(handle)
    if h.lower().startswith("channel/"):
        base = f"https://www.youtube.com/{h}"
        return [f"{base}/videos", base, f"{base}/shorts"]
    if h.startswith("UC") and len(h) >= 22 and "/" not in h:
        base = f"https://www.youtube.com/channel/{h}"
        return [f"{base}/videos", base, f"{base}/shorts"]
    encoded = quote(h)
    base = f"https://www.youtube.com/@{encoded}"
    return [f"{base}/videos", base, f"{base}/shorts"]


def _session() -> requests.Session:
    session = requests.Session()
    session.headers.update({
        "User-Agent": random.choice(_UA_POOL),
        "Accept-Language": "en-US,en;q=0.9",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Encoding": "gzip, deflate",
    })
    session.cookies.set("CONSENT", "YES+cb.20210328-17-p0.en+FX+410", domain=".youtube.com")
    session.cookies.set("SOCS", "CAI", domain=".youtube.com")
    return session


def _fetch_html(session: requests.Session, url: str) -> str:
    resp = session.get(url, params={"hl": "en", "gl": "US"}, timeout=20)
    if resp.status_code == 404:
        return ""
    resp.raise_for_status()
    return resp.text or ""


def scrape_youtube(handle: str, results_limit: int = 12) -> Dict[str, Any]:
    """Scrape a public YouTube channel into the onboarding quality shape."""
    handle = _clean_yt_handle(handle)
    if not handle:
        raise InHouseScrapeError("YouTube handle is required")

    session = _session()
    limit = max(1, min(int(results_limit or 12), 30))
    merged: Dict[str, Any] = {}
    videos: List[dict] = []
    seen = set()
    last_html = ""

    try:
        for url in _channel_urls(handle):
            html = _fetch_html(session, url)
            last_html = html or last_html
            if not html:
                continue
            if _page_unavailable(html):
                continue
            data = extract_yt_initial_data(html)
            if not data:
                continue
            piece = profile_from_yt_initial_data(data, handle, results_limit=limit)
            if not merged:
                merged = piece
            else:
                if piece.get("subscriberCount") and not merged.get("subscriberCount"):
                    merged["subscriberCount"] = piece["subscriberCount"]
                    merged["followerCount"] = piece["subscriberCount"]
                    merged["followersCount"] = piece["subscriberCount"]
                if piece.get("videoCount") and (piece["videoCount"] or 0) > (merged.get("videoCount") or 0):
                    merged["videoCount"] = piece["videoCount"]
                    merged["postsCount"] = piece["videoCount"]
                if piece.get("description") and not merged.get("description"):
                    merged["description"] = piece["description"]
                    merged["signature"] = piece["description"]
                if piece.get("avatarUrl") and not merged.get("avatarUrl"):
                    merged["avatarUrl"] = piece["avatarUrl"]
            for item in piece.get("latestVideos") or []:
                vid = item.get("videoId")
                if not vid or vid in seen:
                    continue
                seen.add(vid)
                videos.append(item)
            if len(videos) >= limit and (merged.get("subscriberCount") or merged.get("videoCount")):
                break
    except requests.RequestException as exc:
        raise InHouseScrapeError(f"No YouTube data for @{handle}") from exc

    if last_html and _page_unavailable(last_html) and not merged:
        raise InHouseScrapeError(f"YouTube channel @{handle} not found")
    if not merged:
        raise InHouseScrapeError(f"No YouTube data for @{handle}")

    merged["latestVideos"] = videos[:limit]
    if not merged.get("videoCount"):
        merged["videoCount"] = len(merged["latestVideos"])
        merged["postsCount"] = merged["videoCount"]
    print(f"[InHouse/YT] @{handle} ({len(merged['latestVideos'])} videos, {merged.get('subscriberCount') or 0} subs)")
    return merged
