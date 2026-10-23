"""Stated creator preferences: remembered in notes["prefs"] and applied to matches.

Examples: "I don't want UGC yet" (gifted only), "no nails", "no fashion",
"nothing sold at Walmart or Shoppers".
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

_NEG_BARE = r"no|not|avoid|skip|without|nothing|no more"
_NEG_VERB = (
    r"(?:don'?t|dont|do not|didn'?t|didnt|never|not|won'?t|wont)\s+"
    r"(?:really\s+)?(?:want|like|do|need|care for|pitch|show)"
    r"|hate|not into|not interested in|stop showing|avoid|skip"
)
_ANY_NEG = rf"{_NEG_BARE}|{_NEG_VERB}"


def _neg_re(alt: str, span: int) -> "re.Pattern[str]":
    return re.compile(
        rf"(?i)\b(?:{_NEG_BARE})\s+(?:(?:more|any|the|those|these)\s+)?(?:{alt})\b"
        rf"|\b(?:{_NEG_VERB})\b[^.!?\n]{{0,{span}}}\b(?:{alt})\b"
    )


_NO_UGC_RE = re.compile(
    rf"(?i)\b(?:{_NEG_VERB})\b[^.!?\n]{{0,25}}\b(?:ugc|paid\s+(?:work|deals?|gigs?|collabs?|ugc))\b"
    r"|\b(?:no|not)\s+(?:paid\s+)?ugc\b"
    r"|\bgifted\s+only\b|\bonly\s+(?:want\s+)?gifted\b|\bjust\s+gifted\b"
    r"|\bugc\s+(?:just\s+)?yet\b|\bnot\s+ready\s+for\s+(?:paid|ugc)\b"
)
_WANTS_PAID_RE = re.compile(
    r"(?i)\b(?:i\s+)?(?:want|ready for|looking for|open to)\s+(?:some\s+)?(?:paid|ugc)\b"
    r"|\bpaid\s+(?:is\s+)?(?:fine|ok|okay)\s+now\b"
)

CATEGORY_WORDS = {
    "nails": ("nail", "nails", "nail polish", "manicure", "press-on", "press on"),
    "fashion": ("fashion", "clothing", "apparel", "clothes"),
    "makeup": ("makeup", "make-up", "cosmetics"),
    "haircare": ("hair", "haircare", "hair care"),
    "fitness": ("fitness", "gym", "activewear"),
    "food": ("food", "snacks", "beverages", "drinks"),
    "supplements": ("supplement", "supplements", "vitamins"),
    "tech": ("tech brands", "gadgets", "electronics"),
    "pets": ("pet", "pets", "pet brands"),
    "alcohol": ("alcohol", "wine", "beer", "spirits"),
    "jewelry": ("jewelry", "jewellery"),
    "fragrance": ("fragrance", "perfume"),
    "baby": ("baby", "baby brands", "parenting"),
    "home": ("homeware", "home decor"),
    "skincare": ("skincare", "skin care"),
    "wellness": ("wellness",),
    "activewear": ("activewear", "athleisure"),
    "travel": ("travel", "luggage"),
    "gaming": ("gaming", "video games"),
    "automotive": ("automotive", "car brands", "cars"),
}

RETAILER_WORDS = {
    "walmart": ("walmart",),
    "shoppers": ("shoppers drug mart", "shoppers"),
    "target": ("at target", "target stores", "target brands"),
    "dollar store": ("dollar tree", "dollarama", "dollar store"),
    "costco": ("costco",),
    "amazon": ("amazon",),
    "drugstore": ("drugstore", "drug store"),
}


def _alt(words) -> str:
    return "|".join(re.escape(w) for w in sorted(words, key=len, reverse=True))


_CATEGORY_RE = {
    key: _neg_re(_alt(words), 25)
    for key, words in CATEGORY_WORDS.items()
}
_RETAILER_RE = {
    key: re.compile(rf"(?i)\b(?:{_ANY_NEG})\b[^.!?\n]{{0,50}}\b(?:{_alt(words)})\b")
    for key, words in RETAILER_WORDS.items()
}


def _list_next_re(prev_words, next_words) -> "re.Pattern[str]":
    """'nails or fashion', 'nails, no fashion' — a list continuing a negated category."""
    return re.compile(
        rf"(?i)\b(?:{_alt(prev_words)})\b\s*(?:,|or|and|nor|&)\s*(?:(?:or|and)\s+)?(?:no\s+)?(?:{_alt(next_words)})\b"
    )


LEAD_METRICS = ("followers", "views", "engagement", "none")
OFFER_TYPES = ("gifted_post", "gifted_ugc", "gifted_ugc_photos", "paid_only")
TONES = ("friendly", "professional")
PLATFORMS = ("tiktok", "instagram", "youtube", "pinterest", "facebook", "twitch", "x")
PITCH_PREF_KEYS = ("show_engagement_rate", "lead_metric", "platforms", "offer_type", "deliverables",
                   "usage_rights_months", "tone", "custom_template")
REWRITE_KEYS = ("lead_metric", "platforms", "offer_type", "deliverables", "usage_rights_months", "tone")
_PLATFORM_ALIASES = {"ig": "instagram", "insta": "instagram", "yt": "youtube", "tik tok": "tiktok",
                     "twitter": "x", "fb": "facebook", "shorts": "youtube", "reels": "instagram"}


def _platform(value: Any) -> str:
    key = str(value or "").strip().lower()
    key = _PLATFORM_ALIASES.get(key, key)
    return key if key in PLATFORMS else ""


def clean_pitch_prefs(raw: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Valid pitch-preference fields only. None or "" means 'clear this one'."""
    out: Dict[str, Any] = {}
    for key, val in (raw or {}).items():
        if key not in PITCH_PREF_KEYS:
            continue
        if val is None or val == "" or val == []:
            out[key] = None
        elif key == "show_engagement_rate":
            out[key] = bool(val)
        elif key == "lead_metric" and str(val).lower() in LEAD_METRICS:
            out[key] = str(val).lower()
        elif key == "offer_type" and str(val).lower() in OFFER_TYPES:
            out[key] = str(val).lower()
        elif key == "tone" and str(val).lower() in TONES:
            out[key] = str(val).lower()
        elif key == "platforms":
            items = val if isinstance(val, (list, tuple)) else re.split(r"[,/]| and ", str(val))
            names = list(dict.fromkeys(p for p in (_platform(i) for i in items) if p))
            if names:
                out[key] = names
        elif key == "usage_rights_months":
            try:
                months = int(float(val))
            except (TypeError, ValueError):
                continue
            if 0 <= months <= 24:
                out[key] = months
        elif key == "deliverables":
            out[key] = re.sub(r"\s+", " ", str(val)).strip()[:200]
        elif key == "custom_template":
            text = str(val).strip()[:3000]
            if len(text) >= 40:
                out[key] = text
    return out


