"""
In-house Instagram / TikTok profile scrapers (sole social scrape path).

Returns stable profile shapes for CreatorProfileScraper.process_scrape()
and BrandContextEnricher.

TikTok: profile SSR (followers + bio) + /embed/@user (latest videos).
Instagram (same required fields as TikTok — no partial scrapes):
  1) Official APIs when session/proxy allows
  2) /{handle}/embed/ with facebookexternalhit UA — TT-equivalent
     (followers + recent posts with thumbs/captions)
  3) imginn / user_feed / shortcode GraphQL as secondary post sources
  4) crawler / search only to fill bio gaps (never alone)

Required fields (public): followers, bio, latest post.
"""

from __future__ import annotations

import json
import os
import random
import re
import time
import uuid
import codecs
from datetime import datetime, timedelta, timezone
from html import unescape
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import quote, quote_plus

import requests

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

_UA_POOL = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:126.0) Gecko/20100101 Firefox/126.0",
]

_IG_APP_ID = "936619743392459"

# Crawler UAs unlock og/description meta (stats + bio) when browser HTML is login-walled
_IG_CRAWLER_UAS = [
    "Mozilla/5.0 (compatible; Googlebot/2.1; +http://www.google.com/bot.html)",
    "facebookexternalhit/1.1 (+http://www.facebook.com/externalhit_uatext.php)",
    "Mozilla/5.0 (compatible; bingbot/2.0; +http://www.bing.com/bingbot.htm)",
]

# Optional authenticated cookie — makes web_profile_info reliable when IG login-walls guests.
# Export a browser sessionid for instagram.com into INSTAGRAM_SESSIONID / IG_SESSIONID.
_IG_SESSION_COOKIE = (
    (os.getenv("INSTAGRAM_SESSIONID") or os.getenv("IG_SESSIONID") or "").strip()
)

# Residential / rotating proxy when Instagram 429s the server IP.
# Set IG_PROXY to a REAL provider URL, e.g.:
#   http://USERNAME:PASSWORD@brd.superproxy.io:22225
# Do NOT paste the documentation placeholder (user:pass@residential-proxy:port).
_IG_PROXY_RAW = (os.getenv("IG_PROXY") or "").strip().strip('"').strip("'")


class InHouseScrapeError(Exception):
    """Raised when in-house scrape cannot produce usable profile data."""


def _normalize_proxy_url(raw: str) -> Optional[str]:
    """Validate proxy URL; ignore docs placeholders so scrapes don't all fail."""
    raw = (raw or "").strip().strip('"').strip("'")
    if not raw:
        return None
    lowered = raw.lower()
    # Common copy-paste placeholders from docs / env templates
    if any(
        token in lowered
        for token in (
            "residential-proxy",
            "user:pass@",
            "username:password@",
            "your-proxy",
            "proxy-host",
            "example.com",
            "host:port",
            "://user:",
        )
    ):
        print(
            "[InHouse/IG] IG_PROXY looks like a placeholder "
            "(e.g. user:pass@residential-proxy:port) — ignoring. "
            "Set a real residential proxy URL from your provider."
        )
        return None
    if "://" not in raw:
        raw = "http://" + raw
    try:
        from urllib.parse import urlparse

        parsed = urlparse(raw)
        if parsed.scheme not in ("http", "https", "socks5", "socks5h", "socks4"):
            print(f"[InHouse/IG] unsupported proxy scheme={parsed.scheme!r} — ignoring")
            return None
        if not parsed.hostname:
            print("[InHouse/IG] IG_PROXY missing hostname — ignoring")
            return None
        if parsed.hostname in ("residential-proxy", "host", "example", "localhost"):
            print(f"[InHouse/IG] placeholder proxy host={parsed.hostname} — ignoring")
            return None
        # "port" as the port number means someone left the template literal
        if parsed.port is None and raw.rstrip("/").endswith(":port"):
            print("[InHouse/IG] IG_PROXY has literal ':port' — ignoring")
            return None
        return raw
    except Exception as e:
        print(f"[InHouse/IG] IG_PROXY parse failed: {e} — ignoring")
        return None


_IG_PROXY = _normalize_proxy_url(_IG_PROXY_RAW)


def _proxies() -> Optional[Dict[str, str]]:
    if not _IG_PROXY:
        return None
    return {"http": _IG_PROXY, "https": _IG_PROXY}


def _session() -> requests.Session:
    s = requests.Session()
    s.headers.update({
        "User-Agent": random.choice(_UA_POOL),
        "Accept-Language": "en-US,en;q=0.9",
        "Accept-Encoding": "gzip, deflate",
        "Connection": "keep-alive",
    })
    proxies = _proxies()
    if proxies:
        s.proxies.update(proxies)
        # Log host only — never credentials
        try:
            from urllib.parse import urlparse

            host = urlparse(_IG_PROXY).hostname or "unknown"
            port = urlparse(_IG_PROXY).port
            print(f"[InHouse/IG] proxy enabled host={host}:{port or 'default'}")
        except Exception:
            print("[InHouse/IG] proxy enabled")
    elif _IG_PROXY_RAW:
        # Invalid proxy was set — continue without it (better than total failure)
        print("[InHouse/IG] continuing without proxy")
    return s


def _http_get(url: str, **kwargs):
    """requests.get with optional IG_PROXY (for non-session calls)."""
    proxies = _proxies()
    if proxies and "proxies" not in kwargs:
        kwargs["proxies"] = proxies
    return requests.get(url, **kwargs)


def _jitter(lo: float = 0.3, hi: float = 1.0) -> None:
    time.sleep(random.uniform(lo, hi))


def _clean_handle(handle: str) -> str:
    return (handle or "").lstrip("@").strip()


def _diy_bio_ok(bio: str) -> bool:
    text = (bio or "").strip().lower()
    return bool(text) and text not in ("undefined", "null", "none")


# Hard ceiling: no personal IG account has ≥1B followers; SERP pages often quote
# Instagram's "2 billion users" marketing and our parsers used to save that.
_IG_MAX_PLAUSIBLE_FOLLOWERS = 750_000_000
# Search snippets are noisier — keep UGC-range; celebrities go through Apify/API.
_IG_SEARCH_MAX_FOLLOWERS = 50_000_000


def _ig_plausible_followers(n: int, *, search_context: bool = False) -> bool:
    """Reject platform-marketing / garbage follower counts (e.g. 2_000_000_000)."""
    try:
        n = int(n or 0)
    except (TypeError, ValueError):
        return False
    if n <= 0:
        return False
    cap = _IG_SEARCH_MAX_FOLLOWERS if search_context else _IG_MAX_PLAUSIBLE_FOLLOWERS
    return n <= cap


def diy_scrape_is_acceptable(
    profile: Dict[str, Any], platform: str, *, allow_partial: bool = False
) -> bool:
    """
    Required bar for TikTok + Instagram DIY:
      public  → followers + at least one latest post (bio preferred, not required)
      private → followers (posts may be hidden; bio preferred, not required)
      allow_partial → followers only (IG IP wall / onboarding rescue)

    Empty bio is accepted when we have followers + content proof — AI Manager
    then flags missing bio as a critical optimization.
    """
    if not profile:
        return False

    if platform == "instagram":
        handle = profile.get("username") or ""
        followers = int(profile.get("followersCount") or 0)
        is_private = bool(profile.get("isPrivate"))
        latest = profile.get("latestPosts") or []
        bio = profile.get("biography") or ""
        if not _ig_plausible_followers(followers):
            return False
        # Synthetic onboarding bios must never count as real profile data
        if re.match(rf"^UGC creator @{re.escape(str(handle))}$", (bio or "").strip(), re.I):
            return False
    else:
        handle = profile.get("uniqueId") or ""
        followers = int(profile.get("followerCount") or 0)
        is_private = bool(profile.get("privateAccount"))
        latest = profile.get("latestVideos") or []

    if not handle:
        return False
    if followers <= 0:
        return False
    if is_private or allow_partial or profile.get("_partial_scrape"):
        return True
    return len(latest) > 0


# ===========================================================================
# Instagram
# ===========================================================================

def scrape_instagram(handle: str, results_limit: int = 12) -> Dict[str, Any]:
    """
    Scrape Instagram profile + recent posts into the shared profile shape.

    Same required fields as TikTok DIY: followers, bio, latest posts.
    Never returns partial/search-only profiles — incomplete scrapes raise.

    Cascade (mirrors TikTok SSR → embed):
      1) web_profile_info / mobile API / GraphQL / HTML
      2) /{handle}/embed/ via facebookexternalhit (followers + posts) — primary DIY path
      3) Crawler HTML / search snippets (bio gap-fill only)
      4) imginn / mobile user feed (extra posts)
      5) shortcode GraphQL enrichment for thin posts
    """
    handle = _clean_handle(handle).lower()
    if not handle:
        raise InHouseScrapeError("Instagram handle is required")
    if not re.match(r"^[a-z0-9._]+$", handle):
        raise InHouseScrapeError("Invalid Instagram username format")

    session = _session()
    limit = max(1, min(int(results_limit or 12), 50))
    _ig_warm_session(session, handle)
    ig_walled = {"hit": False}  # mutable so helpers can mark 429 walls

    source = "none"
    user = _ig_web_profile_info(session, handle, ig_walled=ig_walled)
    if user:
        source = "web_api"
    if not user:
        user = _ig_mobile_profile_info(session, handle, ig_walled=ig_walled)
        if user:
            source = "mobile_api"
    if not user:
        user = _ig_graphql_a1(session, handle)
        if user:
            source = "graphql"
    if not user:
        user = _ig_from_html(session, handle, ig_walled=ig_walled)
        if user:
            source = "html_meta"

    posts: List[Dict[str, Any]] = []
    if user:
        posts = _ig_extract_posts_from_user(user, limit)

    # Profile embed = TikTok /embed/@user equivalent (works without session/proxy)
    if (not posts or not user or _ig_user_is_thin(user)) and not (user or {}).get("is_private"):
        embed_user, embed_posts = _ig_from_profile_embed(handle, limit)
        if embed_posts and not posts:
            posts = embed_posts
            if "embed" not in source:
                source = f"{source}+embed" if source != "none" else "embed"
        if embed_user:
            if not user:
                user = embed_user
                if source == "none":
                    source = "embed"
            else:
                _ig_fill_user_gaps(user, embed_user)
                if "embed" not in source:
                    source = f"{source}+embed"

    # Prefer embed full_name as bio before SERP (avoids junk follower counts from search)
    if user and not _diy_bio_ok(user.get("biography") or ""):
        name = (user.get("full_name") or "").strip()
        if name and name.lower().lstrip("@") != handle:
            user["biography"] = name
            print(f"[InHouse/IG] bio from full_name len={len(name)}")

    if not user or _ig_user_is_thin(user) or not str((user or {}).get("pk") or "").isdigit():
        crawler_user = _ig_from_crawler_html(handle, ig_walled=ig_walled)
        if crawler_user:
            if not user:
                user = crawler_user
                source = f"{source}+crawler" if source != "none" else "crawler_meta"
            else:
                _ig_fill_user_gaps(user, crawler_user)
                if crawler_user.get("pk"):
                    user["pk"] = crawler_user["pk"]
                    user["id"] = crawler_user["pk"]
                if "crawler" not in source:
                    source = f"{source}+crawler"

    # Search is bio/followers gap-fill only — never the sole source of truth
    if user and _ig_user_is_thin(user):
        search_user = _ig_from_search_snippets(handle)
        if search_user:
            _ig_fill_user_gaps(user, search_user)
            if "search" not in source:
                source = f"{source}+search"

    if user and not posts and not user.get("is_private"):
        html_posts = _ig_posts_from_html(session, handle, limit)
        if html_posts:
            posts = html_posts

    # Imginn secondary mirror
    if (not posts or _ig_user_is_thin(user)) and not (user or {}).get("is_private"):
        mirror_user, mirror_posts = _ig_from_imginn(session, handle, limit)
        if mirror_posts and not posts:
            posts = mirror_posts
            if "imginn" not in source:
                source = f"{source}+imginn" if source != "none" else "imginn"
        if mirror_user:
            if not user:
                user = mirror_user
                if source == "none":
                    source = "imginn"
            else:
                _ig_fill_user_gaps(user, mirror_user)

    # Mobile user feed — real posts when mirrors fail (needs numeric pk)
    if not posts and not (user or {}).get("is_private"):
        pk = str((user or {}).get("pk") or (user or {}).get("id") or "").strip()
        if not pk.isdigit():
            pk = _ig_lookup_user_id(handle)
            if pk and user:
                user["pk"] = pk
                user["id"] = pk
        if pk.isdigit():
            feed_posts = _ig_posts_from_user_feed(session, pk, handle, limit)
            if feed_posts:
                posts = feed_posts
                if "user_feed" not in source:
                    source = f"{source}+user_feed" if source != "none" else "user_feed"

    # Enrich thin posts (missing thumb/caption) via shortcode GraphQL
    if posts:
        posts = _ig_enrich_posts_via_shortcode(session, posts, limit=min(limit, 6))

    # Followers/name from a concrete post when profile endpoints are walled
    if posts and (not user or _ig_user_is_thin(user)):
        shortcode = next((p.get("shortCode") for p in posts if p.get("shortCode")), "")
        if shortcode:
            owner = _ig_owner_from_shortcode(session, shortcode)
            if owner:
                if not user:
                    user = owner
                    source = f"{source}+shortcode" if source != "none" else "shortcode"
                else:
                    _ig_fill_user_gaps(user, owner)
                    if "shortcode" not in source:
                        source = f"{source}+shortcode"

    # Bio fallbacks from real IG fields only (never invent "UGC creator @handle")
    # Do this BEFORE search so we don't SERP-hammer when embed already has a name/captions.
    if user and not _diy_bio_ok(user.get("biography") or ""):
        name = (user.get("full_name") or "").strip()
        if name and ("|" in name or re.search(
            r"\b(ugc|skincare|beauty|makeup|lifestyle|creator)\b", name, re.I
        )):
            user["biography"] = name
            print(f"[InHouse/IG] bio from display name len={len(name)}")
        elif name and name.lower().lstrip("@") != handle:
            user["biography"] = name
            print(f"[InHouse/IG] bio from full_name len={len(name)}")
        else:
            for p in posts:
                cap = (p.get("caption") or "").strip()
                if len(cap) >= 8:
                    user["biography"] = cap.split("\n")[0][:160].strip()
                    print(f"[InHouse/IG] bio from post caption len={len(user['biography'])}")
                    break

    # Final bio/followers gap-fill via search (still never return search-only)
    if user and _ig_user_is_thin(user):
        search_user = _ig_from_search_snippets(handle)
        if search_user:
            _ig_fill_user_gaps(user, search_user)
            if "search" not in source:
                source = f"{source}+search"

    if not user:
        if ig_walled.get("hit"):
            raise InHouseScrapeError(
                f"Instagram is rate-limiting this server (HTTP 429) — no profile data for @{handle}. "
                "Set IG_PROXY (residential proxy) or INSTAGRAM_SESSIONID on the API host, then retry."
            )
        raise InHouseScrapeError(f"No Instagram data for @{handle}")

    profile = _ig_to_profile_shape(user, posts, handle)

    if not diy_scrape_is_acceptable(profile, "instagram"):
        followers = int(profile.get("followersCount") or 0)
        bio = profile.get("biography") or ""
        missing = []
        if not _ig_plausible_followers(followers):
            missing.append("followers")
        if not _diy_bio_ok(bio):
            missing.append("bio")
        if not profile.get("isPrivate") and not (profile.get("latestPosts") or []):
            missing.append("latest_post")
        raise InHouseScrapeError(
            f"Incomplete Instagram profile for @{handle} (missing {', '.join(missing) or 'required fields'}). "
            "Same data as TikTok DIY is required: followers, bio, and a latest post."
        )

    print(f"[InHouse/IG] @{handle} via {source} (posts={len(posts)})")
    return profile


