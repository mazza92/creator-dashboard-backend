"""Creator pain loop — diagnose stall, prescribe one fix.

Deterministic. The model writes the sentence; this picks cause + action.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Iterable, List, Optional


PAIN_NO_REPLIES = "no_replies"
PAIN_REJECTED = "rejected"
PAIN_BOUNCED = "bounced"
PAIN_QUIET_AFTER_VIEW = "quiet_after_view"
PAIN_NOT_SENT = "not_sent"
PAIN_KIT_INCOMPLETE = "kit_incomplete"
PAIN_INACTIVE = "inactive_feed"

FIX_FOLLOW_UP = "follow_up"
FIX_NEW_IN_NICHE = "new_in_niche"
FIX_VERIFY_EMAIL = "verify_email"
FIX_SEND_ONE = "send_one"
FIX_PUBLISH_KIT = "publish_kit"
FIX_POST = "post_this_week"

PAIN_LABELS = {
    PAIN_NO_REPLIES: "pitches out, no replies",
    PAIN_REJECTED: "a brand passed",
    PAIN_BOUNCED: "email bounced / wrong inbox",
    PAIN_QUIET_AFTER_VIEW: "they opened the kit, then went quiet",
    PAIN_NOT_SENT: "draft sitting unsent",
    PAIN_KIT_INCOMPLETE: "kit not live",
    PAIN_INACTIVE: "feed gone quiet",
}

FIX_LABELS = {
    FIX_FOLLOW_UP: "send the day-4 follow-up",
    FIX_NEW_IN_NICHE: "line up in-niche brands that actually reply",
    FIX_VERIFY_EMAIL: "don't keep pitching that inbox — next brand or a new contact",
    FIX_SEND_ONE: "send the draft that's waiting",
    FIX_PUBLISH_KIT: "publish My Kit so pitches have a page to land on",
    FIX_POST: "post something this week so the profile looks active",
}

CHECKIN_CHIPS = [
    {"id": "checkin_quiet", "label": "Still quiet — draft follow-up", "action": "task_act"},
    {"id": "checkin_replied", "label": "They replied", "action": "task_act"},
    {"id": "checkin_bounced", "label": "Email bounced", "action": "task_act"},
    {"id": "checkin_passed", "label": "They passed", "action": "task_act"},
    {"id": "checkin_not_sent", "label": "I never sent it", "action": "task_act"},
]

REPLY_KIND_CHIPS = [
    {"id": "checkin_interested", "label": "Interested / sending PR", "action": "task_act"},
    {"id": "checkin_question", "label": "They asked something", "action": "task_act"},
    {"id": "checkin_passed", "label": "They passed", "action": "task_act"},
]


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _as_dt(value: Any) -> Optional[datetime]:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if isinstance(value, str) and value:
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    return None


def checkin_chips(brand: Optional[Dict] = None, task_id: Any = None) -> List[Dict[str, Any]]:
    brand = brand or {}
    bid = brand.get("id") or brand.get("brand_id")
    name = brand.get("name") or brand.get("brand_name")
    out = []
    for chip in CHECKIN_CHIPS:
        item = dict(chip)
        item["brand_id"] = bid
        item["brand_name"] = name
        if task_id:
            item["task_id"] = task_id
        out.append(item)
    return out


def reply_kind_chips(brand: Optional[Dict] = None, task_id: Any = None) -> List[Dict[str, Any]]:
    brand = brand or {}
    bid = brand.get("id") or brand.get("brand_id")
    name = brand.get("name") or brand.get("brand_name")
    out = []
    for chip in REPLY_KIND_CHIPS:
        item = dict(chip)
        item["brand_id"] = bid
        item["brand_name"] = name
        if task_id:
            item["task_id"] = task_id
        out.append(item)
    return out


def make_pain(
    code: str,
    cause: str,
    fix: str,
    evidence: Optional[Iterable[str]] = None,
    brand_name: Optional[str] = None,
    brand_id: Any = None,
) -> Dict[str, Any]:
    bits = [str(e).strip() for e in (evidence or []) if e]
    return {
        "code": code,
        "label": PAIN_LABELS.get(code, code),
        "cause": cause,
        "fix": fix,
        "fix_label": FIX_LABELS.get(fix, fix),
        "evidence": bits[:4],
        "brand_name": brand_name,
        "brand_id": brand_id,
        "status": "open",
        "updated_at": utc_now().isoformat(),
    }


def _events(context: Optional[Dict]) -> List[Dict]:
    return [e for e in ((context or {}).get("recent_timeline") or []) if isinstance(e, dict)]


def _tasks(context: Optional[Dict]) -> List[Dict]:
    return [t for t in ((context or {}).get("active_tasks") or []) if isinstance(t, dict)]


def diagnose_pain(
    notes: Optional[Dict] = None,
    context: Optional[Dict] = None,
    kit: Optional[Dict] = None,
    scrape: Optional[Dict] = None,
) -> Optional[Dict[str, Any]]:
    """Pick the stall that matters most. Kit/feed only if outreach isn't the issue."""
    notes = notes or {}
    context = context or {}
    events = _events(context)
    active = _tasks(context)
    views = [e for e in events if e.get("event_type") == "portfolio_viewed"]
    rejections = [e for e in events if e.get("event_type") == "brand_replied_rejected"]
    bounces = [e for e in events if e.get("event_type") == "email_bounced"]
    pending = notes.get("pending_pitch") if isinstance(notes.get("pending_pitch"), dict) else None
    open_outreach = [
        t for t in active
        if (t.get("type") or "") in ("pitch_sent", "follow_up_due")
    ]

    if bounces:
        hit = bounces[0]
        return make_pain(
            PAIN_BOUNCED, "wrong_inbox", FIX_VERIFY_EMAIL,
            evidence=[hit.get("event_label") or "email bounced"],
            brand_name=hit.get("brand_name"),
            brand_id=hit.get("brand_id"),
        )

    view_ids = {e.get("brand_id") for e in views if e.get("brand_id")}
    quiet_view = next(
        (
            t for t in open_outreach
            if t.get("brand_id") and t.get("brand_id") in view_ids
        ),
        None,
    )
    if quiet_view:
        name = quiet_view.get("brand_name") or (views[0].get("brand_name") if views else None)
        return make_pain(
            PAIN_QUIET_AFTER_VIEW, "offer_or_fit", FIX_FOLLOW_UP,
            evidence=["kit opened, still no reply"],
            brand_name=name,
            brand_id=quiet_view.get("brand_id"),
        )

    if rejections and not any((t.get("type") or "") in ("pr_shipped", "pr_received", "reply_needed") for t in active):
        hit = rejections[0]
        return make_pain(
            PAIN_REJECTED, "mismatch", FIX_NEW_IN_NICHE,
            evidence=[hit.get("event_label") or "brand passed"],
            brand_name=hit.get("brand_name"),
            brand_id=hit.get("brand_id"),
        )

    if pending and (pending.get("name") or pending.get("brand_name")):
        return make_pain(
            PAIN_NOT_SENT, "activation", FIX_SEND_ONE,
            evidence=[f"draft waiting: {pending.get('name') or pending.get('brand_name')}"],
            brand_name=pending.get("name") or pending.get("brand_name"),
            brand_id=pending.get("id") or pending.get("brand_id"),
        )

    if open_outreach and not views:
        hit = open_outreach[0]
        return make_pain(
            PAIN_NO_REPLIES, "unopened_or_bounce", FIX_FOLLOW_UP,
            evidence=["pitch(es) out, no kit views yet"],
            brand_name=hit.get("brand_name"),
            brand_id=hit.get("brand_id"),
        )

    if kit and not kit.get("published"):
        return make_pain(
            PAIN_KIT_INCOMPLETE, "no_portfolio", FIX_PUBLISH_KIT,
            evidence=["My Kit is not live"],
        )

    last = None
    scrape = scrape or {}
    for key in ("last_posted_at", "last_post_at", "latest_post_at"):
        last = _as_dt(scrape.get(key))
        if last:
            break
    if last and utc_now() - last >= timedelta(days=7):
        return make_pain(
            PAIN_INACTIVE, "inactive_profile", FIX_POST,
            evidence=["no post in 7+ days"],
        )

    existing = notes.get("active_pain") if isinstance(notes.get("active_pain"), dict) else None
    if existing and existing.get("status") == "open" and existing.get("code"):
        return existing
    return None


