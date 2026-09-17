"""Match brand targeting (pool DB) against creator scrape.

Never invent gender from a first name. Men's-only brands stay hidden unless
the scrape itself shows men's content or male presentation.
"""

from __future__ import annotations

import json
import re
from typing import Any, Dict, Iterable, List, Optional, Set


# Brand copy / name / target_audience. "female" is not "male".
_MENS_BRAND_RE = re.compile(
    r"(?:men'?s|menswear|\bmens\b|\bfor men\b|\blooking for men\b|"
    r"\bmen ages?\b|\bmen \d{2}|\bmale grooming\b|\bmale\b|\bgentlemen\b|"
    r"\bbarbershop\b|\bbeard oil\b|\bmen\b)",
    re.I,
)
_WOMENS_BRAND_RE = re.compile(
    r"(?:women'?s|womenswear|\bwomens\b|\bfor women\b|\blooking for women\b|"
    r"\bladies\b|\bfemale\b|\bmaternity\b|\bmoms?\b|\bmums?\b|\bwomen\b)",
    re.I,
)

# Scrape-only creator signals. No name dictionaries.
_MENS_CREATOR_RE = re.compile(
    r"\b(?:he/him|him/he|\bdad\b|\bdaddy\b|\bfather\b|\bhusband\b|"
    r"menswear|men's fashion|men's style|male creator|as a guy|"
    r"beard)\b",
    re.I,
)
_WOMENS_CREATOR_RE = re.compile(
    r"\b(?:she/her|her/she|\bmom\b|\bmum\b|\bmama\b|\bwife\b|"
    r"womenswear|women's fashion|grwm|get ready with me|"
    r"as a girl|as a woman)\b",
    re.I,
)

_STOP = frozenset({
    "the", "a", "an", "and", "or", "for", "with", "from", "this", "that", "your",
    "our", "their", "its", "to", "of", "in", "on", "at", "by", "is", "are", "be",
    "as", "we", "you", "they", "it", "brand", "brands", "product", "products",
    "creator", "creators", "content", "official", "shop", "store", "new", "best",
    "made", "using", "use", "all", "about", "more", "than", "into", "over",
    "looking", "seeking", "join", "already", "hundreds", "thousands",
    "men", "mens", "male", "women", "womens", "female", "guys", "ladies",
})

LANE_MEN = "men"
LANE_WOMEN = "women"
LANE_BOTH = "both"
LANE_NONE = None


def _flatten(*parts: Any) -> str:
    bits = []

    def walk(part: Any) -> None:
        if part is None:
            return
        if isinstance(part, dict):
            for value in part.values():
                walk(value)
            return
        if isinstance(part, (list, tuple)):
            for item in part:
                walk(item)
            return
        text = str(part).strip()
        if text:
            bits.append(text)

    for part in parts:
        walk(part)
    return " ".join(bits).lower()


def brand_blob(brand: Optional[Dict]) -> str:
    brand = brand or {}
    return _flatten(
        brand.get("name"),
        brand.get("brand_name"),
        brand.get("slug"),
        brand.get("description"),
        brand.get("hero_product"),
        brand.get("product_sku_name"),
        brand.get("target_audience"),
        brand.get("category"),
        brand.get("regions"),
    )


def creator_blob(profile: Optional[Dict], niches: Optional[Iterable] = None) -> str:
    profile = profile or {}
    aesthetic = profile.get("aesthetic") or {}
    if isinstance(aesthetic, str):
        try:
            aesthetic = json.loads(aesthetic)
        except Exception:
            aesthetic = {}
    return _flatten(
        profile.get("raw_bio"),
        profile.get("bio"),
        profile.get("primary_niche"),
        profile.get("secondary_niches"),
        profile.get("content_themes"),
        profile.get("recent_captions"),
        niches,
        profile.get("match_intent_lanes"),
        profile.get("match_proof_lanes"),
        (aesthetic or {}).get("aesthetic_descriptors") if isinstance(aesthetic, dict) else None,
        profile.get("aesthetic_descriptors"),
        (aesthetic or {}).get("overall_vibe") if isinstance(aesthetic, dict) else None,
        profile.get("location"),
        profile.get("city"),
        profile.get("country"),
    )


