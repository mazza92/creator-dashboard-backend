"""Brands tab "For you" feed: real TikTok posts of creators who got gifted or paid by a brand, each with the
brand's card, padded with directory brands so the feed never runs dry. Paid UGC gigs are mixed in by the
page from /api/opportunities/paid-gigs (same list, same apply sheet as the Paid UGC tab).

Also the creator's brand targets: brands they saved from the feed. Autopilot pitches targets first.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from psycopg2.extras import RealDictCursor

POST_WINDOW_DAYS = 60
EVIDENCE_DAYS = 30
POSTS_LIMIT = 40
BRANDS_LIMIT = 24

# Radar niche -> directory categories it covers.
NICHE_CATEGORIES: Dict[str, List[str]] = {
    "skincare": ["skincare", "beauty"],
    "makeup": ["beauty"],
    "haircare": ["haircare", "beauty"],
    "nails": ["beauty"],
    "fragrance": ["beauty", "luxury"],
    "fashion": ["fashion", "jewelry", "luxury"],
    "fitness": ["fitness", "activewear"],
    "food": ["food"],
    "wellness": ["wellness", "supplements"],
    "home": ["home", "lifestyle", "sustainable"],
    "pet": ["pet"],
    "baby": ["baby"],
    "tech": ["tech", "gaming"],
}
NICHES = list(NICHE_CATEGORIES)

_TARGETS_READY = False


def ensure_targets(cursor) -> None:
    global _TARGETS_READY
    if _TARGETS_READY:
        return
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS creator_brand_targets (
            creator_id INTEGER NOT NULL,
            brand_id INTEGER NOT NULL,
            source VARCHAR(32),
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            PRIMARY KEY (creator_id, brand_id)
        )
    """)
    _TARGETS_READY = True


def creator_niche(cursor, creator_id: int) -> str:
    """The creator's first radar niche from what they told us ('' when unknown)."""
    from services.radar_extract import radar_niche

    cursor.execute("SELECT niche, creator_niches FROM creators WHERE id = %s", (creator_id,))
    row = cursor.fetchone()
    if not row:
        return ""
    row = dict(row) if not isinstance(row, dict) else row
    parts = list(row.get("creator_niches") or []) + [row.get("niche") or ""]
    for part in parts:
        n = radar_niche(part)
        if n:
            return n
    return ""


_BRAND_COLS = """
    b.id, b.slug, b.brand_name AS name, b.logo_url, b.category, b.regions, b.min_followers,
    b.avg_product_value, b.hero_product, b.website, b.tiktok_handle,
    to_jsonb(b) ->> 'gifting_signal' AS gifting_signal,
    (NULLIF(TRIM(b.contact_email), '') IS NOT NULL) AS has_email
"""


def _brand_card(r: Dict[str, Any], targets: set) -> Dict[str, Any]:
    value = r.get("avg_product_value")
    try:
        value = int(value) if value else None
    except (TypeError, ValueError):
        value = None
    return {
        "id": r["id"], "slug": r.get("slug"), "name": r.get("name"), "logo_url": r.get("logo_url"),
        "category": r.get("category"), "regions": r.get("regions"), "min_followers": r.get("min_followers"),
        "product_value": value, "hero_product": r.get("hero_product"),
        "gifting_confirmed": bool(r.get("gifting_signal")), "gifting_signal": r.get("gifting_signal"),
        "creators_30d": int(r.get("creators_30d") or 0), "has_email": bool(r.get("has_email")),
        "targeted": r["id"] in targets,
    }


def _iso(v):
    return v.isoformat() if hasattr(v, "isoformat") else v


def target_ids(cursor, creator_id: int) -> set:
    ensure_targets(cursor)
    cursor.execute("SELECT brand_id FROM creator_brand_targets WHERE creator_id = %s", (creator_id,))
    return {int((r["brand_id"] if isinstance(r, dict) else r[0])) for r in cursor.fetchall()}


