"""7-day Pro trial journey: get Autopilot sending in week one, then remind before the first charge.

Trials convert when the creator sees Polly working before day 7, so the journey is:
- day 0 (checkout webhook): welcome, connect Gmail so pitches start this week;
- day 1+ (Polly cron): one nudge if Gmail still isn't connected;
- day 5 (Polly cron): what Polly did this week + the charge date and how to cancel.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from html import escape
from typing import Any, Callable, Dict, List, Optional
from urllib.parse import urlencode

from psycopg2.extras import RealDictCursor

TRIAL_LENGTH = timedelta(days=7)
GMAIL_NUDGE_AFTER = timedelta(hours=24)
ENDING_NOTICE_AFTER = timedelta(days=5)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _aware(dt: Optional[datetime]) -> Optional[datetime]:
    if dt is None:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _date_label(dt: datetime) -> str:
    return f"{dt.strftime('%A, %b')} {dt.day}"


def _link(path: str, campaign: str, extra: Optional[Dict[str, str]] = None) -> str:
    from services.polly_alerts import frontend_url

    params = dict(extra or {})
    params.update({"utm_source": "email", "utm_medium": "polly_alert", "utm_campaign": campaign})
    return f"{frontend_url()}{path}?{urlencode(params)}"


def connect_gmail_link(campaign: str) -> str:
    return _link("/creator/dashboard/for-you", campaign, {"autopilot": "pro_started"})


def settings_link(campaign: str) -> str:
    return _link("/creator/dashboard/settings", campaign)


def trial_started_email(first_name: str, ends_at: datetime, unsubscribe_url: str = "") -> Dict[str, str]:
    from services.polly_alerts import render_email

    subject = "Your free week of Pro has started"
    lines = [
        "I'm on it. Connect Gmail and I'll start pitching this week: about 6 matched brands a week, "
        "written and sent from your own address, with follow-ups. Nothing to approve.",
        "Want different brands? Change your niche and targets in Settings and I'll adjust.",
        f"Your free week ends on <strong>{escape(_date_label(ends_at))}</strong>. "
        "I'll email you 2 days before with what I did, so you can decide.",
    ]
    buttons = [{"label": "Connect Gmail", "href": connect_gmail_link("trial_started")}]
    return {"subject": subject, "html": render_email(first_name, subject, lines, buttons, unsubscribe_url)}


def trial_gmail_email(first_name: str, ends_at: datetime, unsubscribe_url: str = "") -> Dict[str, str]:
    from services.polly_alerts import render_email

    subject = "I can't start pitching until Gmail is connected"
    lines = [
        "Your free week of Pro is running, but I haven't sent a single pitch yet: "
        "Gmail isn't connected, so I have no way to send from your address.",
        f"It takes 30 seconds. Connect it now and this week's brands go out before your trial ends on "
        f"<strong>{escape(_date_label(ends_at))}</strong>.",
    ]
    buttons = [{"label": "Connect Gmail", "href": connect_gmail_link("trial_gmail")}]
    return {"subject": subject, "html": render_email(first_name, subject, lines, buttons, unsubscribe_url)}


def trial_facts(conn, creator_id: int, since: datetime) -> Dict[str, Any]:
    from services.polly_autopilot import REPLIED_EVENTS

    cur = conn.cursor(cursor_factory=RealDictCursor)
    cur.execute(
        """
        SELECT
          COUNT(*) FILTER (WHERE status = 'sent' AND sent_at >= %s) AS sent,
          COUNT(*) FILTER (WHERE followup_sent_at >= %s) AS followups,
          COUNT(*) FILTER (WHERE status IN ('approved', 'draft')) AS lined_up
        FROM polly_autopilot_queue WHERE creator_id = %s
        """,
        (since, since, creator_id),
    )
    queue = dict(cur.fetchone() or {})
    cur.execute(
        """
        SELECT
          COUNT(DISTINCT brand_id) FILTER (WHERE event_type = ANY(%s)) AS replies,
          COUNT(*) FILTER (WHERE event_type = 'portfolio_viewed') AS kit_views
        FROM polly_timeline_events
        WHERE creator_id = %s AND occurred_at >= %s
        """,
        (list(REPLIED_EVENTS), creator_id, since),
    )
    events = dict(cur.fetchone() or {})
    cur.execute(
        "SELECT 1 FROM polly_gmail_accounts WHERE creator_id = %s AND revoked_at IS NULL LIMIT 1",
        (creator_id,),
    )
    return {
        "sent": int(queue.get("sent") or 0),
        "followups": int(queue.get("followups") or 0),
        "lined_up": int(queue.get("lined_up") or 0),
        "replies": int(events.get("replies") or 0),
        "kit_views": int(events.get("kit_views") or 0),
        "gmail": cur.fetchone() is not None,
    }


def _billing_recipient(conn, creator_id: int) -> Optional[Dict[str, Any]]:
    """Trial notices are about their card, so they go out even if marketing email is off."""
    cur = conn.cursor(cursor_factory=RealDictCursor)
    cur.execute(
        """
        SELECT u.id AS user_id, u.email, u.first_name
        FROM creators c JOIN users u ON u.id = c.user_id
        WHERE c.id = %s
        """,
        (creator_id,),
    )
    row = cur.fetchone()
    if not row or not row.get("email"):
        return None
    first = (row.get("first_name") or "").strip()
    return {"user_id": row["user_id"], "email": row["email"],
            "first": first.split()[0].capitalize() if first else ""}


def send_trial_started(conn, creator_id: int, send_fn: Optional[Callable] = None) -> bool:
    from services.polly_alerts import _default_send, _log_email, _unsubscribe_url, ensure_alert_tables

    ensure_alert_tables(conn)
    who = _billing_recipient(conn, creator_id)
    if not who:
        return False
    unsub = _unsubscribe_url(who["user_id"])
    mail = trial_started_email(who["first"], _now() + TRIAL_LENGTH, unsub)
    result = (send_fn or _default_send)(who["email"], mail["subject"], mail["html"], unsub, "trial_started")
    _log_email(conn, creator_id, "trial_started", None, None, mail["subject"], result)
    return bool(result.get("success"))


def _plural(n: int, word: str) -> str:
    many = word + ("es" if word.endswith(("ch", "sh", "s", "x")) else "s")
    return f"{n} {word if n == 1 else many}"


def trial_ending_email(first_name: str, ends_at: datetime, facts: Dict[str, Any],
                       unsubscribe_url: str = "") -> Dict[str, str]:
    from services.polly_alerts import render_email

    when = escape(_date_label(ends_at))
    if facts.get("sent"):
        subject = f"Your free week: {_plural(facts['sent'], 'pitch')} sent"
        done = [f"<strong>{_plural(facts['sent'], 'pitch')}</strong> sent from your Gmail"]
        if facts.get("followups"):
            done.append(_plural(facts["followups"], "follow-up"))
        if facts.get("replies"):
            done.append(f"<strong>{_plural(facts['replies'], 'brand')}</strong> replied")
        if facts.get("kit_views"):
            done.append(f"{_plural(facts['kit_views'], 'kit view')}")
        lines = [
            "Here's your free week so far: " + ", ".join(done) + ".",
            "Each pitch gets a follow-up on day 4, so the ones I sent this week are still working.",
        ]
        if facts.get("lined_up"):
            lines.append(f"{_plural(facts['lined_up'], 'more pitch')} {'is' if facts['lined_up'] == 1 else 'are'} "
                         "lined up for next week.")
        buttons = [{"label": "See what I sent", "href": _link("/creator/dashboard/for-you", "trial_ending",
                                                              {"chip": "line_up"})}]
    else:
        subject = "Your free week ends soon and I haven't sent anything yet"
        lines = [
            "I haven't sent a pitch yet because Gmail isn't connected." if not facts.get("gmail") else
            "I haven't sent a pitch yet. Your first batch goes out on your next send day.",
            "Connect Gmail now and I'll still get this week's brands out before your trial ends.",
        ]
        buttons = [{"label": "Connect Gmail", "href": connect_gmail_link("trial_ending")}]
    lines.append(
        f"Your free week ends on <strong>{when}</strong>. Unless you cancel, Pro continues at $19/mo "
        "and I keep going. Not for you? Cancel in Settings before then and you won't be charged."
    )
    links = [{"label": "Manage subscription", "href": settings_link("trial_ending")}]
    return {"subject": subject, "html": render_email(first_name, subject, lines, buttons, unsubscribe_url, links=links)}


def email_trial_journey(conn, limit: int = 40, dry_run: bool = False, send_fn: Optional[Callable] = None,
                        test_email: str = "") -> List[Dict[str, Any]]:
    """Gmail nudge (day 1+) and the day-5 recap for creators on the free week."""
    from services.polly_alerts import (
        OTHER_ALERT_GAP, _default_send, _log_email, _recently_emailed, _recipient, _unsubscribe_url,
        ensure_alert_tables,
    )

    ensure_alert_tables(conn)
    send = send_fn or _default_send
    now = _now()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    cur.execute(
        """
        SELECT c.id AS creator_id, c.subscription_started_at AS started,
               EXISTS (SELECT 1 FROM polly_gmail_accounts g
                       WHERE g.creator_id = c.id AND g.revoked_at IS NULL) AS gmail,
               EXISTS (SELECT 1 FROM polly_email_log l WHERE l.creator_id = c.id AND l.ok
                       AND l.kind = 'trial_gmail' AND l.sent_at >= c.subscription_started_at) AS nudged,
               EXISTS (SELECT 1 FROM polly_email_log l WHERE l.creator_id = c.id AND l.ok
                       AND l.kind = 'trial_ending' AND l.sent_at >= c.subscription_started_at) AS reminded
        FROM creators c
        WHERE c.subscription_status = 'trialing' AND c.subscription_started_at IS NOT NULL
        ORDER BY c.subscription_started_at
        LIMIT %s
        """,
        (limit,),
    )
    out = []
    for row in [dict(r) for r in cur.fetchall()]:
        cid = row["creator_id"]
        started = _aware(row["started"])
        ends_at = started + TRIAL_LENGTH
        if now >= ends_at:
            continue
        age = now - started
        if age >= ENDING_NOTICE_AFTER and not row["reminded"]:
            kind = "trial_ending"
        elif age >= GMAIL_NUDGE_AFTER and not row["gmail"] and not row["nudged"] and not row["reminded"]:
            if _recently_emailed(conn, cid, ["trial_ending", "kit_view", "nudge", "polly_brief"], OTHER_ALERT_GAP):
                continue
            kind = "trial_gmail"
        else:
            continue
        who = _recipient(conn, cid) or (_billing_recipient(conn, cid) if kind == "trial_ending" else None)
        if not who:
            continue
        unsub = _unsubscribe_url(who["user_id"])
        if kind == "trial_ending":
            mail = trial_ending_email(who["first"], ends_at, trial_facts(conn, cid, started), unsub)
        else:
            mail = trial_gmail_email(who["first"], ends_at, unsub)
        item = {"creator_id": cid, "kind": kind, "subject": mail["subject"], "to": who["email"]}
        if dry_run:
            out.append(item)
            continue
        result = send(test_email or who["email"], mail["subject"], mail["html"], unsub, kind)
        _log_email(conn, cid, kind, None, None, mail["subject"], result)
        item["ok"] = bool(result.get("success"))
        out.append(item)
    return out