_LEAD_RE = re.compile(r"(?i)\blead(?:s|ing)?\s+with\s+(?:my\s+)?(followers|views|engagement)\b")
_TONE_RE = re.compile(r"(?i)\b(?:more|sound|keep (?:it|them))\s+(professional|formal|friendly|casual|warm)\b")
_USAGE_RE = re.compile(r"(?i)\b(\d{1,2})[- ]?(?:month|mo)s?\b[^.!?\n]{0,25}\busage\b"
                       r"|\busage(?:\s+rights)?\b[^.!?\n]{0,20}?\b(\d{1,2})[- ]?(?:month|mo)s?\b")


def parse_pitch_preferences(text: str) -> Dict[str, Any]:
    """The few pitch preferences safe to read without the model (fallback path)."""
    raw = text or ""
    patch: Dict[str, Any] = {}
    m = _LEAD_RE.search(raw)
    if m:
        patch["lead_metric"] = m.group(1).lower()
    m = _TONE_RE.search(raw)
    if m:
        patch["tone"] = "professional" if m.group(1).lower() in ("professional", "formal") else "friendly"
    m = _USAGE_RE.search(raw)
    if m:
        patch["usage_rights_months"] = int(m.group(1) or m.group(2))
    return clean_pitch_prefs(patch)


def _allow_re(words) -> "re.Pattern[str]":
    alt = _alt(words)
    return re.compile(
        rf"(?i)\b(?:(?:i'?m|im|i am)\s+(?:ok|okay|fine|open)\s+(?:with\s+)?|show\s+me\s+|turn\s+on\s+|"
        rf"(?:bring|put)\s+back\s+|you\s+can\s+(?:suggest|show)\s+)(?:\w+\s+){{0,2}}(?:{alt})\b"
        rf"(?:[^.!?\n]{{0,20}}\b(?:again|now|back)\b)?"
        rf"|\b(?:{alt})\b[^.!?\n]{{0,15}}\b(?:is|are)\s+(?:ok|okay|fine)\s+(?:now|again)\b"
    )


