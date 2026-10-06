"""Polly as manager: the gifted-to-paid ladder, the monthly plan, and work done between visits."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from psycopg2.extras import RealDictCursor

UNSENT_MIN_AGE = timedelta(minutes=30)
UNSENT_MAX_AGE = timedelta(days=7)
AWAY_BRIEF_GAP = timedelta(hours=20)

STAGES = (
    {"key": "starter", "label": "Getting started"},
    {"key": "pitching", "label": "Pitching"},
    {"key": "first_yes", "label": "First yes"},
    {"key": "gifted", "label": "Gifted creator"},
    {"key": "paid_ready", "label": "Paid-ready"},
    {"key": "paid", "label": "Paid UGC creator"},
)

PAID_READY_POSTS = 3
PRO_PITCH_GOAL = 10

COUNT_KEYS = (
    "pitched", "pitched_month", "replies", "replies_month", "boxes",
    "posted", "posted_month", "paid_pitched", "paid", "kit_views_month",
)

MONTH_KEYS = ("pitched", "kit_views", "replies", "posted", "paid")


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def month_key(now: Optional[datetime] = None) -> str:
    return (now or utc_now()).strftime("%Y-%m")


def _cursor(conn):
    return conn.cursor(cursor_factory=RealDictCursor)


def _parse_iso(value: Any) -> Optional[datetime]:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


# ---------------------------------------------------------------------------
# Unsent drafts
# ---------------------------------------------------------------------------

def unsent_drafts(conn, creator_id: int, limit: int = 3) -> List[Dict[str, Any]]:
    """Pitches Polly wrote that never left the chat (no Open/Copy, not logged as sent)."""
    from services.polly_alerts import ensure_alert_tables

    ensure_alert_tables(conn)
    cur = _cursor(conn)
    cur.execute(
        """
        SELECT d.id, d.brand_id, COALESCE(d.brand_name, b.brand_name) AS brand_name, d.drafted_at
        FROM polly_drafts d
        LEFT JOIN pr_brands b ON b.id = d.brand_id
        WHERE d.creator_id = %s
          AND d.handoff_at IS NULL
          AND d.refunded_at IS NULL
          AND d.drafted_at <= NOW() - %s
          AND d.drafted_at >= NOW() - %s
          AND NOT EXISTS (
              SELECT 1 FROM polly_timeline_events e
              WHERE e.creator_id = d.creator_id AND e.brand_id = d.brand_id
                AND e.event_type IN ('pitch_sent', 'pitch_not_sent', 'brand_replied_interested',
                                     'brand_replied_question', 'brand_replied_rejected')
          )
        ORDER BY d.drafted_at DESC
        LIMIT %s
        """,
        (creator_id, UNSENT_MIN_AGE, UNSENT_MAX_AGE, int(limit)),
    )
    return [
        {
            "draft_id": r["id"],
            "brand_id": r["brand_id"],
            "brand_name": r.get("brand_name") or "this brand",
        }
        for r in cur.fetchall()
    ]


def send_draft_chip(draft: Dict[str, Any]) -> Dict[str, Any]:
    name = draft.get("brand_name") or "this brand"
    return {
        "id": "send_draft",
        "label": f"Send my {name} pitch",
        "action": "generate_pitch",
        "brand_id": draft.get("brand_id"),
        "brand_name": name,
    }


# ---------------------------------------------------------------------------
# Career ladder
# ---------------------------------------------------------------------------

_COUNTS_SQL = """
    SELECT
      COUNT(DISTINCT brand_id) FILTER (WHERE event_type = 'pitch_sent') AS pitched,
      COUNT(DISTINCT brand_id) FILTER (
          WHERE event_type = 'pitch_sent' AND occurred_at >= date_trunc('month', NOW())) AS pitched_month,
      COUNT(DISTINCT brand_id) FILTER (
          WHERE event_type IN ('brand_replied_interested', 'brand_replied_question')) AS replies,
      COUNT(DISTINCT brand_id) FILTER (
          WHERE event_type IN ('brand_replied_interested', 'brand_replied_question')
            AND occurred_at >= date_trunc('month', NOW())) AS replies_month,
      COUNT(DISTINCT brand_id) FILTER (WHERE event_type IN ('pr_shipped', 'pr_received')) AS boxes,
      COUNT(DISTINCT brand_id) FILTER (WHERE event_type = 'content_posted') AS posted,
      COUNT(DISTINCT brand_id) FILTER (
          WHERE event_type = 'content_posted' AND occurred_at >= date_trunc('month', NOW())) AS posted_month,
      COUNT(DISTINCT brand_id) FILTER (WHERE event_type = 'paid_pitch_sent') AS paid_pitched,
      COUNT(*) FILTER (WHERE event_type = 'milestone' AND event_label LIKE 'Paid%%') AS paid,
      COUNT(*) FILTER (
          WHERE event_type = 'portfolio_viewed' AND occurred_at >= date_trunc('month', NOW())) AS kit_views_month
    FROM polly_timeline_events
    WHERE creator_id = %s
