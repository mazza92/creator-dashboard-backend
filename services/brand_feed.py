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