def parse_preferences(text: str) -> Dict[str, Any]:
    """Preference patch from one creator message. Empty dict when nothing stated."""
    raw = (text or "").strip()
    if not raw or len(raw) > 600:
        return {}
    patch: Dict[str, Any] = {}
    if _NO_UGC_RE.search(raw):
        patch["gifted_only"] = True
    elif _WANTS_PAID_RE.search(raw):
        patch["gifted_only"] = False
    cats = [key for key, rx in _CATEGORY_RE.items() if rx.search(raw)]
    while cats:
        more = [
            key for key, words in CATEGORY_WORDS.items()
            if key not in cats and any(_list_next_re(CATEGORY_WORDS[c], words).search(raw) for c in cats)
        ]
        if not more:
            break
        cats += more
    if cats:
        patch["avoid_categories"] = cats
    shops = [key for key, rx in _RETAILER_RE.items() if rx.search(raw)]
    if shops:
        patch["avoid_retailers"] = shops
    back = [key for key, words in CATEGORY_WORDS.items()
            if key not in cats and _allow_re(words).search(raw)]
    if back:
        patch["allow_categories"] = back
        patch["avoid_categories"] = [c for c in patch.get("avoid_categories") or [] if c not in back]
        if not patch["avoid_categories"]:
            patch.pop("avoid_categories")
    from services.polly_pitch_edit import wants_no_engagement
    if wants_no_engagement(raw):
        patch["show_engagement_rate"] = False
    patch.update(parse_pitch_preferences(raw))
    return patch


def get_prefs(notes: Optional[Dict] = None) -> Dict[str, Any]:
    prefs = (notes or {}).get("prefs")
    return dict(prefs) if isinstance(prefs, dict) else {}


def merge_prefs(notes: Optional[Dict], patch: Optional[Dict]) -> Dict[str, Any]:
    out = dict(notes or {})
    if not patch:
        return out
    prefs = get_prefs(out)
    if "gifted_only" in patch:
        prefs["gifted_only"] = bool(patch["gifted_only"])
    for key, val in clean_pitch_prefs(patch).items():
        if val is None:
            prefs.pop(key, None)
        else:
            prefs[key] = val
    for key in ("avoid_categories", "avoid_retailers"):
        if patch.get(key):
            prefs[key] = list(dict.fromkeys(list(prefs.get(key) or []) + list(patch[key])))
    if patch.get("allow_categories"):
        allowed = {str(c).strip().lower() for c in patch["allow_categories"]}
        prefs["avoid_categories"] = [c for c in prefs.get("avoid_categories") or [] if c not in allowed]
    offer = patch.get("offer_type")
    if offer == "paid_only":
        prefs["gifted_only"] = False
        out["deal_intent"] = "paid"
        out["deal_intents"] = ["paid"]
    elif offer in OFFER_TYPES:
        out["deal_intent"] = "gifted"
    out["prefs"] = prefs
    if patch.get("gifted_only") is False and "offer_type" not in patch:
        out = add_deal_intent(out, "paid")
    if prefs.get("gifted_only"):
        out["deal_intent"] = "gifted"
        out["deal_intents"] = ["gifted"]
        if str(out.get("goal_30d") or "").strip().lower() == "paid ugc":
            out.pop("goal_30d", None)
    return out


_OFFER_LABEL = {"gifted_post": "gifted post", "gifted_ugc": "gifted + UGC",
                "gifted_ugc_photos": "gifted + UGC + photos", "paid_only": "paid only"}
_PLATFORM_LABEL = {"tiktok": "TikTok", "instagram": "Instagram", "youtube": "YouTube", "x": "X"}