def _ig_user_is_thin(user: Optional[Dict[str, Any]]) -> bool:
    """True when followers or bio are still missing (keep trying alternate sources)."""
    if not user:
        return True
    followers = int((user.get("edge_followed_by") or {}).get("count") or user.get("follower_count") or 0)
    if followers > 0 and not _ig_plausible_followers(followers):
        return True
    bio = (user.get("biography") or "").strip()
    return followers <= 0 or not _diy_bio_ok(bio)


def _ig_warm_session(session: requests.Session, handle: str) -> None:
    """Seed guest/session cookies + CSRF, then open the profile page."""
    try:
        session.cookies.set("ig_did", str(uuid.uuid4()).upper(), domain=".instagram.com")
        session.cookies.set("mid", uuid.uuid4().hex[:28], domain=".instagram.com")
        session_cookie = (
            (os.getenv("INSTAGRAM_SESSIONID") or os.getenv("IG_SESSIONID") or _IG_SESSION_COOKIE or "")
            .strip()
        )
        if session_cookie:
            session.cookies.set("sessionid", session_cookie, domain=".instagram.com")
            print("[InHouse/IG] using INSTAGRAM_SESSIONID cookie")
        session.get("https://www.instagram.com/", timeout=10)
        _jitter(0.2, 0.5)
        session.get(
            f"https://www.instagram.com/{handle}/",
            headers={
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Referer": "https://www.instagram.com/",
                "Sec-Fetch-Dest": "document",
                "Sec-Fetch-Mode": "navigate",
                "Sec-Fetch-Site": "same-origin",
                "Upgrade-Insecure-Requests": "1",
            },
            timeout=12,
        )
        _jitter(0.2, 0.6)
    except requests.RequestException:
        pass


def _ig_mark_walled(ig_walled: Optional[Dict], status: int) -> None:
    if ig_walled is not None and status in (429, 401, 403):
        ig_walled["hit"] = True


def _ig_web_profile_info(
    session: requests.Session, handle: str, ig_walled: Optional[Dict] = None
) -> Optional[Dict[str, Any]]:
    url = f"https://www.instagram.com/api/v1/users/web_profile_info/?username={handle}"
    csrf = session.cookies.get("csrftoken") or ""
    headers = {
        "Accept": "*/*",
        "X-IG-App-ID": _IG_APP_ID,
        "X-Requested-With": "XMLHttpRequest",
        "X-ASBD-ID": "129477",
        "X-CSRFToken": csrf,
        "Referer": f"https://www.instagram.com/{handle}/",
        "Sec-Fetch-Dest": "empty",
        "Sec-Fetch-Mode": "cors",
        "Sec-Fetch-Site": "same-origin",
    }
    try:
        resp = session.get(url, headers=headers, timeout=12)
        print(f"[InHouse/IG] web_profile_info status={resp.status_code}")
        _ig_mark_walled(ig_walled, resp.status_code)
        if resp.status_code == 404:
            raise InHouseScrapeError(f"Instagram account @{handle} not found")
        if resp.status_code != 200:
            return None
        data = resp.json()
        user = (data.get("data") or {}).get("user") or {}
        return user or None
    except InHouseScrapeError:
        raise
    except Exception as e:
        print(f"[InHouse/IG] web_profile_info error: {e}")
        return None


def _ig_mobile_profile_info(
    session: requests.Session, handle: str, ig_walled: Optional[Dict] = None
) -> Optional[Dict[str, Any]]:
    url = f"https://i.instagram.com/api/v1/users/web_profile_info/?username={handle}"
    headers = {
        "User-Agent": "Instagram 275.0.0.27.98 Android (33/13; 420dpi; 1080x2400; samsung; SM-G991B; o1s; exynos2100; en_US; 458229258)",
        "Accept": "*/*",
        "X-IG-App-ID": "567067343352427",
    }
    try:
        resp = session.get(url, headers=headers, timeout=12)
        print(f"[InHouse/IG] mobile_profile_info status={resp.status_code}")
        _ig_mark_walled(ig_walled, resp.status_code)
        if resp.status_code != 200:
            return None
        data = resp.json()
        user = (data.get("data") or {}).get("user") or {}
        return user or None
    except Exception as e:
        print(f"[InHouse/IG] mobile_profile_info error: {e}")
        return None


def _ig_graphql_a1(session: requests.Session, handle: str) -> Optional[Dict[str, Any]]:
    """Legacy ?__a=1&__d=dis endpoint (often blocked; cheap to try)."""
    url = f"https://www.instagram.com/{handle}/?__a=1&__d=dis"
    headers = {
        "Accept": "application/json",
        "X-IG-App-ID": _IG_APP_ID,
        "X-Requested-With": "XMLHttpRequest",
        "Referer": f"https://www.instagram.com/{handle}/",
    }
    try:
        resp = session.get(url, headers=headers, timeout=10)
        print(f"[InHouse/IG] graphql_a1 status={resp.status_code}")
        if resp.status_code != 200 or not (resp.text or "").strip():
            return None
        ctype = resp.headers.get("Content-Type", "")
        if "json" not in ctype and "javascript" not in ctype:
            return None
        data = resp.json()
        user = (data.get("graphql") or {}).get("user") or data.get("user") or {}
        return user or None
    except Exception as e:
        print(f"[InHouse/IG] graphql_a1 error: {e}")
        return None


def _ig_from_html(
    session: requests.Session, handle: str, ig_walled: Optional[Dict] = None
) -> Optional[Dict[str, Any]]:
    try:
        resp = session.get(
            f"https://www.instagram.com/{handle}/",
            headers={
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Referer": "https://www.instagram.com/",
            },
            timeout=12,
        )
        print(f"[InHouse/IG] profile html status={resp.status_code}")
        _ig_mark_walled(ig_walled, resp.status_code)
        if resp.status_code == 404:
            raise InHouseScrapeError(f"Instagram account @{handle} not found")
        if resp.status_code != 200:
            return None
        html = resp.text

        shared = re.search(r"window\._sharedData\s*=\s*(\{.+?\});", html)
        if shared:
            try:
                data = json.loads(shared.group(1))
                user = (
                    data.get("entry_data", {})
                    .get("ProfilePage", [{}])[0]
                    .get("graphql", {})
                    .get("user", {})
                )
                if user:
                    return user
            except (json.JSONDecodeError, IndexError, KeyError, TypeError):
                pass

        # Pull key fields from embedded JSON when full object isn't parseable
        followers = _re_int(html, r'"edge_followed_by"\s*:\s*\{\s*"count"\s*:\s*(\d+)')
        following = _re_int(html, r'"edge_follow"\s*:\s*\{\s*"count"\s*:\s*(\d+)')
        media = _re_int(html, r'"edge_owner_to_timeline_media"\s*:\s*\{\s*"count"\s*:\s*(\d+)')
        is_private = bool(re.search(r'"is_private"\s*:\s*true', html, re.I))
        is_verified = bool(re.search(r'"is_verified"\s*:\s*true', html, re.I))
        bio = _re_str(html, r'"biography"\s*:\s*"((?:\\.|[^"\\])*)"')
        full_name = _re_str(html, r'"full_name"\s*:\s*"((?:\\.|[^"\\])*)"')
        external = _re_str(html, r'"external_url"\s*:\s*"((?:\\.|[^"\\])*)"')

        # Login-wall pages still expose og:description with public counts
        if not followers and not media:
            meta_user = _ig_user_from_og_meta(html, handle)
            if meta_user:
                return meta_user

        if followers or media:
            return {
                "username": handle,
                "full_name": full_name or "",
                "biography": bio or "",
                "external_url": external or "",
                "edge_followed_by": {"count": followers},
                "edge_follow": {"count": following},
                "edge_owner_to_timeline_media": {"count": media, "edges": []},
                "is_private": is_private,
                "is_verified": is_verified,
                "is_business_account": False,
                "business_category_name": "",
            }
    except InHouseScrapeError:
        raise
    except Exception as e:
        print(f"[InHouse/IG] html fallback error: {e}")
    return None


