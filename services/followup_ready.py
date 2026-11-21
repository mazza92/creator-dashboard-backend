"""Day-4 "your follow-up is ready" email, for free creators who sent a pitch by hand.

Who gets it: free creators (Pro has Autopilot) whose pitch went out by hand (Open email / Copy), 4 to 6 days ago, with
no reply and no follow-up yet. Pitches sent through Gmail or the Newcollab relay are left out: they're in
polly_autopilot_queue and Polly sends their day-4 follow-up itself (also for free creators: origin first_pitches).

One email per creator (at most every 3 days: services.email_policy MIN_GAP), naming every brand that's due. It's
logged once, under the lead brand, so it counts as one email in the weekly budget; that brand is never emailed again. The button opens Polly on the follow-up draft. A follow-up never uses a free pitch.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from html import escape
from typing import Any, Callable, Dict, List, Optional

KIND = "followup_ready"
DUE_AFTER = timedelta(days=4)  # services.polly_autopilot.FOLLOWUP_GAPS[0]
WINDOW_END = timedelta(days=7)  # day 4 to 6: later than that it's the Monday report's job
FOLLOWED_UP_GAP = timedelta(days=3)  # services.waiting_room.FOLLOWED_UP_GAP
REPLY_EVENTS = ("brand_replied_interested", "brand_replied_question", "brand_replied_rejected")


def _now() -> datetime:
    return datetime.now(timezone.utc)


def due_manual_pitches(conn, now: Optional[datetime] = None, limit: int = 200) -> Dict[int, List[Dict[str, Any]]]:
    """{creator_id: [{brand_id, brand_name, sent_at, opened}]} for hand-sent pitches whose follow-up is due."""
    from services.polly_alerts import _cursor

    now = now or _now()
    cur = _cursor(conn)
    cur.execute(
        """
        WITH sends AS (
            SELECT e.creator_id, e.brand_id, MIN(e.occurred_at) AS sent_at, MAX(e.occurred_at) AS last_sent_at
            FROM polly_timeline_events e
            WHERE e.event_type IN ('pitch_sent', 'paid_pitch_sent') AND e.brand_id IS NOT NULL
              AND e.occurred_at > %(oldest)s
            GROUP BY 1, 2
        )
        SELECT s.creator_id, s.brand_id, s.sent_at, COALESCE(b.brand_name, 'the brand') AS brand_name,
               EXISTS (SELECT 1 FROM polly_timeline_events v WHERE v.creator_id = s.creator_id
                       AND v.brand_id = s.brand_id AND v.event_type = 'portfolio_viewed'
                       AND v.occurred_at >= s.sent_at) AS opened
        FROM sends s
        JOIN creators c ON c.id = s.creator_id
        LEFT JOIN pr_brands b ON b.id = s.brand_id
        WHERE s.sent_at <= %(due)s AND s.sent_at > %(oldest)s
          AND s.last_sent_at < s.sent_at + %(gap)s
          AND COALESCE(c.unlocks_tier, 'free') <> 'pro' AND LOWER(COALESCE(c.subscription_tier, '')) <> 'pro'
          AND NOT EXISTS (SELECT 1 FROM polly_timeline_events r WHERE r.creator_id = s.creator_id
                          AND r.brand_id = s.brand_id AND r.event_type = ANY(%(replies)s))
          AND NOT EXISTS (SELECT 1 FROM polly_autopilot_queue q WHERE q.creator_id = s.creator_id
                          AND q.brand_id = s.brand_id AND q.sent_at IS NOT NULL)
          AND NOT EXISTS (SELECT 1 FROM polly_email_log l WHERE l.creator_id = s.creator_id
                          AND l.brand_id = s.brand_id AND l.kind = %(kind)s AND l.ok)
        ORDER BY s.sent_at
        LIMIT %(limit)s
        """,
        {"due": now - DUE_AFTER, "oldest": now - WINDOW_END, "gap": FOLLOWED_UP_GAP,
         "replies": list(REPLY_EVENTS), "kind": KIND, "limit": limit},
    )
    out: Dict[int, List[Dict[str, Any]]] = {}
    for r in cur.fetchall():
        out.setdefault(int(r["creator_id"]), []).append(dict(r))
    return out


def followup_ready_email(first_name: str, brands: List[Dict[str, Any]], now: Optional[datetime] = None,
                         unsubscribe_url: str = "") -> Dict[str, str]:
    from services.polly_alerts import chip_link, render_email

    now = now or _now()
    lead = brands[0]
    name = lead.get("brand_name") or "the brand"
    sent = lead.get("sent_at")
    days = (now - sent).days if isinstance(sent, datetime) else 4
    if len(brands) == 1:
        subject = f"Your {name} follow-up is ready"
    else:
        subject = f"{len(brands)} follow-ups are ready"
    opened = [b for b in brands if b.get("opened")]
    lines = []
    if opened:
        o = opened[0]
        lines.append(f"<strong>{escape(o.get('brand_name') or 'A brand')}</strong> opened your kit after your pitch. "
                     "That's the best moment for a short follow-up.")
    else:
        lines.append(f"You pitched <strong>{escape(name)}</strong> {days} days ago and there's no reply yet, which "
                     "is normal: brand inboxes are busy. A short, friendly follow-up keeps you on their radar.")
    if len(brands) > 1:
        others = ", ".join(escape(b.get("brand_name") or "") for b in brands[1:4])
        lines.append(f"Also ready: {others}.")
    lines.append(f"I'll write {'it' if len(brands) == 1 else 'them'} for you, short and friendly. "
                 "Follow-ups never use a free pitch.")
    lines.append("Tip: connect Gmail in Polly and I'll send follow-ups like this for you, on day 4 and day 10.")
    buttons = [{"label": f"Draft my {name} follow-up",
                "href": chip_link("draft_followup", brand_id=lead.get("brand_id"), brand_name=name, kind=KIND)}]
    return {"subject": subject, "html": render_email(first_name, subject, lines, buttons, unsubscribe_url)}


def email_followups_ready(conn, limit: int = 40, dry_run: bool = False, send_fn: Optional[Callable] = None,
                          test_email: str = "", now: Optional[datetime] = None) -> List[Dict[str, Any]]:
    from services import polly_alerts as pa

    pa.ensure_alert_tables(conn)
    send = send_fn or pa._default_send
    now = now or _now()
    out: List[Dict[str, Any]] = []
    for cid, brands in list(due_manual_pitches(conn, now).items())[:limit]:
        who = pa._recipient(conn, cid, kind=KIND)
        if not who or who["is_pro"]:
            continue
        brands.sort(key=lambda b: (not b.get("opened"), b.get("sent_at")))
        unsub = pa._unsubscribe_url(who["user_id"])
        mail = followup_ready_email(who["first"], brands, now=now, unsubscribe_url=unsub)
        item = {"creator_id": cid, "kind": KIND, "subject": mail["subject"], "to": who["email"],
                "brands": [b["brand_id"] for b in brands]}
        if dry_run:
            out.append(item)
            continue
        result = send(test_email or who["email"], mail["subject"], mail["html"], unsub, KIND)
        pa._log_email(conn, cid, KIND, brands[0]["brand_id"], None, mail["subject"], result)
        item["ok"] = bool(result.get("success"))
        out.append(item)
    return out