"""


def load_career_counts(conn, creator_id: int) -> Dict[str, int]:
    cur = _cursor(conn)
    cur.execute(_COUNTS_SQL, (creator_id,))
    row = cur.fetchone() or {}
    return {k: int(row.get(k) or 0) for k in COUNT_KEYS}


def _goal(stage_key: str, pitch_cap: Optional[int]) -> Dict[str, int]:
    if stage_key in ("starter", "pitching"):
        goal = {"pitches": PRO_PITCH_GOAL, "replies": 2, "posts": 0}
    elif stage_key == "first_yes":
        goal = {"pitches": 8, "replies": 2, "posts": 1}
    elif stage_key == "gifted":
        goal = {"pitches": 8, "replies": 2, "posts": 2}
    else:
        goal = {"pitches": 6, "replies": 2, "posts": 1}
    if pitch_cap is not None:
        goal["pitches"] = max(1, min(goal["pitches"], int(pitch_cap)))
    return goal


def _next_step(key: str, c: Dict[str, int], has_rates: bool) -> Dict[str, Any]:
    gifted_chip = {"id": "line_up", "action": "suggest_brands", "skip_discovery": True, "deal": "gifted"}
    if key == "starter":
        return {
            "label": "Send your first pitch",
            "why": "Every paid UGC creator started with a gifted yes.",
            "chip": dict(gifted_chip, label="Pitch 3 gifted brands for me"),
        }
    if key == "pitching":
        return {
            "label": "Get your first yes",
            "why": "Replies come from steady pitching plus day-4 follow-ups.",
            "chip": dict(gifted_chip, label="Line up brands to pitch"),
        }
    if key == "first_yes":
        return {
            "label": "Turn the yes into a posted collab",
            "why": "Once the box lands, that post is your first portfolio piece.",
            "chip": {"id": "need_idea", "label": "Plan my content", "action": "chat"},
        }
    if key == "gifted":
        left = max(0, PAID_READY_POSTS - c["posted"])
        return {
            "label": f"{left} more posted collab{'s' if left != 1 else ''} to paid-ready",
            "why": "Three posted gifted collabs is the proof paid brands ask for.",
            "chip": dict(gifted_chip, label="Pitch my next gifted brand"),
        }
    if key == "paid_ready":
        if not has_rates:
            return {
                "label": "Set your rates",
                "why": "Paid pitches need a number. I'll build it from your posted collabs.",
                "chip": {"id": "rates", "label": "Build my rate card", "action": "coach_rates"},
            }
        return {
            "label": "Pitch your first paid deal",
            "why": "Lead with your posted results, then your rate.",
            "chip": {"id": "paid_ugc", "label": "Show paid UGC I can pitch", "action": "suggest_gigs",
                     "skip_discovery": True},
        }
    return {
        "label": "Turn paid brands into repeat clients",
        "why": "A monthly retainer beats chasing a new brand every week.",
        "chip": {"id": "paid_ugc", "label": "Find my next paid deal", "action": "suggest_gigs",
                 "skip_discovery": True},
    }


def career_from_counts(
    counts: Optional[Dict[str, Any]] = None,
    notes: Optional[Dict] = None,
    pitch_cap: Optional[int] = None,
) -> Dict[str, Any]:
    """Where the creator is on gifted -> paid, this month's goal, and the one next step.

    ``pitch_cap`` is the free plan's monthly credit limit; Pro passes None.
    """
    c = {k: int((counts or {}).get(k) or 0) for k in COUNT_KEYS}
    notes = notes or {}
    established = (notes.get("polly_track") or "") == "established"
    has_rates = bool(notes.get("rate_card") or notes.get("rates_set_at"))

    if c["paid"] > 0:
        key = "paid"
    elif c["posted"] >= PAID_READY_POSTS or (established and c["posted"] >= 1):
        key = "paid_ready"
    elif c["posted"] > 0:
        key = "gifted"
    elif c["replies"] > 0 or c["boxes"] > 0:
        key = "first_yes"
    elif c["pitched"] > 0:
        key = "pitching"
    else:
        key = "starter"
    index = next(i for i, s in enumerate(STAGES) if s["key"] == key)
    goal = _goal(key, pitch_cap)
    return {
        "stage": key,
        "stage_index": index,
        "stage_label": STAGES[index]["label"],
        "stages": [s["label"] for s in STAGES],
        "next": _next_step(key, c, has_rates),
        "month": {
            "pitches": c["pitched_month"],
            "pitches_goal": goal["pitches"],
            "replies": c["replies_month"],
            "replies_goal": goal["replies"],
            "posts": c["posted_month"],
            "posts_goal": goal["posts"],
            "kit_views": c["kit_views_month"],
        },
        "capped": pitch_cap is not None,
        "totals": c,
    }


def pitch_cap_for(balance: Optional[Dict[str, Any]]) -> Optional[int]:
    balance = balance or {}
    if balance.get("is_unlimited"):
        return None
    try:
        return int(balance.get("limit") or 3)
    except (TypeError, ValueError):
        return 3


def career_snapshot(conn, creator_id: int, notes: Optional[Dict] = None,
                    balance: Optional[Dict[str, Any]] = None) -> Optional[Dict[str, Any]]:
    try:
        return career_from_counts(load_career_counts(conn, creator_id), notes, pitch_cap_for(balance))
    except Exception as err:
        print(f"[Polly manager] career skipped: {err}")
        try:
            conn.rollback()
        except Exception:
            pass
        return None


def career_context(career: Optional[Dict[str, Any]]) -> str:
    """One block for the brain so replies reference the ladder and this month's goal."""
    if not career:
        return ""
    m = career.get("month") or {}
    t = career.get("totals") or {}
    return (
        "Career ladder (gifted -> paid UGC):\n"
        f"- Stage {career.get('stage_index', 0) + 1} of {len(STAGES)}: {career.get('stage_label')}.\n"
        f"- This month: {m.get('pitches', 0)}/{m.get('pitches_goal', 0)} pitches sent, "
        f"{m.get('replies', 0)}/{m.get('replies_goal', 0)} replies, {m.get('posts', 0)} posted collabs, "
        f"{m.get('kit_views', 0)} kit views.\n"
        f"- All time: {t.get('pitched', 0)} pitched, {t.get('replies', 0)} replies, "
        f"{t.get('posted', 0)} posted collabs, {t.get('paid', 0)} paid deals.\n"
        f"- Next step: {(career.get('next') or {}).get('label')}. Steer toward it when it fits."
    )


