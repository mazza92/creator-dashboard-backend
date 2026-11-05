"""What Polly did and what happened since the creator last looked: the proof that outreach runs without them.

Feeds the "While you were away" card (a structured activity feed instead of a list of to-dos) and the live
in-app toast while Polly is open. No emails come from here.

Sources, all real:
- polly_autopilot_queue: pitches sent from the creator's Gmail (Autopilot or the batch card), follow-ups Polly
  sent and the ones it will send next, each with a link to the Gmail thread;
- polly_timeline_events: brands opening the creator's media kit (tracked ?ref= link) and replies they logged;
- the tracker's follow_up_due tasks and unsent drafts, only where Polly isn't already handling it.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional
from urllib.parse import quote

MAX_ITEMS = 6
UPCOMING_WINDOW = timedelta(hours=48)
DEFAULT_LOOKBACK = timedelta(days=7)
REPLY_EVENTS = ("brand_replied_interested", "brand_replied_question", "brand_replied_rejected")


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _aware(ts):
    if ts is None:
        return None
    if isinstance(ts, str):
        try:
            ts = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        except ValueError:
            return None
    return ts.replace(tzinfo=timezone.utc) if ts.tzinfo is None else ts


def _iso(ts) -> Optional[str]:
    ts = _aware(ts)
    return ts.isoformat() if ts else None


def gmail_thread_url(account_email: str, thread_id: str) -> Optional[str]:
    """Opens the exact email in the creator's Gmail (the right account when they have several)."""
    if not thread_id:
        return None
    who = f"?authuser={quote(account_email)}" if account_email else ""
    return f"https://mail.google.com/mail/{who}#all/{quote(str(thread_id))}"


def _cursor(conn):
    from psycopg2.extras import RealDictCursor
    return conn.cursor(cursor_factory=RealDictCursor)


def _rows(conn, sql: str, params) -> List[Dict[str, Any]]:
    try:
        cur = _cursor(conn)
        cur.execute(sql, params)
        return [dict(r) for r in cur.fetchall()]
    except Exception as err:
        print(f"[Polly activity] query skipped: {err}")
        try:
            conn.rollback()
        except Exception:
            pass
        return []


def _gmail_email(conn, creator_id: int) -> str:
    rows = _rows(conn, "SELECT email FROM polly_gmail_accounts WHERE creator_id = %s AND revoked_at IS NULL",
                 (creator_id,))
    return (rows[0].get("email") or "") if rows else ""


def handled_brand_ids(conn, creator_id: int) -> set:
    """Brands whose follow-ups Polly sends itself: never nag the creator to follow up by hand."""
    rows = _rows(conn, """SELECT brand_id FROM polly_autopilot_queue
                          WHERE creator_id = %s AND followup_status IN ('scheduled', 'headsup')""", (creator_id,))
    return {r["brand_id"] for r in rows if r.get("brand_id")}


