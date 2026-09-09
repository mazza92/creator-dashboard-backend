# -*- coding: utf-8 -*-
"""
TikTok UGC creator crawler (same pipeline shape as Meta Ads → Shopify).

DISCOVERY (volume):
  1) SerpAPI Google + Bing, up to 10 pages per query (cached)
  2) TikTok hashtag pages
  3) @mention graph from bios/captions (BFS until 1k–2k profiles)
  4) HTML search only if seeds are still thin

ENRICHMENT: existing in-house scrape_tiktok (SSR / embed) plus optional
link-in-bio page fetch for emails that only live on Beacons / Linktree.

QUALIFIED: UGC + niche + public email + at least 1,000 followers.
Location is extracted when present but not required.
"""
from __future__ import annotations

import hashlib
import json
import os
import random
import re
import time
from collections import deque
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Dict, Iterable, List, Optional, Set
from urllib.parse import parse_qs, quote_plus, unquote, urlparse

import requests

try:
    from services.inhouse_social_scraper import InHouseScrapeError, scrape_tiktok
    from services.tiktok_shop_scraper import (
        _LINK_IN_BIO_HOSTS,
        _UA,
        _extract_emails,
        _pick_contact_email,
    )
except ImportError:
    from inhouse_social_scraper import InHouseScrapeError, scrape_tiktok
    from tiktok_shop_scraper import (
        _LINK_IN_BIO_HOSTS,
        _UA,
        _extract_emails,
        _pick_contact_email,
    )

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_STATE_FILE = os.path.join(_ROOT, "_tiktok_ugc_state.json")
_SERP_CACHE_FILE = os.path.join(_ROOT, "_tiktok_ugc_serp_cache.json")
_SERP_CACHE_TTL = 60 * 60 * 20  # 20h — avoid burning SerpAPI on re-runs

_CHECKPOINT_FILE = os.path.join(_ROOT, "_tiktok_ugc_partial.json")

SOURCE = "tiktok_ugc_search"
OUTREACH_TAG = "UGC_SUPPLY_OUTREACH"
MIN_FOLLOWERS = 1000
DEFAULT_QUOTA = 1000
DEFAULT_MAX_HANDLES = 2000
DEFAULT_WORKERS = 4
DEFAULT_SERP_PAGES = 10

_JUNK_EMAIL_MARKERS = (
    "example.com",
    "email.com",
    "domain.com",
    "yoursite.com",
    "yourdomain.com",
    "test.com",
    "placeholder",
    "noreply",
    "no-reply",
)


def is_usable_contact_email(email: Optional[str]) -> bool:
    el = (email or "").strip().lower()
    if "@" not in el or "." not in el.split("@")[-1]:
        return False
    return not any(marker in el for marker in _JUNK_EMAIL_MARKERS)


DEFAULT_NICHES = [
    "hairstylist",
    "hair",
    "skincare",
    "beauty",
    "makeup",
    "wellness",
    "fashion",
    "fitness",
    "lifestyle",
    "food",
    "home",
]

_HANDLE_RE = re.compile(
    r"(?:tiktok\.com|vm\.tiktok\.com)/@?([A-Za-z0-9._]{2,24})",
    re.I,
)
_UNIQUE_ID_RE = re.compile(r'"uniqueId"\s*:\s*"([A-Za-z0-9._]{2,24})"')
_MENTION_RE = re.compile(r"(?:^|[\s|/])@([A-Za-z0-9._]{2,24})\b")
_UGC_RE = re.compile(r"\bugc\b", re.I)
DEFAULT_HASHTAGS = [
    "ugccreator",
    "ugc",
    "ugcbeauty",
    "ugchairstylist",
    "ugcskincare",
    "ugcmakeup",
    "ugclifestyle",
    "ugccommunity",
    "ugccoach",
    "ugcmarketing",
    "branddeals",
    "getpaidtocreate",
    "ugccontentcreator",
    "ugcbeginner",
    "ugcportfolio",
    "paidugc",
    "ugcfashion",
    "ugcfitness",
    "ugcwellness",
]
_SKIP_HANDLES = {
    "www", "vm", "t", "foryou", "discover", "live", "api", "login", "signup",
    "embed", "music", "tag", "search", "about", "legal", "privacy", "explore",
    "following", "friends", "inbox", "messages", "upload", "creator", "shop",
    "place", "hashtag", "trending", "fyp", "video", "photo", "effect",
    "us", "uk", "en", "topic", "share", "item",
}