def posts(cursor, niche: str, targets: set, limit: int = POSTS_LIMIT) -> List[Dict[str, Any]]:
    """Newest post per brand: small creators posting a published brand's gifted/paid content."""
    niche_sql = "AND s.niche = %(niche)s" if niche else ""
    cursor.execute(f"""
        WITH sig AS (
            SELECT DISTINCT ON (b.id) b.id AS brand_id, v.tiktok_id, v.url, v.caption, v.cover_url,
                   v.creator_handle, v.creator_followers, v.created_at, s.signal_type
            FROM tiktok_videos v
            JOIN brand_signals s ON s.tiktok_id = v.tiktok_id AND s.signal_type IN ('gifted', 'paid')
            JOIN discovered_brands d ON d.id = s.discovered_brand_id
            JOIN pr_brands b ON b.id = d.brand_id AND COALESCE(b.status, 'published') = 'published'
            WHERE v.created_at > NOW() - INTERVAL '{POST_WINDOW_DAYS} days' {niche_sql}
            ORDER BY b.id,
                     (v.cover_url LIKE '%%/storage/v1/object/public/%%') DESC,
                     (s.signal_type = 'gifted') DESC, v.created_at DESC
        ),
        ev AS (
            SELECT d.brand_id, COUNT(DISTINCT v.creator_handle) AS creators_30d
            FROM discovered_brands d
            JOIN brand_signals s ON s.discovered_brand_id = d.id AND s.signal_type IN ('gifted', 'paid')
            JOIN tiktok_videos v ON v.tiktok_id = s.tiktok_id
            WHERE d.brand_id IN (SELECT brand_id FROM sig)
              AND v.created_at > NOW() - INTERVAL '{EVIDENCE_DAYS} days'
            GROUP BY d.brand_id
        )
        SELECT sig.*, ev.creators_30d, {_BRAND_COLS}
        FROM sig JOIN pr_brands b ON b.id = sig.brand_id
        LEFT JOIN ev ON ev.brand_id = sig.brand_id
        ORDER BY (sig.cover_url LIKE '%%/storage/v1/object/public/%%') DESC, sig.created_at DESC
        LIMIT %(limit)s
    """, {"niche": niche, "limit": limit})
    out = []
    for r in cursor.fetchall():
        r = dict(r)
        out.append({
            "type": "post",
            "key": f"post-{r['tiktok_id']}",
            "post": {
                "url": r.get("url"), "caption": (r.get("caption") or "")[:220], "cover_url": r.get("cover_url"),
                "creator_handle": r.get("creator_handle"), "creator_followers": r.get("creator_followers"),
                "posted_at": _iso(r.get("created_at")), "signal": r.get("signal_type"),
            },
            "brand": _brand_card(r, targets),
        })
    return out


def directory_brands(cursor, niche: str, exclude: List[int], targets: set,
                     limit: int = BRANDS_LIMIT) -> List[Dict[str, Any]]:
    """Brands without a fresh post: gifting-confirmed first, in the creator's categories, pitchable."""
    cats = NICHE_CATEGORIES.get(niche) or []
    cat_sql = "AND LOWER(b.category) = ANY(%(cats)s)" if cats else ""
    cursor.execute(f"""
        SELECT {_BRAND_COLS}, 0 AS creators_30d
        FROM pr_brands b
        WHERE COALESCE(b.status, 'published') = 'published'
          AND b.id <> ALL(%(exclude)s) {cat_sql}
          AND (NULLIF(TRIM(b.contact_email), '') IS NOT NULL OR to_jsonb(b) ->> 'gifting_signal' IS NOT NULL)
        ORDER BY (to_jsonb(b) ->> 'gifting_signal' IS NOT NULL) DESC,
                 (b.logo_url IS NOT NULL) DESC, b.is_featured DESC NULLS LAST, md5(b.id::text || CURRENT_DATE::text)
        LIMIT %(limit)s
    """, {"cats": cats, "exclude": list(exclude) or [0], "limit": limit})
    return [{"type": "brand", "key": f"brand-{r['id']}", "brand": _brand_card(dict(r), targets)}
            for r in cursor.fetchall()]


