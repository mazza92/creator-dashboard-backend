"""3 real Newcollab creators for a brand's cold email, matched to its niche.

Only creators approved in the last 30 days (Mazza, 2026-11-13): older approvals include accounts grandfathered in
by earlier auto-approval rules, and their quality is uneven. Each pick has posts on their public portfolio, 500 to
50K followers, and a link to newcollab.co/kit/<slug>. The order is stable per brand (the same brand always gets
the same 3) but differs between brands, so the pool isn't shown to everyone as the same three names.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import time
from typing import Any, Dict, List, Optional

KIT_BASE = (os.getenv("PUBLIC_SITE_URL") or "https://newcollab.co").rstrip("/") + "/kit"
APPROVED_WITHIN_DAYS = 30
MIN_FOLLOWERS, MAX_FOLLOWERS = 500, 50_000
POOL_TTL_SECONDS = 600

# Brand category -> creator niches that fit it, best first.
NICHES_FOR_CATEGORY = {
    "skincare": ("skincare", "beauty", "makeup"),
    "beauty": ("beauty", "makeup", "skincare"),
    "luxury": ("beauty", "skincare", "luxury", "fashion"),
    "haircare": ("hair", "haircare", "beauty"),
    "wellness": ("wellness", "health", "fitness", "supplements", "self care"),
    "supplements": ("supplements", "wellness", "fitness", "health"),
    "fitness": ("fitness", "activewear", "gym", "wellness"),
    "activewear": ("activewear", "fitness", "gym", "yoga"),
    "fashion": ("fashion", "style", "accessories"),
    "jewelry": ("jewelry", "jewellery", "accessories", "fashion"),
    "food": ("food", "cooking", "drinks", "recipes"),
    "home": ("home", "home decor", "cleaning", "kitchen"),
    "sustainable": ("sustainable", "home", "wellness"),
    "pet": ("pet", "pets", "dog", "cat"),
    "baby": ("baby", "mom", "parenting", "family"),
}
FALLBACK_NICHES = ("lifestyle", "beauty", "skincare")

_POOL: Dict[str, Any] = {"at": 0.0, "rows": []}

POOL_SQL = f"""
    SELECT c.id, c.username, c.kit_slug, c.niche, c.social_platform,
           COALESCE(NULLIF(c.social_follower_count, 0), c.followers_count, 0) AS followers
    FROM creators c
    WHERE c.approval_status = 'approved'
      AND c.approved_at > NOW() - INTERVAL '{APPROVED_WITHIN_DAYS} days'
      AND COALESCE(NULLIF(c.social_follower_count, 0), c.followers_count, 0) BETWEEN {MIN_FOLLOWERS} AND {MAX_FOLLOWERS}
      AND COALESCE(c.image_profile, '') <> ''
      AND COALESCE(c.username, '') <> ''
      AND c.username NOT ILIKE '%%newcollab%%'
      AND (
        EXISTS (SELECT 1 FROM portfolio_posts p WHERE p.creator_id = c.id)
        OR (c.social_oauth_videos IS NOT NULL AND c.social_oauth_videos::text NOT IN ('null', '[]', '{{}}', ''))
        OR EXISTS (SELECT 1 FROM creator_profile_data d WHERE d.user_id = c.user_id
                   AND d.recent_posts IS NOT NULL AND d.recent_posts::text NOT IN ('null', '[]'))
      )
"""


def parse_niches(raw: Any) -> List[str]:
    """creators.niche is either a plain string or a JSON array stored as text."""
    if isinstance(raw, list):
        items = raw
    else:
        text = str(raw or "").strip()
        try:
            items = json.loads(text) if text.startswith("[") else [text]
        except ValueError:
            items = [text]
    out = []
    for item in items:
        n = re.sub(r"\s+", " ", str(item or "")).strip().lower()
        if n and n not in ("other", "null", "none") and n not in out:
            out.append(n)
    return out


def load_pool(cursor, now: Optional[float] = None) -> List[Dict[str, Any]]:
    now = time.time() if now is None else now
    if _POOL["rows"] and now - _POOL["at"] < POOL_TTL_SECONDS:
        return _POOL["rows"]
    cursor.execute(POOL_SQL)
    rows = [dict(r) for r in cursor.fetchall()]
    _POOL.update(at=now, rows=rows)
    return rows


def _fit(niches: List[str], wanted: tuple) -> int:
    """Higher is better: a match on the brand's first niche beats a match on a later one."""
    best = 0
    for rank, w in enumerate(wanted):
        if any(w == n or w in n for n in niches):
            best = max(best, len(wanted) - rank)
    return best


def _shuffle_key(brand_id: int, creator_id: int) -> str:
    return hashlib.md5(f"{brand_id}:{creator_id}".encode()).hexdigest()


def pick_creators(cursor, brand_id: int, category: Optional[str], n: int = 3,
                  pool: Optional[List[Dict[str, Any]]] = None) -> List[Dict[str, Any]]:
    rows = pool if pool is not None else load_pool(cursor)
    wanted = NICHES_FOR_CATEGORY.get((category or "").strip().lower(), FALLBACK_NICHES)
    scored = []
    for r in rows:
        niches = parse_niches(r.get("niche"))
        fit = _fit(niches, wanted) or (_fit(niches, FALLBACK_NICHES) and 0.5) or 0
        if fit:
            scored.append((-fit, _shuffle_key(brand_id, r["id"]), r, niches))
    scored.sort(key=lambda s: (s[0], s[1]))
    picks = []
    for _, _, r, niches in scored[:n]:
        slug = (r.get("kit_slug") or r.get("username") or "").strip()
        shown = [x for x in niches if any(w in x or x in w for w in wanted)] or niches
        picks.append({
            "creator_id": r["id"],
            "handle": (r.get("username") or slug).lstrip("@"),
            "platform": (r.get("social_platform") or "").strip().lower(),
            "followers": int(r.get("followers") or 0),
            "niches": shown[:2],
            "url": f"{KIT_BASE}/{slug}",
        })
    return picks


def followers_label(n: int) -> str:
    if n >= 1000:
        k = n / 1000
        return f"{k:.1f}K".replace(".0K", "K") if k < 10 else f"{int(round(k))}K"
    return str(n)


PLATFORM_NAMES = {"instagram": "Instagram", "tiktok": "TikTok", "youtube": "YouTube"}


def creator_line(c: Dict[str, Any]) -> str:
    """• @handle: beauty & skincare, 21K followers on Instagram. [Portfolio](url)"""
    niche = " & ".join(c["niches"]) if c.get("niches") else "lifestyle"
    where = PLATFORM_NAMES.get(c.get("platform") or "", "")
    reach = f"{followers_label(c['followers'])} followers" + (f" on {where}" if where else "")
    return f"• @{c['handle']}: {niche}, {reach}. [Portfolio]({c['url']})"
