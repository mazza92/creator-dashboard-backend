"""TikTok queries for the Gifting Radar: phrases creators actually caption gifted posts with.

Each niche has its PR hashtags plus searches spread across sub-niches (a tech creator's PR is a
keyboard, earbuds or a desk setup, not 'gifted tech'). Order matters: the first queries are the
highest-yield, and later rounds rotate through the rest while earlier ones sit in the cache.
"""

from __future__ import annotations

from typing import Dict, List

SHARED_HASHTAGS = ("prpackage", "gifted")

QUERY_BANK: Dict[str, Dict[str, List[str]]] = {
    "skincare": {
        "hashtags": ["skincarepr", "prunboxing"],
        "searches": ["skincare pr unboxing", "skincare pr haul", "kbeauty pr package", "first skincare pr package",
                     "thank you for sending skincare", "sunscreen pr unboxing", "skincare brands that sent me",
                     "korean skincare pr haul", "gifted skincare routine", "serum pr package review",
                     "skincare pr small creator", "body care pr unboxing"],
    },
    "makeup": {
        "hashtags": ["makeuppr", "prunboxing"],
        "searches": ["makeup pr unboxing", "makeup pr haul", "first makeup pr package", "lip gloss pr package",
                     "makeup brands that sent me", "mascara pr unboxing", "gifted makeup review",
                     "blush pr package", "drugstore makeup pr haul", "thank you for sending makeup",
                     "makeup pr small creator", "lip combo pr unboxing"],
    },
    "haircare": {
        "hashtags": ["haircarepr", "prunboxing"],
        "searches": ["haircare pr unboxing", "hair pr package", "curly hair pr haul", "hair oil pr package",
                     "hair tools pr unboxing", "hair brands that sent me", "first hair pr package",
                     "wig pr unboxing", "shampoo pr unboxing", "thank you for sending hair",
                     "hair growth pr package", "haircare pr small creator"],
    },
    "nails": {
        "hashtags": ["nailpr", "prunboxing"],
        "searches": ["press on nails pr", "nail polish pr unboxing", "gifted press on nails", "nail pr package",
                     "nail brands that sent me", "gel nail kit pr", "first nail pr package", "nail art pr haul",
                     "press ons sent me", "nail pr small creator"],
    },
    "fragrance": {
        "hashtags": ["fragrancepr", "perfumepr"],
        "searches": ["perfume pr unboxing", "fragrance pr package", "perfume pr haul", "body mist pr package",
                     "fragrance brands that sent me", "first perfume pr package", "niche perfume pr unboxing",
                     "thank you for sending perfume", "perfume pr small creator", "body spray pr haul"],
    },
    "fashion": {
        "hashtags": ["fashionpr", "prunboxing"],
        "searches": ["clothing pr haul", "fashion pr unboxing", "gifted try on haul", "jewelry pr unboxing",
                     "shoes pr unboxing", "bag pr unboxing", "fashion brands that sent me",
                     "first fashion pr package", "swimwear pr try on", "thank you for sending outfit",
                     "fashion pr small creator", "sunglasses pr unboxing"],
    },
    "fitness": {
        "hashtags": ["fitnesspr", "gympr"],
        "searches": ["activewear pr haul", "gym pr package", "gifted gym clothes", "protein pr unboxing",
                     "pre workout pr package", "fitness brands that sent me", "leggings pr try on",
                     "first fitness pr package", "supplement pr unboxing", "pilates pr package",
                     "fitness pr small creator", "running shoes pr unboxing"],
    },
    "food": {
        "hashtags": ["foodpr", "prunboxing"],
        "searches": ["snack pr package", "food pr unboxing", "coffee pr package", "gifted snacks",
                     "matcha pr package", "drink pr unboxing", "food brands that sent me", "tea pr package",
                     "chocolate pr box", "sauce pr package", "food pr small creator", "candy pr unboxing"],
    },
    "wellness": {
        "hashtags": ["wellnesspr", "prunboxing"],
        "searches": ["supplement pr package", "wellness pr unboxing", "gifted vitamins", "collagen pr package",
                     "gut health pr package", "self care pr haul", "wellness brands that sent me",
                     "greens powder pr", "sleep pr package", "wellness pr small creator"],
    },
    "home": {
        "hashtags": ["homepr", "prunboxing"],
        "searches": ["home decor pr haul", "home pr unboxing", "gifted home decor", "candle pr package",
                     "cleaning pr package", "kitchen pr unboxing", "bedding pr unboxing", "home brands that sent me",
                     "organization pr haul", "home pr small creator"],
    },
    "pet": {
        "hashtags": ["petpr", "dogpr"],
        "searches": ["dog pr package", "pet pr unboxing", "gifted dog toys", "cat pr package",
                     "pet brands that sent me", "dog treats pr package", "puppy pr haul", "cat toys pr unboxing",
                     "dog pr small creator", "pet pr haul"],
    },
    "baby": {
        "hashtags": ["babypr", "mompr"],
        "searches": ["baby pr package", "mom pr unboxing", "gifted baby products", "toddler pr haul",
                     "baby brands that sent me", "postpartum pr package", "nursery pr unboxing",
                     "kids toys pr unboxing", "mom pr small creator", "baby essentials pr haul"],
    },
    "tech": {
        "hashtags": ["techpr", "techtok"],
        "searches": ["tech pr unboxing", "tech pr package", "keyboard pr unboxing", "headphones pr unboxing",
                     "desk setup pr", "gadget pr unboxing", "tech girl pr package", "earbuds pr unboxing",
                     "phone case pr package", "gaming setup pr package", "tech brands that sent me",
                     "smart home pr unboxing", "camera gear pr unboxing", "first tech pr package"],
    },
}


