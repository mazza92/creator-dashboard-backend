"""Daily founder email: brands that said yes to a creator in the last 24 hours.

An interested reply is the moment a creator has a real deal on the line, and the best
creator-side proof we have. Free creators who just got a yes are also the warmest Pro leads.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from html import escape
from typing import Any, Callable, Dict, List, Optional

from psycopg2.extras import RealDictCursor

from services.pro_delivery_watch import DIGEST_HOUR_UTC, founder_email

KIND = "founder_wins"
WINDOW = timedelta(hours=24)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def load_wins(conn, now: datetime) -> List[Dict[str, Any]]:
    from services.pro_roster_placement import internal_emails

    cur = conn.cursor(cursor_factory=RealDictCursor)
    cur.execute(
        """
        SELECT e.creator_id, e.brand_id, e.occurred_at,
               COALESCE(b.brand_name, e.event_label) AS brand_name,
               u.first_name,
               (c.unlocks_tier = 'pro' OR LOWER(COALESCE(c.subscription_tier, '')) = 'pro') AS is_pro,
               EXISTS (SELECT 1 FROM polly_timeline_events r
                       WHERE r.creator_id = e.creator_id AND r.brand_id IS NOT DISTINCT FROM e.brand_id
                         AND r.event_type = 'creator_replied' AND r.occurred_at >= e.occurred_at) AS replied
        FROM polly_timeline_events e
        JOIN creators c ON c.id = e.creator_id
        JOIN users u ON u.id = c.user_id
        LEFT JOIN pr_brands b ON b.id = e.brand_id
        WHERE e.event_type = 'brand_replied_interested'
          AND e.occurred_at > %s AND e.occurred_at <= %s
          AND LOWER(COALESCE(u.email, '')) NOT LIKE '%%@newcollab.co'
          AND LOWER(COALESCE(u.email, '')) <> ALL(%s)
        ORDER BY e.occurred_at
        """,
        (now - WINDOW, now, internal_emails() or [""]),
    )
    return [dict(r) for r in cur.fetchall() or []]


def month_count(conn, now: datetime) -> int:
    cur = conn.cursor()
    cur.execute(
        """
        SELECT COUNT(DISTINCT (creator_id, brand_id)) FROM polly_timeline_events
        WHERE event_type = 'brand_replied_interested' AND occurred_at > %s
        """,
        (now - timedelta(days=30),),
    )
    row = cur.fetchone()
    return int(row[0] if row else 0)


def wins_email(rows: List[Dict[str, Any]], month: int) -> Dict[str, str]:
    from services.polly_alerts import render_email

    n = len(rows)
    subject = f"{n} brand{'s' if n != 1 else ''} said yes to a creator in the last 24h"
    free = sum(1 for r in rows if not r.get("is_pro"))
    lines = [f"<strong>{month}</strong> creator-brand yeses in the last 30 days."]
    if free:
        lines.append(f"<strong>{free}</strong> of today's are free creators, your warmest Pro leads.")
    for r in rows:
        first = (r.get("first_name") or "").strip().split(" ")[0]
        who = f"#{r['creator_id']}" + (f" {escape(first)}" if first else "")
        bits = [who, "Pro" if r.get("is_pro") else "free",
                f"<strong>{escape(str(r.get('brand_name') or 'a brand'))}</strong>",
                "replied" if r.get("replied") else "no reply logged yet"]
        lines.append(" · ".join(bits))
    return {"subject": subject, "html": render_email("team", subject, lines, [])}


def email_founder_wins(conn, dry_run: bool = False, send_fn: Optional[Callable] = None,
                       test_email: str = "", now: Optional[datetime] = None, **_: Any) -> List[Dict[str, Any]]:
    """Cron: once a day after DIGEST_HOUR_UTC, only when at least one brand said yes."""
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
    rows = load_wins(conn, now)
    if not rows:
        return []
    mail = wins_email(rows, month_count(conn, now))
    to = test_email or founder_email()
    item = {"creator_id": 0, "kind": KIND, "subject": mail["subject"], "to": to, "wins": len(rows)}
    if dry_run:
        return [item]
    result = (send_fn or _default_send)(to, mail["subject"], mail["html"], "", KIND)
    _log_email(conn, 0, KIND, None, ref, mail["subject"], result)
    item["ok"] = bool(result.get("success"))
    return [item]
