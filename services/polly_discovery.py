"""First-session discovery + manager notes. One question at a time."""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple


FIELDS = (
    "goal_30d",
    "stage",
    "niche",
    "location",
    "biggest_challenge",
    "dream_brands",
)

_EMPTY_ANSWERS = {
    "none", "no", "n/a", "na", "nothing", "idk", "skip", "nope", "nah",
    "no one", "nobody", "not sure", "-",
}

_QUESTION_HINTS = (
    ("dream_brands", ("dreaming of working with", "dream brands", "brands are you dreaming")),
    ("biggest_challenge", ("biggest frustration", "what's not working")),
    ("location", ("where are you based", "country + city")),
    ("niche", ("what's your niche", "you can pick 1 or 2")),
    ("stage", ("ugc journey", "just starting, never worked")),
    ("goal_30d", ("30-60 days", "hoping to achieve")),
)

NICHE_CATEGORIES = {
    "skincare": ["skincare", "beauty"],
    "beauty": ["beauty", "skincare"],
    "makeup": ["beauty", "makeup", "skincare"],
    "hair": ["haircare", "beauty"],
    "haircare": ["haircare", "beauty"],
    "fashion": ["fashion"],
    "fitness": ["fitness", "activewear"],
    "food": ["food", "beverages"],
    "wellness": ["wellness", "skincare"],  # NOT fitness — reply-chance matching
    "lifestyle": ["lifestyle", "beauty", "home"],
    "tech": ["tech"],
}

_BEAUTY_MATCH_CATS = frozenset({"skincare", "beauty", "makeup", "haircare", "wellness"})
_FITNESS_MATCH_CATS = frozenset({"fitness", "activewear", "sports", "athleisure"})

STAGE_MAP = (
    ("established", "established"),
    ("scale", "established"),
    ("5+", "growing"),
    ("paid", "growing"),
    ("1-3", "early_stage"),
    ("gifted", "early_stage"),
    ("just starting", "just_starting"),
    ("never", "just_starting"),
    ("new", "just_starting"),
    ("a)", "just_starting"),
    ("b)", "early_stage"),
    ("c)", "growing"),
    ("d)", "established"),
)


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def discovery_complete(notes: Optional[Dict] = None) -> bool:
    notes = notes or {}
    return bool(notes.get("discovery_completed_at") or notes.get("discovery_skipped_at"))


def remaining_fields(notes: Optional[Dict] = None) -> List[str]:
    notes = notes or {}
    return [field for field in FIELDS if not _filled(notes, field)]


def assign_track(notes: Optional[Dict] = None, scrape: Optional[Dict] = None) -> Dict[str, Any]:
    """Lock aspiring vs established once we know enough. Default aspiring (the 80%)."""
    out = dict(notes or {})
    provisional = bool(out.get("polly_track_provisional"))
    locked = bool(out.get("polly_track_locked_at")) and not provisional
    if locked and out.get("polly_track") in ("aspiring", "established"):
        return out
    stage = (out.get("stage") or "").strip()
    followers = 0
    try:
        followers = int((scrape or {}).get("follower_count") or 0)
    except (TypeError, ValueError):
        followers = 0
    if stage in ("growing", "established"):
        out["polly_track"] = "established"
        out["polly_track_locked_at"] = utc_now()
        out.pop("polly_track_provisional", None)
        return out
    if stage in ("just_starting", "early_stage"):
        out["polly_track"] = "aspiring"
        out["polly_track_locked_at"] = utc_now()
        out.pop("polly_track_provisional", None)
        return out
    if out.get("discovery_skipped_at") or out.get("discovery_completed_at"):
        out["polly_track"] = "established" if followers >= 50000 else "aspiring"
        out["polly_track_locked_at"] = utc_now()
        out.pop("polly_track_provisional", None)
        return out
    out["polly_track"] = "aspiring"
    out["polly_track_provisional"] = True
    return out


