"""Paid pack credits: $9 buys 3 extra email+pitch unlocks."""

from psycopg2.extras import RealDictCursor

PACK_BUNDLE_SIZE = 3
PACK_BUNDLE_CENTS = 900
PACK_PRODUCT = 'pack_bundle_3'

_COLUMN_READY = None
_TABLE_READY = None


def pack_credits_column_exists(conn) -> bool:
    """Cheap catalog check. Never takes an exclusive lock on creators."""
    global _COLUMN_READY
    if _COLUMN_READY is True:
        return True
    cursor = conn.cursor()
    try:
        cursor.execute(
            """
            SELECT 1
            FROM information_schema.columns
            WHERE table_schema = 'public'
              AND table_name = 'creators'
              AND column_name = 'pack_credits'
            LIMIT 1
            """
        )
        _COLUMN_READY = cursor.fetchone() is not None
        return _COLUMN_READY
    finally:
        cursor.close()


def pack_credits_select_sql(conn) -> str:
    """Safe SELECT fragment. Uses 0 until the migration has actually landed."""
    if pack_credits_column_exists(conn):
        return "COALESCE(pack_credits, 0) AS pack_credits"
    return "0 AS pack_credits"


def ensure_pack_credits_schema(conn) -> None:
    """
    Create pack_credits + pack_purchases if missing.
    Call from rare paths (checkout), never from unlock / dashboard reads.
    ALTER TABLE on creators can wait for an ACCESS EXCLUSIVE lock and blow
    statement_timeout (~2 min), which is what froze PR package unlock.
    """
    global _COLUMN_READY, _TABLE_READY
    cursor = conn.cursor()
    try:
        if pack_credits_column_exists(conn) and _TABLE_READY:
            return

        cursor.execute(
            """
            SELECT 1
            FROM information_schema.tables
            WHERE table_schema = 'public'
              AND table_name = 'pack_purchases'
            LIMIT 1
            """
        )
        table_exists = cursor.fetchone() is not None
        if pack_credits_column_exists(conn) and table_exists:
            _TABLE_READY = True
            return

        cursor.execute("SET LOCAL lock_timeout = '1s'")
        if not pack_credits_column_exists(conn):
            cursor.execute(
                "ALTER TABLE creators ADD COLUMN IF NOT EXISTS pack_credits INTEGER NOT NULL DEFAULT 0"
            )
            _COLUMN_READY = True
        if not table_exists:
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
        _TABLE_READY = True
        _COLUMN_READY = True
    except Exception as exc:
        conn.rollback()
        print(f"[pack_credits] schema ensure skipped: {exc}")
    finally:
        cursor.close()


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
    if not pack_credits_column_exists(conn):
        raise RuntimeError('pack_credits column is not available yet')

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
