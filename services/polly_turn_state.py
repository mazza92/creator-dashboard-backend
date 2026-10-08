"""One Polly chat turn: everything the brain can see, and everything the turn produces.

The brain (Gemini) reads `to_model_json()` and calls tools. Tools read the same state
and write UI output (cards, pitch, chips, paywall) back onto it. Nothing else decides
which brand the creator means.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from services.polly import (
    brand_names_agree,
    last_thread_pitch,
    locked_pitch_name,
    out_of_free_unlocks,
    pitch_has_placeholder,
    sanitize_brand_card,
)


def _int(value: Any) -> Optional[int]:
    try:
        out = int(value)
    except (TypeError, ValueError):
        return None
    return out or None


def _brand_ref(row: Optional[Dict]) -> Optional[Dict[str, Any]]:
    if not isinstance(row, dict):
        return None
    name = str(row.get("name") or row.get("brand_name") or "").strip()
    bid = _int(row.get("id") or row.get("brand_id"))
    if not name and not bid:
        return None
    return {"id": bid, "name": name or None}


@dataclass
class TurnState:
    creator_id: int
    creator: Dict[str, Any]
    first: str
    scrape: Dict[str, Any]
    kit: Dict[str, Any]
    profile_context: str
    coach_moves: List[Any]
    balance: Dict[str, Any]
    notes: Dict[str, Any]
    messages: List[Dict[str, Any]]
    stored_messages: List[Dict[str, Any]]
    suggested: List[Dict[str, Any]]
    tracker_ctx: Dict[str, Any]
    early_fu: Dict[str, Any]
    career: Optional[Dict[str, Any]]
    user_text: str
    chip: Dict[str, Any]
    conn: Any = None

    brands: List[Dict[str, Any]] = field(default_factory=list)
    gigs: List[Dict[str, Any]] = field(default_factory=list)
    gigs_has_more: bool = False
    pitch: Optional[Dict[str, Any]] = None
    pitch_update: Optional[Dict[str, Any]] = None
    paywall: bool = False
    paywall_payload: Optional[Dict[str, Any]] = None
    task_chips: List[Dict[str, Any]] = field(default_factory=list)
    kit_actions: List[Dict[str, Any]] = field(default_factory=list)
    error: Optional[str] = None
    final_say: str = ""
    say_tail: List[str] = field(default_factory=list)
    sent_now: bool = False
    after_send_empty: bool = False
    paywall_moment: Optional[Dict[str, Any]] = None
    tool_log: List[Dict[str, Any]] = field(default_factory=list)
    intent: str = "chat"
    auth_failed: bool = False

    def db(self):
        """Open DB connection for tools. Reopens if an earlier step closed it."""
        if self.conn is None or getattr(self.conn, "closed", 0):
            from pr_crm_routes import get_db_connection
            self.conn = get_db_connection()
        return self.conn

    # ----- what's on screen -------------------------------------------------

    def draft(self) -> Optional[Dict[str, Any]]:
        """The unsent pitch the creator could send right now."""
        pending = self.notes.get("pending_pitch") if isinstance(self.notes.get("pending_pitch"), dict) else None
        card = last_thread_pitch(self.messages) or {}
        card_name = str(card.get("brand_name") or card.get("name") or "").strip()
        ref = _brand_ref(pending)
        if not ref and card_name and not card.get("is_followup"):
            ref = {"id": _int(card.get("brand_id")), "name": card_name}
        if not ref:
            return None
        same_card = bool(card_name) and brand_names_agree(card_name, ref.get("name"))
        body = str(card.get("body") or "") if same_card else ""
        return {
            "id": ref.get("id"),
            "name": ref.get("name"),
            "email": (card.get("email") or None) if same_card else None,
            "needs_location": pitch_has_placeholder(body) if body else False,
            "on_screen": same_card,
        }

    def locked(self) -> Optional[Dict[str, Any]]:
        """Paywalled pitch preview, when it is Polly's latest message."""
        name = locked_pitch_name(self.stored_messages)
        if not name:
            return None
        wall = self.notes.get("paywall_brand") if isinstance(self.notes.get("paywall_brand"), dict) else {}
        bid = _int(wall.get("id")) if brand_names_agree(name, wall.get("name")) else None
        return {"id": bid, "name": name}

    def paywall_brand(self) -> Optional[Dict[str, Any]]:
        return _brand_ref(self.notes.get("paywall_brand"))

    def cards_on_screen(self) -> List[Dict[str, Any]]:
        for msg in reversed(self.messages or []):
            if (msg.get("role") or "").lower() != "assistant":
                continue
            return [r for r in (_brand_ref(b) for b in (msg.get("brands") or [])) if r]
        return []

    def pitched_names(self) -> List[str]:
        return [str(n) for n in (self.notes.get("pitched_brand_names") or []) if n]

    def is_pitched(self, brand: Optional[Dict]) -> bool:
        ref = _brand_ref(brand)
        if not ref:
            return False
        ids = set()
        for raw in self.notes.get("pitched_brand_ids") or []:
            val = _int(raw)
            if val:
                ids.add(val)
        if ref.get("id") and ref["id"] in ids:
            return True
        return any(brand_names_agree(ref.get("name"), n) for n in self.pitched_names())

    def known_brand_names(self) -> List[str]:
        """Every brand the reply may name: on screen, in memory, or returned by a tool this turn."""
        names: List[str] = []
        pools: List[Any] = [
            self.suggested, self.brands, self.cards_on_screen(),
            [self.draft(), self.locked(), self.paywall_brand(), self.pitch],
            [{"name": n} for n in self.pitched_names()],
            [{"name": t.get("brand_name")} for t in (self.tracker_ctx.get("active_tasks") or [])],
        ]
        for msg in self.messages or []:
            if (msg.get("role") or "").lower() == "assistant":
                pools.append(msg.get("brands") or [])
                pools.append([msg.get("pitch")])
        for entry in self.tool_log:
            pools.append([{"name": n} for n in entry.get("brand_names") or []])
        for pool in pools:
            for row in pool or []:
                ref = _brand_ref(row)
                if ref and ref.get("name") and ref["name"] not in names:
                    names.append(ref["name"])
        return names

    # ----- outputs ----------------------------------------------------------

    def show_brands(self, rows: List[Dict[str, Any]]) -> None:
        from routes.polly import _hydrate_brand_cards
        self.brands = _hydrate_brand_cards(rows)

    def add_chips(self, chips: List[Dict[str, Any]]) -> None:
        seen = {(c.get("id"), c.get("brand_id")) for c in self.task_chips}
        for chip in chips or []:
            key = (chip.get("id"), chip.get("brand_id"))
            if key not in seen:
                self.task_chips.append(chip)
                seen.add(key)

    def tools_ran(self, name: str, status: Optional[str] = None) -> bool:
        return any(
            e.get("name") == name and (status is None or e.get("status") == status)
            for e in self.tool_log
        )

    # ----- what the brain reads ---------------------------------------------

    def to_model_json(self) -> Dict[str, Any]:
        draft = self.draft()
        locked = self.locked()
        tasks = []
        for task in (self.tracker_ctx.get("active_tasks") or [])[:8]:
            tasks.append({
                "brand": task.get("brand_name"),
                "brand_id": task.get("brand_id"),
                "task": task.get("type"),
                "status": task.get("status"),
                "due": str(task.get("due_at") or "")[:10] or None,
            })
        chip = {k: v for k, v in (self.chip or {}).items() if v not in (None, "", False)}
        return {
            "latest_user_message": self.user_text,
            "tapped_button": chip or None,
            "unsent_draft": draft,
            "locked_pitch_preview": locked,
            "brand_cards_on_screen": self.cards_on_screen()[:8],
            "suggested_queue": [
                {"id": _int(b.get("id")), "name": b.get("name"), "category": b.get("category")}
                for b in (self.suggested or [])[:12]
                if isinstance(b, dict) and b.get("name")
            ],
            "already_pitched": self.pitched_names()[:20],
            "tracker_tasks": tasks,
            "credits": {
                "remaining": self.balance.get("remaining"),
                "limit": self.balance.get("limit"),
                "is_pro": bool(self.balance.get("is_unlimited")),
                "out_of_free_pitches": out_of_free_unlocks(self.balance),
            },
            "kit_published": bool((self.kit or {}).get("published")),
            "preferences": self.notes.get("prefs") or {},
            "deal_intent": self.notes.get("deal_intent"),
            "location": self.notes.get("location"),
        }


def brand_card(row: Optional[Dict]) -> Optional[Dict[str, Any]]:
    """Small brand summary for tool results the model reads."""
    if not isinstance(row, dict):
        return None
    clean = sanitize_brand_card(dict(row)) or row
    out = {
        "id": _int(clean.get("id") or clean.get("brand_id")),
        "name": clean.get("name") or clean.get("brand_name"),
        "category": clean.get("category"),
    }
    for key in ("source", "why", "description", "hero_product"):
        val = clean.get(key)
        if val:
            out[key] = str(val)[:220]
    return out
