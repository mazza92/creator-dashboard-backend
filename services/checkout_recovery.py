"""Abandoned Pro checkout: short sessions with a Stripe recovery link, then one email."""

from __future__ import annotations

import time
from datetime import timedelta
from typing import Any, Callable, Dict, Optional

from psycopg2.extras import RealDictCursor

SESSION_LIFETIME = timedelta(hours=2)
RECOVERY_EMAIL_EVERY = timedelta(days=7)
KIND = "checkout_recovery"


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

    subject = "Your Pro upgrade didn't finish"
    lines = [
        "You started upgrading to Pro, but checkout didn't go through.",
        "If your card was declined, a different card or Link usually works. It takes a minute.",
        "With Pro, Polly pitches brands from your Gmail and follows up on day 4, with no monthly pitch cap.",
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