def _lane_from_patterns(text: str, men_re: re.Pattern, women_re: re.Pattern) -> Optional[str]:
    men = bool(text and men_re.search(text))
    women = bool(text and women_re.search(text))
    if men and women:
        return LANE_BOTH
    if men:
        return LANE_MEN
    if women:
        return LANE_WOMEN
    return LANE_NONE


def brand_gender_lane(brand: Optional[Dict]) -> Optional[str]:
    return _lane_from_patterns(brand_blob(brand), _MENS_BRAND_RE, _WOMENS_BRAND_RE)


def creator_gender_lane(profile: Optional[Dict], niches: Optional[Iterable] = None) -> Optional[str]:
    return _lane_from_patterns(creator_blob(profile, niches), _MENS_CREATOR_RE, _WOMENS_CREATOR_RE)


# Wellness is skincare/supplements, not gym. Fitness ≠ beauty.
_LANE_BY_TOKEN = {
    "skincare": "beauty",
    "beauty": "beauty",
    "makeup": "beauty",
    "haircare": "beauty",
    "hair": "beauty",
    "wellness": "beauty",
    "self-care": "beauty",
    "selfcare": "beauty",
    "fitness": "fitness",
    "activewear": "fitness",
    "athleisure": "fitness",
    "sports": "fitness",
    "running": "fitness",
    "fashion": "fashion",
    "apparel": "fashion",
    "clothing": "fashion",
    "streetwear": "fashion",
    "luxury": "fashion",
    "food": "food",
    "beverages": "food",
    "kitchen": "food",
    "tech": "tech",
    "gadgets": "tech",
    "gaming": "tech",
    "home": "home",
    "decor": "home",
    "parenting": "parenting",
    "baby": "parenting",
    "family": "parenting",
    "lifestyle": "lifestyle",
}

# Adjacent product finds — lifestyle can sit next to beauty/home/food, never running shoes.
_LANE_COMPATIBLE = {
    "beauty": {"beauty", "lifestyle"},
    "lifestyle": {"lifestyle", "beauty", "home", "food"},
    "home": {"home", "lifestyle"},
    "food": {"food", "lifestyle"},
    "fitness": {"fitness"},
    "fashion": {"fashion"},
    "tech": {"tech"},
    "parenting": {"parenting", "lifestyle", "home", "beauty"},
}

_FITNESS_NAME_RE = re.compile(
    r"\b(on running|on cloud|running shoes?|run club|athletic wear|"
    r"athleisure|gym wear|trainers|sneakers)\b",
    re.I,
)

# Household names that do not reply to cold UGC from micros. Reply chance, not prestige.
_HOUSEHOLD_COLD_RE = re.compile(
    r"\b("
    r"on running|on cloud|"
    r"nike|adidas|lululemon|"
    r"new balance|under armour|underarmor|"
    r"puma|asics|hoka|allbirds|reebok|salomon|"
    r"sephora|ulta(\s+beauty)?|"
    r"apple|samsung|\bamazon\b|walmart|"
    r"gucci|dior|\bchanel\b|prada|louis vuitton|\bherm[eè]s\b|"
    r"coca[- ]cola|\bpepsi\b|starbucks|mcdonald'?s"
    r")\b",
    re.I,
)

HOUSEHOLD_REPLY_MIN_FOLLOWERS = 20000


def _split_niche_tokens(*parts: Any) -> List[str]:
    out: List[str] = []
    for part in parts:
        if part is None:
            continue
        if isinstance(part, (list, tuple, set)):
            for item in part:
                out.extend(_split_niche_tokens(item))
            continue
        raw = str(part).strip().lower()
        if not raw:
            continue
        chunks = [raw]
        for sep in (" & ", " and ", "/", ",", "+", "|"):
            if sep in raw:
                chunks = [bit.strip() for bit in raw.split(sep) if bit.strip()]
                break
        for chunk in chunks:
            if chunk not in out:
                out.append(chunk)
    return out


def _lanes_from_tokens(tokens: Iterable[str]) -> Set[str]:
    lanes: Set[str] = set()
    for token in tokens or []:
        lane = _LANE_BY_TOKEN.get(str(token).lower().strip())
        if lane:
            lanes.add(lane)
    return lanes