# ---------------------------------------------------------------------------
# Month report card + this month's plan
# ---------------------------------------------------------------------------

def load_last_month(conn, creator_id: int) -> Dict[str, int]:
    cur = _cursor(conn)
    cur.execute(
        """
        SELECT
          COUNT(DISTINCT brand_id) FILTER (WHERE event_type = 'pitch_sent') AS pitched,
          COUNT(*) FILTER (WHERE event_type = 'portfolio_viewed') AS kit_views,
          COUNT(DISTINCT brand_id) FILTER (
              WHERE event_type IN ('brand_replied_interested', 'brand_replied_question')) AS replies,
          COUNT(DISTINCT brand_id) FILTER (WHERE event_type = 'content_posted') AS posted,
          COUNT(*) FILTER (WHERE event_type = 'milestone' AND event_label LIKE 'Paid%%') AS paid
        FROM polly_timeline_events
        WHERE creator_id = %s
          AND occurred_at >= date_trunc('month', NOW()) - INTERVAL '1 month'
          AND occurred_at < date_trunc('month', NOW())
        """,
        (creator_id,),
    )
    row = cur.fetchone() or {}
    return {k: int(row.get(k) or 0) for k in MONTH_KEYS}


_PLURALS = {"pitch": "pitches", "reply": "replies"}


