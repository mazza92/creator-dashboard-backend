"""Where UGC Pulse finds hooks: real creator videos, not brand ad copy.

Nov 2026: every Pulse hook came from Meta ad text ("The A-game dress. Now 60% off"), which is brand copy, not what
a creator says on camera. Two free sources replace it:

1. Our TikTok store (tiktok_videos, filled by the gifting radar): organic creator posts with views and follower
   counts. A post that beat its creator's usual reach is a hook that worked; its caption's first line is usually
   the on-screen hook. Brand ads (is_ad) and reseller / price posts are left out.
2. YouTube Shorts: found with our in-house YouTube search scrape (no API key), then watched by Gemini (it reads public
   YouTube links directly): the spoken first line, the on-screen text, what's on screen in the first 2 seconds and the
   format. Cached per video, a few new ones a niche a day.

Every hook keeps the videos it came from (link, handle, views), so a creator can watch it work.
"""
from __future__ import annotations

import json
import math
import re
from datetime import date, datetime, timedelta, timezone
from typing import Any, Callable, Dict, List, Optional
from urllib.parse import quote_plus

from psycopg2.extras import Json, RealDictCursor

MIN_VIEWS = 20000
POOL_LIMIT = 6000
NICHE_SAMPLES = 60
YT_ANALYSES_PER_NICHE = 3      # new Shorts watched per niche per day (cached forever after)
YT_LOOKBACK_DAYS = 120
_READY = False

_SPAM = re.compile(r"(?i)\b(wholesale|reseller|resell|dm (me )?to order|location:|order now|price list|in stock|"
                   r"giveaway|link in bio to (buy|order)|₦|naira|whatsapp)\b")
_PROFANE = re.compile(r"(?i)\b(f+u+c+k\w*|shit\w*|bitch\w*|cunt|dick|pussy|kill (you|your)|nsfw|onlyfans|"
                      r"nude\w*|sex\w*)\b")
# A seller or brand account talking about its own product ("our versatile bracelet", "shop now"): brand copy,
# the thing Pulse moved away from.
_BRAND_VOICE = re.compile(r"(?i)\b(our (new |versatile |best[- ]selling )?(product|collection|jewelry|jewellery|"
                          r"bracelet|ring|serum|cream|range|store|shop|brand)s?|shop now|available now|order yours|"
                          r"we('re| are) (so )?(excited|proud)|our customers|free shipping|% off)\b")
_TAG = re.compile(r"(?:^|\s)[#@][\w.\-’']+")
_URL = re.compile(r"https?://\S+")
_EMOJI = re.compile("[\U0001F000-\U0001FAFF☀-➿️‍]+")


def ensure_tables(conn) -> None:
    global _READY
    if _READY:
        return
    cur = conn.cursor()
    cur.execute("""CREATE TABLE IF NOT EXISTS pulse_video_notes (
        video_id TEXT PRIMARY KEY, platform TEXT NOT NULL, niche TEXT, url TEXT, title TEXT, channel TEXT,
        views BIGINT, notes JSONB, ok BOOLEAN NOT NULL DEFAULT FALSE, created_at TIMESTAMPTZ NOT NULL DEFAULT NOW())""")
    cur.execute("CREATE INDEX IF NOT EXISTS pulse_video_notes_niche ON pulse_video_notes (niche, created_at DESC)")
    conn.commit()
    _READY = True


# ----- 1. our TikTok store ----------------------------------------------------------------------------------------

def clean_caption(text: Any) -> str:
    """A caption's words: no hashtags, @mentions, links or emoji runs."""
    t = _URL.sub(" ", str(text or ""))
    t = _TAG.sub(" ", t)
    t = _EMOJI.sub(" ", t)
    return " ".join(t.split()).strip(" -|·•:")


def opener(text: str, limit: int = 140) -> str:
    raw = " ".join(str(text or "").split())
    for stop in (". ", "! ", "? ", " | ", " - "):
        i = raw.find(stop)
        if 10 <= i <= limit:
            return raw[: i + 1].strip()
    return raw[:limit].strip()


