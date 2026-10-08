"""Pro placement — put active Pro creators on live rosters that fit them.

Sales copy sells Polly autopilot. This still auto-applies Pro creators to
in-niche active rosters (reusing the posts and
shipping address from their last application) and ranks them first on the
roster and in the brand's one-click shortlist.

Runs from the hourly roster cron. Daily and per-roster caps keep brand lists
from turning into a wall of the same Pro faces.
"""
from __future__ import annotations

import json
import os
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple

from psycopg2.extras import Json

from services.audience_fit import (
    _LANE_COMPATIBLE,
    audience_mismatch,
    brand_content_lanes,
    creator_content_lanes,
)

PRO_SOURCE = "pro_auto"
PRO_DAILY_ADDS = 3
PRO_PER_ROSTER = 2
PLACED_LOOKBACK_DAYS = 30


def internal_emails() -> List[str]:
    """Founder/test Pro accounts that must never land on a real brand roster."""
    raw = os.getenv("PRO_PLACEMENT_EXCLUDE_EMAILS", "mahery92@hotmail.fr")
    return [e.strip().lower() for e in raw.split(",") if e.strip()]

_GLOBAL_REGIONS = {"global", "worldwide", "international", "ww", "all"}
_EU_CODES = {
    "AT", "BE", "BG", "HR", "CY", "CZ", "DK", "EE", "FI", "FR", "DE", "GR", "HU",
    "IE", "IT", "LV", "LT", "LU", "MT", "NL", "PL", "PT", "RO", "SK", "SI", "ES", "SE",
}
_REGION_ALIASES = {
    "usa": "US", "united states": "US", "america": "US",
    "uk": "GB", "united kingdom": "GB", "england": "GB", "great britain": "GB",
    "canada": "CA", "australia": "AU", "new zealand": "NZ",
}


def _as_list(raw: Any) -> list:
    if raw is None:
        return []
    if isinstance(raw, (list, tuple, set)):
        return list(raw)
    if isinstance(raw, str):
        text = raw.strip()
        if not text:
            return []
        try:
            parsed = json.loads(text)
            if isinstance(parsed, list):
                return parsed
        except Exception:
            pass
        return [bit.strip() for bit in text.split(",") if bit.strip()]
    return []


def _as_dict(raw: Any) -> dict:
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str) and raw.strip():
        try:
            parsed = json.loads(raw)
            return parsed if isinstance(parsed, dict) else {}
        except Exception:
            return {}
    return {}


def _country_code(raw: Any) -> Optional[str]:
    text = str(raw or "").strip()
    if not text:
        return None
    alias = _REGION_ALIASES.get(text.lower())
    if alias:
        return alias
    if len(text) == 2 and text.isalpha():
        code = text.upper()
        return "GB" if code == "UK" else code
    try:
        from social_verification_routes import normalize_country_code

        return normalize_country_code(text)
    except Exception:
        return None


def ships_to(brand_regions: Any, country: Any) -> bool:
    """Unknown on either side passes. Brands list codes, names, EU/Europe or Global."""
    regions = [str(r).strip() for r in _as_list(brand_regions) if str(r).strip()]
    if not regions:
        return True
    code = _country_code(country)
    if not code:
        return True
    for region in regions:
        low = region.lower()
        if low in _GLOBAL_REGIONS:
            return True
        if low in ("eu", "europe") and code in _EU_CODES:
            return True
        if _country_code(region) == code:
            return True
    return False


def niche_fits(creator_niches: Iterable, brand: Dict) -> bool:
    """Positive lane overlap only. Unknown lanes do not count as a fit here."""
    creator_lanes = creator_content_lanes(niches=list(creator_niches or []))
    brand_lanes = brand_content_lanes(brand)
    if not creator_lanes or not brand_lanes:
        return False
    return any(brand_lanes & _LANE_COMPATIBLE.get(lane, {lane}) for lane in creator_lanes)


def complete_address(addr: Dict) -> bool:
    return bool(
        str(addr.get("full_name") or "").strip()
        and str(addr.get("address_line1") or "").strip()
        and str(addr.get("city") or "").strip()
        and str(addr.get("country") or "").strip()
    )


def creator_niches(creator: Dict) -> List[str]:
    out: List[str] = []
    for raw in (creator.get("creator_niches"), creator.get("niche")):
        for item in _as_list(raw):
            text = str(item or "").strip()
            if text and text.lower() not in (n.lower() for n in out):
                out.append(text)
    return out


