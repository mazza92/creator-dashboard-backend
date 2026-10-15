"""Tell Pro creators when Autopilot can't send: Gmail isn't connected, or Autopilot is paused.

Pro is paid for outreach that happens without the creator watching, so a silent stall reads as
"the app doesn't work" and ends in a cancel. Polly says so in chat and by email, then reminds on
a widening schedule: Gmail every few days until it's fixed (nothing can go out without it), a
pause only a few times (it may be deliberate).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from html import escape
from typing import Any, Callable, Dict, List, Optional, Sequence

from psycopg2.extras import RealDictCursor

GMAIL_KIND = "autopilot_gmail"
PAUSED_KIND = "autopilot_paused"
# Gap before each reminder: the first counts from when sending stopped, the rest from the
# previous reminder. The last gap repeats for Gmail; paused stops after PAUSED_MAX reminders.
GMAIL_GAPS = (timedelta(days=1), timedelta(days=3), timedelta(days=7), timedelta(days=14))
PAUSED_GAPS = (timedelta(days=2), timedelta(days=7), timedelta(days=14))
PAUSED_MAX = len(PAUSED_GAPS)
OTHER_KINDS = ["trial_gmail", "trial_ending", "kit_view", "nudge", "polly_brief", "monday_board",
               "renewal_note", "autopilot_week", "paywall_followup"]


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _aware(dt: Optional[datetime]) -> Optional[datetime]:
    if dt is None:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _plural(n: int, word: str) -> str:
    return f"{n} {word if n == 1 else word + ('es' if word.endswith(('ch', 'sh', 's', 'x')) else 's')}"


def _due_after(since: Optional[datetime], warned: Sequence[datetime], gaps: Sequence[timedelta],
               now: datetime, max_count: Optional[int] = None) -> bool:
    since = _aware(since)
    sent = sorted(t for t in (_aware(w) for w in warned) if t and (since is None or t >= since))
    if max_count is not None and len(sent) >= max_count:
        return False
    anchor = sent[-1] if sent else since
    if anchor is None:
        return True
    return now - anchor >= gaps[min(len(sent), len(gaps) - 1)]


def due_kind(row: Dict[str, Any], now: datetime, gmail_available: bool = True) -> Optional[str]:
    """Which warning (if any) is due for this creator right now."""
    gmail_missing = gmail_available and not row.get("gmail") and row.get("subscription_status") != "trialing"
    if gmail_missing:
        since = row.get("revoked_at") or row.get("pro_since")
        if _due_after(since, row.get("gmail_warned") or [], GMAIL_GAPS, now):
            return GMAIL_KIND
        return None
    if not row.get("enabled", True):
        if _due_after(row.get("paused_since"), row.get("paused_warned") or [], PAUSED_GAPS, now, PAUSED_MAX):
            return PAUSED_KIND
    return None


def _date_label(dt: datetime) -> str:
    return f"{dt.strftime('%A, %b')} {dt.day}"


def _waiting_line(waiting: int) -> Optional[str]:
    if not waiting:
        return None
    return (f"<strong>{_plural(waiting, 'pitch')}</strong> {'is' if waiting == 1 else 'are'} "
            "already written and waiting to go out.")


def gmail_email(first_name: str, facts: Dict[str, Any], unsubscribe_url: str = "") -> Dict[str, str]:
    from services.polly_alerts import render_email
    from services.pro_trial import _link

    if facts.get("revoked"):
        subject = "Your Gmail disconnected, so your pitches are on hold"
        lead = ("Your Gmail got disconnected from Newcollab, so I've stopped sending. "
                "I send every pitch and follow-up from your own address, so nothing goes out until it's back.")
    else:
        subject = "I can't send your pitches until Gmail is connected"
        lead = ("You're on Pro, so I can find brands, write your pitches, send them and follow up for you. "
                "But Gmail isn't connected, and I send from your own address, so right now nothing goes out.")
    lines = [lead]
    waiting = _waiting_line(int(facts.get("waiting") or 0))
    if waiting:
        lines.append(waiting)
    lines.append("It takes 30 seconds. Once it's connected I send on your next send day and follow up on day 4.")
    if not facts.get("enabled", True):
        lines.append("Autopilot is also paused. Switch it back on in the same panel.")
    buttons = [{"label": "Connect Gmail", "href": _link("/creator/dashboard/for-you", GMAIL_KIND,
                                                        {"autopilot": "connect_gmail"})}]
    return {"subject": subject, "html": render_email(first_name, subject, lines, buttons, unsubscribe_url)}


def paused_email(first_name: str, facts: Dict[str, Any], unsubscribe_url: str = "") -> Dict[str, str]:
    from services.polly_alerts import render_email
    from services.pro_trial import _link

    subject = "Autopilot is paused, so no pitches are going out"
    since = _aware(facts.get("paused_since"))
    when = f" since <strong>{escape(_date_label(since))}</strong>" if since else ""
    lines = [f"Autopilot has been paused{when}, so I'm not sending pitches or follow-ups for you."]
    waiting = _waiting_line(int(facts.get("waiting") or 0))
    if waiting:
        lines.append(waiting)
    lines += [
        "Turn it back on and I'll pick up on your next send day. Want different brands instead? "
        "Change your niche and targets in Settings and I'll adjust.",
        "Paused on purpose? No problem, I'll only remind you a couple of times.",
    ]
    buttons = [{"label": "Turn Autopilot back on", "href": _link("/creator/dashboard/for-you", PAUSED_KIND,
                                                                 {"autopilot": "resume"})}]
    return {"subject": subject, "html": render_email(first_name, subject, lines, buttons, unsubscribe_url)}


def chat_note(kind: str, facts: Dict[str, Any]) -> Dict[str, Any]:
    waiting = int(facts.get("waiting") or 0)
    tail = f" {_plural(waiting, 'pitch')} {'is' if waiting == 1 else 'are'} written and waiting." if waiting else ""
    if kind == GMAIL_KIND:
        head = ("⚠️ Your Gmail disconnected, so I've stopped sending." if facts.get("revoked") else
                "⚠️ I can't send your pitches yet: Gmail isn't connected.")
        text = f"{head}{tail} Connect it in **Autopilot** and I'll send on your next send day."
        label = "Connect Gmail"
    else:
        text = (f"⏸️ Autopilot is paused, so no pitches or follow-ups are going out.{tail} "
                "Turn it back on in **Autopilot** and I'll pick up on your next send day.")
        label = "Turn Autopilot back on"
    return {"text": text, "chips": [{"id": "autopilot", "label": label, "action": "autopilot"}]}


def _candidates(conn, limit: int) -> List[Dict[str, Any]]:
    cur = conn.cursor(cursor_factory=RealDictCursor)
    cur.execute(
        """
        SELECT c.id AS creator_id, c.subscription_status,
               c.subscription_started_at AS pro_since,
               COALESCE(a.enabled, TRUE) AS enabled,
               CASE WHEN a.enabled IS FALSE THEN a.updated_at END AS paused_since,
               (g.creator_id IS NOT NULL AND g.revoked_at IS NULL) AS gmail,
               g.revoked_at,
               (SELECT COUNT(*) FROM polly_autopilot_queue q
                WHERE q.creator_id = c.id AND q.status IN ('draft', 'approved'))::int AS waiting,
               ARRAY(SELECT l.sent_at FROM polly_email_log l
                     WHERE l.creator_id = c.id AND l.ok AND l.kind = %s) AS gmail_warned,
               ARRAY(SELECT l.sent_at FROM polly_email_log l
                     WHERE l.creator_id = c.id AND l.ok AND l.kind = %s) AS paused_warned,
               (SELECT MAX(l.sent_at) FROM polly_email_log l
                WHERE l.creator_id = c.id AND l.ok AND l.kind IN (%s, %s)) AS last_warned
        FROM creators c
        LEFT JOIN polly_autopilot a ON a.creator_id = c.id
        LEFT JOIN polly_gmail_accounts g ON g.creator_id = c.id
        WHERE (c.unlocks_tier = 'pro' OR LOWER(COALESCE(c.subscription_tier, '')) = 'pro')
          AND (a.enabled IS FALSE OR g.creator_id IS NULL OR g.revoked_at IS NOT NULL)
        ORDER BY last_warned NULLS FIRST, c.id
        LIMIT %s
        """,
        (GMAIL_KIND, PAUSED_KIND, GMAIL_KIND, PAUSED_KIND, limit),
    )
    return [dict(r) for r in cur.fetchall()]


def email_autopilot_blocked(conn, limit: int = 40, dry_run: bool = False, send_fn: Optional[Callable] = None,
                            test_email: str = "", note_fn: Optional[Callable] = None) -> List[Dict[str, Any]]:
    """Cron: Pro creators whose Autopilot can't send get a Polly chat note and an email."""
    from services.polly_alerts import (
        OTHER_ALERT_GAP, _default_send, _log_email, _recently_emailed, _recipient, _unsubscribe_url,
        ensure_alert_tables,
    )
    from services.polly_autopilot import ensure_autopilot_tables, post_thread_note
    from services.polly_gmail import ensure_gmail_table, gmail_configured
    from services.pro_trial import _billing_recipient

    ensure_alert_tables(conn)
    ensure_autopilot_tables(conn)
    ensure_gmail_table(conn)
    send = send_fn or _default_send
    note = note_fn or post_thread_note
    gmail_available = gmail_configured()
    now = _now()
    out = []
    for row in _candidates(conn, limit):
        cid = row["creator_id"]
        kind = due_kind(row, now, gmail_available)
        if not kind or _recently_emailed(conn, cid, OTHER_KINDS, OTHER_ALERT_GAP):
            continue
        # Service notice about a paid feature that has stopped, so it reaches marketing opt-outs too.
        who = _recipient(conn, cid) or _billing_recipient(conn, cid)
        if not who:
            continue
        facts = {"waiting": row.get("waiting"), "enabled": row.get("enabled", True),
                 "revoked": bool(row.get("revoked_at")), "paused_since": row.get("paused_since")}
        unsub = _unsubscribe_url(who["user_id"])
        mail = (gmail_email if kind == GMAIL_KIND else paused_email)(who["first"], facts, unsub)
        item = {"creator_id": cid, "kind": kind, "subject": mail["subject"], "to": who["email"]}
        if dry_run:
            out.append(item)
            continue
        msg = chat_note(kind, facts)
        try:
            note(conn, cid, msg["text"], msg["chips"])
        except Exception as err:
            print(f"[Autopilot health] chat note skipped for {cid}: {err}")
            try:
                conn.rollback()
            except Exception:
                pass
        result = send(test_email or who["email"], mail["subject"], mail["html"], unsub, kind)
        _log_email(conn, cid, kind, None, None, mail["subject"], result)
        item["ok"] = bool(result.get("success"))
        out.append(item)
    return out
