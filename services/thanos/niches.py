"""Thanos niches: which directory categories a creator matches, and which brands never fit them.

Moved from pr_crm_routes (For You) in Nov 2026 so the whole app reads one set of niche rules. Hard-won rules
live here: wellness != fitness, beauty/skincare != supplements, parenting creators never get CBD or fashion,
optical/wig brands only for creators who make that content, scrape lanes only count when they agree with the
niches the creator picked, sensitive categories only for beauty/wellness/lifestyle creators.
"""

import re

try:
    from services.fit_score_calculator import scrape_agrees_with_intent
except ImportError:  # pragma: no cover
    scrape_agrees_with_intent = None

SENSITIVE_CATEGORIES = ['intimacy', 'adult', 'sexual wellness']
SENSITIVE_ALLOWED_NICHES = ['beauty', 'wellness', 'lifestyle', 'self-care']


def excluded_categories_for(niches):
    """Sensitive categories stay out unless the creator works in beauty, wellness, lifestyle or self-care."""
    lowered = [str(n).lower() for n in (niches or [])]
    return [] if any(n in SENSITIVE_ALLOWED_NICHES for n in lowered) else list(SENSITIVE_CATEGORIES)


def get_min_follower_cap(creator_followers):
    """
    Get max brand min_followers requirement to show based on creator size.
    Prevents showing brands with high requirements to micro-creators.
    Uses min_followers (brand's creator requirement) as proxy for brand accessibility.
    """
    if not creator_followers or creator_followers < 1000:
        return 5000  # 200-follower micros: brands that actually accept micros
    if creator_followers < 5000:
        return 10000  # Show brands that accept creators under 10K
    elif creator_followers < 20000:
        return 50000  # Show brands that accept creators under 50K
    elif creator_followers < 50000:
        return 100000  # Show brands that accept creators under 100K
    else:
        return None  # No cap for 50K+ creators


def normalize_niche(niche_str):
    """
    Normalize compound niches like "tech & gadgets" into individual components.
    Returns a list of individual niche terms.
    Examples:
        "tech & gadgets" -> ["tech", "gadgets", "tech & gadgets"]
        "food & beverage" -> ["food", "beverage", "food & beverage"]
        "fitness" -> ["fitness"]
    """
    if not niche_str:
        return []

    niche_lower = niche_str.lower().strip()
    result = [niche_lower]  # Always include the original

    # Split by common separators
    for sep in [' & ', ' and ', '/', ', ', '+']:
        if sep in niche_lower:
            parts = [p.strip() for p in niche_lower.split(sep) if p.strip()]
            result.extend(parts)
            break

    return list(set(result))  # Dedupe


# Shared related-niche map for For You + matched count.
# Keep relationships tight — lifestyle must NOT auto-pull luxury fashion.
FOR_YOU_RELATED_NICHES = {
    # Beauty/skincare and wellness/supplements are separate lanes (Nov 2026: wellness creators got skincare).
    'beauty': ['skincare', 'makeup'],
    'skincare': ['beauty', 'makeup'],
    'makeup': ['beauty', 'skincare'],
    'haircare': ['beauty'],
    # Fashion stays within style/apparel — not parenting/lifestyle mom finds
    'fashion': ['accessories', 'activewear', 'athleisure'],
    'luxury': ['fashion', 'accessories'],
    # Lifestyle/parenting: adjacent product finds — NOT specialty baby carriers by default
    'lifestyle': ['home', 'food', 'wellness', 'beauty', 'skincare'],
    'parenting': ['family', 'home', 'lifestyle', 'beauty', 'skincare', 'wellness'],
    'baby': ['parenting', 'kids', 'family', 'home', 'lifestyle'],
    'kids': ['parenting', 'baby', 'family', 'home', 'lifestyle'],
    'family': ['parenting', 'home', 'lifestyle', 'food', 'beauty'],
    'mom': ['parenting', 'family', 'home', 'lifestyle', 'beauty', 'skincare'],
    'fitness': ['athleisure', 'activewear', 'sports'],  # NOT wellness
    'activewear': ['fitness', 'athleisure', 'sports'],
    'athleisure': ['fitness', 'activewear', 'sports'],
    'sports': ['fitness', 'activewear', 'athleisure'],
    'wellness': ['supplements', 'self-care'],  # NOT fitness, NOT skincare
    'supplements': ['wellness', 'self-care'],
    'food': ['lifestyle', 'kitchen', 'beverages', 'food & beverage', 'home'],
    'food & beverage': ['food', 'beverages', 'lifestyle', 'kitchen'],
    'beverages': ['food', 'food & beverage', 'lifestyle'],
    'tech': ['gaming', 'gadgets', 'electronics'],
    'gadgets': ['tech', 'gaming', 'electronics'],
    'gaming': ['tech', 'entertainment'],
    'home': ['lifestyle', 'decor', 'kitchen', 'food'],
}