def video_pool(conn, since: date, min_views: int = MIN_VIEWS, limit: int = POOL_LIMIT) -> List[Dict[str, Any]]:
    """Organic creator posts that reached real audiences recently."""
    cur = conn.cursor(cursor_factory=RealDictCursor)
    cur.execute("""
        SELECT tiktok_id, url, caption, hashtags, views, creator_handle, creator_followers, language
        FROM tiktok_videos
        WHERE views >= %s AND COALESCE(is_ad, FALSE) = FALSE AND COALESCE(fetched_at, created_at) >= %s
          AND LENGTH(COALESCE(caption, '')) >= 20
        ORDER BY views DESC LIMIT %s""", (min_views, since, limit))
    return [dict(r) for r in cur.fetchall()]


def lift(views: Any, followers: Any) -> float:
    """How far a post beat its creator's size: a 2K-follower creator with 600K views is the signal we want."""
    try:
        v, f = float(views or 0), float(followers or 0)
    except (TypeError, ValueError):
        return 0.0
    return v / max(f, 2000.0) * math.log10(max(v, 10.0))


def niche_video_samples(pool: List[Dict[str, Any]], niche: str, limit: int = NICHE_SAMPLES) -> List[Dict[str, Any]]:
    from services.radar_extract import radar_niche, video_niches

    key = radar_niche(niche) or niche
    picked, seen = [], set()
    for v in sorted(pool, key=lambda r: -lift(r.get("views"), r.get("creator_followers"))):
        lang = str(v.get("language") or "en").lower()
        if lang and not lang.startswith("en"):
            continue
        if key not in video_niches({"caption": v.get("caption"), "hashtags": v.get("hashtags") or []}):
            continue
        text = clean_caption(v.get("caption"))
        handle = str(v.get("creator_handle") or "").lstrip("@")
        caption = v.get("caption") or ""
        if (len(text) < 18 or _SPAM.search(caption) or _PROFANE.search(caption) or _BRAND_VOICE.search(caption)
                or not handle or handle in seen):
            continue
        seen.add(handle)
        picked.append({"i": len(picked), "platform": "tiktok", "url": v.get("url"), "handle": handle,
                       "views": int(v.get("views") or 0), "followers": int(v.get("creator_followers") or 0),
                       "text": text[:400], "opener": opener(text)})
        if len(picked) >= limit:
            break
    return picked


CREATOR_SYSTEM = (
    "You are a UGC (user-generated content) strategist. You get the captions of real organic TikTok videos by "
    "creators in one niche that beat their creator's usual reach, each with an index and its views. A caption's "
    "first line is usually the hook said or shown in the first seconds. Return JSON {\"hooks\": [...], "
    "\"trend\": {...}, \"tip\": \"...\"}.\n"
    "hooks: the 5 to 8 strongest scroll-stopping OPENING LINES a UGC creator could reuse. Each item: {\"hook\": the "
    "video's own opening line, quoted or lightly cleaned (fix typos, cut emojis/hashtags, max 18 words; never invent "
    "words, claims or product names that aren't in the caption), \"pattern\": one of PATTERNS, \"why\": one or two "
    "sentences (max 35 words): why it stops the scroll AND what to show in the first 2 seconds when filming it, "
    "\"sources\": [indexes of videos that open this way, best first]}. Prefer hooks that work for a product video "
    "(review, routine, GRWM, before/after, honest take, POV, storytime). Skip captions that are only a product name, "
    "a giveaway, a price, a reply-to-comment stub or not about the video.\n"
    "trend: {\"title\": max 9 words, \"body\": max 35 words} the format that recurs most across these videos, as advice "
    "to a creator. tip: one practical stop-the-scroll or editing tip for that format, max 25 words.\n"
    "Plain language, no hype, no hashtags."
)


def video_sources(idx: List[Any], samples: List[Dict[str, Any]], limit: int = 4) -> List[Dict[str, Any]]:
    from services.pitch_writer import fmt_count

    by_i = {s["i"]: s for s in samples}
    out, seen = [], set()
    for i in idx or []:
        try:
            s = by_i.get(int(i))
        except (TypeError, ValueError):
            s = None
        if not s or s["handle"] in seen or not s.get("url"):
            continue
        seen.add(s["handle"])
        out.append({"platform": s["platform"], "url": s["url"], "handle": s["handle"], "views": s["views"],
                    "label": f"@{s['handle']} · {fmt_count(s['views'])} views"})
        if len(out) >= limit:
            break
    return out


