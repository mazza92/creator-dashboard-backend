"""Polly Autopilot (Pro): approved pitches go out from the creator's Gmail on their send days,
with up to two follow-ups (day 4, day 10) in the same thread unless the brand replied."""

from __future__ import annotations

import random
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Dict, List, Optional

from psycopg2.extras import RealDictCursor

DEFAULT_MONTHLY_TARGET = 24
WEEK_BATCH = 6
TARGET_MIN = 8
TARGET_MAX = 30
MAX_SENDS_PER_DAY = 2
SEND_WINDOW_UTC = (14, 17)
WEEKDAYS = (0, 1, 2, 3, 4)
DEAL_FOCUS = ("auto", "gifted", "paid")
DEFAULT_FOLLOWUPS = 2
# Gap before each follow-up: day 4 after the pitch, then 6 more days (day 10).
FOLLOWUP_GAPS = (timedelta(days=4), timedelta(days=6))
FOLLOWUP_AFTER = FOLLOWUP_GAPS[0]
HEADSUP_BEFORE = timedelta(days=1)
HEADSUP_MIN_NOTICE = timedelta(hours=20)
NUDGE_HOLD = timedelta(days=5)

REPLIED_EVENTS = ("brand_replied_interested", "brand_replied_question", "brand_replied_rejected")
REPLY_EVENTS = REPLIED_EVENTS + ("dropped", "email_bounced", "pitch_not_sent")

_TABLES_READY = False


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
            monthly_target INTEGER NOT NULL DEFAULT 24,
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
    cur.execute(
        "CREATE INDEX IF NOT EXISTS idx_polly_autopilot_due ON polly_autopilot_queue (status, scheduled_for)"
    )
    cur.execute("ALTER TABLE polly_autopilot ADD COLUMN IF NOT EXISTS deal_focus TEXT NOT NULL DEFAULT 'auto'")
    cur.execute("ALTER TABLE polly_autopilot ADD COLUMN IF NOT EXISTS send_days TEXT NOT NULL DEFAULT '01234'")
    cur.execute(
        f"ALTER TABLE polly_autopilot ADD COLUMN IF NOT EXISTS followups INTEGER NOT NULL DEFAULT {DEFAULT_FOLLOWUPS}"
    )
    cur.execute("ALTER TABLE polly_autopilot_queue ADD COLUMN IF NOT EXISTS followups_sent INTEGER NOT NULL DEFAULT 0")
    conn.commit()
    _TABLES_READY = True


# ---------------------------------------------------------------------------
# Settings + queue
# ---------------------------------------------------------------------------

def parse_send_days(raw: Any) -> List[int]:
    days = sorted({int(ch) for ch in str(raw or "") if ch.isdigit() and int(ch) in WEEKDAYS})
    return days or list(WEEKDAYS)


def clamp_target(target: Any) -> int:
    try:
        return max(TARGET_MIN, min(TARGET_MAX, int(target)))
    except (TypeError, ValueError):
        return DEFAULT_MONTHLY_TARGET


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
        "monthly_target": int(row["monthly_target"]) if row else DEFAULT_MONTHLY_TARGET,
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


def sends_per_day(target: int, days: List[int]) -> int:
    """One a send day, two when the week's batch doesn't fit."""
    return 1 if per_week(target) <= len(days or WEEKDAYS) else MAX_SENDS_PER_DAY


def add_draft(conn, creator_id: int, brand_id: int, brand_name: str, to_email: str,
              subject: str, body: str) -> Dict[str, Any]:
    ensure_autopilot_tables(conn)
    cur = _cursor(conn)
    cur.execute(
        """
        INSERT INTO polly_autopilot_queue (creator_id, brand_id, brand_name, to_email, subject, body, status)
        VALUES (%s, %s, %s, %s, %s, %s, 'draft')
        ON CONFLICT (creator_id, brand_id) DO UPDATE
          SET brand_name = EXCLUDED.brand_name, to_email = EXCLUDED.to_email, subject = EXCLUDED.subject,
              body = EXCLUDED.body, status = 'draft', error = NULL, created_at = NOW()
          WHERE polly_autopilot_queue.status IN ('draft', 'skipped', 'failed')
        RETURNING id, brand_id, brand_name, to_email, subject, body, status
        """,
        (creator_id, brand_id, brand_name, to_email, subject, body),
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
    messages.append({"role": "assistant", "kind": "alert", "content": text, "task_chips": chips or []})
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


def _due(conn, sql: str, params: tuple) -> List[Dict[str, Any]]:
    cur = _cursor(conn)
    cur.execute(sql, params)
    return [dict(r) for r in cur.fetchall()]


_ACTIVE_JOIN = """
    LEFT JOIN polly_autopilot a ON a.creator_id = q.creator_id
    JOIN polly_gmail_accounts g ON g.creator_id = q.creator_id AND g.revoked_at IS NULL
"""


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
    out = {"pitches": 0, "headsups": 0, "followups": 0, "skipped": 0, "failed": 0}

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