def _ig_from_search_snippets(handle: str) -> Optional[Dict[str, Any]]:
    """
    Fallback: Startpage / DuckDuckGo / Bing snippets mirror IG og:description
    (followers + bio) when Instagram login-walls the profile page.
    """
    queries = [
        f'"{handle}" Instagram',
        f"site:instagram.com/{handle}",
        f"@{handle} Instagram followers",
    ]
    urls: List[Tuple[str, str]] = []
    for q in queries:
        urls.append(("startpage", f"https://www.startpage.com/sp/search?query={quote_plus(q)}"))
        urls.append(("ddg", f"https://html.duckduckgo.com/html/?q={quote_plus(q)}"))
        urls.append(("bing", f"https://www.bing.com/search?q={quote_plus(q)}&setlang=en"))

    best: Optional[Dict[str, Any]] = None
    best_score = -1
    for engine, url in urls:
        try:
            resp = _http_get(
                url,
                headers={
                    "User-Agent": random.choice(_UA_POOL),
                    "Accept": "text/html",
                    "Accept-Language": "en-US,en;q=0.9",
                },
                timeout=15,
            )
            print(f"[InHouse/IG] search snippets status={resp.status_code} via={engine}")
            if resp.status_code != 200 or not resp.text:
                continue
            parsed = _ig_parse_search_snippet_html(resp.text, handle)
            if not parsed:
                continue
            score = _ig_search_candidate_score(parsed)
            if score < 0:
                continue
            if not _ig_user_is_thin(parsed):
                return parsed
            if score > best_score:
                best = parsed
                best_score = score
        except Exception as e:
            print(f"[InHouse/IG] search snippets error: {e}")
    return best


def _ig_search_candidate_score(user: Dict[str, Any]) -> int:
    """Rank SERP candidates. Implausible follower counts score -1 (reject)."""
    followers = int((user.get("edge_followed_by") or {}).get("count") or 0)
    following = int((user.get("edge_follow") or {}).get("count") or 0)
    media = int((user.get("edge_owner_to_timeline_media") or {}).get("count") or 0)
    bio = (user.get("biography") or "").strip()
    if followers > 0 and not _ig_plausible_followers(followers, search_context=True):
        return -1
    score = 0
    if followers > 0:
        score += 2
    if _diy_bio_ok(bio):
        score += 4
    if following > 0 and media > 0 and followers > 0:
        score += 3  # classic IG meta trio — high confidence
    return score


def _ig_sanitize_search_followers(followers: int) -> int:
    """Zero out SERP junk (Instagram '2B users' marketing, etc.)."""
    if _ig_plausible_followers(followers, search_context=True):
        return followers
    if followers > 0:
        print(f"[InHouse/IG] rejecting implausible search followers={followers}")
    return 0


def _ig_parse_search_snippet_html(html: str, handle: str) -> Optional[Dict[str, Any]]:
    """Pure parser for SERP HTML / JSON blobs that quote IG profile meta."""
    text = unescape(html or "")
    if not text:
        return None

    followers = following = media = 0
    handle_pat = rf"(?:<[^>]+>)*\s*{re.escape(handle)}\s*(?:</[^>]+>)*"
    # Classic IG meta: "554 Followers, 50 Following, 136 Posts"
    m = re.search(
        r"([\d,.]+[KMBkmb]?)\s*Followers,\s*([\d,.]+[KMBkmb]?)\s*Following,\s*([\d,.]+[KMBkmb]?)\s*Posts",
        text,
        re.I,
    )
    if m:
        followers = _ig_sanitize_search_followers(_parse_compact_count(m.group(1)))
        following = _parse_compact_count(m.group(2))
        media = _parse_compact_count(m.group(3))
    # Startpage / modern SERP: "552 followers · 51 following · 135 posts · @handle: “bio…"
    if followers <= 0:
        m = re.search(
            rf"([\d,.]+[KMBkmb]?)\s*followers\s*[·•|,]\s*"
            rf"([\d,.]+[KMBkmb]?)\s*following\s*[·•|,]\s*"
            rf"([\d,.]+[KMBkmb]?)\s*posts\s*[·•|,]\s*"
            rf"@?\s*{handle_pat}\s*:\s*[\"“]\s*(.{{8,320}}?)(?:[\"”]|…|\.\.\.|</)",
            text,
            re.I | re.S,
        )
        if m:
            followers = _ig_sanitize_search_followers(_parse_compact_count(m.group(1)))
            following = _parse_compact_count(m.group(2))
            media = _parse_compact_count(m.group(3))
            bio = re.sub(r"\s+", " ", m.group(4)).strip()
            bio = re.sub(r"</?b>", "", bio).strip(" .…")
            print(f"[InHouse/IG] search followers={followers} bio_len={len(bio)}")
            if followers <= 0 and not bio:
                return None
            return {
                "username": handle,
                "full_name": "",
                "biography": bio,
                "external_url": "",
                "edge_followed_by": {"count": followers},
                "edge_follow": {"count": following},
                "edge_owner_to_timeline_media": {"count": media, "edges": []},
                "is_private": False,
                "is_verified": False,
                "is_business_account": False,
                "business_category_name": "",
            }

    if followers <= 0:
        for fm in re.finditer(
            rf"{re.escape(handle)}.{{0,220}}?([\d,.]+[KMBkmb]?)\s*[Ff]ollowers",
            text,
            re.I | re.S,
        ):
            followers = _ig_sanitize_search_followers(_parse_compact_count(fm.group(1)))
            if followers > 0:
                break
    if followers <= 0:
        # Looser: "1.2K Followers" anywhere near the handle / instagram.com/handle
        for fm in re.finditer(
            rf"(?:instagram\.com/{re.escape(handle)}|{re.escape(handle)}).{{0,400}}?"
            rf"([\d,.]+[KMBkmb]?)\s*[Ff]ollowers",
            text,
            re.I | re.S,
        ):
            followers = _ig_sanitize_search_followers(_parse_compact_count(fm.group(1)))
            if followers > 0:
                break
    if followers <= 0:
        # Bing/Google sometimes invert order: "1,234 Followers · @handle"
        for fm in re.finditer(
            rf"([\d,.]+[KMBkmb]?)\s*[Ff]ollowers.{{0,160}}?@?{re.escape(handle)}\b",
            text,
            re.I | re.S,
        ):
            followers = _ig_sanitize_search_followers(_parse_compact_count(fm.group(1)))
            if followers > 0:
                break

    bio = ""
    bio_m = re.search(r'on Instagram:\s*["“](.{8,240}?)["”]', text, re.I | re.S)
    if bio_m:
        bio = re.sub(r"\s+", " ", bio_m.group(1)).strip()
    if not bio:
        # "@handle: “Soft Girly Beauty Creator…"
        bio_m = re.search(
            rf"@?\s*{handle_pat}\s*:\s*[\"“]\s*(.{{8,320}}?)(?:[\"”]|…|\.\.\.|</)",
            text,
            re.I | re.S,
        )
        if bio_m:
            bio = re.sub(r"\s+", " ", bio_m.group(1)).strip()
            bio = re.sub(r"</?b>", "", bio).strip(" .…")
    if not bio:
        email_m = re.search(r"([\w.+-]+@[\w.-]+\.\w{2,})", text)
        if email_m:
            idx = email_m.start()
            window = re.sub(r"\s+", " ", text[max(0, idx - 120) : idx + 80]).strip()
            if handle.lower() in window.lower() or "instagram" in window.lower():
                bio = window

    if followers <= 0 and not bio:
        return None

    print(f"[InHouse/IG] search followers={followers} bio_len={len(bio)}")
    return {
        "username": handle,
        "full_name": "",
        "biography": bio,
        "external_url": "",
        "edge_followed_by": {"count": followers},
        "edge_follow": {"count": following},
        "edge_owner_to_timeline_media": {"count": media, "edges": []},
        "is_private": False,
        "is_verified": False,
        "is_business_account": False,
        "business_category_name": "",
    }


def _ig_owner_from_shortcode(session: requests.Session, shortcode: str) -> Optional[Dict[str, Any]]:
    """
    Resolve followers/name from a public post shortcode.
    Mirrors TikTok embed userInfo: GraphQL owner first, then bot post-embed HTML.
    """
    if not shortcode:
        return None
    owner = _ig_owner_from_shortcode_graphql(session, shortcode)
    if owner and not _ig_user_is_thin(owner):
        return owner
    embed_owner = _ig_owner_from_post_embed(shortcode)
    if embed_owner:
        if owner:
            _ig_fill_user_gaps(owner, embed_owner)
            return owner
        return embed_owner
    return owner


def _ig_owner_from_shortcode_graphql(
    session: requests.Session, shortcode: str
) -> Optional[Dict[str, Any]]:
    """Instagram GraphQL shortcode media → owner edge_followed_by + full_name."""
    try:
        resp = session.get(
            "https://www.instagram.com/graphql/query",
            params={
                "doc_id": "10015901848480474",
                "variables": json.dumps({"shortcode": shortcode}),
            },
            headers={
                "X-IG-App-ID": _IG_APP_ID,
                "X-Requested-With": "XMLHttpRequest",
                "Accept": "*/*",
                "Referer": f"https://www.instagram.com/p/{shortcode}/",
            },
            timeout=15,
        )
        print(f"[InHouse/IG] shortcode graphql status={resp.status_code}")
        if resp.status_code != 200 or not resp.text:
            return None
        data = resp.json()
        media = ((data.get("data") or {}).get("xdt_shortcode_media") or {})
        owner = media.get("owner") or {}
        if not owner:
            return None
        followers = int((owner.get("edge_followed_by") or {}).get("count") or 0)
        media_count = int((owner.get("edge_owner_to_timeline_media") or {}).get("count") or 0)
        username = (owner.get("username") or "").strip()
        print(f"[InHouse/IG] shortcode owner followers={followers} posts={media_count}")
        return {
            "username": username,
            "full_name": owner.get("full_name") or "",
            "biography": "",  # not in this query — filled by search/crawler
            "external_url": "",
            "profile_pic_url": owner.get("profile_pic_url") or "",
            "edge_followed_by": {"count": followers},
            "edge_follow": {"count": 0},
            "edge_owner_to_timeline_media": {"count": media_count, "edges": []},
            "is_private": bool(owner.get("is_private")),
            "is_verified": bool(owner.get("is_verified")),
            "is_business_account": False,
            "business_category_name": "",
        }
    except Exception as e:
        print(f"[InHouse/IG] shortcode graphql error: {e}")
        return None


def _ig_fetch_embed_post_meta(
    session: requests.Session, shortcode: str
) -> Optional[Dict[str, Any]]:
    """
    Pull likes/comments/views + display URL for one post via shortcode GraphQL.
    Used by PR-Ready auto-kit enrichment when scrape rows lack engagement.
    """
    shortcode = (shortcode or "").strip()
    if not shortcode or not re.match(r"^[A-Za-z0-9_-]{5,}$", shortcode):
        return None
    if re.match(r"^[a-z]{2}_[A-Z]{2}$", shortcode):
        return None
    try:
        resp = session.get(
            "https://www.instagram.com/graphql/query",
            params={
                "doc_id": "10015901848480474",
                "variables": json.dumps({"shortcode": shortcode}),
            },
            headers={
                "X-IG-App-ID": _IG_APP_ID,
                "X-Requested-With": "XMLHttpRequest",
                "Accept": "*/*",
                "Referer": f"https://www.instagram.com/p/{shortcode}/",
            },
            timeout=15,
        )
        if resp.status_code != 200 or not resp.text:
            return None
        media = ((resp.json().get("data") or {}).get("xdt_shortcode_media") or {})
        if not media:
            return None
        likes = (
            (media.get("edge_media_preview_like") or {}).get("count")
            or (media.get("edge_liked_by") or {}).get("count")
            or media.get("like_count")
            or 0
        )
        comments = (
            (media.get("edge_media_to_parent_comment") or {}).get("count")
            or (media.get("edge_media_to_comment") or {}).get("count")
            or media.get("comment_count")
            or 0
        )
        views = media.get("video_view_count") or media.get("video_play_count") or 0
        display = media.get("display_url") or media.get("thumbnail_src") or ""
        caption = ""
        edges = (media.get("edge_media_to_caption") or {}).get("edges") or []
        if edges:
            caption = ((edges[0].get("node") or {}).get("text") or "").strip()
        ts = media.get("taken_at_timestamp") or 0
        timestamp = ""
        if isinstance(ts, (int, float)) and ts > 0:
            timestamp = datetime.fromtimestamp(int(ts), tz=timezone.utc).isoformat().replace(
                "+00:00", "Z"
            )
        return {
            "likesCount": int(likes or 0),
            "commentsCount": int(comments or 0),
            "videoViewCount": int(views or 0),
            "displayUrl": display or "",
            "caption": caption,
            "timestamp": timestamp,
        }
    except Exception as e:
        print(f"[InHouse/IG] embed/shortcode meta error: {e}")
        return None