# Mentor tiers for For You:
# - Keep Good Match + Growth Match (unlock still useful: Pitch + tips)
# - Drop Stretch / Not Recommended (Theory EU-style mismatches)
FOR_YOU_MIN_FIT_SCORE = 35  # growth_match floor
FOR_YOU_DEEP_LIMIT = 30  # ?pool=autopilot: Autopilot's pipeline pool
FOR_YOU_EXCLUDE_TIERS = frozenset({'stretch_match', 'not_recommended'})
# Hard-block categories that are never right for parenting/mom-finds creators
FOR_YOU_PARENTING_BLOCKED_CATEGORIES = frozenset({
    'fashion', 'luxury', 'apparel', 'clothing', 'streetwear',
    'cbd', 'cannabis', 'hemp',
})
_PARENTING_BLOCKED_NAME_RE = re.compile(
    r'\b(cbd|thc|cannabis|hemp|delta-?8|delta-?9)\b',
    re.I,
)
_OPTICAL_BRAND_RE = re.compile(
    r'\b(glasses|eyewear|optical|spectacles|frames|contact lenses?|eye care|eyecare)\b',
    re.I,
)
_WIG_BRAND_RE = re.compile(
    r'wig|hairpieces?|hair pieces?|hair extensions?|lace front|glueless',
    re.I,
)
_HAIR_CREATOR_RE = re.compile(
    r'\b(hair|haircare|wig|wigs|curl|curly|scalp|blowout|extensions?)\b',
    re.I,
)

# Onboarding niches that should drive For You when scrape is vague (lifestyle/home)
CONCRETE_FOR_YOU_NICHES = frozenset({
    'beauty', 'skincare', 'makeup', 'haircare', 'cosmetics',
    'parenting', 'baby', 'kids', 'family', 'mom', 'mum', 'maternity',
    'fitness', 'food', 'fashion', 'tech', 'gaming', 'pet', 'wellness',
})


def _creator_is_parenting_focused(niches, profile=None) -> bool:
    tokens = set()
    for n in (niches or []):
        tokens.update(normalize_niche(n))
    if profile:
        for key in ('primary_niche',):
            val = profile.get(key)
            if val:
                tokens.update(normalize_niche(str(val)))
        for n in (profile.get('secondary_niches') or []):
            tokens.update(normalize_niche(str(n)))
    parenting_markers = {
        'parenting', 'baby', 'kids', 'family', 'mom', 'mum', 'dad', 'maternity', 'toddler',
    }
    return bool(tokens & parenting_markers)


# Scrape labels that are too vague to drive For You lane matching
WEAK_SCRAPE_NICHES = frozenset({
    'other', 'unknown', 'general', 'misc', 'miscellaneous', 'n/a', 'na', 'none', '',
})
VAGUE_SCRAPE_WHEN_INTERESTS = frozenset({'lifestyle', 'home'}) | WEAK_SCRAPE_NICHES

# Prefer concrete creator niches when inferring from interests/themes
_INFER_PRIMARY_PREFERRED = [
    'haircare', 'skincare', 'beauty', 'makeup', 'fitness', 'activewear',
    'wellness', 'fashion', 'food', 'parenting', 'baby', 'home', 'lifestyle',
]
_NOISY_SCRAPE_NICHES = frozenset({
    'home decor', 'organization', 'cooking', 'ugc_creator', 'ugc',
    'content creator', 'creator', 'other', 'unknown', 'general',
})