INTENT = ("pr", "gifted", "sent", "unboxing", "haul", "ad", "partner", "sponsored", "collab", "package")

_AI_PROMPT = """You write TikTok search queries that find posts where small creators show products a brand
sent them (PR packages, gifted items, brand-sent unboxings) or paid partnerships.

Creator niche: {niche}
Their content: {themes}
Country: {country}

Write {n} queries. Rules:
- Each targets a different, specific sub-niche, product type or problem inside the niche (for skincare:
  tretinoin, eczema, sunscreen, korean skincare, body care, acne patches), so the results barely overlap.
  Lean toward what this creator actually posts about.
- Each pairs that sub-niche with how creators caption gifted posts: "pr package", "pr unboxing", "gifted",
  "sent me", "pr haul", "brand sent", "ad".
- A few may add their country or a big retailer there when it reads naturally.
- 2 to 6 words, lowercase, worded the way a creator would caption it. No brand names, no # symbols.
- Don't repeat these: {avoid}

Return JSON: {{"queries": ["..."]}}"""


def _write_json(prompt: str, _user: str) -> Dict:
    """Gemini without thinking: the queries are a short list and the creator is waiting on them."""
    import json
    import os

    import requests

    key = os.getenv("GEMINI_API_KEY")
    if not key:
        raise ValueError("GEMINI_API_KEY not configured")
    model = os.getenv("GEMINI_PR_READY_MODEL", "gemini-2.5-flash")
    resp = requests.post(
        f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={key}",
        json={"contents": [{"role": "user", "parts": [{"text": prompt}]}],
              "generationConfig": {"temperature": 0.7, "responseMimeType": "application/json",
                                   "thinkingConfig": {"thinkingBudget": 0}}},
        timeout=20,
    )
    resp.raise_for_status()
    data = resp.json()
    from services.gemini_usage import record_gemini_usage
    record_gemini_usage("radar_queries", model, data)
    text = data["candidates"][0]["content"]["parts"][0]["text"]
    return json.loads(text)


def _clean_query(raw) -> str:
    q = " ".join(str(raw or "").lower().replace("#", " ").split())
    if not 2 <= len(q.split()) <= 7 or len(q) > 60:
        return ""
    if not any(w in INTENT for w in q.split()):
        q += " pr"
    return q


def ai_queries(niche: str, profile: Dict[str, str] = None, avoid: List[str] = (), n: int = 16,
               write=None) -> List[str]:
    """Narrow sub-niche searches written for this creator, or [] when the model is unavailable."""
    profile = profile or {}
    write = write or _write_json
    seen = {str(a).lower() for a in avoid or []}
    prompt = _AI_PROMPT.format(niche=niche, themes=profile.get("themes") or "not known",
                               country=profile.get("country") or "not known", n=n,
                               avoid=", ".join(sorted(seen)[:40]) or "none")
    try:
        reply = write(prompt, "Write the queries.")
    except Exception as err:
        print(f"[radar] query writer skipped: {err}")
        return []
    out = []
    for raw in (reply or {}).get("queries") or []:
        q = _clean_query(raw)
        if q and q not in seen and q not in out:
            out.append(q)
    return out[:n]


def niche_queries(niche: str) -> Dict[str, List[str]]:
    bank = QUERY_BANK.get(niche)
    if bank:
        return bank
    return {"hashtags": [f"{niche}pr"],
            "searches": [f"{niche} pr unboxing", f"{niche} pr package", f"{niche} pr haul",
                         f"{niche} brands that sent me", f"first {niche} pr package", f"{niche} pr small creator"]}
