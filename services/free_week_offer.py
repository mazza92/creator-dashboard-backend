"""The free week of Pro, a few days after a creator hits the free limit and didn't buy at $19.

Sent once per creator, only while the limit is recent (so switching this on doesn't email everyone who ever
hit it), and only when checkout would actually honour the trial (quota_trial_test.trial_eligible).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from html import escape
from typing import Any, Callable, Dict, List, Optional

KIND = "free_week"
RECENT_LIMIT = timedelta(days=21)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def free_week_email(first_name: str, facts: Dict[str, Any], unsubscribe_url: str = "") -> Dict[str, str]:
    from services.polly_alerts import render_email, upgrade_link

    names = [n for n in (facts.get("brand_names") or []) if n][:2]
    waiting = int(facts.get("waiting") or 0)
    subject = "Your free week of Pro is ready"
    lines = ["You used your 3 free pitches this month. Instead of waiting for the 1st, take a week of Pro on me."]
    if names:
        shown = " and ".join(f"<strong>{escape(n)}</strong>" for n in names)
        more = waiting - len(names)
        tail = f" and {more} more brands" if more > 0 else ""
        lines.append(f"{shown}{tail} are matched to you and waiting.")
    lines += [
        "For 7 days I pitch <strong>3 new brands every weekday</strong> for you: each pitch written for that "
        "brand, sent from your Gmail, followed up on day 4 and day 10. Replies land in your own inbox.",
        "Free for 7 days, then $19/mo. Cancel before day 7 and you pay nothing.",
    ]
    buttons = [{"label": "Start my free week", "href": upgrade_link(f"{KIND}_trial")}]
    return {"subject": subject, "html": render_email(first_name, subject, lines, buttons, unsubscribe_url)}


def _candidates(conn, limit: int, now: datetime) -> List[Dict[str, Any]]:
    from services.polly_alerts import OTHER_ALERT_GAP, _cursor
    from services.quota_trial_test import LIMIT_TO_TRIAL_DAYS

    cur = _cursor(conn)
    cur.execute(
        """
        WITH hits AS (
            SELECT creator_id, MIN(at) AS hit FROM (
                SELECT creator_id, sent_at AS at FROM lifecycle_email_sends WHERE template_slug = 'max_quota_hit'
                UNION ALL
                SELECT creator_id, sent_at FROM polly_email_log WHERE kind = 'paywall_followup'
            ) x GROUP BY creator_id
        )
        SELECT h.creator_id
        FROM hits h JOIN creators c ON c.id = h.creator_id
        WHERE h.hit <= %(ready)s AND h.hit > %(recent)s
          AND COALESCE(c.unlocks_tier, 'free') <> 'pro'
          AND LOWER(COALESCE(c.subscription_tier, '')) NOT IN ('pro', 'elite')
          AND c.stripe_customer_id IS NULL AND c.stripe_subscription_id IS NULL
          AND NOT EXISTS (SELECT 1 FROM polly_email_log l WHERE l.creator_id = c.id AND l.ok
                          AND (l.kind = %(kind)s OR l.sent_at > %(gap)s))
          AND NOT EXISTS (SELECT 1 FROM lifecycle_email_sends s WHERE s.creator_id = c.id
                          AND s.status = 'sent' AND s.sent_at > %(gap)s)
        ORDER BY h.hit
        LIMIT %(limit)s
        """,
        {"ready": now - timedelta(days=LIMIT_TO_TRIAL_DAYS), "recent": now - RECENT_LIMIT, "kind": KIND,
         "gap": now - OTHER_ALERT_GAP, "limit": limit},
    )
    return [dict(r) for r in cur.fetchall()]


def email_free_week_offers(conn, limit: int = 25, dry_run: bool = False, send_fn: Optional[Callable] = None,
                           test_email: str = "") -> List[Dict[str, Any]]:
    from services import polly_alerts as pa
    from services.pro_upsell import pro_week_facts
    from services.quota_trial_test import trial_eligible

    pa.ensure_alert_tables(conn)
    send = send_fn or pa._default_send
    out = []
    for row in _candidates(conn, limit, _now()):
        cid = row["creator_id"]
        who = pa._recipient(conn, cid)
        if not who or who["is_pro"] or not trial_eligible(pa._cursor(conn), cid):
            continue
        facts = pro_week_facts(conn, cid)
        unsub = pa._unsubscribe_url(who["user_id"])
        mail = free_week_email(who["first"], {"brand_names": facts.get("brand_names"),
                                              "waiting": facts.get("matched")}, unsubscribe_url=unsub)
        item = {"creator_id": cid, "kind": KIND, "subject": mail["subject"], "to": who["email"]}
        if dry_run:
            out.append(item)
            continue
        result = send(test_email or who["email"], mail["subject"], mail["html"], unsub, KIND)
        pa._log_email(conn, cid, KIND, None, None, mail["subject"], result)
        item["ok"] = bool(result.get("success"))
        out.append(item)
    return out