def creator_content_lanes(
    profile: Optional[Dict] = None,
    niches: Optional[Iterable] = None,
    required_categories: Optional[Iterable] = None,
) -> Set[str]:
    profile = profile or {}
    tokens = _split_niche_tokens(
        required_categories,
        niches,
        profile.get("primary_niche"),
        profile.get("secondary_niches"),
        profile.get("match_intent_lanes"),
    )
    return _lanes_from_tokens(tokens)


def brand_content_lanes(brand: Optional[Dict] = None) -> Set[str]:
    brand = brand or {}
    tokens = _split_niche_tokens(brand.get("category"), brand.get("brand_niches"))
    lanes = _lanes_from_tokens(tokens)
    name = str(brand.get("name") or brand.get("brand_name") or "")
    if _FITNESS_NAME_RE.search(name):
        lanes.add("fitness")
    return lanes


def category_mismatch(
    brand: Optional[Dict],
    profile: Optional[Dict] = None,
    niches: Optional[Iterable] = None,
    required_categories: Optional[Iterable] = None,
) -> bool:
    """True when brand lane cannot overlap the creator's lane. Live rosters still obey this."""
    creator_lanes = creator_content_lanes(profile, niches, required_categories)
    brand_lanes = brand_content_lanes(brand)
    if not creator_lanes or not brand_lanes:
        return False
    for lane in creator_lanes:
        allowed = _LANE_COMPATIBLE.get(lane, {lane})
        if brand_lanes & allowed:
            return False
    return True


def creator_follower_count(profile: Optional[Dict] = None) -> int:
    profile = profile or {}
    for key in ("follower_count", "followers", "creator_followers", "followers_count", "media_kit_followers"):
        try:
            value = int(profile.get(key) or 0)
        except (TypeError, ValueError):
            continue
        if value > 0:
            return value
    return 0


def too_big_to_reply(brand: Optional[Dict], profile: Optional[Dict] = None) -> bool:
    """Household / high min-follower brands a micro will not get a reply from."""
    brand = brand or {}
    profile = profile or {}
    followers = creator_follower_count(profile)
    name = str(brand.get("name") or brand.get("brand_name") or "")
    try:
        min_followers = int(brand.get("min_followers") or 0)
    except (TypeError, ValueError):
        min_followers = 0
    if _HOUSEHOLD_COLD_RE.search(name) and followers < HOUSEHOLD_REPLY_MIN_FOLLOWERS:
        return True
    if followers and min_followers and min_followers > max(followers * 8, 5000):
        return True
    return False


def opportunity_mismatch(
    brand: Optional[Dict],
    profile: Optional[Dict] = None,
    niches: Optional[Iterable] = None,
    required_categories: Optional[Iterable] = None,
) -> bool:
    """Drop off-niche or unreachable brands. Reply chance beats famous logos."""
    if category_mismatch(brand, profile, niches, required_categories):
        return True
    if too_big_to_reply(brand, profile):
        return True
    return False


def audience_mismatch(brand: Optional[Dict], profile: Optional[Dict] = None, niches: Optional[Iterable] = None) -> bool:
    """True when the brand's stated audience conflicts with scrape proof.

    Men's-only brands require men's signals on the creator. Unknown lifestyle
    scrapes (no pronouns) are not a match for 'looking for men'.
    Women's-only brands are only dropped for clearly men's creators.
    """
    brand_lane = brand_gender_lane(brand)
    if brand_lane in (LANE_NONE, LANE_BOTH):
        return False
    creator_lane = creator_gender_lane(profile, niches)
    if brand_lane == LANE_MEN:
        return creator_lane not in (LANE_MEN, LANE_BOTH)
    if brand_lane == LANE_WOMEN:
        return creator_lane == LANE_MEN
    return False


def _tokens(text: str) -> Set[str]:
    return {
        t for t in re.findall(r"[a-z]{4,}", (text or "").lower())
        if t not in _STOP
    }


def scrape_overlap_tokens(brand: Optional[Dict], profile: Optional[Dict] = None, niches: Optional[Iterable] = None) -> Set[str]:
    return _tokens(brand_blob(brand)) & _tokens(creator_blob(profile, niches))