_US_STATES = {
    "alabama", "alaska", "arizona", "arkansas", "california", "colorado",
    "connecticut", "delaware", "florida", "georgia", "hawaii", "idaho",
    "illinois", "indiana", "iowa", "kansas", "kentucky", "louisiana", "maine",
    "maryland", "massachusetts", "michigan", "minnesota", "mississippi",
    "missouri", "montana", "nebraska", "nevada", "new hampshire", "new jersey",
    "new mexico", "new york", "north carolina", "north dakota", "ohio",
    "oklahoma", "oregon", "pennsylvania", "rhode island", "south carolina",
    "south dakota", "tennessee", "texas", "utah", "vermont", "virginia",
    "washington", "west virginia", "wisconsin", "wyoming", "district of columbia",
}
_US_STATE_ABBR = {
    "al", "ak", "az", "ar", "ca", "co", "ct", "de", "fl", "ga", "hi", "id",
    "il", "in", "ia", "ks", "ky", "la", "me", "md", "ma", "mi", "mn", "ms",
    "mo", "mt", "ne", "nv", "nh", "nj", "nm", "ny", "nc", "nd", "oh", "ok",
    "or", "pa", "ri", "sc", "sd", "tn", "tx", "ut", "vt", "va", "wa", "wv",
    "wi", "wy", "dc",
}
_REGIONS = {
    "midwest", "northeast", "southeast", "southwest", "pacific northwest",
    "west coast", "east coast", "new england", "united states", "usa", "u.s.",
    "canada", "uk", "united kingdom", "australia", "new zealand",
}
_CITIES = {
    "chicago", "new york", "los angeles", "miami", "dallas", "houston",
    "austin", "seattle", "denver", "atlanta", "boston", "phoenix", "nashville",
    "portland", "san diego", "san francisco", "london", "toronto", "sydney",
    "melbourne", "vancouver",
}

_LOCATION_NOISE = re.compile(
    r"[^a-z0-9\s]",
    re.I,
)


def default_search_queries(niches: Optional[Iterable[str]] = None) -> List[str]:
    """Broad query set so SerpAPI can fill 1k+ unique TikTok handles."""
    niches = list(niches or DEFAULT_NICHES)
    queries: List[str] = [
        'site:tiktok.com "@" "UGC creator"',
        'site:tiktok.com "UGC creator" gmail',
        'site:tiktok.com "UGC creator" beacons',
        'site:tiktok.com "UGC creator" linktree',
        'site:tiktok.com "UGC" "@gmail.com"',
        'site:tiktok.com "UGC creator" collab',
        'site:tiktok.com "UGC creator" "work with me"',
        'site:tiktok.com "UGC creator" portfolio',
        'site:tiktok.com "| UGC creator"',
        'site:tiktok.com "UGC +"',
    ]
    for niche in niches:
        queries.append(f'site:tiktok.com "UGC creator" {niche}')
        queries.append(f'site:tiktok.com "UGC" {niche} gmail')
        queries.append(f'site:tiktok.com "UGC creator" {niche} collab')
    for city in (
        "chicago", "miami", "dallas", "austin", "atlanta", "nashville",
        "london", "toronto", "sydney", "los angeles", "new york", "orlando",
    ):
        queries.append(f'site:tiktok.com "UGC creator" {city}')
    # de-dupe, keep order
    out: List[str] = []
    seen: Set[str] = set()
    for q in queries:
        if q in seen:
            continue
        seen.add(q)
        out.append(q)
    return out


def unwrap_ddg_url(href: str) -> str:
    if not href:
        return ""
    href = href.strip()
    parsed = urlparse(href)
    qs = parse_qs(parsed.query)
    if "uddg" in qs:
        return unquote(qs["uddg"][0])
    if href.startswith("//duckduckgo.com/l/?") or "duckduckgo.com/l/" in href:
        inner = parse_qs(urlparse("https:" + href if href.startswith("//") else href).query)
        if "uddg" in inner:
            return unquote(inner["uddg"][0])
    return href


def tiktok_profile_url(handle: str) -> str:
    h = (handle or "").lstrip("@").strip().lower()
    return f"https://www.tiktok.com/@{h}" if h else ""