def _minimal_fit_profile_from_niches(niches, followers=0):
    """Build a calculator-compatible profile when scrape data is missing."""
    niche_list = []
    for n in (niches or []):
        niche_list.extend(normalize_niche(n))
    # Prefer a concrete primary (parenting over compound blobs)
    preferred = _INFER_PRIMARY_PREFERRED + ['family', 'food', 'home']
    primary = next((p for p in preferred if p in niche_list), None)
    if not primary:
        primary = niche_list[0] if niche_list else 'lifestyle'
    secondary = [n for n in niche_list if n != primary]
    return {
        'primary_niche': primary,
        'secondary_niches': secondary,
        'content_themes': niche_list + ['finds', 'product', 'routine'],
        'engagement_rate': 2.5,
        'posting_cadence_per_week': 3,
        'latest_post_days_ago': 7,
        'follower_count': followers or 0,
        'has_collab_email': False,
        'aesthetic_descriptors': [],
    }


def _infer_primary_from_signals(profile, niches=None):
    """
    When scrape primary is 'other'/weak, infer a usable primary from
    onboarding interests first. Themes must not steal the lane (e.g. skincare
    in posts should not replace Beauty + Baby).
    """
    interest_tokens = set()
    for n in (niches or []):
        interest_tokens.update(t.lower().strip() for t in normalize_niche(n) if t)
    concrete = interest_tokens & CONCRETE_FOR_YOU_NICHES
    if concrete:
        for pref in _INFER_PRIMARY_PREFERRED:
            if pref in concrete:
                return pref
        return next(iter(concrete))

    tokens = []
    for s in (profile.get('secondary_niches') or []):
        tokens.extend(normalize_niche(str(s)))
    themes = profile.get('content_themes') or []
    if isinstance(themes, str):
        try:
            import json as _json
            themes = _json.loads(themes)
        except Exception:
            themes = [themes]
    for t in themes:
        tokens.extend(normalize_niche(str(t)))

    token_set = {t.lower().strip() for t in tokens if t}
    if not token_set:
        return None

    for pref in _INFER_PRIMARY_PREFERRED:
        if pref in token_set:
            return pref
    return next(iter(token_set), None)


def _has_concrete_interests(niches) -> bool:
    tokens = set()
    for n in (niches or []):
        tokens.update(normalize_niche(n))
    return bool(tokens & CONCRETE_FOR_YOU_NICHES)


def _apply_inferred_primary(profile, inferred, niches, keep_scrape_primary=None):
    secondary = []
    for n in (niches or []):
        for part in normalize_niche(n):
            if part != inferred and part not in secondary:
                secondary.append(part)
    if keep_scrape_primary:
        for part in normalize_niche(str(keep_scrape_primary)):
            if (
                part != inferred
                and part not in secondary
                and part not in _NOISY_SCRAPE_NICHES
            ):
                secondary.append(part)
    profile['primary_niche'] = inferred
    profile['secondary_niches'] = secondary
    return profile


def _merge_interest_secondaries(profile, niches):
    """Keep onboarding niches on the profile so adjacency includes Beauty + Parenting."""
    primary = str(profile.get('primary_niche') or '').lower().strip()
    secondary = []
    for n in (niches or []):
        for part in normalize_niche(n):
            if part != primary and part not in secondary:
                secondary.append(part)
    for s in (profile.get('secondary_niches') or []):
        for part in normalize_niche(str(s)):
            if (
                part != primary
                and part not in secondary
                and part not in _NOISY_SCRAPE_NICHES
            ):
                secondary.append(part)
    profile['secondary_niches'] = secondary
    return profile