def track_playbook(notes: Optional[Dict] = None) -> str:
    track = (notes or {}).get("polly_track") or "aspiring"
    if track == "established":
        return (
            "CREATOR TRACK (locked): established. Objective: more and bigger brand outreach, "
            "paid UGC, retainers. Kit is a closer, not the main job. Prioritize live PR rosters, "
            "then high-fit brands. Talk volume of pitches, follow-ups, and retainer packaging."
        )
    return (
        "CREATOR TRACK (locked unless discovery later upgrades them): aspiring. "
        "Objective: become a quality profile brands actually reply to. Order: publish My Kit → "
        "put the kit URL in TikTok bio → pitch live PR rosters / gifted collabs → improve "
        "content quality from real briefs → then paid UGC. Do not skip kit/bio to spray pitches. "
        "Lead with brands that gift micros and are actively recruiting in-niche. "
        "Relevance and reply chance beat famous logos — never pitch Nike / On Running / "
        "Sephora-scale names to a new micro. Live PR rosters still have to match the niche."
    )


def discovery_brief(notes: Optional[Dict] = None) -> str:
    notes = notes or {}
    known = []
    for field in FIELDS:
        if _filled(notes, field):
            known.append(f"{field}={notes.get(field)}")
    missing = remaining_fields(notes)
    bits = []
    if notes.get("polly_track"):
        bits.append(track_playbook(notes))
    if known:
        bits.append("Already known: " + "; ".join(known))
    if discovery_complete(notes):
        bits.append("Discovery complete — do not restart it. Do not greet as if this is a first meeting.")
    elif missing:
        bits.append(
            "Still useful to learn (ask naturally, at most ONE question, never as a form): "
            + ", ".join(missing)
        )
    return " ".join(bits) if bits else "No manager notes yet."


def discovery_started(notes: Optional[Dict] = None) -> bool:
    notes = notes or {}
    if discovery_complete(notes):
        return True
    return any(notes.get(field) for field in FIELDS) or int(notes.get("discovery_step") or 0) > 0


def _is_empty_answer(text: str) -> bool:
    low = re.sub(r"[.!?,]", "", (text or "").strip().lower())
    return low in _EMPTY_ANSWERS


def _filled(notes: Dict[str, Any], field: str) -> bool:
    """True once the question has been answered, including 'none' / empty list."""
    return field in notes and notes.get(field) is not None


def next_field(notes: Optional[Dict] = None) -> Optional[str]:
    notes = notes or {}
    for field in FIELDS:
        if not _filled(notes, field):
            return field
    return None


def field_from_history(history: Optional[List] = None) -> Optional[str]:
    for msg in reversed(history or []):
        if (msg.get("role") or "").lower() != "assistant":
            continue
        content = (msg.get("content") or "").lower()
        for field, hints in _QUESTION_HINTS:
            if any(hint in content for hint in hints):
                return field
        break
    return None


def normalize_stage(text: str) -> str:
    low = (text or "").strip().lower()
    if re.match(r"^(a|1)([).:]|\b)", low) or "just starting" in low or "never worked" in low:
        return "just_starting"
    if re.match(r"^(b|2)([).:]|\b)", low) or "1-3" in low or "gifted" in low:
        return "early_stage"
    if re.match(r"^(c|3)([).:]|\b)", low) or "5+" in low:
        return "growing"
    if re.match(r"^(d|4)([).:]|\b)", low) or "established" in low or "scale" in low:
        return "established"
    for needle, stage in STAGE_MAP:
        if needle in low:
            return stage
    return "early_stage"


def stated_niches(notes: Optional[Dict] = None) -> List[str]:
    notes = notes or {}
    raw = notes.get("niche") or []
    if isinstance(raw, str):
        raw = [raw]
    out = []
    for item in raw:
        token = str(item).strip().lower()
        if token and token not in out:
            out.append(token)
    return out


def categories_for_notes(notes: Optional[Dict] = None) -> List[str]:
    cats = []
    for niche in stated_niches(notes):
        for cat in NICHE_CATEGORIES.get(niche, [niche]):
            if cat not in cats:
                cats.append(cat)
    return _drop_fitness_if_beauty(cats)


def _drop_fitness_if_beauty(cats: List[str]) -> List[str]:
    """Wellness ≠ fitness. If the creator is in beauty/skincare, drop gym brands."""
    if any(c in _BEAUTY_MATCH_CATS for c in cats) and any(c in _FITNESS_MATCH_CATS for c in cats):
        return [c for c in cats if c not in _FITNESS_MATCH_CATS]
    return cats


