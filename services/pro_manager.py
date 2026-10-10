"""Pro-only manager surfaces: the Monday board and the note before each renewal.

Free creators get the month plan and while-you-were-away briefs. Pro gets a weekly board
that lists exactly what's left this week (setup, paid briefs, replies that turn a yes into
paid usage), and a note a few days before each renewal that shows what
Pro did that month.
"""

from __future__ import annotations

import re
from calendar import monthrange
from datetime import datetime, timedelta, timezone
from html import escape
from typing import Any, Callable, Dict, List, Optional

from psycopg2.extras import RealDictCursor

WEEKLY_PAID = 2
SWAP_AFTER = timedelta(days=14)
SWAP_WINDOW = timedelta(days=30)
SWAP_MIN_PITCHES = 5
REPLY_LOOKBACK = timedelta(days=30)
POSTED_LOOKBACK = timedelta(days=60)
RENEWAL_NOTE_MIN = timedelta(days=1)
RENEWAL_NOTE_MAX = timedelta(days=6)
NEXT_BRANDS = 5

REPLY_DONE = ("paid_ask_drafted", "pr_shipped", "pr_received", "content_posted", "brand_replied_rejected")
POSTED_DONE = ("ad_usage_asked", "paid_pitch_sent", "milestone")

OPEN_BOARD_CHIP = {"id": "open_board", "label": "Open my Monday board", "action": "board"}


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _cursor(conn):
    return conn.cursor(cursor_factory=RealDictCursor)


def _aware(dt: Optional[datetime]) -> Optional[datetime]:
    if not dt:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def week_start(now: Optional[datetime] = None) -> datetime:
    now = _aware(now) or utc_now()
    day = now - timedelta(days=now.weekday())
    return day.replace(hour=0, minute=0, second=0, microsecond=0)


def week_key(now: Optional[datetime] = None) -> str:
    return week_start(now).strftime("%Y-%m-%d")


def _plural(n: int, word: str, many: str = "") -> str:
    return f"{n} {word if n == 1 else (many or word + 's')}"


def _md_html(text: str) -> str:
    return re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", escape(text))


# ---------------------------------------------------------------------------
# Counts
# ---------------------------------------------------------------------------

_WINDOW_SQL = """
    SELECT
      COUNT(DISTINCT brand_id) FILTER (
          WHERE event_type = 'pitch_sent' AND event_label NOT LIKE 'Follow-up%%') AS pitched,
      COUNT(*) FILTER (WHERE event_type = 'pitch_sent' AND event_label LIKE 'Follow-up%%') AS followups,
      COUNT(*) FILTER (WHERE event_type = 'portfolio_viewed') AS kit_views,
      COUNT(DISTINCT brand_id) FILTER (
          WHERE event_type IN ('brand_replied_interested', 'brand_replied_question')) AS replies,
      COUNT(DISTINCT brand_id) FILTER (WHERE event_type = 'content_posted') AS posted,
      COUNT(*) FILTER (WHERE event_type = 'milestone' AND event_label LIKE 'Paid%%') AS paid
    FROM polly_timeline_events
    WHERE creator_id = %s AND occurred_at >= %s AND occurred_at < %s
"""

WINDOW_KEYS = ("pitched", "followups", "kit_views", "replies", "posted", "paid")


def window_counts(conn, creator_id: int, since: datetime, until: Optional[datetime] = None) -> Dict[str, int]:
    cur = _cursor(conn)
    cur.execute(_WINDOW_SQL, (creator_id, since, until or utc_now() + timedelta(minutes=1)))
    row = cur.fetchone() or {}
    return {k: int(row.get(k) or 0) for k in WINDOW_KEYS}


def paid_applications(conn, creator_id: int, since: datetime) -> int:
    cur = _cursor(conn)
    try:
        cur.execute(
            "SELECT COUNT(*) AS n FROM opportunity_applications WHERE creator_id = %s AND applied_at >= %s",
            (creator_id, since),
        )
        return int((cur.fetchone() or {}).get("n") or 0)
    except Exception:
        conn.rollback()
        return 0


