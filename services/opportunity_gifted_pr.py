"""
Push approved brand submissions onto the Gifted PR apply + roster path.

Scanner-sourced gigs stay on Open gigs. Brand-submitted listings become a
published pr_brands row with an active roster so creators apply in-app.
"""

from __future__ import annotations

import json
import re
from datetime import datetime

from psycopg2.extras import Json


def is_brand_submission(opp) -> bool:
    notes = str((opp or {}).get("additional_notes") or "")
    return "[scanner:" not in notes


def slugify_brand(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", (name or "").lower()).strip("-")
    return slug or f"brand-{int(datetime.now().timestamp())}"


def min_followers_from_ranges(ranges) -> int:
    if not ranges:
        return 0
    if isinstance(ranges, str):
        ranges = [ranges]
    lowest = None
    for raw in ranges:
        text = str(raw or "").strip().lower().replace(",", "")
        if not text:
            continue
        nums = []
        for match in re.finditer(r"(\d+(?:\.\d+)?)\s*([kmb])?", text):
            value = float(match.group(1))
            suffix = match.group(2) or ""
            mult = {"k": 1000, "m": 1_000_000, "b": 1_000_000_000}.get(suffix, 1)
            nums.append(int(value * mult))
        if nums:
            candidate = min(nums)
            lowest = candidate if lowest is None else min(lowest, candidate)
    return int(lowest or 0)


def _as_list(value):
    if value is None:
        return []
    if isinstance(value, list):
        return [str(v).strip() for v in value if str(v).strip()]
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return []
        if text.startswith("["):
            try:
                parsed = json.loads(text)
                if isinstance(parsed, list):
                    return [str(v).strip() for v in parsed if str(v).strip()]
            except Exception:
                pass
        return [part.strip() for part in re.split(r"[,|/]", text) if part.strip()]
    return [str(value).strip()] if str(value).strip() else []


def niches_from_opportunity(opp) -> list:
    items = _as_list(opp.get("creator_niches"))
    category = str(opp.get("brand_category") or "").strip()
    if category and category.lower() not in ("other", "unknown", "n/a", "none"):
        items.append(category)
    seen = set()
    out = []
    for item in items:
        key = item.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(item)
    return out[:12]


def platforms_from_opportunity(opp) -> list:
    mapping = {
        "tiktok": "tiktok",
        "reel": "instagram",
        "reels": "instagram",
        "instagram": "instagram",
        "ig": "instagram",
        "youtube": "youtube",
        "yt": "youtube",
        "short": "youtube",
    }
    found = []
    for raw in _as_list(opp.get("content_types")):
        key = raw.lower()
        platform = mapping.get(key)
        if not platform:
            for token, name in mapping.items():
                if token in key:
                    platform = name
                    break
        if platform and platform not in found:
            found.append(platform)
    return found or ["instagram", "tiktok"]


def category_from_opportunity(opp) -> str:
    raw = str(opp.get("brand_category") or "").strip()
    if raw and raw.lower() not in ("other", "unknown", "n/a", "none"):
        return raw.replace("_", " ")[:100]
    niches = niches_from_opportunity(opp)
    return niches[0][:100] if niches else "lifestyle"


def apply_url_for_brand(slug: str) -> str:
    return f"https://app.newcollab.co/creator/dashboard/for-you?brand={slug}"


def ensure_opportunity_gifted_pr_columns(cursor):
    cursor.execute("ALTER TABLE opportunities ADD COLUMN IF NOT EXISTS pr_brand_id INTEGER")
    cursor.execute("ALTER TABLE pr_brands ADD COLUMN IF NOT EXISTS source_opportunity_id INTEGER")
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_opportunities_pr_brand_id ON opportunities(pr_brand_id)"
    )
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_pr_brands_source_opportunity ON pr_brands(source_opportunity_id)"
    )


def _unique_slug(cursor, base: str, existing_id=None) -> str:
    slug = slugify_brand(base)
    candidate = slug
    suffix = 2
    while True:
        cursor.execute(
            "SELECT id FROM pr_brands WHERE slug = %s AND (%s IS NULL OR id != %s) LIMIT 1",
            (candidate, existing_id, existing_id),
        )
        if not cursor.fetchone():
            return candidate
        candidate = f"{slug}-{suffix}"
        suffix += 1


_GENERIC_HOSTS = {
    "amazon.com", "amzn.to", "instagram.com", "tiktok.com", "facebook.com",
    "youtube.com", "linktr.ee", "bit.ly", "forms.gle", "docs.google.com",
}