def _prepare_for_you_profile(creator_profile_dict, niches=None, followers=0):
    """
    Scoring profile blends scrape (content proof) with onboarding (pitch intent).
    When scrape is vague OR conflicts with concrete interests (fashion scrape vs
    Beauty/Wellness), interests win as primary so cards match what the creator
    actually wants to pitch.
    """
    if not creator_profile_dict or not creator_profile_dict.get('primary_niche'):
        profile = _minimal_fit_profile_from_niches(niches, followers)
        return _attach_match_lanes(profile, niches, scrape_primary=None)

    profile = dict(creator_profile_dict)
    primary = str(profile.get('primary_niche') or '').lower().strip()
    scrape_primary = primary
    confidence = int(profile.get('primary_niche_confidence') or 0)
    concrete = _has_concrete_interests(niches)
    agrees = True
    if scrape_agrees_with_intent:
        agrees = scrape_agrees_with_intent(primary, list(niches or []))
    vague_vs_interests = concrete and primary in VAGUE_SCRAPE_WHEN_INTERESTS
    conflict_vs_interests = concrete and not agrees and primary not in WEAK_SCRAPE_NICHES

    if (
        primary in WEAK_SCRAPE_NICHES
        or confidence < 40
        or vague_vs_interests
        or conflict_vs_interests
    ):
        inferred = _infer_primary_from_signals(profile, niches)
        if inferred and inferred not in VAGUE_SCRAPE_WHEN_INTERESTS:
            reason = 'conflict' if conflict_vs_interests else 'vague'
            print(
                f"[ForYou] Scrape niche={primary!r} (conf={confidence}, {reason}) "
                f"-> inferred primary={inferred!r} from interests/themes"
            )
            profile = _apply_inferred_primary(
                profile, inferred, niches, keep_scrape_primary=scrape_primary
            )
        elif primary in WEAK_SCRAPE_NICHES or confidence < 40:
            fallback = _minimal_fit_profile_from_niches(niches, followers or profile.get('follower_count') or 0)
            for key in ('follower_count', 'engagement_rate', 'posting_cadence_per_week',
                        'raw_bio', 'handle', 'aesthetic', 'content_themes'):
                if profile.get(key) is not None:
                    fallback[key] = profile.get(key)
            return _attach_match_lanes(fallback, niches, scrape_primary=scrape_primary)

    profile = _merge_interest_secondaries(profile, niches)
    if followers and not profile.get('follower_count'):
        profile['follower_count'] = followers
    return _attach_match_lanes(profile, niches, scrape_primary=scrape_primary)


def _attach_match_lanes(profile, niches, scrape_primary=None):
    intent = []
    for n in (niches or []):
        for part in normalize_niche(n):
            if part and part not in intent and part not in WEAK_SCRAPE_NICHES:
                intent.append(part)
    proof = []
    if scrape_primary:
        for part in normalize_niche(str(scrape_primary)):
            if part and part not in proof and part not in WEAK_SCRAPE_NICHES:
                proof.append(part)
    for s in (profile.get('secondary_niches') or []):
        for part in normalize_niche(str(s)):
            if part and part not in proof and part not in WEAK_SCRAPE_NICHES:
                proof.append(part)
    profile['match_intent_lanes'] = intent
    profile['match_proof_lanes'] = proof
    return profile


