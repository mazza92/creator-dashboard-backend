# -*- coding: utf-8 -*-
"""Brand Gifted UGC billing helpers ($299/mo)."""

from __future__ import annotations

import threading
from typing import Any, Optional, Tuple

from psycopg2.extras import RealDictCursor

PLAN_GIFTED_UGC_299 = "gifted_ugc_299"
PRODUCT_META = "brand_gifted_ugc"

_SCHEMA_READY = False
_SCHEMA_LOCK = threading.Lock()


def ensure_brand_billing_schema(cursor, conn=None) -> None:
    """Idempotent schema bootstrap (mirrors roster _ensure_schema)."""
    global _SCHEMA_READY
    if _SCHEMA_READY:
        return
    with _SCHEMA_LOCK:
        if _SCHEMA_READY:
            return
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS brand_billing (
                id SERIAL PRIMARY KEY,
                brand_id INTEGER NOT NULL UNIQUE REFERENCES pr_brands(id) ON DELETE CASCADE,
                email TEXT,
                plan VARCHAR(64) NOT NULL DEFAULT 'gifted_ugc_299',
                status VARCHAR(32) NOT NULL DEFAULT 'none',
                free_campaigns_used INTEGER NOT NULL DEFAULT 0,
                stripe_customer_id VARCHAR(255),
                stripe_subscription_id VARCHAR(255),
                current_period_end TIMESTAMPTZ,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                CONSTRAINT brand_billing_status_check CHECK (
                    status IN ('none', 'trial_used', 'active', 'past_due', 'canceled')
                ),
                CONSTRAINT brand_billing_free_campaigns_check CHECK (free_campaigns_used >= 0)
            )
            """
        )
        cursor.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_brand_billing_stripe_customer
                ON brand_billing(stripe_customer_id)
                WHERE stripe_customer_id IS NOT NULL
            """
        )
        cursor.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_brand_billing_stripe_subscription
                ON brand_billing(stripe_subscription_id)
                WHERE stripe_subscription_id IS NOT NULL
            """
        )
        if conn is not None:
            conn.commit()
        _SCHEMA_READY = True


def _shipped_count(cursor, brand_id: int) -> int:
    cursor.execute(
        """
        SELECT COUNT(*)::int AS n
        FROM brand_pr_campaigns
        WHERE brand_id = %s AND status = 'shipped'
        """,
        (brand_id,),
    )
    return int((cursor.fetchone() or {}).get("n") or 0)


def get_or_create_brand_billing(cursor, brand_id: int, email: Optional[str] = None) -> dict:
    ensure_brand_billing_schema(cursor)
    cursor.execute(
        "SELECT * FROM brand_billing WHERE brand_id = %s",
        (brand_id,),
    )
    row = cursor.fetchone()
    if row:
        if email and not (row.get("email") or "").strip():
            cursor.execute(
                """
                UPDATE brand_billing
                SET email = %s, updated_at = NOW()
                WHERE brand_id = %s
                RETURNING *
                """,
                (email.strip(), brand_id),
            )
            return dict(cursor.fetchone() or row)
        return dict(row)

    cursor.execute(
        """
        INSERT INTO brand_billing (brand_id, email, plan, status)
        VALUES (%s, %s, %s, 'none')
        ON CONFLICT (brand_id) DO UPDATE
            SET email = COALESCE(NULLIF(EXCLUDED.email, ''), brand_billing.email),
                updated_at = NOW()
        RETURNING *
        """,
        (brand_id, (email or None), PLAN_GIFTED_UGC_299),
    )
    return dict(cursor.fetchone())


def effective_free_used(cursor, billing: dict, brand_id: int) -> int:
    """Free trial is consumed once any campaign has shipped (legacy brands included)."""
    shipped = _shipped_count(cursor, brand_id)
    stored = int(billing.get("free_campaigns_used") or 0)
    return max(stored, 1 if shipped > 0 else 0)


def billing_public_summary(cursor, brand_id: int) -> dict:
    billing = get_or_create_brand_billing(cursor, brand_id)
    free_used = effective_free_used(cursor, billing, brand_id)
    status = billing.get("status") or "none"
    subscribed = status == "active"
    needs_subscribe = (not subscribed) and free_used >= 1
    can_mint = subscribed or free_used < 1
    return {
        "plan": billing.get("plan") or PLAN_GIFTED_UGC_299,
        "status": status,
        "free_campaigns_used": free_used,
        "subscribed": subscribed,
        "needs_subscribe": needs_subscribe,
        "can_mint_next": can_mint,
        "current_period_end": (
            billing["current_period_end"].isoformat()
            if billing.get("current_period_end")
            else None
        ),
        "has_stripe_customer": bool(billing.get("stripe_customer_id")),
    }


def can_mint_campaign(cursor, brand_id: int) -> Tuple[bool, dict, Optional[str]]:
    summary = billing_public_summary(cursor, brand_id)
    if summary["can_mint_next"]:
        return True, summary, None
    return (
        False,
        summary,
        "First campaign used. Subscribe to Gifted UGC ($299/mo) to mint the next roster.",
    )


def mark_free_campaign_consumed(cursor, brand_id: int) -> dict:
    """Call when a roster is marked shipped — consumes the free campaign if not subscribed."""
    billing = get_or_create_brand_billing(cursor, brand_id)
    free_used = max(int(billing.get("free_campaigns_used") or 0), 1)
    status = billing.get("status") or "none"
    if status not in ("active", "past_due"):
        status = "trial_used"
    cursor.execute(
        """
        UPDATE brand_billing
        SET free_campaigns_used = GREATEST(free_campaigns_used, %s),
            status = CASE
                WHEN status IN ('active', 'past_due') THEN status
                ELSE %s
            END,
            updated_at = NOW()
        WHERE brand_id = %s
        RETURNING *
        """,
        (free_used, status, brand_id),
    )
    return dict(cursor.fetchone() or billing)


def apply_subscription_active(
    cursor,
    *,
    brand_id: int,
    customer_id: Optional[str],
    subscription_id: Optional[str],
    email: Optional[str] = None,
    current_period_end: Any = None,
    status: str = "active",
) -> dict:
    get_or_create_brand_billing(cursor, brand_id, email=email)
    cursor.execute(
        """
        UPDATE brand_billing
        SET status = %s,
            stripe_customer_id = COALESCE(%s, stripe_customer_id),
            stripe_subscription_id = COALESCE(%s, stripe_subscription_id),
            email = COALESCE(NULLIF(%s, ''), email),
            current_period_end = COALESCE(%s, current_period_end),
            updated_at = NOW()
        WHERE brand_id = %s
        RETURNING *
        """,
        (
            status,
            customer_id,
            subscription_id,
            (email or "").strip() or None,
            current_period_end,
            brand_id,
        ),
    )
    return dict(cursor.fetchone())


def apply_subscription_status_by_stripe_id(
    cursor,
    subscription_id: str,
    status: str,
    current_period_end: Any = None,
    customer_id: Optional[str] = None,
) -> Optional[dict]:
    ensure_brand_billing_schema(cursor)
    mapped = status
    if status in ("canceled", "unpaid", "incomplete_expired"):
        mapped = "canceled"
    elif status == "past_due":
        mapped = "past_due"
    elif status in ("active", "trialing"):
        mapped = "active"
    cursor.execute(
        """
        UPDATE brand_billing
        SET status = %s,
            stripe_customer_id = COALESCE(%s, stripe_customer_id),
            current_period_end = COALESCE(%s, current_period_end),
            updated_at = NOW()
        WHERE stripe_subscription_id = %s
        RETURNING *
        """,
        (mapped, customer_id, current_period_end, subscription_id),
    )
    row = cursor.fetchone()
    return dict(row) if row else None