def interleave(post_items: List[Dict], brand_items: List[Dict]) -> List[Dict]:
    """Two posts, then a directory brand, so the feed stays mostly real proof but never runs short."""
    out, bi = [], 0
    for i, p in enumerate(post_items):
        out.append(p)
        if i % 2 == 1 and bi < len(brand_items):
            out.append(brand_items[bi])
            bi += 1
    out.extend(brand_items[bi:])
    return out


def build(conn, creator_id: int, niche: Optional[str] = None) -> Dict[str, Any]:
    cur = conn.cursor(cursor_factory=RealDictCursor)
    niche = (niche or "").strip().lower()
    if niche not in NICHE_CATEGORIES:
        niche = creator_niche(cur, creator_id) if niche != "all" else ""
    targets = target_ids(cur, creator_id)
    # The creator's niche first, then every other niche: sorting, never shrinking the feed.
    post_items = posts(cur, niche, targets) if niche else []
    seen = {p["brand"]["id"] for p in post_items}
    post_items += [p for p in posts(cur, "", targets, limit=POSTS_LIMIT * 2) if p["brand"]["id"] not in seen]
    brand_items = directory_brands(cur, niche, [p["brand"]["id"] for p in post_items], targets)
    if niche:
        taken = [p["brand"]["id"] for p in post_items] + [b["brand"]["id"] for b in brand_items]
        brand_items += directory_brands(cur, "", taken, targets, limit=BRANDS_LIMIT // 2)
    return {"niche": niche, "niches": NICHES, "items": interleave(post_items, brand_items),
            "targets": len(targets)}


def set_target(conn, creator_id: int, brand_id: int, on: bool, source: str = "feed") -> int:
    cur = conn.cursor()
    ensure_targets(cur)
    if on:
        cur.execute("""INSERT INTO creator_brand_targets (creator_id, brand_id, source) VALUES (%s, %s, %s)
                       ON CONFLICT DO NOTHING""", (creator_id, brand_id, (source or "feed")[:32]))
    else:
        cur.execute("DELETE FROM creator_brand_targets WHERE creator_id = %s AND brand_id = %s",
                    (creator_id, brand_id))
    cur.execute("SELECT COUNT(*) FROM creator_brand_targets WHERE creator_id = %s", (creator_id,))
    n = int(cur.fetchone()[0])
    conn.commit()
    return n


def target_brands(conn, creator_id: int) -> List[Dict[str, Any]]:
    """Targets, oldest first, as {id, name} for Autopilot to pitch before its own picks."""
    cur = conn.cursor(cursor_factory=RealDictCursor)
    ensure_targets(cur)
    cur.execute("""SELECT b.id, b.brand_name AS name, b.slug, b.category
                   FROM creator_brand_targets t JOIN pr_brands b ON b.id = t.brand_id
                   WHERE t.creator_id = %s AND COALESCE(b.status, 'published') = 'published'
                   ORDER BY t.created_at""", (creator_id,))
    return [dict(r) for r in cur.fetchall()]


# ----- fresh, endless feed ---------------------------------------------------------------------------------------
# Every visit should feel new: creators never land on the same list twice. Ranking (no new scraping needed):
#   1. posts the creator has never seen, newest week first, their niche next, a shuffle on top;
#   2. directory brands they have never seen;
#   3. only when everything unseen is used up, what they saw longest ago (the "archive" comes last).
# Pages of PAGE_SIZE; the page sends the keys it already holds so pages never repeat inside a session.
import random  # noqa: E402

PAGE_SIZE = 12
NEW_DAYS = 7
SEEN_WINDOW_DAYS = 45
SESSION_CAP = 240  # keys a page may send; past this the feed ends with "all caught up"
_SEEN_READY = False


def ensure_seen(cursor) -> None:
    global _SEEN_READY
    if _SEEN_READY:
        return
    cursor.execute("SELECT to_regclass('creator_feed_seen') IS NOT NULL AS ok")
    row = cursor.fetchone()
    ok = row.get("ok") if isinstance(row, dict) else (row[0] if row else False)
    if not ok:
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS creator_feed_seen (
                creator_id INTEGER NOT NULL,
                item_key TEXT NOT NULL,
                seen_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                PRIMARY KEY (creator_id, item_key)
            )""")
    _SEEN_READY = True


def mark_seen(conn, creator_id: int, keys: List[str]) -> int:
    keys = list(dict.fromkeys(str(k)[:80] for k in (keys or []) if k and isinstance(k, str)))[:60]
    if not keys:
        return 0
    cur = conn.cursor()
    ensure_seen(cur)
    cur.execute("""
        INSERT INTO creator_feed_seen (creator_id, item_key, seen_at)
        SELECT %s, k, NOW() FROM unnest(%s::text[]) AS k
        ON CONFLICT (creator_id, item_key) DO UPDATE SET seen_at = EXCLUDED.seen_at
    """, (creator_id, keys))
    conn.commit()
    return len(keys)


def seen_map(cursor, creator_id: int) -> Dict[str, Any]:
    ensure_seen(cursor)
    cursor.execute(f"""SELECT item_key, seen_at FROM creator_feed_seen
                       WHERE creator_id = %s AND seen_at > NOW() - INTERVAL '{SEEN_WINDOW_DAYS} days'""",
                   (creator_id,))
    return {(r["item_key"] if isinstance(r, dict) else r[0]): (r["seen_at"] if isinstance(r, dict) else r[1])
            for r in cursor.fetchall()}


def all_posts(cursor, targets: set, limit: int = 500) -> List[Dict[str, Any]]:
    """Every gifted/paid post of a published brand in the window (not just the newest one per brand)."""
    cursor.execute(f"""
        WITH sig AS (
            SELECT DISTINCT ON (v.tiktok_id) b.id AS brand_id, v.tiktok_id, v.url, v.caption, v.cover_url,
                   v.creator_handle, v.creator_followers, v.created_at, s.signal_type, s.niche
            FROM tiktok_videos v
            JOIN brand_signals s ON s.tiktok_id = v.tiktok_id AND s.signal_type IN ('gifted', 'paid')
            JOIN discovered_brands d ON d.id = s.discovered_brand_id
            JOIN pr_brands b ON b.id = d.brand_id AND COALESCE(b.status, 'published') = 'published'
            WHERE v.created_at > NOW() - INTERVAL '{POST_WINDOW_DAYS} days'
            ORDER BY v.tiktok_id, (s.signal_type = 'gifted') DESC
        ),
        ev AS (
            SELECT d.brand_id, COUNT(DISTINCT v.creator_handle) AS creators_30d
            FROM discovered_brands d
            JOIN brand_signals s ON s.discovered_brand_id = d.id AND s.signal_type IN ('gifted', 'paid')
            JOIN tiktok_videos v ON v.tiktok_id = s.tiktok_id
            WHERE d.brand_id IN (SELECT brand_id FROM sig)
              AND v.created_at > NOW() - INTERVAL '{EVIDENCE_DAYS} days'
            GROUP BY d.brand_id
        )
        SELECT sig.*, ev.creators_30d, {_BRAND_COLS}
        FROM sig JOIN pr_brands b ON b.id = sig.brand_id
        LEFT JOIN ev ON ev.brand_id = sig.brand_id
        ORDER BY sig.created_at DESC
        LIMIT %(limit)s
    """, {"limit": limit})
    out = []
    for r in cursor.fetchall():
        r = dict(r)
        out.append({
            "type": "post",
            "key": f"post-{r['tiktok_id']}",
            "niche": r.get("niche") or "",
            "created_at": r.get("created_at"),
            "stored_cover": "/storage/v1/object/public/" in (r.get("cover_url") or ""),
            "post": {
                "url": r.get("url"), "caption": (r.get("caption") or "")[:220], "cover_url": r.get("cover_url"),
                "creator_handle": r.get("creator_handle"), "creator_followers": r.get("creator_followers"),
                "posted_at": _iso(r.get("created_at")), "signal": r.get("signal_type"),
            },
            "brand": _brand_card(r, targets),
        })
    return out


def _age_days(ts) -> float:
    from datetime import datetime, timezone
    if not ts:
        return 999.0
    if getattr(ts, "tzinfo", None) is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return max(0.0, (datetime.now(timezone.utc) - ts).total_seconds() / 86400)


def rank_posts(items: List[Dict], niche: str, seen: Dict[str, Any], rng: random.Random) -> List[Dict]:
    """Unseen first (this week's posts on top), niche next, shuffled within; seen ones last, oldest-seen first."""
    def key(it):
        is_seen = it["key"] in seen
        age = _age_days(it.get("created_at"))
        if is_seen:
            seen_at = seen[it["key"]]
            return (3, _age_days(seen_at) * -1, rng.random())
        tier = 0 if age <= NEW_DAYS else (1 if niche and it.get("niche") == niche else 2)
        score = age / POST_WINDOW_DAYS - (0.15 if it.get("stored_cover") else 0) + rng.random() * 0.6
        return (tier, score, 0)
    return sorted(items, key=key)


def _strip(it: Dict) -> Dict:
    return {k: v for k, v in it.items() if k not in ("niche", "created_at", "stored_cover")}


def next_page(conn, creator_id: int, niche: Optional[str] = None, exclude: Optional[List[str]] = None,
              size: int = PAGE_SIZE, rng: Optional[random.Random] = None) -> Dict[str, Any]:
    cur = conn.cursor(cursor_factory=RealDictCursor)
    rng = rng or random.Random()
    niche = (niche or "").strip().lower()
    if niche not in NICHE_CATEGORIES:
        niche = creator_niche(cur, creator_id) if niche != "all" else ""
    targets = target_ids(cur, creator_id)
    seen = seen_map(cur, creator_id)
    exclude_set = set((exclude or [])[:SESSION_CAP])

    ranked = [p for p in rank_posts(all_posts(cur, targets), niche, seen, rng) if p["key"] not in exclude_set]
    # One post per brand inside a page; a brand's other posts wait for a later page.
    want_posts = max(1, round(size * 2 / 3))
    page_posts, brands_in_page, rest = [], set(), []
    for p in ranked:
        bid = p["brand"]["id"]
        if len(page_posts) < want_posts and bid not in brands_in_page:
            page_posts.append(p)
            brands_in_page.add(bid)
        else:
            rest.append(p)

    taken = list(brands_in_page) + [int(k.split("-", 1)[1]) for k in exclude_set
                                    if k.startswith("brand-") and k.split("-", 1)[1].isdigit()]
    pool = directory_brands(cur, niche, taken, targets, limit=60)
    if niche and len(pool) < 20:
        pool += directory_brands(cur, "", taken + [b["brand"]["id"] for b in pool], targets, limit=40)
    unseen_b = [b for b in pool if b["key"] not in seen]
    seen_b = sorted([b for b in pool if b["key"] in seen], key=lambda b: seen[b["key"]])
    rng.shuffle(unseen_b)
    brand_pool = unseen_b + seen_b
    want_brands = size - len(page_posts)
    page_brands = brand_pool[:want_brands]

    # Unseen posts all used up: let unseen brands fill the page before any repeat shows.
    if page_posts and page_posts[-1]["key"] in seen and len(unseen_b) > want_brands:
        repeats = [p for p in page_posts if p["key"] in seen]
        page_posts = [p for p in page_posts if p["key"] not in seen]
        page_brands = unseen_b[:want_brands + len(repeats)]

    items = [_strip(i) for i in interleave(page_posts, page_brands)]
    remaining = len(rest) + max(0, len(brand_pool) - len(page_brands))
    has_more = bool(items) and remaining > 0 and len(exclude_set) + len(items) < SESSION_CAP
    return {"niche": niche, "niches": NICHES, "items": items, "targets": len(targets), "has_more": has_more,
            "unseen_left": sum(1 for p in rest if p["key"] not in seen)}
