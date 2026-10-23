"""One status per brand relationship, who acts next, and what the brand is doing on Newcollab.

Direct pitches move Drafted → Sent → Replied → Shipping → Posted, or Dropped. Gifted
roster applications (only when a brand runs an open PR campaign) use their own stages.
Status comes from logged events only. Brand signals are counts across all creators,
never names.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Iterable, List, Optional

from psycopg2.extras import RealDictCursor

DROP_AFTER = timedelta(days=14)
SIGNAL_WINDOW = timedelta(days=30)
REPLY_WINDOW = timedelta(days=90)
PICK_WINDOW = "Most brands pick within 2 to 3 weeks of opening their roster."

# stage: (label, who acts next, copy). {brand} and {date} are filled in.
STAGES: Dict[str, tuple] = {
    "drafted": ("Drafted", "You", "Written, not sent yet. Send it from your email, then tap I sent it."),
    "sent": ("Sent", "Brand", "It's in their inbox. I'll check in on day 4 and day 10."),
    "replied_interested": ("Replied · interested", "You",
                           "They said yes. Reply with your address and a usage rate."),
    "replied_question": ("Replied · question", "You", "They asked something. Reply within 24 hours."),
    "replied_rejected": ("Replied · passed", "Nobody", "They passed this time. I'll line up a better fit."),
    "bounced": ("Bounced", "You", "That address bounced. Send me another inbox for {brand}."),
    "shipping": ("Shipping", "Brand ships", "Your PR is on its way. Tell me when it lands."),
    "content_due": ("Content due", "You", "Post your content{date_part}."),
    "posted": ("Posted", "Brand", "Content is live. Ask if they want to run it as an ad."),
    "dropped": ("Dropped", "Nobody", "No reply after 14 days. I'll flag their next campaign."),
    "applied": ("Applied", "Brand", "You're in {brand}'s applicant pool. They choose who gets PR."),
    "picked": ("Picked 🎉", "Brand ships", "You've been picked. {brand} has your shipping address."),
    "completed": ("Done", "Nobody", "Added to your kit."),
    "not_picked": ("Not this time", "Nobody",
                   "They picked other creators this round. I'll flag their next campaign."),
}

_EVENT_STAGE = {
    "pitch_drafted": "drafted",
    "pitch_not_sent": "drafted",
    "pitch_sent": "sent",
    "brand_replied_interested": "replied_interested",
    "brand_replied_question": "replied_question",
    "brand_replied_rejected": "replied_rejected",
    "email_bounced": "bounced",
    "pr_shipped": "shipping",
    "pr_received": "content_due",
    "content_posted": "posted",
}
_APPLY_STAGE = {"review": "applied", "ships": "picked", "posted": "completed",
                "declined": "not_picked", "skipped": "not_picked"}
_REPLY_EVENTS = ("brand_replied_interested", "brand_replied_question", "brand_replied_rejected")


def _aware(value: Any) -> Optional[datetime]:
    if not isinstance(value, datetime):
        return None
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def _date(value: Any) -> str:
    dt = _aware(value)
    return f"{dt.strftime('%b')} {dt.day}" if dt else ""


def pitch_stage(events: Iterable[Dict[str, Any]], open_tasks: Iterable[Dict[str, Any]] = (),
                now: Optional[datetime] = None) -> Optional[str]:
    """Stage from this creator's events for one brand, oldest first. None if nothing pitched."""
    stage = None
    sent_at = None
    for ev in events:
        nxt = _EVENT_STAGE.get(ev.get("event_type") or "")
        if not nxt:
            continue
        if nxt == "drafted" and stage not in (None, "drafted", "sent"):
            continue
        stage = nxt
        if nxt == "sent":
            sent_at = _aware(ev.get("occurred_at"))
    if stage == "sent":
        waiting = any(t.get("type") in ("follow_up_due", "reply_needed") for t in open_tasks)
        if not waiting and sent_at and (now or datetime.now(timezone.utc)) - sent_at > DROP_AFTER:
            return "dropped"
    return stage


def stage_view(stage: str, brand: str, due_at: Any = None) -> Dict[str, str]:
    label, who, copy = STAGES.get(stage, STAGES["sent"])
    date_part = f" by {_date(due_at)}" if due_at and _date(due_at) else ""
    if stage == "content_due" and date_part:
        label = f"Content due {_date(due_at)}"
    return {"stage": stage, "label": label, "who_next": who,
            "copy": copy.format(brand=brand or "the brand", date_part=date_part)}


def apply_stage(status: Optional[str]) -> str:
    return _APPLY_STAGE.get((status or "review").strip().lower(), "applied")


