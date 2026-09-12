"""
Roster fill demand — finish a short list of gift rosters, then mint the next.

Do not boost every 1-applicant brand. That spreads creators across infinite
thin lists and none ever become sendable.

Hunger is fill progress (higher = closer to send), not emptiness.
Only lists with ROSTER_FOCUS_MIN applicants enter the race, and only the
ROSTER_FOCUS_CAP closest-to-full get a For You / Discover boost.

Admin can also spotlight cold-emailed rosters so they skip the 3-applicant
gate and show on the Live now desk immediately.
"""

import threading

ROSTER_FILL_MULT = 3
ROSTER_FILL_PAD = 8
ROSTER_FOCUS_MIN = 3
ROSTER_FOCUS_CAP = 8
ROSTER_MINT_MIN = 8

_SPOTLIGHT_COL_READY = False
_SPOTLIGHT_COL_LOCK = threading.Lock()

# SQL joined as roster_demand on pr_brands b
ROSTER_DEMAND_JOIN = """
LEFT JOIN (
    WITH counts AS (
        SELECT a.brand_id, COUNT(*)::int AS fill_count
        FROM brand_pr_applications a
        WHERE a.status IN ('review', 'ships', 'posted')
        GROUP BY a.brand_id
    ),
    inbound_ids AS (
        SELECT DISTINCT c.brand_id
        FROM brand_pr_campaigns c
        JOIN pr_brands b ON b.id = c.brand_id
        WHERE c.status = 'active'
          AND b.source_opportunity_id IS NOT NULL
    ),
    spotlight_ids AS (
        SELECT DISTINCT brand_id
        FROM brand_pr_campaigns
        WHERE status = 'active'
          AND creator_spotlighted_at IS NOT NULL
    ),
    keys AS (
        SELECT brand_id FROM counts
        UNION
        SELECT brand_id FROM inbound_ids
        UNION
        SELECT brand_id FROM spotlight_ids
    ),
    targets AS (
        SELECT
            k.brand_id,
            COALESCE(co.fill_count, 0) AS fill_count,
            COALESCE(c.slot_limit, 5) AS slot_limit,
            GREATEST(
                COALESCE(c.slot_limit, 5) * 3,
                COALESCE(c.slot_limit, 5) + 8
            ) AS target,
            CASE WHEN i.brand_id IS NOT NULL THEN 1 ELSE 0 END AS inbound,
            CASE WHEN s.brand_id IS NOT NULL THEN 1 ELSE 0 END AS spotlighted
        FROM keys k
        LEFT JOIN counts co ON co.brand_id = k.brand_id
        LEFT JOIN inbound_ids i ON i.brand_id = k.brand_id
        LEFT JOIN spotlight_ids s ON s.brand_id = k.brand_id
        LEFT JOIN brand_pr_campaigns c
          ON c.brand_id = k.brand_id AND c.status = 'active'
    ),
    focused AS (
        SELECT brand_id
        FROM targets
        WHERE (fill_count >= 3 OR inbound = 1 OR spotlighted = 1)
          AND fill_count < target
        ORDER BY spotlighted DESC, inbound DESC, fill_count DESC, brand_id
        LIMIT 8
    )
    SELECT
        t.brand_id,
        CASE
            WHEN t.spotlighted = 1 OR t.inbound = 1 THEN GREATEST(t.fill_count, 1)
            WHEN f.brand_id IS NOT NULL THEN t.fill_count
            ELSE 0
        END AS hunger,
        t.fill_count,
        t.target,
        t.slot_limit,
        CASE WHEN t.spotlighted = 1 OR t.inbound = 1 OR t.fill_count > 0 THEN 1 ELSE 0 END AS is_open,
        t.spotlighted
    FROM targets t
    LEFT JOIN focused f ON f.brand_id = t.brand_id
) roster_demand ON roster_demand.brand_id = b.id
"""

ROSTER_DEMAND_SELECT = """
COALESCE(roster_demand.hunger, 0) AS roster_hunger,
COALESCE(roster_demand.fill_count, 0) AS roster_fill_count,
COALESCE(roster_demand.target, 0) AS roster_fill_target,
COALESCE(roster_demand.slot_limit, 0) AS roster_slot_limit,
COALESCE(roster_demand.is_open, 0) AS roster_is_open,
COALESCE(roster_demand.spotlighted, 0) AS roster_spotlighted
"""


def ensure_campaign_spotlight_column(cursor, conn=None):
    """Once per process. Adds creator_spotlighted_at if this DB is behind."""
    global _SPOTLIGHT_COL_READY
    if _SPOTLIGHT_COL_READY:
        return
    with _SPOTLIGHT_COL_LOCK:
        if _SPOTLIGHT_COL_READY:
            return
        try:
            from services.pg_hotpath_schema import public_column_exists, public_index_exists
            if public_column_exists(cursor, "brand_pr_campaigns", "creator_spotlighted_at"):
                _SPOTLIGHT_COL_READY = True
                return
            cursor.execute("SET LOCAL lock_timeout = '2s'")
            cursor.execute(
                """
                ALTER TABLE brand_pr_campaigns
                ADD COLUMN IF NOT EXISTS creator_spotlighted_at TIMESTAMPTZ
                """
            )
            if not public_index_exists(cursor, "idx_brand_pr_campaigns_spotlight"):
                cursor.execute(
                    """
                    CREATE INDEX IF NOT EXISTS idx_brand_pr_campaigns_spotlight
                    ON brand_pr_campaigns (creator_spotlighted_at DESC)
                    WHERE creator_spotlighted_at IS NOT NULL AND status = 'active'
                    """
                )
            if conn:
                conn.commit()
            _SPOTLIGHT_COL_READY = True
        except Exception as exc:
            if conn:
                try:
                    conn.rollback()
                except Exception:
                    pass
            print(f"[roster_demand] spotlight column ensure skipped: {exc}")


