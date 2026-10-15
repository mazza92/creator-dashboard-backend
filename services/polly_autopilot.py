"""Polly Autopilot (Pro): the cron keeps a week of pitches written and scheduled, sends them from the
creator's Gmail on their send days with no approval step, with up to two follow-ups (day 4, day 10)
in the same thread unless the brand replied."""

from __future__ import annotations

import random
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Dict, List, Optional

from psycopg2.extras import RealDictCursor

TARGET_OPTIONS = (30, 60, 100, 150)
DEFAULT_MONTHLY_TARGET = 60
# Rows saved before the volume options still hold the old default; they never chose it.
LEGACY_DEFAULT_TARGET = 24
WEEK_BATCH = 15
TARGET_MIN = TARGET_OPTIONS[0]
TARGET_MAX = TARGET_OPTIONS[-1]
MAX_SENDS_PER_DAY = 8
# The browser writes about 20 seconds a pitch, so "start now" writes a few and the cron does the rest.
MANUAL_BATCH = 6
SEND_WINDOW_UTC = (13, 21)
WEEKDAYS = (0, 1, 2, 3, 4)
DEAL_FOCUS = ("auto", "gifted", "paid")
DEFAULT_FOLLOWUPS = 2
# Gap before each follow-up: day 4 after the pitch, then 6 more days (day 10).
FOLLOWUP_GAPS = (timedelta(days=4), timedelta(days=6))
FOLLOWUP_AFTER = FOLLOWUP_GAPS[0]
HEADSUP_BEFORE = timedelta(days=1)
HEADSUP_MIN_NOTICE = timedelta(hours=20)
NUDGE_HOLD = timedelta(days=5)
# Autopilot needs no approval: any draft left in the queue is scheduled on the next run.
AUTO_APPROVE_AFTER = timedelta(0)
AUTO_FILL_EVERY = timedelta(hours=1)
AUTO_DRAFTS_PER_RUN = 2
FILL_BLOCKED_WAIT = timedelta(hours=5)

REPLIED_EVENTS = ("brand_replied_interested", "brand_replied_question", "brand_replied_rejected")
REPLY_EVENTS = REPLIED_EVENTS + ("dropped", "email_bounced", "pitch_not_sent")

_TABLES_READY = False
_ADDED_COLUMNS = (
    ("polly_autopilot", "deal_focus", "TEXT NOT NULL DEFAULT 'auto'"),
    ("polly_autopilot", "send_days", "TEXT NOT NULL DEFAULT '01234'"),
    ("polly_autopilot", "followups", f"INTEGER NOT NULL DEFAULT {DEFAULT_FOLLOWUPS}"),
    ("polly_autopilot", "filled_at", "TIMESTAMPTZ"),
    ("polly_autopilot", "fill_blocked", "TEXT"),
    ("polly_autopilot_queue", "followups_sent", "INTEGER NOT NULL DEFAULT 0"),
    ("polly_autopilot_queue", "announced_at", "TIMESTAMPTZ"),
)


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _cursor(conn):
    return conn.cursor(cursor_factory=RealDictCursor)


