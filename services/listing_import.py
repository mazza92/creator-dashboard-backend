"""Bring old listing applicants into the brand's roster, then ask each to confirm in one tap.

Before rosters, creators applied to manually added opportunities through the listing
(opportunity_applications). Those brands now run a roster (brand_pr_applications), so their applicants
were split in two. This moves listing-only applicants onto the brand's active roster as status 'confirm':
the brand sees them, but can't pick them until the creator adds a shipping address and accepts the gifted
terms, which they do through the normal apply sheet (it turns 'confirm' into 'review').

Idempotent: runs from the hourly roster cron. The confirm email goes to every imported creator once, but
only when LISTING_CONFIRM_LIVE is set; until then a single sample goes to team@newcollab.co.
"""
from __future__ import annotations

import os
import traceback
from html import escape
from typing import Any, Callable, Dict, List, Optional

from psycopg2.extras import RealDictCursor

SOURCE = "listing_import"
TEST_TO = "team@newcollab.co"
SEND_LIMIT = 50  # per cron run

_READY = False


def ensure_columns(cursor) -> None:
    global _READY
    if _READY:
        return
    cursor.execute("ALTER TABLE brand_pr_applications ADD COLUMN IF NOT EXISTS confirm_emailed_at TIMESTAMPTZ")
    _READY = True


def live() -> bool:
    return str(os.getenv("LISTING_CONFIRM_LIVE") or "").strip().lower() in ("1", "true", "yes")


def import_listing_applicants(cursor) -> int:
    """Listing applicants missing from their brand's active roster join it as 'confirm'. Returns rows added."""
    ensure_columns(cursor)
    cursor.execute(
        """
        INSERT INTO brand_pr_applications
            (creator_id, brand_id, campaign_id, status, selected_posts, source, applied_at, updated_at)
        SELECT DISTINCT ON (oa.creator_id, o.pr_brand_id)
               oa.creator_id, o.pr_brand_id, camp.id, 'confirm', '[]'::jsonb, %s,
               COALESCE(oa.applied_at, NOW()), NOW()
        FROM opportunity_applications oa
        JOIN opportunities o ON o.id = oa.opportunity_id AND o.pr_brand_id IS NOT NULL
        JOIN LATERAL (
            SELECT c.id FROM brand_pr_campaigns c
            WHERE c.brand_id = o.pr_brand_id AND c.status = 'active'
            ORDER BY c.created_at DESC LIMIT 1
        ) camp ON TRUE
        JOIN creators cr ON cr.id = oa.creator_id
        ORDER BY oa.creator_id, o.pr_brand_id, oa.applied_at
        ON CONFLICT (creator_id, brand_id) DO NOTHING
        """,
        (SOURCE,),
    )
    return cursor.rowcount or 0


def build_confirm_email(row: Dict[str, Any]) -> Dict[str, str]:
    from services.roster_delivery import _first_name, _link, _p, _wrap

    brand = (row.get("brand_name") or "The brand").strip()
    product = (row.get("sku_note") or row.get("hero_product") or "").strip()
    subject = f"{brand} is picking creators. Confirm your spot"
    inner = "".join([
        _p(f"Hi {escape(_first_name(row.get('first_name')))},"),
        _p(f"You applied to {escape(brand)} on Newcollab. They now pick creators from their roster, and "
           "your application is on it."),
        _p(f"To be picked{' for ' + escape(product) if product else ''}, confirm your spot. It takes a minute: "
           "choose 3 posts, add your shipping address and accept the gifted terms (1 organic post and 1 raw "
           "UGC video in exchange for the product)."),
        _p("It doesn't use a credit."),
    ])
    url = _link("/creator/dashboard/pr-brands", "listing_confirm", "email", {"brand": row.get("slug") or ""})
    return {"subject": subject, "html": _wrap(subject, f"Your {brand} application needs a quick confirm.", inner, url,
                                              "Confirm my spot"), "url": url}


def _pending(cursor, limit: int) -> List[Dict[str, Any]]:
    cursor.execute(
        """
        SELECT a.id AS application_id, a.creator_id, u.email, u.first_name,
               b.brand_name, b.slug, b.hero_product, camp.sku_note
        FROM brand_pr_applications a
        JOIN creators cr ON cr.id = a.creator_id
        JOIN users u ON u.id = cr.user_id
        JOIN pr_brands b ON b.id = a.brand_id
        LEFT JOIN brand_pr_campaigns camp ON camp.id = a.campaign_id
        WHERE a.source = %s AND a.status = 'confirm' AND a.confirm_emailed_at IS NULL
          AND u.email IS NOT NULL AND TRIM(u.email) <> ''
          AND (to_jsonb(u) ->> 'unsubscribed_at') IS NULL
        ORDER BY a.applied_at
        LIMIT %s
        """,
        (SOURCE, limit),
    )
    return [dict(r) for r in cursor.fetchall()]


def send_confirm_emails(cursor, conn, *, dry_run: bool = False, send_fn: Optional[Callable] = None,
                        is_live: Optional[bool] = None) -> Dict[str, Any]:
    from services.roster_delivery import _default_send

    send = send_fn or _default_send
    is_live = live() if is_live is None else is_live
    if not is_live:
        # One sample to the team, once, so the copy can be checked before anything reaches creators.
        cursor.execute("SELECT 1 FROM brand_pr_events WHERE event = 'listing_confirm_test' LIMIT 1")
        if cursor.fetchone():
            return {"live": False, "sent": 0, "test_sent": False}
        rows = _pending(cursor, 1)
        if not rows or dry_run:
            return {"live": False, "sent": 0, "test_sent": False, "pending_sample": bool(rows)}
        mail = build_confirm_email(rows[0])
        res = send(TEST_TO, "[TEST] " + mail["subject"], mail["html"], "listing_confirm_test")
        if res.get("success"):
            cursor.execute("INSERT INTO brand_pr_events (event, source, meta) VALUES ('listing_confirm_test', 'cron', '{}')")
            conn.commit()
        return {"live": False, "sent": 0, "test_sent": bool(res.get("success"))}

    rows = _pending(cursor, SEND_LIMIT)
    sent = 0
    for row in rows:
        if dry_run:
            continue
        mail = build_confirm_email(row)
        try:
            res = send(row["email"].strip(), mail["subject"], mail["html"], "listing_confirm")
        except Exception:
            traceback.print_exc()
            continue
        if res.get("success"):
            cursor.execute("UPDATE brand_pr_applications SET confirm_emailed_at = NOW() WHERE id = %s",
                           (row["application_id"],))
            conn.commit()
            sent += 1
    return {"live": True, "sent": sent, "pending": len(rows)}


def process_listing_import(conn, *, dry_run: bool = False) -> Dict[str, Any]:
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    added = 0
    if not dry_run:
        added = import_listing_applicants(cursor)
        conn.commit()
    emails = send_confirm_emails(cursor, conn, dry_run=dry_run)
    return {"imported": added, "emails": emails}