def collect(conn, creator_id: int, since=None, tracker: Optional[Dict[str, Any]] = None,
            drafts: Optional[List[Dict[str, Any]]] = None, now: Optional[datetime] = None,
            include_todo: bool = True) -> Dict[str, Any]:
    now = now or _now()
    since = _aware(since) or (now - DEFAULT_LOOKBACK)
    email = _gmail_email(conn, creator_id)
    items: List[Dict[str, Any]] = []

    for r in _rows(conn, """
            SELECT DISTINCT ON (e.brand_id) e.brand_id, e.event_type, e.occurred_at,
                   COALESCE(e.event_data->>'brand_name', b.brand_name) AS brand_name
            FROM polly_timeline_events e LEFT JOIN pr_brands b ON b.id = e.brand_id
            WHERE e.creator_id = %s AND e.event_type = ANY(%s) AND e.occurred_at > %s
            ORDER BY e.brand_id, e.occurred_at DESC""", (creator_id, list(REPLY_EVENTS), since)):
        items.append({"type": "reply", "brand_id": r.get("brand_id"), "brand_name": r.get("brand_name"),
                      "at": _iso(r.get("occurred_at")),
                      "text": "replied to your pitch" if r["event_type"] != "brand_replied_rejected"
                      else "answered: not this time"})

    views = _rows(conn, """
            SELECT e.brand_id, MAX(e.occurred_at) AS at, COUNT(*) AS n,
                   MAX(COALESCE(e.event_data->>'brand_name', b.brand_name)) AS brand_name,
                   MAX(q.sent_at) AS pitched_at
            FROM polly_timeline_events e
            LEFT JOIN pr_brands b ON b.id = e.brand_id
            LEFT JOIN polly_autopilot_queue q ON q.creator_id = e.creator_id AND q.brand_id = e.brand_id
            WHERE e.creator_id = %s AND e.event_type = 'portfolio_viewed' AND e.occurred_at > %s
            GROUP BY e.brand_id""", (creator_id, since))
    for r in views:
        n = int(r.get("n") or 1)
        text = "opened your media kit" + (f" {n} times" if n > 1 else "")
        at, pitched = _aware(r.get("at")), _aware(r.get("pitched_at"))
        if at and pitched and at > pitched:
            hours = int((at - pitched).total_seconds() // 3600)
            text += (f", {hours} hours after my pitch" if 1 < hours < 72 else "")
        items.append({"type": "kit_view", "brand_id": r.get("brand_id"), "brand_name": r.get("brand_name"),
                      "at": _iso(r.get("at")), "text": text})

    queue = _rows(conn, """
            SELECT brand_id, brand_name, sent_at, thread_id, followup_status, followup_due_at, followup_sent_at,
                   followups_sent
            FROM polly_autopilot_queue
            WHERE creator_id = %s AND status = 'sent'
              AND (sent_at > %s OR followup_sent_at > %s
                   OR (followup_status IN ('scheduled', 'headsup') AND followup_due_at <= %s))""",
                 (creator_id, since, since, now + UPCOMING_WINDOW))
    sent = [q for q in queue if _aware(q.get("sent_at")) and _aware(q["sent_at"]) > since]
    for q in queue:
        fu_at = _aware(q.get("followup_sent_at"))
        if fu_at and fu_at > since:
            wave = int(q.get("followups_sent") or 1)
            items.append({"type": "followup_sent", "brand_id": q.get("brand_id"), "brand_name": q.get("brand_name"),
                          "at": _iso(fu_at), "text": f"I followed up (day {4 if wave <= 1 else 10}), same thread",
                          "gmail_url": gmail_thread_url(email, q.get("thread_id"))})
    if sent:
        sent.sort(key=lambda q: _aware(q["sent_at"]), reverse=True)
        names = [q.get("brand_name") for q in sent if q.get("brand_name")]
        if len(sent) == 1:
            q = sent[0]
            items.append({"type": "sent", "brand_id": q.get("brand_id"), "brand_name": q.get("brand_name"),
                          "at": _iso(q["sent_at"]), "text": f"pitch sent from {email or 'your Gmail'}",
                          "gmail_url": gmail_thread_url(email, q.get("thread_id"))})
        else:
            shown = ", ".join(names[:3]) + (f" +{len(names) - 3}" if len(names) > 3 else "")
            items.append({"type": "sent", "brand_name": f"{len(sent)} pitches sent", "at": _iso(sent[0]["sent_at"]),
                          "text": shown, "gmail_url": f"https://mail.google.com/mail/"
                          f"{'?authuser=' + quote(email) if email else ''}#sent"})
    upcoming = [q for q in queue if q.get("followup_status") in ("scheduled", "headsup")
                and _aware(q.get("followup_due_at")) and _aware(q["followup_due_at"]) <= now + UPCOMING_WINDOW]
    upcoming.sort(key=lambda q: _aware(q["followup_due_at"]))
    for q in upcoming[:2]:
        due = _aware(q["followup_due_at"])
        when = "today" if due.date() <= now.date() else ("tomorrow" if due.date() == (now + timedelta(days=1)).date()
                                                        else due.strftime("%A"))
        items.append({"type": "next", "brand_id": q.get("brand_id"), "brand_name": q.get("brand_name"),
                      "at": _iso(due), "text": f"I follow up {when}, nothing for you to do"})

    if include_todo:
        handled = handled_brand_ids(conn, creator_id)
        for t in (tracker or {}).get("due_soon") or []:
            if (t.get("type") or "") != "follow_up_due" or not t.get("brand_name"):
                continue
            if t.get("brand_id") in handled:
                continue
            items.append({"type": "todo_followup", "brand_id": t.get("brand_id"), "brand_name": t.get("brand_name"),
                          "task_id": t.get("id"), "text": "no reply yet: a short follow-up gets most answers"})
        for d in drafts or []:
            items.append({"type": "todo_draft", "brand_id": d.get("brand_id"), "brand_name": d.get("brand_name"),
                          "text": "pitch written, not sent yet"})

    order = {"reply": 0, "kit_view": 1, "followup_sent": 2, "sent": 3, "next": 4, "todo_followup": 5, "todo_draft": 6}
    items.sort(key=lambda i: (order.get(i["type"], 9), -(_aware(i.get("at")) or now).timestamp()
                              if i["type"] not in ("next",) else (_aware(i.get("at")) or now).timestamp()))
    trimmed = items[:MAX_ITEMS]
    return {
        "items": trimmed,
        "counts": {
            "replies": sum(1 for i in items if i["type"] == "reply"),
            "kit_views": sum(1 for i in items if i["type"] == "kit_view"),
            "sent": len(sent),
            "followups": sum(1 for i in items if i["type"] == "followup_sent"),
        },
    }


def headline(activity: Dict[str, Any]) -> str:
    items = activity.get("items") or []
    first = items[0] if items else {}
    kind = first.get("type")
    name = first.get("brand_name") or "A brand"
    if kind == "reply":
        return f"🎉 **{name}** replied. Check your inbox."
    if kind == "kit_view":
        return f"👀 **{name}** opened your media kit. Brands that look usually reply."
    if kind in ("followup_sent", "sent", "next"):
        return "I kept your outreach going while you were away."
    return "A couple of things are waiting for you."


def live_events(conn, creator_id: int, since) -> List[Dict[str, Any]]:
    """New kit views and replies since the page last checked, for the in-app toast."""
    since = _aware(since) or (_now() - timedelta(minutes=10))
    out = []
    for r in _rows(conn, """
            SELECT e.event_type, e.occurred_at, e.brand_id,
                   COALESCE(e.event_data->>'brand_name', b.brand_name) AS brand_name
            FROM polly_timeline_events e LEFT JOIN pr_brands b ON b.id = e.brand_id
            WHERE e.creator_id = %s AND e.occurred_at > %s
              AND (e.event_type = 'portfolio_viewed' OR e.event_type = ANY(%s))
            ORDER BY e.occurred_at DESC LIMIT 5""", (creator_id, since, list(REPLY_EVENTS))):
        name = r.get("brand_name") or "A brand"
        text = (f"{name} just opened your media kit" if r["event_type"] == "portfolio_viewed"
                else f"{name} replied to your pitch")
        out.append({"type": "kit_view" if r["event_type"] == "portfolio_viewed" else "reply",
                    "brand_id": r.get("brand_id"), "brand_name": name, "text": text, "at": _iso(r.get("occurred_at"))})
    return out