def scrape_overlap_score(brand: Optional[Dict], profile: Optional[Dict] = None, niches: Optional[Iterable] = None) -> int:
    overlap = scrape_overlap_tokens(brand, profile, niches)
    cat = str((brand or {}).get("category") or "").lower().strip()
    niche = str((profile or {}).get("primary_niche") or "").lower().strip()
    score = len(overlap) * 3
    if cat and cat == niche:
        score += 8
    elif cat and cat in creator_blob(profile, niches):
        score += 4
    try:
        score += min(12, int((brand or {}).get("match_score") or 0) // 10)
    except (TypeError, ValueError):
        pass
    try:
        followers = int((profile or {}).get("follower_count") or 0)
        min_f = int((brand or {}).get("min_followers") or 0)
        max_f = int((brand or {}).get("max_followers") or 0)
        if followers and min_f:
            if followers >= min_f and (not max_f or followers <= int(max_f * 1.25)):
                score += 6
            elif followers < min_f * 0.5:
                score -= 4
    except (TypeError, ValueError):
        pass
    loc = _tokens(_flatten(
        (profile or {}).get("location"),
        (profile or {}).get("city"),
        (profile or {}).get("country"),
    ))
    brand_geo = _tokens(_flatten(
        (brand or {}).get("regions"),
        (brand or {}).get("target_audience"),
    ))
    if loc and loc & brand_geo:
        score += 6
    return score


_GENERIC_OVERLAP = frozenset({
    "beauty", "skincare", "lifestyle", "journey", "content", "posts", "brand",
    "natural", "organic", "clean", "premium", "daily", "routine", "self",
    "care", "wellness", "health", "skin", "face", "body", "hair", "makeup",
    "product", "products", "creator", "creators", "collab", "gifted",
})


def _is_live_campaign(brand: Optional[Dict]) -> bool:
    brand = brand or {}
    if str(brand.get("source") or "").lower() in ("recruiting", "open_lists"):
        return True
    try:
        if int(brand.get("roster_spotlighted") or 0):
            return True
        if int(brand.get("roster_is_open") or brand.get("roster_open") or 0):
            return True
        if int(brand.get("roster_fill_count") or 0) > 0:
            return True
    except (TypeError, ValueError):
        return False
    return False


def match_why(brand: Optional[Dict], profile: Optional[Dict] = None, niches: Optional[Iterable] = None) -> str:
    """Short, brand-specific reason. Never a recycled token dump."""
    brand = brand or {}
    profile = profile or {}
    name = (brand.get("name") or brand.get("brand_name") or "").strip()
    cat = (brand.get("category") or "").strip()
    niche = (profile.get("primary_niche") or "").strip()
    hero = (brand.get("hero_product") or brand.get("product_sku_name") or "").strip()
    if _is_live_campaign(brand):
        if hero:
            return f"Open PR roster now — they're filling spots for {hero}."
        if cat:
            return f"Open PR roster now — actively recruiting {cat} creators."
        return "Open PR roster now — higher chance they actually reply."
    if hero and niche:
        return f"{hero} is a clean fit for your {niche} content."
    if hero:
        return f"They're looking for creators who can feature {hero}."
    desc = re.sub(r"\s+", " ", (brand.get("description") or "").strip())
    snippet = re.split(r"[.!?]", desc)[0].strip() if desc else ""
    if snippet and len(snippet) >= 12:
        if len(snippet) > 140:
            return snippet[:110].rstrip() + "…"
        return snippet
    overlap = [
        t for t in sorted(scrape_overlap_tokens(brand, profile, niches))
        if t not in _GENERIC_OVERLAP
    ][:2]
    if overlap and niche:
        return f"Your {niche} work overlaps {', '.join(overlap)}."
    if name and cat and niche and cat.lower() != niche.lower():
        return f"{name} is {cat} next to your {niche} lane."
    if name and cat:
        return f"{name} — {cat} brand, worth a tight pitch this week."
    if name and niche:
        return f"{name} sits in your {niche} lane."
    if cat:
        return f"{cat} — a tight pitch is worth it this week."
    if niche:
        return f"Fits your {niche} lane."
    return ""