def plan_placements(
    creators: List[Dict],
    rosters: List[Dict],
    applied: Set[Tuple[int, int]],
    pro_on_roster: Dict[int, int],
    adds_today: Dict[int, int],
    daily_cap: int = PRO_DAILY_ADDS,
    per_roster: int = PRO_PER_ROSTER,
) -> List[Tuple[Dict, Dict]]:
    """Pick (creator, roster) pairs. Rosters arrive best-first; creators least-served first."""
    plan: List[Tuple[Dict, Dict]] = []
    seats = dict(pro_on_roster)
    for creator in creators:
        cid = int(creator["creator_id"])
        budget = daily_cap - int(adds_today.get(cid, 0))
        if budget <= 0:
            continue
        niches = creator_niches(creator)
        country = creator.get("country")
        for roster in rosters:
            if budget <= 0:
                break
            bid = int(roster["brand_id"])
            if (cid, bid) in applied:
                continue
            if seats.get(bid, 0) >= per_roster:
                continue
            if not niche_fits(niches, roster):
                continue
            if audience_mismatch(roster, {}, niches):
                continue
            if not ships_to(roster.get("regions"), country):
                continue
            plan.append((creator, roster))
            applied.add((cid, bid))
            seats[bid] = seats.get(bid, 0) + 1
            budget -= 1
    return plan


def _load_pro_creators(cursor) -> Tuple[List[Dict], List[int]]:
    cursor.execute(
        """
        SELECT
            c.id AS creator_id,
            c.niche,
            c.creator_niches,
            c.shipping_address AS profile_address,
            u.country AS user_country,
            last_app.selected_posts,
            last_app.shipping_address AS last_address,
            (
                SELECT COUNT(*)::int FROM brand_pr_applications a
                WHERE a.creator_id = c.id AND a.status = 'review'
            ) AS waiting
        FROM creators c
        JOIN users u ON u.id = c.user_id
        LEFT JOIN LATERAL (
            SELECT a.selected_posts, a.shipping_address
            FROM brand_pr_applications a
            WHERE a.creator_id = c.id
              AND jsonb_typeof(a.selected_posts) = 'array'
              AND jsonb_array_length(a.selected_posts) > 0
            ORDER BY a.applied_at DESC NULLS LAST
            LIMIT 1
        ) last_app ON TRUE
        WHERE LOWER(COALESCE(c.subscription_tier, '')) = 'pro'
          AND LOWER(COALESCE(c.subscription_status, '')) = 'active'
          AND LOWER(COALESCE(u.email, '')) NOT LIKE '%%@newcollab.co'
          AND LOWER(COALESCE(u.email, '')) <> ALL(%s)
          AND NOT EXISTS (
            SELECT 1 FROM brand_pr_applications p
            WHERE p.creator_id = c.id
              AND p.status IN ('ships', 'posted')
              AND p.updated_at > NOW() - make_interval(days => %s)
          )
        ORDER BY waiting ASC, c.id
        """,
        (internal_emails() or [""], PLACED_LOOKBACK_DAYS),
    )
    ready, missing = [], []
    for row in cursor.fetchall() or []:
        row = dict(row)
        addr = _as_dict(row.get("last_address")) or _as_dict(row.get("profile_address"))
        posts = _as_list(row.get("selected_posts"))
        if not posts or not complete_address(addr):
            missing.append(int(row["creator_id"]))
            continue
        row["address"] = addr
        row["posts"] = posts[:3]
        row["country"] = addr.get("country") or row.get("user_country")
        ready.append(row)
    return ready, missing


def _load_open_rosters(cursor) -> List[Dict]:
    """Brands most likely to pick first: real recent views, then fuller lists."""
    cursor.execute(
        """
        SELECT
            c.id AS campaign_id,
            b.id AS brand_id,
            b.brand_name,
            b.category,
            b.niches AS brand_niches,
            b.description,
            b.hero_product,
            b.regions,
            (
                SELECT MAX(e.created_at) FROM brand_pr_events e
                WHERE e.brand_id = b.id AND e.event = 'roster_view' AND e.meta ? 'ua'
            ) AS last_view_at,
            (
                SELECT COUNT(*)::int FROM brand_pr_applications a
                WHERE a.brand_id = b.id AND a.status = 'review'
            ) AS waiting
        FROM brand_pr_campaigns c
        JOIN pr_brands b ON b.id = c.brand_id
        WHERE c.status = 'active'
          AND COALESCE(b.status, 'published') = 'published'
        ORDER BY last_view_at DESC NULLS LAST, waiting DESC, c.id
        """
    )
    return [dict(r) for r in cursor.fetchall() or []]


