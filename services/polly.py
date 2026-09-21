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

from services.irresistible_pitch import (
    LOCATION_PLACEHOLDER,
    apply_location_to_body,
    resolve_shipping,
)
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
_CASUAL_ACK_RE = re.compile(
    r"^(thanks|thank you|thanks so much|thanks a lot|thx|ty|cheers|"
    r"cool|nice|great|awesome|perfect|lovely|brilliant|amazing|"
    r"got it|noted|appreciate it|appreciated|sweet)[\s!.]*$",
    re.I,
)
_CASUAL_WORDS = frozenset({
    "thanks", "thank", "thx", "ty", "cheers", "cool", "nice", "great",
    "awesome", "perfect", "lovely", "brilliant", "amazing", "noted",
    "appreciate", "appreciated", "sweet", "dope",
})
_ACK_FILLER = frozenset({
    "so", "much", "a", "lot", "very", "really", "it", "you", "ok", "okay", "got",
})

_BRAND_ASK_PREFIX_RE = re.compile(
    r"(?i)^(i\s+(?:want|wanna|need)|can\s+(?:i|we)\s+(?:get|do|pitch|try)|"
    r"please\s+(?:pitch|draft|do)|how\s+about|what\s+about|maybe|"
    r"help\s+me\s+(?:pitch|contact|hit\s+up)|"
    r"let'?s\s+(?:hit up|pitch|try|do|reach out(?:\s+to)?|email)|"
    r"hit up|pitch|contact|reach out to|"
    r"write\s+(?:a\s+|me\s+a\s+)?pitch\s+for|"
    r"draft\s+(?:an?\s+)?(?:email|pitch|note)\s+(?:to|for)|"
    r"send\s+(?:a\s+)?(?:note|pitch|email)\s+to|"
    r"get\s+me\s+in(?:\s+front)?\s+(?:with|of)|"
    r"start\s+with|"
    r"find(?:ing)?|fin|search(?:ing)?(?:\s+for)?|look(?:ing)?(?:\s+for)?)\s+"
)
_FOLLOWUP_LABEL_RE = re.compile(
    r"(?i)^(?:please\s+)?(?:draft|write|send|share)\s+(?:a\s+|the\s+)?(.+?)\s+follow-?up\s*$"
)
_ALSO_FOR_RE = re.compile(
    r"(?i)^(?:also|too|and)(?:\s+(?:one|another))?(?:\s+for)?\s+(.+)$"
)
_FOLLOWUP_ASK_RE = re.compile(
    r"(?i)\b(follow[\s-]*up|followup|follow\s*wup|f/?u|bump(?:\s+them)?|nudge)\b"
)
_BRAND_REJECT_RE = re.compile(
    r"(?i)^(not|no,?\s+not|don't|dont|do not)\b"
)
_FAKE_PITCH_UI_RE = re.compile(
    r"(?i)[^.!?\n]*\b("
    r"pitches (tab|section|page|screen)|next screen|send pitch button|"
    r"check (your )?pitches|"
    r"already drafted|already presented|already (?:sent|wrote) those follow-?ups?|"
    r"here'?s the follow-up|follow-up is already"
    r")\b[^.!?\n]*[.!?]?"
)
_ASK_FILLER = frozenset({
    "fin", "find", "finding", "search", "searching", "look", "looking",
    "want", "wanna", "need", "get", "show", "give", "me", "us", "a", "an",
    "the", "for", "up", "to", "please", "can", "i", "we", "try", "pitch",
    "contact", "about", "maybe", "how", "what", "brand", "brands", "help",
    "also", "too", "same", "one",
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
_CONTACT_ASK_RE = re.compile(
    r"(?i)\b("
    r"hit up|reach out|contact|"
    r"let'?s\s+(?:hit up|pitch|try|do|reach out)|"
    r"pitch .{1,40} for me|"
    r"write (?:a |me a )?pitch|"
    r"draft (?:an? )?(?:email|pitch|note)|"
    r"send (?:a )?(?:note|pitch|email) to|"
    r"get me in(?: front)? (?:with|of)|"
    r"start with|"
    r"i want"
    r")\b"
)
_SUGGEST_RE = re.compile(
    r"\b(suggest|recommend|match|for you|who should|find brand|show me brand|"
    r"line up|hit up|brands? to (reach|pitch|contact)|which brand|what brand|"
    r"find me|paid collab|paid ugc|paid deals?|paid opportunit|get paid|"
    r"pay ugc|pay for ugc|who pays|brands? (that |who |do )pay|"
    r"gifted collab|pr packages?|brand deals?)\b",
    re.I,
)
_DEAL_SEARCH_RE = re.compile(
    r"(?i)(?:"
    r"(?:find(?:\s+me)?|show\s+me|line\s+up|get\s+me|looking\s+for|i\s+want|i\s+need)\s+"
    r"(?:to\s+(?:get|land|find)\s+)?"
    r"(?:some\s+|a\s+|3\s+)?"
    r"(?:paid\s+|gifted\s+|pr\s+|ugc\s+|brand\s+)?"
    r"(?:collab(?:oration)?s?|deals?|packages?|brands?|ugc|opportunit(?:y|ies)|gigs?|offers?|work)|"
    r"\b(?:what|which)\s+brands?\b.{0,48}\b(?:pay|paid|ugc|collab)|"
    r"\bwho\s+pays?\b|"
    r"\bbrands?\s+(?:that|who|do(?:es)?)\s+pay|"
    r"\bdo(?:es)?\s+pay\s+ugc\b|"
    r"\bpay(?:s|ing)?\s+(?:for\s+)?(?:ugc|collab)|"
    r"\bpay\s+ugc\b|"
    r"\bget\s+paid\b|"
    r"\bwant\s+paid\b|"
    r"\bneed\s+paid\b|"
    r"\bpaid\s+(?:collab(?:oration)?s?|ugc|deals?|work|partnerships?|gigs?|offers?|opportunit(?:y|ies))\b|"
    r"\bpaid\s+from\s+the\s+start\b|"
    r"\bnot\s+just\s+(?:receive\s+)?products\b|"
    r"\bugc.{0,48}\bpagos?\b|"
    r"\bpagos?\b.{0,48}\bugc|"
    r"\bgifted\s+(?:collab(?:oration)?s?|pr|deals?|packages?)\b|"
    r"\bpr\s+packages?\b|"
    r"\bbrand\s+deals?\b"
    r")"
)
_GENERIC_BRAND_ASK = frozenset({
    "paid", "collaboration", "collaborations", "collab", "collabs",
    "deal", "deals", "ugc", "gifted", "brand", "brands", "package", "packages",
    "pr", "work", "partnership", "partnerships", "gig", "gigs",
    "opportunity", "opportunities", "offer", "offers",
    "paid collaborations", "paid collaboration", "paid collabs", "paid collab",
    "paid ugc", "paid deals", "paid deal", "paid opportunities", "paid opportunity",
    "get paid", "pay ugc", "pay for ugc", "do pay ugc", "does pay ugc",
    "gifted collabs", "gifted collab",
    "pr packages", "pr package", "brand deals", "brand deal",
})
_QUERY_VOCAB = _GENERIC_BRAND_ASK | _ASK_FILLER | frozenset({
    "do", "does", "did", "that", "which", "who", "whom", "whose",
    "any", "some", "these", "those", "they", "them", "their",
    "pay", "pays", "paying", "now", "today", "first", "real", "really",
    "actual", "actually", "just", "from", "start", "receive", "products",
    "line", "up", "with", "and", "or", "of", "in", "on", "at",
})
_PROMPT_VOCAB = frozenset({
    "replies", "reply", "response", "responses", "more", "better", "improve",
    "increase", "tips", "advice", "getting", "make", "making", "should",
    "could", "would", "after", "follow", "following", "next", "this",
    "that", "my", "your", "open", "rate", "rates", "kit", "portfolio",
    "week", "today", "now", "how", "why", "when", "where", "who",
})
_QUESTION_LEAD_RE = re.compile(
    r"(?i)^(how\s+(?:to|do|does|can|could|should|would|i\b)|"
    r"what(?:'s| is| are| should| can| do)\b|"
    r"why\b|when\b|where\b|who\b|"
    r"can you|could you|should i|tell me|explain|any tips|"
    r"help me (?!pitch\b|contact\b|hit\b))"
)
_CATEGORY_ASK = frozenset({
    "beauty", "skincare", "makeup", "fashion", "fitness", "food", "wellness",
    "lifestyle", "tech", "hair", "haircare", "fragrance", "home", "activewear",
    "sports", "health", "travel", "parenting", "pets",
})
_STATUS_ASK_RE = re.compile(
    r"(?i)^(not sent|never sent|didn'?t send|did not send|haven'?t sent|"
    r"have not sent|won'?t send|will not send|not actually sent)\b"
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


def leftover_is_category(text: str) -> bool:
    parts = []
    for part in strip_brand_ask(text or "").split():
        token = re.sub(r"[^\w&+'-]+", "", part).lower()
        if token and token not in _ASK_FILLER:
            parts.append(token)
    return len(parts) == 1 and parts[0] in _CATEGORY_ASK


def is_casual_ack(text: str) -> bool:
    """True for gratitude / filler that is never a Directory brand name."""
    raw = (text or "").strip()
    if not raw:
        return False
    if _CASUAL_ACK_RE.match(raw):
        return True
    tokens = [re.sub(r"[^\w]+", "", part).lower() for part in raw.split()]
    tokens = [t for t in tokens if t]
    if not tokens:
        return False
    return all(t in _CASUAL_WORDS or t in _ACK_FILLER for t in tokens) and any(
        t in _CASUAL_WORDS for t in tokens
    )


def is_status_ask(text: str) -> bool:
    return bool(_STATUS_ASK_RE.search((text or "").strip()))


def leftover_is_prompt(text: str) -> bool:
    """True when the user is asking a question / coaching, not naming a brand."""
    raw = (text or "").strip()
    if not raw:
        return False
    if followup_brand_query(raw):
        return False
    if "?" in raw:
        return True
    if re.match(r"(?i)^(how|what)\s+about\b", raw):
        return False
    if _QUESTION_LEAD_RE.search(raw):
        return True
    if (
        _COACH_PROFILE_RE.search(raw)
        or _COACH_WEEK_RE.search(raw)
        or _COACH_RATES_RE.search(raw)
        or _COACH_KIT_RE.search(raw)
        or _EXPLAIN_RE.search(raw)
    ):
        return True
    return False


def leftover_looks_like_query(text: str) -> bool:
    """True when leftover tokens are deal-vocab (pay/UGC/brands), not a proper name."""
    stripped = strip_brand_ask(text or "")
    parts = []
    for part in stripped.split():
        token = re.sub(r"[^\w&+'-]+", "", part).lower()
        if not token or token in _ASK_FILLER:
            continue
        parts.append(token)
    if not parts:
        return False
    return all(part in _QUERY_VOCAB for part in parts)


def candidate_looks_like_brand_name(text: str) -> bool:
    """True only when leftover could be a Directory name — never a prompt or category."""
    leftover = (text or "").strip(" .!,")
    if not leftover or len(leftover) < 2:
        return False
    if (
        leftover_is_prompt(leftover)
        or is_status_ask(leftover)
        or leftover_looks_like_query(leftover)
        or is_casual_ack(leftover)
    ):
        return False
    low = leftover.lower()
    if low in _CATEGORY_ASK or low in _GENERIC_BRAND_ASK or low in _CASUAL_WORDS:
        return False
    parts = []
    for part in leftover.split():
        token = re.sub(r"[^\w&+'-]+", "", part).lower()
        if token and token not in _ASK_FILLER and token not in _CASUAL_WORDS:
            parts.append(token)
    if not parts:
        return False
    if all(part in _QUERY_VOCAB or part in _CATEGORY_ASK or part in _PROMPT_VOCAB for part in parts):
        return False
    return True


def allow_fuzzy_brand_lookup(candidate: str) -> bool:
    token = (candidate or "").strip()
    if len(token) < 5:
        return False
    return candidate_looks_like_brand_name(token)


def claims_pitch_elsewhere(text: str) -> bool:
    return bool(_FAKE_PITCH_UI_RE.search(text or ""))


def deal_search_kind(text: str) -> Optional[str]:
    """'find me paid collaborations' / 'what brands do pay UGC' is an outcome search, not a brand name."""
    raw = (text or "").strip()
    if not raw:
        return None
    leftover_outcome = leftover_looks_like_query(raw) and bool(
        re.search(r"(?i)\b(pay|paid|pagos?|ugc|collab|deal|gifted)\b", raw)
    )
    if leftover_is_category(raw) and not leftover_outcome and not _DEAL_SEARCH_RE.search(raw):
        return "brands"
    if not (_DEAL_SEARCH_RE.search(raw) or leftover_outcome):
        return None
    low = raw.lower()
    if re.search(
        r"\bpaid\b|\bpay(?:s|ing)?\b|\bpagos?\b|\bugc\b|\bnot\s+just\s+(?:receive\s+)?products\b",
        low,
    ):
        return "paid"
    if re.search(r"\b(gifted|pr package)\b", low):
        return "gifted"
    return "brands"


def is_deal_search(text: str) -> bool:
    return deal_search_kind(text) is not None


def deal_pool_intent(text: str) -> Optional[str]:
    """Paid UGC hunts the scanner. Gifted / generic deals stay on Directory cards."""
    kind = deal_search_kind(text)
    if kind == "paid":
        return "suggest_gigs"
    if kind:
        return "suggest_brands"
    return None


def is_brand_reject(text: str) -> bool:
    raw = (text or "").strip()
    if not raw or is_status_ask(raw):
        return False
    return bool(_BRAND_REJECT_RE.match(raw))


def last_followup_brand(history: Optional[List[Dict]] = None) -> Optional[str]:
    for msg in reversed(history or []):
        if (msg.get("role") or "").lower() != "assistant":
            continue
        pitch = msg.get("pitch")
        if isinstance(pitch, dict) and pitch.get("is_followup"):
            name = str(pitch.get("brand_name") or pitch.get("name") or "").strip()
            if name:
                return name
        return None
    return None


def last_assistant_was_followup(history: Optional[List[Dict]] = None) -> bool:
    return bool(last_followup_brand(history))


def followup_brand_query(text: str) -> str:
    """'Draft Tarte Cosmetics follow-up' / 'also for Future Society' → brand name."""
    raw = (text or "").strip()
    if is_brand_reject(raw):
        return ""
    match = _FOLLOWUP_LABEL_RE.match(raw)
    name = ""
    if match:
        name = match.group(1).strip(" .!,")
    else:
        also = _ALSO_FOR_RE.match(raw)
        if also:
            name = also.group(1).strip(" .!,")
            name = re.sub(r"(?i)^(the\s+)?(one\s+)?(for\s+)?", "", name).strip()
    if not name:
        return ""
    name = re.sub(r"(?i)^(the|a|an)\s+", "", name).strip()
    name = re.sub(r"(?i)\s+follow-?up$", "", name).strip()
    if leftover_looks_like_query(name) or leftover_is_category(name) or is_casual_ack(name):
        return ""
    return name if candidate_looks_like_brand_name(name) else ""


def match_named_brand(text: str, pools: Optional[List] = None) -> str:
    blob = (text or "").lower()
    if not blob:
        return ""
    best = ""
    for row in pools or []:
        if isinstance(row, str):
            name = row.strip()
        elif isinstance(row, dict):
            name = str(row.get("name") or row.get("brand_name") or "").strip()
        else:
            continue
        if len(name) >= 2 and name.lower() in blob and len(name) >= len(best):
            best = name
    return best


def asked_brand_query(text: str) -> str:
    """Drop find/fin/want filler so 'fin Dell' looks up Dell."""
    if is_brand_reject(text):
        return ""
    follow = followup_brand_query(text)
    if follow:
        return follow
    if (
        leftover_is_prompt(text)
        or is_deal_search(text)
        or leftover_looks_like_query(text)
        or leftover_is_category(text)
        or is_status_ask(text)
        or is_casual_ack(text)
    ):
        return ""
    stripped = strip_brand_ask(text)
    parts = [
        part.strip(" .!,")
        for part in stripped.split()
        if part.strip(" .!,").lower() not in _ASK_FILLER
    ]
    cleaned = " ".join(parts).strip(" .!,")
    leftover = cleaned or stripped
    if leftover.strip().lower() in _GENERIC_BRAND_ASK or leftover.strip().lower() in _CATEGORY_ASK:
        return ""
    if leftover_looks_like_query(leftover) or not candidate_looks_like_brand_name(leftover):
        return ""
    return leftover


def brand_lookup_names(text: str) -> List[str]:
    if is_casual_ack(text) or leftover_looks_like_query(text) or is_deal_search(text):
        return []
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
    if not raw or is_done_turn(raw) or is_more_brands_turn(raw) or is_deal_search(raw):
        return False
    if (
        leftover_is_prompt(raw)
        or leftover_looks_like_query(raw)
        or leftover_is_category(raw)
        or is_status_ask(raw)
        or is_casual_ack(raw)
    ):
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
    if _CONTACT_RE.search(raw) or _CONTACT_ASK_RE.search(raw):
        return True
    leftover = asked_brand_query(raw)
    if leftover and candidate_looks_like_brand_name(leftover):
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
    if leftover_is_prompt(raw) or is_casual_ack(raw) or leftover_looks_like_query(raw) or is_deal_search(raw):
        return None
    typed = asked_brand_query(raw) if looks_like_brand_request(raw, history) else ""
    typed = (typed or "").strip()
    # What they typed wins over the model (which often repeats the pending draft brand).
    needle = typed or (brand_name or "").strip() or raw
    if leftover_looks_like_query(needle) or is_deal_search(raw):
        return None
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


def paid_rate_phrase(kit: Optional[Dict] = None, scrape: Optional[Dict] = None) -> str:
    kit = kit or {}
    rates = kit.get("rates") if isinstance(kit.get("rates"), dict) else {}
    for key in ("tiktok", "reel", "ugc", "video"):
        val = rates.get(key)
        if val not in (None, "", 0, "0"):
            text = str(val).strip()
            if text[:1].isdigit() and not text.startswith("$"):
                return f"${text}"
            return text
    scrape = scrape or {}
    raw = scrape.get("followers") or scrape.get("follower_count") or scrape.get("tiktok_followers")
    try:
        n = int(float(raw or 0))
    except (TypeError, ValueError):
        n = 0
    if n >= 25000:
        return "$250–$500"
    if n >= 10000:
        return "$150–$250"
    if n >= 2000:
        return "$80–$150"
    if n > 0:
        return "$50–$100"
    return "a paid fee"


def is_location_placeholder(text: str) -> bool:
    raw = (text or "").strip()
    if not raw:
        return False
    if LOCATION_PLACEHOLDER.lower() in raw.lower():
        return True
    return bool(re.search(r"\[\s*city\s*,\s*country\s*\]", raw, re.I))


def pitch_has_placeholder(text: str) -> bool:
    return is_location_placeholder(text or "")


def parse_location_reply(text: str) -> Optional[Dict[str, str]]:
    """City + country from a short creator reply. None if it looks like a brand ask."""
    raw = (text or "").strip().strip(" .!")
    if not raw or len(raw) > 80:
        return None
    if is_done_turn(raw) or is_more_brands_turn(raw) or is_casual_ack(raw):
        return None
    if _CONTACT_ASK_RE.search(raw) or _CONTACT_RE.search(raw) or is_deal_search(raw):
        return None
    if leftover_is_prompt(raw):
        return None
    cleaned = re.sub(
        r"(?i)^(i(?:'m| am)\s+(?:in|based in|from)|based in|i live in|from)\s+",
        "",
        raw,
    ).strip()
    if not cleaned or cleaned.lower() in _CHIP_SKIP_LABELS:
        return None
    parts = [p.strip() for p in re.split(r"\s*[,/|]\s*", cleaned) if p.strip()]
    city = ""
    country = ""
    if len(parts) >= 2:
        city = parts[0]
        country = ", ".join(parts[1:])
    else:
        words = cleaned.replace(",", " ").split()
        if not (2 <= len(words) <= 6):
            return None
        city = " ".join(words[:-1])
        country = words[-1]
    city = re.sub(r"\s+", " ", city).strip(" ,")
    country = re.sub(r"\s+", " ", country).strip(" ,")
    if not city or not country:
        return None
    if city.lower() in _CHIP_SKIP_LABELS or country.lower() in {"me", "it", "this"}:
        return None
    return {"city": city[:80], "country": country[:80]}


def resolve_pitch_location(
    creator: Optional[Dict] = None,
    scrape: Optional[Dict] = None,
    notes: Optional[Dict] = None,
) -> Dict[str, str]:
    creator = dict(creator or {})
    scrape = scrape or {}
    notes = notes or {}
    city = str(creator.get("city") or scrape.get("city") or "").strip()
    country = str(creator.get("country") or scrape.get("country") or "").strip()
    parsed = parse_location_reply(str(notes.get("location") or ""))
    if parsed:
        city = city or parsed.get("city") or ""
        country = country or parsed.get("country") or ""
    shipping = resolve_shipping(creator, city, country)
    if is_location_placeholder(shipping.get("display") or "") and parsed:
        shipping = resolve_shipping(creator, parsed.get("city") or "", parsed.get("country") or "")
    return shipping


def apply_location_to_pitch(
    pitch: Optional[Dict[str, Any]],
    city: str = "",
    country: str = "",
    display: str = "",
) -> Optional[Dict[str, Any]]:
    if not pitch:
        return pitch
    out = dict(pitch)
    loc = (display or "").strip() or ", ".join(p for p in (city.strip(), country.strip()) if p)
    if not loc or is_location_placeholder(loc):
        out["needs_location"] = True
        return out
    prev = ""
    match = re.search(r"shipping to ([^\n.]+)", str(out.get("body") or ""), re.I)
    if match:
        prev = match.group(1).strip()
    body = apply_location_to_body(str(out.get("body") or ""), loc, prev)
    out["body"] = body
    out["location_display"] = loc
    out["needs_location"] = pitch_has_placeholder(body)
    if out.get("email"):
        out["mailto"] = build_mailto(out.get("email"), out.get("subject") or "", body)
    return out


def last_thread_pitch(history: Optional[List[Dict]] = None) -> Optional[Dict[str, Any]]:
    for msg in reversed(history or []):
        pitch = msg.get("pitch") if isinstance(msg, dict) else None
        if isinstance(pitch, dict) and (pitch.get("body") or pitch.get("subject")):
            return dict(pitch)
    return None


def patch_last_pitch_in_history(
    history: Optional[List[Dict]],
    update: Optional[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    rows = [dict(m) if isinstance(m, dict) else m for m in (history or [])]
    if not update:
        return rows
    for idx in range(len(rows) - 1, -1, -1):
        msg = rows[idx]
        if not isinstance(msg, dict):
            continue
        pitch = msg.get("pitch")
        if isinstance(pitch, dict) and (pitch.get("body") or pitch.get("subject")):
            rows[idx] = {**msg, "pitch": {**pitch, **update}}
            break
    return rows


def apply_paid_ask_to_pitch(
    pitch: Optional[Dict[str, Any]],
    kit: Optional[Dict] = None,
    scrape: Optional[Dict] = None,
    location_display: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """Paid UGC ask: quote kit rates, never a gifted 'no fee' trial."""
    if not pitch:
        return pitch
    out = dict(pitch)
    subject = str(out.get("subject") or "")
    body = str(out.get("body") or "")
    rate = paid_rate_phrase(kit, scrape)
    loc = ""
    loc_m = re.search(r"shipping to ([^\n.]+)", body, re.I)
    if loc_m:
        loc = loc_m.group(1).strip()
    filled = (location_display or "").strip()
    if filled and not is_location_placeholder(filled):
        loc = filled
    placeholder = is_location_placeholder(loc)
    if placeholder:
        loc = LOCATION_PLACEHOLDER
        ship = f", plus product shipping to {loc} if you want it in-shot."
    elif loc:
        ship = f", plus product shipping to {loc} if you want it in-shot."
    else:
        ship = "."
    paid_line = (
        f"Rate: {rate} for 1 organic post + 2 UGC files (6-month paid usage)"
        + ship
    )
    if re.search(r"no fee|gifted trial|pr/gifting|gifting sample", f"{subject}\n{body}", re.I):
        body = re.sub(r"No fee\. Just product \+ shipping to [^\n.]+.?", paid_line, body, flags=re.I)
        if re.search(r"no fee", body, re.I):
            body = re.sub(r"No fee[^\n]*", paid_line, body, flags=re.I)
    if re.search(r"(?i)^rate:", body, re.M):
        body = re.sub(r"(?im)^rate:[^\n]+", paid_line, body)
    elif "Rate:" not in body:
        body = (body.rstrip() + "\n\n" + paid_line).strip()
    if filled and not is_location_placeholder(filled):
        body = apply_location_to_body(body, filled, LOCATION_PLACEHOLDER)
    out["subject"] = "Paid UGC — 1 post + 2 raw files"
    out["body"] = body
    if out.get("email"):
        out["mailto"] = build_mailto(out.get("email"), out["subject"], body)
    out["deal_type"] = "paid"
    out["quoted_rate"] = rate
    out["needs_location"] = pitch_has_placeholder(body)
    if filled and not is_location_placeholder(filled):
        out["location_display"] = filled
    return out


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


def wants_followup_pitch(
    data: Optional[Dict] = None,
    user_text: str = "",
    history: Optional[List[Dict]] = None,
    pitched_names: Optional[List] = None,
) -> bool:
    data = data or {}
    if is_brand_reject(user_text):
        return False
    chip = str(data.get("starter") or data.get("chip_id") or "").strip().lower()
    if data.get("is_followup") or chip in ("draft_followup", "draft_it", "draft_final"):
        return True
    if _FOLLOWUP_ASK_RE.search(user_text or ""):
        return True
    name = followup_brand_query(user_text)
    if name and last_assistant_was_followup(history):
        return True
    pitched = {str(n).strip().lower() for n in (pitched_names or []) if n}
    if name and name.lower() in pitched:
        return True
    if _ALSO_FOR_RE.match((user_text or "").strip()) and last_assistant_was_followup(history):
        return True
    return False


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
    r"^(yep,? )?(i('ve| have| )?sent( it)?|sent it|done|finished|"
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
    pending = out.get("pending_pitch") if isinstance(out.get("pending_pitch"), dict) else None
    if pending:
        try:
            pending_id = int(pending.get("id") or pending.get("brand_id") or 0)
        except (TypeError, ValueError):
            pending_id = 0
        pending_name = str(pending.get("name") or pending.get("brand_name") or "").strip().lower()
        same = (bid and pending_id == bid) or (name and pending_name == name.lower())
        if same:
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


_UNLOCK_RESET_RE = re.compile(
    r"(?i)\b("
    r"reset|when will that be|when do (?:i|they|credits|unlocks) (?:reset|come back)|"
    r"wait(?:ing)? for the reset|next month|"
    r"(?:1st|first) of (?:the )?month|"
    r"unlock pro|upgrade to pro|out of (?:free )?unlocks"
    r")\b"
)


def is_unlock_reset_ask(text: str) -> bool:
    return bool(_UNLOCK_RESET_RE.search(text or ""))


def next_unlock_brand(
    last_pitch: Optional[Dict] = None,
    remaining_brands: Optional[List[Dict]] = None,
    pending: Optional[Dict] = None,
) -> Optional[Dict[str, Any]]:
    """Brand the Pro chip should sell after a send — never the one they just logged."""
    skip_ids = set()
    skip_names = set()

    def _skip(row: Optional[Dict]) -> None:
        if not isinstance(row, dict):
            return
        try:
            bid = int(row.get("id") or row.get("brand_id") or 0)
        except (TypeError, ValueError):
            bid = 0
        if bid:
            skip_ids.add(bid)
        name = str(row.get("name") or row.get("brand_name") or "").strip().lower()
        if name:
            skip_names.add(name)

    def _card(row: Optional[Dict]) -> Optional[Dict[str, Any]]:
        if not isinstance(row, dict):
            return None
        try:
            bid = int(row.get("id") or row.get("brand_id") or 0)
        except (TypeError, ValueError):
            bid = 0
        name = str(row.get("name") or row.get("brand_name") or "").strip()
        if bid and bid in skip_ids:
            return None
        if name.lower() in skip_names:
            return None
        if not name and not bid:
            return None
        return {"id": bid or None, "name": name}

    _skip(last_pitch)
    _skip(pending)
    for row in remaining_brands or []:
        card = _card(row)
        if card and card.get("name"):
            return card
    return None


def paywall_unlock_chips(brand: Optional[Dict] = None) -> List[Dict[str, Any]]:
    brand = brand or {}
    name = str(brand.get("name") or brand.get("brand_name") or "").strip()
    bid = brand.get("id") or brand.get("brand_id")
    label_name = name
    if label_name and len(label_name) > 28:
        label_name = label_name[:26].rstrip() + "…"
    label = f"Keep pitching {label_name} — unlock Pro" if label_name else "Unlock Pro to keep pitching"
    return [
        {
            "id": "unlock_pro",
            "label": label,
            "action": "unlock_pro",
            "brand_id": bid,
            "brand_name": name or None,
        }
    ]


def out_of_free_unlocks(balance: Optional[Dict] = None) -> bool:
    if not isinstance(balance, dict) or balance.get("is_unlimited"):
        return False
    try:
        remaining = balance.get("remaining")
        return int(0 if remaining is None else remaining) <= 0
    except (TypeError, ValueError):
        return False


def _task_brand(task: Optional[Dict]) -> Optional[Dict[str, Any]]:
    if not isinstance(task, dict):
        return None
    name = str(task.get("brand_name") or task.get("name") or "").strip()
    if not name:
        return None
    return {"id": task.get("brand_id"), "name": name}


def follow_brand_from_tracker(
    tracker: Optional[Dict] = None,
    notes: Optional[Dict] = None,
) -> Optional[Dict[str, Any]]:
    notes = notes if isinstance(notes, dict) else {}
    tracker = tracker if isinstance(tracker, dict) else {}
    preferred = ("follow_up_due", "campaign_applied", "pitch_sent", "reply_needed")
    seen = []
    for bucket in (tracker.get("due_soon") or [], tracker.get("active_tasks") or []):
        for task in bucket:
            if not isinstance(task, dict):
                continue
            if task.get("type") not in preferred:
                continue
            row = _task_brand(task)
            if not row or not row.get("name"):
                continue
            seen.append((preferred.index(task.get("type")), row))
    if seen:
        seen.sort(key=lambda item: item[0])
        return seen[0][1]
    wall = notes.get("paywall_brand") if isinstance(notes.get("paywall_brand"), dict) else None
    if wall and (wall.get("name") or wall.get("brand_name")):
        return {
            "id": wall.get("id") or wall.get("brand_id"),
            "name": wall.get("name") or wall.get("brand_name"),
        }
    pending = notes.get("pending_pitch") if isinstance(notes.get("pending_pitch"), dict) else None
    if pending and (pending.get("name") or pending.get("brand_name")):
        return {
            "id": pending.get("id") or pending.get("brand_id"),
            "name": pending.get("name") or pending.get("brand_name"),
        }
    return None


def open_apply_count(tracker: Optional[Dict] = None) -> int:
    ids = set()
    names = set()
    for task in (tracker or {}).get("active_tasks") or []:
        if not isinstance(task, dict) or task.get("type") != "campaign_applied":
            continue
        bid = task.get("brand_id")
        if bid:
            ids.add(bid)
        else:
            names.add(str(task.get("brand_name") or "").strip().lower())
    return len(ids) + len({n for n in names if n})


def empty_unlock_starters(
    notes: Optional[Dict] = None,
    tracker: Optional[Dict] = None,
) -> List[Dict[str, Any]]:
    notes = notes if isinstance(notes, dict) else {}
    follow = follow_brand_from_tracker(tracker, notes)
    chips: List[Dict[str, Any]] = []
    pending = notes.get("pending_pitch") if isinstance(notes.get("pending_pitch"), dict) else None
    pending_name = str((pending or {}).get("name") or (pending or {}).get("brand_name") or "").strip()
    if pending_name:
        chips.append({
            "id": "i_sent_it",
            "label": "I sent it",
            "action": "chat",
            "brand_id": pending.get("id") or pending.get("brand_id"),
            "brand_name": pending_name,
        })
    if follow and follow.get("name"):
        chips.append({
            "id": "draft_followup",
            "label": f"Draft {follow['name']} follow-up",
            "action": "generate_pitch",
            "brand_id": follow.get("id"),
            "brand_name": follow.get("name"),
            "is_followup": True,
        })
    chips.extend(paywall_unlock_chips(follow))
    chips.append({
        "id": "paid_ugc",
        "label": "Show paid UGC I can apply to now",
        "hint": "Live briefs — tap Apply",
        "action": "suggest_gigs",
        "skip_discovery": True,
    })
    chips.append({
        "id": "week_plan",
        "label": "What should I do this week?",
        "action": "coach_week",
    })
    return chips[:4]


def empty_unlock_open(
    first_name: Optional[str] = None,
    tracker: Optional[Dict] = None,
    notes: Optional[Dict] = None,
) -> Dict[str, Any]:
    from services.polly_persona import persona_empty_unlock_brief, persona_empty_unlock_greeting

    follow = follow_brand_from_tracker(tracker, notes)
    apply_n = open_apply_count(tracker)
    brand = (follow or {}).get("name")
    greeting = persona_empty_unlock_greeting(first_name, apply_n, brand)
    starters = empty_unlock_starters(notes, tracker)
    watched = []
    if apply_n:
        watched.append(f"{apply_n} application{'s' if apply_n != 1 else ''} still in review")
    if follow and follow.get("name"):
        watched.append(f"{follow['name']} — follow-up is free")
    return {
        "greeting": greeting,
        "starters": starters,
        "brief": {
            "kind": "brief",
            "title": "Out of free unlocks",
            "summary": persona_empty_unlock_brief(apply_n, brand),
            "priority": f"{brand} · follow-up" if brand else "Unlock Pro",
            "watched": watched[:4],
            "chips": starters,
        },
        "follow": follow,
        "apply_count": apply_n,
    }


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
    if is_done_turn(raw) or is_casual_ack(raw):
        return {"intent": "chat", "say": "", "brand_id": None, "brand_name": None}
    from services.polly_gigs import wants_more_gigs
    if wants_more_gigs(raw, history):
        return {"intent": "suggest_gigs", "say": "", "brand_id": None, "brand_name": None}
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
    if is_deal_search(raw):
        return {
            "intent": deal_pool_intent(raw) or "suggest_brands",
            "say": "",
            "brand_id": None,
            "brand_name": None,
        }
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
        '{"intent":"suggest_gigs|suggest_brands|generate_pitch|discovery|explain_newcollab|'
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
        "- also for <brand> / share follow-up after a follow-up card: intent=generate_pitch "
        "for that brand. Never say a follow-up is already in chat unless this turn returns "
        "a pitch card. Do not invent that they already sent it.\n"
        "- More brands is NOT confirmation. Never write that the pitch is out, sent, logged, "
        "or on Timeline unless they tapped I sent it or said they sent it.\n"
        "- 'done' / 'sent' / 'I sent it' means that brand is finished. Then you may line up "
        "the next unpitched brands as cards, not an auto-draft.\n"
        "- After they confirm a pitch went out: log it, then keep mentoring (next brands, "
        "follow-ups, rates). Kit-in-bio is a side note, not a lecture and not a gate. "
        "Brands already get the kit from the pitch; we can see who viewed it. "
        "If they cannot add a bio link yet (low followers), say that's fine.\n"
        "- If intent=generate_pitch: `say` is 1-2 short sentences. Never write Subject, "
        "the email body, or 'Hey team'. The UI already shows the pitch card.\n"
        "- If the pitch still has [CITY, COUNTRY] or other placeholders: do not tell them to "
        "open mail or send. Ask for city + country first. You are mentoring a new creator.\n"
        "- Never recommend a brand in already_pitched.\n"
        "- If TASK TRACKER mentions a kit view, that brand opened the pitch link. "
        "Treat it as a hot lead and offer a follow-up. Do not ignore it.\n"
        "- If CHECK-IN DUE or ACTIVE PAIN is set, ask the pulse first: "
        "Got any reply from that brand since they contacted them? "
        "Do not draft a follow-up until day 4 or they ask. "
        "Do not dump new brand cards unless they asked or the fix is lining up in-niche matches.\n"
        "- You allocate tools. Free-text questions (how to / why / what should / help me get "
        "replies) are coach_profile, coach_week, or chat — never generate_pitch. "
        "Never treat leftover words as a brand name. Never write 'isn't in our directory' "
        "unless they clearly named a company/product.\n"
        "- thanks / thank you / cool / nice / great / got it after a pitch is intent=chat, "
        "never generate_pitch. Those words are not brand names. Keep the unsent draft. "
        "Ask them to tap **I sent it** when it's out.\n"
        "- Only generate_pitch when they name a company/product (on suggested_brands, "
        "already in the thread, or 'hit up' / Contact / pitch <Name>). "
        "Only name directory brands. Never invent brands, emails, or UI screens.\n"
        "- If they tap Help me get more replies from brands: intent=coach_profile. "
        "Audit kit + bio + rates + follow-up habits + niche clarity. Not kit-only. "
        "Do not invent follower counts.\n"
        "- If they tap Write a pitch for a brand I name: intent=ask_brand. "
        "Ask for the brand name only. Do not draft until they name one.\n"
        "- If they ask for paid collabs, paid UGC, paid opportunities, paid offers, "
        "gigs, to get paid, which brands pay / who pays for UGC: intent=suggest_gigs. "
        "Skip the discovery / kit quiz. In `say`, explain simply: you pull paid UGC "
        "briefs from AspireIQ, LinkedIn and other platforms into one list so they "
        "don't hunt board-by-board. The UI shows Apply here cards labelled by source. "
        "Do not mix those with gifted Directory Contact cards. Never treat leftover "
        "query words as a brand name. Never say a query 'isn't in our directory'. "
        "If My Kit has no rates, tell them to add rates there (lever, not a gate).\n"
        "- After they confirm a pitch went out: log it. Do not write remaining unlock "
        "counts, 'this month', or Unlock Pro — the server appends one credit line. "
        "Do not ask them to draft the next brand if they are out of unlocks.\n"
        "- Out of free unlocks: never mention the monthly reset, the 1st, or waiting until "
        "next month. Point them at unlocking Pro to keep pitching a new brand — not one "
        "they already sent.\n"
        "- If they ask when credits reset: still no calendar date. Sell Pro for this week.\n"
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
    pending_draft: Optional[str] = None,
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
    pending_label = (pending_draft or "").strip()
    if pending_label:
        extra += (
            f"\nUnsent draft still on the card: {pending_label}. "
            "Acks (thanks, cool, nice) keep that draft. Never treat the ack as a brand."
        )
    system = polly_system_prompt(profile_context, extra=extra)
    user_prompt = json.dumps({
        "latest_user_message": text,
        "suggested_brands": brand_catalog,
        "explicit_brand_id": brand_id,
        "forced_intent": force_intent,
        "already_pitched": pitched_names,
        "pending_unsent_draft": pending_label or None,
    })
    try:
        parsed = _llm_generate_json(system, user_prompt, history=history)
    except Exception as err:
        print(f"[Polly] LLM classify failed, using heuristic: {_redact_secrets(err)}")
        return heuristic

    intent = str(parsed.get("intent") or heuristic["intent"]).strip().lower()
    allowed = {
        "suggest_gigs", "suggest_brands", "generate_pitch", "chat", "discovery",
        "explain_newcollab", "coach_week", "coach_portfolio", "coach_rates",
        "coach_profile", "ask_brand",
    }
    if intent not in allowed:
        intent = heuristic["intent"]
    if force_intent in allowed:
        intent = force_intent
    elif leftover_is_prompt(text) or is_casual_ack(text):
        if intent == "generate_pitch":
            coach = heuristic.get("intent") or "chat"
            intent = coach if coach != "generate_pitch" else "chat"
        parsed["brand_id"] = None
        parsed["brand_name"] = None
        gem_say = str(parsed.get("say") or "")
        if "isn't in our directory" in gem_say.lower() or "closest we do have" in gem_say.lower():
            parsed["say"] = ""
    elif heuristic["intent"] == "generate_pitch" and looks_like_brand_request(text, history):
        asked = requested_brand_name(
            text,
            suggested_brands,
            history,
            brand_name=heuristic.get("brand_name") or parsed.get("brand_name"),
        )
        matched = resolve_brand(suggested_brands, brand_name=asked) or resolve_brand(
            names_mentioned_by_assistant(history),
            brand_name=asked,
        )
        if matched or (asked and candidate_looks_like_brand_name(asked) and (
            _CONTACT_RE.search(text or "") or _CONTACT_ASK_RE.search(text or "")
        )):
            intent = "generate_pitch"
            if asked and not parsed.get("brand_name"):
                parsed["brand_name"] = asked
    elif heuristic["intent"] in ("suggest_gigs", "suggest_brands") and intent == "chat" and is_deal_search(text):
        intent = deal_pool_intent(text) or heuristic["intent"]
    if is_done_turn(text) and intent in ("suggest_brands", "suggest_gigs") and not force_intent:
        intent = "chat"
    from services.polly_gigs import wants_more_gigs
    more_gigs_turn = wants_more_gigs(text, history)
    if more_gigs_turn and not force_intent:
        intent = "suggest_gigs"
        parsed["brand_id"] = None
        parsed["brand_name"] = None
    elif is_more_brands_turn(text) and not force_intent:
        intent = "suggest_brands"
    if is_deal_search(text) and not force_intent and not more_gigs_turn:
        intent = deal_pool_intent(text) or "suggest_brands"
        parsed["brand_id"] = None
        parsed["brand_name"] = None
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
            "If unpublished: tell them to tap My portfolio. Never paste an editor path. "
            "Keep pitching either way.\n"
            "If published: specific notes from kit_snapshot, then offer the exact live URL "
            "for bio if they have a link slot — not newcollab.co homepage. "
            "Missing bio link is not an immediate no. Low-follower accounts often cannot add one yet.\n"
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
