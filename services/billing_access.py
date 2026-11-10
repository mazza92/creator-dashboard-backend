"""Pro access follows payment: no paid invoice, no Pro.

Why (Nov 2026 audit): 10 creators were `past_due` and still had full Pro (Autopilot sending, unlimited unlocks),
5 of them for over a month, and trials whose first charge failed kept Pro too. Every Pro check in the app reads
``creators.subscription_tier`` / ``unlocks_tier``, so access is enforced there, in one place:

- a trial whose first charge fails never paid: Pro switches off at once (no grace);
- a paying member whose renewal fails keeps Pro for GRACE (3 days, like Spotify/Netflix) while Stripe retries
  and we email them, then Pro switches off. Their subscription stays, so paying restores Pro on the spot;
- Stripe is the source of truth: every hour we compare each Pro creator with their Stripe subscription, which
  also catches missed webhooks (3 "active" creators whose subscription had already ended).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional

from psycopg2.extras import RealDictCursor

GRACE = timedelta(days=3)
LIVE = ("active", "trialing")
ENDED = ("canceled", "unpaid", "incomplete_expired", "incomplete")

_READY = False


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _aware(dt):
    if dt is None:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def ensure_columns(conn) -> None:
    global _READY
    if _READY:
        return
    from services.db_ddl import ensure_columns as _ensure
    cur = conn.cursor()
    _ensure(cur, """ALTER TABLE creators ADD COLUMN IF NOT EXISTS past_due_since TIMESTAMPTZ,
                    ADD COLUMN IF NOT EXISTS pro_locked_at TIMESTAMPTZ,
                    ADD COLUMN IF NOT EXISTS pro_locked_reason TEXT""")
    conn.commit()
    _READY = True


def _get(obj, key, default=None):
    if obj is None:
        return default
    try:
        return obj.get(key, default)
    except AttributeError:
        return getattr(obj, key, default)


def has_paid(stripe_client, subscription_id: Optional[str]) -> bool:
    """True once the subscription has at least one invoice actually paid with money (not a $0 trial invoice)."""
    if not subscription_id or stripe_client is None:
        return False
    try:
        invoices = stripe_client.Invoice.list(subscription=subscription_id, status="paid", limit=10)
    except Exception as err:
        print(f"[billing] invoice lookup failed for {subscription_id}: {err}")
        return True  # can't tell: treat as a paying member, so the grace period applies rather than a hard lock
    for inv in _get(invoices, "data", []) or []:
        if int(_get(inv, "amount_paid", 0) or 0) > 0:
            return True
    return False


def lock(conn, creator_id: int, reason: str, status: Optional[str] = None) -> None:
    """Switch Pro off. The Stripe subscription is left alone so a successful payment brings Pro back."""
    cur = conn.cursor()
    cur.execute(
        """UPDATE creators SET subscription_tier = 'free', unlocks_tier = 'free',
                  subscription_status = COALESCE(%s, subscription_status),
                  pro_locked_at = COALESCE(pro_locked_at, NOW()), pro_locked_reason = %s
           WHERE id = %s""",
        (status, reason, creator_id),
    )
    conn.commit()
    print(f"[billing] Pro locked for creator {creator_id}: {reason}")


def restore(conn, creator_id: int, status: str = "active", ends_at: Optional[datetime] = None) -> bool:
    """Payment went through (or the subscription is live again): Pro back on. True when access changed."""
    cur = conn.cursor()
    cur.execute(
        """UPDATE creators SET subscription_tier = 'pro', subscription_status = %s,
                  subscription_ends_at = COALESCE(%s, subscription_ends_at),
                  past_due_since = NULL, pro_locked_at = NULL, pro_locked_reason = NULL
           WHERE id = %s""",
        (status, ends_at, creator_id),
    )
    conn.commit()
    return True


def mark_past_due(conn, creator_id: int, now: Optional[datetime] = None) -> datetime:
    """Start the grace clock once; return when it started."""
    now = now or _now()
    cur = conn.cursor()
    cur.execute(
        """UPDATE creators SET subscription_status = 'past_due', past_due_since = COALESCE(past_due_since, %s)
           WHERE id = %s RETURNING past_due_since""",
        (now, creator_id),
    )
    row = cur.fetchone()
    conn.commit()
    since = row[0] if row else None
    return _aware(since) if isinstance(since, datetime) else now


def _unpaid_since(sub: Any, now: Optional[datetime] = None) -> datetime:
    """When the unpaid period started (the renewal that failed), so a creator past due for weeks before this
    code existed doesn't get a fresh grace period."""
    now = now or _now()
    ts = _get(sub, "current_period_start")
    if not ts:
        items = _get(_get(sub, "items") or {}, "data") or []
        ts = _get(items[0], "current_period_start") if items else None
    try:
        start = datetime.fromtimestamp(int(ts), tz=timezone.utc) if ts else None
    except (TypeError, ValueError):
        start = None
    return min(start, now) if start else now


def decide_past_due(paid_before: bool, past_due_since: Optional[datetime], now: Optional[datetime] = None) -> str:
    """'lock' or 'grace' for a subscription Stripe reports as past_due."""
    if not paid_before:
        return "lock"  # a trial whose first charge failed never paid for Pro
    now = now or _now()
    since = _aware(past_due_since) or now
    return "lock" if now - since >= GRACE else "grace"


