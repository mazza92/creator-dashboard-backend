"""Paid pack credits: $9 buys 3 extra email+pitch unlocks."""

from psycopg2.extras import RealDictCursor

PACK_BUNDLE_SIZE = 3
PACK_BUNDLE_CENTS = 900
PACK_PRODUCT = 'pack_bundle_3'

_SCHEMA_READY = False


def ensure_pack_credits_schema(conn) -> None:
    global _SCHEMA_READY
    if _SCHEMA_READY:
        return
    cursor = conn.cursor()
    cursor.execute(
        "ALTER TABLE creators ADD COLUMN IF NOT EXISTS pack_credits INTEGER NOT NULL DEFAULT 0"
    )
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS pack_purchases (
            id SERIAL PRIMARY KEY,
            creator_id INTEGER NOT NULL,
            stripe_session_id TEXT NOT NULL UNIQUE,
            packs INTEGER NOT NULL DEFAULT 3,
            amount_cents INTEGER NOT NULL DEFAULT 900,
            created_at TIMESTAMPTZ DEFAULT NOW()
        )
        """
    )
    conn.commit()
    cursor.close()
    _SCHEMA_READY = True


def pack_credits_of(creator_row) -> int:
    if not creator_row:
        return 0
    try:
        return max(0, int(creator_row.get('pack_credits') or 0))
    except (TypeError, ValueError):
        return 0


def total_unlocks_left(free_remaining, pack_credits) -> int:
    try:
        free_n = max(0, int(free_remaining or 0))
    except (TypeError, ValueError):
        free_n = 0
    try:
        pack_n = max(0, int(pack_credits or 0))
    except (TypeError, ValueError):
        pack_n = 0
    return free_n + pack_n


def grant_pack_bundle(conn, creator_id: int, stripe_session_id: str) -> dict:
    """
    Credit 3 pack unlocks once per Stripe session.
    Returns {granted, packs, pack_credits, already}.
    """
    ensure_pack_credits_schema(conn)
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    try:
        cursor.execute(
            """
            INSERT INTO pack_purchases (creator_id, stripe_session_id, packs, amount_cents)
            VALUES (%s, %s, %s, %s)
            ON CONFLICT (stripe_session_id) DO NOTHING
            RETURNING id
            """,
            (creator_id, stripe_session_id, PACK_BUNDLE_SIZE, PACK_BUNDLE_CENTS),
        )
        inserted = cursor.fetchone()
        if not inserted:
            cursor.execute(
                "SELECT pack_credits FROM creators WHERE id = %s",
                (creator_id,),
            )
            row = cursor.fetchone() or {}
            conn.commit()
            return {
                'granted': False,
                'already': True,
                'packs': PACK_BUNDLE_SIZE,
                'pack_credits': pack_credits_of(row),
            }

        cursor.execute(
            """
            UPDATE creators
            SET pack_credits = COALESCE(pack_credits, 0) + %s
            WHERE id = %s
            RETURNING pack_credits
            """,
            (PACK_BUNDLE_SIZE, creator_id),
        )
        row = cursor.fetchone() or {}
        conn.commit()
        return {
            'granted': True,
            'already': False,
            'packs': PACK_BUNDLE_SIZE,
            'pack_credits': pack_credits_of(row),
        }
    except Exception:
        conn.rollback()
        raise
    finally:
        cursor.close()
