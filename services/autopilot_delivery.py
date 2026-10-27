"""Daily delivery check for Pro Autopilot: Pro is sold as pitches every weekday, so a send day with nothing
sent is a product failure, not noise. After the day's send window, every enabled creator with Gmail connected
whose send day it was and who got 0 pitches out is recorded with the cause, and the team gets one digest.
"""
from __future__ import annotations

import os
from datetime import date, datetime, timedelta, timezone
from html import escape
from typing import Any, Callable, Dict, List, Optional

from psycopg2.extras import RealDictCursor

from services.polly_autopilot import SEND_WINDOW_UTC, ensure_autopilot_tables, parse_send_days

TEAM_EMAIL = os.getenv("AUTOPILOT_ALERT_EMAIL") or "team@newcollab.co"


def ensure_table(conn) -> None:
    cur = conn.cursor()
    cur.execute("""CREATE TABLE IF NOT EXISTS polly_autopilot_misses (
                     day DATE NOT NULL, creator_id INTEGER NOT NULL, cause TEXT NOT NULL, detail TEXT,
                     created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(), PRIMARY KEY (day, creator_id))""")
    conn.commit()


def _cause(conn, creator_id: int, day: date, fill_blocked: Optional[str]) -> Dict[str, str]:
    cur = conn.cursor(cursor_factory=RealDictCursor)
    start = datetime(day.year, day.month, day.day, tzinfo=timezone.utc)
    end = start + timedelta(days=1)
    cur.execute("""SELECT error FROM polly_autopilot_queue WHERE creator_id = %s AND error IS NOT NULL
                   AND scheduled_for >= %s AND scheduled_for < %s ORDER BY id DESC LIMIT 1""", (creator_id, start, end))
    err = cur.fetchone()
    if err:
        return {"cause": "send_error", "detail": str(err["error"])[:200]}
    cur.execute("""SELECT COUNT(*) FILTER (WHERE status = 'approved' AND scheduled_for >= %s) AS later,
                          COUNT(*) FILTER (WHERE status IN ('draft', 'approved')) AS queued
                   FROM polly_autopilot_queue WHERE creator_id = %s""", (end, creator_id))
    row = cur.fetchone() or {}
    if int(row.get("queued") or 0) == 0:
        return {"cause": "no_brands", "detail": fill_blocked or "no pitches written or queued"}
    if int(row.get("later") or 0):
        return {"cause": "scheduled_later", "detail": f"{row['later']} queued for later days"}
    return {"cause": "unknown", "detail": "pitches queued but none were due today"}


def record_misses(conn, day: date) -> List[Dict[str, Any]]:
    """Creators who should have sent on `day` and didn't, with the cause. Idempotent per day."""
    ensure_autopilot_tables(conn)
    ensure_table(conn)
    start = datetime(day.year, day.month, day.day, tzinfo=timezone.utc)
    cur = conn.cursor(cursor_factory=RealDictCursor)
    cur.execute("""
        SELECT a.creator_id, a.send_days, a.fill_blocked, g.connected_at,
               (SELECT COUNT(*) FROM polly_autopilot_queue q WHERE q.creator_id = a.creator_id
                  AND q.sent_at >= %s AND q.sent_at < %s) AS sent
        FROM polly_autopilot a
        JOIN polly_gmail_accounts g ON g.creator_id = a.creator_id AND g.revoked_at IS NULL
        WHERE a.enabled""", (start, start + timedelta(days=1)))
    misses = []
    for r in cur.fetchall():
        if day.weekday() not in parse_send_days(r["send_days"]) or int(r["sent"] or 0) > 0:
            continue
        connected = r["connected_at"]
        if connected and (connected if connected.tzinfo else connected.replace(tzinfo=timezone.utc)) >= start:
            continue  # connected that day: the first-send path covers it
        c = _cause(conn, r["creator_id"], day, r.get("fill_blocked"))
        misses.append({"creator_id": r["creator_id"], **c})
    wc = conn.cursor()
    for m in misses:
        wc.execute("""INSERT INTO polly_autopilot_misses (day, creator_id, cause, detail) VALUES (%s, %s, %s, %s)
                      ON CONFLICT (day, creator_id) DO NOTHING""", (day, m["creator_id"], m["cause"], m["detail"]))
    conn.commit()
    return misses


def due(now: datetime) -> bool:
    """After the day's send window has closed (UTC)."""
    return now.hour >= SEND_WINDOW_UTC[1] + 1


def daily_check(conn, now: Optional[datetime] = None, send_fn: Optional[Callable] = None) -> Dict[str, Any]:
    """From the alerts cron: once per day after the send window, record misses and email the team."""
    now = now or datetime.now(timezone.utc)
    if not due(now):
        return {"skipped": "window_open"}
    ensure_table(conn)
    day = now.date()
    cur = conn.cursor()
    cur.execute("SELECT 1 FROM polly_autopilot_misses WHERE day = %s AND creator_id = 0", (day,))
    if cur.fetchone():
        return {"skipped": "done_today"}
    misses = record_misses(conn, day)
    cur.execute("INSERT INTO polly_autopilot_misses (day, creator_id, cause) VALUES (%s, 0, 'checked') "
                "ON CONFLICT DO NOTHING", (day,))
    conn.commit()
    if misses:
        from services.resend_mail import send_resend_email

        send = send_fn or send_resend_email
        counts: Dict[str, int] = {}
        for m in misses:
            counts[m["cause"]] = counts.get(m["cause"], 0) + 1
        rows = "".join(f"<li>creator {m['creator_id']}: <b>{escape(m['cause'])}</b> · {escape(m['detail'] or '')}</li>"
                       for m in misses[:60])
        summary = ", ".join(f"{k}: {v}" for k, v in sorted(counts.items(), key=lambda x: -x[1]))
        send(TEAM_EMAIL, f"Autopilot: {len(misses)} Pro creators sent nothing on {day.isoformat()}",
             f"<p>Weekday with zero pitches sent, Gmail connected and Autopilot on.</p><p>{escape(summary)}</p><ul>{rows}</ul>")
    return {"day": day.isoformat(), "misses": len(misses)}