def ensure_autopilot_tables(conn) -> None:
    global _TABLES_READY
    if _TABLES_READY:
        return
    cur = _cursor(conn)
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS polly_autopilot (
            creator_id INTEGER PRIMARY KEY,
            enabled BOOLEAN NOT NULL DEFAULT TRUE,
            monthly_target INTEGER NOT NULL DEFAULT 60,
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS polly_autopilot_queue (
            id SERIAL PRIMARY KEY,
            creator_id INTEGER NOT NULL,
            brand_id INTEGER NOT NULL,
            brand_name TEXT,
            to_email TEXT NOT NULL,
            subject TEXT NOT NULL,
            body TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'draft',
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            approved_at TIMESTAMPTZ,
            scheduled_for TIMESTAMPTZ,
            sent_at TIMESTAMPTZ,
            gmail_id TEXT,
            thread_id TEXT,
            message_id TEXT,
            error TEXT,
            followup_status TEXT,
            followup_due_at TIMESTAMPTZ,
            headsup_at TIMESTAMPTZ,
            followup_sent_at TIMESTAMPTZ,
            UNIQUE (creator_id, brand_id)
        )
        """
    )
    cur.execute("SELECT 1 FROM pg_indexes WHERE indexname = 'idx_polly_autopilot_due'")
    if not cur.fetchone():
        cur.execute(
            "CREATE INDEX IF NOT EXISTS idx_polly_autopilot_due ON polly_autopilot_queue (status, scheduled_for)"
        )
    # ALTER takes an exclusive lock even when the column exists, so only run the missing ones.
    cur.execute(
        """
        SELECT table_name || '.' || column_name AS col FROM information_schema.columns
        WHERE table_name IN ('polly_autopilot', 'polly_autopilot_queue')
        """
    )
    have = {r["col"] for r in cur.fetchall()}
    for table, column, ddl in _ADDED_COLUMNS:
        if f"{table}.{column}" not in have:
            cur.execute(f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS {column} {ddl}")
    conn.commit()
    _TABLES_READY = True


# ---------------------------------------------------------------------------
# Settings + queue
# ---------------------------------------------------------------------------

def parse_send_days(raw: Any) -> List[int]:
    days = sorted({int(ch) for ch in str(raw or "") if ch.isdigit() and int(ch) in WEEKDAYS})
    return days or list(WEEKDAYS)


def clamp_target(target: Any) -> int:
    """Snap to the next volume option up, so any saved number lands on 30, 60, 100 or 150."""
    try:
        value = int(target)
    except (TypeError, ValueError):
        return DEFAULT_MONTHLY_TARGET
    return next((opt for opt in TARGET_OPTIONS if value <= opt), TARGET_MAX)


def effective_target(stored: Any) -> int:
    """The target a saved row means today."""
    if stored is None or stored == LEGACY_DEFAULT_TARGET:
        return DEFAULT_MONTHLY_TARGET
    return clamp_target(stored)


def per_week(target: int) -> int:
    return max(1, -(-int(target) // 4))


def settings(conn, creator_id: int) -> Dict[str, Any]:
    ensure_autopilot_tables(conn)
    cur = _cursor(conn)
    cur.execute(
        "SELECT enabled, monthly_target, deal_focus, send_days, followups FROM polly_autopilot WHERE creator_id = %s",
        (creator_id,),
    )
    row = cur.fetchone() or {}
    focus = row.get("deal_focus") if row.get("deal_focus") in DEAL_FOCUS else "auto"
    followups = row.get("followups")
    return {
        "enabled": bool(row["enabled"]) if row else True,
        "monthly_target": effective_target(row.get("monthly_target")),
        "deal_focus": focus,
        "send_days": parse_send_days(row.get("send_days")),
        "followups": int(followups) if followups is not None else DEFAULT_FOLLOWUPS,
    }


def update_settings(conn, creator_id: int, patch: Dict[str, Any]) -> Dict[str, Any]:
    """Validate and save any of: monthly_target, deal_focus, send_days, followups."""
    cols: Dict[str, Any] = {}
    if "monthly_target" in patch:
        cols["monthly_target"] = clamp_target(patch["monthly_target"])
    if patch.get("deal_focus") in DEAL_FOCUS:
        cols["deal_focus"] = patch["deal_focus"]
    if "send_days" in patch:
        raw = patch["send_days"]
        days = parse_send_days("".join(str(d) for d in raw) if isinstance(raw, (list, tuple)) else raw)
        cols["send_days"] = "".join(str(d) for d in days)
    if "followups" in patch:
        try:
            cols["followups"] = max(0, min(len(FOLLOWUP_GAPS), int(patch["followups"])))
        except (TypeError, ValueError):
            pass
    if cols:
        ensure_autopilot_tables(conn)
        names = list(cols)
        cur = _cursor(conn)
        cur.execute(
            f"""
            INSERT INTO polly_autopilot (creator_id, {', '.join(names)}) VALUES (%s, {', '.join(['%s'] * len(names))})
            ON CONFLICT (creator_id) DO UPDATE SET {', '.join(f'{n} = EXCLUDED.{n}' for n in names)}, updated_at = NOW()
            """,
            (creator_id, *[cols[n] for n in names]),
        )
        conn.commit()
    return settings(conn, creator_id)


def set_enabled(conn, creator_id: int, enabled: bool) -> None:
    ensure_autopilot_tables(conn)
    cur = _cursor(conn)
    cur.execute(
        """
        INSERT INTO polly_autopilot (creator_id, enabled) VALUES (%s, %s)
        ON CONFLICT (creator_id) DO UPDATE SET enabled = EXCLUDED.enabled, updated_at = NOW()
        """,
        (creator_id, bool(enabled)),
    )
    conn.commit()


def month_counts(conn, creator_id: int) -> Dict[str, int]:
    ensure_autopilot_tables(conn)
    cur = _cursor(conn)
    cur.execute(
        """
        SELECT
          COUNT(*) FILTER (WHERE status = 'sent' AND sent_at >= date_trunc('month', NOW())) AS sent,
          COALESCE(SUM(GREATEST(followups_sent, 1)) FILTER (
              WHERE followup_sent_at >= date_trunc('month', NOW())), 0) AS followups,
          COUNT(*) FILTER (WHERE status = 'approved') AS scheduled,
          COUNT(*) FILTER (WHERE status = 'draft') AS drafts,
          COUNT(*) FILTER (WHERE status = 'sent' AND sent_at >= date_trunc('month', NOW()) AND EXISTS (
              SELECT 1 FROM polly_timeline_events e
              WHERE e.creator_id = q.creator_id AND e.brand_id = q.brand_id
                AND e.event_type = ANY(%s) AND e.occurred_at >= q.sent_at
          )) AS replied
        FROM polly_autopilot_queue q WHERE creator_id = %s
        """,
        (list(REPLIED_EVENTS), creator_id),
    )
    row = cur.fetchone() or {}
    return {k: int(row.get(k) or 0) for k in ("sent", "followups", "scheduled", "drafts", "replied")}


def queued_brand_ids(conn, creator_id: int) -> List[int]:
    ensure_autopilot_tables(conn)
    cur = _cursor(conn)
    cur.execute(
        "SELECT brand_id FROM polly_autopilot_queue WHERE creator_id = %s AND status <> 'skipped'",
        (creator_id,),
    )
    return [int(r["brand_id"]) for r in cur.fetchall()]


def set_monthly_target(conn, creator_id: int, target: int) -> int:
    return update_settings(conn, creator_id, {"monthly_target": target})["monthly_target"]


def batch_size(counts: Dict[str, int], target: int) -> int:
    """How many new brands to plan now: a week's worth, never past the monthly target."""
    left = max(0, int(target) - counts.get("sent", 0) - counts.get("scheduled", 0) - counts.get("drafts", 0))
    return min(per_week(target), left)


def refill_size(counts: Dict[str, int], target: int) -> int:
    """How many drafts the cron writes so a week's worth is always waiting, never past the target."""
    pending = counts.get("scheduled", 0) + counts.get("drafts", 0)
    left = int(target) - counts.get("sent", 0) - pending
    return max(0, min(per_week(target) - pending, left))


def sends_per_day(target: int, days: List[int]) -> int:
    """Enough per send day to fit the week's batch, never more than MAX_SENDS_PER_DAY."""
    return min(MAX_SENDS_PER_DAY, max(1, -(-per_week(target) // len(days or WEEKDAYS))))


def add_draft(conn, creator_id: int, brand_id: int, brand_name: str, to_email: str,
              subject: str, body: str, announced: bool = False) -> Dict[str, Any]:
    """``announced`` when the creator wrote it in the panel, so the cron doesn't email about it."""
    ensure_autopilot_tables(conn)
    cur = _cursor(conn)
    cur.execute(
        """
        INSERT INTO polly_autopilot_queue
          (creator_id, brand_id, brand_name, to_email, subject, body, status, announced_at)
        VALUES (%s, %s, %s, %s, %s, %s, 'draft', CASE WHEN %s THEN NOW() END)
        ON CONFLICT (creator_id, brand_id) DO UPDATE
          SET brand_name = EXCLUDED.brand_name, to_email = EXCLUDED.to_email, subject = EXCLUDED.subject,
              body = EXCLUDED.body, status = 'draft', error = NULL, created_at = NOW(),
              announced_at = EXCLUDED.announced_at
          WHERE polly_autopilot_queue.status IN ('draft', 'skipped', 'failed')
        RETURNING id, brand_id, brand_name, to_email, subject, body, status
        """,
        (creator_id, brand_id, brand_name, to_email, subject, body, bool(announced)),
    )
    row = cur.fetchone()
    conn.commit()
    return dict(row) if row else {}


def list_items(conn, creator_id: int, limit: int = 40) -> List[Dict[str, Any]]:
    ensure_autopilot_tables(conn)
    cur = _cursor(conn)
    cur.execute(
        """
        SELECT id, brand_id, brand_name, to_email, subject, body, status, scheduled_for, sent_at,
               followup_status, followup_due_at, followup_sent_at, error
        FROM polly_autopilot_queue
        WHERE creator_id = %s AND status <> 'skipped'
          AND (status IN ('draft', 'approved', 'failed') OR sent_at >= date_trunc('month', NOW()))
        ORDER BY CASE status WHEN 'draft' THEN 0 WHEN 'approved' THEN 1 WHEN 'failed' THEN 2 ELSE 3 END,
                 COALESCE(scheduled_for, sent_at, created_at)
        LIMIT %s
        """,
        (creator_id, limit),
    )
    out = []
    for r in cur.fetchall():
        item = dict(r)
        for key in ("scheduled_for", "sent_at", "followup_due_at", "followup_sent_at"):
            if item.get(key):
                item[key] = item[key].isoformat()
        out.append(item)
    return out


def update_draft(conn, creator_id: int, item_id: int, subject: str, body: str) -> bool:
    cur = _cursor(conn)
    cur.execute(
        """
        UPDATE polly_autopilot_queue SET subject = %s, body = %s
        WHERE id = %s AND creator_id = %s AND status IN ('draft', 'approved')
        """,
        ((subject or "").strip()[:250], (body or "").strip()[:8000], item_id, creator_id),
    )
    conn.commit()
    return cur.rowcount > 0


def skip_item(conn, creator_id: int, item_id: int) -> bool:
    cur = _cursor(conn)
    cur.execute(
        """
        UPDATE polly_autopilot_queue SET status = 'skipped', scheduled_for = NULL
        WHERE id = %s AND creator_id = %s AND status IN ('draft', 'approved', 'failed')
        """,
        (item_id, creator_id),
    )
    conn.commit()
    return cur.rowcount > 0


def next_slots(count: int, after: datetime, rng: Optional[random.Random] = None,
               days: Optional[List[int]] = None, per_day: int = 1) -> List[datetime]:
    """Send slots on the creator's send days in the afternoon UTC window (US morning, EU afternoon)."""
    rng = rng or random.Random()
    allowed = set(days or WEEKDAYS)
    slots: List[datetime] = []
    day = after.astimezone(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    while len(slots) < count:
        if day.weekday() in allowed:
            picked = []
            for _ in range(max(1, per_day)):
                slot = day.replace(hour=rng.randint(SEND_WINDOW_UTC[0], SEND_WINDOW_UTC[1] - 1),
                                   minute=rng.randint(0, 59))
                if slot > after and slot not in picked:
                    picked.append(slot)
            slots.extend(sorted(picked)[:count - len(slots)])
        day += timedelta(days=1)
    return slots


def approve(conn, creator_id: int, item_ids: List[int], now: Optional[datetime] = None) -> List[Dict[str, Any]]:
    """Schedule approved drafts after anything already queued, on the creator's send days."""
    now = now or utc_now()
    ids = [int(i) for i in item_ids or [] if str(i).isdigit()]
    if not ids:
        return []
    prefs = settings(conn, creator_id)
    days = prefs["send_days"]
    per_day = sends_per_day(prefs["monthly_target"], days)
    cur = _cursor(conn)
    cur.execute(
        "SELECT MAX(scheduled_for) AS last FROM polly_autopilot_queue WHERE creator_id = %s AND status = 'approved'",
        (creator_id,),
    )
    last = (cur.fetchone() or {}).get("last")
    start = max(now, last) if last else now
    cur.execute(
        "SELECT id FROM polly_autopilot_queue WHERE creator_id = %s AND status = 'draft' AND id = ANY(%s) ORDER BY id",
        (creator_id, ids),
    )
    rows = [r["id"] for r in cur.fetchall()]
    out = []
    for item_id, slot in zip(rows, next_slots(len(rows), start, days=days, per_day=per_day)):
        cur.execute(
            """
            UPDATE polly_autopilot_queue SET status = 'approved', approved_at = NOW(), scheduled_for = %s
            WHERE id = %s RETURNING id, brand_name, scheduled_for
            """,
            (slot, item_id),
        )
        row = cur.fetchone()
        if row:
            out.append({"id": row["id"], "brand_name": row["brand_name"], "scheduled_for": row["scheduled_for"].isoformat()})
    conn.commit()
    return out


# ---------------------------------------------------------------------------
# Follow-up copy
# ---------------------------------------------------------------------------

def compose_followup(first_name: str, brand_name: str, subject: str, wave: int = 1) -> Dict[str, str]:
    name = (brand_name or "your team").strip()
    subj = (subject or f"{name} collab").strip()
    if not subj.lower().startswith("re:"):
        subj = f"Re: {subj}"
    sign = (first_name or "").strip()
    if wave >= 2:
        middle = (
            f"One last nudge on this. If the timing isn't right for {name}, no worries at all. "
            "If it is, I'd love to send over a quick concept."
        )
    else:
        middle = (
            f"Bumping my note below in case it got buried. I'd still love to create for {name}. "
            "Happy to share a quick concept first if that helps."
        )
    body = "Hi,\n\n" + middle + "\n\nThanks!" + (f"\n{sign}" if sign else "")
    return {"subject": subj, "body": body}


# ---------------------------------------------------------------------------
# Cron
# ---------------------------------------------------------------------------

def _creator_names(conn, creator_id: int) -> Dict[str, str]:
    cur = _cursor(conn)
    cur.execute(
        "SELECT u.first_name, u.last_name FROM creators c JOIN users u ON u.id = c.user_id WHERE c.id = %s",
        (creator_id,),
    )
    row = cur.fetchone() or {}
    first = (row.get("first_name") or "").strip()
    last = (row.get("last_name") or "").strip()
    return {"first": first.split()[0].capitalize() if first else "", "full": " ".join(p for p in (first, last) if p)}


def _brand_closed(conn, creator_id: int, brand_id: int, since: Optional[datetime]) -> bool:
    cur = _cursor(conn)
    cur.execute(
        """
        SELECT 1 FROM polly_timeline_events
        WHERE creator_id = %s AND brand_id = %s AND event_type = ANY(%s)
          AND (%s::timestamptz IS NULL OR occurred_at >= %s::timestamptz)
        LIMIT 1
        """,
        (creator_id, brand_id, list(REPLY_EVENTS), since, since),
    )
    return cur.fetchone() is not None


def _already_pitched(conn, creator_id: int, brand_id: int) -> bool:
    cur = _cursor(conn)
    cur.execute(
        "SELECT 1 FROM polly_timeline_events WHERE creator_id = %s AND brand_id = %s AND event_type = 'pitch_sent' LIMIT 1",
        (creator_id, brand_id),
    )
    return cur.fetchone() is not None


def _followed_up_by_hand(conn, creator_id: int, brand_id: int, sent_at: Optional[datetime]) -> bool:
    """``sent_at`` is our last email in the thread, so our own follow-up events don't count."""
    if not sent_at:
        return False
    cur = _cursor(conn)
    cur.execute(
        """
        SELECT 1 FROM polly_timeline_events
        WHERE creator_id = %s AND brand_id = %s AND event_type = 'pitch_sent' AND occurred_at > %s
        LIMIT 1
        """,
        (creator_id, brand_id, sent_at + timedelta(hours=1)),
    )
    return cur.fetchone() is not None


def _hold_day4_nudge(conn, creator_id: int, brand_id: int, until: datetime) -> None:
    """Autopilot owns the day-4 follow-up, so the manual day-4 nudge waits."""
    from services.polly_tracker import OPEN_STATUSES

    cur = _cursor(conn)
    cur.execute(
        """
        UPDATE polly_tasks SET snoozed_until = %s
        WHERE creator_id = %s AND brand_id = %s AND type = 'follow_up_due'
          AND status = ANY(%s) AND COALESCE(metadata->>'wave', '4') = '4'
        """,
        (until, creator_id, brand_id, list(OPEN_STATUSES)),
    )
    conn.commit()


def _close_day4_task(conn, creator_id: int, brand_id: int) -> None:
    from services.polly_tracker import OPEN_STATUSES

    cur = _cursor(conn)
    cur.execute(
        """
        UPDATE polly_tasks SET status = 'completed', outcome = 'autopilot_followup'
        WHERE creator_id = %s AND brand_id = %s AND type = 'follow_up_due'
          AND status = ANY(%s) AND COALESCE(metadata->>'wave', '4') = '4'
        """,
        (creator_id, brand_id, list(OPEN_STATUSES)),
    )
    conn.commit()


def post_thread_note(conn, creator_id: int, text: str, chips: Optional[List[Dict]] = None,
                     pitched: Optional[Dict[str, Any]] = None) -> None:
    """Append a Polly note; ``pitched`` also marks that brand as pitched so chat stops suggesting it."""
    from services.polly import mark_pitched
    from services.polly_memory import load_thread, save_thread

    thread = load_thread(conn, creator_id)
    messages = list(thread.get("messages") or [])
    messages.append({"role": "assistant", "kind": "autopilot", "content": text, "task_chips": chips or []})
    notes = thread.get("notes") or {}
    if pitched:
        notes = mark_pitched(notes, pitched)
    save_thread(conn, creator_id, messages, thread.get("suggested_brands") or [], notes=notes)


def _default_headsup_email(conn, creator_id: int, brand_id: int, brand_name: str) -> None:
    from services.polly_alerts import (
        _default_send, _log_email, _recipient, _unsubscribe_url, chip_link, render_email,
    )
    from html import escape

    who = _recipient(conn, creator_id)
    if not who:
        return
    unsub = _unsubscribe_url(who["user_id"])
    subject = f"Tomorrow I follow up with {brand_name}"
    lines = [
        f"Your pitch to <strong>{escape(brand_name)}</strong> went out 3 days ago. "
        "Tomorrow I'll send a short follow-up in the same Gmail thread.",
        "If they already replied, tap below and I'll hold it.",
    ]
    buttons = [{"label": f"{brand_name} replied", "href": chip_link(
        "checkin_replied", brand_id=brand_id, brand_name=brand_name, kind="autopilot_headsup")}]
    result = _default_send(who["email"], subject, render_email(who["first"], subject, lines, buttons, unsub),
                           unsub, "autopilot_headsup")
    _log_email(conn, creator_id, "autopilot_headsup", brand_id, None, subject, result)


def drafts_note(brand_names: List[str]) -> str:
    names = ", ".join(f"**{n}**" for n in brand_names)
    count = len(brand_names)
    return (f"✍️ Autopilot update: I lined up {count} new pitch{'es' if count != 1 else ''}: {names}. "
            "They go out from your Gmail on your send days, and I'll follow up if they're quiet. "
            "Nothing for you to do. Tweak brands or target any time in **Autopilot** settings.")


def _default_drafts_email(conn, creator_id: int, brand_names: List[str]) -> None:
    from services.polly_alerts import POLLY_PATH, _default_send, _log_email, _recipient, _unsubscribe_url, frontend_url, render_email
    from html import escape

    who = _recipient(conn, creator_id)
    if not who or not brand_names:
        return
    unsub = _unsubscribe_url(who["user_id"])
    count = len(brand_names)
    subject = f"Autopilot lined up {count} more pitch{'es' if count != 1 else ''}"
    lines = [
        "I picked this week's brands and wrote your pitches: "
        + ", ".join(f"<strong>{escape(n)}</strong>" for n in brand_names) + ".",
        "They go out from your Gmail on your send days, and I follow up in the same thread if a brand is quiet. "
        "Nothing for you to do.",
    ]
    href = f"{frontend_url()}{POLLY_PATH}?autopilot=review&utm_source=email&utm_medium=polly_alert&utm_campaign=autopilot_drafts"
    buttons = [{"label": "See the schedule", "href": href}]
    result = _default_send(who["email"], subject, render_email(who["first"], subject, lines, buttons, unsub),
                           unsub, "autopilot_drafts")
    _log_email(conn, creator_id, "autopilot_drafts", None, None, subject, result)


def _due(conn, sql: str, params: tuple) -> List[Dict[str, Any]]:
    cur = _cursor(conn)
    cur.execute(sql, params)
    return [dict(r) for r in cur.fetchall()]


_ACTIVE_JOIN = """
    LEFT JOIN polly_autopilot a ON a.creator_id = q.creator_id
    JOIN polly_gmail_accounts g ON g.creator_id = q.creator_id AND g.revoked_at IS NULL
"""


def auto_approve_due(conn, now: Optional[datetime] = None) -> int:
    """Drafts nobody skipped within AUTO_APPROVE_AFTER get scheduled like a manual OK."""
    now = now or utc_now()
    rows = _due(conn, f"""
        SELECT q.creator_id, ARRAY_AGG(q.id ORDER BY q.id) AS ids
        FROM polly_autopilot_queue q {_ACTIVE_JOIN}
        WHERE q.status = 'draft' AND q.created_at <= %s AND COALESCE(a.enabled, TRUE)
        GROUP BY q.creator_id
    """, (now - AUTO_APPROVE_AFTER,))
    return sum(len(approve(conn, r["creator_id"], list(r["ids"]), now=now)) for r in rows)


def fill_candidates(conn, now: Optional[datetime] = None, limit: int = 20) -> List[Dict[str, Any]]:
    """Enabled creators with Gmail whose pipeline is short of a week's worth, least recently filled first."""
    ensure_autopilot_tables(conn)
    now = now or utc_now()
    rows = _due(conn, """
        SELECT a.creator_id, a.monthly_target, a.fill_blocked
        FROM polly_autopilot a
        JOIN polly_gmail_accounts g ON g.creator_id = a.creator_id AND g.revoked_at IS NULL
        WHERE a.enabled AND (a.filled_at IS NULL OR a.filled_at <= %s)
        ORDER BY a.filled_at NULLS FIRST
        LIMIT %s
    """, (now - AUTO_FILL_EVERY, limit))
    out = []
    for row in rows:
        row["monthly_target"] = effective_target(row["monthly_target"])
        need = refill_size(month_counts(conn, row["creator_id"]), row["monthly_target"])
        if need > 0:
            out.append({**row, "need": need})
        else:
            mark_filled(conn, row["creator_id"], now, None)
    return out


def claim_unannounced_drafts(conn, creator_id: int, now: datetime) -> List[str]:
    """Brand names of queued pitches the creator hasn't been told about yet, marked as told."""
    cur = _cursor(conn)
    cur.execute(
        """
        UPDATE polly_autopilot_queue SET announced_at = %s
        WHERE creator_id = %s AND status IN ('draft', 'approved') AND announced_at IS NULL
        RETURNING brand_name, scheduled_for
        """,
        (now, creator_id),
    )
    rows = sorted(cur.fetchall(), key=lambda r: r["scheduled_for"] or now)
    names = [r["brand_name"] or "a brand" for r in rows]
    conn.commit()
    return names


def mark_filled(conn, creator_id: int, now: datetime, blocked: Optional[str]) -> None:
    cur = _cursor(conn)
    cur.execute(
        "UPDATE polly_autopilot SET filled_at = %s, fill_blocked = %s WHERE creator_id = %s",
        (now, blocked, creator_id),
    )
    conn.commit()


def run_autopilot(
    conn,
    now: Optional[datetime] = None,
    limit: int = 40,
    send_fn: Optional[Callable] = None,
    headsup_fn: Optional[Callable] = None,
    note_fn: Optional[Callable] = None,
) -> Dict[str, int]:
    """Cron: send due pitches, warn a day before follow-ups, then send the follow-ups."""
    from services.polly_gmail import GmailError, ensure_gmail_table, send_email
    from services.polly_tracker import add_event, record_pitch_sent

    ensure_autopilot_tables(conn)
    ensure_gmail_table(conn)
    now = now or utc_now()
    send = send_fn or send_email
    headsup = headsup_fn or _default_headsup_email
    note = note_fn or post_thread_note
    out = {"approved": auto_approve_due(conn, now), "pitches": 0, "headsups": 0, "followups": 0,
           "skipped": 0, "failed": 0}

    pitches = _due(conn, f"""
        SELECT DISTINCT ON (q.creator_id) q.*, a.followups
        FROM polly_autopilot_queue q {_ACTIVE_JOIN}
        WHERE q.status = 'approved' AND q.scheduled_for <= %s AND COALESCE(a.enabled, TRUE)
          AND (SELECT COUNT(*) FROM polly_autopilot_queue s
               WHERE s.creator_id = q.creator_id AND s.sent_at >= %s) < %s
        ORDER BY q.creator_id, q.scheduled_for
        LIMIT %s
    """, (now, now - timedelta(hours=24), MAX_SENDS_PER_DAY, limit))
    for item in pitches:
        cid, bid, name = item["creator_id"], item["brand_id"], item.get("brand_name") or "the brand"
        if _already_pitched(conn, cid, bid):
            _set(conn, item["id"], status="skipped", error="already_pitched")
            out["skipped"] += 1
            continue
        try:
            sent = send(conn, cid, item["to_email"], item["subject"], item["body"],
                        from_name=_creator_names(conn, cid)["full"])
        except GmailError as err:
            _set(conn, item["id"], error=str(err)[:300], **({} if err.revoked else {"status": "failed"}))
            if err.revoked:
                note(conn, cid, "I couldn't send from your Gmail: access was removed. Reconnect it in "
                                "**Autopilot** and I'll pick up where I left off.", [])
            out["failed"] += 1
            continue
        follows = item.get("followups") if item.get("followups") is not None else DEFAULT_FOLLOWUPS
        _set(conn, item["id"], status="sent", sent_at=now, gmail_id=sent.get("gmail_id"),
             thread_id=sent.get("thread_id"), message_id=sent.get("message_id"), error=None,
             **({"followup_status": "scheduled", "followup_due_at": now + FOLLOWUP_GAPS[0]} if follows else {}))
        record_pitch_sent(conn, cid, {"id": bid, "name": name}, drafted=False)
        if follows:
            _hold_day4_nudge(conn, cid, bid, now + NUDGE_HOLD)
        tail = ", and I'll follow up on day 4 if they're quiet." if follows else "."
        note(conn, cid, f"✉️ Sent your **{name}** pitch from your Gmail. It's on your Timeline{tail}",
             [], pitched={"id": bid, "name": name})
        out["pitches"] += 1

    warn = _due(conn, f"""
        SELECT q.* FROM polly_autopilot_queue q {_ACTIVE_JOIN}
        WHERE q.followup_status = 'scheduled' AND q.followup_due_at - %s <= %s AND COALESCE(a.enabled, TRUE)
        LIMIT %s
    """, (HEADSUP_BEFORE, now, limit))
    for item in warn:
        cid, bid, name = item["creator_id"], item["brand_id"], item.get("brand_name") or "the brand"
        if _brand_closed(conn, cid, bid, item.get("sent_at")):
            _set(conn, item["id"], followup_status="cancelled")
            continue
        note(conn, cid, f"Tomorrow I follow up with **{name}** in the same Gmail thread. "
                        "If they already replied, tell me and I'll hold it.",
             [{"id": "checkin_replied", "label": f"{name} replied", "action": "task_act",
               "brand_id": bid, "brand_name": name}])
        try:
            headsup(conn, cid, bid, name)
        except Exception as err:
            print(f"[Polly autopilot] heads-up email skipped: {err}")
            conn.rollback()
        _set(conn, item["id"], followup_status="headsup", headsup_at=now,
             followup_due_at=max(item["followup_due_at"], now + HEADSUP_MIN_NOTICE))
        out["headsups"] += 1

    follow = _due(conn, f"""
        SELECT q.*, a.followups FROM polly_autopilot_queue q {_ACTIVE_JOIN}
        WHERE q.followup_status = 'headsup' AND q.followup_due_at <= %s AND COALESCE(a.enabled, TRUE)
        LIMIT %s
    """, (now, limit))
    for item in follow:
        cid, bid, name = item["creator_id"], item["brand_id"], item.get("brand_name") or "the brand"
        last_ours = item.get("followup_sent_at") or item.get("sent_at")
        if _brand_closed(conn, cid, bid, item.get("sent_at")) or _followed_up_by_hand(conn, cid, bid, last_ours):
            _set(conn, item["id"], followup_status="cancelled")
            continue
        wave = int(item.get("followups_sent") or 0) + 1
        names = _creator_names(conn, cid)
        mail = compose_followup(names["first"], name, item["subject"], wave=wave)
        try:
            send(conn, cid, item["to_email"], mail["subject"], mail["body"], from_name=names["full"],
                 thread_id=item.get("thread_id") or "", in_reply_to=item.get("message_id") or "")
        except GmailError as err:
            _set(conn, item["id"], error=str(err)[:300])
            out["failed"] += 1
            continue
        allowed = item.get("followups") if item.get("followups") is not None else DEFAULT_FOLLOWUPS
        if wave < min(int(allowed), len(FOLLOWUP_GAPS)):
            _set(conn, item["id"], followup_status="scheduled", followup_due_at=now + FOLLOWUP_GAPS[wave],
                 followup_sent_at=now, followups_sent=wave, error=None)
        else:
            _set(conn, item["id"], followup_status="sent", followup_sent_at=now, followups_sent=wave, error=None)
        if wave == 1:
            _close_day4_task(conn, cid, bid)
        add_event(conn, cid, "pitch_sent", f"Follow-up sent · {name}", brand_id=bid,
                  polly_notes="Sent by Polly Autopilot in the same Gmail thread.")
        note(conn, cid, f"🔁 Followed up with **{name}** in the same Gmail thread.", [])
        out["followups"] += 1
    return out


_SETTABLE = {"status", "sent_at", "gmail_id", "thread_id", "message_id", "error", "followup_status",
             "followup_due_at", "headsup_at", "followup_sent_at", "followups_sent"}


def _set(conn, item_id: int, **fields) -> None:
    cols = [k for k in fields if k in _SETTABLE]
    if not cols:
        return
    cur = _cursor(conn)
    cur.execute(
        f"UPDATE polly_autopilot_queue SET {', '.join(f'{c} = %s' for c in cols)} WHERE id = %s",
        tuple(fields[c] for c in cols) + (item_id,),
    )
    conn.commit()