def _website_host(url: str) -> str:
    raw = str(url or "").strip().lower()
    if not raw:
        return ""
    raw = re.sub(r"^https?://", "", raw)
    raw = raw.split("/")[0].split("?")[0]
    if raw.startswith("www."):
        raw = raw[4:]
    if not raw or raw in _GENERIC_HOSTS:
        return ""
    return raw


def _find_existing_brand(cursor, opp):
    opp_id = opp.get("id")
    if opp_id:
        cursor.execute(
            "SELECT id, slug FROM pr_brands WHERE source_opportunity_id = %s LIMIT 1",
            (opp_id,),
        )
        row = cursor.fetchone()
        if row:
            return row
        if opp.get("pr_brand_id"):
            cursor.execute(
                "SELECT id, slug FROM pr_brands WHERE id = %s LIMIT 1",
                (opp["pr_brand_id"],),
            )
            row = cursor.fetchone()
            if row:
                return row

    host = _website_host(opp.get("brand_website"))
    if host:
        cursor.execute(
            """
            SELECT id, slug
            FROM pr_brands
            WHERE regexp_replace(
                    lower(split_part(regexp_replace(COALESCE(website, ''), '^https?://', '') , '/', 1)),
                    '^www\\.',
                    ''
                  ) = %s
            ORDER BY CASE WHEN COALESCE(status, 'published') = 'published' THEN 0 ELSE 1 END, id
            LIMIT 1
            """,
            (host,),
        )
        row = cursor.fetchone()
        if row:
            return row

    name = str(opp.get("brand_name") or "").strip()
    if not name:
        return None
    cursor.execute(
        """
        SELECT id, slug
        FROM pr_brands
        WHERE LOWER(TRIM(brand_name)) = LOWER(TRIM(%s))
        ORDER BY CASE WHEN COALESCE(status, 'published') = 'published' THEN 0 ELSE 1 END, id
        LIMIT 1
        """,
        (name,),
    )
    return cursor.fetchone()


def upsert_pr_brand_from_opportunity(cursor, opp) -> dict:
    existing = _find_existing_brand(cursor, opp)
    name = str(opp.get("brand_name") or "Brand").strip()
    slug = _unique_slug(cursor, name, existing["id"] if existing else None)
    niches = niches_from_opportunity(opp)
    regions = _as_list(opp.get("shipping_regions")) or ["Worldwide"]
    platforms = platforms_from_opportunity(opp)
    category = category_from_opportunity(opp)
    product = str(opp.get("product_name") or "").strip() or None
    description = str(opp.get("campaign_description") or "").strip() or None
    website = str(opp.get("brand_website") or "").strip() or None
    email = str(opp.get("brand_email") or "").strip() or None
    logo = str(opp.get("brand_logo_url") or "").strip() or None
    min_followers = min_followers_from_ranges(opp.get("follower_ranges"))
    value = opp.get("pr_value_usd")
    notes = "Inbound brand submission — actively filling a gifted PR roster."

    if existing:
        cursor.execute(
            """
            UPDATE pr_brands
            SET brand_name = COALESCE(NULLIF(%s, ''), brand_name),
                slug = COALESCE(slug, %s),
                website = COALESCE(NULLIF(%s, ''), website),
                logo_url = COALESCE(NULLIF(%s, ''), logo_url),
                description = COALESCE(NULLIF(%s, ''), description),
                contact_email = COALESCE(NULLIF(%s, ''), contact_email),
                category = COALESCE(NULLIF(%s, ''), category),
                niches = COALESCE(%s, niches),
                platforms = COALESCE(%s, platforms),
                regions = COALESCE(%s, regions),
                min_followers = COALESCE(%s, min_followers, 0),
                hero_product = COALESCE(NULLIF(%s, ''), hero_product),
                avg_product_value = COALESCE(%s, avg_product_value),
                collaboration_type = COALESCE(collaboration_type, 'gifted'),
                accepting_pr = TRUE,
                open_pr_featured = TRUE,
                status = 'published',
                source_opportunity_id = COALESCE(source_opportunity_id, %s),
                notes = CASE
                    WHEN notes IS NULL OR notes = '' THEN %s
                    ELSE notes
                END,
                updated_at = NOW()
            WHERE id = %s
            RETURNING id, slug, brand_name
            """,
            (
                name,
                slug,
                website,
                logo,
                description,
                email,
                category,
                Json(niches),
                Json(platforms),
                Json(regions),
                min_followers,
                product,
                value,
                opp.get("id"),
                notes,
                existing["id"],
            ),
        )
        return dict(cursor.fetchone())

    cursor.execute(
        """
        INSERT INTO pr_brands (
            brand_name, slug, website, logo_url, description, contact_email,
            category, niches, platforms, regions, min_followers, hero_product,
            avg_product_value, collaboration_type, accepting_pr, open_pr_featured,
            status, source_opportunity_id, notes, source_url, created_at
        ) VALUES (
            %s, %s, %s, %s, %s, %s,
            %s, %s, %s, %s, %s, %s,
            %s, 'gifted', TRUE, TRUE,
            'published', %s, %s, %s, NOW()
        )
        RETURNING id, slug, brand_name
        """,
        (
            name,
            slug,
            website,
            logo,
            description,
            email,
            category,
            Json(niches),
            Json(platforms),
            Json(regions),
            min_followers,
            product,
            value,
            opp.get("id"),
            notes,
            website,
        ),
    )
    return dict(cursor.fetchone())


