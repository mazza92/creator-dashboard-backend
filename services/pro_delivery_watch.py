"""Daily founder alert: paying Pro creators Autopilot hasn't sent anything for.

Pro is paid for pitches going out. A paying creator with nothing sent is a refund-shaped cancel
waiting to happen, so the founder hears about it the same day, with the reason and how close
the renewal is, while there's still time to fix it personally.
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from html import escape
from typing import Any, Callable, Dict, List, Optional

from psycopg2.extras import RealDictCursor

KIND = "founder_delivery"
DIGEST_HOUR_UTC = 9
STALL_AFTER = timedelta(days=7)
GRACE = timedelta(hours=2)
SOON = timedelta(days=3)
PAYING = ("active", "trialing", "past_due")
REASONS = {
    "no_gmail": "Gmail not connected",
    "gmail_revoked": "Gmail disconnected",
    "paused": "Autopilot paused",
    "nothing_queued": "Nothing queued",
}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _aware(dt: Optional[datetime]) -> Optional[datetime]:
    if dt is None:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def founder_email() -> str:
    from services.admin_auth import ADMIN_EMAIL

    return (os.getenv("FOUNDER_ALERT_EMAIL") or ADMIN_EMAIL).strip()


def stall_reason(row: Dict[str, Any], now: datetime) -> Optional[str]:
    """Why nothing is going out for this creator, or None when delivery is on track."""
    since = _aware(row.get("pro_since"))
    if since and now - since < GRACE:
        return None
    last_sent = _aware(row.get("last_sent"))
    if last_sent and now - last_sent < STALL_AFTER:
        return None
    if not row.get("gmail"):
        return "gmail_revoked" if row.get("revoked_at") else "no_gmail"
    if not row.get("enabled", True):
        return "paused"
    next_slot = _aware(row.get("next_slot"))
    if next_slot and next_slot - now <= SOON:
        return None
    if int(row.get("drafts") or 0) or next_slot:
        return None
    return "nothing_queued"


def stalled_creators(conn, now: Optional[datetime] = None) -> List[Dict[str, Any]]:
    from services.pro_manager import next_renewal

    now = now or _now()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    cur.execute(
        """
        SELECT c.id AS creator_id, c.subscription_status,
               c.subscription_started_at AS pro_since, c.subscription_ends_at AS pro_ends,
               COALESCE(a.enabled, TRUE) AS enabled,
               (g.creator_id IS NOT NULL AND g.revoked_at IS NULL) AS gmail, g.revoked_at,
               (SELECT MAX(q.sent_at) FROM polly_autopilot_queue q WHERE q.creator_id = c.id) AS last_sent,
               (SELECT MIN(q.scheduled_for) FROM polly_autopilot_queue q
                WHERE q.creator_id = c.id AND q.status = 'approved') AS next_slot,
               (SELECT COUNT(*) FROM polly_autopilot_queue q
                WHERE q.creator_id = c.id AND q.status IN ('draft', 'approved'))::int AS drafts,
               (SELECT COUNT(*) FROM polly_email_log l
                WHERE l.creator_id = c.id AND l.ok AND l.kind IN ('autopilot_gmail', 'trial_gmail'))::int AS reminders
        FROM creators c
        LEFT JOIN polly_autopilot a ON a.creator_id = c.id
        LEFT JOIN polly_gmail_accounts g ON g.creator_id = c.id
        WHERE (c.unlocks_tier = 'pro' OR LOWER(COALESCE(c.subscription_tier, '')) = 'pro')
          AND c.stripe_subscription_id IS NOT NULL
          AND COALESCE(c.subscription_status, 'active') IN %s
        ORDER BY c.subscription_started_at
        """,
        (PAYING,),
    )
    out = []
    for row in [dict(r) for r in cur.fetchall()]:
        reason = stall_reason(row, now)
        if not reason:
            continue
        renews = next_renewal(row.get("pro_since"), row.get("pro_ends"), now)
        since = _aware(row.get("pro_since"))
        out.append({
            "creator_id": row["creator_id"],
            "reason": reason,
            "status": row.get("subscription_status") or "active",
            "days_on_pro": (now - since).days if since else None,
            "renews_in": (renews - now).days if renews else None,
            "ever_sent": bool(row.get("last_sent")),
            "waiting": int(row.get("drafts") or 0),
            "reminders": int(row.get("reminders") or 0),
        })
    out.sort(key=lambda r: (r["renews_in"] if r["renews_in"] is not None else 999, r["creator_id"]))
    return out


def digest_email(rows: List[Dict[str, Any]], now: datetime) -> Dict[str, str]:
    from services.polly_alerts import render_email

    subject = f"{len(rows)} paying Pro creator{'s' if len(rows) != 1 else ''} with nothing sent"
    counts: Dict[str, int] = {}
    for r in rows:
        counts[r["reason"]] = counts.get(r["reason"], 0) + 1
    lines = [" · ".join(f"<strong>{n}</strong> {escape(REASONS[k])}" for k, n in counts.items())]
    for r in rows:
        renew = f"renews in {r['renews_in']}d" if r["renews_in"] is not None else "renewal unknown"
        bits = [f"#{r['creator_id']}", escape(REASONS[r["reason"]]), escape(r["status"]),
                f"{r['days_on_pro']}d on Pro" if r["days_on_pro"] is not None else "",
                renew, "never sent" if not r["ever_sent"] else "",
                f"{r['waiting']} waiting" if r["waiting"] else "",
                f"{r['reminders']} reminders"]
        lines.append(" · ".join(b for b in bits if b))
    return {"subject": subject, "html": render_email("team", subject, lines, [])}


def email_founder_delivery(conn, dry_run: bool = False, send_fn: Optional[Callable] = None,
                           test_email: str = "", now: Optional[datetime] = None, **_: Any) -> List[Dict[str, Any]]:
    """Cron: once a day after DIGEST_HOUR_UTC, the founder gets the stalled Pro list."""
    from services.polly_alerts import _default_send, _log_email, ensure_alert_tables

    now = now or _now()
    if now.hour < DIGEST_HOUR_UTC:
        return []
    ensure_alert_tables(conn)
    ref = int(now.strftime("%Y%m%d"))
    cur = conn.cursor()
    cur.execute("SELECT 1 FROM polly_email_log WHERE creator_id = 0 AND kind = %s AND ref_id = %s LIMIT 1",
                (KIND, ref))
    if cur.fetchone():
        return []
    rows = stalled_creators(conn, now)
    if not rows:
        return []
    mail = digest_email(rows, now)
    to = test_email or founder_email()
    item = {"creator_id": 0, "kind": KIND, "subject": mail["subject"], "to": to, "stalled": len(rows)}
    if dry_run:
        return [item]
    result = (send_fn or _default_send)(to, mail["subject"], mail["html"], "", KIND)
    _log_email(conn, 0, KIND, None, ref, mail["subject"], result)
    item["ok"] = bool(result.get("success"))
    return [item]