def extract_tiktok_handles(text: str) -> List[str]:
    seen: Set[str] = set()
    out: List[str] = []
    for raw in _HANDLE_RE.findall(text or ""):
        handle = raw.strip().lstrip("@").rstrip(".").lower()
        if not handle or handle in _SKIP_HANDLES:
            continue
        if handle in seen:
            continue
        seen.add(handle)
        out.append(handle)
    for raw in _UNIQUE_ID_RE.findall(text or ""):
        handle = raw.strip().lstrip("@").rstrip(".").lower()
        if not handle or handle in _SKIP_HANDLES or handle in seen:
            continue
        seen.add(handle)
        out.append(handle)
    return out


def extract_mentioned_handles(text: str) -> List[str]:
    """@handles in bios/captions, not emails like name@gmail.com."""
    seen: Set[str] = set()
    out: List[str] = []
    for raw in _MENTION_RE.findall(" " + (text or "")):
        handle = raw.strip().lstrip("@").rstrip(".").lower()
        if not handle or handle in _SKIP_HANDLES or handle in seen:
            continue
        seen.add(handle)
        out.append(handle)
    return out


def extract_handles_from_ddg_html(html: str) -> List[str]:
    html = html or ""
    hrefs = re.findall(r'href="([^"]+)"', html, re.I)
    blob = html
    for href in hrefs:
        blob += "\n" + unwrap_ddg_url(href.replace("&amp;", "&"))
    return extract_tiktok_handles(blob)


def _blob(*parts: Any) -> str:
    return " ".join(str(p or "") for p in parts)


def has_ugc_signal(text: str) -> bool:
    return bool(_UGC_RE.search(text or ""))


def detect_niche(text: str, niches: Optional[Iterable[str]] = None) -> Optional[str]:
    hay = (text or "").lower()
    for niche in niches or DEFAULT_NICHES:
        token = (niche or "").strip().lower()
        if not token:
            continue
        if re.search(rf"\b{re.escape(token)}\b", hay):
            return token
    return None


def detect_location(text: str) -> Optional[str]:
    raw = text or ""
    hay = _LOCATION_NOISE.sub(" ", raw.lower())
    hay = re.sub(r"\s+", " ", hay).strip()

    pin = re.search(r"(?:📍|based in|from)\s*([A-Za-z][A-Za-z .'-]{1,40})", raw, re.I)
    if pin:
        cand = pin.group(1).strip(" .,-|/").lower()
        if cand in _US_STATES or cand in _CITIES or cand in _REGIONS:
            return cand.title() if cand not in _REGIONS else cand
        for state in _US_STATES:
            if state in cand:
                return state.title()

    for state in sorted(_US_STATES, key=len, reverse=True):
        if re.search(rf"\b{re.escape(state)}\b", hay):
            return state.title()
    for city in sorted(_CITIES, key=len, reverse=True):
        if re.search(rf"\b{re.escape(city)}\b", hay):
            return city.title()
    for region in sorted(_REGIONS, key=len, reverse=True):
        if re.search(rf"\b{re.escape(region)}\b", hay):
            return region
    # "IL" only when next to a city-ish comma: "Chicago, IL"
    abbr = re.search(r"\b([A-Z]{2})\b", raw)
    if abbr and abbr.group(1).lower() in _US_STATE_ABBR:
        return abbr.group(1).upper()
    return None


def is_link_in_bio(url: str) -> bool:
    host = (urlparse(url).hostname or "").lower().removeprefix("www.")
    return any(host == h or host.endswith("." + h) or h in host for h in _LINK_IN_BIO_HOSTS)


def fetch_link_in_bio_emails(url: str, timeout: int = 12) -> List[str]:
    if not url or not url.startswith("http"):
        return []
    try:
        resp = requests.get(
            url,
            headers={"User-Agent": _UA, "Accept-Language": "en-US,en;q=0.9"},
            timeout=timeout,
            allow_redirects=True,
        )
        if resp.status_code != 200:
            return []
        return _extract_emails(resp.text)
    except Exception:
        return []


def qualify_profile(
    *,
    handle: str = "",
    nickname: str = "",
    signature: str = "",
    bio_link: str = "",
    emails: Optional[List[str]] = None,
    niches: Optional[Iterable[str]] = None,
    followers: int = 0,
    min_followers: int = MIN_FOLLOWERS,
) -> Dict[str, Any]:
    blob = _blob(handle, nickname, signature, bio_link)
    email = _pick_contact_email(emails or _extract_emails(blob))
    if email and not is_usable_contact_email(email):
        email = None
    ugc = has_ugc_signal(blob)
    niche = detect_niche(blob, niches)
    location = detect_location(blob)
    try:
        follower_count = int(followers or 0)
    except (TypeError, ValueError):
        follower_count = 0
    has_min_followers = follower_count >= int(min_followers or MIN_FOLLOWERS)
    qualified = bool(ugc and niche and email and has_min_followers)
    return {
        "has_ugc": ugc,
        "niche": niche,
        "location": location,
        "contact_email": email,
        "has_min_followers": has_min_followers,
        "qualified": qualified,
    }


