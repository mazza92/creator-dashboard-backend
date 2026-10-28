"""Abandoned Pro checkout: short sessions with a Stripe recovery link, an email, and one reminder 3 days later.

Half of all Pro payment attempts fail, mostly declines (insufficient funds, banks blocking subscriptions on
debit or prepaid cards). Most of those creators never retry, so the emails say why it usually fails and
what works (another card, Apple Pay / Google Pay, Link).
"""

from __future__ import annotations

import time
from datetime import timedelta
from typing import Any, Callable, Dict, Optional

from psycopg2.extras import RealDictCursor

SESSION_LIFETIME = timedelta(hours=2)
RECOVERY_EMAIL_EVERY = timedelta(days=7)
KIND = "checkout_recovery"
SECOND_KIND = "checkout_recovery_2"
SECOND_AFTER = timedelta(days=3)
SECOND_UNTIL = timedelta(days=10)
DECLINE_TIP = ("If your card was declined: banks often block subscriptions on debit or prepaid cards. "
               "Another card, Apple Pay, Google Pay or Link usually goes through in a minute.")


def checkout_recovery_kwargs(promo_codes: bool = True, now: Optional[float] = None) -> Dict[str, Any]:
    """No payment_method_types, so Checkout offers every method enabled in Stripe (Link, wallets)."""
    return {
        "expires_at": int((now or time.time()) + SESSION_LIFETIME.total_seconds()),
        "after_expiration": {"recovery": {"enabled": True, "allow_promotion_codes": bool(promo_codes)}},
    }


def recovery_link(session: Dict[str, Any]) -> str:
    rec = ((session or {}).get("after_expiration") or {}).get("recovery") or {}
    return rec.get("url") or ""


def recovery_email(first_name: str, finish_url: str, unsubscribe_url: str = "") -> Dict[str, str]:
    from services.polly_alerts import render_email

    from services.pro_offer import SHORT

    subject = "Your Pro upgrade didn't finish"
    lines = [
        "You started upgrading to Pro, but checkout didn't go through.",
        DECLINE_TIP,
        f"With Pro: {SHORT}, with follow-ups, while you create.",
    ]
    buttons = [{"label": "Finish upgrading", "href": finish_url}]
    return {"subject": subject, "html": render_email(first_name, subject, lines, buttons, unsubscribe_url)}


def handle_expired_session(conn, session: Dict[str, Any], send_fn: Optional[Callable] = None) -> Dict[str, Any]:
    """Email the creator once per week at most, only if they still aren't Pro."""
    from services.polly_alerts import (
        _default_send, _log_email, _recently_emailed, _recipient, _unsubscribe_url,
        ensure_alert_tables,
    )
    from services.subscription_retention import restart_pro_url

    meta = (session or {}).get("metadata") or {}
    if meta.get("tier") != "pro" or not meta.get("creator_id"):
        return {"sent": False, "why": "not_pro_checkout"}
    try:
        creator_id = int(meta["creator_id"])
    except (TypeError, ValueError):
        return {"sent": False, "why": "bad_creator"}
    ensure_alert_tables(conn)
    who = _recipient(conn, creator_id)
    if not who:
        return {"sent": False, "why": "no_recipient"}
    if who["is_pro"]:
        return {"sent": False, "why": "already_pro"}
    if _recently_emailed(conn, creator_id, [KIND], RECOVERY_EMAIL_EVERY):
        return {"sent": False, "why": "throttled"}
    cur = conn.cursor(cursor_factory=RealDictCursor)
    cur.execute(
        "SELECT subscription_status FROM creators WHERE id = %s",
        (creator_id,),
    )
    row = cur.fetchone() or {}
    if (row.get("subscription_status") or "") in ("active", "trialing"):
        return {"sent": False, "why": "already_pro"}
    finish = recovery_link(session) or restart_pro_url("checkout_recovery")
    unsub = _unsubscribe_url(who["user_id"])
    mail = recovery_email(who["first"], finish, unsub)
    result = (send_fn or _default_send)(who["email"], mail["subject"], mail["html"], unsub, KIND)
    _log_email(conn, creator_id, KIND, None, None, mail["subject"], result)
    return {"sent": bool((result or {}).get("success")), "why": "sent"}


def second_email(first_name: str, finish_url: str, unsubscribe_url: str = "") -> Dict[str, str]:
    from services.polly_alerts import render_email
    from services.pro_offer import PROMISE

    subject = "Still want Polly sending your pitches?"
    lines = [
        "Your Pro checkout didn't go through a few days ago, so nothing has gone out for you yet.",
        PROMISE,
        DECLINE_TIP,
    ]
    buttons = [{"label": "Finish upgrading", "href": finish_url}]
    return {"subject": subject, "html": render_email(first_name, subject, lines, buttons, unsubscribe_url)}


def email_second_recovery(conn, limit: int = 25, dry_run: bool = False, send_fn: Optional[Callable] = None,
                          test_email: str = "", now=None) -> list:
    """Cron: one reminder 3-10 days after the first recovery email, only if they still haven't paid."""
    from datetime import datetime, timezone

    from services.polly_alerts import _default_send, _log_email, _recipient, _unsubscribe_url, ensure_alert_tables
    from services.subscription_retention import restart_pro_url

    now = now or datetime.now(timezone.utc)
    ensure_alert_tables(conn)
    cur = conn.cursor(cursor_factory=RealDictCursor)
    cur.execute(
        """
        SELECT l.creator_id, MIN(l.sent_at) AS first_sent FROM polly_email_log l
        WHERE l.kind = %s AND l.ok
        GROUP BY l.creator_id
        HAVING MIN(l.sent_at) BETWEEN %s AND %s
           AND NOT EXISTS (SELECT 1 FROM polly_email_log s WHERE s.creator_id = l.creator_id AND s.kind = %s)
        LIMIT %s
        """,
        (KIND, now - SECOND_UNTIL, now - SECOND_AFTER, SECOND_KIND, limit),
    )
    out = []
    for row in [dict(r) for r in cur.fetchall()]:
        cid = row["creator_id"]
        cur.execute("""SELECT 1 FROM polly_usage_events WHERE creator_id = %s AND event = 'checkout_completed'
                       AND created_at > %s LIMIT 1""", (cid, row["first_sent"]))
        if cur.fetchone():
            continue
        who = _recipient(conn, cid)
        if not who or who["is_pro"]:
            continue
        unsub = _unsubscribe_url(who["user_id"])
        mail = second_email(who["first"], restart_pro_url(SECOND_KIND), unsub)
        item = {"creator_id": cid, "kind": SECOND_KIND, "subject": mail["subject"], "to": who["email"]}
        if dry_run:
            out.append(item)
            continue
        result = (send_fn or _default_send)(test_email or who["email"], mail["subject"], mail["html"], unsub, SECOND_KIND)
        _log_email(conn, cid, SECOND_KIND, None, None, mail["subject"], result)
        item["ok"] = bool((result or {}).get("success"))
        out.append(item)
    return out