def brands_waiting(conn, creator_id: int, event_type: str, done_types, since: datetime,
                   limit: int = 3) -> List[Dict[str, Any]]:
    """Brands with ``event_type`` since ``since`` and no later event in ``done_types``."""
    cur = _cursor(conn)
    cur.execute(
        """
        SELECT * FROM (
            SELECT DISTINCT ON (e.brand_id) e.brand_id, e.event_label, e.occurred_at, b.brand_name
            FROM polly_timeline_events e
            LEFT JOIN pr_brands b ON b.id = e.brand_id
            WHERE e.creator_id = %s AND e.event_type = %s AND e.occurred_at >= %s
              AND e.brand_id IS NOT NULL
              AND NOT EXISTS (
                  SELECT 1 FROM polly_timeline_events d
                  WHERE d.creator_id = e.creator_id AND d.brand_id = e.brand_id
                    AND d.event_type = ANY(%s) AND d.occurred_at >= e.occurred_at
              )
            ORDER BY e.brand_id, e.occurred_at DESC
        ) x ORDER BY occurred_at DESC LIMIT %s
        """,
        (creator_id, event_type, since, list(done_types), int(limit)),
    )
    out = []
    for r in cur.fetchall():
        name = r.get("brand_name") or str(r.get("event_label") or "").split(" · ")[-1].strip()
        out.append({"brand_id": r["brand_id"], "brand_name": name or "this brand"})
    return out


def stalled(conn, creator_id: int, now: Optional[datetime] = None) -> bool:
    """Pitched enough brands at least two weeks ago and heard nothing back since."""
    now = now or utc_now()
    older = window_counts(conn, creator_id, now - SWAP_WINDOW, now - SWAP_AFTER)
    if older["pitched"] < SWAP_MIN_PITCHES:
        return False
    recent = window_counts(conn, creator_id, now - SWAP_WINDOW, now)
    return recent["replies"] == 0


# ---------------------------------------------------------------------------
# Monday board
# ---------------------------------------------------------------------------

def _task(task_id: str, label: str, detail: str, done: bool, chip: Optional[Dict[str, Any]] = None,
          locked: bool = False, progress: Optional[Dict[str, int]] = None) -> Dict[str, Any]:
    out = {"id": task_id, "label": label, "detail": detail, "done": bool(done), "locked": bool(locked)}
    if chip and not done and not locked:
        out["chip"] = chip
    if progress:
        out["progress"] = progress
    return out


def board_from_facts(facts: Dict[str, Any], now: Optional[datetime] = None) -> Dict[str, Any]:
    """The week as a checklist. Setup first, then the money tasks."""
    now = now or utc_now()
    tasks: List[Dict[str, Any]] = []
    gmail_ready = bool(facts.get("gmail_connected")) or not facts.get("gmail_available", True)
    kit_ready = bool(facts.get("kit_published"))

    if facts.get("gmail_available", True):
        tasks.append(_task(
            "gmail", "Connect Gmail",
            "So I can send your pitches and day-4 follow-ups. Send-only: I can't read your inbox.",
            gmail_ready, {"id": "autopilot", "label": "Connect Gmail", "action": "autopilot"},
        ))
    tasks.append(_task(
        "kit", "Publish your kit",
        "Every pitch links to it, and I can see which brands open it.",
        kit_ready, {"id": "walk_kit", "label": "Finish my kit", "action": "coach_portfolio"},
    ))

    ap = facts.get("autopilot") or {}
    applied = int(facts.get("paid_applied_week") or 0)
    tasks.append(_task(
        "paid_briefs", f"Apply to {WEEKLY_PAID} paid briefs",
        "Brands paying for UGC right now. These pay cash, not product.",
        applied >= WEEKLY_PAID,
        {"id": "paid_ugc", "label": "Show paid briefs", "action": "suggest_gigs", "skip_discovery": True},
        progress={"done": min(applied, WEEKLY_PAID), "total": WEEKLY_PAID},
    ))

    for b in facts.get("replies_open") or []:
        name = b.get("brand_name") or "this brand"
        tasks.append(_task(
            f"reply_{b.get('brand_id')}", f"Reply to {name}",
            "They said yes. I'll write the reply: your shipping details plus a rate to run the video as an ad.",
            False, {"id": "paid_reply", "label": f"Write my reply to {name}", "action": "paid_reply",
                    "brand_id": b.get("brand_id"), "brand_name": name},
        ))
    for b in facts.get("posted_open") or []:
        name = b.get("brand_name") or "this brand"
        tasks.append(_task(
            f"ad_{b.get('brand_id')}", f"Ask {name} to run your video as an ad",
            "Your post is live. This is the ask that turns a free product into paid usage.",
            False, {"id": "ad_usage", "label": f"Ask {name} about ad usage", "action": "ad_usage",
                    "brand_id": b.get("brand_id"), "brand_name": name},
        ))
    if facts.get("stalled"):
        tasks.append(_task(
            "swap", "Swap in fresh brands",
            "No replies two weeks in. I'll switch to brands that answer creators your size.",
            False, {"id": "line_up", "label": "Swap in new brands", "action": "suggest_brands",
                    "deal": "gifted", "skip_discovery": True},
        ))

    left = [t for t in tasks if not t["done"]]
    week = facts.get("week") or {}
    start = week_start(now)
    return {
        "week_of": week_key(now),
        "week_label": f"{start.strftime('%b')} {start.day}",
        "tasks": tasks,
        "left": len(left),
        "stats": {
            "pitched": int(week.get("pitched") or 0),
            "followups": int(week.get("followups") or 0),
            "replies": int(week.get("replies") or 0),
            "kit_views": int(week.get("kit_views") or 0),
            "month_sent": int(ap.get("month_sent") or 0),
            "month_target": int(ap.get("monthly_target") or 0),
        },
    }