def stamp_pain(notes: Optional[Dict], pain: Optional[Dict]) -> Dict[str, Any]:
    out = dict(notes or {})
    if not pain:
        return out
    prev = out.get("active_pain") if isinstance(out.get("active_pain"), dict) else None
    if (
        prev
        and prev.get("code") == pain.get("code")
        and prev.get("brand_id") == pain.get("brand_id")
        and prev.get("status") == "open"
    ):
        return out
    history = list(out.get("pain_history") or [])
    if prev and prev.get("code"):
        history.append({
            "code": prev.get("code"),
            "status": "superseded",
            "updated_at": prev.get("updated_at"),
        })
    out["active_pain"] = pain
    out["pain_history"] = history[-20:]
    out["updated_at"] = utc_now().isoformat()
    return out


def resolve_pain(notes: Optional[Dict], code: Optional[str] = None) -> Dict[str, Any]:
    out = dict(notes or {})
    current = out.get("active_pain") if isinstance(out.get("active_pain"), dict) else None
    if not current:
        return out
    if code and current.get("code") != code:
        return out
    current = dict(current)
    current["status"] = "resolved"
    current["updated_at"] = utc_now().isoformat()
    history = list(out.get("pain_history") or [])
    history.append(current)
    out["pain_history"] = history[-20:]
    out.pop("active_pain", None)
    out["updated_at"] = utc_now().isoformat()
    return out


def pain_context(notes: Optional[Dict] = None) -> str:
    pain = (notes or {}).get("active_pain") if isinstance((notes or {}).get("active_pain"), dict) else None
    if not pain or pain.get("status") == "resolved" or not pain.get("code"):
        return ""
    name = pain.get("brand_name") or ""
    brand = f" on {name}" if name else ""
    evidence = "; ".join(pain.get("evidence") or [])
    return (
        f"ACTIVE PAIN{brand}: {pain.get('label') or pain.get('code')}. "
        f"Cause: {pain.get('cause')}. Fix: {pain.get('fix_label') or pain.get('fix')}."
        + (f" Evidence: {evidence}." if evidence else "")
        + " Address this before spraying new brands unless they asked for more brands "
        "or the fix is lining up in-niche matches."
    )


def checkin_due(context: Optional[Dict] = None) -> Optional[Dict[str, Any]]:
    """Stale outreach that needs a pulse, not a new logo dump."""
    for task in (_tasks(context) + list((context or {}).get("due_soon") or [])):
        kind = task.get("type") or ""
        if kind not in ("follow_up_due", "pitch_sent"):
            continue
        due = _as_dt(task.get("due_at"))
        created = _as_dt(task.get("created_at"))
        stale = False
        if due and due <= utc_now():
            stale = True
        elif created and utc_now() - created >= timedelta(days=3):
            stale = True
        if not stale:
            continue
        return {
            "brand_id": task.get("brand_id"),
            "brand_name": task.get("brand_name") or (task.get("metadata") or {}).get("brand_name"),
            "task_id": task.get("id"),
            "type": kind,
        }
    return None