def push_opportunity_to_gifted_pr(cursor, opp) -> dict | None:
    """Create/update the Gifted PR brand + mint a roster. No-op for scanner gigs."""
    if not is_brand_submission(opp):
        return None

    ensure_opportunity_gifted_pr_columns(cursor)
    brand = upsert_pr_brand_from_opportunity(cursor, opp)

    from brand_pr_roster_routes import (
        DEFAULT_DEAL_CHIPS,
        DEFAULT_HEADLINE,
        DEFAULT_LEDE,
        ensure_active_roster_for_brand,
    )

    slot_limit = max(1, min(int(opp.get("spots_total") or 5), 50))
    campaign, created = ensure_active_roster_for_brand(
        cursor, brand["id"], slot_limit=slot_limit
    )
    product = str(opp.get("product_name") or "").strip()
    title = f"{brand.get('brand_name') or 'Brand'} · Gifted PR"
    if campaign:
        cursor.execute(
            """
            UPDATE brand_pr_campaigns
            SET title = COALESCE(NULLIF(title, ''), %s),
                headline = COALESCE(NULLIF(headline, ''), %s),
                lede = COALESCE(NULLIF(lede, ''), %s),
                sku_note = COALESCE(NULLIF(sku_note, ''), %s),
                slot_limit = CASE
                    WHEN slot_limit IS NULL OR slot_limit < 1 THEN %s
                    ELSE slot_limit
                END,
                deal_chips = COALESCE(deal_chips, %s),
                updated_at = NOW()
            WHERE id = %s
            RETURNING id, token, status, slot_limit, title
            """,
            (
                title[:255],
                DEFAULT_HEADLINE,
                DEFAULT_LEDE,
                product or None,
                slot_limit,
                Json(list(DEFAULT_DEAL_CHIPS)),
                campaign["id"],
            ),
        )
        patched = cursor.fetchone()
        if patched:
            campaign = patched

    cursor.execute(
        "UPDATE opportunities SET pr_brand_id = %s WHERE id = %s",
        (brand["id"], opp["id"]),
    )

    token = (campaign or {}).get("token")
    slug = brand.get("slug")
    return {
        "pr_brand_id": brand["id"],
        "pr_brand_slug": slug,
        "pr_brand_name": brand.get("brand_name"),
        "apply_url": apply_url_for_brand(slug) if slug else None,
        "roster_token": token,
        "roster_url": f"https://app.newcollab.co/r/{token}" if token else None,
        "roster_status": (campaign or {}).get("status"),
        "roster_created": bool(created),
        "slot_limit": (campaign or {}).get("slot_limit") or slot_limit,
    }


def gifted_pr_payload_for_opportunity(cursor, opp) -> dict:
    ensure_opportunity_gifted_pr_columns(cursor)
    brand_id = opp.get("pr_brand_id")
    if not brand_id:
        return {
            "pr_brand_id": None,
            "pr_brand_slug": None,
            "apply_url": None,
            "roster_url": None,
            "roster_status": None,
            "gifted_pr": False,
        }
    cursor.execute(
        """
        SELECT b.id, b.slug, b.brand_name, c.token, c.status AS roster_status
        FROM pr_brands b
        LEFT JOIN LATERAL (
            SELECT token, status
            FROM brand_pr_campaigns
            WHERE brand_id = b.id AND status IN ('active', 'locked')
            ORDER BY CASE WHEN status = 'active' THEN 0 ELSE 1 END, created_at DESC
            LIMIT 1
        ) c ON TRUE
        WHERE b.id = %s
        """,
        (brand_id,),
    )
    row = cursor.fetchone() or {}
    slug = row.get("slug")
    token = row.get("token")
    return {
        "pr_brand_id": row.get("id") or brand_id,
        "pr_brand_slug": slug,
        "apply_url": apply_url_for_brand(slug) if slug else None,
        "roster_url": f"https://app.newcollab.co/r/{token}" if token else None,
        "roster_status": row.get("roster_status"),
        "gifted_pr": True,
    }
