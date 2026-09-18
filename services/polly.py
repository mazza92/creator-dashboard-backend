"""
Polly — Gemini deal agent that orchestrates existing Newcollab tools.

Does not invent brands, emails, or follower counts. Brand suggestions come
from For You matching; pitches come from generate-pr-package.
"""

from __future__ import annotations

import json
import os
import re
import time
from decimal import Decimal
from typing import Any, Dict, List, Optional
from urllib.parse import quote

import requests

from services.polly_persona import (
    persona_brand_intro,
    persona_followup,
    persona_greeting,
    polly_system_prompt,
)

POLLY_BCC = "creators@newcollab.co"
MAX_SUGGESTED_BRANDS = 3
MAX_HISTORY_TURNS = 24
DEFAULT_POLLY_MODEL = "gemini-2.5-flash"
_MODEL_FALLBACKS = (
    "gemini-2.5-flash",
    "gemini-3.5-flash",
    "gemini-3.6-flash",
    "gemini-3.5-flash-lite",
)
DEFAULT_ANTHROPIC_MODEL = "claude-haiku-4-5"
_GEMINI_DEPLETED = False
_LAST_BRAIN = {"provider": None, "model": None}
_TURN_COSTS = []

_AFFIRM_RE = re.compile(
    r"^(y|yes|yeah|yep|yup|sure|ok|okay|please|go ahead|do it|show me|sounds good)[\s!.]*$",
    re.I,
)

_BRAND_ASK_PREFIX_RE = re.compile(
    r"(?i)^(i\s+(?:want|wanna|need)|can\s+(?:i|we)\s+(?:get|do|pitch|try)|"
    r"please\s+(?:pitch|draft|do)|how\s+about|what\s+about|maybe|"
    r"let'?s\s+(?:hit up|pitch|try|do)|hit up|pitch|contact|reach out to|"
    r"find(?:ing)?|fin|search(?:ing)?(?:\s+for)?|look(?:ing)?(?:\s+for)?)\s+"
)
_ASK_FILLER = frozenset({
    "fin", "find", "finding", "search", "searching", "look", "looking",
    "want", "wanna", "need", "get", "show", "give", "me", "us", "a", "an",
    "the", "for", "up", "to", "please", "can", "i", "we", "try", "pitch",
    "contact", "about", "maybe", "how", "what", "brand", "brands",
})
_CHIP_SKIP_LABELS = frozenset({
    "continue setup", "skip", "skip for now", "not now", "later",
    "edit my kit", "view live kit", "get me set up", "keep going on my kit",
    "help me land my first brand deal", "skip, show me brands",
    "line up brands for me today", "what is newcollab?", "i published my kit",
    "review my kit", "more brands", "i sent it", "next brand to pitch",
    "find me 3 brands to pitch today", "write a pitch for a brand i name",
    "help me get more replies from brands",
})
_CONTACT_RE = re.compile(
    r"\b(contact|pitch|email|reach out|write to|mailto|message|send (it|this|the pitch)|open (the )?mail)\b",
    re.I,
)
_SUGGEST_RE = re.compile(
    r"\b(suggest|recommend|match|for you|who should|find brand|show me brand|"
    r"line up|hit up|brands? to (reach|pitch|contact)|which brand|what brand)\b",
    re.I,
)
_EXPLAIN_RE = re.compile(r"\bwhat('?s| is) newcollab\b", re.I)
_COACH_WEEK_RE = re.compile(r"\b(this week|what should i do|action plan)\b", re.I)
_COACH_PROFILE_RE = re.compile(
    r"\b(more replies|get more replies|profile audit|why (don'?t|do not) brands reply)\b",
    re.I,
)
_COACH_KIT_RE = re.compile(
    r"\b(portfolio|my kit|media kit|review my (kit|portfolio)|ugc kit)\b|newcollab\.co/kit/",
    re.I,
)
_COACH_RATES_RE = re.compile(r"\b(rate card|my rates|how much (should|do) i charge|set my rates)\b", re.I)

_BRAND_CARD_KEYS = (
    "id",
    "slug",
    "name",
    "logo",
    "logo_url",
    "description",
    "category",
    "match_score",
    "fit_tier",
    "fit_label",
    "website",
    "application_form_url",
    "source",
    "why",
)


def unpack_view_result(resp: Any) -> tuple:
    """Flask views often return (jsonify(...), status). Calling them in-process
    yields a tuple, not a Response with status_code."""
    status = 200
    body = resp
    if isinstance(resp, tuple):
        body = resp[0] if resp else None
        if len(resp) > 1 and isinstance(resp[1], int):
            status = resp[1]
    elif body is not None:
        status = getattr(body, "status_code", 200) or 200
    data = None
    if isinstance(body, dict):
        data = body
    elif body is not None and hasattr(body, "get_json"):
        try:
            data = body.get_json(silent=True)
        except TypeError:
            data = body.get_json()
        except Exception:
            data = None
    return status, data or {}