def on_payment_failed(conn, stripe_client, creator_id: int, subscription_id: Optional[str],
                      now: Optional[datetime] = None) -> str:
    """Webhook invoice.payment_failed. Returns 'locked' or 'grace'."""
    ensure_columns(conn)
    since = mark_past_due(conn, creator_id, now)
    verdict = decide_past_due(has_paid(stripe_client, subscription_id), since, now)
    if verdict == "lock":
        lock(conn, creator_id, "payment_failed", "past_due")
        return "locked"
    return "grace"


def on_payment_succeeded(conn, creator_id: int, ends_at: Optional[datetime] = None) -> None:
    ensure_columns(conn)
    restore(conn, creator_id, "active", ends_at)


def apply_status(conn, stripe_client, creator: Dict[str, Any], sub: Any, now: Optional[datetime] = None) -> str:
    """Bring one creator in line with their Stripe subscription. Returns the action taken."""
    from services.subscription_retention import period_end_datetime

    cid = creator["id"]
    status = _get(sub, "status") or "canceled"
    ends_at = period_end_datetime(sub) if sub is not None else None
    is_pro = (creator.get("subscription_tier") or "").lower() == "pro" or creator.get("unlocks_tier") == "pro"
    if status in LIVE:
        if _get(sub, "pause_collection") or creator.get("subscription_status") == "paused":
            return "paused"  # a retention pause switches Pro off on purpose; retention_context owns it
        if not is_pro or creator.get("pro_locked_at") or creator.get("subscription_status") != status:
            restore(conn, cid, status, ends_at)
            return "restored" if not is_pro else "synced"
        return "ok"
    if status == "past_due":
        since = creator.get("past_due_since") or mark_past_due(conn, cid, _unpaid_since(sub, now))
        if decide_past_due(has_paid(stripe_client, _get(sub, "id")), since, now) == "lock":
            if is_pro:
                lock(conn, cid, "payment_failed", "past_due")
                return "locked"
            return "ok"
        return "grace"
    if is_pro:
        lock(conn, cid, f"subscription_{status}", status)
        cur = conn.cursor()
        cur.execute("UPDATE creators SET subscription_ends_at = COALESCE(subscription_ends_at, NOW()) WHERE id = %s",
                    (cid,))
        conn.commit()
        return "locked"
    return "ok"


def reconcile(conn, stripe_client, limit: int = 200, now: Optional[datetime] = None,
              dry_run: bool = False) -> Dict[str, Any]:
    """Hourly: every creator who has Pro, or who is past due, checked against Stripe."""
    ensure_columns(conn)
    cur = conn.cursor(cursor_factory=RealDictCursor)
    cur.execute(
        """SELECT id, username, subscription_tier, unlocks_tier, subscription_status, stripe_subscription_id,
                  past_due_since, pro_locked_at
           FROM creators
           WHERE stripe_subscription_id IS NOT NULL
             AND (LOWER(COALESCE(subscription_tier, '')) = 'pro' OR unlocks_tier = 'pro'
                  OR subscription_status IN ('past_due', 'trialing', 'active'))
           ORDER BY id LIMIT %s""",
        (limit,),
    )
    rows = [dict(r) for r in cur.fetchall()]
    out: Dict[str, Any] = {"checked": 0, "actions": {}, "changed": []}
    for row in rows:
        try:
            sub = stripe_client.Subscription.retrieve(row["stripe_subscription_id"])
        except Exception as err:
            msg = str(err).lower()
            if "no such subscription" in msg:
                sub = {"id": row["stripe_subscription_id"], "status": "canceled"}
            else:
                print(f"[billing] could not load {row['stripe_subscription_id']}: {err}")
                continue
        out["checked"] += 1
        if dry_run:
            status = _get(sub, "status")
            action = "would_check:" + str(status)
        else:
            action = apply_status(conn, stripe_client, row, sub, now)
        out["actions"][action] = out["actions"].get(action, 0) + 1
        if action in ("locked", "restored"):
            out["changed"].append({"creator_id": row["id"], "action": action})
    return out


def access_state(conn, creator_id: int) -> Dict[str, Any]:
    """What the app shows: is Pro on, is a payment needed, when grace ends."""
    ensure_columns(conn)
    cur = conn.cursor(cursor_factory=RealDictCursor)
    cur.execute("""SELECT subscription_tier, subscription_status, past_due_since, pro_locked_at, pro_locked_reason
                   FROM creators WHERE id = %s""", (creator_id,))
    row = cur.fetchone() or {}
    since = _aware(row.get("past_due_since"))
    locked = bool(row.get("pro_locked_at")) and row.get("subscription_status") == "past_due"
    payment_issue = row.get("subscription_status") == "past_due"
    return {
        "payment_issue": payment_issue,
        "locked": locked,
        "grace_ends_at": (since + GRACE).isoformat() if (since and payment_issue and not locked) else None,
        "locked_reason": row.get("pro_locked_reason") if locked else None,
    }