def _ig_owner_from_post_embed(shortcode: str) -> Optional[Dict[str, Any]]:
    """facebookexternalhit embed HTML exposes '136 posts · 554 followers'."""
    try:
        resp = requests.get(
            f"https://www.instagram.com/p/{shortcode}/embed/captioned/",
            headers={
                "User-Agent": _IG_CRAWLER_UAS[1],  # facebookexternalhit
                "Accept": "text/html",
                "Accept-Language": "en-US,en;q=0.9",
            },
            timeout=15,
        )
        print(f"[InHouse/IG] post embed status={resp.status_code} len={len(resp.text or '')}")
        if resp.status_code != 200 or not resp.text:
            return None
        return _ig_parse_post_embed_owner(resp.text)
    except Exception as e:
        print(f"[InHouse/IG] post embed error: {e}")
        return None


def _ig_parse_post_embed_owner(html: str) -> Optional[Dict[str, Any]]:
    text = html or ""
    m = re.search(
        r'class="HoverCardStatus"><span>([\d,.]+[KMBkmb]?)\s*posts\s*[·•]\s*([\d,.]+[KMBkmb]?)\s*followers</span>',
        text,
        re.I,
    )
    if not m:
        m = re.search(
            r"([\d,.]+[KMBkmb]?)\s*posts\s*[·•]\s*([\d,.]+[KMBkmb]?)\s*followers",
            text,
            re.I,
        )
    if not m:
        return None
    media = _parse_compact_count(m.group(1))
    followers = _parse_compact_count(m.group(2))
    name_m = re.search(r'class="Username"[^>]*>([^<]+)</span>', text, re.I)
    username = (name_m.group(1).strip() if name_m else "")
    print(f"[InHouse/IG] post embed followers={followers} posts={media}")
    return {
        "username": username,
        "full_name": "",
        "biography": "",
        "external_url": "",
        "edge_followed_by": {"count": followers},
        "edge_follow": {"count": 0},
        "edge_owner_to_timeline_media": {"count": media, "edges": []},
        "is_private": False,
        "is_verified": False,
        "is_business_account": False,
        "business_category_name": "",
    }


def _ig_from_crawler_html(
    handle: str, ig_walled: Optional[Dict] = None
) -> Optional[Dict[str, Any]]:
    """
    Fetch profile HTML with crawler UAs. Instagram still exposes og/description
    meta (followers + bio) to Googlebot/facebookexternalhit when browser HTML is walled.
    """
    best: Optional[Dict[str, Any]] = None
    for ua in _IG_CRAWLER_UAS:
        try:
            resp = _http_get(
                f"https://www.instagram.com/{handle}/",
                headers={
                    "User-Agent": ua,
                    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                    "Accept-Language": "en-US,en;q=0.9",
                },
                timeout=12,
            )
            print(f"[InHouse/IG] crawler html status={resp.status_code} ua={ua.split('/')[0]}")
            _ig_mark_walled(ig_walled, resp.status_code)
            if resp.status_code == 404:
                raise InHouseScrapeError(f"Instagram account @{handle} not found")
            if resp.status_code != 200 or not resp.text:
                continue
            user = _ig_user_from_og_meta(resp.text, handle)
            if not user:
                continue
            if not _ig_user_is_thin(user):
                return user
            best = user
        except InHouseScrapeError:
            raise
        except Exception as e:
            print(f"[InHouse/IG] crawler html error: {e}")
    return best


def _ig_user_from_og_meta(html: str, handle: str) -> Optional[Dict[str, Any]]:
    """
    Parse crawler/browser meta tags:
      og:description → "554 Followers, 50 Following, 136 Posts - See Instagram photos..."
      description    → "... on Instagram: \"bio text\""
    """
    og = unescape((_ig_meta_content(html, "og:description") or "").replace("&#064;", "@"))
    desc = unescape((_ig_meta_content(html, "description") or "").replace("&#064;", "@"))
    blob = og or desc
    if not blob:
        return None

    m = re.search(
        r"([\d,.]+[KMBkmb]?)\s*Followers,\s*([\d,.]+[KMBkmb]?)\s*Following,\s*([\d,.]+[KMBkmb]?)\s*Posts",
        blob,
        re.I,
    )
    if not m:
        return None
    followers = _parse_compact_count(m.group(1))
    following = _parse_compact_count(m.group(2))
    media = _parse_compact_count(m.group(3))

    full_name = ""
    for src in (og, desc):
        if not src:
            continue
        nm = re.search(r"from\s+(.+?)\s*\(@", src, re.I)
        if not nm:
            nm = re.search(rf"Posts\s*-\s*(.+?)\s*\(@{re.escape(handle)}\)", src, re.I)
        if nm:
            candidate = nm.group(1).strip()
            if candidate and "Followers" not in candidate:
                full_name = candidate
                break
    title = _ig_meta_content(html, "og:title") or ""
    if not full_name and title:
        full_name = title.split("(")[0].strip()
    full_name = unescape(full_name.replace("&#039;", "'"))

    bio = ""
    # Allow short/emoji bios (e.g. "❤️") — require closing quote, not end-anchor only
    bio_m = re.search(r'on Instagram:\s*["“](.+?)["”]\s*$', desc, re.I | re.S)
    if not bio_m:
        bio_m = re.search(r'on Instagram:\s*["“](.+?)["”]', desc, re.I | re.S)
    if bio_m:
        bio = unescape(bio_m.group(1)).replace("\\n", "\n").strip()
        # Keep readable multi-line bios as single-spaced lines for storage/UI
        bio = "\n".join(ln.strip() for ln in bio.splitlines() if ln.strip())
    if bio.lower() in ("undefined", "null", "none"):
        bio = ""

    avatar = _ig_meta_content(html, "og:image") or ""
    pk = _ig_extract_pk_from_html(html)

    print(f"[InHouse/IG] meta followers={followers} posts={media} bio_len={len(bio)} pk={pk or '-'}")
    out: Dict[str, Any] = {
        "username": handle,
        "full_name": full_name,
        "biography": bio,
        "external_url": "",
        "profile_pic_url": avatar,
        "edge_followed_by": {"count": followers},
        "edge_follow": {"count": following},
        "edge_owner_to_timeline_media": {"count": media, "edges": []},
        "is_private": False,
        "is_verified": False,
        "is_business_account": False,
        "business_category_name": "",
    }
    if pk:
        out["pk"] = pk
        out["id"] = pk
    return out


def _ig_extract_pk_from_html(html: str) -> str:
    """Pull numeric Instagram user id from crawler/browser HTML (profilePage_{id})."""
    if not html:
        return ""
    for pat in (
        r"profilePage_(\d{5,})",
        r'"profilePage_(\d{5,})"',
        r'"user_id"\s*:\s*"?(\d{5,})"?',
        r'"profile_id"\s*:\s*"?(\d{5,})"?',
        r'"owner_id"\s*:\s*"?(\d{5,})"?',
        r'"pk"\s*:\s*"?(\d{5,})"?',
    ):
        m = re.search(pat, html)
        if m:
            return m.group(1)
    return ""


def _ig_lookup_user_id(handle: str) -> str:
    """Resolve numeric pk via crawler HTML when profile APIs are walled."""
    handle = _clean_handle(handle).lower()
    if not handle:
        return ""
    for ua in _IG_CRAWLER_UAS:
        try:
            resp = _http_get(
                f"https://www.instagram.com/{handle}/",
                headers={
                    "User-Agent": ua,
                    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                    "Accept-Language": "en-US,en;q=0.9",
                },
                timeout=12,
            )
            if resp.status_code != 200 or not resp.text:
                continue
            pk = _ig_extract_pk_from_html(resp.text)
            if pk:
                print(f"[InHouse/IG] resolved pk={pk} via={ua.split('/')[0]}")
                return pk
        except Exception as e:
            print(f"[InHouse/IG] pk lookup error: {e}")
    return ""


def _ig_posts_from_user_feed(
    session: requests.Session, pk: str, handle: str, limit: int
) -> List[Dict[str, Any]]:
    """
    Mobile feed endpoint — returns real timeline items when imginn is blocked.
    Call at most once per scrape; over-probing burns the IP with 401s.
    """
    pk = str(pk or "").strip()
    if not pk.isdigit():
        return []
    count = max(1, min(int(limit or 12), 12))
    url = f"https://i.instagram.com/api/v1/feed/user/{pk}/?count={count}"
    headers = {
        # iPhone web-app UA historically succeeds with X-IG-App-ID (web) when Android UA 401s
        "User-Agent": (
            "Mozilla/5.0 (iPhone; CPU iPhone OS 16_6 like Mac OS X) "
            "AppleWebKit/605.1.15 (KHTML, like Gecko) Mobile/15E148 "
            "Instagram 192.168.0.5.117"
        ),
        "Accept": "*/*",
        "X-IG-App-ID": _IG_APP_ID,
        "X-ASBD-ID": "129477",
        "X-Requested-With": "XMLHttpRequest",
        "Referer": f"https://www.instagram.com/{handle}/",
        "Origin": "https://www.instagram.com",
    }
    try:
        _jitter(0.25, 0.6)
        resp = session.get(url, headers=headers, timeout=15)
        print(f"[InHouse/IG] user_feed status={resp.status_code} pk={pk}")
        if resp.status_code != 200 or not (resp.text or "").strip():
            return []
        data = resp.json()
        items = data.get("items") or []
        posts: List[Dict[str, Any]] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            post = _ig_node_to_post(item)
            if post.get("displayUrl") or post.get("caption") or post.get("shortCode"):
                posts.append(post)
            if len(posts) >= count:
                break
        print(f"[InHouse/IG] user_feed posts={len(posts)}")
        return posts
    except Exception as e:
        print(f"[InHouse/IG] user_feed error: {e}")
        return []


def _ig_html_head(html: str) -> str:
    """Limit meta parsing to <head> to avoid catastrophic regex on full documents."""
    m = re.search(r"<head[^>]*>(.*?)</head>", html or "", re.I | re.S)
    return m.group(1) if m else (html or "")[:80000]


def _ig_meta_content(html: str, prop: str) -> str:
    """
    Extract a meta content value from <head>.

    IG bios often put newlines inside content="...". Use [\\s\\S] so those match,
    but still stop at the closing quote so we never span into the next tag.
    Inner bio quotes on IG are entity-encoded (&quot;).
    """
    head = _ig_html_head(html)
    # ((?:(?!\1)[\s\S])*) = any char except the opening quote delimiter (incl. newlines)
    patterns = [
        rf'(?:property|name)=["\']{re.escape(prop)}["\'][^>]*?content=(["\'])((?:(?!\1)[\s\S])*)\1',
        rf'content=(["\'])((?:(?!\1)[\s\S])*)\1[^>]*?(?:property|name)=["\']{re.escape(prop)}["\']',
    ]
    for pat in patterns:
        m = re.search(pat, head, re.I)
        if m:
            raw = m.group(2).replace("&#064;", "@").replace("&#x40;", "@").replace("&#X40;", "@")
            return unescape(raw)
    return ""


def _ig_posts_from_html(session: requests.Session, handle: str, limit: int) -> List[Dict[str, Any]]:
    try:
        resp = session.get(f"https://www.instagram.com/{handle}/", timeout=12)
        if resp.status_code != 200:
            return []
        html = resp.text
        shared = re.search(r"window\._sharedData\s*=\s*(\{.+?\});", html)
        if not shared:
            return []
        data = json.loads(shared.group(1))
        user = (
            data.get("entry_data", {})
            .get("ProfilePage", [{}])[0]
            .get("graphql", {})
            .get("user", {})
        )
        return _ig_extract_posts_from_user(user or {}, limit)
    except Exception as e:
        print(f"[InHouse/IG] html posts error: {e}")
        return []


def _ig_unescape_embedded_url(raw: str) -> str:
    """Unescape IG embed HTML URLs like https:\\\\\\/\\\\\\/scontent..."""
    if not raw:
        return ""
    url = raw.replace("\\\\\\/", "/").replace("\\/", "/")
    url = (
        url.replace("\\u00253D", "=")
        .replace("\\u0025", "%")
        .replace("\u00253D", "=")
        .replace("\u0025", "%")
    )
    return url.strip()


def _ig_unescape_embedded_text(raw: str) -> str:
    if not raw:
        return ""
    s = raw.replace("\\/", "/")
    # Embed HTML often double-escapes unicode (\\\\ud83c → needs multiple passes)
    for _ in range(3):
        if "\\u" not in s and "\\n" not in s and "\\U" not in s and "\\x" not in s:
            break
        try:
            nxt = codecs.decode(s, "unicode_escape")
        except Exception:
            break
        if nxt == s:
            break
        s = nxt
    # unicode_escape of astral emoji yields UTF-16 surrogates — merge them
    try:
        s = s.encode("utf-16", "surrogatepass").decode("utf-16")
    except Exception:
        pass
    return unescape(s)