def _plural(n: int, word: str) -> str:
    return f"{n} {word if n == 1 else _PLURALS.get(word, word + 's')}"


def month_report_lines(last: Dict[str, int], is_pro: bool, cap: Optional[int] = None,
                       matched_left: int = 0) -> List[str]:
    """Last month in numbers. Empty when nothing happened."""
    last = {k: int((last or {}).get(k) or 0) for k in MONTH_KEYS}
    if not any(last.values()):
        return []
    lines = [
        f"Last month: **{_plural(last['pitched'], 'pitch')}** sent, "
        f"**{_plural(last['kit_views'], 'kit view')}**, **{_plural(last['replies'], 'reply')}**"
        + (f", **{_plural(last['posted'], 'posted collab')}**" if last["posted"] else "")
        + (f", **{_plural(last['paid'], 'paid deal')}**" if last["paid"] else "")
        + "."
    ]
    if last["replies"] and last["pitched"]:
        lines.append(f"That's {last['replies']} in {last['pitched']} brands answering. Pitching works for you.")
    elif last["kit_views"] and not last["replies"]:
        lines.append("Brands looked but didn't answer. The day-4 follow-up is where those turn into replies.")
    if not is_pro and cap and last["pitched"] >= cap and matched_left > 0:
        lines.append(
            f"🔒 You hit your {cap} free pitches. {_plural(matched_left, 'more brand')} matched you "
            "that you couldn't pitch. Pro has no cap."
        )
    return lines


