"""Which brands we KNOW are giving out boxes. Everything else is pitched cold.

`pr_brands.accepting_pr` is a default left on for every brand, so it can't tell creators anything. A brand is
gifting-confirmed (pr_brands.gifting_confirmed_at / gifting_signal) only on evidence:

- brand_list: the brand runs a gift list with us (listing it submitted is live, or it locked/shipped a list
  recently, or it pays for one);
- shipped:    a creator on Newcollab got a box from it recently;
- tiktok:     the daily TikTok scan saw small creators posting its gifted PR in the last WINDOW days.

Only brand_list brands take box requests in the app (someone on their side reads them). The others carry the
badge and are pitched by email, with the evidence. refresh() recomputes the lot; it's cheap and idempotent.
"""

from __future__ import annotations

from typing import Dict

TIKTOK_WINDOW_DAYS = 60
SHIPPED_WINDOW_DAYS = 90
LIST_WINDOW_DAYS = 90
SIGNALS = ("brand_list", "shipped", "tiktok")

_READY = False


def ensure_columns(cursor) -> None:
    global _READY
    if _READY:
        return
    cursor.execute("""
        ALTER TABLE pr_brands ADD COLUMN IF NOT EXISTS gifting_confirmed_at TIMESTAMPTZ;
        ALTER TABLE pr_brands ADD COLUMN IF NOT EXISTS gifting_signal VARCHAR(20);
    """)
    _READY = True


# Brand runs a list with us: live listing it submitted, a list it locked or shipped lately, or a paid plan.
BRAND_LIST_SQL = f"""
    SELECT b.id AS brand_id, NOW() AS at
    FROM pr_brands b
    JOIN opportunities o ON o.id = b.source_opportunity_id OR o.pr_brand_id = b.id
    WHERE o.status = 'live' AND (o.closes_at IS NULL OR o.closes_at > NOW())
      AND COALESCE(o.additional_notes, '') NOT LIKE '%%[scanner:%%'
    UNION ALL
    SELECT c.brand_id, COALESCE(c.shipped_at, c.locked_at, c.updated_at) AS at
    FROM brand_pr_campaigns c
    WHERE c.status IN ('locked', 'shipped')
      AND COALESCE(c.shipped_at, c.locked_at, c.updated_at) > NOW() - INTERVAL '{LIST_WINDOW_DAYS} days'
    UNION ALL
    SELECT bb.brand_id, NOW() AS at FROM brand_billing bb WHERE bb.status = 'active'
"""

SHIPPED_SQL = f"""
    SELECT a.brand_id, COALESCE(a.shipped_at, a.updated_at) AS at
    FROM brand_pr_applications a
    WHERE (a.shipped_at > NOW() - INTERVAL '{SHIPPED_WINDOW_DAYS} days')
       OR (a.status IN ('ships', 'posted') AND a.updated_at > NOW() - INTERVAL '{SHIPPED_WINDOW_DAYS} days')
"""

TIKTOK_SQL = f"""
    SELECT d.brand_id, d.last_signal_at AS at
    FROM discovered_brands d
    WHERE d.brand_id IS NOT NULL
      AND d.last_signal_at > NOW() - INTERVAL '{TIKTOK_WINDOW_DAYS} days'
      AND EXISTS (SELECT 1 FROM brand_signals s
                  WHERE s.discovered_brand_id = d.id AND s.signal_type = 'gifted')
"""


def _safe_part(cursor, conn, sql: str) -> str:
    """A source table may not exist yet (fresh DB): skip that source instead of failing the refresh."""
    try:
        cursor.execute("SAVEPOINT gifting_part")
        cursor.execute(f"SELECT 1 FROM ({sql}) x LIMIT 1")
        cursor.execute("RELEASE SAVEPOINT gifting_part")
        return sql
    except Exception as err:
        cursor.execute("ROLLBACK TO SAVEPOINT gifting_part")
        print(f"[gifting] source skipped: {err}")
        return "SELECT NULL::int AS brand_id, NULL::timestamptz AS at WHERE FALSE"


