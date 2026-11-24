"""Polly's recap right after Autopilot setup: what goes out today, the daily rhythm, follow-ups, the morning brief,
and where the schedule lives (Tracker). Posted once to the thread; it replaces the first-run welcome, which used to
reappear after setup for a creator who hadn't sent a pitch yet (Nov 2026)."""

from datetime import timedelta
from typing import Any, Dict

TRACKER_HREF = "/creator/dashboard/tracker"
RECAP_KEY = "autopilot_setup_recap"


def recap_text(today: int, per_day: int, trial: bool, first_name: str = "") -> str:
    hello = f"You're all set, {first_name}." if first_name else "You're all set."
    if trial:
        daily = ("**Every day:** I find new brands that fit you, including brands running UGC ads right now, and "
                 "build up to 20 pitches a day, weekends included.")
    else:
        daily = (f"**Every weekday:** I find new brands that fit you, including brands running UGC ads right now, "
                 f"and send up to {per_day} {'pitch' if per_day == 1 else 'pitches'}.")
    lines = [
        f"{hello} Here's how your outreach runs from now:",
        f"- **Today:** up to {today} {'pitch goes' if today == 1 else 'pitches go'} out from your Gmail, "
        "the first within the hour.",
        f"- {daily}",
        "- **4 days after each pitch:** a short follow-up in the same thread if the brand hasn't replied.",
        "- **Every morning:** your brief with what went out, who opened, and anything that needs you.",
        "Every pitch and its send time is in Tracker. Nothing for you to do today.",
    ]
    return "\n".join(lines)


def post_setup_recap(conn, creator_id: int, first_name: str = "") -> Dict[str, Any]:
    """Posts the recap (once) and closes the first-run welcome. Returns the message for the page to show now."""
    from services.creator_tracker import mark_milestone
    from services.polly_autopilot import (
        daily_sends, post_thread_note, sends_per_day, settings, utc_now,
    )

    prefs = settings(conn, creator_id)
    trial = bool(prefs.get("trial_started_at"))
    today = daily_sends(conn, creator_id, prefs, utc_now())
    per_day = sends_per_day(prefs["monthly_target"], prefs["send_days"])
    text = recap_text(today, per_day, trial, first_name)
    chips = [{"id": "open_tracker", "label": "See my schedule in Tracker", "action": "link", "href": TRACKER_HREF}]
    mark_milestone(conn, creator_id, "first_run_done")
    post_thread_note(conn, creator_id, text, chips=chips, dedupe_key=RECAP_KEY, within=timedelta(days=30))
    return {"role": "assistant", "kind": "autopilot", "content": text, "task_chips": chips}