# ----- 2. YouTube Shorts, watched by Gemini -----------------------------------------------------------------------

YT_QUERIES = ("{n} ugc", "{n} honest review", "{n} tiktok made me buy it", "{n} get ready with me")
_SHORT_KEYS = ("reelItemRenderer", "shortsLockupViewModel")


def _iter_keyed(obj: Any, keys, out: List[tuple], depth: int = 0) -> List[tuple]:
    if depth > 40:
        return out
    if isinstance(obj, dict):
        for k in keys:
            if isinstance(obj.get(k), dict):
                out.append((k, obj[k]))
        for v in obj.values():
            _iter_keyed(v, keys, out, depth + 1)
    elif isinstance(obj, list):
        for v in obj:
            _iter_keyed(v, keys, out, depth + 1)
    return out


def _seconds(text: str) -> int:
    parts = [p for p in re.findall(r"\d+", str(text or ""))][-3:]
    total = 0
    for p in parts:
        total = total * 60 + int(p)
    return total


def search_shorts(query: str, http: Optional[Callable] = None) -> List[Dict[str, Any]]:
    """Shorts on a YouTube search page: id, title, views (no API key; same parser as our channel scrape)."""
    from services.youtube_scraper import (extract_yt_initial_data, parse_compact_count, video_from_renderer,
                                          yt_text)
    import requests

    get = http or requests.get
    try:
        resp = get(f"https://www.youtube.com/results?search_query={quote_plus(query)}&hl=en&gl=US",
                   headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                                          "(KHTML, like Gecko) Chrome/125.0 Safari/537.36",
                            "Accept-Language": "en-US,en;q=0.9"}, timeout=12)
        if getattr(resp, "status_code", 0) != 200:
            return []
        data = extract_yt_initial_data(resp.text or "")
    except Exception as err:
        print(f"[pulse yt] search failed {query!r}: {err}")
        return []
    out, seen = [], set()
    for key, node in _iter_keyed(data, _SHORT_KEYS + ("videoRenderer",), []):
        if key == "videoRenderer" and not (0 < _seconds(yt_text(node.get("lengthText"))) <= 70):
            continue
        v = video_from_renderer(node) or {}
        vid = v.get("id")
        if not vid or vid in seen:
            continue
        seen.add(vid)
        views = v.get("viewCount") or parse_compact_count(yt_text(node.get("viewCountText")))
        out.append({"video_id": vid, "url": f"https://www.youtube.com/shorts/{vid}", "title": v.get("title") or "",
                    "views": int(views or 0), "created": v.get("createTime")})
    return out


WATCH_PROMPT = (
    "Watch this short video. Return JSON {\"is_ugc\": true if a person is talking about, showing or using a product "
    "(review, routine, unboxing, GRWM, before/after, demo), false for brand ads with no creator, music clips, news, "
    "or videos with no product, \"spoken_hook\": the exact first sentence spoken (empty if none), \"on_screen_text\": "
    "the text shown on screen in the first 3 seconds (empty if none), \"opening_shot\": what is on screen in the first "
    "2 seconds, max 15 words, \"format\": e.g. honest review, GRWM, before/after, POV, storytime, routine, \"pattern\": "
    "one of PATTERNS, \"why\": max 30 words, why the opening stops the scroll}. Quote words exactly as said or shown; "
    "never invent."
)


def watch_short(url: str, niche: str, patterns: List[str], gemini: Optional[Callable] = None) -> Dict[str, Any]:
    """Gemini reads a public YouTube link directly (video understanding)."""
    if gemini:
        return gemini(url) or {}
    try:
        import requests
        from services.polly import get_gemini_key
        key = get_gemini_key()
        if not key:
            return {}
        resp = requests.post(
            "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent",
            json={"contents": [{"role": "user", "parts": [
                {"file_data": {"file_uri": url, "mime_type": "video/*"}},
                {"text": WATCH_PROMPT.replace("PATTERNS", ", ".join(patterns)) + f" Niche: {niche}."}]}],
                  "generationConfig": {"temperature": 0.1, "maxOutputTokens": 700, "responseMimeType": "application/json",
                                       "mediaResolution": "MEDIA_RESOLUTION_LOW",
                                       "thinkingConfig": {"thinkingBudget": 0}}},
            headers={"Content-Type": "application/json", "x-goog-api-key": key}, timeout=60)
        if resp.status_code >= 400:
            print(f"[pulse yt] watch HTTP {resp.status_code}: {resp.text[:160]}")
            return {}
        data = resp.json()
        try:
            from services.gemini_usage import record_gemini_usage
            record_gemini_usage("pulse_watch", "gemini-2.5-flash", data)
        except Exception:
            pass
        text = data.get("candidates", [{}])[0].get("content", {}).get("parts", [{}])[0].get("text", "")
        got = json.loads(re.sub(r"^```(?:json)?|```$", "", text.strip()).strip() or "{}")
        return got if isinstance(got, dict) else {}
    except Exception as err:
        print(f"[pulse yt] watch failed {url}: {err}")
        return {}