def month_plan_message(first: str, career: Dict[str, Any], last: Dict[str, int], is_pro: bool,
                       brands: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
    """First open of the month: report card, the goal, and brands to pitch toward it."""
    m = career.get("month") or {}
    cap = None if is_pro else m.get("pitches_goal")
    report = month_report_lines(last, is_pro, cap=cap, matched_left=len(brands or []))
    brands = list(brands or [])[:5]
    goal_bits = [f"**{_plural(int(m.get('pitches_goal') or 0), 'pitch')}**"]
    if m.get("replies_goal"):
        goal_bits.append(f"**{_plural(int(m['replies_goal']), 'reply')}**")
    if m.get("posts_goal"):
        goal_bits.append(f"**{_plural(int(m['posts_goal']), 'posted collab')}**")
    name = (first or "").strip()
    head = f"New month{', ' + name if name else ''}. Here's the plan."
    goal = (
        f"You're at **{career.get('stage_label')}** (stage {int(career.get('stage_index') or 0) + 1} of "
        f"{len(STAGES)}). This month's goal: {', '.join(goal_bits)}."
    )
    nxt = career.get("next") or {}
    parts = [head]
    parts.extend(report)
    parts.append(goal)
    if brands:
        parts.append(
            f"I picked {_plural(len(brands), 'brand')} that fit you. Tap **Contact** on one and I'll write the pitch."
        )
    elif nxt.get("why"):
        parts.append(nxt["why"])
    chips = [nxt["chip"]] if nxt.get("chip") else []
    return {
        "role": "assistant",
        "kind": "brief",
        "content": "\n\n".join(parts),
        "brands": brands,
        "task_chips": chips,
    }


# ---------------------------------------------------------------------------
# While you were away
# ---------------------------------------------------------------------------

def away_items(tracker: Optional[Dict[str, Any]], drafts: List[Dict[str, Any]],
               since: Any = None) -> Dict[str, Any]:
    """What happened (or is waiting) since the last brief: drafts, kit views, follow-ups due."""
    tracker = tracker or {}
    since = since if isinstance(since, datetime) else _parse_iso(since)
    views = []
    seen = set()
    for e in tracker.get("recent_timeline") or []:
        if (e.get("event_type") or "") != "portfolio_viewed":
            continue
        at = _parse_iso(e.get("occurred_at"))
        if since and at and at <= since:
            continue
        key = e.get("brand_id") or e.get("brand_name")
        if key in seen:
            continue
        seen.add(key)
        views.append(e)
    followups = [
        t for t in (tracker.get("due_soon") or [])
        if (t.get("type") or "") == "follow_up_due" and t.get("brand_name")
    ]
    return {"drafts": drafts or [], "views": views[:3], "followups": followups[:3]}


def away_brief_message(items: Dict[str, Any], career: Optional[Dict[str, Any]] = None,
                       early: Optional[Dict[str, datetime]] = None) -> Optional[Dict[str, Any]]:
    """``early`` maps brand id / lowercased name to when its follow-up unlocks (4 days after sending)."""
    drafts = items.get("drafts") or []
    views = items.get("views") or []
    followups = items.get("followups") or []
    if not (drafts or views or followups):
        return None
    early = early or {}
    lines = ["While you were away:"]
    chips: List[Dict[str, Any]] = []
    for v in views:
        name = v.get("brand_name") or "A brand"
        ready = early.get(str(v.get("brand_id") or "")) or early.get(name.strip().lower())
        if ready:
            lines.append(
                f"👀 **{name}** opened your kit. Good sign. Give them until "
                f"**{ready.strftime('%A')}** to reply, then I'll have the follow-up ready."
            )
            continue
        lines.append(f"👀 **{name}** opened your kit. Follow up while you're on their mind.")
        if v.get("brand_id") and len(chips) < 3:
            chips.append({
                "id": "draft_followup", "label": f"Follow up with {name}", "action": "generate_pitch",
                "brand_id": v.get("brand_id"), "brand_name": name, "is_followup": True,
            })
    for t in followups:
        name = t.get("brand_name")
        lines.append(f"🔔 Day-4 follow-up due for **{name}**. It's free and it's where most replies come from.")
        if len(chips) < 3:
            chips.append({
                "id": "draft_followup", "label": f"Draft {name} follow-up", "action": "generate_pitch",
                "brand_id": t.get("brand_id"), "brand_name": name, "task_id": t.get("id"), "is_followup": True,
            })
    for d in drafts:
        lines.append(f"📝 Your **{d.get('brand_name')}** pitch is written but not sent.")
        if len(chips) < 3:
            chips.append(send_draft_chip(d))
    m = (career or {}).get("month") or {}
    if m.get("pitches_goal"):
        lines.append(f"Month so far: {m.get('pitches', 0)}/{m.get('pitches_goal')} pitches sent.")
    return {
        "role": "assistant",
        "kind": "brief",
        "content": "\n\n".join(lines),
        "task_chips": chips,
    }


def plan_open_note(notes: Dict[str, Any], has_thread: bool, now: Optional[datetime] = None) -> Optional[str]:
    """Which note to post on this open: 'month', 'away', or None."""
    now = now or utc_now()
    if has_thread and notes.get("month_plan_month") != month_key(now):
        return "month"
    last = _parse_iso(notes.get("away_brief_at"))
    if has_thread and (not last or now - last >= AWAY_BRIEF_GAP):
        return "away"
    return None
