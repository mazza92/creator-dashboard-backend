"""Pro starts counting when Gmail connects: nothing can be sent before that, so days lost are given back.

On a creator's first Gmail connection for a subscription, the days between the subscription start and the
connection (after one grace day) are returned once:
  trialing  -> trial_end moves later by those days (up to MAX_TRIAL_DAYS)
  active    -> an account credit for those days (up to MAX_CREDIT_DAYS) on the next invoice
Off unless PRO_GMAIL_CLOCK=1, because it changes Stripe billing.
"""
from __future__ import annotations

import math
import os
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from psycopg2.extras import RealDictCursor

GRACE_DAYS = 1
MAX_TRIAL_DAYS = 7
MAX_CREDIT_DAYS = 30


def enabled() -> bool:
    return os.getenv("PRO_GMAIL_CLOCK") == "1"


def ensure_table(conn) -> None:
    cur = conn.cursor()
    cur.execute("""CREATE TABLE IF NOT EXISTS pro_gmail_clock (
                     creator_id INTEGER NOT NULL, subscription_id TEXT NOT NULL, days INTEGER NOT NULL,
                     kind TEXT NOT NULL, amount_cents INTEGER, created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                     PRIMARY KEY (creator_id, subscription_id))""")
    conn.commit()


def missed_days(start_ts: int, now: datetime) -> int:
    started = datetime.fromtimestamp(int(start_ts), tz=timezone.utc)
    return max(0, math.floor((now - started).total_seconds() / 86400) - GRACE_DAYS)


def on_gmail_connected(conn, creator_id: int, stripe_mod=None, now: Optional[datetime] = None,
                       note_fn=None) -> Dict[str, Any]:
    if not enabled():
        return {"skipped": "off"}
    now = now or datetime.now(timezone.utc)
    cur = conn.cursor(cursor_factory=RealDictCursor)
    cur.execute("SELECT stripe_subscription_id FROM creators WHERE id = %s", (creator_id,))
    sub_id = (cur.fetchone() or {}).get("stripe_subscription_id")
    if not sub_id:
        return {"skipped": "no_subscription"}
    ensure_table(conn)
    cur.execute("SELECT 1 FROM pro_gmail_clock WHERE creator_id = %s AND subscription_id = %s", (creator_id, sub_id))
    if cur.fetchone():
        return {"skipped": "already_done"}
    if stripe_mod is None:
        import stripe as stripe_mod
        stripe_mod.api_key = stripe_mod.api_key or os.getenv("STRIPE_SECRET_KEY")
    sub = stripe_mod.Subscription.retrieve(sub_id)
    days = missed_days(sub["start_date"], now)
    out: Dict[str, Any] = {"days": days, "status": sub["status"]}
    kind, amount = "none", None
    if days and sub["status"] == "trialing" and sub.get("trial_end"):
        days = min(days, MAX_TRIAL_DAYS)
        stripe_mod.Subscription.modify(sub_id, trial_end=int(sub["trial_end"]) + days * 86400,
                                       proration_behavior="none")
        kind = "trial_extended"
    elif days and sub["status"] == "active":
        days = min(days, MAX_CREDIT_DAYS)
        item = sub["items"]["data"][0]
        price = item["price"]
        unit = int(price.get("unit_amount") or 0)
        period = 365 if (price.get("recurring") or {}).get("interval") == "year" else 30
        amount = round(unit * days / period)
        if amount > 0:
            stripe_mod.Customer.create_balance_transaction(
                sub["customer"], amount=-amount, currency=price.get("currency") or "usd",
                description=f"Newcollab Pro: {days} days before Gmail was connected")
            kind = "credited"
    if kind == "none":
        days = 0
    cur.execute("INSERT INTO pro_gmail_clock (creator_id, subscription_id, days, kind, amount_cents) "
                "VALUES (%s, %s, %s, %s, %s) ON CONFLICT DO NOTHING", (creator_id, sub_id, days, kind, amount))
    conn.commit()
    if kind != "none":
        line = (f"Gmail's connected. I couldn't send anything before that, so I added **{days} day"
                f"{'' if days == 1 else 's'}** to your Pro {'trial' if kind == 'trial_extended' else 'month'}.")
        try:
            if note_fn is None:
                from services.polly_autopilot import post_thread_note as note_fn
            note_fn(conn, creator_id, line, [])
        except Exception as err:
            conn.rollback()
            print(f"[pro-gmail-clock] note skipped: {err}")
    return {**out, "kind": kind, "days_given": days, "amount_cents": amount}