def required_categories_for_match(
    notes: Optional[Dict] = None,
    scrape: Optional[Dict] = None,
) -> List[str]:
    """Notes niche wins. If discovery was skipped, use scrape — still wellness ≠ fitness."""
    cats = categories_for_notes(notes)
    if cats:
        return cats
    scrape = scrape or {}
    tokens: List[str] = []
    raw_primary = scrape.get("primary_niche")
    if raw_primary:
        for part in re.split(r"[&,/]| and ", str(raw_primary).lower()):
            token = part.strip()
            if token and token not in tokens:
                tokens.append(token)
    secondary = scrape.get("secondary_niches") or []
    if isinstance(secondary, str):
        secondary = [secondary]
    for item in secondary:
        token = str(item).strip().lower()
        if token and token not in tokens:
            tokens.append(token)
    cats = []
    for niche in tokens:
        for cat in NICHE_CATEGORIES.get(niche, [niche] if niche else []):
            if cat and cat not in cats:
                cats.append(cat)
    return _drop_fitness_if_beauty(cats)


def apply_answer(notes: Dict[str, Any], field: str, text: str) -> Dict[str, Any]:
    out = dict(notes or {})
    raw = (text or "").strip()
    if not raw:
        return out
    if field == "stage":
        out["stage"] = normalize_stage(raw)
        out["stage_raw"] = raw[:240]
    elif field == "niche":
        parts = [part.strip() for part in raw.replace("/", ",").split(",") if part.strip()]
        parts = [p for p in parts if not _is_empty_answer(p)]
        out["niche"] = parts[:4]
        out["niche_raw"] = raw[:240]
    elif field == "dream_brands":
        names = [part.strip() for part in raw.replace(" and ", ",").split(",") if part.strip()]
        names = [n for n in names if not _is_empty_answer(n)]
        out["dream_brands"] = names[:8]
        out["dream_brands_raw"] = raw[:240]
    else:
        out[field] = "" if _is_empty_answer(raw) else raw[:400]
    missing = next_field(out)
    if missing is None:
        out["discovery_completed_at"] = out.get("discovery_completed_at") or utc_now()
        out["discovery_step"] = 7
    else:
        out["discovery_step"] = FIELDS.index(missing) + 1
    out["updated_at"] = utc_now()
    return assign_track(out)


def merge_notes_patch(notes: Optional[Dict], patch: Any) -> Dict[str, Any]:
    out = dict(notes or {})
    if not isinstance(patch, dict):
        return out
    for field in FIELDS:
        if field not in patch or patch[field] in (None, ""):
            continue
        val = patch[field]
        if isinstance(val, list):
            text = ", ".join(str(v) for v in val if v)
        else:
            text = str(val)
        if text.strip():
            out = apply_answer(out, field, text)
    return out


def skip_discovery(notes: Optional[Dict] = None) -> Dict[str, Any]:
    out = dict(notes or {})
    out["discovery_skipped_at"] = utc_now()
    out["updated_at"] = utc_now()
    return assign_track(out)


def opener(first_name: Optional[str] = None) -> str:
    name = (first_name or "").strip()
    hello = f"Hey {name}, welcome 👋" if name else "Hey, welcome 👋"
    return (
        f"{hello}\n\n"
        "I'm Polly. I'll be your manager while you're on Newcollab, so anything you need — "
        "landing brand deals, writing pitches, getting **My Kit** live — you just message me here.\n\n"
        "Before I start finding you brands, I want to make sure I actually get you right. "
        "Mind if I ask you a few quick things about where you're at? It'll take 2 minutes and "
        "everything I do from now on will be shaped around your answers.\n\n"
        "Everything you tell me stays here between us. I use it to work smarter for you, not to "
        "profile you or sell to you. Fair?\n\n"
        "Ready?"
    )


