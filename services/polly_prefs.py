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
    for key in ("avoid_categories", "avoid_retailers"):
        if patch.get(key):
            prefs[key] = list(dict.fromkeys(list(prefs.get(key) or []) + list(patch[key])))
    out["prefs"] = prefs
    if prefs.get("gifted_only"):
        out["deal_intent"] = "gifted"
        if str(out.get("goal_30d") or "").strip().lower() == "paid ugc":
            out.pop("goal_30d", None)
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
