"""State-based opener for creators who open Polly without typing.

Picks the single most useful thing Polly knows about their pipeline and leads
with it, instead of the generic "What do you want to land first?".
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from services.polly_discovery import GIFTED_LISTS_CHIP

LOCATION_ASK = (
    "One quick thing so every pitch is ready to send: what city and country "
    "should brands ship your PR to? (e.g. *Lyon, France*)"
)

KIT_VIEW_WINDOW = timedelta(days=7)


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


def _hello(first_name: Optional[str]) -> str:
    name = (first_name or "").strip()
    return f"Hey {name}" if name else "Hey"


def _recent_kit_view(tracker: Dict) -> Optional[Dict]:
    now = datetime.now(timezone.utc)
    for event in tracker.get("recent_timeline") or []:
        if (event.get("event_type") or "") != "portfolio_viewed":
            continue
        when = _as_dt(event.get("occurred_at"))
        if when and now - when > KIT_VIEW_WINDOW:
            continue
        if event.get("brand_name"):
            return event
    return None


def _applications_by_status(applications: Optional[List[Dict]], status: str) -> List[Dict]:
    seen = set()
    out = []
    for app in applications or []:
        if (str(app.get("status") or "review").strip().lower()) != status:
            continue
        key = app.get("brand_id") or app.get("brand_name")
        if key in seen:
            continue
        seen.add(key)
        out.append(app)
    return out


def _followup_chip(brand_id: Any, name: str) -> Dict[str, Any]:
    return {
        "id": "draft_followup",
        "label": f"Draft {name} follow-up",
        "hint": "Follow-ups never use a credit",
        "action": "generate_pitch",
        "brand_id": brand_id,
        "brand_name": name,
        "is_followup": True,
    }


def _replied_chip(brand_id: Any, name: str, task_id: Any = None) -> Dict[str, Any]:
    chip = {
        "id": "checkin_replied",
        "label": f"{name} replied",
        "action": "task_act",
        "brand_id": brand_id,
        "brand_name": name,
    }
    if task_id:
        chip["task_id"] = task_id
    return chip


def state_opener(
    first_name: Optional[str] = None,
    kit: Optional[Dict] = None,
    tracker: Optional[Dict] = None,
    notes: Optional[Dict] = None,
    applications: Optional[List[Dict]] = None,
) -> Optional[Dict[str, Any]]:
    """{"greeting", "starters", "state"} for the most urgent pipeline state, or None.

    ``applications`` are brand_pr_applications rows (brand_id, brand_name, status).
    """
    kit = kit or {}
    tracker = tracker or {}
    notes = notes or {}
    hello = _hello(first_name)

    selected = _applications_by_status(applications, "ships")
    if selected:
        name = selected[0].get("brand_name") or "A brand"
        return {
            "state": "selected",
            "greeting": (
                f"{hello} — **{name}** picked you for their gifted list. Your box is on the way. 🎉\n\n"
                "When it lands, film within a week. Posted content from a gifted box is "
                "exactly what gets you your first paid ask."
            ),
            "starters": [
                {"id": "need_idea", "label": f"Plan my {name} content", "action": "chat"},
                dict(GIFTED_LISTS_CHIP),
                {"id": "portfolio", "label": "Review my kit", "action": "coach_portfolio"},
            ],
        }

    view = _recent_kit_view(tracker)
    if view:
        name = view.get("brand_name")
        bid = view.get("brand_id")
        return {
            "state": "kit_view",
            "greeting": (
                f"{hello} — **{name}** opened your kit. That's a real look, not a bot.\n\n"
                "Brands decide fast, so a short follow-up now beats a perfect one next week. "
                "Want me to draft it?"
            ),
            "starters": [
                _followup_chip(bid, name),
                _replied_chip(bid, name),
                dict(GIFTED_LISTS_CHIP),
            ],
        }

    pulse = tracker.get("checkin_due") if isinstance(tracker.get("checkin_due"), dict) else None
    if pulse and pulse.get("brand_name"):
        from services.polly_pain import checkin_chips

        name = pulse["brand_name"]
        return {
            "state": "checkin",
            "greeting": (
                f"{hello} — quick one on **{name}**. Did they reply since you pitched them?\n\n"
                "Tap what happened and I'll take the next step."
            ),
            "starters": checkin_chips(
                {"id": pulse.get("brand_id"), "name": name},
                task_id=pulse.get("task_id"),
                early=bool(pulse.get("early")),
            )[:4],
        }

    pending = notes.get("pending_pitch") if isinstance(notes.get("pending_pitch"), dict) else None
    pending_name = str((pending or {}).get("name") or (pending or {}).get("brand_name") or "").strip()
    if pending_name:
        return {
            "state": "unsent_draft",
            "greeting": (
                f"{hello} — your **{pending_name}** pitch is written but not sent yet.\n\n"
                "Tap **Show my pitch**, then **Copy email** and **Copy pitch**, paste into "
                "your email and send. Tap **I sent it** after and I'll track the reply."
            ),
            "starters": [
                {
                    "id": "reshow_pitch",
                    "label": f"Show my {pending_name} pitch",
                    "action": "generate_pitch",
                    "brand_id": (pending or {}).get("id") or (pending or {}).get("brand_id"),
                    "brand_name": pending_name,
                },
                {
                    "id": "i_sent_it",
                    "label": "I sent it",
                    "action": "chat",
                    "brand_id": (pending or {}).get("id") or (pending or {}).get("brand_id"),
                    "brand_name": pending_name,
                },
                {"id": "more_brands", "label": "More brands", "action": "suggest_brands", "deal": "gifted"},
                dict(GIFTED_LISTS_CHIP),
            ],
        }

    apps = _applications_by_status(applications, "review")
    if apps:
        n = len(apps)
        lead = apps[0].get("brand_name") or "a brand"
        where = (
            f"you're on **{lead}**'s gifted list"
            if n == 1
            else f"you're on **{n}** gifted lists, including **{lead}**"
        )
        return {
            "state": "applications",
            "greeting": (
                f"{hello} — {where}. That means applied, still in review: "
                "the brand picks who gets a box, and they haven't picked yet.\n\n"
                "Best move while you wait: pitch one brand directly so you're not relying on a single list."
            ),
            "starters": [
                {
                    "id": "line_up",
                    "label": "Pitch 3 gifted brands for me today",
                    "hint": "I'll draft the emails",
                    "action": "suggest_brands",
                    "skip_discovery": True,
                    "deal": "gifted",
                },
                dict(GIFTED_LISTS_CHIP),
                {"id": "portfolio", "label": "Review my kit", "action": "coach_portfolio"},
            ],
        }

    if kit and not kit.get("published"):
        return {
            "state": "kit_not_live",
            "greeting": (
                f"{hello} — your kit isn't live yet, and every pitch links to it. "
                "Brands open it before they reply.\n\n"
                "It's a 2-minute fix: add 3 posts, a one-line bio, hit publish. "
                "Want me to walk you through it?"
            ),
            "starters": [
                {"id": "portfolio", "label": "Help me publish my kit", "action": "coach_portfolio"},
                dict(GIFTED_LISTS_CHIP),
                {
                    "id": "line_up",
                    "label": "Pitch 3 gifted brands for me today",
                    "action": "suggest_brands",
                    "skip_discovery": True,
                    "deal": "gifted",
                },
            ],
        }
    return None


def with_location_ask(greeting: str) -> str:
    text = (greeting or "").rstrip()
    if LOCATION_ASK.lower() in text.lower():
        return text
    return f"{text}\n\n{LOCATION_ASK}"


_NOT_A_PLACE = frozenset({
    "brand", "brands", "pitch", "pitches", "find", "show", "ugc", "gifted", "paid", "kit",
    "help", "more", "yes", "no", "ok", "okay", "thanks", "what", "how", "why", "send", "sent",
    "email", "later", "skip", "want", "need", "me", "my", "i", "pro", "credit", "credits",
})


def looks_like_place(text: str) -> bool:
    raw = (text or "").strip(" .!")
    if not raw or len(raw) > 60 or "?" in raw or any(ch.isdigit() for ch in raw):
        return False
    tokens = [t.strip(",.").lower() for t in raw.replace(",", " , ").split() if t.strip(",.")]
    if not tokens or len(tokens) > 6:
        return False
    return not any(t in _NOT_A_PLACE for t in tokens)


def single_city_reply(text: str, known_country: str = "") -> Optional[Dict[str, str]]:
    """'Lyon' when we already know the country from signup."""
    raw = (text or "").strip(" .!")
    country = (known_country or "").strip()
    if not raw or not country or not looks_like_place(raw):
        return None
    if "," in raw or len(raw.split()) > 2:
        return None
    return {"city": raw.title(), "country": country}