def question_for(field: str, first_name: Optional[str] = None) -> str:
    if field == "goal_30d":
        return (
            "Perfect. Let's start with the big one:\n\n"
            "What are you hoping to achieve on Newcollab in the next 30-60 days?\n\n"
            "Landing your first gifted PR? Getting your Newcollab kit live? Getting paid UGC deals? "
            "Something else? Don't overthink it, just tell me what you're actually after."
        )
    if field == "stage":
        return (
            "Got it, that gives me a lot to work with.\n\n"
            "Where are you in your UGC journey right now? Just so I know how to guide you:\n\n"
            "a) Just starting, never worked with a brand\n"
            "b) Landed 1-3 gifted collabs, want more\n"
            "c) 5+ collabs, chasing paid work now\n"
            "d) Established creator, want to scale"
        )
    if field == "niche":
        return (
            "Right, makes sense. Last few quick ones:\n\n"
            "What's your niche? Beauty, skincare, fashion, lifestyle, fitness, food, wellness, tech — "
            "you can pick 1 or 2."
        )
    if field == "location":
        return (
            "Perfect. And where are you based? Country + city if you're happy sharing — "
            "this matters for shipping."
        )
    if field == "biggest_challenge":
        return (
            "Ok almost done. Two more:\n\n"
            "What's your biggest frustration right now when it comes to landing brand deals? "
            "What's not working?"
        )
    if field == "dream_brands":
        return (
            "Honest, that's useful. And finally:\n\n"
            "What brands are you dreaming of working with? Names or types — no wrong answers, "
            "just gives me a target."
        )
    return "Tell me a bit more so I can work properly for you."


def wrap_up(notes: Optional[Dict] = None, first_name: Optional[str] = None) -> str:
    notes = notes or {}
    name = (first_name or "").strip() or "you"
    stage = notes.get("stage") or "early_stage"
    niche = notes.get("niche") or []
    if isinstance(niche, str):
        niche = [niche]
    niche_bits = [n for n in niche if n and not _is_empty_answer(str(n))]
    niche_txt = ", ".join(niche_bits) if niche_bits else "your lane"
    dreams = notes.get("dream_brands") or []
    if isinstance(dreams, str):
        dreams = [dreams]
    dream_bits = [d for d in dreams if d and not _is_empty_answer(str(d))]
    dream_txt = ", ".join(dream_bits[:3]) if dream_bits else "ones that actually fit"
    challenge = (notes.get("biggest_challenge") or "").strip()
    if not challenge or _is_empty_answer(challenge):
        challenge = "getting a clean first yes"
    return (
        f"Amazing. Ok {name}, I've got everything I need. Here's what I'm going to do with all this:\n\n"
        f"Based on you being {stage.replace('_', ' ')} in {niche_txt}, aiming at "
        f"{dream_txt}, and struggling most with {challenge} — I'm going to line up 3 brands you "
        "should hit up this week. All ones where I think you actually have a shot, not just a spray-and-pray.\n\n"
        "Want me to pull them up now, or do you want to explore the app first and come back?"
    )


def detour_from_action() -> str:
    return (
        "Nice energy — before I pull you brands, let me get you right so I actually pick "
        "ones you've got a shot at. Won't take long, promise.\n\n"
    )


def explain_newcollab(first_name: Optional[str] = None) -> str:
    name = (first_name or "").strip()
    lead = f"Right {name}, short version:" if name else "Right, short version:"
    return (
        f"{lead} Newcollab is how nano and micro creators actually get in "
        "front of brand PR teams without cold-DMing into the void.\n\n"
        "You've got **My Kit** (your public UGC page), a brand pool that's real (not invented), "
        "and me — I match you, draft the pitch, and coach you up the ladder: gifted PR first, "
        "then paid UGC, then retainers. That's the job. Not vibes, deals.\n\n"
        "Want me to get you set up properly so the matches aren't generic?"
    )


