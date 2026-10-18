"""Rank roster applicants by fit to the brand, so the brand sees Polly's 5 first.

Creators tick 10-15 niches each, so "applied" says little. Fit leans on the creator's
primary niches, the brand's shipping countries, follower range and real sample posts.
Free brands see the shortlist plus a few alternates; the rest of the list is the
paid plan, the same way creators get 3 free unlocks.
"""

from __future__ import annotations

import json
import re
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

FREE_PICK_LIMIT = 5
PAID_PICK_LIMIT = 10
FREE_VISIBLE = 10
TOP_PICK_MIN_SCORE = 30

NICHE_GROUPS = {
    "beauty": ("beauty", "makeup", "cosmetic", "nail", "lash", "fragrance", "perfume"),
    "skincare": ("skincare", "skin care", "skin"),
    "haircare": ("hair",),
    "wellness": ("wellness", "health", "supplement", "nutrition", "self care", "selfcare", "sleep",
                 "longevity", "holistic", "low tox"),
    "fitness": ("fitness", "activewear", "gym", "sport", "workout", "yoga", "outdoor"),
    "food": ("food", "drink", "beverage", "snack", "cooking", "recipe", "matcha", "coffee", "tea"),
    "home": ("home", "decor", "bedding", "kitchen", "interior", "cleaning"),
    "fashion": ("fashion", "clothing", "apparel", "style", "swim", "shoe"),
    "jewelry": ("jewelry", "jewellery", "accessor"),
    "baby": ("baby", "mom", "mum", "parent", "kids", "family"),
    "pet": ("pet", "dog", "cat"),
    "tech": ("tech", "gadget", "electronic", "gaming"),
    "travel": ("travel",),
    "grooming": ("grooming", "men", "shaving", "beard", "barber"),
}
RELATED = {
    "beauty": {"skincare", "haircare"},
    "skincare": {"beauty", "wellness"},
    "haircare": {"beauty"},
    "wellness": {"fitness", "food", "skincare"},
    "fitness": {"wellness"},
    "food": {"wellness"},
    "fashion": {"jewelry"},
    "jewelry": {"fashion"},
    "grooming": {"skincare", "haircare"},
}
EU_CODES = {"AT", "BE", "BG", "HR", "CY", "CZ", "DK", "EE", "FI", "FR", "DE", "GR", "HU", "IE", "IT",
            "LV", "LT", "LU", "MT", "NL", "PL", "PT", "RO", "SK", "SI", "ES", "SE"}
REGION_CODES = {"US": {"US"}, "UK": {"GB", "UK"}, "GB": {"GB", "UK"}, "AU": {"AU"}, "CA": {"CA"},
                "EU": EU_CODES, "NZ": {"NZ"}, "SG": {"SG"}}
REGION_NAMES = {"US": "the US", "UK": "the UK", "GB": "the UK", "AU": "Australia", "CA": "Canada",
                "EU": "the EU", "NZ": "New Zealand", "SG": "Singapore"}
FOLLOWER_RANGES = {"under 1k": (0, 1_000), "1k-10k": (1_000, 10_000), "10k-50k": (10_000, 50_000),
                   "50k-100k": (50_000, 100_000), "100k+": (100_000, 10**12)}


def _as_list(raw: Any) -> List[str]:
    if raw is None:
        return []
    if isinstance(raw, (list, tuple)):
        return [str(x).strip() for x in raw if str(x or "").strip()]
    text = str(raw).strip()
    if not text:
        return []
    if text.startswith("["):
        try:
            parsed = json.loads(text)
            if isinstance(parsed, list):
                return [str(x).strip() for x in parsed if str(x or "").strip()]
        except Exception:
            pass
    if text.startswith("{") and text.endswith("}"):
        text = text[1:-1]
    return [p.strip().strip('"') for p in re.split(r"[,/&|]", text) if p.strip().strip('"')]


def niche_group(label: str) -> Optional[str]:
    low = (label or "").lower()
    if not low:
        return None
    for group, words in NICHE_GROUPS.items():
        if any(w in low for w in words):
            return group
    return None


def brand_targets(category: Any = None, regions: Any = None, brief_niches: Any = None,
                  brief_regions: Any = None, follower_ranges: Any = None) -> Dict[str, Any]:
    niches: List[str] = []
    for label in _as_list(brief_niches) + _as_list(category):
        g = niche_group(label)
        if g and g not in niches:
            niches.append(g)
    region_list = [r.upper() for r in (_as_list(brief_regions) or _as_list(regions))]
    if not region_list or any(r in ("WORLDWIDE", "GLOBAL", "ANY") for r in region_list):
        countries: Optional[set] = None
    else:
        countries = set()
        for r in region_list:
            countries |= REGION_CODES.get(r, {r})
    ranges = []
    for label in _as_list(follower_ranges):
        key = label.lower().replace(" ", "")
        for name, bounds in FOLLOWER_RANGES.items():
            if key == name.replace(" ", ""):
                ranges.append(bounds)
    regions_label = [REGION_NAMES.get(r, r) for r in region_list if r in REGION_CODES] if countries else []
    return {"niches": niches, "countries": countries, "ranges": ranges, "regions_label": regions_label}


