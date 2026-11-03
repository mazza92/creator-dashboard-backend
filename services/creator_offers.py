"""Signup offers carried by a link (?ref=polly_trial7).

The UGC supply cold email promises 7 days of Pro free. The frontend keeps the ref through signup and posts it
as signup_ref; it's stored here so checkout can honour the offer whatever email the creator signs up with.
"""
from __future__ import annotations

from typing import Optional

TRIAL_OFFERS = ("polly_trial7",)
_table_ready = False


def ensure_table(cur) -> None:
    global _table_ready
    if _table_ready:
        return
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS creator_signup_offers (
            user_id INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
            offer VARCHAR(40) NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """
    )
    _table_ready = True


def record_offer(conn, user_id: int, ref: Optional[str]) -> bool:
    """Store a known offer for a new account. Unknown refs (blog widget etc.) are ignored."""
    ref = (ref or "").strip().lower()
    if not user_id or ref not in TRIAL_OFFERS:
        return False
    cur = conn.cursor()
    ensure_table(cur)
    cur.execute(
        "INSERT INTO creator_signup_offers (user_id, offer) VALUES (%s, %s) ON CONFLICT (user_id) DO NOTHING",
        (user_id, ref),
    )
    conn.commit()
    return bool(cur.rowcount)


def has_trial_offer(cursor, creator_id: int) -> bool:
    """Signed up through a trial-offer link and never paid. The table appears with the first offer signup."""
    cursor.execute("SELECT to_regclass('public.creator_signup_offers') AS t")
    row = cursor.fetchone()
    if not row or not (row["t"] if isinstance(row, dict) else row[0]):
        return False
    cursor.execute(
        """
        SELECT 1
        FROM creators c
        JOIN creator_signup_offers o ON o.user_id = c.user_id
        WHERE c.id = %s
          AND o.offer = ANY(%s)
          AND c.stripe_subscription_id IS NULL
          AND c.stripe_customer_id IS NULL
          AND c.subscription_started_at IS NULL
        LIMIT 1
        """,
        (creator_id, list(TRIAL_OFFERS)),
    )
    return cursor.fetchone() is not None
