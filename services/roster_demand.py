"""
Roster fill demand — push underfilled Brand PR campaigns in For You / Discover.

Mechanic: once a roster is minted (status=active), keep boosting that brand
until it has enough applicants for a real shortlist (3× slots, min slots+8).
Then the boost turns off so the next roster can fill. Niche matching still
filters who sees it.
"""

ROSTER_FILL_MULT = 3
ROSTER_FILL_PAD = 8

# SQL joined as roster_demand on pr_brands b
ROSTER_DEMAND_JOIN = """
LEFT JOIN (
    SELECT
        c.brand_id,
        MAX(
            GREATEST(
                0,
                GREATEST(c.slot_limit * 3, c.slot_limit + 8)
                - COALESCE((
                    SELECT COUNT(*)::int
                    FROM brand_pr_applications a
                    WHERE a.brand_id = c.brand_id
                      AND (a.campaign_id IS NULL OR a.campaign_id = c.id)
                      AND a.status IN ('review', 'ships', 'posted')
                ), 0)
            )
        ) AS hunger
    FROM brand_pr_campaigns c
    WHERE c.status = 'active'
    GROUP BY c.brand_id
) roster_demand ON roster_demand.brand_id = b.id
"""

ROSTER_DEMAND_SELECT = """
COALESCE(roster_demand.hunger, 0) AS roster_hunger
"""


def fill_target(slot_limit):
    n = int(slot_limit or 5)
    n = max(1, min(n, 50))
    return max(n * ROSTER_FILL_MULT, n + ROSTER_FILL_PAD)


def prefer_hungry_rosters(ranked, pool=None, limit=8, max_hungry=4, min_fit=35):
    """Reorder underfilled rosters first — never invent off-niche or 0% cards.

    Hunger is a sort boost among brands that already passed niche + fit.
    Unranked pool rows (match_score 0) stay out so a tech creator does not
    see a hungry beauty roster at 0% fit.
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
