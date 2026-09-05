"""
Roster fill demand — finish a short list of gift rosters, then mint the next.

Do not boost every 1-applicant brand. That spreads creators across infinite
thin lists and none ever become sendable.

Hunger is fill progress (higher = closer to send), not emptiness.
Only lists with ROSTER_FOCUS_MIN applicants enter the race, and only the
ROSTER_FOCUS_CAP closest-to-full get a For You / Discover boost.
"""

ROSTER_FILL_MULT = 3
ROSTER_FILL_PAD = 8
ROSTER_FOCUS_MIN = 3
ROSTER_FOCUS_CAP = 8
ROSTER_MINT_MIN = 8

# SQL joined as roster_demand on pr_brands b
ROSTER_DEMAND_JOIN = """
LEFT JOIN (
    WITH counts AS (
        SELECT a.brand_id, COUNT(*)::int AS fill_count
        FROM brand_pr_applications a
        WHERE a.status IN ('review', 'ships', 'posted')
        GROUP BY a.brand_id
    ),
    targets AS (
        SELECT
            co.brand_id,
            co.fill_count,
            COALESCE(c.slot_limit, 5) AS slot_limit,
            GREATEST(
                COALESCE(c.slot_limit, 5) * 3,
                COALESCE(c.slot_limit, 5) + 8
            ) AS target
        FROM counts co
        LEFT JOIN brand_pr_campaigns c
          ON c.brand_id = co.brand_id AND c.status = 'active'
    ),
    focused AS (
        SELECT brand_id
        FROM targets
        WHERE fill_count >= 3
          AND fill_count < target
        ORDER BY fill_count DESC, brand_id
        LIMIT 8
    )
    SELECT
        t.brand_id,
        CASE WHEN f.brand_id IS NOT NULL THEN t.fill_count ELSE 0 END AS hunger,
        t.fill_count,
        t.target,
        t.slot_limit
    FROM targets t
    LEFT JOIN focused f ON f.brand_id = t.brand_id
) roster_demand ON roster_demand.brand_id = b.id
"""

ROSTER_DEMAND_SELECT = """
COALESCE(roster_demand.hunger, 0) AS roster_hunger,
COALESCE(roster_demand.fill_count, 0) AS roster_fill_count,
COALESCE(roster_demand.target, 0) AS roster_fill_target,
COALESCE(roster_demand.slot_limit, 0) AS roster_slot_limit
"""


def fill_target(slot_limit):
    n = int(slot_limit or 5)
    n = max(1, min(n, 50))
    return max(n * ROSTER_FILL_MULT, n + ROSTER_FILL_PAD)


def mark_focus(campaigns):
    """Flag the closest-to-full active lists For You will actually push."""
    rows = list(campaigns or [])
    eligible = [
        c for c in rows
        if c.get("status") == "active"
        and int(c.get("fill_count") or 0) >= ROSTER_FOCUS_MIN
        and int(c.get("fill_count") or 0) < int(c.get("fill_target") or fill_target(c.get("slot_limit")))
    ]
    eligible.sort(key=lambda c: (-int(c.get("fill_count") or 0), int(c.get("id") or 0)))
    focus_ids = {c.get("id") for c in eligible[:ROSTER_FOCUS_CAP]}
    for c in rows:
        c["in_focus"] = c.get("id") in focus_ids
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
        if fill <= 0:
            continue
        bid = b.get("id")
        if bid in seen:
            continue
        seen.add(bid)
        rows.append(b)
    rows.sort(key=lambda b: int(b.get("roster_fill_count") or b.get("roster_hunger") or 0), reverse=True)
    return rows[: max(1, int(limit or 4))]


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