def _ig_from_profile_embed(
    handle: str, limit: int
) -> Tuple[Optional[Dict[str, Any]], List[Dict[str, Any]]]:
    """
    TikTok-equivalent: GET /{handle}/embed/ with facebookexternalhit UA.

    Returns followers + recent posts (thumbs, captions, shortcodes) without
    session cookie or residential proxy — Meta still serves profile embeds to crawlers.
    """
    try:
        _jitter(0.2, 0.6)
        resp = _http_get(
            f"https://www.instagram.com/{handle}/embed/",
            headers={
                "User-Agent": "facebookexternalhit/1.1 (+http://www.facebook.com/externalhit_uatext.php)",
                "Accept": "text/html,application/xhtml+xml",
                "Accept-Language": "en-US,en;q=0.9",
                "Referer": "https://www.facebook.com/",
            },
            timeout=25,
        )
        print(f"[InHouse/IG] profile embed status={resp.status_code} len={len(resp.text or '')}")
        if resp.status_code != 200 or not resp.text:
            return None, []
        if "followers_count" not in resp.text and "graphql_media" not in resp.text:
            print("[InHouse/IG] profile embed: no media payload (login wall?)")
            return None, []
        return _ig_parse_profile_embed_html(resp.text, handle, limit)
    except Exception as e:
        print(f"[InHouse/IG] profile embed error: {e}")
        return None, []


def _ig_parse_profile_embed_html(
    html: str, handle: str, limit: int = 12
) -> Tuple[Optional[Dict[str, Any]], List[Dict[str, Any]]]:
    """Pure parser for /{handle}/embed/ HTML (facebookexternalhit payload)."""
    text = html or ""
    if not text:
        return None, []

    followers = 0
    fm = re.search(r'followers_count\\":(\d+)', text) or re.search(
        r'"followers_count"\s*:\s*(\d+)', text
    )
    if fm:
        followers = int(fm.group(1))
        if not _ig_plausible_followers(followers):
            print(f"[InHouse/IG] embed rejecting implausible followers={followers}")
            followers = 0

    media_count = 0
    pm = re.search(r'posts_count\\":(\d+)', text) or re.search(
        r'"posts_count"\s*:\s*(\d+)', text
    )
    if pm:
        media_count = int(pm.group(1))

    full_name = ""
    nm = re.search(r'full_name\\":\\"(.*?)\\"', text)
    if nm:
        full_name = _ig_unescape_embedded_text(nm.group(1)).strip()

    username = handle
    for um in re.finditer(r'username\\":\\"([A-Za-z0-9._]+)\\"', text):
        if um.group(1).lower() == handle.lower():
            username = um.group(1)
            break

    is_private = False
    priv = re.search(r'is_private\\":(true|false)', text)
    if priv:
        is_private = priv.group(1) == "true"

    profile_pic = ""
    pic = re.search(r'profile_pic_url\\":\\"(https:[^\\"]+)\\"', text)
    if pic:
        profile_pic = _ig_unescape_embedded_url(pic.group(1))

    posts: List[Dict[str, Any]] = []
    # Split on shortcode_media blocks when present
    blocks = re.split(r'\{\\"shortcode_media\\":\{', text)
    if len(blocks) <= 1:
        blocks = re.split(r'\{"shortcode_media":\{', text)

    for block in blocks[1 : limit + 3]:
        code_m = re.search(r'shortcode\\":\\"([A-Za-z0-9_-]+)\\"', block) or re.search(
            r'"shortcode"\s*:\s*"([A-Za-z0-9_-]+)"', block
        )
        if not code_m:
            continue
        code = code_m.group(1)
        if not re.match(r"^[A-Za-z0-9_-]{5,}$", code):
            continue

        du_m = re.search(
            r'display_url\\":\\"(https:.*?)\\",\\"display_resources', block
        ) or re.search(
            r'"display_url"\s*:\s*"(https:[^"]+)"', block
        )
        display = _ig_unescape_embedded_url(du_m.group(1)) if du_m else ""

        ts = 0
        ts_m = re.search(r'taken_at_timestamp\\":(\d+)', block) or re.search(
            r'"taken_at_timestamp"\s*:\s*(\d+)', block
        )
        if ts_m:
            ts = int(ts_m.group(1))

        caption = ""
        # Prefer caption edge text; fall back to any \"text\":\"...\" in the block
        cap_m = re.search(r'\\"text\\":\\"(.*?)\\"', block) or re.search(
            r'"text"\s*:\s*"((?:\\.|[^"\\])*)"', block
        )
        if cap_m:
            caption = _ig_unescape_embedded_text(cap_m.group(1)).strip()

        likes = 0
        lk = re.search(r'edge_liked_by\\":\{\\"count\\":(\d+)', block) or re.search(
            r'edge_media_preview_like\\":\{\\"count\\":(\d+)', block
        )
        if lk:
            likes = int(lk.group(1))

        comments = 0
        cm = re.search(r'edge_media_to_comment\\":\{\\"count\\":(\d+)', block)
        if cm:
            comments = int(cm.group(1))

        timestamp = ""
        if ts > 0:
            timestamp = datetime.fromtimestamp(ts, tz=timezone.utc).isoformat().replace(
                "+00:00", "Z"
            )

        posts.append(
            {
                "likesCount": likes,
                "commentsCount": comments,
                "videoViewCount": 0,
                "caption": caption,
                "displayUrl": display,
                "timestamp": timestamp,
                "isPinnedItem": False,
                "shortCode": code,
            }
        )
        if len(posts) >= limit:
            break

    # Fallback: shortcodes only (enrich later via GraphQL)
    if not posts:
        codes = list(dict.fromkeys(re.findall(r'shortcode\\":\\"([A-Za-z0-9_-]+)\\"', text)))
        for code in codes[:limit]:
            if re.match(r"^[A-Za-z0-9_-]{5,}$", code):
                posts.append(
                    {
                        "likesCount": 0,
                        "commentsCount": 0,
                        "videoViewCount": 0,
                        "caption": "",
                        "displayUrl": "",
                        "timestamp": "",
                        "isPinnedItem": False,
                        "shortCode": code,
                    }
                )

    if followers <= 0 and not posts and not full_name:
        return None, []

    user: Dict[str, Any] = {
        "username": username or handle,
        "full_name": full_name,
        "biography": "",
        "external_url": "",
        "profile_pic_url": profile_pic,
        "edge_followed_by": {"count": followers},
        "edge_follow": {"count": 0},
        "edge_owner_to_timeline_media": {"count": media_count or len(posts), "edges": []},
        "is_private": is_private,
        "is_verified": False,
        "is_business_account": False,
        "business_category_name": "",
    }
    print(
        f"[InHouse/IG] profile embed parsed followers={followers} "
        f"posts={len(posts)} name_len={len(full_name)}"
    )
    return user, posts


def _ig_enrich_posts_via_shortcode(
    session: requests.Session, posts: List[Dict[str, Any]], limit: int = 6
) -> List[Dict[str, Any]]:
    """Fill missing displayUrl/caption/likes via shortcode GraphQL for thin embed posts."""
    if not posts:
        return posts
    enriched: List[Dict[str, Any]] = []
    for i, post in enumerate(posts):
        code = post.get("shortCode") or ""
        if not code or i >= limit:
            enriched.append(post)
            continue
        # Always enrich a few posts for clean captions/thumbs/likes (embed text is noisy)
        try:
            _jitter(0.15, 0.4)
            meta = _ig_fetch_embed_post_meta(session, code)
            if not meta:
                enriched.append(post)
                continue
            merged = dict(post)
            if not merged.get("displayUrl") and meta.get("displayUrl"):
                merged["displayUrl"] = meta["displayUrl"]
            if meta.get("caption"):
                # Prefer GraphQL caption — embed HTML captions are heavily escaped
                merged["caption"] = meta["caption"]
            if not merged.get("likesCount") and meta.get("likesCount"):
                merged["likesCount"] = meta["likesCount"]
            if not merged.get("commentsCount") and meta.get("commentsCount"):
                merged["commentsCount"] = meta["commentsCount"]
            if not merged.get("timestamp") and meta.get("timestamp"):
                merged["timestamp"] = meta["timestamp"]
            enriched.append(merged)
        except Exception:
            enriched.append(post)
    return enriched


def _ig_from_imginn(
    session: requests.Session, handle: str, limit: int
) -> Tuple[Optional[Dict[str, Any]], List[Dict[str, Any]]]:
    """
    Public mirror fallback (bio + recent posts) when Instagram APIs / profile embed fail.
    Secondary to /{handle}/embed/ (the primary TikTok-equivalent path).
    """
    try:
        _jitter(0.2, 0.5)
        url = f"https://imginn.com/{handle}/"
        headers = {
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Referer": "https://imginn.com/",
        }
        resp = session.get(url, headers=headers, timeout=20)
        print(f"[InHouse/IG] imginn status={resp.status_code}")
        if resp.status_code == 404:
            return None, []
        if resp.status_code != 200 or not resp.text:
            return None, []
        return _ig_parse_imginn_html(resp.text, handle, limit)
    except Exception as e:
        print(f"[InHouse/IG] imginn error: {e}")
        return None, []


def _ig_parse_imginn_html(
    html: str, handle: str, limit: int
) -> Tuple[Optional[Dict[str, Any]], List[Dict[str, Any]]]:
    """Pure parser for imginn profile HTML (unit-testable)."""
    counters = {
        (m.group(2) or "").strip().lower(): _parse_compact_count(m.group(1))
        for m in re.finditer(
            r'<div class="counter-item"><div class="num">([^<]+)</div><span>([^<]+)</span></div>',
            html,
            re.I,
        )
    }
    followers = counters.get("followers", 0)
    following = counters.get("following", 0)
    media = counters.get("posts", 0)

    # og:description often has bio + counts
    og = _ig_meta_content(html, "og:description") or _ig_meta_content(html, "description") or ""
    if not followers:
        m = re.search(
            r"([\d,.]+[KMBkmb]?)\s*Followers,\s*([\d,.]+[KMBkmb]?)\s*Following,\s*([\d,.]+[KMBkmb]?)\s*Posts",
            og,
            re.I,
        )
        if m:
            followers = _parse_compact_count(m.group(1))
            following = _parse_compact_count(m.group(2))
            media = _parse_compact_count(m.group(3))

    bio_m = re.search(r'<div class="bio"[^>]*>(.*?)</div>', html, re.I | re.S)
    bio = ""
    if bio_m:
        bio = re.sub(r"<[^>]+>", "", unescape(bio_m.group(1))).strip()
    if bio.lower() in ("undefined", "null", "none"):
        bio = ""
    if not bio and og and "undefined" not in og.lower() and not re.search(r"\b0\s+Followers\b", og, re.I):
        # Prefer quoted bio from share snippets; else strip follower meta + HTML
        quoted = re.search(
            r'on\s+(?:Instagram|TikTok)\s*:\s*[“"\']\s*(.+?)\s*[”"\']\s*$',
            unescape(og),
            re.I | re.S,
        )
        if quoted:
            bio = re.sub(r"<[^>]+>", "", unescape(quoted.group(1))).strip()
        else:
            bio = re.sub(r"<[^>]+>", "", unescape(og)).strip()
            bio = re.sub(
                r"\s*[\d,.]+[KMBkmb]?\s*Followers,\s*[\d,.]+[KMBkmb]?\s*Following,\s*(?:[\d,.]+[KMBkmb]?|undefined)\s*Posts\s*$",
                "",
                bio,
                flags=re.I,
            ).strip()
            bio = re.sub(
                r"^[\s\S]*?(?:F?ollowers|Following)\s*,\s*[\d,.]+\s*Following\s*,\s*[\d,.]+\s*Posts\s*[-–—:]?\s*",
                "",
                bio,
                flags=re.I,
            ).strip()
        if bio.lower() in ("undefined", "null", "none"):
            bio = ""

    # Ignore broken mirror counters (imginn sometimes emits 0 / undefined for dotted handles)
    if followers <= 0 or re.search(r"\bundefined\s+Following\b|\bundefined\s+Posts\b", og, re.I):
        followers = 0
        following = 0
        media = 0

    name_m = re.search(r'<div class="name"[^>]*>(.*?)</div>', html, re.I | re.S)
    full_name = ""
    if name_m:
        full_name = re.sub(r"<[^>]+>", "", unescape(name_m.group(1))).strip()
        if full_name.startswith("@"):
            full_name = ""
    if not full_name:
        title = _ig_meta_content(html, "og:title") or ""
        full_name = title.split("(")[0].strip()

    # Parse posts first — posts alone are enough (stats optional, like TikTok DIY)
    posts: List[Dict[str, Any]] = []
    for m in re.finditer(r'<div class="item">', html):
        chunk = html[m.start() : m.start() + 2500]
        href = re.search(r'href="(/p/[^"]+)"', chunk)
        img = re.search(r'<img[^>]+src="([^"]+)"', chunk)
        alt = re.search(r'<img[^>]+alt="([^"]*)"', chunk)
        likes = re.search(r'<div class="likes"[^>]*>.*?<span>([^<]+)</span>', chunk, re.I | re.S)
        comments = re.search(r'<div class="comments"[^>]*>.*?<span>([^<]+)</span>', chunk, re.I | re.S)
        time_m = re.search(r'<div class="time"[^>]*>([^<]+)</div>', chunk, re.I)
        if not img and not href:
            continue
        display = unescape((img.group(1) if img else "").replace("&#38;", "&"))
        caption = unescape(alt.group(1)) if alt else ""
        ts = _ig_relative_time_to_iso(time_m.group(1) if time_m else "")
        posts.append({
            "likesCount": _parse_compact_count(likes.group(1)) if likes else 0,
            "commentsCount": _parse_compact_count(comments.group(1)) if comments else 0,
            "caption": caption,
            "displayUrl": display,
            "timestamp": ts or "",
            "isPinnedItem": False,
            "shortCode": (href.group(1).strip("/").split("/")[-1] if href else ""),
        })
        if len(posts) >= limit:
            break

    if not posts and not followers and not bio and not full_name:
        return None, []

    if media <= 0 and posts:
        media = len(posts)

    user = {
        "username": handle,
        "full_name": full_name or handle,
        "biography": bio,
        "external_url": "",
        "edge_followed_by": {"count": followers},
        "edge_follow": {"count": following},
        "edge_owner_to_timeline_media": {"count": media, "edges": []},
        "is_private": False,
        "is_verified": False,
        "is_business_account": False,
        "business_category_name": "",
    }

    print(f"[InHouse/IG] imginn mapped posts={len(posts)} followers={followers}")
    return user, posts


