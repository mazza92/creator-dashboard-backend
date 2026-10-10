"""Friday Autopilot report: what Polly sent this week, who replied, and what's lined up next."""

from __future__ import annotations

from datetime import datetime
from html import escape
from typing import Any, Callable, Dict, List, Optional
from urllib.parse import urlencode

from psycopg2.extras import RealDictCursor

REPORT_WEEKDAY = 4  # Friday
REPLY_LABELS = {
    "brand_replied_interested": "said yes",
    "brand_replied_question": "asked a question",
    "brand_replied_rejected": "passed for now",
}


def _cursor(conn):
    return conn.cursor(cursor_factory=RealDictCursor)


def _plural(n: int, word: str, many: str = "") -> str:
    return f"{n} {word if n == 1 else (many or word + 's')}"


def _names(rows: List[Dict[str, Any]], limit: int = 5) -> str:
    names = [str(r.get("brand_name") or "").strip() for r in rows if r.get("brand_name")]
    shown = ", ".join(names[:limit])
    more = len(names) - limit
    return shown + (f" and {more} more" if more > 0 else "")


def load_week_facts(conn, creator_id: int, since: datetime) -> Dict[str, Any]:
    from services.polly_autopilot import REPLIED_EVENTS, month_counts, settings

    cur = _cursor(conn)
    cur.execute(
        """
        SELECT brand_name, status, sent_at, scheduled_for, followup_sent_at
        FROM polly_autopilot_queue
        WHERE creator_id = %s AND status <> 'skipped'
          AND (sent_at >= %s OR followup_sent_at >= %s OR status IN ('approved', 'draft'))
        ORDER BY COALESCE(sent_at, scheduled_for)
        """,
        (creator_id, since, since),
    )
    rows = [dict(r) for r in cur.fetchall()]
    cur.execute(
        """
        SELECT DISTINCT ON (e.brand_id) e.event_type, COALESCE(q.brand_name, b.brand_name) AS brand_name
        FROM polly_timeline_events e
        JOIN polly_autopilot_queue q ON q.creator_id = e.creator_id AND q.brand_id = e.brand_id
        LEFT JOIN pr_brands b ON b.id = e.brand_id
        WHERE e.creator_id = %s AND e.event_type = ANY(%s) AND e.occurred_at >= %s
        ORDER BY e.brand_id, e.occurred_at DESC
        """,
        (creator_id, list(REPLIED_EVENTS), since),
    )
    replies = [dict(r) for r in cur.fetchall()]
    prefs = settings(conn, creator_id)
    return {
        "sent": [r for r in rows if r.get("sent_at") and r["sent_at"] >= since],
        "followups": [r for r in rows if r.get("followup_sent_at") and r["followup_sent_at"] >= since],
        "scheduled": [r for r in rows if r["status"] == "approved"],
        "drafts": [r for r in rows if r["status"] == "draft"],
        "replies": replies,
        "month": month_counts(conn, creator_id),
        "target": prefs["monthly_target"],
        "enabled": prefs["enabled"],
    }


def has_activity(facts: Dict[str, Any]) -> bool:
    return any(facts.get(k) for k in ("sent", "followups", "scheduled", "drafts", "replies"))


def report_subject(facts: Dict[str, Any]) -> str:
    sent, replies = len(facts.get("sent") or []), len(facts.get("replies") or [])
    if replies:
        return f"Your Autopilot week: {_plural(replies, 'brand')} replied"
    if sent:
        return f"Your Autopilot week: {_plural(sent, 'brand')} pitched"
    return "Your Autopilot week: next pitches are ready"


def report_sections(facts: Dict[str, Any]) -> List[Dict[str, str]]:
    """Plain-text sections shared by the email (escaped) and the Polly note (markdown)."""
    out: List[Dict[str, str]] = []
    sent, follows = facts.get("sent") or [], facts.get("followups") or []
    replies, scheduled, drafts = facts.get("replies") or [], facts.get("scheduled") or [], facts.get("drafts") or []
    if replies:
        bits = [f"{r.get('brand_name') or 'A brand'} {REPLY_LABELS.get(r.get('event_type'), 'replied')}" for r in replies]
        out.append({"label": "Replies", "text": "; ".join(bits) + "."})
    if sent:
        out.append({"label": "Pitched", "text": f"{_plural(len(sent), 'brand')} from your Gmail: {_names(sent)}."})
    if follows:
        out.append({"label": "Followed up", "text": f"{_plural(len(follows), 'brand')} in the same thread: {_names(follows)}."})
    if scheduled:
        out.append({"label": "Next up",
                    "text": f"{_plural(len(scheduled), 'pitch', 'pitches')} scheduled: {_names(scheduled)}."})
    if drafts:
        out.append({"label": "Your OK",
                    "text": f"{_plural(len(drafts), 'pitch', 'pitches')} written and waiting for your OK."})
    month, target = facts.get("month") or {}, int(facts.get("target") or 0)
    if target:
        line = f"{month.get('sent', 0)} of {target} brands pitched"
        if month.get("replied"):
            line += f", {_plural(month['replied'], 'reply', 'replies')}"
        out.append({"label": "This month", "text": line + "."})
    return out


