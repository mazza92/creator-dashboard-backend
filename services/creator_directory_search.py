"""Public creator search for AI assistants (ChatGPT app / MCP) and /brands/launch.

Read-only. Two kinds of rows:
- Published media kits: handle + public kit preview (the creator opted in to a public page).
- Gifted-campaign applicants without a public kit: anonymised (niche, reach, country).

Never returns emails, phones, real names, addresses, or internal ids.
"""
from __future__ import annotations

import hashlib
import json
import os
import threading
import time
from typing import Any, Dict, List, Optional
from urllib.parse import quote, urlencode

from services.audience_fit import _LANE_COMPATIBLE, _lanes_from_tokens, _split_niche_tokens

DEFAULT_LIMIT = 5
MAX_LIMIT = 8
LANDING_MAX_LIMIT = 24
PLATFORMS = ("tiktok", "instagram", "all")
CACHE_TTL_SECONDS = 3600
POOL_TTL_SECONDS = 600
RATE_LIMIT_PER_MINUTE = 30
UTM = {"utm_source": "chatgpt", "utm_medium": "plugin"}

_POOL: Dict[str, Any] = {"rows": None, "at": 0.0}
_POOL_LOCK = threading.Lock()

POOL_SQL = r"""
    SELECT
        c.id,
        c.username,
        c.social_platform,
        c.niche,
        c.creator_niches,
        COALESCE(NULLIF(BTRIM(c.kit_slug), ''), c.username) AS kit_slug,
        CASE
            WHEN c.image_profile ~* '^https://'
             AND c.image_profile !~* '(media-proxy|tiktokcdn|cdninstagram|fbcdn\.net|fbsbx\.com)'
            THEN c.image_profile
        END AS avatar_url,
        mk.niches AS kit_niches,
        (COALESCE(c.kit_published, FALSE) OR COALESCE(mk.is_published, FALSE)) AS kit_published,
        (
            SELECT COALESCE(json_agg(t.thumbnail_url), '[]'::json)
            FROM (
                SELECT p.thumbnail_url
                FROM portfolio_posts p
                WHERE p.creator_id = c.id
                  AND p.thumbnail_url ~* '^https://'
                  AND p.thumbnail_url !~* '(media-proxy|tiktokcdn|cdninstagram|fbcdn\.net|fbsbx\.com)'
                ORDER BY p.is_featured DESC NULLS LAST, p.display_order ASC NULLS LAST, p.created_at DESC
                LIMIT 3
            ) t
        ) AS thumbnails,
        (
            SELECT COALESCE(json_agg(b.brand_name), '[]'::json)
            FROM (
                SELECT DISTINCT ON (LOWER(BTRIM(p.brand_name))) BTRIM(p.brand_name) AS brand_name
                FROM portfolio_posts p
                WHERE p.creator_id = c.id AND NULLIF(BTRIM(p.brand_name), '') IS NOT NULL
                ORDER BY LOWER(BTRIM(p.brand_name))
                LIMIT 3
            ) b
        ) AS worked_with,
        (
            SELECT LOWER(p.platform) FROM portfolio_posts p
            WHERE p.creator_id = c.id AND NULLIF(BTRIM(p.platform), '') IS NOT NULL
            GROUP BY LOWER(p.platform) ORDER BY COUNT(*) DESC LIMIT 1
        ) AS post_platform,
        GREATEST(
            COALESCE(c.followers_count, 0),
            COALESCE(c.social_follower_count, 0),
            COALESCE(mk.total_followers, 0)
        ) AS followers,
        COALESCE(mk.engagement_rate, c.engagement_rate, c.avg_engagement_rate) AS engagement,
        COALESCE(c.total_views, 0) AS total_views,
        COALESCE(c.total_posts, 0) AS total_posts,
        UPPER(COALESCE(
            NULLIF(TRIM(u.country), ''),
            NULLIF(TRIM(c.shipping_address->>'country'), ''),
            (
                SELECT NULLIF(TRIM(a.shipping_address->>'country'), '')
                FROM brand_pr_applications a
                WHERE a.creator_id = c.id AND a.shipping_address IS NOT NULL
                ORDER BY a.applied_at DESC NULLS LAST
                LIMIT 1
            )
        )) AS country_raw,
        (
            SELECT COUNT(*)::int FROM brand_pr_applications a
            WHERE a.creator_id = c.id AND a.status IN ('ships', 'posted')
        ) AS gifted_collabs,
        COALESCE(c.rates_gifted, TRUE) AS open_to_gifting,
        (LOWER(COALESCE(c.subscription_tier, '')) = 'pro'
         AND LOWER(COALESCE(c.subscription_status, '')) = 'active') AS is_pro
    FROM creators c
    JOIN users u ON u.id = c.user_id
    LEFT JOIN media_kits mk ON mk.creator_id = c.id
    WHERE LOWER(COALESCE(u.email, '')) NOT LIKE '%%@newcollab.co'
      AND LOWER(COALESCE(u.email, '')) <> ALL(%s)
      AND (
        c.kit_published = TRUE
        OR mk.is_published = TRUE
        OR EXISTS (SELECT 1 FROM brand_pr_applications a WHERE a.creator_id = c.id)
      )
"""