def refresh_youtube(conn, niche: str, label: str, patterns: List[str], http: Optional[Callable] = None,
                    gemini: Optional[Callable] = None, per_niche: int = YT_ANALYSES_PER_NICHE) -> int:
    """Find this niche's most-watched recent Shorts and have Gemini watch the new ones (cached)."""
    ensure_tables(conn)
    found: Dict[str, Dict[str, Any]] = {}
    now_ts = datetime.now(timezone.utc).timestamp()
    for q in YT_QUERIES:
        for v in search_shorts(q.format(n=label), http=http):
            if v.get("created") and now_ts - float(v["created"]) > YT_LOOKBACK_DAYS * 86400:
                continue
            if v["views"] >= MIN_VIEWS and v["video_id"] not in found:
                found[v["video_id"]] = v
    if not found:
        return 0
    cur = conn.cursor()
    cur.execute("SELECT video_id FROM pulse_video_notes WHERE video_id = ANY(%s)", (list(found),))
    known = {r[0] for r in cur.fetchall()}
    todo = sorted((v for k, v in found.items() if k not in known), key=lambda v: -v["views"])[:per_niche]
    for v in todo:
        notes = watch_short(v["url"], niche, patterns, gemini=gemini)
        hook = " ".join(str(notes.get("spoken_hook") or notes.get("on_screen_text") or "").split())
        ok = bool(notes.get("is_ugc")) and 8 <= len(hook) <= 200
        cur.execute("""INSERT INTO pulse_video_notes (video_id, platform, niche, url, title, views, notes, ok)
                       VALUES (%s, 'youtube', %s, %s, %s, %s, %s, %s) ON CONFLICT (video_id) DO NOTHING""",
                    (v["video_id"], niche, v["url"], v["title"][:300], v["views"], Json(notes), ok))
    conn.commit()
    return len(todo)


def youtube_hook_items(conn, niche: str, day: date, patterns: List[str], limit: int = 4) -> List[Dict[str, Any]]:
    """Hooks from Shorts Gemini watched in the last 30 days: the spoken line (or on-screen text), why it works and
    the opening shot to copy."""
    from services.pitch_writer import fmt_count

    ensure_tables(conn)
    cur = conn.cursor(cursor_factory=RealDictCursor)
    cur.execute("""SELECT video_id, url, title, views, notes FROM pulse_video_notes
                   WHERE niche = %s AND ok AND created_at >= %s ORDER BY views DESC LIMIT %s""",
                (niche, day - timedelta(days=30), limit * 2))
    items = []
    for r in cur.fetchall():
        n = r.get("notes") or {}
        hook = " ".join(str(n.get("spoken_hook") or n.get("on_screen_text") or "").split()).strip(' "“”')
        if not hook:
            continue
        shot = " ".join(str(n.get("opening_shot") or "").split())
        why = " ".join(str(n.get("why") or "").split())
        detail = (why + (f" Opening shot: {shot}." if shot else "")).strip()
        items.append({"day": day, "niche": niche, "kind": "hook",
                      "pattern": n.get("pattern") if n.get("pattern") in patterns else "Other",
                      "text": f"“{hook[:160]}”", "why": detail[:300] or None,
                      "score": max(1, int(math.log10(max(int(r.get("views") or 0), 10)))),
                      "sources": [{"platform": "youtube", "url": r["url"], "handle": "",
                                   "views": int(r.get("views") or 0),
                                   "label": f"YouTube Short · {fmt_count(r.get('views'))} views"}]})
        if len(items) >= limit:
            break
    return items