def notes_context(notes: Optional[Dict] = None) -> str:
    notes = notes or {}
    if not notes:
        return "Manager notes: discovery not completed yet."
    bits = []
    if notes.get("goal_30d"):
        bits.append(f"30-60 day goal: {notes['goal_30d']}")
    if notes.get("polly_track"):
        bits.append(f"Track: {notes['polly_track']}")
    if notes.get("stage"):
        bits.append(f"UGC stage: {notes['stage']}")
    if notes.get("niche"):
        bits.append(f"Stated niche: {notes['niche']}")
    if notes.get("location"):
        bits.append(f"Based: {notes['location']}")
    if notes.get("biggest_challenge"):
        bits.append(f"Frustration: {notes['biggest_challenge']}")
    if notes.get("dream_brands"):
        bits.append(f"Dream brands: {notes['dream_brands']}")
    if notes.get("pitched_brand_names"):
        bits.append("Already pitched (do not re-pitch): " + ", ".join(str(n) for n in notes["pitched_brand_names"][:12]))
    pending = notes.get("pending_pitch") if isinstance(notes.get("pending_pitch"), dict) else None
    if pending and (pending.get("name") or pending.get("brand_name")):
        bits.append(
            "Draft waiting — NOT sent yet: "
            f"{pending.get('name') or pending.get('brand_name')}. "
            "Ask if they sent it. Do not log Timeline. Do not auto-draft another brand."
        )
    kit_view = notes.get("last_kit_view") if isinstance(notes.get("last_kit_view"), dict) else None
    if kit_view and kit_view.get("brand_name"):
        bits.append(
            f"Latest kit view: {kit_view['brand_name']} opened the pitch kit link "
            "(hot lead — offer a follow-up)"
        )
    from services.polly_pain import pain_context
    pain_line = pain_context(notes)
    if pain_line:
        bits.append(pain_line)
    if notes.get("discovery_completed_at"):
        bits.append("Discovery complete.")
    elif notes.get("discovery_skipped_at"):
        bits.append("Discovery skipped — still try to learn as they chat.")
    return "Manager notes (long-term memory — reference these):\n" + (
        "\n".join(f"- {b}" for b in bits) if bits else "still collecting."
    )


def starters_for(notes: Optional[Dict] = None, top_brand: Optional[str] = None) -> List[Dict[str, Any]]:
    notes = notes or {}
    if not discovery_complete(notes):
        if discovery_started(notes):
            return [
                {"id": "continue_setup", "label": "Continue setup", "action": "discovery"},
                {"id": "skip_setup", "label": "Skip for now", "action": "suggest_brands", "skip_discovery": True},
            ]
        return [
            {"id": "get_set_up", "label": "Get me set up", "action": "discovery"},
            {"id": "what_is_nc", "label": "What is Newcollab?", "action": "explain_newcollab"},
            {"id": "first_deal", "label": "Help me land my first brand deal", "action": "discovery"},
        ]
    chips = [
        {"id": "line_up", "label": "Line up brands for me today", "action": "suggest_brands"},
        {"id": "week_plan", "label": "What should I do this week?", "action": "coach_week"},
        {"id": "portfolio", "label": "Review my kit", "action": "coach_portfolio"},
        {"id": "rates", "label": "Set my rates", "action": "coach_rates"},
    ]
    if (notes.get("polly_track") or "") == "established":
        chips = [
            {"id": "line_up", "label": "Line up brands for me today", "action": "suggest_brands"},
            {"id": "week_plan", "label": "What should I do this week?", "action": "coach_week"},
            {"id": "rates", "label": "Set my rates", "action": "coach_rates"},
            {"id": "portfolio", "label": "Review my kit", "action": "coach_portfolio"},
        ]
    if top_brand:
        pitched = {str(n).strip().lower() for n in (notes.get("pitched_brand_names") or []) if n}
        if top_brand.strip().lower() in pitched:
            top_brand = None
    if top_brand:
        chips.insert(1, {
            "id": "pitch_top",
            "label": f"Help me pitch {top_brand}",
            "action": "generate_pitch",
            "brand_name": top_brand,
        })
    return chips


def advance(
    notes: Optional[Dict],
    user_text: str,
    first_name: Optional[str] = None,
    history: Optional[List] = None,
) -> Tuple[Dict[str, Any], str, bool]:
    """Apply an answer (or start) and return (notes, say, pull_brands)."""
    notes = dict(notes or {})
    asked = field_from_history(history)
    field = asked or next_field(notes)
    pull = False
    if field is None:
        notes["discovery_completed_at"] = notes.get("discovery_completed_at") or utc_now()
        return notes, wrap_up(notes, first_name), True

    starting = not discovery_started(notes)
    if starting:
        notes["discovery_step"] = 1
        notes["updated_at"] = utc_now()
        return notes, question_for(next_field(notes) or field, first_name), False

    notes = apply_answer(notes, field, user_text)
    nxt = next_field(notes)
    if nxt is None:
        return notes, wrap_up(notes, first_name), True
    return notes, question_for(nxt, first_name), pull