def _sample_count(card: Dict[str, Any]) -> int:
    return sum(1 for p in card.get("posts") or [] if isinstance(p, dict) and p.get("thumbnail_url"))


def fit_score(niches: Sequence[str], card: Dict[str, Any], targets: Dict[str, Any]) -> Tuple[int, List[str], bool]:
    """(score, reasons, eligible for the shortlist)."""
    score = 0
    reasons: List[str] = []
    groups = [niche_group(n) for n in niches]
    want = targets.get("niches") or []
    if want:
        primary = groups[0] if groups else None
        if primary in want:
            score += 40
            reasons.append(f"{niches[0].strip().capitalize()} is their main niche")
        elif any(g in want for g in groups[:3]):
            score += 25
            hit = next(n for n, g in zip(niches, groups) if g in want)
            reasons.append(f"Posts about {hit.strip().lower()}")
        elif primary and RELATED.get(primary, set()) & set(want):
            score += 15
            reasons.append(f"Close niche: {niches[0].strip().lower()}")
        elif any(g in want for g in groups):
            score += 8
    else:
        score += 15
    if len(niches) > 8:
        score -= 8

    countries = targets.get("countries")
    code = (card.get("country_code") or "").upper()
    country_ok = True
    if countries:
        if code and code in countries:
            score += 15
            reasons.append(f"Based in {card.get('country') or code}")
        elif code:
            score -= 60
            country_ok = False
        else:
            score -= 10

    samples = _sample_count(card)
    if samples >= 3:
        score += 15
        reasons.append(f"{samples} sample posts")
    elif samples:
        score += 8
        reasons.append(f"{samples} sample post{'s' if samples > 1 else ''}")
    else:
        score -= 25

    rate = card.get("engagement") or 0
    if rate >= 3:
        score += 10
        reasons.append(f"{card.get('engagement_label')} engagement")
    elif rate >= 1.5:
        score += 5

    followers = int(card.get("followers") or 0)
    ranges = targets.get("ranges") or []
    if ranges:
        score += 10 if any(lo <= followers < hi for lo, hi in ranges) else -5
    elif 1_000 <= followers <= 100_000:
        score += 6
    elif followers < 500:
        score -= 10

    eligible = country_ok and samples > 0 and score >= TOP_PICK_MIN_SCORE
    return score, reasons[:3], eligible


def rank_cards(pairs: Iterable[Tuple[Sequence[str], Dict[str, Any]]], targets: Dict[str, Any],
               top_n: int = FREE_PICK_LIMIT) -> List[Dict[str, Any]]:
    """Cards sorted best-fit first, each with fit_score / fit_reasons / top_pick.

    The sort is stable, so equal scores keep the incoming Pro-first order.
    """
    scored = []
    for niches, card in pairs:
        score, reasons, eligible = fit_score(list(niches or []), card, targets)
        card["fit_score"] = score
        card["fit_reasons"] = reasons
        card["top_pick"] = False
        scored.append((eligible, score, card))
    scored.sort(key=lambda t: (t[0], t[1]), reverse=True)
    picks = 0
    for eligible, _score, card in scored:
        if picks >= top_n:
            break
        if eligible and card.get("status") == "review" and not card.get("skipped"):
            card["top_pick"] = True
            picks += 1
    return [c for _e, _s, c in scored]


def split_visible(cards: List[Dict[str, Any]], subscribed: bool,
                  visible: int = FREE_VISIBLE) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Free brands see their picks, anyone already moving, and the top `visible` of the rest."""
    if subscribed:
        return cards, []
    shown, hidden, browsed = [], [], 0
    for card in cards:
        status = card.get("status")
        if card.get("selected") or status in ("ships", "posted"):
            shown.append(card)
        elif browsed < visible:
            shown.append(card)
            browsed += 1
        else:
            hidden.append(card)
    return shown, hidden


def locked_teasers(hidden: List[Dict[str, Any]], limit: int = 6) -> Dict[str, Any]:
    return {
        "count": len(hidden),
        "teasers": [
            {"niche": c.get("niche") or "", "followers_label": c.get("followers_label") or "",
             "country_code": c.get("country_code") or ""}
            for c in hidden[:limit]
        ],
    }


def pick_limit_for(subscribed: bool) -> int:
    return PAID_PICK_LIMIT if subscribed else FREE_PICK_LIMIT