def refresh(conn) -> Dict[str, int]:
    """Recompute gifting_confirmed_at / gifting_signal for every brand. Returns counts per signal."""
    cur = conn.cursor()
    ensure_columns(cur)
    parts = {
        "brand_list": _safe_part(cur, conn, BRAND_LIST_SQL),
        "shipped": _safe_part(cur, conn, SHIPPED_SQL),
        "tiktok": _safe_part(cur, conn, TIKTOK_SQL),
    }
    cur.execute(f"""
        WITH ev AS (
            SELECT brand_id, 1 AS rank, 'brand_list' AS signal, at FROM ({parts['brand_list']}) a
            UNION ALL SELECT brand_id, 2, 'shipped', at FROM ({parts['shipped']}) s
            UNION ALL SELECT brand_id, 3, 'tiktok', at FROM ({parts['tiktok']}) t
        ),
        best AS (
            SELECT DISTINCT ON (brand_id) brand_id, signal,
                   MAX(at) OVER (PARTITION BY brand_id) AS at
            FROM ev WHERE brand_id IS NOT NULL
            ORDER BY brand_id, rank
        ),
        cleared AS (
            UPDATE pr_brands b SET gifting_confirmed_at = NULL, gifting_signal = NULL
            WHERE b.gifting_signal IS NOT NULL AND NOT EXISTS (SELECT 1 FROM best WHERE best.brand_id = b.id)
            RETURNING b.id
        )
        UPDATE pr_brands b
        SET gifting_signal = best.signal, gifting_confirmed_at = COALESCE(best.at, NOW())
        FROM best
        WHERE b.id = best.brand_id
          AND (b.gifting_signal IS DISTINCT FROM best.signal
               OR b.gifting_confirmed_at IS DISTINCT FROM COALESCE(best.at, NOW()))
    """)
    cur.execute("""SELECT gifting_signal, COUNT(*) FROM pr_brands
                   WHERE gifting_signal IS NOT NULL AND COALESCE(status, 'published') = 'published'
                   GROUP BY 1""")
    counts = {s: 0 for s in SIGNALS}
    counts.update({str(k): int(v) for k, v in cur.fetchall()})
    conn.commit()
    return counts


def takes_box_requests(cursor, brand_id: int) -> bool:
    """A box request only makes sense where the brand reads them: a list it runs with us."""
    cursor.execute("""
        SELECT 1 FROM pr_brands b
        WHERE b.id = %s AND (
            b.gifting_signal = 'brand_list'
            OR EXISTS (SELECT 1 FROM brand_pr_campaigns c JOIN opportunities o ON o.id = b.source_opportunity_id
                       WHERE c.brand_id = b.id AND c.status = 'active' AND o.status = 'live')
            OR EXISTS (SELECT 1 FROM brand_pr_campaigns c
                       WHERE c.brand_id = b.id AND c.creator_spotlighted_at IS NOT NULL
                         AND c.status IN ('active', 'locked', 'shipped'))
        )
    """, (brand_id,))
    return cursor.fetchone() is not None


def hide_unread_requests(conn) -> int:
    """'In review' requests to brands that never read them carry no meaning: take them off creators' lists.
    'hidden' is the existing off-list status every reader already skips."""
    cur = conn.cursor()
    ensure_columns(cur)
    cur.execute("""
        UPDATE brand_pr_applications a SET status = 'hidden', updated_at = NOW()
        FROM pr_brands b
        WHERE b.id = a.brand_id AND COALESCE(a.status, 'review') = 'review'
          AND COALESCE(b.gifting_signal, '') <> 'brand_list'
          AND NOT EXISTS (SELECT 1 FROM brand_pr_campaigns c JOIN opportunities o ON o.id = b.source_opportunity_id
                          WHERE c.brand_id = b.id AND c.status = 'active' AND o.status = 'live')
          AND NOT EXISTS (SELECT 1 FROM brand_pr_campaigns c
                          WHERE c.brand_id = b.id AND c.creator_spotlighted_at IS NOT NULL
                            AND c.status IN ('active', 'locked', 'shipped'))
    """)
    n = cur.rowcount
    conn.commit()
    return n