def _build_for_you_category_pool(scrape_profile, interest_niches):
    """
    Candidate SQL categories:
    - Core: onboarding interests when they are concrete (Beauty, Parenting, etc.)
    - Soft: scrape lane, only when scrape agrees with interests
    Conflicting scrape (fashion vs Beauty/Wellness) must not explode the pool
    into apparel. Vague lifestyle interests must not pull coffee/food when
    concrete interests already exist.
    """
    related = FOR_YOU_RELATED_NICHES
    core = set()
    soft = set()
    concrete = _has_concrete_interests(interest_niches)

    for n in (interest_niches or []):
        for part in normalize_niche(n):
            if part in WEAK_SCRAPE_NICHES:
                continue
            core.add(part)
            if part in VAGUE_SCRAPE_WHEN_INTERESTS and concrete:
                continue
            core.update(related.get(part, []))

    scrape_vague = True
    scrape_conflicts = False
    if scrape_profile and scrape_profile.get('primary_niche'):
        primary = str(scrape_profile.get('primary_niche')).lower().strip()
        scrape_vague = (
            primary in WEAK_SCRAPE_NICHES
            or (primary in VAGUE_SCRAPE_WHEN_INTERESTS and bool(core))
        )
        if core and scrape_agrees_with_intent:
            scrape_conflicts = not scrape_agrees_with_intent(primary, list(interest_niches or []))
        if not scrape_vague and not scrape_conflicts:
            for part in normalize_niche(primary):
                soft.add(part)
                soft.update(related.get(part, []))
            for s in (scrape_profile.get('secondary_niches') or []):
                for part in normalize_niche(str(s)):
                    if part in WEAK_SCRAPE_NICHES:
                        continue
                    soft.add(part)
                    soft.update(related.get(part, []))
        elif scrape_conflicts:
            for part in normalize_niche(primary):
                if part not in WEAK_SCRAPE_NICHES:
                    soft.add(part)

    if core:
        labels = core if scrape_vague else (core | soft)
        expanded = set(labels)
        for part in list(labels):
            if part in ('baby', 'parenting', 'kids', 'family', 'maternity'):
                expanded.update({
                    'baby & parenting', 'baby and parenting', 'parenting & baby',
                    'mom & baby', 'beauty & parenting',
                })
        return list(expanded)
    return list(soft)


def _roster_category_pool(scrape_profile, interest_niches):
    """Live rosters jump the queue, so they must fit the niche the creator picked, not a scrape lane."""
    if _has_concrete_interests(interest_niches):
        pool = _build_for_you_category_pool(None, interest_niches)
    else:
        pool = _build_for_you_category_pool(scrape_profile, interest_niches)
    return {str(c).lower() for c in (pool or [])}


def _creator_is_eyewear_focused(niches, profile=None) -> bool:
    blob = ' '.join(str(n) for n in (niches or []))
    if profile:
        blob += ' ' + str(profile.get('primary_niche') or '')
        blob += ' ' + str(profile.get('raw_bio') or '')
        themes = profile.get('content_themes') or []
        if isinstance(themes, (list, tuple)):
            blob += ' ' + ' '.join(str(t) for t in themes)
    return bool(re.search(r'\b(glasses|eyewear|optical|frames|vision|spectacles)\b', blob, re.I))


def _creator_is_hair_focused(niches, profile=None) -> bool:
    blob = ' '.join(str(n) for n in (niches or []))
    if profile:
        blob += ' ' + str(profile.get('primary_niche') or '')
        blob += ' ' + ' '.join(str(s) for s in (profile.get('secondary_niches') or []))
        blob += ' ' + str(profile.get('raw_bio') or '')
        themes = profile.get('content_themes') or []
        if isinstance(themes, (list, tuple)):
            blob += ' ' + ' '.join(str(t) for t in themes)
    return bool(_HAIR_CREATOR_RE.search(blob))


def _for_you_should_skip_brand(brand, niches, profile=None) -> bool:
    """Drop fashion/CBD for parenting creators, optical, wig/haircare, and gender-mismatched brands."""
    cat = (brand.get('category') or '').lower().strip()
    name = ' '.join(str(brand.get(k) or '') for k in ('name', 'brand_name', 'description', 'hero_product'))
    if _creator_is_parenting_focused(niches, profile):
        if cat in FOR_YOU_PARENTING_BLOCKED_CATEGORIES:
            return True
        if _PARENTING_BLOCKED_NAME_RE.search(name):
            return True
    if _OPTICAL_BRAND_RE.search(name) and not _creator_is_eyewear_focused(niches, profile):
        return True
    hair_focused = _creator_is_hair_focused(niches, profile)
    if cat == 'haircare' and not hair_focused:
        return True
    if _WIG_BRAND_RE.search(name) and not hair_focused:
        return True
    try:
        from services.audience_fit import audience_mismatch
        if audience_mismatch(brand, profile, niches):
            return True
    except Exception:
        pass
    return False