def load_brand_signals(conn, creator_id: int, brand_ids: List[int],
                       now: Optional[datetime] = None) -> Dict[int, Dict[str, Any]]:
    """Per brand: creators who pitched it lately, its last reply to anyone, this creator's kit view,
    and the applicant count when it runs an open roster."""
    ids = sorted({int(b) for b in brand_ids if b})
    if not ids:
        return {}
    now = now or datetime.now(timezone.utc)
    cur = conn.cursor(cursor_factory=RealDictCursor)
    cur.execute(
        """
        SELECT brand_id,
               COUNT(DISTINCT creator_id) FILTER (
                   WHERE event_type = 'pitch_sent' AND occurred_at > %s) AS pitched_30d,
               COUNT(DISTINCT creator_id) FILTER (
                   WHERE event_type = 'pitch_sent' AND occurred_at > %s) AS pitched_90d,
               COUNT(DISTINCT creator_id) FILTER (
                   WHERE event_type = ANY(%s) AND occurred_at > %s) AS replied_90d,
               MAX(occurred_at) FILTER (WHERE event_type = ANY(%s)) AS last_reply_at,
               MAX(occurred_at) FILTER (
                   WHERE event_type = 'portfolio_viewed' AND creator_id = %s) AS kit_viewed_at,
               MAX(occurred_at) FILTER (WHERE event_type = 'portfolio_viewed') AS last_kit_view_any_at
        FROM polly_timeline_events
        WHERE brand_id = ANY(%s)
        GROUP BY brand_id
        """,
        (now - SIGNAL_WINDOW, now - REPLY_WINDOW, list(_REPLY_EVENTS), now - REPLY_WINDOW,
         list(_REPLY_EVENTS), creator_id, ids),
    )
    out: Dict[int, Dict[str, Any]] = {bid: {} for bid in ids}
    for row in cur.fetchall():
        out[int(row["brand_id"])] = dict(row)
    cur.execute(
        """
        SELECT c.brand_id, COUNT(a.id)::int AS applicants, MAX(c.created_at) AS roster_opened_at
        FROM brand_pr_campaigns c
        LEFT JOIN brand_pr_applications a
               ON a.brand_id = c.brand_id AND COALESCE(a.status, 'review') <> 'hidden'
        WHERE c.status = 'active' AND c.brand_id = ANY(%s)
        GROUP BY c.brand_id
        """,
        (ids,),
    )
    for row in cur.fetchall():
        out.setdefault(int(row["brand_id"]), {}).update(
            applicants=row["applicants"], roster_opened_at=row["roster_opened_at"], roster_open=True,
        )
    return out


def _ago(value: Any, now: datetime) -> str:
    dt = _aware(value)
    if not dt:
        return ""
    days = (now - dt).days
    if days <= 0:
        return "today"
    if days == 1:
        return "yesterday"
    return f"{days} days ago"


def signal_lines(sig: Dict[str, Any], stage: Optional[str] = None,
                 now: Optional[datetime] = None) -> List[str]:
    """Short, creator-facing lines. Counts only."""
    now = now or datetime.now(timezone.utc)
    lines: List[str] = []
    if sig.get("kit_viewed_at"):
        lines.append(f"Opened your kit {_ago(sig['kit_viewed_at'], now)}")
    pitched = int(sig.get("pitched_30d") or 0)
    if sig.get("roster_open") and stage in ("applied", "picked", "not_picked", "completed"):
        n = int(sig.get("applicants") or 0)
        lines.append(f"1 of {n} creators who applied" if n else "Roster open")
    elif pitched >= 2:
        lines.append(f"Pitched by {pitched} creators in the last 30 days")
    if sig.get("last_reply_at"):
        lines.append(f"Brand last replied to a creator {_ago(sig['last_reply_at'], now)}")
    elif int(sig.get("pitched_90d") or 0) >= 3:
        lines.append("No replies to creators on Newcollab yet")
    return lines


def relationship_status(stage: Optional[str], brand: str, sig: Optional[Dict[str, Any]] = None,
                        due_at: Any = None, now: Optional[datetime] = None) -> Dict[str, Any]:
    """Everything a card or Polly needs to say where this brand stands."""
    view = stage_view(stage or "sent", brand, due_at)
    sig = sig or {}
    view["signals"] = signal_lines(sig, stage, now)
    if stage == "applied":
        view["timing"] = PICK_WINDOW
    for key in ("pitched_30d", "replied_90d", "pitched_90d", "applicants"):
        if sig.get(key) is not None:
            view[key] = int(sig[key] or 0)
    for key in ("last_reply_at", "kit_viewed_at"):
        if sig.get(key):
            view[key] = _aware(sig[key]).isoformat()
    return view
