"""TikTok data for the Gifting Radar, behind one small interface so the actor can be swapped.

Apify runs start async (a search takes 20-90s and Vercel requests can't wait); a later poll
reads the run and its dataset. The token only ever comes from APIFY_TOKEN, server-side.
"""

from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import requests

ACTOR = os.getenv("RADAR_APIFY_ACTOR", "clockworks~tiktok-scraper")
_API = "https://api.apify.com/v2"
# Pay-per-event pricing seen on the actor (Oct 2026): $0.005 per start + $0.003 per result.
EST_START_USD = 0.005
EST_RESULT_USD = 0.0037


class SourceUnavailable(RuntimeError):
    pass


def _headers() -> Dict[str, str]:
    token = (os.getenv("APIFY_TOKEN") or "").strip()
    if not token:
        raise SourceUnavailable("APIFY_TOKEN not configured")
    return {"Authorization": f"Bearer {token}"}


def estimate_cost(videos: int) -> float:
    return round(EST_START_USD + EST_RESULT_USD * max(0, videos), 4)


def run_input(hashtags: List[str], queries: List[str], per_query: int) -> Dict[str, Any]:
    return {
        "hashtags": list(hashtags),
        "searchQueries": list(queries),
        "searchSection": "/video",
        "resultsPerPage": int(per_query),
        "shouldDownloadVideos": False,
        "shouldDownloadCovers": False,
        "shouldDownloadAvatars": False,
        "shouldDownloadSlideshowImages": False,
        "shouldDownloadMusicCovers": False,
        "commentsPerPost": 0,
        "scrapeRelatedVideos": False,
    }


def start_run(hashtags: List[str], queries: List[str], per_query: int) -> str:
    """Kick off one actor run for every stale hashtag + query. Returns the Apify run id."""
    resp = requests.post(f"{_API}/acts/{ACTOR}/runs", headers=_headers(),
                         json=run_input(hashtags, queries, per_query), timeout=30)
    if resp.status_code >= 300:
        raise SourceUnavailable(f"apify start failed: HTTP {resp.status_code}")
    return str(resp.json()["data"]["id"])


def start_posts_run(post_urls: List[str]) -> str:
    """Re-fetch specific videos (fresh cover links). Returns the Apify run id."""
    body = {**run_input([], [], 1), "postURLs": list(post_urls)}
    body.pop("searchQueries", None)
    body.pop("hashtags", None)
    resp = requests.post(f"{_API}/acts/{ACTOR}/runs", headers=_headers(), json=body, timeout=30)
    if resp.status_code >= 300:
        raise SourceUnavailable(f"apify start failed: HTTP {resp.status_code}")
    return str(resp.json()["data"]["id"])


def run_state(run_id: str) -> Dict[str, Any]:
    """{status, cost_usd, dataset_id, finished}."""
    resp = requests.get(f"{_API}/actor-runs/{run_id}", headers=_headers(), timeout=20)
    if resp.status_code >= 300:
        raise SourceUnavailable(f"apify run lookup failed: HTTP {resp.status_code}")
    data = resp.json().get("data") or {}
    status = str(data.get("status") or "")
    return {
        "status": status,
        "finished": status in ("SUCCEEDED", "FAILED", "ABORTED", "TIMED-OUT"),
        "ok": status == "SUCCEEDED",
        "cost_usd": float(data.get("usageTotalUsd") or 0),
        "dataset_id": data.get("defaultDatasetId"),
        "items_so_far": int(((data.get("chargedEventCounts") or {}).get("result")) or 0),
    }


def abort_run(run_id: str) -> None:
    """Stop a slow run; what it already pushed to its dataset stays readable."""
    try:
        requests.post(f"{_API}/actor-runs/{run_id}/abort", headers=_headers(), timeout=20)
    except Exception as err:  # best effort: the run ends on its own anyway
        print(f"[radar] abort failed run={run_id}: {err}")


def fetch_items(dataset_id: str, limit: int = 400) -> List[Dict[str, Any]]:
    resp = requests.get(f"{_API}/datasets/{dataset_id}/items", headers=_headers(),
                        params={"clean": 1, "limit": int(limit)}, timeout=60)
    if resp.status_code >= 300:
        raise SourceUnavailable(f"apify dataset fetch failed: HTTP {resp.status_code}")
    items = resp.json()
    return items if isinstance(items, list) else []


def _when(item: Dict[str, Any]) -> Optional[datetime]:
    raw = item.get("createTimeISO")
    if raw:
        try:
            return datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
        except ValueError:
            pass
    try:
        return datetime.fromtimestamp(int(item.get("createTime")), tz=timezone.utc)
    except (TypeError, ValueError, OSError):
        return None


def normalize(item: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Actor output -> the radar's video object. None for rows without an id or author."""
    vid = str(item.get("id") or "").strip()
    author = item.get("authorMeta") or {}
    handle = str(author.get("name") or "").strip().lstrip("@").lower()
    if not vid or not handle:
        return None
    mentions = [str(m.get("name") or "").strip().lstrip("@").lower()
                for m in item.get("detailedMentions") or [] if isinstance(m, dict)]
    return {
        "tiktok_id": vid,
        "url": item.get("webVideoUrl") or f"https://www.tiktok.com/@{handle}/video/{vid}",
        "caption": str(item.get("text") or "")[:2000],
        "hashtags": [str(h.get("name") or "").lower() for h in item.get("hashtags") or []
                     if isinstance(h, dict) and h.get("name")],
        "mentions": [m for m in mentions if m],
        "mention_names": [str(m).strip().lstrip("@") for m in item.get("mentions") or [] if m],
        "views": int(item.get("playCount") or 0),
        "likes": int(item.get("diggCount") or 0),
        "created_at": _when(item),
        "cover_url": (item.get("videoMeta") or {}).get("coverUrl") or None,
        "is_ad": bool(item.get("isAd")),
        "is_sponsored": bool(item.get("isSponsored")),
        "language": str(item.get("textLanguage") or "").lower() or None,
        "query": item.get("searchQuery") or None,
        "creator": {
            "handle": handle,
            "followers": int(author.get("fans") or 0),
            "region": author.get("region") or None,
            "bio": str(author.get("signature") or "")[:500],
        },
    }