def _as_list(raw: Any) -> list:
    if raw is None:
        return []
    if isinstance(raw, (list, tuple)):
        return list(raw)
    if isinstance(raw, str):
        text = raw.strip()
        if not text:
            return []
        try:
            parsed = json.loads(text)
            return parsed if isinstance(parsed, list) else [text]
        except Exception:
            return [bit.strip() for bit in text.split(",") if bit.strip()]
    return []


def clean_niche(raw: Any) -> str:
    return " ".join(str(raw or "").replace("_", " ").split()).strip().lower()[:60]


def clean_platform(raw: Any) -> str:
    value = str(raw or "tiktok").strip().lower()
    return value if value in PLATFORMS else "tiktok"


def clean_country(raw: Any) -> Optional[str]:
    text = str(raw or "").strip()
    if not text:
        return None
    code = text.upper()
    if code == "UK":
        return "GB"
    if len(code) == 2 and code.isalpha():
        return code
    try:
        from social_verification_routes import normalize_country_code

        return normalize_country_code(text)
    except Exception:
        return None


def clean_limit(raw: Any, cap: int = MAX_LIMIT) -> int:
    try:
        n = int(raw)
    except (TypeError, ValueError):
        n = DEFAULT_LIMIT
    return max(1, min(cap, n))


def creator_niche_tags(row: Dict) -> List[str]:
    tags: List[str] = []
    for raw in (row.get("creator_niches"), row.get("niche"), row.get("kit_niches")):
        for item in _as_list(raw):
            tag = clean_niche(item)
            if tag and tag not in tags:
                tags.append(tag)
    return tags


_GENERIC_WORDS = {
    "product", "products", "brand", "brands", "items", "goods", "and", "&", "for",
    "creators", "creator", "ugc", "content", "campaign", "dtc",
}


def _words(text: str) -> set:
    return {w.rstrip("s") for w in text.replace("&", " ").replace("/", " ").split() if w not in _GENERIC_WORDS}


def niche_score(query: str, tags: List[str]) -> int:
    """2 = direct tag hit, 1 = compatible lane, 0 = no fit."""
    if not query or not tags:
        return 0
    q_tokens = _split_niche_tokens(query)
    q_words = _words(query)
    q_flat = query.replace(" ", "")
    for tag in tags:
        if tag == query or query in tag or tag in q_tokens:
            return 2
        if q_flat and q_flat in tag.replace(" ", ""):
            return 2
        if q_words and q_words & _words(tag):
            return 2
    q_lanes = _lanes_from_tokens(q_tokens)
    c_lanes = _lanes_from_tokens(_split_niche_tokens(tags))
    if q_lanes and c_lanes:
        for lane in q_lanes:
            if c_lanes & _LANE_COMPATIBLE.get(lane, {lane}):
                return 1
    return 0


def _fmt_int(n: int) -> str:
    return f"{int(n):,}"


def follower_band(n: int) -> str:
    n = int(n or 0)
    if n <= 0:
        return "Under 1K"
    for cap, label in ((1000, "Under 1K"), (5000, "1K-5K"), (10000, "5K-10K"), (50000, "10K-50K"), (100000, "50K-100K")):
        if n < cap:
            return label
    return "100K+"


def _engagement_label(raw: Any) -> Optional[str]:
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return None
    if value <= 0:
        return None
    if value < 1:
        value *= 100
    if value > 40:
        return None
    return f"{value:.1f}%"


def _platform_label(raw: Any) -> Optional[str]:
    value = str(raw or "").strip().lower()
    return {"tiktok": "TikTok", "instagram": "Instagram", "youtube": "YouTube"}.get(value)