def next_step(facts: Dict[str, Any]) -> str:
    if any(r.get("event_type") == "brand_replied_interested" for r in facts.get("replies") or []):
        return "A brand said yes. Open Polly and I'll write the reply with your shipping details and usage rate."
    if facts.get("drafts"):
        return "Give next week's pitches a quick read and tap OK. I'll take it from there."
    if not facts.get("enabled", True):
        return "Autopilot is paused. Turn it back on and I'll pick up where I left off."
    if not facts.get("scheduled"):
        return "Next week's batch isn't lined up yet. One tap and I'll pick and write it."
    return "Nothing for you to do. I'll keep sending and following up."


def report_email(first_name: str, facts: Dict[str, Any], unsubscribe_url: str = "",
                 open_href: str = "") -> Dict[str, str]:
    from services.polly_alerts import render_email

    subject = report_subject(facts)
    lines = [
        f"<strong>{escape(s['label'])}:</strong> {escape(s['text'])}" for s in report_sections(facts)
    ]
    lines.append(escape(next_step(facts)))
    buttons = [{"label": "Open Autopilot", "href": open_href}] if open_href else []
    return {"subject": subject, "html": render_email(first_name, subject, lines, buttons, unsubscribe_url)}


def report_note(facts: Dict[str, Any]) -> str:
    body = "\n".join(f"- **{s['label']}:** {s['text']}" for s in report_sections(facts))
    return f"📊 **Your Autopilot week**\n\n{body}\n\n{next_step(facts)}"


def _open_href(kind: str) -> str:
    from services.polly_alerts import POLLY_PATH, frontend_url

    params = {"autopilot": "report", "utm_source": "email", "utm_medium": "polly_alert", "utm_campaign": kind}
    return f"{frontend_url()}{POLLY_PATH}?{urlencode(params)}"


def email_autopilot_reports(conn, limit: int = 40, dry_run: bool = False, send_fn: Optional[Callable] = None,
                            test_email: str = "", now: Optional[datetime] = None) -> List[Dict[str, Any]]:
    """Fridays: every creator with Gmail connected gets the week's Autopilot activity, in Polly and by email."""
    from services.polly_alerts import _default_send, _log_email, _recipient, _unsubscribe_url, ensure_alert_tables
    from services.polly_autopilot import ensure_autopilot_tables, post_thread_note, utc_now
    from services.polly_gmail import ensure_gmail_table
    from services.pro_manager import week_start

    now = now or utc_now()
    if now.weekday() != REPORT_WEEKDAY:
        return []
    ensure_alert_tables(conn)
    ensure_autopilot_tables(conn)
    ensure_gmail_table(conn)
    send = send_fn or _default_send
    since = week_start(now)
    ref = int(since.strftime("%Y%m%d"))
    cur = _cursor(conn)
    cur.execute(
        """
        SELECT g.creator_id FROM polly_gmail_accounts g
        WHERE g.revoked_at IS NULL
          AND NOT EXISTS (
              SELECT 1 FROM polly_email_log l
              WHERE l.creator_id = g.creator_id AND l.kind = 'autopilot_week' AND l.ref_id = %s
          )
        LIMIT %s
        """,
        (ref, limit),
    )
    out: List[Dict[str, Any]] = []
    for row in [dict(r) for r in cur.fetchall()]:
        cid = row["creator_id"]
        who = _recipient(conn, cid)
        if not who or not who.get("is_pro"):
            continue
        try:
            facts = load_week_facts(conn, cid, since)
        except Exception as err:
            print(f"[Autopilot report] {cid} skipped: {err}")
            conn.rollback()
            continue
        if not has_activity(facts):
            continue
        unsub = _unsubscribe_url(who["user_id"])
        mail = report_email(who["first"], facts, unsub, open_href=_open_href("autopilot_week"))
        item = {"creator_id": cid, "kind": "autopilot_week", "subject": mail["subject"], "to": who["email"]}
        if dry_run:
            out.append(item)
            continue
        try:
            post_thread_note(conn, cid, report_note(facts))
        except Exception as err:
            print(f"[Autopilot report] thread note skipped: {err}")
            conn.rollback()
        result = send(test_email or who["email"], mail["subject"], mail["html"], unsub, "autopilot_week")
        _log_email(conn, cid, "autopilot_week", None, ref, mail["subject"], result)
        item["ok"] = bool(result.get("success"))
        out.append(item)
    return out