def _load_applied(cursor, creator_ids: List[int]) -> Set[Tuple[int, int]]:
    if not creator_ids:
        return set()
    cursor.execute(
        "SELECT creator_id, brand_id FROM brand_pr_applications WHERE creator_id = ANY(%s)",
        (creator_ids,),
    )
    return {(int(r["creator_id"]), int(r["brand_id"])) for r in cursor.fetchall() or []}


def _load_pro_seats(cursor) -> Dict[int, int]:
    cursor.execute(
        """
        SELECT a.brand_id, COUNT(*)::int AS n
        FROM brand_pr_applications a
        JOIN creators c ON c.id = a.creator_id
        JOIN brand_pr_campaigns bc ON bc.brand_id = a.brand_id AND bc.status = 'active'
        WHERE a.status = 'review'
          AND LOWER(COALESCE(c.subscription_tier, '')) = 'pro'
          AND LOWER(COALESCE(c.subscription_status, '')) = 'active'
        GROUP BY a.brand_id
        """
    )
    return {int(r["brand_id"]): int(r["n"]) for r in cursor.fetchall() or []}


def _load_adds_today(cursor, creator_ids: List[int]) -> Dict[int, int]:
    if not creator_ids:
        return {}
    cursor.execute(
        """
        SELECT creator_id, COUNT(*)::int AS n
        FROM brand_pr_applications
        WHERE creator_id = ANY(%s) AND source = %s
          AND applied_at > NOW() - INTERVAL '24 hours'
        GROUP BY creator_id
        """,
        (creator_ids, PRO_SOURCE),
    )
    return {int(r["creator_id"]): int(r["n"]) for r in cursor.fetchall() or []}


def place_pro_creators(conn, dry_run: bool = False) -> Dict[str, Any]:
    """Auto-apply active Pro creators to fitting live rosters. Commits per insert."""
    from psycopg2.extras import RealDictCursor

    cursor = conn.cursor(cursor_factory=RealDictCursor)
    creators, missing = _load_pro_creators(cursor)
    ids = [int(c["creator_id"]) for c in creators]
    plan = plan_placements(
        creators,
        _load_open_rosters(cursor),
        _load_applied(cursor, ids),
        _load_pro_seats(cursor),
        _load_adds_today(cursor, ids),
    )
    stats: Dict[str, Any] = {
        "pro_ready": len(creators),
        "pro_missing_profile": missing,
        "planned": len(plan),
        "added": 0,
        "errors": 0,
        "placements": [],
    }
    if dry_run:
        stats["placements"] = [
            {"creator_id": c["creator_id"], "brand_id": r["brand_id"], "brand": r.get("brand_name")}
            for c, r in plan
        ]
        conn.rollback()
        return stats

    for creator, roster in plan:
        try:
            cursor.execute(
                """
                INSERT INTO brand_pr_applications
                    (creator_id, brand_id, campaign_id, status, selected_posts, shipping_address,
                     source, agreed_at, applied_at, updated_at)
                SELECT %s, %s, %s, 'review', %s, %s, %s, NOW(), NOW(), NOW()
                WHERE NOT EXISTS (
                    SELECT 1 FROM brand_pr_applications
                    WHERE creator_id = %s AND brand_id = %s
                )
                RETURNING id
                """,
                (
                    creator["creator_id"], roster["brand_id"], roster["campaign_id"],
                    Json(creator["posts"]), Json(creator["address"]), PRO_SOURCE,
                    creator["creator_id"], roster["brand_id"],
                ),
            )
            row = cursor.fetchone()
            if not row:
                conn.rollback()
                continue
            cursor.execute(
                """
                INSERT INTO brand_pr_events (creator_id, brand_id, event, source, meta)
                VALUES (%s, %s, 'apply_submitted', %s, %s)
                """,
                (
                    creator["creator_id"], roster["brand_id"], PRO_SOURCE,
                    Json({"application_id": row["id"], "campaign_id": roster["campaign_id"]}),
                ),
            )
            conn.commit()
        except Exception as exc:
            conn.rollback()
            stats["errors"] += 1
            print(f"[pro-placement] insert failed creator={creator['creator_id']} brand={roster['brand_id']}: {exc}")
            continue
        stats["added"] += 1
        stats["placements"].append({
            "creator_id": creator["creator_id"],
            "brand_id": roster["brand_id"],
            "brand": roster.get("brand_name"),
            "application_id": row["id"],
        })
        try:
            from services.polly_tracker import record_campaign_applied

            record_campaign_applied(
                conn,
                int(creator["creator_id"]),
                {"id": roster["brand_id"], "name": roster.get("brand_name"), "application_id": row["id"]},
            )
        except Exception as exc:
            print(f"[pro-placement] timeline log skipped: {exc}")
    return stats