def _search_headers() -> Dict[str, str]:
    return {
        "User-Agent": _UA,
        "Accept-Language": "en-US,en;q=0.9",
        "Accept": "text/html,application/xhtml+xml;q=0.9,*/*;q=0.8",
    }


def _serp_cache_load() -> Dict[str, Any]:
    try:
        with open(_SERP_CACHE_FILE, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _serp_cache_save(cache: Dict[str, Any]) -> None:
    try:
        with open(_SERP_CACHE_FILE, "w", encoding="utf-8") as f:
            json.dump(cache, f)
    except Exception:
        pass


def _serp_cache_key(query: str, start: int, engine: str = "google") -> str:
    return hashlib.sha256(f"{engine}|{query}|{start}".encode("utf-8")).hexdigest()[:24]


def _walk_strings(obj: Any) -> List[str]:
    out: List[str] = []
    if isinstance(obj, str):
        out.append(obj)
    elif isinstance(obj, dict):
        for val in obj.values():
            out.extend(_walk_strings(val))
    elif isinstance(obj, list):
        for val in obj:
            out.extend(_walk_strings(val))
    return out


def extract_handles_from_serpapi(data: Dict[str, Any]) -> List[str]:
    blob = "\n".join(_walk_strings(data))
    handles = extract_tiktok_handles(blob)
    for mention in extract_mentioned_handles(blob):
        if mention not in handles:
            handles.append(mention)
    return handles


def _serpapi_search(
    query: str,
    *,
    start: int = 0,
    engine: str = "google",
    timeout: int = 30,
) -> Dict[str, Any]:
    key = (os.getenv("SERPAPI_API_KEY") or "").strip()
    if not key:
        return {}
    cache = _serp_cache_load()
    ck = _serp_cache_key(query, start, engine)
    hit = cache.get(ck) or {}
    if (
        isinstance(hit, dict)
        and hit.get("data")
        and (time.time() - float(hit.get("ts") or 0)) < _SERP_CACHE_TTL
    ):
        print(f"[TikTokUGC] serpapi cache hit engine={engine} start={start}")
        return hit["data"]

    params: Dict[str, Any] = {
        "engine": engine,
        "q": query,
        "api_key": key,
        "hl": "en",
    }
    if engine == "bing":
        params["first"] = max(1, int(start) + 1)
    else:
        params["num"] = 10
        params["start"] = start
        params["gl"] = "us"

    resp = requests.get(
        "https://serpapi.com/search",
        params=params,
        timeout=timeout,
    )
    resp.raise_for_status()
    data = resp.json() if resp.content else {}
    if isinstance(data, dict) and data.get("error"):
        print(f"[TikTokUGC] serpapi error: {data.get('error')}")
        return {}
    cache[ck] = {"ts": time.time(), "data": data}
    if len(cache) > 600:
        oldest = sorted(cache.items(), key=lambda kv: float((kv[1] or {}).get("ts") or 0))
        for drop_k, _ in oldest[: len(cache) - 600]:
            cache.pop(drop_k, None)
    _serp_cache_save(cache)
    return data if isinstance(data, dict) else {}


def _ddg_search(query: str, timeout: int = 20) -> str:
    url = f"https://html.duckduckgo.com/html/?q={quote_plus(query)}"
    resp = requests.get(url, headers=_search_headers(), timeout=timeout)
    if resp.status_code not in (200, 202):
        resp.raise_for_status()
    return resp.text


def _brave_search(query: str, timeout: int = 20) -> str:
    url = f"https://search.brave.com/search?q={quote_plus(query)}"
    resp = requests.get(url, headers=_search_headers(), timeout=timeout)
    resp.raise_for_status()
    return resp.text


def _startpage_search(query: str, timeout: int = 20) -> str:
    url = f"https://www.startpage.com/sp/search?query={quote_plus(query)}"
    resp = requests.get(url, headers=_search_headers(), timeout=timeout)
    resp.raise_for_status()
    return resp.text


def _ddg_lite_search(query: str, timeout: int = 20) -> str:
    url = f"https://lite.duckduckgo.com/lite/?q={quote_plus(query)}"
    resp = requests.get(url, headers=_search_headers(), timeout=timeout)
    if resp.status_code not in (200, 202):
        resp.raise_for_status()
    return resp.text


def _bing_search(query: str, timeout: int = 20) -> str:
    url = (
        "https://www.bing.com/search?q="
        f"{quote_plus(query)}&cc=US&setlang=en&mkt=en-US"
    )
    resp = requests.get(url, headers=_search_headers(), timeout=timeout)
    resp.raise_for_status()
    return resp.text


def _engine_has_results(html: str) -> bool:
    if not html:
        return False
    low = html.lower()
    if "uddg=" in low or "result__a" in low:
        return True
    return "tiktok.com/@" in low or "tiktok.com%2f%40" in low or '"uniqueid"' in low


_SEARCH_ENGINES = (
    ("startpage", _startpage_search),
    ("ddg_lite", _ddg_lite_search),
    ("bing", _bing_search),
    ("brave", _brave_search),
    ("ddg", _ddg_search),
)


def _add_handles(found: List[str], seen: Set[str], handles: Iterable[str], cap: int) -> bool:
    for handle in handles:
        if handle in seen:
            continue
        seen.add(handle)
        found.append(handle)
        if len(found) >= cap:
            return True
    return False


def discover_handles_from_hashtags(
    tags: Optional[Iterable[str]] = None,
    *,
    max_handles: int = 80,
) -> List[str]:
    found: List[str] = []
    seen: Set[str] = set()
    for tag in tags or DEFAULT_HASHTAGS:
        slug = re.sub(r"[^A-Za-z0-9._]", "", (tag or "").lstrip("#"))
        if not slug:
            continue
        url = f"https://www.tiktok.com/tag/{slug}"
        print(f"[TikTokUGC] hashtag: #{slug}")
        try:
            resp = requests.get(url, headers=_search_headers(), timeout=20)
            if resp.status_code != 200 or not resp.text:
                print(f"[TikTokUGC] hashtag #{slug} status={resp.status_code}")
                continue
            page_handles = extract_tiktok_handles(resp.text)
            print(f"[TikTokUGC] hashtag #{slug}: {len(page_handles)} handles")
            if _add_handles(found, seen, page_handles, max_handles):
                return found
        except Exception as exc:
            print(f"[TikTokUGC] hashtag #{slug} failed: {exc}")
        time.sleep(0.8 + random.random())
    return found


def discover_handles_html(
    queries: List[str],
    *,
    max_handles: int = 80,
    pause: float = 3.5,
) -> List[str]:
    found: List[str] = []
    seen: Set[str] = set()
    skipped: Set[str] = set()
    for query in queries:
        print(f"[TikTokUGC] html search: {query}")
        page_handles: List[str] = []
        for engine, fetcher in _SEARCH_ENGINES:
            if engine in skipped:
                continue
            try:
                html = fetcher(query)
            except Exception as exc:
                msg = str(exc)
                print(f"[TikTokUGC] {engine} failed: {exc}")
                if "429" in msg or "Too Many Requests" in msg:
                    skipped.add(engine)
                    time.sleep(8)
                continue
            if not _engine_has_results(html):
                continue
            page_handles = extract_handles_from_ddg_html(html)
            if page_handles:
                print(f"[TikTokUGC] {engine}: {len(page_handles)} handles")
                break
        if _add_handles(found, seen, page_handles, max_handles):
            return found
        time.sleep(pause + random.random())
    return found


def discover_handles(
    queries: List[str],
    *,
    max_handles: int = 80,
    pause: float = 0.45,
    hashtags: Optional[Iterable[str]] = None,
    serp_pages: int = DEFAULT_SERP_PAGES,
    engines: Optional[Iterable[str]] = None,
) -> List[str]:
    found: List[str] = []
    seen: Set[str] = set()
    has_serpapi = bool((os.getenv("SERPAPI_API_KEY") or "").strip())
    engine_list = [e for e in (engines or ("google", "bing")) if e]
    page_starts = list(range(0, max(1, int(serp_pages)) * 10, 10))

    if has_serpapi:
        for engine in engine_list:
            empty_rounds = 0
            for start in page_starts:
                if len(found) >= max_handles:
                    return found
                added_this_round = 0
                for query in queries:
                    if len(found) >= max_handles:
                        return found
                    print(f"[TikTokUGC] serpapi engine={engine} start={start}: {query}")
                    try:
                        data = _serpapi_search(query, start=start, engine=engine)
                    except Exception as exc:
                        print(f"[TikTokUGC] serpapi failed: {exc}")
                        continue
                    page_handles = extract_handles_from_serpapi(data)
                    before = len(found)
                    if _add_handles(found, seen, page_handles, max_handles):
                        print(f"[TikTokUGC] discovery cap {max_handles}")
                        return found
                    added_this_round += len(found) - before
                    time.sleep(pause)
                print(
                    f"[TikTokUGC] {engine} start={start}: "
                    f"+{added_this_round} new / {len(found)} total"
                )
                if added_this_round == 0:
                    empty_rounds += 1
                    if empty_rounds >= 2:
                        break
                else:
                    empty_rounds = 0
    else:
        print("[TikTokUGC] SERPAPI_API_KEY missing — skipping Google/Bing API")

    if len(found) < max_handles:
        extra = discover_handles_from_hashtags(hashtags, max_handles=max_handles - len(found))
        if _add_handles(found, seen, extra, max_handles):
            return found

    if len(found) < max(80, max_handles // 10) and queries:
        extra = discover_handles_html(
            queries[:8],
            max_handles=max_handles - len(found),
            pause=3.5,
        )
        _add_handles(found, seen, extra, max_handles)
    return found


def _mentions_from_profile(profile: Dict[str, Any], *extra: Any) -> List[str]:
    parts = [profile.get("signature"), profile.get("nickname"), *extra]
    for video in profile.get("latestVideos") or []:
        if isinstance(video, dict):
            parts.append(video.get("text") or "")
    blob = _blob(*parts)
    seen: Set[str] = set()
    out: List[str] = []
    for handle in extract_tiktok_handles(blob) + extract_mentioned_handles(blob):
        if handle in seen:
            continue
        seen.add(handle)
        out.append(handle)
    return out


def _write_checkpoint(records: List[Dict[str, Any]]) -> None:
    try:
        with open(_CHECKPOINT_FILE, "w", encoding="utf-8") as f:
            json.dump(records, f, ensure_ascii=False)
    except Exception:
        pass


def enrich_handle(
    handle: str,
    *,
    niches: Optional[Iterable[str]] = None,
    fetch_bio_link: bool = True,
    min_followers: int = MIN_FOLLOWERS,
) -> Optional[Dict[str, Any]]:
    handle = (handle or "").lstrip("@").strip().lower()
    if not handle:
        return None
    try:
        profile = scrape_tiktok(handle, results_limit=8)
    except InHouseScrapeError as exc:
        print(f"[TikTokUGC] skip @{handle}: {exc}")
        return None
    except Exception as exc:
        print(f"[TikTokUGC] error @{handle}: {exc}")
        return None

    nickname = profile.get("nickname") or ""
    signature = profile.get("signature") or ""
    bio_link = (profile.get("bioLink") or "").strip()
    if isinstance(bio_link, dict):
        bio_link = (bio_link.get("link") or bio_link.get("url") or "").strip()

    emails = _extract_emails(_blob(nickname, signature, bio_link, handle))
    if fetch_bio_link and bio_link and not emails:
        extra = fetch_link_in_bio_emails(bio_link)
        for e in extra:
            if e not in emails:
                emails.append(e)

    follower_count = int(profile.get("followerCount") or 0)
    flags = qualify_profile(
        handle=handle,
        nickname=nickname,
        signature=signature,
        bio_link=bio_link,
        emails=emails,
        niches=niches,
        followers=follower_count,
        min_followers=min_followers,
    )
    mentions = [
        m for m in _mentions_from_profile(profile, bio_link)
        if m != handle
    ]
    resolved = (profile.get("uniqueId") or handle).lstrip("@").lower()
    record = {
        "handle": resolved,
        "display_name": nickname,
        "bio": signature,
        "bio_link": bio_link or None,
        "followers": follower_count,
        "likes": int(profile.get("heartCount") or 0),
        "video_count": int(profile.get("videoCount") or 0),
        "avatar_url": profile.get("avatarUrl") or None,
        "source": SOURCE,
        "profile_url": tiktok_profile_url(resolved),
        "mentioned_handles": mentions,
        **flags,
    }
    return record


def _enrich_batch(
    handles: List[str],
    *,
    niches: List[str],
    fetch_bio_link: bool,
    min_followers: int,
    workers: int,
) -> List[Dict[str, Any]]:
    if not handles:
        return []
    workers = max(1, int(workers or 1))
    if workers == 1:
        out = []
        for handle in handles:
            rec = enrich_handle(
                handle,
                niches=niches,
                fetch_bio_link=fetch_bio_link,
                min_followers=min_followers,
            )
            if rec:
                out.append(rec)
            time.sleep(0.25 + random.random() * 0.4)
        return out

    out: List[Dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futs = {
            pool.submit(
                enrich_handle,
                handle,
                niches=niches,
                fetch_bio_link=fetch_bio_link,
                min_followers=min_followers,
            ): handle
            for handle in handles
        }
        for fut in as_completed(futs):
            try:
                rec = fut.result()
            except Exception as exc:
                print(f"[TikTokUGC] worker error @{futs[fut]}: {exc}")
                continue
            if rec:
                out.append(rec)
    return out


def discover_and_enrich(
    queries: Optional[List[str]] = None,
    *,
    quota: int = DEFAULT_QUOTA,
    max_handles: int = DEFAULT_MAX_HANDLES,
    niches: Optional[Iterable[str]] = None,
    fetch_bio_link: bool = True,
    min_followers: int = MIN_FOLLOWERS,
    workers: int = DEFAULT_WORKERS,
    serp_pages: int = DEFAULT_SERP_PAGES,
    ignore_seen: bool = False,
    expand_graph: bool = True,
) -> List[Dict[str, Any]]:
    niches = list(niches or DEFAULT_NICHES)
    queries = list(queries or default_search_queries(niches))
    print(
        f"[TikTokUGC] volume run: {len(queries)} queries, "
        f"target {max_handles} profiles, quota {quota} qualified, "
        f"workers={workers}"
    )
    seeds = discover_handles(
        queries,
        max_handles=max_handles,
        serp_pages=serp_pages,
    )
    print(f"[TikTokUGC] {len(seeds)} seed handles")

    seen_state = set() if ignore_seen else _load_seen()
    queue: deque = deque()
    queued: Set[str] = set()
    for handle in seeds:
        if handle in queued:
            continue
        queued.add(handle)
        queue.append(handle)

    out: List[Dict[str, Any]] = []
    qualified = 0
    batch_size = max(workers * 2, 4)

    while queue and len(out) < max_handles and qualified < quota:
        batch: List[str] = []
        while queue and len(batch) < batch_size and len(out) + len(batch) < max_handles:
            handle = queue.popleft()
            if handle in seen_state:
                continue
            batch.append(handle)
        if not batch:
            break
        records = _enrich_batch(
            batch,
            niches=niches,
            fetch_bio_link=fetch_bio_link,
            min_followers=min_followers,
            workers=workers,
        )
        for rec in records:
            hid = (rec.get("handle") or "").lower()
            if hid:
                seen_state.add(hid)
            out.append(rec)
            if rec.get("qualified"):
                qualified += 1
                print(
                    f"[TikTokUGC] Q @{rec['handle']} email={rec.get('contact_email')} "
                    f"niche={rec.get('niche')} followers={rec.get('followers')}"
                )
            if expand_graph:
                for mention in rec.get("mentioned_handles") or []:
                    if mention in queued or mention in seen_state:
                        continue
                    queued.add(mention)
                    queue.append(mention)
        print(
            f"[TikTokUGC] progress {len(out)}/{max_handles} enriched "
            f"({qualified} qualified, {len(queue)} queued)"
        )
        _write_checkpoint(out)
        _save_seen(seen_state)
        if qualified >= quota or len(out) >= max_handles:
            break

    _save_seen(seen_state)
    print(f"[TikTokUGC] done: {len(out)} profiles, {qualified} qualified")
    return out


def _load_seen() -> Set[str]:
    try:
        with open(_STATE_FILE, encoding="utf-8") as f:
            data = json.load(f)
        return set(h.lower() for h in (data.get("handles") or []) if h)
    except Exception:
        return set()


def _save_seen(handles: Set[str]) -> None:
    try:
        with open(_STATE_FILE, "w", encoding="utf-8") as f:
            json.dump({"handles": sorted(handles)[-20000:]}, f)
    except Exception:
        pass