def json_safe(obj: Any) -> Any:
    if isinstance(obj, Decimal):
        return int(obj) if obj == obj.to_integral_value() else float(obj)
    if isinstance(obj, dict):
        return {k: json_safe(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [json_safe(item) for item in obj]
    if hasattr(obj, "isoformat"):
        return obj.isoformat()
    return obj


def build_profile_context(scrape: Optional[Dict], creator: Optional[Dict] = None) -> str:
    """Compact scrape+signup context for every Polly turn. Never invents stats."""
    scrape = scrape or {}
    creator = creator or {}
    lines = []

    handle = scrape.get("handle") or creator.get("instagram_handle") or creator.get("tiktok_handle")
    if handle:
        lines.append(f"Handle: @{str(handle).lstrip('@')}")

    platform = scrape.get("primary_platform") or scrape.get("platform")
    if platform:
        lines.append(f"Platform: {platform}")

    name = (scrape.get("full_name") or "").strip()
    if not name:
        first = (creator.get("first_name") or "").strip()
        last = (creator.get("last_name") or "").strip()
        name = f"{first} {last}".strip()
    if name:
        lines.append(f"Display name: {name}")

    niche = scrape.get("primary_niche") or creator.get("niche")
    if niche:
        lines.append(f"Primary niche: {niche}")

    secondary = scrape.get("secondary_niches") or creator.get("creator_niches") or []
    if isinstance(secondary, str):
        try:
            secondary = json.loads(secondary)
        except Exception:
            secondary = [n.strip() for n in secondary.split(",") if n.strip()]
    if isinstance(secondary, list) and secondary:
        lines.append("Secondary niches: " + ", ".join(str(n) for n in secondary[:8] if n))

    followers = scrape.get("follower_count")
    if followers in (None, "", 0, "0"):
        followers = None
    if followers is not None:
        try:
            lines.append(f"Followers (from scrape): {int(followers)}")
        except (TypeError, ValueError):
            pass

    bio = (scrape.get("raw_bio") or scrape.get("bio") or "").strip()
    if bio:
        lines.append(f"Bio: {bio[:280]}")

    themes = scrape.get("content_themes") or []
    if isinstance(themes, str):
        try:
            themes = json.loads(themes)
        except Exception:
            themes = []
    if isinstance(themes, list) and themes:
        lines.append("Content themes: " + ", ".join(str(t) for t in themes[:8] if t))

    aesthetic = scrape.get("aesthetic") or {}
    if isinstance(aesthetic, dict):
        vibe = aesthetic.get("overall_vibe") or aesthetic.get("vibe")
        if vibe:
            lines.append(f"Aesthetic: {vibe}")

    captions = scrape.get("recent_captions") or []
    if isinstance(captions, str):
        try:
            captions = json.loads(captions)
        except Exception:
            captions = []
    snippets = []
    for cap in captions[:4]:
        text = re.sub(r"\s+", " ", str(cap or "")).strip()
        if text:
            snippets.append(text[:140])
    if snippets:
        lines.append("Recent captions: " + " | ".join(snippets))

    location_bits = [
        scrape.get("city") or creator.get("city"),
        scrape.get("country") or creator.get("country"),
    ]
    location = ", ".join(str(b).strip() for b in location_bits if b)
    if location:
        lines.append(f"Location: {location}")

    if not lines:
        return "No onboarding scrape is stored yet. Do not invent a profile. Ask them to finish social connect if needed."
    return "\n".join(lines)


def public_profile_summary(scrape: Optional[Dict], creator: Optional[Dict] = None) -> Dict[str, Any]:
    """Frontend bootstrap — no emails, no invented follower counts."""
    scrape = scrape or {}
    creator = creator or {}
    followers = scrape.get("follower_count")
    try:
        followers = int(followers) if followers not in (None, "", 0, "0") else None
    except (TypeError, ValueError):
        followers = None
    handle = scrape.get("handle") or creator.get("instagram_handle") or creator.get("tiktok_handle")
    first = (creator.get("first_name") or "").strip()
    return {
        "handle": str(handle).lstrip("@") if handle else None,
        "first_name": first or None,
        "primary_niche": scrape.get("primary_niche") or creator.get("niche"),
        "platform": scrape.get("primary_platform") or scrape.get("platform"),
        "has_scrape": bool(scrape.get("handle") or scrape.get("primary_niche") or scrape.get("raw_bio")),
        "follower_count": followers,
        "creator_id": creator.get("id") or creator.get("creator_id"),
    }


def sanitize_brand_card(row: Any, source: str = "matched") -> Optional[Dict[str, Any]]:
    if not row:
        return None
    brand = dict(row) if not isinstance(row, dict) else dict(row)
    brand_id = brand.get("id") or brand.get("brand_id")
    name = brand.get("name") or brand.get("brand_name")
    slug = brand.get("slug")
    if not brand_id or not name:
        return None
    desc = brand_card_blurb(brand)
    logo = brand.get("logo") or brand.get("logo_url")
    card = {
        "id": int(brand_id),
        "slug": slug,
        "name": name,
        "logo": logo,
        "logo_url": logo,
        "description": desc or None,
        "category": brand.get("category"),
        "match_score": json_safe(brand.get("match_score")),
        "fit_tier": brand.get("fit_tier"),
        "fit_label": brand.get("fit_label"),
        "website": brand.get("website"),
        "application_form_url": brand.get("application_form_url"),
        "source": source,
        "why": desc or None,
    }
    return {k: v for k, v in card.items() if k in _BRAND_CARD_KEYS}


def brand_card_blurb(brand: Optional[Dict] = None) -> str:
    """Brand short description for match cards. Never the overlap 'already sit in' line."""
    brand = brand or {}
    desc = re.sub(r"\s+", " ", str(brand.get("description") or "").strip())
    if re.search(r"already sit in", desc, re.I):
        desc = ""
    if not desc:
        return ""
    if len(desc) > 160:
        return desc[:157].rstrip() + "…"
    return desc


def flatten_for_you(
    payload: Optional[Dict],
    limit: int = MAX_SUGGESTED_BRANDS,
    scrape: Optional[Dict] = None,
    niches: Optional[List] = None,
    required_categories: Optional[List] = None,
    exclude_ids: Optional[List] = None,
) -> List[Dict[str, Any]]:
    """Live PR rosters first, then niche/follower/location matches."""
    payload = payload or {}
    required = [str(c).lower().strip() for c in (required_categories or []) if c]
    skip_ids = set()
    for item in exclude_ids or []:
        try:
            skip_ids.add(int(item))
        except (TypeError, ValueError):
            continue
    sections = (
        ("recruiting", 0, payload.get("recruiting") or []),
        ("open_lists", 0, payload.get("open_lists") or []),
        ("matched", 2, payload.get("matched") or []),
        ("hot", 3, payload.get("hot") or []),
        ("newest", 4, payload.get("newest") or []),
        ("seasonal", 5, payload.get("seasonal") or []),
    )
    from services.audience_fit import (
        audience_mismatch,
        creator_follower_count,
        opportunity_mismatch,
        scrape_overlap_score,
    )

    ranked = []
    seen = set()
    for source, source_rank, rows in sections:
        for row in rows:
            try:
                rid = int((row or {}).get("id") or (row or {}).get("brand_id") or 0)
            except (TypeError, ValueError):
                rid = 0
            if rid and rid in skip_ids:
                continue
            cat = str((row or {}).get("category") or "").lower()
            try:
                live = source in ("recruiting", "open_lists") or int((row or {}).get("roster_is_open") or (row or {}).get("roster_spotlighted") or 0)
            except (TypeError, ValueError):
                live = source in ("recruiting", "open_lists")
            if required and cat and not any(req in cat or cat in req for req in required):
                continue
            if opportunity_mismatch(row, scrape, niches, required):
                continue
            if scrape is not None and audience_mismatch(row, scrape, niches):
                continue
            card = sanitize_brand_card(row, source=source)
            if not card or card["id"] in seen:
                continue
            seen.add(card["id"])
            overlap = scrape_overlap_score(row, scrape, niches) if scrape is not None else 0
            try:
                score = int(card.get("match_score") or 0)
            except (TypeError, ValueError):
                score = 0
            campaign_rank = 0 if live else source_rank
            reach_rank = 1
            if (row or {}).get("micro_friendly"):
                reach_rank = 0
            try:
                min_followers = int((row or {}).get("min_followers") or 0)
            except (TypeError, ValueError):
                min_followers = 0
            followers = creator_follower_count(scrape)
            if followers and min_followers and followers >= min_followers:
                reach_rank = 0
            elif min_followers and followers and min_followers > followers:
                reach_rank = 2
            ranked.append((campaign_rank, reach_rank, -overlap, -score, len(ranked), card))
            if scrape is None and not required and not skip_ids and len(ranked) >= limit:
                return [item[-1] for item in ranked]
    ranked.sort()
    return [item[-1] for item in ranked[:limit]]


def resolve_brand(
    suggested: Optional[List[Dict]],
    brand_id: Any = None,
    brand_name: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    suggested = [s for s in (suggested or []) if isinstance(s, dict)]
    if brand_id not in (None, "", 0, "0"):
        try:
            bid = int(brand_id)
        except (TypeError, ValueError):
            bid = None
        if bid is not None:
            for b in suggested:
                try:
                    if int(b.get("id") or 0) == bid:
                        return b
                except (TypeError, ValueError):
                    continue
    needle = (brand_name or "").strip().lower()
    if needle:
        for b in suggested:
            name = str(b.get("name") or "").strip().lower()
            slug = str(b.get("slug") or "").strip().lower()
            if needle == name or needle == slug or needle in name:
                return b
    return None


def strip_brand_ask(text: str) -> str:
    """Turn 'I want DELL' / 'Let's hit up Grace & Stella' into the brand name."""
    raw = (text or "").strip()
    cleaned = _BRAND_ASK_PREFIX_RE.sub("", raw).strip(" .!,")
    return cleaned or raw


def asked_brand_query(text: str) -> str:
    """Drop find/fin/want filler so 'fin Dell' looks up Dell."""
    stripped = strip_brand_ask(text)
    parts = [
        part.strip(" .!,")
        for part in stripped.split()
        if part.strip(" .!,").lower() not in _ASK_FILLER
    ]
    cleaned = " ".join(parts).strip(" .!,")
    return cleaned or stripped


def brand_lookup_names(text: str) -> List[str]:
    asked = asked_brand_query(text)
    names = []
    for item in (asked, strip_brand_ask(text), (text or "").strip()):
        val = (item or "").strip(" .!,")
        if val and val.lower() not in {n.lower() for n in names}:
            names.append(val)
    parts = asked.split()
    if len(parts) > 1:
        last = parts[-1].strip(" .!,")
        if last and last.lower() not in {n.lower() for n in names} and len(last) >= 2:
            names.append(last)
    return names


def brand_in_suggested(suggested: Optional[List[Dict]], brand: Optional[Dict]) -> bool:
    if not brand:
        return False
    return resolve_brand(
        suggested,
        brand_id=brand.get("id") or brand.get("brand_id"),
        brand_name=brand.get("name") or brand.get("brand_name"),
    ) is not None


def names_mentioned_by_assistant(history: Optional[List[Dict]]) -> List[Dict[str, Any]]:
    """Brand cards, pitch cards, and **Name** mentions from recent assistant turns."""
    found: List[Dict[str, Any]] = []
    seen = set()
    for msg in reversed(history or []):
        if (msg.get("role") or "").lower() != "assistant":
            continue
        rows = list(msg.get("brands") or [])
        pitch = msg.get("pitch")
        if isinstance(pitch, dict) and (pitch.get("brand_name") or pitch.get("name")):
            rows.append({
                "id": pitch.get("brand_id") or pitch.get("id"),
                "name": pitch.get("brand_name") or pitch.get("name"),
            })
        for match in re.finditer(r"\*\*([^*]{2,48})\*\*", msg.get("content") or ""):
            rows.append({"name": match.group(1).strip()})
        for row in rows:
            if not isinstance(row, dict):
                continue
            name = str(row.get("name") or row.get("brand_name") or "").strip()
            key = name.lower()
            if not key or key in seen:
                continue
            seen.add(key)
            found.append(row)
        if len(found) >= 8:
            break
    return found


def looks_like_brand_request(text: str, history: Optional[List[Dict]] = None) -> bool:
    """True when the user is naming / asking for a specific brand, not answering discovery."""
    raw = (text or "").strip()
    if not raw or is_done_turn(raw) or is_more_brands_turn(raw):
        return False
    if _AFFIRM_RE.match(raw):
        return False
    if _EXPLAIN_RE.search(raw) or _COACH_KIT_RE.search(raw) or _COACH_RATES_RE.search(raw) or _COACH_WEEK_RE.search(raw):
        return False
    last_assistant = ""
    for msg in reversed(history or []):
        if (msg.get("role") or "").lower() == "assistant":
            last_assistant = (msg.get("content") or "").lower()
            break
    if any(hint in last_assistant for hint in (
        "where you're based", "where are you based", "country + city",
        "hoping to achieve", "30 days", "biggest frustration", "what's your niche",
        "ugc journey", "dreaming of working",
    )):
        return False
    if raw.lower() in _CHIP_SKIP_LABELS:
        return False
    words = raw.split()
    if len(words) > 8 or len(raw) > 60:
        return False
    if resolve_brand(None, brand_name=raw):
        return True
    needle = strip_brand_ask(raw).lower()
    for row in names_mentioned_by_assistant(history):
        name = str(row.get("name") or "").strip().lower()
        if name and (needle == name or name in needle or needle in name):
            return True
    if _CONTACT_RE.search(raw) or re.search(r"\b(hit up|let'?s (try|do|pitch)|pitch|i want)\b", raw, re.I):
        return True
    if 1 <= len(words) <= 5 and not raw.endswith("?"):
        return True
    return False


def requested_brand_name(
    text: str,
    suggested: Optional[List[Dict]] = None,
    history: Optional[List[Dict]] = None,
    brand_id: Any = None,
    brand_name: Optional[str] = None,
) -> Optional[str]:
    raw = (text or "").strip()
    typed = asked_brand_query(raw) if looks_like_brand_request(raw, history) else ""
    typed = (typed or "").strip()
    # What they typed wins over the model (which often repeats the pending draft brand).
    needle = typed or (brand_name or "").strip() or raw
    resolved = resolve_brand(suggested, brand_id=None if typed else brand_id, brand_name=needle)
    if not resolved:
        for candidate in brand_lookup_names(needle):
            resolved = resolve_brand(suggested, brand_name=candidate) or resolve_brand(
                names_mentioned_by_assistant(history),
                brand_name=candidate,
            )
            if resolved:
                break
    if resolved and resolved.get("name"):
        if typed and strip_brand_ask(resolved["name"]).lower() != typed.lower() and typed.lower() not in str(resolved.get("name") or "").lower():
            return typed
        return str(resolved.get("name"))
    if typed:
        return typed
    if (brand_name or "").strip():
        return strip_brand_ask(str(brand_name).strip())
    return None


def build_mailto(email: str, subject: str, body: str, bcc: str = POLLY_BCC) -> Optional[str]:
    email = (email or "").strip()
    if not email or "@" not in email:
        return None
    return (
        f"mailto:{email}"
        f"?subject={quote(subject or '')}"
        f"&body={quote(body or '')}"
        f"&bcc={quote(bcc)}"
    )


def pitch_from_package_response(data: Optional[Dict]) -> Optional[Dict[str, Any]]:
    data = data or {}
    package = data.get("package") or {}
    pitches = package.get("pitches") or {}
    growing = pitches.get("growing") or pitches.get("short") or pitches.get("founder") or {}
    subject = growing.get("subject") or ""
    body = growing.get("body_plain") or ""
    email = data.get("brand_email")
    brand = package.get("brand") or {}
    mailto = build_mailto(email, subject, body)
    if not subject and not body:
        return None
    return {
        "brand_id": brand.get("id"),
        "brand_name": brand.get("name"),
        "brand_logo": brand.get("logo_url"),
        "subject": subject,
        "body": body,
        "email": email,
        "mailto": mailto,
        "application_form_url": data.get("application_form_url"),
    }


def pitch_from_followup_response(
    data: Optional[Dict],
    brand: Optional[Dict] = None,
) -> Optional[Dict[str, Any]]:
    """Map /generate-pitch is_followup=True (or package follow_ups) onto a Polly card."""
    data = data or {}
    brand = brand or {}
    subject = (data.get("subject") or "").strip()
    body = (data.get("body") or data.get("body_plain") or "").strip()
    if not subject and not body:
        follow_ups = (data.get("package") or {}).get("follow_ups") or data.get("follow_ups") or {}
        for key in ("day3", "day8", "day14"):
            row = follow_ups.get(key) or {}
            subject = (row.get("subject") or "").strip()
            body = (row.get("body") or row.get("body_plain") or "").strip()
            if subject or body:
                break
    email = data.get("brand_email") or data.get("email")
    mailto = build_mailto(email, subject, body)
    if not subject and not body:
        return None
    name = (
        data.get("brand_name")
        or brand.get("name")
        or brand.get("brand_name")
        or (data.get("package") or {}).get("brand", {}).get("name")
    )
    bid = data.get("brand_id") or brand.get("id") or brand.get("brand_id")
    return {
        "brand_id": bid,
        "brand_name": name,
        "brand_logo": data.get("brand_logo") or brand.get("logo") or brand.get("logo_url"),
        "subject": subject,
        "body": body,
        "email": email,
        "mailto": mailto,
        "application_form_url": data.get("application_form_url") or brand.get("application_form_url"),
        "is_followup": True,
        "kind": "followup",
    }


def wants_followup_pitch(data: Optional[Dict] = None, user_text: str = "") -> bool:
    data = data or {}
    chip = str(data.get("starter") or data.get("chip_id") or "").strip().lower()
    if data.get("is_followup") or chip in ("draft_followup", "draft_it", "draft_final"):
        return True
    low = (user_text or "").lower()
    if "follow-up" in low:
        return True
    return "follow up" in low and any(w in low for w in ("draft", "bump", "nudge"))


def _load_env() -> None:
    try:
        from pathlib import Path
        from dotenv import load_dotenv
        load_dotenv(Path(__file__).resolve().parents[1] / ".env")
    except Exception:
        pass


def get_gemini_key() -> str:
    key = (os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_AI_API_KEY") or "").strip()
    if key:
        return key
    _load_env()
    return (os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_AI_API_KEY") or "").strip()


def get_anthropic_key() -> str:
    key = (os.getenv("ANTHROPIC_API_KEY") or "").strip()
    if key:
        return key
    _load_env()
    return (os.getenv("ANTHROPIC_API_KEY") or "").strip()


def get_polly_model() -> str:
    return (
        os.getenv("GEMINI_POLLY_MODEL")
        or os.getenv("GEMINI_PR_READY_MODEL")
        or DEFAULT_POLLY_MODEL
    )


def get_anthropic_model() -> str:
    return os.getenv("POLLY_ANTHROPIC_MODEL") or DEFAULT_ANTHROPIC_MODEL


def llm_disabled() -> bool:
    return os.getenv("POLLY_DISABLE_LLM", "").strip().lower() in {"1", "true", "yes"}


def gemini_available() -> bool:
    return bool(get_gemini_key()) and not llm_disabled()


def llm_available() -> bool:
    if llm_disabled():
        return False
    return bool(get_gemini_key() or get_anthropic_key())


def begin_polly_turn() -> None:
    global _LAST_BRAIN, _TURN_COSTS
    _LAST_BRAIN = {"provider": None, "model": None}
    _TURN_COSTS = []


def remember_polly_brain(
    provider: Optional[str],
    model: Optional[str] = None,
    usage: Optional[Dict[str, Any]] = None,
) -> None:
    global _LAST_BRAIN, _TURN_COSTS
    _LAST_BRAIN = {"provider": provider, "model": model}
    if isinstance(usage, dict) and usage:
        _TURN_COSTS.append(usage)


def last_polly_brain() -> Dict[str, Optional[str]]:
    return dict(_LAST_BRAIN)


def last_polly_cost() -> Dict[str, Any]:
    from services.polly_llm_cost import rollup_usage
    return rollup_usage(_TURN_COSTS)


def _history_lines(history: Optional[List[Dict]]) -> List[str]:
    lines = []
    for msg in (history or [])[-MAX_HISTORY_TURNS:]:
        role = msg.get("role") or "user"
        content = (msg.get("content") or "").strip()
        if content:
            lines.append(f"{role}: {content[:800]}")
    return lines


def _turn_messages(history: Optional[List[Dict]], latest_user: str = "") -> List[Dict[str, str]]:
    msgs: List[Dict[str, str]] = []
    for msg in (history or [])[-MAX_HISTORY_TURNS:]:
        role = "assistant" if (msg.get("role") or "").lower() == "assistant" else "user"
        content = (msg.get("content") or "").strip()
        if not content:
            continue
        if msgs and msgs[-1]["role"] == role:
            msgs[-1]["content"] = (msgs[-1]["content"] + "\n" + content)[:4000]
        else:
            msgs.append({"role": role, "content": content[:2000]})
    latest = (latest_user or "").strip()
    if latest:
        if msgs and msgs[-1]["role"] == "user":
            if latest not in msgs[-1]["content"]:
                msgs[-1]["content"] = (msgs[-1]["content"] + "\n" + latest)[:4000]
        else:
            msgs.append({"role": "user", "content": latest[:2000]})
    if msgs and msgs[0]["role"] != "user":
        msgs.insert(0, {"role": "user", "content": "(continuing)"})
    return msgs


def _parse_json_text(text: str) -> Dict[str, Any]:
    text = (text or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    return json.loads(text)


def profile_aware_chat(profile_context: str, text: str = "", first_name: Optional[str] = None) -> str:
    """Offline Polly Reid voice when the LLM is down."""
    return persona_greeting(profile_context, first_name=first_name)


_DONE_RE = re.compile(
    r"^(yep,? )?(i('ve| have| )?sent( it)?|sent it|done|finished|thanks|thank you|"
    r"got it( out)?|that'?s it|ok done|done zo)[\s!.]*$",
    re.I,
)
_MORE_RE = re.compile(
    r"^(more|another|next|more brands|show more|next one|another one)[\s!.]*$",
    re.I,
)
_MORE_PHRASE_RE = re.compile(
    r"\b(more brands|show me more|another brand|next brand)\b",
    re.I,
)


def is_done_turn(text: str) -> bool:
    return bool(_DONE_RE.match((text or "").strip()))


def is_more_brands_turn(text: str) -> bool:
    raw = (text or "").strip()
    return bool(_MORE_RE.match(raw) or _MORE_PHRASE_RE.search(raw))


_UNCONFIRMED_SENT_RE = re.compile(
    r"pitch is out|already (sent|out)|now that .{0,80}(is out|is sent|pitch is)|"
    r"i('ve| have) logged|on your timeline|logged it",
    re.I,
)


def say_claims_unconfirmed_send(text: str) -> bool:
    """True when copy treats a draft as already sent."""
    return bool(_UNCONFIRMED_SENT_RE.search(text or ""))


def last_pitch_brand(
    history: Optional[List[Dict]] = None,
    notes: Optional[Dict] = None,
) -> Optional[Dict[str, Any]]:
    pending = (notes or {}).get("pending_pitch") if isinstance(notes, dict) else None
    if isinstance(pending, dict):
        bid = pending.get("id") or pending.get("brand_id")
        name = pending.get("name") or pending.get("brand_name")
        if bid or name:
            return {"id": bid, "name": name, "brand_id": bid, "brand_name": name}
    for msg in reversed(history or []):
        if (msg.get("role") or "").lower() != "assistant":
            continue
        pitch = msg.get("pitch")
        if not isinstance(pitch, dict):
            continue
        bid = pitch.get("brand_id") or pitch.get("id")
        name = pitch.get("brand_name") or pitch.get("name")
        if bid or name:
            return {"id": bid, "name": name}
    return None


def pitched_id_set(notes: Optional[Dict] = None) -> set:
    out = set()
    for item in (notes or {}).get("pitched_brand_ids") or []:
        try:
            out.add(int(item))
        except (TypeError, ValueError):
            continue
    return out


def mark_pitched(notes: Optional[Dict], brand: Optional[Dict]) -> Dict[str, Any]:
    out = dict(notes or {})
    if not brand:
        return out
    ids = list(out.get("pitched_brand_ids") or [])
    names = list(out.get("pitched_brand_names") or [])
    try:
        bid = int(brand.get("id") or brand.get("brand_id") or 0)
    except (TypeError, ValueError):
        bid = 0
    name = str(brand.get("name") or brand.get("brand_name") or "").strip()
    if bid and bid not in pitched_id_set(out):
        ids.append(bid)
    seen_names = {str(n).strip().lower() for n in names}
    if name and name.lower() not in seen_names:
        names.append(name)
    out["pitched_brand_ids"] = ids[-40:]
    out["pitched_brand_names"] = names[-40:]
    out.pop("pending_pitch", None)
    return out


def unmark_pitched(notes: Optional[Dict], brand: Optional[Dict]) -> Dict[str, Any]:
    """Undo a false send. Draft is pending again — not on Timeline as sent."""
    out = mark_draft_pending(notes, brand)
    try:
        bid = int((brand or {}).get("id") or (brand or {}).get("brand_id") or 0)
    except (TypeError, ValueError):
        bid = 0
    name = str((brand or {}).get("name") or (brand or {}).get("brand_name") or "").strip().lower()
    if bid:
        out["pitched_brand_ids"] = [
            item for item in (out.get("pitched_brand_ids") or [])
            if int(item or 0) != bid
        ]
    if name:
        out["pitched_brand_names"] = [
            item for item in (out.get("pitched_brand_names") or [])
            if str(item).strip().lower() != name
        ]
    return out


def mark_draft_pending(notes: Optional[Dict], brand: Optional[Dict]) -> Dict[str, Any]:
    """Remember a drafted pitch that has not been sent yet. Not Timeline."""
    out = dict(notes or {})
    if not brand:
        return out
    try:
        bid = int(brand.get("id") or brand.get("brand_id") or 0) or None
    except (TypeError, ValueError):
        bid = None
    name = str(brand.get("name") or brand.get("brand_name") or "").strip()
    if not bid and not name:
        return out
    out["pending_pitch"] = {
        "id": bid,
        "brand_id": bid,
        "name": name or None,
        "brand_name": name or None,
    }
    return out


def pitch_confirm_chips(brand: Optional[Dict] = None) -> List[Dict[str, Any]]:
    brand = brand or {}
    bid = brand.get("id") or brand.get("brand_id")
    name = brand.get("name") or brand.get("brand_name")
    return [
        {
            "id": "i_sent_it",
            "label": "I sent it",
            "action": "chat",
            "brand_id": bid,
            "brand_name": name,
        },
        {
            "id": "more_brands",
            "label": "More brands",
            "action": "suggest_brands",
        },
    ]


def drop_pending_draft(
    brands: Optional[List[Dict]],
    notes: Optional[Dict] = None,
) -> List[Dict[str, Any]]:
    """Hide the unsent draft from a 'more brands' list. Do not mark it pitched."""
    pending = (notes or {}).get("pending_pitch") if isinstance(notes, dict) else None
    if not isinstance(pending, dict):
        return [b for b in (brands or []) if isinstance(b, dict)]
    skip_ids = set()
    try:
        bid = int(pending.get("id") or pending.get("brand_id") or 0)
        if bid:
            skip_ids.add(bid)
    except (TypeError, ValueError):
        pass
    skip_name = str(pending.get("name") or pending.get("brand_name") or "").strip().lower()
    kept = []
    for brand in brands or []:
        if not isinstance(brand, dict):
            continue
        try:
            bid = int(brand.get("id") or 0)
        except (TypeError, ValueError):
            bid = 0
        name = str(brand.get("name") or "").strip().lower()
        if bid and bid in skip_ids:
            continue
        if skip_name and name == skip_name:
            continue
        kept.append(brand)
    return kept


def drop_pitched(
    brands: Optional[List[Dict]],
    notes: Optional[Dict] = None,
    extra_ids: Optional[List] = None,
) -> List[Dict[str, Any]]:
    skip = pitched_id_set(notes)
    for item in extra_ids or []:
        try:
            skip.add(int(item))
        except (TypeError, ValueError):
            continue
    skip_names = {
        str(n).strip().lower()
        for n in (notes or {}).get("pitched_brand_names") or []
        if n
    }
    kept = []
    for brand in brands or []:
        if not isinstance(brand, dict):
            continue
        try:
            bid = int(brand.get("id") or 0)
        except (TypeError, ValueError):
            bid = 0
        name = str(brand.get("name") or "").strip().lower()
        if bid and bid in skip:
            continue
        if name and name in skip_names:
            continue
        kept.append(brand)
    return kept


def classify_intent_heuristic(
    text: str,
    suggested_brands: Optional[List[Dict]] = None,
    brand_id: Any = None,
    history: Optional[List[Dict]] = None,
) -> Dict[str, Any]:
    if brand_id not in (None, "", 0, "0"):
        resolved = resolve_brand(suggested_brands, brand_id=brand_id)
        return {
            "intent": "generate_pitch",
            "say": "",
            "brand_id": resolved.get("id") if resolved else brand_id,
            "brand_name": resolved.get("name") if resolved else None,
        }
    raw = (text or "").strip()
    if is_done_turn(raw):
        return {"intent": "chat", "say": "", "brand_id": None, "brand_name": None}
    if is_more_brands_turn(raw):
        return {"intent": "suggest_brands", "say": "", "brand_id": None, "brand_name": None}
    if _AFFIRM_RE.match(raw):
        last_assistant = ""
        for msg in reversed(history or []):
            if (msg.get("role") or "").lower() == "assistant":
                last_assistant = (msg.get("content") or "").lower()
                break
        if any(word in last_assistant for word in ("match", "brand", "pool", "pitch", "reach")):
            return {"intent": "suggest_brands", "say": "", "brand_id": None, "brand_name": None}
    if _EXPLAIN_RE.search(raw):
        return {"intent": "explain_newcollab", "say": "", "brand_id": None, "brand_name": None}
    if _COACH_PROFILE_RE.search(raw):
        return {"intent": "coach_profile", "say": "", "brand_id": None, "brand_name": None}
    if _COACH_RATES_RE.search(raw):
        return {"intent": "coach_rates", "say": "", "brand_id": None, "brand_name": None}
    if _COACH_KIT_RE.search(raw):
        return {"intent": "coach_portfolio", "say": "", "brand_id": None, "brand_name": None}
    if _COACH_WEEK_RE.search(raw):
        return {"intent": "coach_week", "say": "", "brand_id": None, "brand_name": None}
    if raw.lower() in ("write a pitch for a brand i name",):
        return {"intent": "ask_brand", "say": "", "brand_id": None, "brand_name": None}
    generic_pool = bool(_SUGGEST_RE.search(raw)) and not looks_like_brand_request(raw, history)
    if generic_pool and not _CONTACT_RE.search(raw):
        return {"intent": "suggest_brands", "say": "", "brand_id": None, "brand_name": None}
    if _CONTACT_RE.search(raw) or looks_like_brand_request(raw, history):
        resolved = resolve_brand(suggested_brands, brand_name=raw)
        if not resolved:
            for brand in suggested_brands or []:
                name = str(brand.get("name") or "")
                if name and name.lower() in raw.lower():
                    resolved = brand
                    break
        if not resolved:
            resolved = resolve_brand(names_mentioned_by_assistant(history), brand_name=raw)
        if resolved:
            return {
                "intent": "generate_pitch",
                "say": "",
                "brand_id": resolved.get("id"),
                "brand_name": resolved.get("name"),
            }
        asked = requested_brand_name(raw, suggested_brands, history)
        if asked and not re.search(r"\b(matches|one of|who should|suggest|recommend)\b", raw, re.I):
            return {
                "intent": "generate_pitch",
                "say": "",
                "brand_id": None,
                "brand_name": asked,
            }
        if suggested_brands or re.search(r"\b(matches|suggest|recommend)\b", raw, re.I):
            return {"intent": "suggest_brands", "say": "", "brand_id": None, "brand_name": None}
        return {
            "intent": "generate_pitch",
            "say": "",
            "brand_id": None,
            "brand_name": asked,
        }
    if _SUGGEST_RE.search(raw):
        return {"intent": "suggest_brands", "say": "", "brand_id": None, "brand_name": None}
    return {"intent": "chat", "say": "", "brand_id": None, "brand_name": None}


def _redact_secrets(text: Any) -> str:
    return re.sub(r"(key=)[^&\s]+", r"\1REDACTED", str(text), flags=re.I)


def _model_candidates() -> List[str]:
    ordered = []
    for model in (get_polly_model(),) + _MODEL_FALLBACKS:
        if model and model not in ordered:
            ordered.append(model)
    return ordered


def _gemini_generate_json(system_prompt: str, user_prompt: str, history: Optional[List[Dict]] = None) -> Dict[str, Any]:
    global _GEMINI_DEPLETED
    if _GEMINI_DEPLETED:
        raise ValueError("Gemini billing credits depleted")
    api_key = get_gemini_key()
    if not api_key:
        raise ValueError("GEMINI_API_KEY not configured")
    contents = []
    for msg in _turn_messages(history):
        role = "model" if msg["role"] == "assistant" else "user"
        contents.append({"role": role, "parts": [{"text": msg["content"]}]})
    if not contents or contents[-1]["role"] != "user":
        contents.append({"role": "user", "parts": [{"text": user_prompt}]})
    else:
        contents[-1]["parts"][0]["text"] += "\n\n" + user_prompt
    payload = {
        "systemInstruction": {"parts": [{"text": system_prompt}]},
        "contents": contents,
        "generationConfig": {
            "temperature": 0.7,
            "responseMimeType": "application/json",
        },
    }
    last_err = "Gemini failed"
    for model in _model_candidates():
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
        headers = {"Content-Type": "application/json", "x-goog-api-key": api_key}
        resp = requests.post(url, json=payload, headers=headers, timeout=45)
        if resp.status_code == 429:
            body = (resp.text or "")[:400]
            if "depleted" in body.lower() or "prepayment" in body.lower():
                _GEMINI_DEPLETED = True
                raise ValueError("Gemini billing credits depleted")
            last_err = f"{model} rate limited"
            time.sleep(0.4)
            continue
        if resp.status_code >= 400:
            last_err = f"{model} HTTP {resp.status_code}"
            continue
        data = resp.json()
        text = (
            data.get("candidates", [{}])[0]
            .get("content", {})
            .get("parts", [{}])[0]
            .get("text", "")
        )
        parsed = _parse_json_text(text)
        from services.polly_llm_cost import usage_from_gemini
        usage = usage_from_gemini(data, model)
        print(
            f"[Polly] brain=gemini model={model} "
            f"in={usage.get('input_tokens')} out={usage.get('output_tokens')} "
            f"usd={usage.get('usd')}"
        )
        remember_polly_brain("gemini", model, usage)
        return parsed
    raise ValueError(_redact_secrets(last_err))


def _anthropic_generate_json(system_prompt: str, user_prompt: str, history: Optional[List[Dict]] = None) -> Dict[str, Any]:
    api_key = get_anthropic_key()
    if not api_key:
        raise ValueError("ANTHROPIC_API_KEY not configured")
    messages = _turn_messages(history)
    if not messages:
        messages = [{"role": "user", "content": user_prompt}]
    elif messages[-1]["role"] == "user":
        messages[-1]["content"] += "\n\n" + user_prompt
    else:
        messages.append({"role": "user", "content": user_prompt})
    model = get_anthropic_model()
    resp = requests.post(
        "https://api.anthropic.com/v1/messages",
        json={
            "model": model,
            "max_tokens": 700,
            "temperature": 0.7,
            "system": system_prompt,
            "messages": messages,
        },
        headers={
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
            "Content-Type": "application/json",
        },
        timeout=45,
    )
    if resp.status_code >= 400:
        raise ValueError(f"Anthropic HTTP {resp.status_code}")
    data = resp.json()
    parts = data.get("content") or []
    text = "".join(part.get("text") or "" for part in parts if isinstance(part, dict))
    parsed = _parse_json_text(text)
    from services.polly_llm_cost import usage_from_anthropic
    usage = usage_from_anthropic(data, model)
    print(
        f"[Polly] brain=anthropic model={model} "
        f"in={usage.get('input_tokens')} out={usage.get('output_tokens')} "
        f"usd={usage.get('usd')}"
    )
    remember_polly_brain("anthropic", model, usage)
    return parsed


def _llm_generate_json(system_prompt: str, user_prompt: str, history: Optional[List[Dict]] = None) -> Dict[str, Any]:
    if llm_disabled():
        raise ValueError("Polly LLM disabled")
    errors = []
    if get_gemini_key() and not _GEMINI_DEPLETED:
        try:
            return _gemini_generate_json(system_prompt, user_prompt, history=history)
        except Exception as err:
            errors.append(f"gemini:{err}")
            print(f"[Polly] Gemini failed, trying Anthropic: {_redact_secrets(err)}")
    if get_anthropic_key():
        try:
            return _anthropic_generate_json(system_prompt, user_prompt, history=history)
        except Exception as err:
            errors.append(f"anthropic:{err}")
    raise ValueError(_redact_secrets("; ".join(errors) or "No LLM available"))


def _brain_extra(discovery_hint: str = "", force_intent: Optional[str] = None) -> str:
    force = f" Forced intent for this turn: {force_intent}." if force_intent else ""
    return (
        "You are the brain of this conversation. Write the reply in `say` using Markdown.\n"
        "Return JSON only:\n"
        '{"intent":"suggest_brands|generate_pitch|discovery|explain_newcollab|'
        'coach_week|coach_portfolio|coach_rates|coach_profile|ask_brand|chat",'
        '"say":"user-facing reply in Polly\'s voice",'
        '"brand_id":null,"brand_name":null,'
        '"notes_patch":{"goal_30d":null,"stage":null,"niche":null,"location":null,'
        '"biggest_challenge":null,"dream_brands":null}}\n'
        "Rules:\n"
        "- Have an opinion. Do not write a canned menu or restart with a greeting if history exists.\n"
        "- Format `say` for reading: short paragraphs, **bold** the must-do, *italics* for asides, "
        "__underline__ the exact kit URL, numbered steps when coaching. 1-3 emojis max.\n"
        "- Prefer live PR rosters (recruiting / open lists) over generic pool matches, "
        "but only when they are in-niche. A live fitness roster is not a match for skincare.\n"
        "- Prefer brands this creator can actually get a reply from: in-niche, recruiting, "
        "micro-friendly. Never push household athletic/luxury names (Nike, On Running, "
        "Sephora-scale) to aspiring micros. Reply chance beats famous logos.\n"
        "- If they tap the same chip twice (Continue setup, Get me set up) or send 1-3 words "
        "with no new info: do not repeat the previous lecture. One next action, or skip to "
        "brands. Never stack a second question (no kit steps plus 'what's your biggest challenge').\n"
        "- Tone: professional-friendly startup manager. No pet names "
        "(love, darling, hun, honey, babe, superstar, sweetie).\n"
        "- Drafting a pitch is not sending it. Never treat a drafted brand as already pitched "
        "and do not mention Timeline until they say they sent it.\n"
        "- After a pitch card, wait. If they say more / another / next, intent=suggest_brands "
        "(show cards). Do not auto-generate the next pitch. Only generate_pitch when they "
        "name a brand or tap Contact — even if that brand is not in suggested_brands.\n"
        "- If they name a brand not on their match cards: still intent=generate_pitch. "
        "Say if it's a weak fit, then still give the pitch. Never invent a Pitches tab, "
        "next screen, Send Pitch button, or Directory contact button. The card appears "
        "in this chat or it does not exist.\n"
        "- If they name a brand we do not have: say it is not in the directory and offer "
        "similar in-niche alternatives. Never pretend a draft is ready.\n"
        "- More brands is NOT confirmation. Never write that the pitch is out, sent, logged, "
        "or on Timeline unless they tapped I sent it or said they sent it.\n"
        "- 'done' / 'sent' / 'I sent it' means that brand is finished. Then you may line up "
        "the next unpitched brands as cards, not an auto-draft.\n"
        "- If intent=generate_pitch: `say` is 1-2 short sentences. Never write Subject, "
        "the email body, or 'Hey team'. The UI already shows the pitch card.\n"
        "- Never recommend a brand in already_pitched.\n"
        "- If TASK TRACKER mentions a kit view, that brand opened the pitch link. "
        "Treat it as a hot lead and offer a follow-up. Do not ignore it.\n"
        "- If CHECK-IN DUE or ACTIVE PAIN is set, ask the pulse or do that one fix. "
        "Do not dump new brand cards unless they asked or the fix is lining up in-niche matches.\n"
        "- Replies, rejections, bounces, and 'never sent' are facts to log, not small talk.\n"
        "- Only name directory brands (suggested_brands, tool_brands, or a brand they asked "
        "for that the server will look up). Never invent brands, emails, or UI screens.\n"
        "- If they tap Help me get more replies from brands: intent=coach_profile. "
        "Audit kit + bio + rates + follow-up habits + niche clarity. Not kit-only. "
        "Do not invent follower counts.\n"
        "- If they tap Write a pitch for a brand I name: intent=ask_brand. "
        "Ask for the brand name only. Do not draft until they name one.\n"
        "- Never paste /creator/dashboard/my-kit or a raw editor path. "
        "Tell them to tap **My portfolio**. The UI already shows that button.\n"
        "- notes_patch: fill fields you just learned. Leave null if unknown.\n"
        f"- Manager memory: {discovery_hint or 'none yet.'}\n"
        f"{force}"
    )


def classify_intent(
    text: str,
    profile_context: str,
    history: Optional[List[Dict]] = None,
    suggested_brands: Optional[List[Dict]] = None,
    brand_id: Any = None,
    discovery_hint: str = "",
    force_intent: Optional[str] = None,
    pitched_names: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """Live LLM is the brain. Heuristic only routes tools if the model is down."""
    heuristic = classify_intent_heuristic(
        text, suggested_brands, brand_id=brand_id, history=history
    )
    if force_intent:
        heuristic["intent"] = force_intent
    if not llm_available():
        print("[Polly] LLM unavailable — using heuristic")
        return heuristic

    brand_catalog = [
        {"id": b.get("id"), "name": b.get("name"), "slug": b.get("slug"), "category": b.get("category")}
        for b in (suggested_brands or [])
        if b.get("id") and b.get("name")
    ]
    pitched_names = [n for n in (pitched_names or []) if n]
    extra = _brain_extra(discovery_hint, force_intent)
    if pitched_names:
        extra += f"\nAlready pitched (do not recommend again): {', '.join(pitched_names)}."
    system = polly_system_prompt(profile_context, extra=extra)
    user_prompt = json.dumps({
        "latest_user_message": text,
        "suggested_brands": brand_catalog,
        "explicit_brand_id": brand_id,
        "forced_intent": force_intent,
        "already_pitched": pitched_names,
    })
    try:
        parsed = _llm_generate_json(system, user_prompt, history=history)
    except Exception as err:
        print(f"[Polly] LLM classify failed, using heuristic: {_redact_secrets(err)}")
        return heuristic

    intent = str(parsed.get("intent") or heuristic["intent"]).strip().lower()
    allowed = {
        "suggest_brands", "generate_pitch", "chat", "discovery",
        "explain_newcollab", "coach_week", "coach_portfolio", "coach_rates",
        "coach_profile", "ask_brand",
    }
    if intent not in allowed:
        intent = heuristic["intent"]
    if force_intent in allowed:
        intent = force_intent
    elif heuristic["intent"] == "generate_pitch" and looks_like_brand_request(text, history):
        intent = "generate_pitch"
        if heuristic.get("brand_name") and not parsed.get("brand_name"):
            parsed["brand_name"] = heuristic.get("brand_name")
    elif heuristic["intent"] in ("suggest_brands", "generate_pitch") and intent == "chat":
        intent = heuristic["intent"]
    if is_done_turn(text) and intent == "suggest_brands" and not force_intent:
        intent = "chat"
    if is_more_brands_turn(text) and not force_intent:
        intent = "suggest_brands"
    parsed_id = parsed.get("brand_id")
    parsed_name = parsed.get("brand_name")
    if intent == "generate_pitch" and looks_like_brand_request(text, history):
        asked = requested_brand_name(
            text,
            suggested_brands,
            history,
            brand_name=heuristic.get("brand_name") or parsed_name,
        )
        if asked:
            parsed_name = asked
            parsed_id = None
    resolved = resolve_brand(suggested_brands, brand_id=parsed_id or brand_id, brand_name=parsed_name)
    say = (parsed.get("say") or "").strip()
    return {
        "intent": intent,
        "say": say,
        "brand_id": resolved.get("id") if resolved else (parsed_id if intent == "generate_pitch" else None),
        "brand_name": resolved.get("name") if resolved else parsed_name,
        "notes_patch": parsed.get("notes_patch") if isinstance(parsed.get("notes_patch"), dict) else {},
        "brain": "llm",
    }


def chat_reply(
    profile_context: str,
    text: str,
    history: Optional[List[Dict]] = None,
    first_name: Optional[str] = None,
    discovery_hint: str = "",
) -> str:
    has_thread = any((m.get("role") or "").lower() == "assistant" for m in (history or []))
    fallback = (
        persona_followup(text, history, first_name=first_name)
        if has_thread
        else persona_greeting(profile_context, first_name=first_name)
    )
    if not llm_available():
        return fallback
    system = polly_system_prompt(
        profile_context,
        extra=_brain_extra(discovery_hint) + "\nDo not greet again if this is a follow-up.",
    )
    user_prompt = json.dumps({"latest_user_message": text, "format": {"say": "string"}})
    try:
        parsed = _llm_generate_json(system, user_prompt, history=history)
        say = (parsed.get("say") or "").strip()
        return say or fallback
    except Exception as err:
        print(f"[Polly] LLM chat failed: {_redact_secrets(err)}")
        return fallback


def narrate_tool_result(
    profile_context: str,
    text: str,
    history: Optional[List[Dict]] = None,
    brands: Optional[List[Dict]] = None,
    pitch: Optional[Dict] = None,
    fallback: str = "",
    discovery_hint: str = "",
) -> str:
    """Second LLM pass after tools so the spoken reply uses real pool data."""
    if not llm_available():
        return fallback or persona_brand_intro(brands, profile_context)
    catalog = []
    for b in brands or []:
        catalog.append({
            "name": b.get("name"),
            "category": b.get("category"),
            "match_score": b.get("match_score"),
            "fit_tier": b.get("fit_tier"),
            "why": (b.get("why") or b.get("description") or "")[:180],
        })
    pitch_bits = None
    if pitch:
        pitch_bits = {
            "brand_name": pitch.get("brand_name"),
            "subject": pitch.get("subject"),
            "has_mailto": bool(pitch.get("mailto")),
        }
    system = polly_system_prompt(
        profile_context,
        extra=(
            _brain_extra(discovery_hint)
            + "\nThe backend already ran a tool. Write the chat reply in Polly's voice, Markdown formatted.\n"
            "Only mention brands in tool_brands. Pick a favourite and say why. "
            "Ask which one to pitch. If a pitch is ready, tell them to open email — casually."
        ),
    )
    user_prompt = json.dumps({
        "latest_user_message": text,
        "tool_brands": catalog,
        "tool_pitch": pitch_bits,
        "format": {"say": "string"},
    })
    try:
        parsed = _llm_generate_json(system, user_prompt, history=history)
        return (parsed.get("say") or "").strip() or fallback
    except Exception as err:
        print(f"[Polly] LLM narrate failed: {_redact_secrets(err)}")
        return fallback


def narrate_kit_review(
    profile_context: str,
    text: str,
    history: Optional[List[Dict]] = None,
    kit: Optional[Dict] = None,
    fallback: str = "",
    discovery_hint: str = "",
) -> str:
    """Ground kit coaching in the live My Kit snapshot — never Linktree."""
    from services.polly_kit import kit_context, persona_kit_review

    grounded = fallback or persona_kit_review(kit)
    if not llm_available():
        return grounded
    snapshot = {
        "published": bool((kit or {}).get("published")),
        "url": (kit or {}).get("url"),
        "gaps": (kit or {}).get("gaps") or [],
        "post_count": (kit or {}).get("post_count") or 0,
        "has_rates": bool((kit or {}).get("has_rates")),
        "bio_has_kit_url": bool((kit or {}).get("bio_has_kit_url")),
        "bio_has_generic_newcollab": bool((kit or {}).get("bio_has_generic_newcollab")),
        "headline": (kit or {}).get("headline"),
        "about_chars": (kit or {}).get("about_chars") or 0,
    }
    system = polly_system_prompt(
        profile_context,
        extra=(
            _brain_extra(discovery_hint, force_intent="coach_portfolio")
            + "\nYou already opened their Newcollab My Kit. Review THAT page.\n"
            "Never mention Linktree or a generic landing page.\n"
            "If unpublished: tell them to tap My portfolio. Never paste an editor path.\n"
            "If published: specific notes from kit_snapshot, then the exact live URL "
            "underlined for their TikTok bio — not newcollab.co homepage.\n"
            + kit_context(kit)
        ),
    )
    user_prompt = json.dumps({
        "latest_user_message": text,
        "kit_snapshot": snapshot,
        "format": {"say": "string"},
    })
    try:
        parsed = _llm_generate_json(system, user_prompt, history=history)
        say = (parsed.get("say") or "").strip()
        return say or grounded
    except Exception as err:
        print(f"[Polly] LLM kit review failed: {_redact_secrets(err)}")
        return grounded
