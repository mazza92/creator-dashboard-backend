"""Polly task tracker — lifecycle intents, tasks, timeline, nudges.

Integer IDs (creators.id / pr_brands.id). Does not invent brands.
"""

from __future__ import annotations

import json
import os
import re
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

from psycopg2.extras import Json, RealDictCursor


TASK_TYPES = (
    "pitch_sent",
    "follow_up_due",
    "brand_replied",
    "reply_needed",
    "pr_shipped",
    "pr_received",
    "content_due",
    "content_posted",
    "paid_pitch_sent",
    "retainer_active",
    "portfolio_incomplete",
    "bio_needs_update",
    "ratecard_missing",
    "idle_re_engage",
    "campaign_applied",
)

OPEN_STATUSES = ("pending", "in_progress")
CLOSED_STATUSES = ("completed", "dropped", "snoozed")

LIFECYCLE_INTENTS = (
    "pitch_sent",
    "brand_replied_interested",
    "brand_replied_rejected",
    "brand_replied_question",
    "pr_shipped",
    "pr_received",
    "content_posted",
    "offer_paid_deal",
    "payment_received",
    "portfolio_published",
    "ask_for_brands",
    "ask_for_help",
    "email_bounced",
    "pitch_not_sent",
    "still_quiet",
    "casual_chat",
    "no_action",
)

EVENT_ICONS = {
    "pitch_sent": "📤",
    "pitch_drafted": "📤",
    "follow_up_due": "🔔",
    "follow_up_nudge": "🔔",
    "brand_replied_interested": "🟢",
    "brand_replied_rejected": "🔴",
    "brand_replied_question": "🟡",
    "pr_shipped": "📦",
    "pr_received": "📬",
    "content_due": "⏰",
    "content_posted": "🎥",
    "metrics_updated": "📊",
    "paid_pitch_sent": "💰",
    "retainer_active": "⭐",
    "portfolio_viewed": "👀",
    "email_bounced": "📭",
    "pitch_not_sent": "📝",
    "polly_nudge_sent": "💬",
    "milestone": "🎉",
    "dropped": "⚪",
    "campaign_applied": "🎁",
}

NUDGE_COPY = {
    "follow_up_d4": (
        "Quick pulse on **{brand}** — it's been a few days. "
        "Any reply, a bounce, or still quiet? Tap what happened and I'll take the next step."
    ),
    "follow_up_d10": (
        "Ok last one for **{brand}**. Day 10, still quiet. I'd send one more nudge "
        "with a fresh piece of content attached — brands respond to new value. "
        "Try it, or shall we move on?"
    ),
    "follow_up_d14": (
        "**{brand}** stayed quiet. That's on them, not you. I'm marking it dropped "
        "and we can line up brands that are actually recruiting. Want me to pull them?"
    ),
    "reply_needed": (
        "Quick nudge — **{brand}** replied and speed matters here. Brands cool off fast. "
        "Want me to help you craft the reply?"
    ),
    "pr_shipped": (
        "Did your **{brand}** box arrive yet? Just tracking — if it's been a week+ "
        "and nothing, we might need to chase them for tracking info."
    ),
    "content_due": (
        "**{brand}** PR's been at yours 2 weeks. Time to film — brands do check. "
        "Even a quick 15s GRWM keeps you in good standing. What's blocking you?"
    ),
}

NUDGE_CHIPS = {
    "follow_up_d4": [
        {"id": "checkin_quiet", "label": "Still quiet — draft follow-up", "action": "task_act"},
        {"id": "checkin_replied", "label": "They replied", "action": "task_act"},
        {"id": "checkin_bounced", "label": "Email bounced", "action": "task_act"},
        {"id": "checkin_passed", "label": "They passed", "action": "task_act"},
        {"id": "checkin_not_sent", "label": "I never sent it", "action": "task_act"},
    ],
    "follow_up_d10": [
        {"id": "draft_followup", "label": "Draft final nudge", "action": "generate_pitch", "is_followup": True},
        {"id": "move_on", "label": "Move on to next brand", "action": "task_act"},
    ],
    "follow_up_d14": [
        {"id": "line_up", "label": "Yes, line them up", "action": "suggest_brands"},
        {"id": "line_up_more", "label": "Show me more options", "action": "suggest_brands"},
    ],
    "reply_needed": [
        {"id": "help_reply", "label": "Help me reply", "action": "chat"},
        {"id": "snooze_24h", "label": "I got it, hang on", "action": "task_act"},
    ],
    "pr_shipped": [
        {"id": "pr_arrived", "label": "Yes arrived", "action": "task_act"},
        {"id": "pr_not_yet", "label": "Not yet", "action": "task_act"},
        {"id": "pr_nothing", "label": "Nothing at all", "action": "task_act"},
    ],
    "content_due": [
        {"id": "need_idea", "label": "Need content idea", "action": "chat"},
        {"id": "filming_weekend", "label": "Filming this weekend", "action": "task_act"},
        {"id": "ran_out", "label": "Ran out of time", "action": "task_act"},
    ],
}

_PITCH_SENT = re.compile(
    r"\b(sent it|i('ve| have) sent|just sent|fired it off|email sent|"
    r"got it out|that's it|ok done|done zo)\b|^(done|sent)[\s!.]*$",
    re.I,
)
_NOT_SENT = re.compile(
    r"\b(never sent|didn'?t send|did not send|haven'?t sent|not actually sent|i never sent)\b",
    re.I,
)
_BOUNCE = re.compile(
    r"\b(bounced|bounce back|undelivered|wrong (email|inbox)|invalid email)\b",
    re.I,
)
_STILL_QUIET = re.compile(
    r"\b(still quiet|no reply yet|nothing back|radio silence|they('ve| have)? gone quiet)\b",
    re.I,
)
_INTERESTED = re.compile(
    r"\b(said yes|they'?re in|interested|wants? to send( pr)?|positive reply|"
    r"they want to (collab|move forward)|sending pr)\b",
    re.I,
)
_REJECTED = re.compile(
    r"\b(said no|not interested|rejected|they passed|not right now|they declined)\b",
    re.I,
)
_QUESTION = re.compile(
    r"\b(asked me|want to know|need more info|replied with questions|asked about)\b",
    re.I,
)
_SHIPPED = re.compile(r"\b(shipped|tracking|on the way|shipping now)\b", re.I)
_RECEIVED = re.compile(
    r"\b(arrived|got (the )?box|box is here|product arrived|just landed|pr (is )?here)\b",
    re.I,
)
_POSTED = re.compile(
    r"\b(posted it|video is live|just went up|content is out|here'?s the link)\b",
    re.I,
)
_PAID_PITCH = re.compile(
    r"\b(pitch( them)? paid|ready to charge|ask for money|paid deal|paid ugc)\b",
    re.I,
)
_PAYMENT = re.compile(r"\b(got paid|money landed|invoice paid|payment came)\b", re.I)
_KIT = re.compile(r"\b(kit is live|portfolio published|finished my kit|published my kit)\b", re.I)
_ASK_BRANDS = re.compile(
    r"\b(any new brands|who should i pitch|line up brands|what brands should i|"
    r"more brands|show me more|another brand|next brand)\b",
    re.I,
)
_ASK_HELP = re.compile(r"\b(how do i|help me with|any advice on|what should i do about)\b", re.I)
_CASUAL = re.compile(r"^(hi|hey|hello|morning|yo|how are you)[\s!.]*$", re.I)


def utc_now() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


