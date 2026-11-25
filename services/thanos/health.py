"""Thanos pool health: will any niche run out of brands to pitch?

For each directory category: how many brands can be pitched (deliverable inbox, published, accepting), how many
active creators want that category, and how many days of supply that is at their pace. Categories under
SUPPLY_DAYS need new brands (Radar contact finding, scrapers) before creators hit the ladder's wider levels.
"""

from __future__ import annotations

from typing import Any, Dict, List

SUPPLY_DAYS = 30
PITCHES_PER_CREATOR_DAY = 3

HEALTH_SQL = """
    WITH sendable AS (
        SELECT LOWER(COALESCE(category, '')) AS category, COUNT(*) AS brands
        FROM pr_brands
        WHERE COALESCE(status, 'published') = 'published'
          AND NULLIF(TRIM(contact_email), '') IS NOT NULL
          AND COALESCE(LOWER(email_status), '') NOT IN ('bounced', 'invalid', 'undeliverable', 'risky')
          AND COALESCE(email_bounce_count, 0) = 0
          AND COALESCE(accepting_pr, TRUE)
        GROUP BY 1
    ),
    pitched AS (
        SELECT LOWER(COALESCE(b.category, '')) AS category, COUNT(DISTINCT e.brand_id) AS brands_pitched_30d,
               COUNT(DISTINCT e.creator_id) AS creators_30d, COUNT(*) AS pitches_30d
        FROM polly_timeline_events e JOIN pr_brands b ON b.id = e.brand_id
        WHERE e.event_type = 'pitch_sent' AND e.occurred_at > NOW() - INTERVAL '30 days'
        GROUP BY 1
    )
    SELECT s.category, s.brands, COALESCE(p.brands_pitched_30d, 0) AS brands_pitched_30d,
           COALESCE(p.creators_30d, 0) AS creators_30d, COALESCE(p.pitches_30d, 0) AS pitches_30d
    FROM sendable s LEFT JOIN pitched p ON p.category = s.category
    ORDER BY s.brands
"""


def assess(rows) -> List[Dict[str, Any]]:
    out = []
    for r in rows or []:
        get = (lambda k, i: r.get(k)) if isinstance(r, dict) else (lambda k, i: r[i])
        brands = int(get("brands", 1) or 0)
        creators = int(get("creators_30d", 3) or 0)
        per_creator_supply = brands / creators if creators else float(brands)
        days = per_creator_supply / PITCHES_PER_CREATOR_DAY
        out.append({
            "category": get("category", 0) or "(none)",
            "sendable_brands": brands,
            "brands_pitched_30d": int(get("brands_pitched_30d", 2) or 0),
            "creators_30d": creators,
            "pitches_30d": int(get("pitches_30d", 4) or 0),
            "days_of_supply": round(days, 1),
            "status": "low" if creators and days < SUPPLY_DAYS else "ok",
        })
    return sorted(out, key=lambda x: (x["status"] != "low", x["days_of_supply"]))


def pool_health(conn) -> List[Dict[str, Any]]:
    from services.thanos.db import fetch
    try:
        return assess(fetch(conn, HEALTH_SQL) or [])
    except Exception as err:  # noqa: BLE001
        print(f"[Thanos] pool health failed: {err}")
        return []