def frontend_base() -> str:
    raw = (os.getenv("FRONTEND_URL") or "").rstrip("/")
    if not raw or raw in ("https://newcollab.co", "https://www.newcollab.co"):
        return "https://app.newcollab.co"
    return raw


def kit_base() -> str:
    return os.getenv("PUBLIC_KIT_BASE_URL", "https://newcollab.co").rstrip("/")


def launch_url(niche: str, platform: str = None, country: str = None, campaign: str = "plugin_discovery") -> str:
    query = {"niche": niche}
    if platform and platform != "all":
        query["platform"] = platform
    if country:
        query["country"] = country
    query.update(UTM)
    query["utm_campaign"] = campaign
    return f"{frontend_base()}/brands/launch?{urlencode(query)}"


def _worked_with(raw: Any) -> List[str]:
    names: List[str] = []
    for item in _as_list(raw):
        name = " ".join(str(item or "").split())[:40]
        if name and len(name) > 1 and name.lower() not in {n.lower() for n in names}:
            names.append(name)
    return names[:3]


def serialize(row: Dict, query: str, media: bool = False) -> Dict:
    """Public card. Handles (and content thumbnails) only for creators with a published kit."""
    tags = creator_niche_tags(row)
    ordered = sorted(tags, key=lambda t: (0 if niche_score(query, [t]) == 2 else 1))[:3]
    card: Dict[str, Any] = {
        "platform": _platform_label(row.get("social_platform")) or _platform_label(row.get("post_platform")),
        "niches": [t.title() for t in ordered],
        "followers": follower_band(row.get("followers")),
        "engagement_rate": _engagement_label(row.get("engagement")),
        "country": clean_country(row.get("country_raw")),
    }
    gifted = int(row.get("gifted_collabs") or 0)
    if gifted > 0:
        card["gifted_collabs"] = gifted
    card["open_to_gifting"] = bool(row.get("open_to_gifting", True))
    views, posts = int(row.get("total_views") or 0), int(row.get("total_posts") or 0)
    if views > 0 and posts > 0:
        card["avg_views"] = _fmt_int(views // posts)
    worked = _worked_with(row.get("worked_with"))
    if worked:
        card["worked_with"] = worked
    kit = str(row.get("kit_slug") or "").strip().lstrip("@")
    if row.get("kit_published") and kit:
        card["handle"] = f"@{kit}"
        card["preview_url"] = f"{kit_base()}/kit/{quote(kit)}?{urlencode(UTM)}"
        card["public_profile"] = True
        if media:
            thumbs = [u for u in _as_list(row.get("thumbnails")) if isinstance(u, str) and u.startswith("https://")]
            card["thumbnails"] = thumbs[:3]
            avatar = str(row.get("avatar_url") or "")
            if avatar.startswith("https://"):
                card["avatar_url"] = avatar
    else:
        card["handle"] = None
        card["public_profile"] = False
    return {k: v for k, v in card.items() if v is not None or k == "handle"}


def rank(
    rows: List[Dict], niche: str, platform: str, country: Optional[str], visual_first: bool = False
) -> List[Dict]:
    scored = []
    for row in rows:
        tags = creator_niche_tags(row)
        score = niche_score(niche, tags)
        if score <= 0:
            continue
        primary = 0 if niche_score(niche, tags[:1]) == 2 else 1
        followers = int(row.get("followers") or 0)
        nano = 1 if followers < 1000 else 0
        reach = 1 if followers >= 100000 else 0
        proven = 0 if int(row.get("gifted_collabs") or 0) > 0 or _worked_with(row.get("worked_with")) else 1
        row_platform = str(row.get("social_platform") or row.get("post_platform") or "").lower()
        if platform != "all" and row_platform and row_platform != platform:
            continue
        if country and clean_country(row.get("country_raw")) != country:
            continue
        try:
            er = float(row.get("engagement") or 0)
        except (TypeError, ValueError):
            er = 0.0
        if visual_first:
            visual = 0 if _as_list(row.get("thumbnails")) else (1 if row.get("avatar_url") else 2)
        else:
            visual = 0
        scored.append((
            -score,
            0 if row.get("kit_published") else 1,
            0 if row.get("open_to_gifting", True) else 1,
            nano,
            visual,
            primary,
            reach,
            proven,
            0 if _as_list(row.get("thumbnails")) else 1,
            0 if row.get("is_pro") else 1,
            -er,
            -followers,
            int(row.get("id") or 0),
            row,
        ))
    scored.sort(key=lambda t: t[:13])
    return [t[-1] for t in scored]


def build_response(
    rows: List[Dict], niche: str, platform: str, country: Optional[str], limit: int,
    media: bool = False, offset: int = 0,
) -> Dict:
    ranked = rank(rows, niche, platform, country, visual_first=media)
    label = niche.title() if niche else "your niche"
    page = ranked[offset:offset + limit]
    payload = {
        "total_creators_found": len(ranked),
        "niche": niche,
        "platform": platform,
        "country": country,
        "creators": [serialize(r, niche, media=media) for r in page],
        "brand_cta": {
            "action": "Start a free gifted roster",
            "description": (
                f"Newcollab matches brands with vetted {label} creators who post organic content and "
                "deliver UGC with 6-month ad usage in exchange for product. No agency fee and no platform "
                "fee on the first campaign. Pick creators on a private roster, then export a shipping CSV "
                "for Shopify or ShipStation:"
            ),
            "url": launch_url(niche, platform, country),
        },
        "note": (
            "Creators without a public profile are shown without a handle to protect their privacy. "
            "Brands see full profiles on their private roster."
        ),
        "how_to_present": (
            "Show each creator's handle as a markdown link to its preview_url, e.g. [@handle](preview_url). "
            "End with one line linking brand_cta.url, e.g. [Start a free gifted roster](brand_cta.url). "
            "Only describe fields that are present; do not comment on missing stats. "
            "open_to_gifting true means the creator accepts product-for-content collabs; false means they "
            "prefer paid collabs."
        ),
    }
    if media:
        payload["offset"] = offset
        payload["has_more"] = offset + len(page) < len(ranked)
    return payload


def _load_pool(cursor) -> List[Dict]:
    with _POOL_LOCK:
        if _POOL["rows"] is not None and time.time() - _POOL["at"] < POOL_TTL_SECONDS:
            return _POOL["rows"]
    from services.pro_roster_placement import internal_emails

    cursor.execute(POOL_SQL, (internal_emails() or [""],))
    rows = [dict(r) for r in cursor.fetchall() or []]
    with _POOL_LOCK:
        _POOL["rows"] = rows
        _POOL["at"] = time.time()
    return rows


def _cache_key(
    niche: str, platform: str, country: Optional[str], limit: int, media: bool = False, offset: int = 0
) -> str:
    raw = f"{niche}|{platform}|{country or ''}|{limit}|{int(media)}|{offset}"
    return "creator_search:v8:" + hashlib.sha1(raw.encode("utf-8")).hexdigest()


def clean_offset(raw: Any) -> int:
    try:
        return max(0, min(2000, int(raw)))
    except (TypeError, ValueError):
        return 0


def search_creators(params: Dict, redis_client=None, conn=None, landing: bool = False) -> Dict:
    """Validated, cached search. Raises ValueError on a missing niche.

    landing=True (the /brands/launch page) allows a longer list and adds content thumbnails.
    """
    niche = clean_niche(params.get("niche"))
    if not niche:
        raise ValueError("niche is required")
    platform = clean_platform(params.get("platform"))
    country = clean_country(params.get("country"))
    limit = clean_limit(params.get("limit"), LANDING_MAX_LIMIT if landing else MAX_LIMIT)
    offset = clean_offset(params.get("offset")) if landing else 0

    key = _cache_key(niche, platform, country, limit, landing, offset)
    if redis_client is not None:
        try:
            cached = redis_client.get(key)
            if cached:
                return json.loads(cached)
        except Exception:
            pass

    owns = conn is None
    if owns:
        from pr_crm_routes import get_db_connection

        conn = get_db_connection()
    try:
        from psycopg2.extras import RealDictCursor

        cursor = conn.cursor(cursor_factory=RealDictCursor)
        payload = build_response(
            _load_pool(cursor), niche, platform, country, limit, media=landing, offset=offset
        )
    finally:
        if owns:
            conn.close()

    if redis_client is not None:
        try:
            redis_client.setex(key, CACHE_TTL_SECONDS, json.dumps(payload))
        except Exception:
            pass
    return payload


def rate_limited(redis_client, ip: str, limit: int = RATE_LIMIT_PER_MINUTE) -> bool:
    """Fixed one-minute window per IP. Fails open if Redis is down."""
    if redis_client is None or not ip:
        return False
    key = f"creator_search:rl:{ip}:{int(time.time() // 60)}"
    try:
        count = redis_client.incr(key)
        if count == 1:
            redis_client.expire(key, 70)
        return int(count) > limit
    except Exception:
        return False