def fill_target(slot_limit):
    n = int(slot_limit or 5)
    n = max(1, min(n, 50))
    return max(n * ROSTER_FILL_MULT, n + ROSTER_FILL_PAD)


def mark_focus(campaigns):
    """Flag spotlighted lists plus the closest-to-full active lists For You will push."""
    rows = list(campaigns or [])
    spotlight_ids = {
        c.get("id") for c in rows
        if c.get("status") == "active" and c.get("spotlighted")
    }
    eligible = [
        c for c in rows
        if c.get("status") == "active"
        and c.get("id") not in spotlight_ids
        and int(c.get("fill_count") or 0) >= ROSTER_FOCUS_MIN
        and int(c.get("fill_count") or 0) < int(c.get("fill_target") or fill_target(c.get("slot_limit")))
    ]
    eligible.sort(key=lambda c: (-int(c.get("fill_count") or 0), int(c.get("id") or 0)))
    focus_ids = {c.get("id") for c in eligible[:ROSTER_FOCUS_CAP]}
    for c in rows:
        c["in_focus"] = c.get("id") in focus_ids or c.get("id") in spotlight_ids
    return rows


def pick_open_lists(ranked, limit=4, min_fit=0):
    """In-niche brands with a live gift list, closest-to-full first.

    Used for the For You campaign desk. Does not invent off-niche cards.
    Hunger can stay 0 here — fill_count is enough to show the list.
    """
    rows = []
    seen = set()
    for raw in ranked or []:
        b = dict(raw or {})
        if int(b.get("match_score") or 0) < int(min_fit or 0):
            continue
        fill = int(b.get("roster_fill_count") or b.get("roster_hunger") or 0)
        is_open = int(b.get("roster_is_open") or b.get("roster_open") or 0)
        if fill <= 0 and not is_open:
            continue
        bid = b.get("id")
        if bid in seen:
            continue
        seen.add(bid)
        rows.append(b)
    rows.sort(key=lambda b: (
        0 if int(b.get("roster_spotlighted") or 0) else 1,
        -int(b.get("roster_fill_count") or b.get("roster_hunger") or 0),
    ))
    return rows[: max(1, int(limit or 4))]


def merge_spotlighted_open_lists(open_lists, spotlighted, limit=8):
    """Put admin-pushed rosters first on the Live now desk, then hunger-ranked lists."""
    seen = set()
    out = []
    for raw in list(spotlighted or []) + list(open_lists or []):
        row = dict(raw or {})
        bid = row.get("id")
        if bid is None or bid in seen:
            continue
        seen.add(bid)
        out.append(row)
        if len(out) >= max(1, int(limit or 8)):
            break
    return out


def fetch_spotlighted_brand_rows(cursor, exclude_ids=None, limit=8):
    """Published brands whose active roster was pushed from admin."""
    ensure_campaign_spotlight_column(cursor)
    ids = list(exclude_ids or []) or [0]
    cursor.execute(
        f"""
        SELECT
            b.id, b.slug, b.brand_name AS name, b.logo_url AS logo,
            b.description, b.category, b.response_rate, b.price_point,
            b.min_followers, b.max_followers, b.micro_friendly, b.website,
            b.application_form_url, b.has_application_form, b.hero_product,
            (b.contact_email IS NOT NULL AND TRIM(b.contact_email) != '') AS has_email_contact,
            b.niches AS brand_niches, b.regions, b.avg_product_value,
            0 AS match_score,
            {ROSTER_DEMAND_SELECT}
        FROM pr_brands b
        {ROSTER_DEMAND_JOIN}
        JOIN brand_pr_campaigns c
          ON c.brand_id = b.id
         AND c.status = 'active'
         AND c.creator_spotlighted_at IS NOT NULL
        WHERE b.slug IS NOT NULL
          AND COALESCE(b.status, 'published') = 'published'
          AND b.id != ALL(%s)
        ORDER BY c.creator_spotlighted_at DESC NULLS LAST, b.id
        LIMIT %s
        """,
        (ids, max(1, int(limit or 8))),
    )
    return [dict(r) for r in (cursor.fetchall() or [])]


def prefer_hungry_rosters(ranked, pool=None, limit=8, max_hungry=4, min_fit=35):
    """Reorder underfilled rosters first — never invent off-niche or 0% cards.

    Hunger is a sort boost among brands that already passed niche + fit.
    Higher hunger = closer to a sendable list. Unranked / 0% stay out.
    """
    ranked_rows = [
        dict(r) for r in (ranked or [])
        if int((r or {}).get("match_score") or 0) >= int(min_fit or 0)
    ]
    if not ranked_rows:
        return []
    ranked_by_id = {b.get("id"): b for b in ranked_rows}
    hungry = [b for b in ranked_rows if int(b.get("roster_hunger") or 0) > 0]
    # Pool may only promote a brand already scored in `ranked`.
    if pool:
        extra = [
            ranked_by_id[b.get("id")]
            for b in pool
            if b.get("id") in ranked_by_id and int(b.get("roster_hunger") or 0) > 0
        ]
        if extra:
            hungry = extra + [b for b in hungry if b.get("id") not in {x.get("id") for x in extra}]
    hungry.sort(key=lambda b: int(b.get("roster_hunger") or 0), reverse=True)
    top_hungry = []
    taken = set()
    for b in hungry:
        bid = b.get("id")
        if bid in taken:
            continue
        taken.add(bid)
        top_hungry.append(b)
        if len(top_hungry) >= max_hungry:
            break
    rest = [b for b in ranked_rows if b.get("id") not in taken]
    return (top_hungry + rest)[:limit]
