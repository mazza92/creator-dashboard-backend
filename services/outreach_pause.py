"""Pause / resume what Polly sends for a creator, for real (the database), so Polly never claims a pause it
didn't make (Nov 2026: a Pro creator said "don't send emails now" twice, Polly said "I've paused all pitches and
follow-ups", Autopilot sent three more pitches and she cancelled).

- pause everything: Autopilot off (the cron checks polly_autopilot.enabled before every pitch and follow-up).
- stop follow-ups only: every scheduled follow-up is cancelled and new pitches go out without follow-ups.
- resume: Autopilot back on.

Each call returns the state as stored afterwards, which is what Polly reports.
"""
from __future__ import annotations

from typing import Any, Dict

ACTIONS = ("pause_all", "stop_followups", "resume")


def outreach_state(conn, creator_id: int) -> Dict[str, Any]:
    from services.polly_autopilot import ensure_autopilot_tables

    ensure_autopilot_tables(conn)
    cur = conn.cursor()
    cur.execute("SELECT enabled, followups FROM polly_autopilot WHERE creator_id = %s", (creator_id,))
    row = cur.fetchone()
    cur.execute("""SELECT COUNT(*) FILTER (WHERE status IN ('approved', 'draft')),
                          COUNT(*) FILTER (WHERE status = 'sent' AND followup_status IN ('scheduled', 'headsup'))
                   FROM polly_autopilot_queue WHERE creator_id = %s""", (creator_id,))
    queued, followups_due = cur.fetchone() or (0, 0)
    return {
        "has_autopilot": row is not None,
        "autopilot_on": bool(row[0]) if row else False,
        "followups_on": (row[1] if row and row[1] is not None else 2) > 0,
        "pitches_waiting": int(queued or 0),
        "followups_scheduled": int(followups_due or 0),
    }


def apply(conn, creator_id: int, action: str) -> Dict[str, Any]:
    from services.polly_autopilot import set_enabled, update_settings

    if action not in ACTIONS:
        return {"ok": False, "error": f"action must be one of {', '.join(ACTIONS)}"}
    before = outreach_state(conn, creator_id)
    cur = conn.cursor()
    cancelled = 0
    if action == "pause_all":
        if before["has_autopilot"]:
            set_enabled(conn, creator_id, False)
    elif action == "stop_followups":
        cur.execute("""UPDATE polly_autopilot_queue SET followup_status = 'cancelled'
                       WHERE creator_id = %s AND followup_status IN ('scheduled', 'headsup')""", (creator_id,))
        cancelled = cur.rowcount or 0
        conn.commit()
        if before["has_autopilot"]:
            update_settings(conn, creator_id, {"followups": 0})
    elif action == "resume":
        if before["has_autopilot"]:
            set_enabled(conn, creator_id, True)
            if not before["followups_on"]:
                from services.polly_autopilot import DEFAULT_FOLLOWUPS
                update_settings(conn, creator_id, {"followups": DEFAULT_FOLLOWUPS})
    after = outreach_state(conn, creator_id)
    return {"ok": True, "action": action, "followups_cancelled": cancelled, **after}