def _ig_fill_user_gaps(user: Dict[str, Any], patch: Dict[str, Any]) -> None:
    if not patch:
        return
    if not (user.get("biography") or "").strip() and (patch.get("biography") or "").strip():
        user["biography"] = patch["biography"]
    if not (user.get("full_name") or "").strip() and (patch.get("full_name") or "").strip():
        user["full_name"] = patch["full_name"]
    if not (user.get("external_url") or "").strip() and (patch.get("external_url") or "").strip():
        user["external_url"] = patch["external_url"]
    for edge_key, patch_key in (
        ("edge_followed_by", "edge_followed_by"),
        ("edge_follow", "edge_follow"),
        ("edge_owner_to_timeline_media", "edge_owner_to_timeline_media"),
    ):
        cur = int(((user.get(edge_key) or {}).get("count") or 0))
        nxt = int(((patch.get(patch_key) or {}).get("count") or 0))
        if edge_key == "edge_followed_by" and nxt > 0 and not _ig_plausible_followers(nxt):
            continue
        if cur <= 0 and nxt > 0:
            edges = (user.get(edge_key) or {}).get("edges") or []
            user[edge_key] = {"count": nxt, "edges": edges}


def _parse_compact_count(value: Any) -> int:
    """Parse 1.2K / 269M / 12,345 style counts."""
    if value is None:
        return 0
    if isinstance(value, (int, float)):
        return int(value)
    s = str(value).strip().upper().replace(",", "")
    if not s:
        return 0
    m = re.match(r"^([\d.]+)\s*([KMB])?$", s)
    if not m:
        digits = re.sub(r"[^\d]", "", s)
        return int(digits) if digits else 0
    num = float(m.group(1))
    unit = m.group(2) or ""
    mult = {"": 1, "K": 1_000, "M": 1_000_000, "B": 1_000_000_000}.get(unit, 1)
    return int(num * mult)


def _ig_relative_time_to_iso(text: str) -> str:
    """Convert imginn relative ages ('2 hours ago') to ISO UTC timestamps."""
    if not text:
        return ""
    raw = unescape(text).strip().lower()
    now = datetime.now(timezone.utc)
    if raw in ("just now", "now"):
        return now.isoformat().replace("+00:00", "Z")
    if raw == "yesterday":
        return (now - timedelta(days=1)).isoformat().replace("+00:00", "Z")

    m = re.match(r"^(?:a|an|\d+)\s+(second|minute|hour|day|week|month|year)s?\s+ago$", raw)
    if not m:
        m2 = re.match(r"^(\d+)\s*(s|m|h|d|w)$", raw)
        if not m2:
            return ""
        n = int(m2.group(1))
        unit = {"s": "second", "m": "minute", "h": "hour", "d": "day", "w": "week"}[m2.group(2)]
    else:
        qty = raw.split()[0]
        n = 1 if qty in ("a", "an") else int(qty)
        unit = m.group(1)

    delta = {
        "second": timedelta(seconds=n),
        "minute": timedelta(minutes=n),
        "hour": timedelta(hours=n),
        "day": timedelta(days=n),
        "week": timedelta(weeks=n),
        "month": timedelta(days=30 * n),
        "year": timedelta(days=365 * n),
    }.get(unit)
    if not delta:
        return ""
    return (now - delta).isoformat().replace("+00:00", "Z")


def _ig_extract_posts_from_user(user: Dict[str, Any], limit: int) -> List[Dict[str, Any]]:
    media = user.get("edge_owner_to_timeline_media") or {}
    edges = media.get("edges") or []
    # Some payloads use "media" / "items"
    if not edges and isinstance(user.get("edge_felix_video_timeline"), dict):
        edges = (user.get("edge_felix_video_timeline") or {}).get("edges") or []

    posts: List[Dict[str, Any]] = []
    for edge in edges[:limit]:
        node = edge.get("node") if isinstance(edge, dict) else None
        if not node and isinstance(edge, dict) and edge.get("id"):
            node = edge
        if not node:
            continue
        posts.append(_ig_node_to_post(node))

    # web_profile_info sometimes nests differently
    if not posts:
        for key in ("media", "items", "latest_posts"):
            items = user.get(key)
            if isinstance(items, list):
                for item in items[:limit]:
                    if isinstance(item, dict):
                        posts.append(_ig_node_to_post(item))
                break

    return [p for p in posts if p.get("displayUrl") or p.get("caption") or p.get("likesCount") is not None][:limit]


def _ig_node_to_post(node: Dict[str, Any]) -> Dict[str, Any]:
    caption = ""
    edge_caption = node.get("edge_media_to_caption") or {}
    cap_edges = edge_caption.get("edges") or []
    if cap_edges:
        caption = ((cap_edges[0] or {}).get("node") or {}).get("text") or ""
    if not caption:
        caption = node.get("caption") or node.get("accessibility_caption") or ""
        if isinstance(caption, dict):
            caption = caption.get("text") or ""

    likes = (
        (node.get("edge_liked_by") or {}).get("count")
        or (node.get("edge_media_preview_like") or {}).get("count")
        or node.get("like_count")
        or node.get("likesCount")
        or 0
    )
    comments = (
        (node.get("edge_media_to_comment") or {}).get("count")
        or (node.get("edge_media_to_parent_comment") or {}).get("count")
        or node.get("comment_count")
        or node.get("commentsCount")
        or 0
    )
    views = (
        node.get("play_count")
        or node.get("ig_play_count")
        or node.get("video_view_count")
        or node.get("videoViewCount")
        or node.get("view_count")
        or node.get("viewsCount")
        or 0
    )
    display = (
        node.get("display_url")
        or node.get("displayUrl")
        or node.get("thumbnail_src")
        or node.get("thumbnail_url")
        or node.get("display_uri")
        or ""
    )
    if not display:
        candidates = ((node.get("image_versions2") or {}).get("candidates") or [])
        if candidates and isinstance(candidates[0], dict):
            display = candidates[0].get("url") or ""
    if not display:
        carousel = node.get("carousel_media") or []
        if carousel and isinstance(carousel[0], dict):
            cands = ((carousel[0].get("image_versions2") or {}).get("candidates") or [])
            if cands and isinstance(cands[0], dict):
                display = cands[0].get("url") or ""
    ts = node.get("taken_at_timestamp") or node.get("taken_at") or node.get("timestamp")
    timestamp = ""
    if isinstance(ts, (int, float)):
        timestamp = datetime.fromtimestamp(int(ts), tz=timezone.utc).isoformat().replace("+00:00", "Z")
    elif isinstance(ts, str) and ts:
        timestamp = ts

    shortcode = (
        node.get("shortcode")
        or node.get("shortCode")
        or node.get("code")
        or ""
    )
    # Reject locale junk that sometimes appears in HTML scrapes (e.g. en_US)
    sc = str(shortcode or "")
    if not re.match(r"^[A-Za-z0-9_-]{5,}$", sc) or re.match(r"^[a-z]{2}_[A-Z]{2}$", sc):
        sc = ""

    return {
        "likesCount": int(likes or 0),
        "commentsCount": int(comments or 0),
        "videoViewCount": int(views or 0),
        "caption": caption or "",
        "displayUrl": display or "",
        "timestamp": timestamp,
        "isPinnedItem": bool(node.get("pinned_for_users") or node.get("isPinnedItem") or False),
        "shortCode": sc,
    }


def _ig_to_profile_shape(user: Dict[str, Any], posts: List[Dict[str, Any]], handle: str) -> Dict[str, Any]:
    followers = int((user.get("edge_followed_by") or {}).get("count") or user.get("follower_count") or 0)
    following = int((user.get("edge_follow") or {}).get("count") or user.get("following_count") or 0)
    posts_count = int(
        (user.get("edge_owner_to_timeline_media") or {}).get("count")
        or user.get("media_count")
        or len(posts)
        or 0
    )
    external = user.get("external_url") or user.get("externalUrl") or ""
    bio_links = user.get("bio_links") or []
    if not external and bio_links and isinstance(bio_links, list):
        external = (bio_links[0] or {}).get("url") or ""

    return {
        "username": user.get("username") or handle,
        "fullName": user.get("full_name") or user.get("fullName") or "",
        "biography": user.get("biography") or "",
        "externalUrl": external or "",
        "followersCount": followers,
        "followsCount": following,
        "postsCount": posts_count,
        "isVerified": bool(user.get("is_verified") or user.get("isVerified")),
        "isPrivate": bool(user.get("is_private") or user.get("isPrivate")),
        "isBusinessAccount": bool(user.get("is_business_account") or user.get("isBusinessAccount")),
        "businessCategoryName": user.get("business_category_name") or user.get("category_name") or "",
        "latestPosts": posts,
    }


# ===========================================================================
# TikTok
# ===========================================================================