def pitch_prefs_line(prefs: Optional[Dict[str, Any]]) -> str:
    """'Leads with views · no engagement · 1 TikTok + 2 UGC' for the pitch card."""
    prefs = prefs or {}
    if prefs.get("custom_template"):
        return "Your template"
    bits = []
    lead = prefs.get("lead_metric")
    if lead and lead != "none":
        bits.append(f"Leads with {lead}")
    elif lead == "none":
        bits.append("No stats")
    if prefs.get("show_engagement_rate") is False and lead != "engagement":
        bits.append("no engagement")
    if prefs.get("platforms"):
        bits.append(" + ".join(_PLATFORM_LABEL.get(p, p.title()) for p in prefs["platforms"]))
    if prefs.get("deliverables"):
        bits.append(prefs["deliverables"])
    elif prefs.get("offer_type"):
        bits.append(_OFFER_LABEL[prefs["offer_type"]])
    if prefs.get("usage_rights_months") is not None:
        n = prefs["usage_rights_months"]
        bits.append("no usage rights" if n == 0 else f"{n}-month usage")
    if prefs.get("tone"):
        bits.append(prefs["tone"])
    return " · ".join(bits)


def needs_rewrite(prefs: Optional[Dict[str, Any]]) -> bool:
    return any((prefs or {}).get(k) not in (None, "", []) for k in REWRITE_KEYS)


def deal_intents(notes: Optional[Dict] = None) -> List[str]:
    """Every kind of deal they want, e.g. ["gifted", "paid"]. deal_intent stays the current focus."""
    notes = notes or {}
    raw = notes.get("deal_intents")
    out = [d for d in (raw if isinstance(raw, list) else []) if d in ("gifted", "paid")]
    single = notes.get("deal_intent")
    if single in ("gifted", "paid") and single not in out:
        out.append(single)
    return out


def add_deal_intent(notes: Optional[Dict], intent: str) -> Dict[str, Any]:
    out = dict(notes or {})
    if intent not in ("gifted", "paid"):
        return out
    if intent == "paid" and get_prefs(out).get("gifted_only"):
        return out
    current = deal_intents(out)
    if get_prefs(out).get("offer_type") == "paid_only" and intent == "gifted":
        return out
    if intent not in current:
        current.append(intent)
    out["deal_intents"] = current
    return out


def gifted_only(notes: Optional[Dict] = None) -> bool:
    return bool(get_prefs(notes).get("gifted_only"))


def prefs_context(notes: Optional[Dict] = None) -> str:
    prefs = get_prefs(notes)
    bits = []
    if prefs.get("gifted_only"):
        bits.append("gifted PR only — no paid UGC pitches or gig lists unless they ask again")
    if prefs.get("avoid_categories"):
        bits.append("never suggest: " + ", ".join(prefs["avoid_categories"]))
    if prefs.get("avoid_retailers"):
        bits.append("skip brands sold at: " + ", ".join(prefs["avoid_retailers"]))
    if prefs.get("show_engagement_rate") is False:
        bits.append("never mention their engagement rate in pitches")
    if prefs.get("offer_type") == "paid_only":
        bits.append("paid only — suggest paid briefs and paid pitches, no gifted asks")
    line = pitch_prefs_line(prefs)
    if line:
        bits.append(f"pitch preferences already applied to every pitch: {line}")
    if not bits:
        return ""
    return "Stated preferences (hard rules): " + "; ".join(bits)


def _brand_text(brand: Dict[str, Any]) -> str:
    parts = [
        brand.get("name"), brand.get("brand_name"), brand.get("category"),
        brand.get("description"), brand.get("why"), brand.get("hero_product"),
        brand.get("target_audience"),
    ]
    return " ".join(str(p) for p in parts if p).lower()


def brand_blocked(brand: Optional[Dict], notes: Optional[Dict] = None) -> bool:
    if not isinstance(brand, dict):
        return False
    prefs = get_prefs(notes)
    cats = prefs.get("avoid_categories") or []
    shops = prefs.get("avoid_retailers") or []
    if not cats and not shops:
        return False
    category = str(brand.get("category") or "").lower()
    text = _brand_text(brand)
    for key in cats:
        words = CATEGORY_WORDS.get(key, (key,))
        if any(re.search(rf"\b{re.escape(w)}\b", category) for w in words):
            return True
        if key == "nails" and any(re.search(rf"\b{re.escape(w)}\b", text) for w in words):
            return True
    for key in shops:
        words = RETAILER_WORDS.get(key, (key,))
        if any(re.search(rf"\b{re.escape(w)}\b", text) for w in words):
            return True
    return False


def filter_brands_by_prefs(brands: Optional[List[Dict]], notes: Optional[Dict] = None) -> List[Dict[str, Any]]:
    rows = [b for b in (brands or []) if isinstance(b, dict)]
    if not get_prefs(notes):
        return rows
    return [b for b in rows if not brand_blocked(b, notes)]