def ensure_tracker_tables(cursor, conn) -> None:
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS polly_tasks (
            id SERIAL PRIMARY KEY,
            creator_id INTEGER NOT NULL,
            brand_id INTEGER,
            parent_task_id INTEGER,
            type TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending',
            priority INTEGER NOT NULL DEFAULT 3,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            due_at TIMESTAMPTZ,
            completed_at TIMESTAMPTZ,
            snoozed_until TIMESTAMPTZ,
            outcome TEXT,
            metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
            polly_last_nudge_at TIMESTAMPTZ,
            polly_nudge_count INTEGER NOT NULL DEFAULT 0
        )
        """
    )
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS polly_timeline_events (
            id SERIAL PRIMARY KEY,
            creator_id INTEGER NOT NULL,
            brand_id INTEGER,
            task_id INTEGER,
            event_type TEXT NOT NULL,
            event_label TEXT NOT NULL,
            event_icon TEXT,
            event_data JSONB NOT NULL DEFAULT '{}'::jsonb,
            polly_notes TEXT,
            occurred_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """
    )
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS polly_intent_logs (
            id SERIAL PRIMARY KEY,
            creator_id INTEGER NOT NULL,
            user_message TEXT NOT NULL,
            detected_intent TEXT NOT NULL,
            detected_brand_id INTEGER,
            confidence DOUBLE PRECISION NOT NULL DEFAULT 0,
            action_taken TEXT,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """
    )
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_polly_tasks_creator_status ON polly_tasks (creator_id, status)"
    )
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_polly_timeline_creator ON polly_timeline_events (creator_id, occurred_at DESC)"
    )
    conn.commit()


def _int(val) -> Optional[int]:
    try:
        n = int(val)
        return n if n else None
    except (TypeError, ValueError):
        return None


def lookup_brand(conn, brand_id: Any = None, name: Optional[str] = None) -> Optional[Dict[str, Any]]:
    cur = _cursor(conn)
    bid = _int(brand_id)
    if bid:
        cur.execute(
            "SELECT id, brand_name AS name, logo_url, category, slug FROM pr_brands WHERE id = %s",
            (bid,),
        )
        row = cur.fetchone()
        if row:
            return dict(row)
    needle = (name or "").strip()
    if needle:
        cur.execute(
            """
            SELECT id, brand_name AS name, logo_url, category, slug
            FROM pr_brands
            WHERE LOWER(brand_name) = LOWER(%s)
               OR brand_name ILIKE %s
            ORDER BY CASE WHEN LOWER(brand_name) = LOWER(%s) THEN 0 ELSE 1 END, id
            LIMIT 1
            """,
            (needle, f"%{needle}%", needle),
        )
        row = cur.fetchone()
        if row:
            return dict(row)
    return None


def brand_from_notes(notes: Optional[Dict] = None) -> Optional[Dict[str, Any]]:
    notes = notes or {}
    ids = list(notes.get("pitched_brand_ids") or [])
    names = list(notes.get("pitched_brand_names") or [])
    if not ids and not names:
        return None
    bid = ids[-1] if ids else None
    name = names[-1] if names else None
    return {"id": bid, "brand_id": bid, "name": name, "brand_name": name}


def backfill_from_notes(conn, creator_id: int, notes: Optional[Dict] = None) -> int:
    """Create tracker rows for pitches Polly already marked in notes."""
    notes = notes or {}
    ids = list(notes.get("pitched_brand_ids") or [])
    names = list(notes.get("pitched_brand_names") or [])
    added = 0
    for i, raw_id in enumerate(ids):
        bid = _int(raw_id)
        if not bid:
            continue
        name = names[i] if i < len(names) else None
        open_f = _open_tasks(conn, creator_id, ["follow_up_due", "pitch_sent"], bid)
        if open_f:
            continue
        cur = _cursor(conn)
        cur.execute(
            "SELECT id FROM polly_tasks WHERE creator_id = %s AND brand_id = %s LIMIT 1",
            (creator_id, bid),
        )
        if cur.fetchone():
            continue
        record_pitch_sent(
            conn, creator_id,
            {"id": bid, "name": name or f"Brand {bid}"},
            drafted=False,
        )
        added += 1
    return added


def match_brand_name(text: str, brands: Optional[List[Dict]] = None) -> Optional[Dict[str, Any]]:
    low = (text or "").lower()
    best = None
    best_len = 0
    for brand in brands or []:
        name = str(brand.get("name") or brand.get("brand_name") or "").strip()
        if len(name) < 2:
            continue
        if name.lower() in low and len(name) > best_len:
            best = brand
            best_len = len(name)
    return best


def classify_lifecycle_heuristic(
    text: str,
    brands: Optional[List[Dict]] = None,
    last_pitch: Optional[Dict] = None,
) -> Dict[str, Any]:
    raw = (text or "").strip()
    brand = match_brand_name(raw, brands) or (last_pitch if last_pitch else None)
    brand_name = (brand or {}).get("name") or (brand or {}).get("brand_name")
    brand_id = _int((brand or {}).get("id") or (brand or {}).get("brand_id"))

    def hit(intent: str, confidence: float) -> Dict[str, Any]:
        return {
            "intent": intent,
            "brand": brand_name,
            "brand_id": brand_id,
            "confidence": confidence,
            "reasoning": "heuristic",
        }

    if _CASUAL.match(raw):
        return hit("casual_chat", 0.9)
    if _REJECTED.search(raw):
        return hit("brand_replied_rejected", 0.86)
    if _INTERESTED.search(raw):
        return hit("brand_replied_interested", 0.86)
    if _QUESTION.search(raw):
        return hit("brand_replied_question", 0.8)
    if _RECEIVED.search(raw):
        return hit("pr_received", 0.85)
    if _SHIPPED.search(raw):
        return hit("pr_shipped", 0.82)
    if _POSTED.search(raw):
        return hit("content_posted", 0.84)
    if _PAYMENT.search(raw):
        return hit("payment_received", 0.88)
    if _PAID_PITCH.search(raw):
        return hit("offer_paid_deal", 0.8)
    if _KIT.search(raw):
        return hit("portfolio_published", 0.85)
    if _ASK_BRANDS.search(raw):
        return hit("ask_for_brands", 0.9)
    if _ASK_HELP.search(raw):
        return hit("ask_for_help", 0.75)
    if _NOT_SENT.search(raw):
        return hit("pitch_not_sent", 0.9 if brand else 0.78)
    if _BOUNCE.search(raw):
        return hit("email_bounced", 0.88 if brand else 0.75)
    if _STILL_QUIET.search(raw):
        return hit("still_quiet", 0.82 if brand else 0.7)
    if _PITCH_SENT.search(raw):
        return hit("pitch_sent", 0.84 if brand else 0.7)
    return hit("no_action", 0.4)


def _portfolio_views(context: Optional[Dict] = None) -> List[Dict]:
    context = context or {}
    recent = context.get("recent_timeline") or []
    return [e for e in recent if (e.get("event_type") or "") == "portfolio_viewed"]


def session_context_text(context: Optional[Dict] = None) -> str:
    context = context or {}
    bits = []
    views = _portfolio_views(context)
    if views:
        bits.append(
            "KIT VIEWS (hot — follow up, do not ignore): "
            + "; ".join(
                f"{e.get('brand_name') or e.get('event_label') or 'a brand'} opened the kit"
                for e in views[:4]
            )
        )
    due = context.get("due_soon") or []
    active = context.get("active_tasks") or []
    if due:
        bits.append("Due in 24h: " + "; ".join(
            f"{t.get('type')} {t.get('brand_name') or ''}".strip() for t in due[:5]
        ))
    if active:
        bits.append("Open tasks: " + "; ".join(
            f"{t.get('type')} {t.get('brand_name') or ''} ({t.get('status')})"
            for t in active[:8]
        ))
    recent = context.get("recent_timeline") or []
    if recent:
        bits.append("Recent: " + "; ".join(
            f"{e.get('event_label')}" for e in recent[:6]
        ))
    pain = context.get("active_pain")
    if isinstance(pain, dict) and pain.get("code"):
        bits.insert(
            0,
            "ACTIVE PAIN: "
            f"{pain.get('label') or pain.get('code')} — fix: {pain.get('fix_label') or pain.get('fix')}. "
            "Do not ignore this to spray new brands.",
        )
    due_check = context.get("checkin_due")
    if isinstance(due_check, dict) and due_check.get("brand_name"):
        bits.insert(
            0,
            f"CHECK-IN DUE: pulse {due_check.get('brand_name')} "
            "(replied / quiet / bounced / passed / never sent). Do not assume it was sent.",
        )
    if not bits:
        return "No open brand relationships yet."
    return "TASK TRACKER (real state — reference these, do not invent):\n" + "\n".join(f"- {b}" for b in bits)


def _cursor(conn):
    cur = conn.cursor(cursor_factory=RealDictCursor)
    ensure_tracker_tables(cur, conn)
    return cur


def log_intent(conn, creator_id: int, message: str, parsed: Dict, action: str = "") -> None:
    cur = _cursor(conn)
    cur.execute(
        """
        INSERT INTO polly_intent_logs
            (creator_id, user_message, detected_intent, detected_brand_id, confidence, action_taken)
        VALUES (%s, %s, %s, %s, %s, %s)
        """,
        (
            creator_id,
            (message or "")[:2000],
            parsed.get("intent") or "no_action",
            _int(parsed.get("brand_id")),
            float(parsed.get("confidence") or 0),
            (action or "")[:240],
        ),
    )
    conn.commit()


def add_event(
    conn,
    creator_id: int,
    event_type: str,
    event_label: str,
    brand_id: Optional[int] = None,
    task_id: Optional[int] = None,
    event_data: Optional[Dict] = None,
    polly_notes: Optional[str] = None,
) -> int:
    cur = _cursor(conn)
    cur.execute(
        """
        INSERT INTO polly_timeline_events
            (creator_id, brand_id, task_id, event_type, event_label, event_icon, event_data, polly_notes)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
        RETURNING id
        """,
        (
            creator_id,
            brand_id,
            task_id,
            event_type,
            event_label,
            EVENT_ICONS.get(event_type, "💬"),
            Json(event_data or {}),
            polly_notes,
        ),
    )
    row = cur.fetchone()
    conn.commit()
    return int(row["id"]) if row else 0


def create_task(
    conn,
    creator_id: int,
    task_type: str,
    brand_id: Optional[int] = None,
    parent_id: Optional[int] = None,
    status: str = "pending",
    priority: int = 3,
    due_at: Optional[datetime] = None,
    metadata: Optional[Dict] = None,
    outcome: Optional[str] = None,
) -> int:
    cur = _cursor(conn)
    cur.execute(
        """
        INSERT INTO polly_tasks
            (creator_id, brand_id, parent_task_id, type, status, priority, due_at, metadata, outcome,
             completed_at)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        RETURNING id
        """,
        (
            creator_id,
            brand_id,
            parent_id,
            task_type,
            status,
            priority,
            due_at,
            Json(metadata or {}),
            outcome,
            utc_now() if status == "completed" else None,
        ),
    )
    row = cur.fetchone()
    conn.commit()
    return int(row["id"]) if row else 0


def _open_tasks(conn, creator_id: int, types: Optional[List[str]] = None, brand_id: Optional[int] = None) -> List[Dict]:
    cur = _cursor(conn)
    sql = """
        SELECT t.*, b.brand_name AS brand_name, b.logo_url, b.category, b.slug
        FROM polly_tasks t
        LEFT JOIN pr_brands b ON b.id = t.brand_id
        WHERE t.creator_id = %s AND t.status = ANY(%s)
    """
    params: List[Any] = [creator_id, list(OPEN_STATUSES)]
    if types:
        sql += " AND t.type = ANY(%s)"
        params.append(types)
    if brand_id:
        sql += " AND t.brand_id = %s"
        params.append(brand_id)
    sql += " ORDER BY t.due_at NULLS LAST, t.priority ASC, t.id DESC"
    cur.execute(sql, params)
    return [dict(r) for r in cur.fetchall()]


def close_tasks(
    conn,
    creator_id: int,
    types: List[str],
    brand_id: Optional[int],
    status: str = "completed",
    outcome: Optional[str] = None,
) -> int:
    cur = _cursor(conn)
    sql = """
        UPDATE polly_tasks
        SET status = %s, outcome = COALESCE(%s, outcome), completed_at = NOW()
        WHERE creator_id = %s AND type = ANY(%s) AND status = ANY(%s)
    """
    params: List[Any] = [status, outcome, creator_id, types, list(OPEN_STATUSES)]
    if brand_id:
        sql += " AND brand_id = %s"
        params.append(brand_id)
    cur.execute(sql, params)
    conn.commit()
    return cur.rowcount or 0


def has_open_followups(conn, creator_id: int, brand_id: Optional[int]) -> bool:
    return bool(_open_tasks(conn, creator_id, ["follow_up_due", "pitch_sent"], brand_id))


def record_pitch_sent(
    conn,
    creator_id: int,
    brand: Optional[Dict],
    drafted: bool = False,
) -> Dict[str, Any]:
    brand = brand or {}
    found = lookup_brand(conn, brand.get("id") or brand.get("brand_id"), brand.get("name") or brand.get("brand_name"))
    if found:
        brand_id = found["id"]
        name = found["name"]
    else:
        brand_id = _int(brand.get("id") or brand.get("brand_id"))
        name = str(brand.get("name") or brand.get("brand_name") or "this brand").strip()
    print(f"[Polly tracker] pitch_sent creator={creator_id} brand_id={brand_id} name={name!r} drafted={drafted}")
    existing = _open_tasks(conn, creator_id, ["follow_up_due"], brand_id) if brand_id else []
    parent = create_task(
        conn, creator_id, "pitch_sent", brand_id=brand_id,
        status="completed", priority=2, outcome="success" if not drafted else None,
        metadata={"brand_name": name, "drafted": drafted},
    )
    add_event(
        conn, creator_id,
        "pitch_sent" if not drafted else "pitch_drafted",
        f"Pitch sent · {name}" if not drafted else f"Pitch drafted · {name}",
        brand_id=brand_id, task_id=parent,
        event_data={"brand_name": name},
    )
    if existing:
        return {"task_id": parent, "brand_id": brand_id, "created_followups": False}
    now = utc_now()
    d4 = create_task(
        conn, creator_id, "follow_up_due", brand_id=brand_id, parent_id=parent,
        priority=2, due_at=now + timedelta(days=4),
        metadata={"brand_name": name, "wave": 4},
    )
    create_task(
        conn, creator_id, "follow_up_due", brand_id=brand_id, parent_id=parent,
        priority=2, due_at=now + timedelta(days=10),
        metadata={"brand_name": name, "wave": 10},
    )
    add_event(
        conn, creator_id, "follow_up_due",
        f"Follow-up scheduled · {name}",
        brand_id=brand_id, task_id=d4,
        polly_notes="Day 4 then day 10, then we drop.",
    )
    return {"task_id": parent, "brand_id": brand_id, "created_followups": True}


def record_campaign_applied(
    conn,
    creator_id: int,
    brand: Optional[Dict] = None,
) -> Dict[str, Any]:
    """Log a gifted-PR apply onto the creator Timeline. Idempotent per brand."""
    brand = brand or {}
    found = lookup_brand(conn, brand.get("id") or brand.get("brand_id"), brand.get("name") or brand.get("brand_name"))
    if found:
        brand_id = found["id"]
        name = found["name"]
    else:
        brand_id = _int(brand.get("id") or brand.get("brand_id"))
        name = str(brand.get("name") or brand.get("brand_name") or "this brand").strip()
    if not brand_id:
        return {"ok": False}
    cur = _cursor(conn)
    cur.execute(
        """
        SELECT id FROM polly_timeline_events
        WHERE creator_id = %s AND brand_id = %s AND event_type = 'campaign_applied'
        LIMIT 1
        """,
        (creator_id, brand_id),
    )
    if cur.fetchone():
        return {"ok": True, "brand_id": brand_id, "duplicate": True}
    existing_tasks = _open_tasks(conn, creator_id, ["campaign_applied"], brand_id)
    task_id = existing_tasks[0]["id"] if existing_tasks else create_task(
        conn, creator_id, "campaign_applied", brand_id=brand_id,
        status="pending", priority=3,
        metadata={
            "brand_name": name,
            "application_id": brand.get("application_id"),
            "source": "apply",
        },
    )
    add_event(
        conn, creator_id, "campaign_applied",
        f"Applied for gifted PR · {name}",
        brand_id=brand_id, task_id=task_id,
        event_data={"brand_name": name, "source": "apply"},
        polly_notes="You're on their gifted list. They pick who gets the box.",
    )
    return {"ok": True, "brand_id": brand_id, "task_id": task_id}


def portfolio_view_alert(brand_name: str, brand_id: Optional[int] = None) -> Dict[str, Any]:
    name = (brand_name or "this brand").strip() or "this brand"
    return {
        "message": (
            f"**{name}** just opened your kit from the pitch. That's a real look — "
            "follow up while you're top of mind. Want me to draft a short bump?"
        ),
        "chips": [
            {
                "id": "draft_followup",
                "label": f"Draft {name} follow-up",
                "action": "generate_pitch",
                "brand_id": brand_id,
                "brand_name": name,
                "is_followup": True,
            }
        ],
    }


def _inject_portfolio_view_alert(
    conn, creator_id: int, brand_id: int, name: str
) -> bool:
    from services.polly_memory import load_thread, save_thread

    thread = load_thread(conn, creator_id)
    messages = list(thread.get("messages") or [])
    last = messages[-1] if messages else None
    last_text = ((last or {}).get("content") or "").lower()
    if (
        last
        and last.get("role") == "assistant"
        and last.get("kind") in ("alert", "nudge")
        and name.lower() in last_text
        and "opened your kit" in last_text
    ):
        return False
    alert = portfolio_view_alert(name, brand_id)
    messages.append({
        "role": "assistant",
        "content": alert["message"],
        "task_chips": alert["chips"],
        "kind": "alert",
    })
    notes = dict(thread.get("notes") or {})
    notes["last_kit_view"] = {"brand_id": brand_id, "brand_name": name}
    save_thread(
        conn,
        creator_id,
        messages,
        thread.get("suggested_brands") or [],
        notes=notes,
    )
    return True


def record_portfolio_viewed(
    conn,
    creator_id: int,
    brand_id: Any,
    brand_name: Optional[str] = None,
    source: str = "kit_ref",
) -> Dict[str, Any]:
    """Log a brand kit view on the Polly timeline and ping the chat thread."""
    bid = _int(brand_id)
    if not creator_id or not bid:
        return {"recorded": False, "alerted": False}
    found = lookup_brand(conn, bid, brand_name)
    name = str((found or {}).get("name") or brand_name or "this brand").strip() or "this brand"
    cur = _cursor(conn)
    cur.execute(
        """
        SELECT id FROM polly_timeline_events
        WHERE creator_id = %s AND brand_id = %s AND event_type = 'portfolio_viewed'
          AND occurred_at > NOW() - INTERVAL '1 day'
        LIMIT 1
        """,
        (creator_id, bid),
    )
    if cur.fetchone():
        return {"recorded": False, "alerted": False}

    follows = _open_tasks(conn, creator_id, ["follow_up_due"], bid)
    if follows:
        ids = [t["id"] for t in follows if t.get("id")]
        if ids:
            cur.execute(
                "UPDATE polly_tasks SET priority = 1 WHERE id = ANY(%s)",
                (ids,),
            )
            conn.commit()
    else:
        cur.execute(
            "SELECT id FROM polly_tasks WHERE creator_id = %s AND brand_id = %s LIMIT 1",
            (creator_id, bid),
        )
        if not cur.fetchone():
            create_task(
                conn,
                creator_id,
                "pitch_sent",
                brand_id=bid,
                status="completed",
                priority=2,
                outcome="success",
                metadata={"brand_name": name, "source": source},
            )

    add_event(
        conn,
        creator_id,
        "portfolio_viewed",
        f"Viewed your portfolio · {name}",
        brand_id=bid,
        event_data={"brand_name": name, "source": source},
        polly_notes=(
            "Opened the kit from the pitch link. Real interest — "
            "follow up while you're top of mind."
        ),
    )
    alerted = False
    try:
        alerted = bool(_inject_portfolio_view_alert(conn, creator_id, bid, name))
    except Exception as err:
        print(f"[Polly tracker] kit-view alert skipped: {err}")
    print(f"[Polly tracker] portfolio_viewed creator={creator_id} brand_id={bid} name={name!r}")
    return {"recorded": True, "alerted": alerted, "brand_id": bid, "brand_name": name}


def apply_lifecycle_intent(
    conn,
    creator_id: int,
    parsed: Dict[str, Any],
    brand: Optional[Dict] = None,
) -> Dict[str, Any]:
    """Create/update tasks from a classified lifecycle intent. Returns action summary."""
    intent = parsed.get("intent") or "no_action"
    brand = brand or {}
    brand_id = _int(parsed.get("brand_id") or brand.get("id") or brand.get("brand_id"))
    name = str(
        parsed.get("brand") or brand.get("name") or brand.get("brand_name") or ""
    ).strip() or "this brand"
    brand_row = {"id": brand_id, "name": name}
    action = "none"
    say_hint = ""
    chips: List[Dict] = []

    if intent == "pitch_sent":
        record_pitch_sent(conn, creator_id, brand_row, drafted=False)
        action = "pitch_sent+followups"
        say_hint = (
            f"Logged. **{name}** is on the board — I'll nudge you day 4 if they're quiet, "
            "day 10 for a last bump, then we move on."
        )
    elif intent == "brand_replied_interested":
        close_tasks(conn, creator_id, ["follow_up_due", "pitch_sent"], brand_id, outcome="success")
        tid = create_task(
            conn, creator_id, "pr_shipped", brand_id=brand_id, status="pending",
            priority=1, due_at=utc_now() + timedelta(days=8),
            metadata={"brand_name": name},
        )
        add_event(
            conn, creator_id, "brand_replied_interested",
            f"Reply: interested · {name}",
            brand_id=brand_id, task_id=tid,
        )
        action = "interested+awaiting_pr"
        say_hint = (
            f"That's the one. **{name}** said yes — I'll track the PR. "
            "When the box lands, tell me and I'll set your content date."
        )
    elif intent == "brand_replied_rejected":
        close_tasks(conn, creator_id, ["follow_up_due", "pitch_sent", "reply_needed"], brand_id,
                    status="dropped", outcome="rejected")
        add_event(
            conn, creator_id, "brand_replied_rejected",
            f"Reply: passed · {name}",
            brand_id=brand_id,
        )
        action = "rejected"
        say_hint = (
            f"**{name}** passed. Not a reflection on you — it happens. "
            "Two things: I'll line up in-niche brands that actually recruit micros, "
            "and we should glance at your kit/bio before the next round."
        )
        chips = [
            {"id": "line_up", "label": "Line up brands", "action": "suggest_brands"},
            {"id": "walk_kit", "label": "Check my kit", "action": "coach_portfolio"},
        ]
    elif intent == "brand_replied_question":
        close_tasks(conn, creator_id, ["follow_up_due"], brand_id, outcome="partial")
        tid = create_task(
            conn, creator_id, "reply_needed", brand_id=brand_id, priority=1,
            due_at=utc_now() + timedelta(hours=24),
            metadata={"brand_name": name},
        )
        add_event(
            conn, creator_id, "brand_replied_question",
            f"Reply: they asked a question · {name}",
            brand_id=brand_id, task_id=tid,
        )
        action = "reply_needed"
        say_hint = f"**{name}** asked something — reply inside 24h. Want help drafting it?"
        chips = NUDGE_CHIPS["reply_needed"]
    elif intent == "pr_shipped":
        close_tasks(conn, creator_id, ["pr_shipped"], brand_id, status="in_progress")
        open_ship = _open_tasks(conn, creator_id, ["pr_shipped"], brand_id)
        if not open_ship:
            create_task(
                conn, creator_id, "pr_shipped", brand_id=brand_id, status="in_progress",
                priority=2, metadata={"brand_name": name},
            )
        tid = create_task(
            conn, creator_id, "pr_received", brand_id=brand_id, priority=2,
            due_at=utc_now() + timedelta(days=8),
            metadata={"brand_name": name},
        )
        add_event(conn, creator_id, "pr_shipped", f"PR shipped · {name}", brand_id=brand_id, task_id=tid)
        action = "pr_shipped"
        say_hint = f"Tracking **{name}**. Ping me when the box lands."
    elif intent == "pr_received":
        close_tasks(conn, creator_id, ["pr_shipped", "pr_received"], brand_id, outcome="success")
        tid = create_task(
            conn, creator_id, "content_due", brand_id=brand_id, priority=2,
            due_at=utc_now() + timedelta(days=14),
            metadata={"brand_name": name},
        )
        add_event(conn, creator_id, "pr_received", f"PR arrived · {name}", brand_id=brand_id, task_id=tid)
        action = "content_due"
        say_hint = (
            f"Box is in. Film **{name}** within 14 days — 15–30s, product in the first 3 seconds. "
            "Tell me when it's live."
        )
    elif intent == "content_posted":
        close_tasks(conn, creator_id, ["content_due"], brand_id, outcome="success")
        tid = create_task(
            conn, creator_id, "paid_pitch_sent", brand_id=brand_id, status="pending",
            priority=3, due_at=utc_now() + timedelta(days=30),
            metadata={"brand_name": name},
        )
        add_event(conn, creator_id, "content_posted", f"Content posted · {name}", brand_id=brand_id, task_id=tid)
        action = "content_posted"
        say_hint = (
            f"Logged the post. In 30 days we can show **{name}** the numbers and ask for paid. "
            "Drop me the URL if you have it."
        )
    elif intent == "payment_received":
        add_event(conn, creator_id, "milestone", f"Paid · {name}", brand_id=brand_id)
        action = "payment"
        say_hint = f"That's a paid collab on the board. **{name}** is now a relationship, not a one-off."
    elif intent == "portfolio_published":
        close_tasks(conn, creator_id, ["portfolio_incomplete"], None, outcome="success")
        add_event(conn, creator_id, "milestone", "My Kit published")
        action = "kit_live"
        say_hint = "Kit's live. Next: that URL in your TikTok bio, then we pitch a live roster."
    elif intent == "offer_paid_deal":
        action = "paid_pitch"
        say_hint = f"We'll pitch **{name}** paid — I'll draft it with your proof, not vibes."
    elif intent == "email_bounced":
        close_tasks(
            conn, creator_id, ["follow_up_due", "pitch_sent", "reply_needed"], brand_id,
            status="dropped", outcome="no_response",
        )
        add_event(
            conn, creator_id, "email_bounced",
            f"Email bounced · {name}",
            brand_id=brand_id,
            polly_notes="Wrong inbox or dead address. Do not keep pitching this contact.",
        )
        action = "bounced"
        say_hint = (
            f"That's a bounce on **{name}**, not a no. We stop that inbox. "
            "I'll pull the next in-niche brand that actually accepts micros."
        )
        chips = [
            {"id": "line_up", "label": "Next brand", "action": "suggest_brands"},
        ]
    elif intent == "pitch_not_sent":
        close_tasks(
            conn, creator_id, ["follow_up_due", "pitch_sent"], brand_id,
            status="dropped", outcome="dropped_by_creator",
        )
        add_event(
            conn, creator_id, "pitch_not_sent",
            f"Pitch not sent · {name}",
            brand_id=brand_id,
            polly_notes="Draft only. Not on Timeline as sent.",
        )
        action = "not_sent"
        say_hint = (
            f"**{name}** is still a draft — I won't log it as sent. "
            "Open mail when you're ready, then tap **I sent it**."
        )
        chips = [
            {
                "id": "draft_again",
                "label": f"Show {name} pitch",
                "action": "generate_pitch",
                "brand_id": brand_id,
                "brand_name": name,
            }
        ]
    elif intent == "still_quiet":
        action = "still_quiet"
        say_hint = (
            f"Quiet on **{name}** is normal at this stage. "
            "A short bump now is the move — not a new spray of brands."
        )
        chips = [
            {
                "id": "draft_followup",
                "label": f"Draft {name} follow-up",
                "action": "generate_pitch",
                "is_followup": True,
            }
        ]
    else:
        action = "none"

    for chip in chips:
        if brand_id:
            chip["brand_id"] = brand_id
        chip["brand_name"] = name

    return {
        "intent": intent,
        "action": action,
        "say_hint": say_hint,
        "chips": chips,
        "brand_id": brand_id,
        "brand_name": name,
        "unmark_pitched": intent == "pitch_not_sent",
    }


def load_creator_context(conn, creator_id: int) -> Dict[str, Any]:
    cur = _cursor(conn)
    active = _open_tasks(conn, creator_id)
    now = utc_now()
    due_soon = []
    for task in active:
        due = task.get("due_at")
        if due and due.tzinfo is None:
            due = due.replace(tzinfo=timezone.utc)
        if due and due <= now + timedelta(hours=24):
            due_soon.append(task)
    cur.execute(
        """
        SELECT e.*, b.brand_name
        FROM polly_timeline_events e
        LEFT JOIN pr_brands b ON b.id = e.brand_id
        WHERE e.creator_id = %s
        ORDER BY e.occurred_at DESC
        LIMIT 20
        """,
        (creator_id,),
    )
    recent = [dict(r) for r in cur.fetchall()]
    from services.polly_pain import checkin_due as _checkin_due
    pulse = _checkin_due({"active_tasks": active, "due_soon": due_soon})
    return {
        "active_tasks": _public_tasks(active),
        "due_soon": _public_tasks(due_soon),
        "checkin_due": pulse,
        "recent_timeline": [
            {
                "id": e.get("id"),
                "event_type": e.get("event_type"),
                "event_label": e.get("event_label"),
                "event_icon": e.get("event_icon"),
                "brand_id": e.get("brand_id"),
                "brand_name": e.get("brand_name"),
                "occurred_at": e.get("occurred_at").isoformat() if e.get("occurred_at") else None,
                "polly_notes": e.get("polly_notes"),
            }
            for e in recent
        ],
    }


def _public_tasks(rows: List[Dict]) -> List[Dict[str, Any]]:
    out = []
    for t in rows:
        due = t.get("due_at")
        out.append({
            "id": t.get("id"),
            "type": t.get("type"),
            "status": t.get("status"),
            "priority": t.get("priority"),
            "brand_id": t.get("brand_id"),
            "brand_name": t.get("brand_name") or (t.get("metadata") or {}).get("brand_name"),
            "logo": t.get("logo_url"),
            "category": t.get("category"),
            "due_at": due.isoformat() if due else None,
            "nudge_count": t.get("polly_nudge_count") or 0,
            "metadata": t.get("metadata") or {},
        })
    return out


def _rel_status(tasks: List[Dict], events: List[Dict]) -> str:
    types = {t.get("type") for t in tasks if t.get("status") in OPEN_STATUSES}
    outcomes = {t.get("outcome") for t in tasks}
    event_types = {e.get("event_type") for e in events}
    if "retainer_active" in types or "paid_pitch_sent" in types and any(
        e.get("event_type") == "content_posted" for e in events
    ):
        if "retainer_active" in types:
            return "won"
    if {"dropped", "rejected"} & outcomes or "brand_replied_rejected" in event_types:
        if not types:
            return "dropped"
    if types & {"content_due", "pr_shipped", "pr_received", "reply_needed", "follow_up_due", "campaign_applied"}:
        return "active"
    if "content_posted" in event_types or "pr_shipped" in event_types:
        return "won"
    if "brand_replied_interested" in event_types:
        return "active"
    if "campaign_applied" in event_types:
        return "active"
    if not types:
        return "dropped" if "brand_replied_rejected" in event_types else "pitched"
    return "active"


def list_relationships(conn, creator_id: int, status: Optional[str] = None) -> List[Dict[str, Any]]:
    cur = _cursor(conn)
    cur.execute(
        """
        SELECT t.*, b.brand_name, b.logo_url, b.category, b.slug
        FROM polly_tasks t
        LEFT JOIN pr_brands b ON b.id = t.brand_id
        WHERE t.creator_id = %s AND t.brand_id IS NOT NULL
        ORDER BY t.created_at DESC
        """,
        (creator_id,),
    )
    tasks = [dict(r) for r in cur.fetchall()]
    cur.execute(
        """
        SELECT DISTINCT ON (brand_id) *
        FROM polly_timeline_events
        WHERE creator_id = %s AND brand_id IS NOT NULL
        ORDER BY brand_id, occurred_at DESC
        """,
        (creator_id,),
    )
    latest_events = {r["brand_id"]: dict(r) for r in cur.fetchall()}
    by_brand: Dict[int, List[Dict]] = {}
    for t in tasks:
        by_brand.setdefault(t["brand_id"], []).append(t)
    rows = []
    for brand_id, brand_tasks in by_brand.items():
        latest = latest_events.get(brand_id) or {}
        cur.execute(
            "SELECT event_type FROM polly_timeline_events WHERE creator_id = %s AND brand_id = %s",
            (creator_id, brand_id),
        )
        ev_types = [dict(r) for r in cur.fetchall()]
        rel = _rel_status(brand_tasks, ev_types + ([latest] if latest else []))
        open_t = [t for t in brand_tasks if t.get("status") in OPEN_STATUSES]
        headline = open_t[0] if open_t else brand_tasks[0]
        name = headline.get("brand_name") or (headline.get("metadata") or {}).get("brand_name")
        rows.append({
            "brand_id": brand_id,
            "name": name,
            "logo": headline.get("logo_url"),
            "category": headline.get("category"),
            "slug": headline.get("slug"),
            "status": rel,
            "stage_label": _stage_label(headline, latest),
            "last_event": latest.get("event_label"),
            "last_event_at": latest.get("occurred_at").isoformat() if latest.get("occurred_at") else None,
            "last_icon": latest.get("event_icon") or EVENT_ICONS.get(latest.get("event_type") or "", "💬"),
            "due_at": headline.get("due_at").isoformat() if headline.get("due_at") else None,
            "source": "apply" if any(t.get("type") == "campaign_applied" for t in brand_tasks) else "pitch",
        })
    rows = merge_application_relationships(rows, _creator_applications(conn, creator_id))
    order = {"active": 0, "won": 1, "pitched": 2, "dropped": 3}
    rows.sort(key=lambda r: r.get("last_event_at") or "", reverse=True)
    rows.sort(key=lambda r: order.get(r["status"], 9))
    if status == "applied":
        rows = [r for r in rows if r.get("source") in ("apply", "both")]
    elif status and status != "all":
        rows = [r for r in rows if r["status"] == status]
    return rows


def _stage_label(task: Dict, latest: Dict) -> str:
    t = (task or {}).get("type")
    st = (task or {}).get("status")
    if (latest or {}).get("event_type") == "portfolio_viewed":
        return "Viewed your portfolio"
    if t == "content_due" and st in OPEN_STATUSES:
        return "Content due"
    if t == "follow_up_due" and st in OPEN_STATUSES:
        wave = (task.get("metadata") or {}).get("wave") or 4
        return f"Awaiting reply · day {wave} follow-up"
    if t == "reply_needed":
        return "Needs your reply"
    if t == "pr_shipped" or t == "pr_received":
        return "PR in transit" if t == "pr_shipped" else "PR arrived"
    if t == "campaign_applied" and st in OPEN_STATUSES:
        return "Applied · in review"
    if (latest or {}).get("event_type") == "campaign_applied":
        return "Applied · in review"
    if t == "paid_pitch_sent" and st in OPEN_STATUSES:
        return "Paid pitch window"
    if t == "retainer_active":
        return "Retainer active"
    if latest.get("event_type") == "brand_replied_rejected":
        return "Dropped"
    if latest.get("event_type") == "pitch_sent":
        return "Pitched"
    return (latest.get("event_label") or t or "On the board").split("·")[0].strip()


def _iso(value: Any) -> Optional[str]:
    if value is None:
        return None
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return str(value)


APPLY_STAGE = {
    "review": ("active", "Applied · in review"),
    "ships": ("won", "Selected · shipping"),
    "posted": ("won", "Posted content"),
    "declined": ("dropped", "They passed"),
    "skipped": ("dropped", "They passed"),
}


def _creator_applications(conn, creator_id: int, brand_id: Optional[int] = None) -> List[Dict[str, Any]]:
    cur = _cursor(conn)
    sql = """
        SELECT a.id, a.brand_id, a.status, a.applied_at, a.updated_at,
               b.brand_name, b.logo_url, b.category, b.slug
        FROM brand_pr_applications a
        JOIN pr_brands b ON b.id = a.brand_id
        WHERE a.creator_id = %s
          AND COALESCE(a.status, 'review') <> 'hidden'
    """
    params: List[Any] = [creator_id]
    if brand_id:
        sql += " AND a.brand_id = %s"
        params.append(brand_id)
    sql += " ORDER BY a.applied_at DESC"
    try:
        cur.execute(sql, params)
        return [dict(r) for r in cur.fetchall()]
    except Exception as err:
        print(f"[Polly tracker] applications merge skipped: {err}")
        return []


def application_relationship(app: Dict[str, Any]) -> Dict[str, Any]:
    status_code = (app.get("status") or "review").strip().lower()
    rel_status, stage = APPLY_STAGE.get(status_code, APPLY_STAGE["review"])
    name = app.get("brand_name") or "Brand"
    return {
        "brand_id": app.get("brand_id"),
        "name": name,
        "logo": app.get("logo_url"),
        "category": app.get("category"),
        "slug": app.get("slug"),
        "status": rel_status,
        "stage_label": stage,
        "last_event": f"Applied for gifted PR · {name}",
        "last_event_at": _iso(app.get("updated_at") or app.get("applied_at")),
        "last_icon": EVENT_ICONS.get("campaign_applied", "🎁"),
        "due_at": None,
        "source": "apply",
        "apply_status": status_code,
    }


def merge_application_relationships(
    rows: List[Dict[str, Any]],
    apps: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    by_id: Dict[Any, Dict[str, Any]] = {}
    for row in rows:
        item = dict(row)
        item.setdefault("source", "pitch")
        by_id[item.get("brand_id")] = item
    rank = {"dropped": 0, "pitched": 1, "active": 2, "won": 3}
    for app in apps:
        rel = application_relationship(app)
        bid = rel.get("brand_id")
        if bid is None:
            continue
        existing = by_id.get(bid)
        if not existing:
            by_id[bid] = rel
            continue
        existing["source"] = "both" if existing.get("source") != "apply" else "apply"
        existing["apply_status"] = rel.get("apply_status")
        if rank.get(rel["status"], 0) >= rank.get(existing.get("status") or "", 0):
            existing["status"] = rel["status"]
            existing["stage_label"] = rel["stage_label"]
        if (rel.get("last_event_at") or "") >= (existing.get("last_event_at") or ""):
            existing["last_event"] = rel["last_event"]
            existing["last_event_at"] = rel["last_event_at"]
            existing["last_icon"] = rel["last_icon"]
    return list(by_id.values())


def application_timeline_events(
    app: Dict[str, Any],
    existing_types: Optional[set] = None,
) -> List[Dict[str, Any]]:
    existing_types = existing_types or set()
    name = app.get("brand_name") or "this brand"
    status = (app.get("status") or "review").strip().lower()
    app_id = app.get("id") or app.get("brand_id") or 0
    out: List[Dict[str, Any]] = []
    if "campaign_applied" not in existing_types:
        out.append({
            "id": f"app-{app_id}-applied",
            "event_type": "campaign_applied",
            "event_label": f"Applied for gifted PR · {name}",
            "event_icon": EVENT_ICONS.get("campaign_applied", "🎁"),
            "polly_notes": "You're on their gifted list. They pick who gets the box.",
            "occurred_at": _iso(app.get("applied_at")),
            "event_data": {"source": "apply", "apply_status": status},
            "source": "apply",
        })
    if status in ("ships", "posted") and "pr_shipped" not in existing_types:
        out.append({
            "id": f"app-{app_id}-ships",
            "event_type": "pr_shipped",
            "event_label": f"Selected · shipping · {name}",
            "event_icon": EVENT_ICONS.get("pr_shipped", "📦"),
            "polly_notes": "They picked you. Box is on the way.",
            "occurred_at": _iso(app.get("updated_at") or app.get("applied_at")),
            "event_data": {"source": "apply"},
            "source": "apply",
        })
    if status == "posted" and "content_posted" not in existing_types:
        out.append({
            "id": f"app-{app_id}-posted",
            "event_type": "content_posted",
            "event_label": f"Content posted · {name}",
            "event_icon": EVENT_ICONS.get("content_posted", "🎥"),
            "occurred_at": _iso(app.get("updated_at")),
            "event_data": {"source": "apply"},
            "source": "apply",
        })
    if status in ("declined", "skipped") and "brand_replied_rejected" not in existing_types:
        out.append({
            "id": f"app-{app_id}-passed",
            "event_type": "brand_replied_rejected",
            "event_label": f"They passed · {name}",
            "event_icon": EVENT_ICONS.get("brand_replied_rejected", "🔴"),
            "polly_notes": "Not this round. Next in-niche brand is the move.",
            "occurred_at": _iso(app.get("updated_at") or app.get("applied_at")),
            "event_data": {"source": "apply"},
            "source": "apply",
        })
    return out


def brand_timeline(conn, creator_id: int, brand_id: int) -> Dict[str, Any]:
    cur = _cursor(conn)
    cur.execute(
        """
        SELECT id, brand_name AS name, logo_url AS logo, category, slug, description
        FROM pr_brands WHERE id = %s
        """,
        (brand_id,),
    )
    brand = cur.fetchone()
    if not brand:
        return {}
    cur.execute(
        """
        SELECT * FROM polly_timeline_events
        WHERE creator_id = %s AND brand_id = %s
        ORDER BY occurred_at ASC, id ASC
        """,
        (creator_id, brand_id),
    )
    events = []
    for row in cur.fetchall():
        events.append({
            "id": row["id"],
            "event_type": row["event_type"],
            "event_label": row["event_label"],
            "event_icon": row["event_icon"] or EVENT_ICONS.get(row["event_type"], "💬"),
            "polly_notes": row["polly_notes"],
            "occurred_at": row["occurred_at"].isoformat() if row["occurred_at"] else None,
            "event_data": row["event_data"] or {},
            "source": (row.get("event_data") or {}).get("source") or "pitch",
        })
    apps = _creator_applications(conn, creator_id, brand_id=brand_id)
    if apps:
        existing_types = {e.get("event_type") for e in events}
        for extra in application_timeline_events(apps[0], existing_types):
            events.append(extra)
        events.sort(key=lambda e: (e.get("occurred_at") or "", str(e.get("id") or "")))
    open_t = _open_tasks(conn, creator_id, brand_id=brand_id)
    next_action = None
    if open_t:
        top = open_t[0]
        next_action = {
            "type": top.get("type"),
            "label": _stage_label(top, {}),
            "due_at": top.get("due_at").isoformat() if top.get("due_at") else None,
            "hint": NUDGE_COPY.get("follow_up_d4", "").format(
                brand=brand["name"]
            ) if top.get("type") == "follow_up_due" else None,
        }
    return {
        "brand": dict(brand),
        "status": _rel_status(
            [{"type": t.get("type"), "status": t.get("status"), "outcome": t.get("outcome")} for t in open_t]
            + [{"type": e["event_type"], "status": "completed", "outcome": None} for e in events],
            events,
        ),
        "events": events,
        "open_tasks": _public_tasks(open_t),
        "next_action": next_action,
    }


def apply_task_chip(
    conn,
    creator_id: int,
    chip_id: str,
    task_id: Optional[int] = None,
    brand_id: Optional[int] = None,
    brand_name: Optional[str] = None,
) -> Dict[str, Any]:
    cur = _cursor(conn)
    task = None
    if task_id:
        cur.execute(
            "SELECT t.*, b.brand_name FROM polly_tasks t LEFT JOIN pr_brands b ON b.id = t.brand_id WHERE t.id = %s AND t.creator_id = %s",
            (task_id, creator_id),
        )
        task = cur.fetchone()
    brand_id = brand_id or (task.get("brand_id") if task else None)
    name = (
        (task.get("brand_name") if task else None)
        or (brand_name or "").strip()
        or "this brand"
    )
    chip_id = (chip_id or "").strip().lower()

    if chip_id in ("snooze_24h", "filming_weekend", "ran_out"):
        hours = 72 if chip_id == "filming_weekend" else 24
        if task:
            cur.execute(
                "UPDATE polly_tasks SET snoozed_until = %s, status = 'snoozed' WHERE id = %s",
                (utc_now() + timedelta(hours=hours), task["id"]),
            )
            conn.commit()
        return {"ok": True, "route": "chat", "say": f"Got it — I'll ping you on **{name}** in a bit."}

    if chip_id in ("move_on",):
        close_tasks(conn, creator_id, ["follow_up_due", "pitch_sent", "reply_needed"], brand_id,
                    status="dropped", outcome="no_response")
        add_event(conn, creator_id, "dropped", f"Moved on · {name}", brand_id=brand_id)
        return {"ok": True, "route": "suggest_brands", "say": f"Dropped **{name}**. Let's hit a live roster instead."}

    if chip_id == "pr_arrived":
        parsed = {"intent": "pr_received", "brand_id": brand_id, "brand": name, "confidence": 1}
        result = apply_lifecycle_intent(conn, creator_id, parsed, {"id": brand_id, "name": name})
        return {"ok": True, "route": "chat", "say": result.get("say_hint"), "chips": result.get("chips")}

    if chip_id in ("pr_not_yet", "pr_nothing"):
        if task:
            cur.execute(
                "UPDATE polly_tasks SET due_at = %s WHERE id = %s",
                (utc_now() + timedelta(days=3), task["id"]),
            )
            conn.commit()
        return {"ok": True, "route": "chat", "say": f"I'll check **{name}** again in a few days."}

    if chip_id in ("draft_followup", "draft_it", "draft_final"):
        return {
            "ok": True,
            "route": "generate_pitch",
            "brand_id": brand_id,
            "brand_name": name,
            "is_followup": True,
        }

    if chip_id in ("line_up", "line_up_more", "walk_kit", "more_brands"):
        return {
            "ok": True,
            "route": "coach_portfolio" if chip_id == "walk_kit" else "suggest_brands",
        }

    from services.polly_pain import reply_kind_chips

    if chip_id == "checkin_replied":
        return {
            "ok": True,
            "route": "chat",
            "say": f"Nice. On **{name}** — interested, they asked a question, or they passed?",
            "chips": reply_kind_chips({"id": brand_id, "name": name}, task_id=task_id),
            "brand_id": brand_id,
            "brand_name": name,
        }
    if chip_id == "checkin_interested":
        result = apply_lifecycle_intent(
            conn, creator_id,
            {"intent": "brand_replied_interested", "brand_id": brand_id, "brand": name, "confidence": 1},
            {"id": brand_id, "name": name},
        )
        return {"ok": True, "route": "chat", "say": result.get("say_hint"), "chips": result.get("chips"), "brand_id": brand_id, "brand_name": name}
    if chip_id == "checkin_question":
        result = apply_lifecycle_intent(
            conn, creator_id,
            {"intent": "brand_replied_question", "brand_id": brand_id, "brand": name, "confidence": 1},
            {"id": brand_id, "name": name},
        )
        return {"ok": True, "route": "chat", "say": result.get("say_hint"), "chips": result.get("chips"), "brand_id": brand_id, "brand_name": name}
    if chip_id in ("checkin_passed",):
        result = apply_lifecycle_intent(
            conn, creator_id,
            {"intent": "brand_replied_rejected", "brand_id": brand_id, "brand": name, "confidence": 1},
            {"id": brand_id, "name": name},
        )
        return {"ok": True, "route": "chat", "say": result.get("say_hint"), "chips": result.get("chips"), "brand_id": brand_id, "brand_name": name}
    if chip_id == "checkin_bounced":
        result = apply_lifecycle_intent(
            conn, creator_id,
            {"intent": "email_bounced", "brand_id": brand_id, "brand": name, "confidence": 1},
            {"id": brand_id, "name": name},
        )
        return {"ok": True, "route": "chat", "say": result.get("say_hint"), "chips": result.get("chips"), "brand_id": brand_id, "brand_name": name}
    if chip_id == "checkin_not_sent":
        result = apply_lifecycle_intent(
            conn, creator_id,
            {"intent": "pitch_not_sent", "brand_id": brand_id, "brand": name, "confidence": 1},
            {"id": brand_id, "name": name},
        )
        return {
            "ok": True,
            "route": "chat",
            "say": result.get("say_hint"),
            "chips": result.get("chips"),
            "brand_id": brand_id,
            "brand_name": name,
            "unmark_pitched": True,
        }
    if chip_id == "checkin_quiet":
        result = apply_lifecycle_intent(
            conn, creator_id,
            {"intent": "still_quiet", "brand_id": brand_id, "brand": name, "confidence": 1},
            {"id": brand_id, "name": name},
        )
        return {
            "ok": True,
            "route": "generate_pitch",
            "is_followup": True,
            "brand_id": brand_id,
            "brand_name": name,
            "say": result.get("say_hint"),
        }
    return {"ok": True, "route": "chat"}


def _nudge_key(task: Dict) -> str:
    t = task.get("type")
    wave = (task.get("metadata") or {}).get("wave")
    if t == "follow_up_due" and wave == 10:
        return "follow_up_d10"
    if t == "follow_up_due" and wave == 14:
        return "follow_up_d14"
    if t == "follow_up_due":
        created = task.get("created_at") or utc_now()
        if created.tzinfo is None:
            created = created.replace(tzinfo=timezone.utc)
        if utc_now() - created >= timedelta(days=13):
            return "follow_up_d14"
        return "follow_up_d4"
    if t == "reply_needed":
        return "reply_needed"
    if t in ("pr_shipped", "pr_received"):
        return "pr_shipped"
    if t == "content_due":
        return "content_due"
    return ""


def due_nudges_for_creator(conn, creator_id: int, limit: int = 1) -> List[Dict[str, Any]]:
    now = utc_now()
    tasks = _open_tasks(conn, creator_id)
    out = []
    for task in tasks:
        snooze = task.get("snoozed_until")
        if snooze:
            if snooze.tzinfo is None:
                snooze = snooze.replace(tzinfo=timezone.utc)
            if snooze > now:
                continue
        due = task.get("due_at")
        if not due:
            continue
        if due.tzinfo is None:
            due = due.replace(tzinfo=timezone.utc)
        if due > now:
            continue
        last = task.get("polly_last_nudge_at")
        if last:
            if last.tzinfo is None:
                last = last.replace(tzinfo=timezone.utc)
            if now - last < timedelta(hours=20):
                continue
        if int(task.get("polly_nudge_count") or 0) >= 3:
            continue
        key = _nudge_key(task)
        if not key:
            continue
        name = task.get("brand_name") or (task.get("metadata") or {}).get("brand_name") or "this brand"
        copy = NUDGE_COPY[key].format(brand=name)
        chips = []
        for chip in NUDGE_CHIPS.get(key) or []:
            item = dict(chip)
            item["task_id"] = task.get("id")
            item["brand_id"] = task.get("brand_id")
            item["brand_name"] = name
            chips.append(item)
        out.append({
            "task_id": task.get("id"),
            "brand_id": task.get("brand_id"),
            "brand_name": name,
            "key": key,
            "message": copy,
            "chips": chips,
        })
        if len(out) >= limit:
            break
    return out


def mark_nudged(conn, task_id: int) -> None:
    cur = _cursor(conn)
    cur.execute(
        """
        UPDATE polly_tasks
        SET polly_last_nudge_at = NOW(), polly_nudge_count = COALESCE(polly_nudge_count, 0) + 1
        WHERE id = %s
        """,
        (task_id,),
    )
    conn.commit()


def deliver_nudge(conn, creator_id: int, nudge: Dict) -> None:
    from services.polly_memory import load_thread, save_thread

    mark_nudged(conn, nudge["task_id"])
    add_event(
        conn, creator_id, "polly_nudge_sent",
        f"Polly nudge · {nudge.get('brand_name')}",
        brand_id=nudge.get("brand_id"),
        task_id=nudge.get("task_id"),
    )
    thread = load_thread(conn, creator_id)
    messages = list(thread.get("messages") or [])
    messages.append({
        "role": "assistant",
        "content": nudge["message"],
        "task_chips": nudge.get("chips") or [],
        "kind": "nudge",
    })
    save_thread(conn, creator_id, messages, thread.get("suggested_brands") or [], notes=thread.get("notes"))


def process_due_nudges(conn, creator_limit: int = 40) -> int:
    """Hourly cron: one nudge per creator per run, max 1/day already gated."""
    cur = _cursor(conn)
    cur.execute(
        """
        SELECT DISTINCT creator_id FROM polly_tasks
        WHERE status = ANY(%s) AND due_at IS NOT NULL AND due_at <= NOW()
        LIMIT %s
        """,
        (list(OPEN_STATUSES), creator_limit),
    )
    sent = 0
    for row in cur.fetchall():
        cid = row["creator_id"]
        nudges = due_nudges_for_creator(conn, cid, limit=1)
        if not nudges:
            continue
        try:
            deliver_nudge(conn, cid, nudges[0])
            sent += 1
        except Exception as err:
            print(f"[Polly tracker] nudge failed creator={cid}: {err}")
    return sent


def maybe_bootstrap_nudge(conn, creator_id: int) -> Optional[Dict[str, Any]]:
    nudges = due_nudges_for_creator(conn, creator_id, limit=1)
    if not nudges:
        return None
    # Don't rewrite the thread on every page load — only if last nudge was >20h
    return nudges[0]


def morning_brief(context: Optional[Dict], first_name: Optional[str] = None) -> Optional[Dict[str, Any]]:
    context = context or {}
    active = context.get("active_tasks") or []
    due = context.get("due_soon") or []
    views = _portfolio_views(context)
    from services.polly_pain import checkin_chips, checkin_due
    pulse = context.get("checkin_due") or checkin_due(context)
    if not active and not due and not views and not pulse:
        return None
    name = (first_name or "").strip() or "there"
    watched = []
    for e in views[:3]:
        watched.append(f"{e.get('brand_name') or 'A brand'} — viewed your portfolio")
    for t in (due + active)[:5]:
        label = t.get("brand_name") or "a brand"
        watched.append(f"{label} — {t.get('type', '').replace('_', ' ')}")
    if views:
        hot = views[0]
        p_name = hot.get("brand_name") or "a brand"
        p_type = "viewed your portfolio"
        summary = (
            f"**{p_name}** viewed your portfolio. That's the signal — "
            "follow up while you're top of mind."
        )
        if active:
            summary += f" You've also got {len(active)} open item{'s' if len(active) != 1 else ''} on the board."
        chips = [
            {
                "id": "draft_followup",
                "label": f"Draft {p_name} follow-up",
                "action": "generate_pitch",
                "brand_id": hot.get("brand_id"),
                "brand_name": p_name,
                "is_followup": True,
            }
        ]
    elif pulse and pulse.get("brand_name"):
        p_name = pulse.get("brand_name")
        p_type = "check-in"
        summary = (
            f"Quick pulse on **{p_name}**. "
            "Reply, bounce, passed, still quiet, or never sent — tap it and I'll move."
        )
        chips = checkin_chips(
            {"id": pulse.get("brand_id"), "name": p_name},
            task_id=pulse.get("task_id"),
        )
    else:
        if not (due or active):
            return None
        priority = (due or active)[0]
        p_name = priority.get("brand_name") or "your next brand"
        p_type = (priority.get("type") or "").replace("_", " ")
        summary = (
            f"You've got {len(active)} open item{'s' if len(active) != 1 else ''} on the board. "
            f"Priority is **{p_name}** ({p_type})."
        )
        chips = []
        if priority.get("type") == "follow_up_due":
            chips = [
                {
                    "id": "draft_followup",
                    "label": f"Draft {p_name} follow-up",
                    "action": "generate_pitch",
                    "brand_id": priority.get("brand_id"),
                    "brand_name": p_name,
                    "task_id": priority.get("id"),
                    "is_followup": True,
                }
            ]
        elif priority.get("type") == "content_due":
            chips = [{"id": "need_idea", "label": "Need a filming plan", "action": "chat"}]
        else:
            chips = [{"id": "line_up", "label": "Line up brands", "action": "suggest_brands"}]
    return {
        "kind": "brief",
        "title": f"Morning {name}",
        "summary": summary,
        "priority": f"{p_name} · {p_type}",
        "watched": watched[:5],
        "chips": chips,
    }