def scrape_tiktok(handle: str, results_limit: int = 12) -> Dict[str, Any]:
    """
    Scrape TikTok profile + recent videos into process_scrape-compatible shape.

    Cascade:
      1) SSR profile HTML (__UNIVERSAL_DATA_FOR_REHYDRATION__)
      2) web /api/post/item_list/ (often empty without signed X-Bogus)
      3) /embed/@user FRONTITY videoList (reliable, no signing)
    """
    handle = _clean_handle(handle)
    if not handle:
        raise InHouseScrapeError("TikTok handle is required")

    session = _session()
    limit = max(1, min(int(results_limit or 12), 50))

    html_profile, items = _tt_from_profile_html(session, handle, limit)
    if not html_profile:
        # Embed can still yield posts + author bio when SSR is blocked
        embed_videos, embed_profile = _tt_from_embed(session, handle, limit)
        if not embed_videos:
            raise InHouseScrapeError(f"No TikTok data for @{handle}")
        print(f"[InHouse/TT] @{handle} via embed-only ({len(embed_videos)} videos)")
        profile = {
            "uniqueId": handle,
            "nickname": "",
            "signature": "",
            "followerCount": 0,
            "followingCount": 0,
            "videoCount": len(embed_videos),
            "heartCount": 0,
            "verified": False,
            "privateAccount": False,
            "avatarUrl": "",
            "bioLink": "",
            "latestVideos": embed_videos,
        }
        _tt_fill_profile_gaps(profile, embed_profile)
        if not profile.get("videoCount"):
            profile["videoCount"] = len(embed_videos)
        return profile

    source = "ssr"
    if len(items) < 1 and not html_profile.get("privateAccount"):
        api_items = _tt_item_list_api(session, handle, html_profile.get("secUid") or "", limit)
        if api_items:
            items = api_items
            source = "item_list"

    videos = [_tt_item_to_video(it) for it in items[:limit]]
    videos = [v for v in videos if (v.get("text") or "") != "" or v.get("videoMeta", {}).get("coverUrl")]

    embed_profile: Dict[str, Any] = {}
    # Embed fallback when SSR ships empty itemList (common in 2025/26)
    if not videos and not html_profile.get("privateAccount"):
        embed_videos, embed_profile = _tt_from_embed(session, handle, limit)
        if embed_videos:
            videos = embed_videos
            source = "embed"

    # Bio sometimes missing from thin SSR payloads — embed userInfo still has signature
    if not (html_profile.get("signature") or "").strip() and not embed_profile:
        _, embed_profile = _tt_from_embed(session, handle, max(1, min(limit, 3)))

    print(f"[InHouse/TT] @{handle} via {source} ({len(videos)} videos)")

    profile = {
        "uniqueId": html_profile.get("uniqueId") or handle,
        "nickname": html_profile.get("nickname") or "",
        "signature": html_profile.get("signature") or "",
        "followerCount": int(html_profile.get("followerCount") or 0),
        "followingCount": int(html_profile.get("followingCount") or 0),
        "videoCount": int(html_profile.get("videoCount") or len(videos) or 0),
        "heartCount": int(html_profile.get("heartCount") or 0),
        "verified": bool(html_profile.get("verified")),
        "privateAccount": bool(html_profile.get("privateAccount")),
        "avatarUrl": html_profile.get("avatarUrl") or "",
        "bioLink": html_profile.get("bioLink") or "",
        "latestVideos": videos,
    }
    _tt_fill_profile_gaps(profile, embed_profile)
    return profile


def _tt_from_profile_html(
    session: requests.Session, handle: str, limit: int
) -> Tuple[Optional[Dict[str, Any]], List[Dict[str, Any]]]:
    url = f"https://www.tiktok.com/@{handle}"
    headers = {
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Referer": "https://www.tiktok.com/",
    }
    try:
        resp = session.get(url, headers=headers, timeout=15, allow_redirects=True)
        print(f"[InHouse/TT] profile html status={resp.status_code}")
        if resp.status_code == 404:
            raise InHouseScrapeError(f"TikTok account @{handle} not found")
        if resp.status_code != 200:
            return None, []
        html = resp.text

        profile: Dict[str, Any] = {}
        items: List[Dict[str, Any]] = []

        # Universal rehydration blob
        m = re.search(
            r'<script id="__UNIVERSAL_DATA_FOR_REHYDRATION__"[^>]*>([^<]+)</script>',
            html,
        )
        if m:
            try:
                data = json.loads(m.group(1))
                scope = data.get("__DEFAULT_SCOPE__") or {}
                user_detail = scope.get("webapp.user-detail") or {}
                user_info = user_detail.get("userInfo") or {}
                user = user_info.get("user") or {}
                stats = user_info.get("stats") or {}
                if user:
                    profile = _tt_user_stats_to_profile(user, stats)

                # Item lists appear under several keys depending on TikTok build
                items = _tt_collect_items_from_obj(scope, limit) or _tt_collect_items_from_obj(data, limit)
            except json.JSONDecodeError as e:
                print(f"[InHouse/TT] rehydration JSON error: {e}")

        if not profile:
            # SIGI_STATE fallback
            sigi = re.search(r'<script id="SIGI_STATE"[^>]*>([^<]+)</script>', html)
            if sigi:
                try:
                    data = json.loads(sigi.group(1))
                    user_mod = data.get("UserModule") or {}
                    users = user_mod.get("users") or {}
                    stats_mod = user_mod.get("stats") or {}
                    user = users.get(handle) or (next(iter(users.values()), {}) if users else {})
                    stats = stats_mod.get(handle) or stats_mod.get(user.get("id", ""), {}) if user else {}
                    if user:
                        profile = _tt_user_stats_to_profile(user, stats or {})
                    item_mod = data.get("ItemModule") or {}
                    if isinstance(item_mod, dict):
                        items = list(item_mod.values())[:limit]
                except Exception as e:
                    print(f"[InHouse/TT] SIGI_STATE error: {e}")

        if not profile:
            # Regex best-effort
            followers = _re_int(html, r'"followerCount"\s*:\s*(\d+)')
            videos = _re_int(html, r'"videoCount"\s*:\s*(\d+)')
            if followers or videos:
                profile = {
                    "uniqueId": handle,
                    "nickname": _re_str(html, r'"nickname"\s*:\s*"((?:\\.|[^"\\])*)"') or "",
                    "signature": _re_str(html, r'"signature"\s*:\s*"((?:\\.|[^"\\])*)"') or "",
                    "followerCount": followers,
                    "followingCount": _re_int(html, r'"followingCount"\s*:\s*(\d+)'),
                    "videoCount": videos,
                    "heartCount": _re_int(html, r'"heartCount"\s*:\s*(\d+)'),
                    "verified": bool(re.search(r'"verified"\s*:\s*true', html)),
                    "privateAccount": bool(re.search(r'"privateAccount"\s*:\s*true', html)),
                    "avatarUrl": "",
                    "bioLink": _re_str(html, r'"bioLink"\s*:\s*\{\s*"link"\s*:\s*"((?:\\.|[^"\\])*)"')
                    or _re_str(html, r'"link"\s*:\s*"(https?://[^"\\]+)"')
                    or "",
                    "secUid": _re_str(html, r'"secUid"\s*:\s*"((?:\\.|[^"\\])*)"') or "",
                }

        return (profile or None), (items or [])
    except InHouseScrapeError:
        raise
    except Exception as e:
        print(f"[InHouse/TT] profile html error: {e}")
        return None, []


def _tt_user_stats_to_profile(user: Dict[str, Any], stats: Dict[str, Any]) -> Dict[str, Any]:
    bio_link = ""
    link = user.get("bioLink")
    if isinstance(link, dict):
        bio_link = link.get("link") or ""
    elif isinstance(link, str):
        bio_link = link

    return {
        "uniqueId": user.get("uniqueId") or user.get("unique_id") or "",
        "nickname": user.get("nickname") or "",
        "signature": user.get("signature") or "",
        "followerCount": int(stats.get("followerCount") or stats.get("follower_count") or 0),
        "followingCount": int(stats.get("followingCount") or stats.get("following_count") or 0),
        "videoCount": int(stats.get("videoCount") or stats.get("video_count") or 0),
        "heartCount": int(stats.get("heartCount") or stats.get("heart") or stats.get("diggCount") or 0),
        "verified": bool(user.get("verified")),
        "privateAccount": bool(user.get("privateAccount") or user.get("secret")),
        "avatarUrl": user.get("avatarLarger") or user.get("avatarMedium") or user.get("avatarThumb") or "",
        "bioLink": bio_link,
        "secUid": user.get("secUid") or "",
    }


def _tt_collect_items_from_obj(obj: Any, limit: int) -> List[Dict[str, Any]]:
    found: List[Dict[str, Any]] = []

    def walk(node: Any, depth: int = 0) -> None:
        if len(found) >= limit or depth > 8:
            return
        if isinstance(node, dict):
            # Direct item list patterns
            for key in ("itemList", "items", "videos", "list"):
                val = node.get(key)
                if isinstance(val, list) and val and isinstance(val[0], dict):
                    if any(k in val[0] for k in ("id", "desc", "stats", "video", "author")):
                        for it in val:
                            if isinstance(it, dict):
                                found.append(it)
                            if len(found) >= limit:
                                return
            # Single item-shaped dict
            if ("id" in node or "aweme_id" in node) and ("desc" in node or "stats" in node or "video" in node):
                found.append(node)
                return
            for v in node.values():
                walk(v, depth + 1)
        elif isinstance(node, list):
            for v in node:
                walk(v, depth + 1)

    walk(obj)
    # de-dupe by id
    seen = set()
    unique: List[Dict[str, Any]] = []
    for it in found:
        iid = str(it.get("id") or it.get("aweme_id") or it.get("video", {}).get("id") or id(it))
        if iid in seen:
            continue
        seen.add(iid)
        unique.append(it)
        if len(unique) >= limit:
            break
    return unique


def _tt_item_list_api(
    session: requests.Session, handle: str, sec_uid: str, limit: int
) -> List[Dict[str, Any]]:
    """Best-effort TikTok web item_list; often empty without X-Bogus signing."""
    if not sec_uid:
        return []
    try:
        _jitter(0.2, 0.5)
        url = (
            "https://www.tiktok.com/api/post/item_list/"
            f"?aid=1988&count={limit}&secUid={quote(sec_uid)}"
        )
        headers = {
            "Accept": "application/json, text/plain, */*",
            "Referer": f"https://www.tiktok.com/@{handle}",
        }
        resp = session.get(url, headers=headers, timeout=12)
        print(f"[InHouse/TT] item_list status={resp.status_code} bytes={len(resp.content)}")
        if resp.status_code != 200 or not (resp.text or "").strip():
            return []
        data = resp.json()
        items = data.get("itemList") or data.get("items") or []
        return [it for it in items if isinstance(it, dict)][:limit]
    except Exception as e:
        print(f"[InHouse/TT] item_list error: {e}")
        return []


def _tt_from_embed(
    session: requests.Session, handle: str, limit: int
) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """
    Parse /embed/@user __FRONTITY_CONNECT_STATE__ videoList + userInfo.

    Returns (videos, profile_fields). Profile includes signature/bio when present.
    """
    try:
        _jitter(0.2, 0.6)
        url = f"https://www.tiktok.com/embed/@{handle}"
        headers = {
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Referer": f"https://www.tiktok.com/@{handle}",
        }
        resp = session.get(url, headers=headers, timeout=15)
        print(f"[InHouse/TT] embed status={resp.status_code}")
        if resp.status_code != 200:
            return [], {}

        m = re.search(
            r'<script id="__FRONTITY_CONNECT_STATE__"[^>]*>([^<]+)</script>',
            resp.text,
        )
        if not m:
            print("[InHouse/TT] embed: no FRONTITY state")
            return [], {}

        data = json.loads(m.group(1))
        block = _tt_extract_embed_block(data, handle)
        profile = _tt_embed_userinfo_to_profile(
            (block or {}).get("userInfo") if isinstance(block, dict) else None,
            handle,
        )
        video_list = []
        if isinstance(block, dict) and isinstance(block.get("videoList"), list):
            video_list = [v for v in block["videoList"] if isinstance(v, dict)]
        if not video_list:
            video_list = _tt_extract_embed_video_list(data, handle)
        if not video_list:
            print("[InHouse/TT] embed: empty videoList")
            return [], profile

        videos = [_tt_embed_item_to_video(it) for it in video_list[:limit]]
        videos = [v for v in videos if v.get("text") or v.get("videoMeta", {}).get("coverUrl")]
        # Embed feeds pin older clips first and omit createTime — recover times + order
        _tt_flag_out_of_order_pinned(videos)
        videos.sort(key=lambda v: int(v.get("createTime") or 0), reverse=True)
        print(f"[InHouse/TT] embed videoList={len(video_list)} mapped={len(videos)}")
        return videos, profile
    except Exception as e:
        print(f"[InHouse/TT] embed error: {e}")
        return [], {}


def _tt_extract_embed_block(data: Dict[str, Any], handle: str) -> Optional[Dict[str, Any]]:
    source_data = ((data.get("source") or {}).get("data")) or {}
    direct = source_data.get(f"/embed/@{handle}") or source_data.get(f"/embed/@{handle.lower()}")
    if isinstance(direct, dict):
        return direct
    for val in source_data.values():
        if isinstance(val, dict) and (
            isinstance(val.get("videoList"), list) or isinstance(val.get("userInfo"), dict)
        ):
            return val
    return None