def load_board_facts(conn, creator_id: int, kit: Optional[Dict[str, Any]] = None,
                     now: Optional[datetime] = None) -> Dict[str, Any]:
    from services.polly_autopilot import batch_size, list_items, month_counts, settings
    from services.polly_gmail import gmail_configured, load_account

    now = now or utc_now()
    start = week_start(now)
    available = gmail_configured()
    account = load_account(conn, creator_id) if available else None
    prefs = settings(conn, creator_id)
    counts = month_counts(conn, creator_id)
    items = list_items(conn, creator_id)
    sent_week = 0
    for item in items:
        if item.get("status") != "sent" or not item.get("sent_at"):
            continue
        sent_at = _aware(datetime.fromisoformat(str(item["sent_at"])))
        if sent_at >= start:
            sent_week += 1
    target = int(prefs.get("monthly_target") or 24)
    return {
        "gmail_available": available,
        "gmail_connected": bool(account and not account.get("revoked_at")),
        "kit_published": bool((kit or {}).get("published")),
        "autopilot": {
            "weekly_target": max(1, -(-target // 4)),
            "sent_week": sent_week,
            "scheduled": counts.get("scheduled", 0),
            "drafts": counts.get("drafts", 0),
            "next_batch": batch_size(counts, target),
            "month_sent": counts.get("sent", 0),
            "monthly_target": target,
        },
        "paid_applied_week": paid_applications(conn, creator_id, start),
        "replies_open": brands_waiting(conn, creator_id, "brand_replied_interested", REPLY_DONE,
                                       now - REPLY_LOOKBACK),
        "posted_open": brands_waiting(conn, creator_id, "content_posted", POSTED_DONE,
                                      now - POSTED_LOOKBACK),
        "stalled": stalled(conn, creator_id, now),
        "week": window_counts(conn, creator_id, start, now + timedelta(minutes=1)),
    }


def load_board(conn, creator_id: int, kit: Optional[Dict[str, Any]] = None,
               now: Optional[datetime] = None) -> Dict[str, Any]:
    return board_from_facts(load_board_facts(conn, creator_id, kit=kit, now=now), now=now)


def board_summary(board: Dict[str, Any], first: str = "") -> str:
    """The chat text above the board card."""
    left = int(board.get("left") or 0)
    name = (first or "").strip()
    head = f"Your Monday board{', ' + name if name else ''}, week of {board.get('week_label')}."
    if not left:
        body = "Everything on it is done. I'll keep following up, and next week's board lands Monday."
    else:
        body = f"{_plural(left, 'thing')} left this week. Work top to bottom, setup first."
    goal = "The goal this month: **one yes**, then I turn it into a paid usage deal."
    return "\n\n".join([head, body, goal])


def board_message(board: Dict[str, Any], first: str = "") -> Dict[str, Any]:
    return {"role": "assistant", "kind": "board", "content": board_summary(board, first), "board": board}


def board_due(notes: Optional[Dict[str, Any]], now: Optional[datetime] = None) -> bool:
    return (notes or {}).get("board_week") != week_key(now)


def board_email(first_name: str, board: Dict[str, Any], unsubscribe_url: str = "",
                open_href: str = "") -> Dict[str, str]:
    from services.polly_alerts import render_email

    left = [t for t in board.get("tasks") or [] if not t.get("done")]
    subject = (
        f"Your Monday board: {_plural(len(left), 'thing')} this week" if left
        else "Your Monday board is clear"
    )
    lines = []
    if left:
        items = "".join(f"<li style=\"margin:0 0 6px 0;\">{escape(t['label'])}</li>" for t in left[:6])
        lines.append(f"Here's this week, top to bottom:<ul style=\"margin:8px 0 0 18px;padding:0;\">{items}</ul>")
    stats = board.get("stats") or {}
    if stats.get("month_target"):
        lines.append(
            f"This month so far: <strong>{stats.get('month_sent', 0)}</strong> of "
            f"{stats['month_target']} brands pitched from your Gmail."
        )
    lines.append("The goal this month: <strong>one yes</strong>, then I turn it into a paid usage deal.")
    buttons = [{"label": "Open my board", "href": open_href}] if open_href else []
    return {"subject": subject, "html": render_email(first_name, subject, lines, buttons, unsubscribe_url)}


# ---------------------------------------------------------------------------
# Renewal note (a few days before each renewal)
# ---------------------------------------------------------------------------

def add_months(dt: datetime, months: int) -> datetime:
    total = dt.month - 1 + months
    year, month = dt.year + total // 12, total % 12 + 1
    return dt.replace(year=year, month=month, day=min(dt.day, monthrange(year, month)[1]))


def next_renewal(started_at: Optional[datetime], ends_at: Optional[datetime],
                 now: Optional[datetime] = None) -> Optional[datetime]:
    """Next billing date: the stored period end, rolled forward a month at a time if stale."""
    now = now or utc_now()
    anchor = _aware(ends_at) or _aware(started_at)
    if not anchor:
        return None
    base, n = anchor, 0
    while add_months(base, n) <= now and n < 120:
        n += 1
    return add_months(base, n)


def renewal_due(renews_at: Optional[datetime], now: Optional[datetime] = None) -> bool:
    now = now or utc_now()
    return bool(renews_at) and now + RENEWAL_NOTE_MIN <= renews_at <= now + RENEWAL_NOTE_MAX


def _date_label(dt: datetime) -> str:
    return f"{dt.strftime('%B')} {dt.day}"


def renewal_lines(counts: Dict[str, int], renews_at: datetime, next_brands: List[str],
                  gmail_connected: bool = True) -> List[str]:
    """Markdown lines: what Pro did this period and what's already queued for the next."""
    pitched = int(counts.get("pitched") or 0)
    followups = int(counts.get("followups") or 0)
    views = int(counts.get("kit_views") or 0)
    replies = int(counts.get("replies") or 0)
    if not pitched:
        return _idle_renewal_lines(renews_at, views, next_brands, gmail_connected)
    lines = [f"Your Pro month renews on **{_date_label(renews_at)}**. Here's what it did:"]
    sent = f"**{_plural(pitched, 'brand')}** pitched"
    if followups:
        sent += f", {_plural(followups, 'follow-up')} sent"
    lines.append(f"📤 {sent}.")
    if views:
        lines.append(f"👀 **{views}** kit view{'s' if views != 1 else ''} from brands.")
    if replies:
        lines.append(f"💬 **{_plural(replies, 'reply', 'replies')}**. Tap any yes and I'll write the paid usage ask.")
    else:
        lines.append(
            "💬 No replies yet. Brands usually answer 2–4 weeks after a pitch, so these pitches are "
            "still live, and I keep following up."
        )
    if counts.get("paid"):
        lines.append(f"💰 **{_plural(int(counts['paid']), 'paid deal')}** landed.")
    if next_brands:
        listed = ", ".join(f"**{n}**" for n in next_brands[:NEXT_BRANDS])
        lines.append(f"Already lined up for next month: {listed}.")
    return lines


def _idle_renewal_lines(renews_at: datetime, views: int, next_brands: List[str],
                        gmail_connected: bool) -> List[str]:
    """Nothing went out this period: say why plainly and make restarting one tap."""
    why = (
        "because Gmail isn't connected yet. It takes a minute, then you OK the week's pitches in Autopilot."
        if not gmail_connected
        else "because no pitches have been OK'd yet. Open Autopilot and OK this week's pitches."
    )
    lines = [
        f"Your Pro month renews on **{_date_label(renews_at)}**.",
        f"I haven't pitched anyone for you this month, {why}",
    ]
    if views:
        lines.append(f"Brands still opened your kit **{views}** time{'s' if views != 1 else ''}. They're looking.")
    if next_brands:
        listed = ", ".join(f"**{n}**" for n in next_brands[:NEXT_BRANDS])
        lines.append(f"Ready to go as soon as you say so: {listed}.")
    else:
        lines.append("Open your Monday board and I'll pick this week's brands.")
    return lines


def renewal_note_message(lines: List[str]) -> Dict[str, Any]:
    return {"role": "assistant", "kind": "brief", "content": "\n\n".join(lines),
            "task_chips": [dict(OPEN_BOARD_CHIP)]}


def renewal_email(first_name: str, lines: List[str], unsubscribe_url: str = "", open_href: str = "",
                  manage_href: str = "", idle: bool = False) -> Dict[str, str]:
    from services.polly_alerts import render_email

    subject = "Your brands are ready when you are" if idle else "What Pro did for you this month"
    html_lines = [_md_html(line) for line in lines]
    buttons = [{"label": "Open my Monday board", "href": open_href}] if open_href else []
    links = [{"label": "Manage plan", "href": manage_href}] if manage_href else None
    return {"subject": subject, "html": render_email(first_name, subject, html_lines, buttons,
                                                     unsubscribe_url, links=links)}


def next_brand_names(conn, creator_id: int, limit: int = NEXT_BRANDS) -> List[str]:
    """Autopilot drafts and scheduled pitches first, then Polly's matched queue."""
    from services.polly import drop_pitched
    from services.polly_autopilot import list_items
    from services.polly_memory import load_thread

    names: List[str] = []
    for item in list_items(conn, creator_id):
        if item.get("status") in ("draft", "approved") and item.get("brand_name"):
            names.append(item["brand_name"])
    thread = load_thread(conn, creator_id)
    for row in drop_pitched(thread.get("suggested_brands") or [], thread.get("notes") or {}):
        if isinstance(row, dict) and row.get("name"):
            names.append(row["name"])
    out: List[str] = []
    for n in names:
        if n.lower() not in {o.lower() for o in out}:
            out.append(n)
    return out[:limit]


def _gmail_connected(conn, creator_id: int) -> bool:
    from services.polly_gmail import gmail_configured, load_account

    if not gmail_configured():
        return True
    account = load_account(conn, creator_id)
    return bool(account and not account.get("revoked_at"))


def _cancel_scheduled(conn, creator_id: int, since: datetime) -> bool:
    cur = _cursor(conn)
    try:
        cur.execute(
            """
            SELECT 1 FROM creator_retention_events
            WHERE creator_id = %s AND outcome = 'canceled' AND created_at >= %s LIMIT 1
            """,
            (creator_id, since),
        )
        return cur.fetchone() is not None
    except Exception:
        conn.rollback()
        return False


def _post_note(conn, creator_id: int, message: Dict[str, Any], notes_patch: Optional[Dict] = None) -> None:
    from services.polly_memory import load_thread, save_thread

    thread = load_thread(conn, creator_id)
    messages = list(thread.get("messages") or []) + [message]
    notes = dict(thread.get("notes") or {})
    notes.update(notes_patch or {})
    save_thread(conn, creator_id, messages, thread.get("suggested_brands") or [], notes=notes)


def email_renewal_notes(conn, limit: int = 40, dry_run: bool = False, send_fn: Optional[Callable] = None,
                        test_email: str = "", now: Optional[datetime] = None) -> List[Dict[str, Any]]:
    """1-6 days before a Pro renewal: what Pro did this period, posted in Polly and emailed."""
    from services.polly_alerts import (
        _default_send, _log_email, _recipient, _unsubscribe_url, chip_link, ensure_alert_tables, frontend_url,
    )

    now = now or utc_now()
    ensure_alert_tables(conn)
    send = send_fn or _default_send
    cur = _cursor(conn)
    cur.execute(
        """
        SELECT c.id AS creator_id, c.subscription_started_at, c.subscription_ends_at
        FROM creators c
        WHERE LOWER(COALESCE(c.subscription_tier, '')) = 'pro'
          AND c.stripe_subscription_id IS NOT NULL
          AND COALESCE(c.subscription_status, 'active') IN ('active', 'trialing')
        """
    )
    out: List[Dict[str, Any]] = []
    for row in [dict(r) for r in cur.fetchall()]:
        if len(out) >= limit:
            break
        cid = row["creator_id"]
        renews = next_renewal(row.get("subscription_started_at"), row.get("subscription_ends_at"), now)
        if not renewal_due(renews, now):
            continue
        ref = int(renews.strftime("%Y%m%d"))
        cur.execute(
            "SELECT 1 FROM polly_email_log WHERE creator_id = %s AND kind = 'renewal_note' AND ref_id = %s LIMIT 1",
            (cid, ref),
        )
        if cur.fetchone():
            continue
        period_start = add_months(renews, -1)
        if _cancel_scheduled(conn, cid, period_start):
            continue
        who = _recipient(conn, cid)
        if not who:
            continue
        counts = window_counts(conn, cid, period_start, now)
        lines = renewal_lines(counts, renews, next_brand_names(conn, cid),
                              gmail_connected=_gmail_connected(conn, cid))
        unsub = _unsubscribe_url(who["user_id"])
        mail = renewal_email(
            who["first"], lines, unsub,
            open_href=chip_link("open_board", kind="renewal_note"),
            manage_href=f"{frontend_url()}/creator/dashboard/settings",
            idle=not counts["pitched"],
        )
        item = {"creator_id": cid, "kind": "renewal_note", "subject": mail["subject"], "to": who["email"],
                "renews_at": renews.isoformat()}
        if dry_run:
            out.append(item)
            continue
        try:
            _post_note(conn, cid, renewal_note_message(lines))
        except Exception as err:
            print(f"[Pro] renewal thread note skipped: {err}")
            conn.rollback()
        result = send(test_email or who["email"], mail["subject"], mail["html"], unsub, "renewal_note")
        _log_email(conn, cid, "renewal_note", None, ref, mail["subject"], result)
        item["ok"] = bool(result.get("success"))
        out.append(item)
    return out


def email_monday_boards(conn, limit: int = 40, dry_run: bool = False, send_fn: Optional[Callable] = None,
                        test_email: str = "", now: Optional[datetime] = None) -> List[Dict[str, Any]]:
    """Mondays: each Pro creator gets this week's board by email (the board itself lives in Polly)."""
    from services.polly_alerts import (
        _default_send, _log_email, _recipient, _unsubscribe_url, chip_link, ensure_alert_tables,
    )
    from services.polly_kit import load_kit_snapshot

    now = now or utc_now()
    if now.weekday() != 0:
        return []
    ensure_alert_tables(conn)
    send = send_fn or _default_send
    ref = int(week_start(now).strftime("%Y%m%d"))
    cur = _cursor(conn)
    cur.execute(
        """
        SELECT c.id AS creator_id
        FROM creators c
        WHERE (LOWER(COALESCE(c.subscription_tier, '')) = 'pro' OR COALESCE(c.unlocks_tier, '') = 'pro')
          AND NOT EXISTS (
              SELECT 1 FROM polly_email_log l
              WHERE l.creator_id = c.id AND l.kind = 'monday_board' AND l.ref_id = %s
          )
        LIMIT %s
        """,
        (ref, limit),
    )
    out: List[Dict[str, Any]] = []
    for row in [dict(r) for r in cur.fetchall()]:
        cid = row["creator_id"]
        who = _recipient(conn, cid)
        if not who:
            continue
        try:
            board = load_board(conn, cid, kit=load_kit_snapshot(conn, cid), now=now)
        except Exception as err:
            print(f"[Pro] board for {cid} skipped: {err}")
            conn.rollback()
            continue
        unsub = _unsubscribe_url(who["user_id"])
        mail = board_email(who["first"], board, unsub, open_href=chip_link("open_board", kind="monday_board"))
        item = {"creator_id": cid, "kind": "monday_board", "subject": mail["subject"], "to": who["email"]}
        if dry_run:
            out.append(item)
            continue
        result = send(test_email or who["email"], mail["subject"], mail["html"], unsub, "monday_board")
        _log_email(conn, cid, "monday_board", None, ref, mail["subject"], result)
        item["ok"] = bool(result.get("success"))
        out.append(item)
    return out