def _tt_embed_userinfo_to_profile(user_info: Any, handle: str) -> Dict[str, Any]:
    if not isinstance(user_info, dict) or not user_info:
        return {}
    return {
        "uniqueId": user_info.get("uniqueId") or handle,
        "nickname": user_info.get("nickname") or "",
        "signature": user_info.get("signature") or "",
        "followerCount": int(user_info.get("followerCount") or 0),
        "followingCount": int(user_info.get("followingCount") or 0),
        "videoCount": int(user_info.get("videoCount") or 0),
        "heartCount": int(user_info.get("heartCount") or 0),
        "verified": bool(user_info.get("verified")),
        "privateAccount": bool(user_info.get("privateAccount")),
        "avatarUrl": user_info.get("avatarThumbUrl") or user_info.get("avatarMedium") or "",
        "bioLink": "",
    }


def _tt_fill_profile_gaps(profile: Dict[str, Any], patch: Dict[str, Any]) -> None:
    """Fill empty profile fields from a secondary source (e.g. embed userInfo)."""
    if not patch:
        return
    for key in ("signature", "nickname", "bioLink", "avatarUrl", "uniqueId"):
        if not (profile.get(key) or "").strip() and (patch.get(key) or "").strip():
            profile[key] = patch[key]
    for key in ("followerCount", "followingCount", "videoCount", "heartCount"):
        if not int(profile.get(key) or 0) and int(patch.get(key) or 0):
            profile[key] = int(patch[key])
    if patch.get("verified") and not profile.get("verified"):
        profile["verified"] = True
    if "privateAccount" in patch and not profile.get("privateAccount"):
        profile["privateAccount"] = bool(patch.get("privateAccount"))


def _tt_extract_embed_video_list(data: Dict[str, Any], handle: str) -> List[Dict[str, Any]]:
    block = _tt_extract_embed_block(data, handle)
    if isinstance(block, dict) and isinstance(block.get("videoList"), list):
        return [v for v in block["videoList"] if isinstance(v, dict)]

    source_data = ((data.get("source") or {}).get("data")) or {}
    for val in source_data.values():
        if isinstance(val, dict) and isinstance(val.get("videoList"), list):
            return [v for v in val["videoList"] if isinstance(v, dict)]

    # Deep fallback
    found: List[Dict[str, Any]] = []

    def walk(node: Any, depth: int = 0) -> None:
        if found or depth > 10:
            return
        if isinstance(node, dict):
            vl = node.get("videoList")
            if isinstance(vl, list) and vl and isinstance(vl[0], dict) and ("coverUrl" in vl[0] or "desc" in vl[0]):
                found.extend([v for v in vl if isinstance(v, dict)])
                return
            for v in node.values():
                walk(v, depth + 1)
        elif isinstance(node, list):
            for v in node:
                walk(v, depth + 1)

    walk(data)
    return found


def _tt_id_to_create_time(video_id: Any) -> int:
    """Unix seconds from TikTok snowflake id (upper 32 bits). Embed omits createTime."""
    try:
        vid = int(str(video_id).strip())
    except (TypeError, ValueError):
        return 0
    if vid <= 0:
        return 0
    ts = vid >> 32
    # Rough sanity window: 2016-01-01 .. 2100-01-01 UTC
    if ts < 1451606400 or ts > 4102444800:
        return 0
    return ts


def _tt_resolve_create_time(item: Dict[str, Any], video_id: Any = None) -> int:
    raw = item.get("createTime") or item.get("create_time") or 0
    try:
        create_time = int(raw)
    except (TypeError, ValueError):
        create_time = 0
    if create_time > 0:
        return create_time
    return _tt_id_to_create_time(video_id if video_id is not None else item.get("id") or item.get("aweme_id"))


def _tt_flag_out_of_order_pinned(videos: List[Dict[str, Any]]) -> None:
    """
    Embed/profile lists often put pinned clips first (older than later items).
    Mark prefix items older than the newest createTime as pinned.
    """
    if len(videos) < 2:
        return
    max_ct = max((int(v.get("createTime") or 0) for v in videos), default=0)
    if not max_ct:
        return
    seen_newest = False
    for v in videos:
        ct = int(v.get("createTime") or 0)
        if ct >= max_ct:
            seen_newest = True
            continue
        if not seen_newest and ct and ct < max_ct:
            v["isPinnedItem"] = True


def _tt_embed_item_to_video(item: Dict[str, Any]) -> Dict[str, Any]:
    """Map FRONTITY embed video object → process_scrape video shape."""
    cover = (
        item.get("coverUrl")
        or item.get("originCoverUrl")
        or item.get("dynamicCoverUrl")
        or ""
    )
    stats = item.get("stats") or {}
    # Embed often only exposes playCount — keep digg/comment/share when present
    digg = int(stats.get("diggCount") or item.get("diggCount") or item.get("likeCount") or 0)
    comments = int(stats.get("commentCount") or item.get("commentCount") or 0)
    shares = int(stats.get("shareCount") or item.get("shareCount") or 0)
    video_id = item.get("id") or ""
    create_time = _tt_resolve_create_time(item, video_id)

    return {
        "diggCount": digg,
        "commentCount": comments,
        "shareCount": shares,
        "playCount": int(item.get("playCount") or stats.get("playCount") or 0),
        "text": item.get("desc") or item.get("text") or "",
        "createTime": create_time,
        "isPinnedItem": bool(item.get("isPinnedItem") or False),
        "videoMeta": {
            "coverUrl": cover,
            "originalCoverUrl": item.get("originCoverUrl") or cover,
        },
        "covers": [cover] if cover else [],
        "id": video_id,
    }


def _tt_item_to_video(item: Dict[str, Any]) -> Dict[str, Any]:
    # Embed-shaped objects (coverUrl at top level)
    if item.get("coverUrl") and not item.get("video") and not item.get("stats"):
        return _tt_embed_item_to_video(item)

    stats = item.get("stats") or item.get("statsV2") or {}
    video = item.get("video") or {}
    covers = item.get("covers") or []
    cover = (
        video.get("cover")
        or video.get("originCover")
        or video.get("dynamicCover")
        or item.get("coverUrl")
        or (covers[0] if covers else "")
        or ""
    )
    video_id = item.get("id") or item.get("aweme_id") or ""
    create_time = _tt_resolve_create_time(item, video_id)

    text = item.get("desc") or item.get("text") or ""
    return {
        "diggCount": int(stats.get("diggCount") or stats.get("likeCount") or 0),
        "commentCount": int(stats.get("commentCount") or 0),
        "shareCount": int(stats.get("shareCount") or 0),
        "text": text,
        "createTime": create_time,
        "isPinnedItem": bool(item.get("isPinnedItem") or item.get("is_top") or False),
        "videoMeta": {
            "coverUrl": cover,
            "originalCoverUrl": video.get("originCover") or item.get("originCoverUrl") or cover,
        },
        "covers": [cover] if cover else [],
        "id": video_id,
    }


# ===========================================================================
# Helpers
# ===========================================================================

def _re_int(text: str, pattern: str) -> int:
    m = re.search(pattern, text)
    if not m:
        return 0
    try:
        return int(m.group(1))
    except (TypeError, ValueError):
        return 0


def _re_str(text: str, pattern: str) -> str:
    m = re.search(pattern, text)
    if not m:
        return ""
    try:
        return json.loads(f'"{m.group(1)}"')
    except Exception:
        return m.group(1).encode("utf-8").decode("unicode_escape", errors="ignore")


# Pure parsers exposed for unit tests (no network)
def parse_instagram_user_payload(user: Dict[str, Any], handle: str = "", results_limit: int = 12) -> Dict[str, Any]:
    posts = _ig_extract_posts_from_user(user, results_limit)
    return _ig_to_profile_shape(user, posts, handle or user.get("username") or "user")


def parse_instagram_imginn_html(html: str, handle: str = "", results_limit: int = 12) -> Dict[str, Any]:
    """Pure parser for imginn mirror HTML → IG profile shape."""
    user, posts = _ig_parse_imginn_html(html, handle or "user", results_limit)
    if not user:
        raise InHouseScrapeError("No Instagram data in imginn HTML")
    return _ig_to_profile_shape(user, posts, handle or user.get("username") or "user")


def parse_instagram_profile_embed_html(
    html: str, handle: str = "", results_limit: int = 12
) -> Dict[str, Any]:
    """Pure parser for /{handle}/embed/ facebookexternalhit HTML → IG profile shape."""
    user, posts = _ig_parse_profile_embed_html(html, handle or "user", results_limit)
    if not user:
        raise InHouseScrapeError("No Instagram data in profile embed HTML")
    return _ig_to_profile_shape(user, posts, handle or user.get("username") or "user")


def parse_instagram_crawler_html(html: str, handle: str = "") -> Dict[str, Any]:
    """Pure parser for crawler/browser meta HTML → IG profile shape (no posts)."""
    user = _ig_user_from_og_meta(html, handle or "user")
    if not user:
        raise InHouseScrapeError("No Instagram meta data in HTML")
    return _ig_to_profile_shape(user, [], handle or user.get("username") or "user")


def parse_instagram_search_snippets(html: str, handle: str = "") -> Dict[str, Any]:
    """Pure parser for Startpage/DDG SERP HTML → IG profile shape (no posts)."""
    user = _ig_parse_search_snippet_html(html, handle or "user")
    if not user:
        raise InHouseScrapeError("No Instagram profile data in search snippets")
    return _ig_to_profile_shape(user, [], handle or user.get("username") or "user")


def parse_instagram_post_embed_owner(html: str, handle: str = "") -> Dict[str, Any]:
    """Pure parser for /p/{shortcode}/embed/captioned HoverCard stats."""
    user = _ig_parse_post_embed_owner(html)
    if not user:
        raise InHouseScrapeError("No Instagram owner stats in post embed HTML")
    if handle and not user.get("username"):
        user["username"] = handle
    return _ig_to_profile_shape(user, [], handle or user.get("username") or "user")


def parse_tiktok_rehydration(data: Dict[str, Any], handle: str = "", results_limit: int = 12) -> Dict[str, Any]:
    scope = data.get("__DEFAULT_SCOPE__") or data
    user_detail = scope.get("webapp.user-detail") or {}
    user_info = user_detail.get("userInfo") or {}
    user = user_info.get("user") or {}
    stats = user_info.get("stats") or {}
    profile = _tt_user_stats_to_profile(user, stats) if user else {
        "uniqueId": handle,
        "nickname": "",
        "signature": "",
        "followerCount": 0,
        "followingCount": 0,
        "videoCount": 0,
        "heartCount": 0,
        "verified": False,
        "privateAccount": False,
        "avatarUrl": "",
        "bioLink": "",
    }
    items = _tt_collect_items_from_obj(scope, results_limit)
    videos = [_tt_item_to_video(it) for it in items[:results_limit]]
    profile["latestVideos"] = videos
    if handle and not profile.get("uniqueId"):
        profile["uniqueId"] = handle
    return profile


def parse_tiktok_embed_frontity(data: Dict[str, Any], handle: str = "", results_limit: int = 12) -> List[Dict[str, Any]]:
    """Pure parser for embed FRONTITY JSON (unit tests / offline)."""
    videos, _profile = parse_tiktok_embed_frontity_bundle(data, handle=handle, results_limit=results_limit)
    return videos


def parse_tiktok_embed_frontity_bundle(
    data: Dict[str, Any], handle: str = "", results_limit: int = 12
) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """Pure parser: embed videos + userInfo profile fields (incl. signature/bio)."""
    handle = handle or "user"
    block = _tt_extract_embed_block(data, handle)
    profile = _tt_embed_userinfo_to_profile(
        (block or {}).get("userInfo") if isinstance(block, dict) else None,
        handle,
    )
    video_list = []
    if isinstance(block, dict) and isinstance(block.get("videoList"), list):
        video_list = [v for v in block["videoList"] if isinstance(v, dict)]
    if not video_list:
        video_list = _tt_extract_embed_video_list(data, handle)
    videos = [_tt_embed_item_to_video(it) for it in video_list[:results_limit]]
    videos = [v for v in videos if v.get("text") or v.get("videoMeta", {}).get("coverUrl")]
    _tt_flag_out_of_order_pinned(videos)
    videos.sort(key=lambda v: int(v.get("createTime") or 0), reverse=True)
    return videos, profile
