"""
Deterministic Fit Score Calculator

Unlock coaching uses this BEFORE Gemini explains (LLM never decides unlock score).
For You uses mentor_matchmaker (Gemini ranks with scraped social data); this
calculator prefilters candidates and serves as fallback + brand-context gates
(e.g. lifestyle-labeled luxury handbags vs affordable mom finds).
"""

from typing import Dict, List, Tuple, Optional
import json


# ============================================
# BRAND DNA PROFILES
# Each category has requirements and weights
# ============================================

CATEGORY_DNA = {
    'pet': {
        'required_signals': ['pets', 'dogs', 'cats', 'animals', 'pet care', 'pet owner'],
        'weights': {
            'niche_match': 0.40,      # Does creator post about pets?
            'content_proof': 0.30,    # Do they show pets in posts?
            'engagement': 0.15,       # Good engagement rate?
            'consistency': 0.15,      # Post regularly?
        },
        'niche_keywords': ['pet', 'dog', 'cat', 'animal', 'puppy', 'kitten', 'vet', 'paw'],
        'content_keywords': ['pet', 'dog', 'cat', 'animal', 'fur', 'walk', 'treat', 'toy'],
        'deal_breaker': 'no_pet_content',  # If no pet content, max score is 25%
    },
    'home': {
        'required_signals': ['home', 'kitchen', 'cooking', 'interior', 'decor', 'organization'],
        'weights': {
            'niche_match': 0.35,
            'content_proof': 0.35,
            'engagement': 0.15,
            'consistency': 0.15,
        },
        'niche_keywords': ['home', 'kitchen', 'cooking', 'food', 'recipe', 'interior', 'decor',
                           'organization', 'parenting', 'mom', 'family', 'lifestyle'],
        'content_keywords': ['kitchen', 'cook', 'recipe', 'home', 'house', 'meal', 'prep', 'organize', 'finds'],
        'deal_breaker': 'no_home_content',
    },
    'food': {
        'required_signals': ['food', 'cooking', 'recipe', 'kitchen', 'beverage'],
        'weights': {
            'niche_match': 0.35,
            'content_proof': 0.30,
            'engagement': 0.20,
            'consistency': 0.15,
        },
        'niche_keywords': ['food', 'cooking', 'recipe', 'kitchen', 'beverage', 'coffee',
                           'parenting', 'mom', 'family', 'lifestyle', 'home'],
        'content_keywords': ['recipe', 'cook', 'food', 'meal', 'eat', 'taste', 'kitchen', 'coffee'],
        'deal_breaker': None,
    },
    'beauty': {
        'required_signals': ['beauty', 'skincare', 'makeup', 'cosmetics', 'routine'],
        'weights': {
            'niche_match': 0.30,
            'content_proof': 0.30,
            'engagement': 0.20,
            'consistency': 0.20,
        },
        # parenting/lifestyle moms often do product finds — adjacent, not stretch
        'niche_keywords': ['beauty', 'skincare', 'makeup', 'cosmetics', 'skin', 'face', 'glow',
                          'parenting', 'mom', 'family', 'baby', 'lifestyle'],
        'content_keywords': ['skincare', 'makeup', 'routine', 'product', 'beauty', 'skin', 'glow', 'serum', 'finds'],
        'deal_breaker': None,  # Beauty is broad, less strict
    },
    'haircare': {
        'required_signals': ['hair', 'haircare', 'styling', 'color', 'treatment'],
        'weights': {
            'niche_match': 0.35,
            'content_proof': 0.35,
            'engagement': 0.15,
            'consistency': 0.15,
        },
        'niche_keywords': ['hair', 'haircare', 'styling', 'beauty', 'parenting', 'mom', 'lifestyle'],
        'content_keywords': ['hair', 'wash', 'style', 'color', 'treatment', 'shine', 'curl', 'straight'],
        'deal_breaker': 'no_hair_content',
    },
    'wellness': {
        'required_signals': ['wellness', 'health', 'fitness', 'nutrition', 'mindfulness'],
        'weights': {
            'niche_match': 0.30,
            'content_proof': 0.25,
            'engagement': 0.25,
            'consistency': 0.20,
        },
        'niche_keywords': ['wellness', 'health', 'fitness', 'nutrition', 'mindful', 'lifestyle',
                           'parenting', 'mom', 'family'],
        'content_keywords': ['health', 'workout', 'nutrition', 'wellness', 'mindful', 'routine', 'morning'],
        'deal_breaker': None,
    },
    'tech': {
        'required_signals': ['tech', 'gadgets', 'gaming', 'setup', 'productivity'],
        'weights': {
            'niche_match': 0.40,
            'content_proof': 0.30,
            'engagement': 0.15,
            'consistency': 0.15,
        },
        'niche_keywords': ['tech', 'gaming', 'gadget', 'setup', 'productivity', 'software', 'hardware'],
        'content_keywords': ['tech', 'game', 'setup', 'desk', 'gadget', 'review', 'unbox'],
        'deal_breaker': 'no_tech_content',
    },
    'fashion': {
        'required_signals': ['fashion', 'style', 'outfit', 'clothing', 'accessories'],
        'weights': {
            'niche_match': 0.35,
            'content_proof': 0.35,
            'engagement': 0.15,
            'consistency': 0.15,
        },
        # Strict — do NOT include parenting/lifestyle (avoids Theory EU for mom-finds creators)
        'niche_keywords': ['fashion', 'style', 'outfit', 'clothing', 'ootd', 'luxury', 'runway'],
        'content_keywords': ['outfit', 'style', 'fashion', 'wear', 'look', 'ootd', 'wardrobe', 'tailored'],
        'deal_breaker': None,
    },
    'lifestyle': {
        'required_signals': ['lifestyle', 'daily', 'routine', 'life', 'personal'],
        'weights': {
            'niche_match': 0.25,
            'content_proof': 0.25,
            'engagement': 0.25,
            'consistency': 0.25,
        },
        'niche_keywords': ['lifestyle', 'daily', 'life', 'routine', 'day', 'parenting', 'mom', 'family', 'baby'],
        'content_keywords': ['day', 'life', 'routine', 'lifestyle', 'vlog', 'finds', 'mom'],
        'deal_breaker': None,  # Lifestyle is very broad
    },
    # Dedicated baby DNA — signup "parenting" niche alone is NOT enough without baby content
    'baby': {
        'required_signals': ['baby', 'parenting', 'newborn', 'toddler', 'babywear'],
        'weights': {
            'niche_match': 0.30,
            'content_proof': 0.45,
            'engagement': 0.15,
            'consistency': 0.10,
        },
        'niche_keywords': ['baby', 'parenting', 'mom', 'mum', 'family', 'kids', 'maternity', 'toddler'],
        'content_keywords': [
            'baby', 'newborn', 'toddler', 'carrier', 'babywear', 'diaper', 'nursery',
            'stroller', 'parenting', 'breastfeed', 'postpartum', 'kids',
        ],
        'deal_breaker': 'no_baby_content',
    },
}

# Default DNA for unknown categories
DEFAULT_DNA = {
    'required_signals': [],
    'weights': {
        'niche_match': 0.30,
        'content_proof': 0.30,
        'engagement': 0.20,
        'consistency': 0.20,
    },
    'niche_keywords': [],
    'content_keywords': [],
    'deal_breaker': None,
}


# ============================================
# SCORE THRESHOLDS
# ============================================

SCORE_TIERS = {
    'top_match': {'min': 75, 'status': 'ready', 'stars': 5, 'label': 'Top Match'},
    'good_match': {'min': 55, 'status': 'almost', 'stars': 4, 'label': 'Good Match'},
    'growth_match': {'min': 35, 'status': 'not_yet', 'stars': 3, 'label': 'Growth Match'},
    'stretch_match': {'min': 20, 'status': 'poor_fit', 'stars': 2, 'label': 'Stretch Match'},
    'not_recommended': {'min': 0, 'status': 'build_first', 'stars': 1, 'label': 'Not Recommended'},
}


def get_brand_dna(category: str) -> Dict:
    """Get brand DNA profile for a category."""
    category_lower = (category or '').lower().strip()
    # Map lookalike labels onto DNA we actually score (avoids soft DEFAULT 50/50)
    aliases = {
        'luxury': 'fashion',
        'apparel': 'fashion',
        'clothing': 'fashion',
        'streetwear': 'fashion',
        'activewear': 'fitness',
        'athleisure': 'fitness',
        'makeup': 'beauty',
        'skincare': 'beauty',
        'haircare': 'beauty',
        'beverage': 'food',
        'beverages': 'food',
        'food & beverage': 'food',
        # Parenting/family soft → lifestyle; baby specialty keeps strict baby DNA
        'parenting': 'lifestyle',
        'family': 'lifestyle',
        'kids': 'baby',
        'maternity': 'baby',
        'babywearing': 'baby',
    }
    mapped = aliases.get(category_lower, category_lower)
    return CATEGORY_DNA.get(mapped, CATEGORY_DNA.get(category_lower, DEFAULT_DNA))


def calculate_niche_score(creator_profile: Dict, brand_dna: Dict) -> float:
    """
    Calculate niche match score (0-100).
    Does the creator's niche align with brand requirements?
    """
    creator_niche = (creator_profile.get('primary_niche') or '').lower()
    secondary_niches = creator_profile.get('secondary_niches', []) or []
    if isinstance(secondary_niches, str):
        try:
            secondary_niches = json.loads(secondary_niches)
        except:
            secondary_niches = []

    all_niches = [creator_niche] + [n.lower() for n in secondary_niches if n]
    niche_keywords = brand_dna.get('niche_keywords', [])

    if not niche_keywords:
        return 50  # No requirements = neutral score

    # Check for keyword matches in creator's niches
    matches = 0
    for niche in all_niches:
        for keyword in niche_keywords:
            if keyword in niche or niche in keyword:
                matches += 1
                break

    # Calculate score based on matches
    if matches >= 2:
        return 100
    elif matches == 1:
        return 70
    else:
        # Check if niche is completely unrelated
        return 10  # Low but not zero


def calculate_content_proof_score(creator_profile: Dict, brand_dna: Dict) -> float:
    """
    Calculate content proof score (0-100).
    Does creator's content show relevant products/themes?
    """
    # Parse content signals from profile
    content_themes = creator_profile.get('content_themes', []) or []
    if isinstance(content_themes, str):
        try:
            content_themes = json.loads(content_themes)
        except:
            content_themes = []

    aesthetic_descriptors = creator_profile.get('aesthetic_descriptors', []) or []
    if isinstance(aesthetic_descriptors, str):
        try:
            aesthetic_descriptors = json.loads(aesthetic_descriptors)
        except:
            aesthetic_descriptors = []
    # Scraper nests descriptors under aesthetic.aesthetic_descriptors
    if not aesthetic_descriptors:
        aesthetic = creator_profile.get('aesthetic') or {}
        if isinstance(aesthetic, dict):
            nested = aesthetic.get('aesthetic_descriptors', []) or []
            if isinstance(nested, str):
                try:
                    nested = json.loads(nested)
                except:
                    nested = []
            aesthetic_descriptors = nested

    # Check brand readiness signals
    brand_readiness = creator_profile.get('brand_readiness_signals', {})
    if isinstance(brand_readiness, str):
        try:
            brand_readiness = json.loads(brand_readiness)
        except:
            brand_readiness = {}

    content_keywords = brand_dna.get('content_keywords', [])
    all_content = ' '.join(
        [str(t) for t in content_themes]
        + [str(a) for a in aesthetic_descriptors]
        + [creator_profile.get('raw_bio') or '']
    ).lower()

    if not content_keywords:
        return 50

    # Count keyword matches in content
    matches = sum(1 for kw in content_keywords if kw.lower() in all_content)
    match_ratio = matches / len(content_keywords)

    # Bonus for showing products
    shows_products = brand_readiness.get('shows_products_in_use', False)
    features_brands = brand_readiness.get('already_features_brands', False)

    base_score = min(match_ratio * 100, 80)  # Cap at 80 from keywords

    if shows_products:
        base_score += 10
    if features_brands:
        base_score += 10

    return min(base_score, 100)


def calculate_engagement_score(creator_profile: Dict) -> float:
    """
    Calculate engagement score (0-100).
    Based on engagement rate benchmarks.
    """
    engagement_rate = creator_profile.get('engagement_rate', 0) or 0
    if isinstance(engagement_rate, str):
        try:
            engagement_rate = float(engagement_rate)
        except:
            engagement_rate = 0

    # Engagement benchmarks (Instagram)
    # >6% = excellent, 3-6% = good, 1-3% = average, <1% = low
    if engagement_rate >= 6:
        return 100
    elif engagement_rate >= 4:
        return 85
    elif engagement_rate >= 2:
        return 70
    elif engagement_rate >= 1:
        return 50
    else:
        return 30


def calculate_consistency_score(creator_profile: Dict) -> float:
    """
    Calculate posting consistency score (0-100).
    """
    posting_cadence = creator_profile.get('posting_cadence_per_week', 0) or 0
    has_email = creator_profile.get('has_collab_email', False)

    # Posting frequency scoring
    if posting_cadence >= 5:
        base_score = 100
    elif posting_cadence >= 3:
        base_score = 80
    elif posting_cadence >= 1:
        base_score = 60
    else:
        base_score = 30

    # Bonus for collab email
    if has_email:
        base_score = min(base_score + 15, 100)

    return base_score


def check_deal_breaker(creator_profile: Dict, brand_dna: Dict, category: str) -> Tuple[bool, str]:
    """
    Check if there's a deal breaker that caps the score.
    Returns (has_deal_breaker, reason)
    """
    deal_breaker = brand_dna.get('deal_breaker')
    if not deal_breaker:
        return False, ''

    content_themes = creator_profile.get('content_themes', []) or []
    if isinstance(content_themes, str):
        try:
            content_themes = json.loads(content_themes)
        except:
            content_themes = []

    aesthetic_descriptors = creator_profile.get('aesthetic_descriptors', []) or []
    if isinstance(aesthetic_descriptors, str):
        try:
            aesthetic_descriptors = json.loads(aesthetic_descriptors)
        except:
            aesthetic_descriptors = []

    all_content = ' '.join(
        [str(t) for t in content_themes]
        + [str(a) for a in aesthetic_descriptors]
        + [creator_profile.get('raw_bio') or '']
    ).lower()
    content_keywords = brand_dna.get('content_keywords', [])

    # Check if ANY content keywords appear
    has_relevant_content = any(kw.lower() in all_content for kw in content_keywords)

    if not has_relevant_content:
        if category == 'pet':
            return True, "No pet content found in recent posts"
        elif category == 'home':
            return True, "No kitchen or cooking content found"
        elif category == 'tech':
            return True, "No tech or gaming content found"
        elif category == 'haircare':
            return True, "No hair-focused content found"
        elif category == 'baby':
            return True, "No baby/parenting content found in posts"
        else:
            return True, f"No {category} content found"

    return False, ''


# Brand-level signals the category label alone cannot see (e.g. lifestyle → luxury handbags)
LUXURY_BRAND_SIGNALS = (
    'luxury', 'designer', 'haute', 'couture', 'aspirational', 'high-end', 'high end',
    'affluent', 'luxe', 'handbag', 'handbags', 'exclusive', 'atelier',
    'fine jewelry', 'bespoke', 'contemporary luxury',
)
AFFORDABLE_CREATOR_SIGNALS = (
    'amazon', 'walmart', 'tiktok shop', 'budget', 'affordable', 'finds', 'dupe',
    'deals', 'worth buying', 'busy mom', 'busy mum', 'mom finds', 'mum finds',
    'under $', 'cheap', 'haul',
)
PARENTING_CREATOR_SIGNALS = (
    'parenting', 'baby', 'mom', 'mum', 'dad', 'family', 'toddler', 'maternity', 'kids',
)


def _flatten_text(*parts) -> str:
    bits = []
    for p in parts:
        if p is None:
            continue
        if isinstance(p, (list, tuple)):
            bits.extend(str(x) for x in p if x)
        elif isinstance(p, dict):
            bits.append(json.dumps(p, default=str))
        else:
            bits.append(str(p))
    return ' '.join(bits).lower()


def check_brand_context_mismatch(creator_profile: Dict, brand: Optional[Dict]) -> Tuple[bool, str]:
    """
    Catch brand-specific mismatches category DNA misses
    (e.g. BY FAR labeled lifestyle but sells luxury handbags).
    Note: brand.price_point is PR package value, not product luxury — do not use it here.
    """
    if not brand:
        return False, ''

    brand_blob = _flatten_text(
        brand.get('name'),
        brand.get('brand_name'),
        brand.get('description'),
        brand.get('category'),
    )

    aesthetic = creator_profile.get('aesthetic') or {}
    if isinstance(aesthetic, str):
        try:
            aesthetic = json.loads(aesthetic)
        except Exception:
            aesthetic = {}
    creator_blob = _flatten_text(
        creator_profile.get('raw_bio'),
        creator_profile.get('primary_niche'),
        creator_profile.get('secondary_niches'),
        creator_profile.get('content_themes'),
        (aesthetic or {}).get('aesthetic_descriptors') if isinstance(aesthetic, dict) else None,
        creator_profile.get('aesthetic_descriptors'),
    )

    is_luxury_brand = any(s in brand_blob for s in LUXURY_BRAND_SIGNALS)
    is_affordable_creator = any(s in creator_blob for s in AFFORDABLE_CREATOR_SIGNALS)
    is_parenting_creator = any(s in creator_blob for s in PARENTING_CREATOR_SIGNALS)

    if is_luxury_brand and (is_affordable_creator or is_parenting_creator):
        return True, (
            "Brand signals luxury/designer positioning that conflicts with this "
            "creator's affordable / parenting content"
        )

    category = (brand.get('category') or '').lower().strip()
    if category in ('fashion', 'luxury', 'apparel', 'clothing', 'streetwear') and (
        is_affordable_creator or is_parenting_creator
    ):
        return True, "Fashion/luxury category does not fit affordable parenting content"

    return False, ''


# Scraped primary niche adjacency — off-lane brands need content proof or they Stretch
PRIMARY_NICHE_ADJACENCY = {
    'beauty': {'beauty', 'skincare', 'makeup', 'haircare', 'wellness', 'cosmetics'},
    'skincare': {'beauty', 'skincare', 'makeup', 'haircare', 'wellness'},
    'makeup': {'beauty', 'skincare', 'makeup', 'haircare'},
    'haircare': {'beauty', 'skincare', 'makeup', 'haircare'},
    'wellness': {'wellness', 'beauty', 'skincare', 'fitness', 'supplements'},
    'fitness': {'fitness', 'activewear', 'athleisure', 'sports', 'wellness'},
    'fashion': {'fashion', 'accessories', 'activewear', 'athleisure', 'luxury'},
    'lifestyle': {'lifestyle', 'home', 'food', 'wellness', 'beauty'},
    'food': {'food', 'beverage', 'beverages', 'home', 'lifestyle', 'kitchen'},
    'home': {'home', 'lifestyle', 'food', 'kitchen', 'decor'},
    'tech': {'tech', 'gaming', 'gadgets', 'electronics'},
    'gaming': {'gaming', 'tech', 'entertainment'},
    'pet': {'pet'},
    'baby': {'baby', 'parenting', 'kids', 'family', 'maternity'},
    'parenting': {'parenting', 'baby', 'kids', 'family', 'lifestyle', 'home'},
}


def _mapped_category(category: str) -> str:
    category_lower = (category or '').lower().strip()
    aliases = {
        'luxury': 'fashion', 'apparel': 'fashion', 'clothing': 'fashion',
        'streetwear': 'fashion', 'activewear': 'fitness', 'athleisure': 'fitness',
        'makeup': 'beauty', 'skincare': 'beauty', 'haircare': 'beauty',
        'cosmetics': 'beauty', 'beverage': 'food', 'beverages': 'food',
        'food & beverage': 'food', 'parenting': 'lifestyle', 'family': 'lifestyle',
        'kids': 'baby', 'maternity': 'baby', 'babywearing': 'baby',
        # Vague scrape labels — treat as lifestyle so adjacency isn't empty
        'other': 'lifestyle', 'unknown': 'lifestyle', 'general': 'lifestyle',
        'misc': 'lifestyle', 'miscellaneous': 'lifestyle',
    }
    return aliases.get(category_lower, category_lower)


def check_primary_niche_mismatch(
    creator_profile: Dict,
    brand_category: str,
    content_score: float,
) -> Tuple[bool, str]:
    """
    Scraped primary niche is source of truth.
    Off-adjacency brands without real content proof → Stretch (not user-selected niches).
    """
    primary = (creator_profile.get('primary_niche') or '').lower().strip()
    if not primary:
        return False, ''

    mapped_primary = _mapped_category(primary)
    mapped_brand = _mapped_category(brand_category)
    adjacent = PRIMARY_NICHE_ADJACENCY.get(mapped_primary, {mapped_primary})

    # Scrape secondary niches can widen adjacency (not dashboard checkbox niches)
    secondary = creator_profile.get('secondary_niches') or []
    if isinstance(secondary, str):
        try:
            secondary = json.loads(secondary)
        except Exception:
            secondary = []
    for s in secondary:
        s_mapped = _mapped_category(str(s))
        adjacent = adjacent | PRIMARY_NICHE_ADJACENCY.get(s_mapped, {s_mapped})
        adjacent.add(s_mapped)

    if mapped_brand in adjacent:
        return False, ''

    # Off-lane without content proof = stretch
    if content_score < 40:
        return True, (
            f"Brand category '{brand_category}' is outside scraped niche "
            f"'{primary}' without supporting content"
        )
    return False, ''


def calculate_fit_score(
    creator_profile: Dict,
    brand_category: str,
    brand: Optional[Dict] = None,
) -> Dict:
    """
    Calculate deterministic fit score for creator vs brand category.

    Optional `brand` dict (name/description/price_point) applies brand-level
    deal-breakers so lifestyle-labeled luxury brands score as Stretch.

    Scoring uses scraped social profile only — do not pass user checkbox niches
    into secondary_niches or they will inflate off-lane matches.
    """
    category = (brand_category or '').lower().strip()
    if brand and not category:
        category = (brand.get('category') or '').lower().strip()
    brand_dna = get_brand_dna(category)
    weights = brand_dna['weights']

    # Calculate sub-scores
    niche_score = calculate_niche_score(creator_profile, brand_dna)
    content_score = calculate_content_proof_score(creator_profile, brand_dna)
    engagement_score = calculate_engagement_score(creator_profile)
    consistency_score = calculate_consistency_score(creator_profile)

    # Check for deal breakers (category DNA + brand-specific context + primary lane)
    has_deal_breaker, deal_breaker_reason = check_deal_breaker(creator_profile, brand_dna, category)
    brand_mismatch, brand_mismatch_reason = check_brand_context_mismatch(creator_profile, brand)
    if brand_mismatch:
        has_deal_breaker = True
        deal_breaker_reason = brand_mismatch_reason
    primary_mismatch, primary_mismatch_reason = check_primary_niche_mismatch(
        creator_profile, category, content_score
    )
    if primary_mismatch:
        has_deal_breaker = True
        deal_breaker_reason = primary_mismatch_reason

    # Calculate weighted score
    overall_score = (
        niche_score * weights['niche_match'] +
        content_score * weights['content_proof'] +
        engagement_score * weights['engagement'] +
        consistency_score * weights['consistency']
    )

    # Apply deal breaker cap
    if has_deal_breaker:
        overall_score = min(overall_score, 25)  # Cap at 25% for deal breakers

    # Determine tier
    tier_info = None
    for tier_name, tier_data in SCORE_TIERS.items():
        if overall_score >= tier_data['min']:
            tier_info = {
                'tier': tier_name,
                'status': tier_data['status'],
                'stars': tier_data['stars'],
                'label': tier_data['label'],
            }
            break

    if not tier_info:
        tier_info = {
            'tier': 'not_recommended',
            'status': 'build_first',
            'stars': 1,
            'label': 'Not Recommended',
        }

    # Identify what's missing
    missing = []
    if niche_score < 50:
        missing.append(f"niche alignment with {category}")
    if content_score < 50:
        missing.append(f"{category} content in posts")
    if engagement_score < 50:
        missing.append("engagement rate")
    if consistency_score < 50:
        missing.append("posting consistency")

    return {
        'overall_score': round(overall_score),
        'sub_scores': {
            'niche': round(niche_score),
            'content': round(content_score),
            'engagement': round(engagement_score),
            'consistency': round(consistency_score),
        },
        'tier': tier_info['tier'],
        'status': tier_info['status'],
        'stars': tier_info['stars'],
        'label': tier_info['label'],
        'deal_breaker': deal_breaker_reason if has_deal_breaker else None,
        'missing': missing,
        'category': category,
    }


def get_score_context_for_llm(fit_score: Dict, brand_name: str) -> str:
    """
    Generate context string to pass to Gemini.
    Gemini will explain the score, not decide it.
    """
    sub = fit_score['sub_scores']

    context = f"""
=== PRE-COMPUTED FIT ANALYSIS (DO NOT OVERRIDE) ===

Brand: {brand_name}
Category: {fit_score['category']}

OVERALL SCORE: {fit_score['overall_score']}%
VERDICT: {fit_score['label']} ({fit_score['stars']}/5 stars)

SUB-SCORES:
- Niche Alignment: {sub['niche']}%
- Content Proof: {sub['content']}%
- Engagement: {sub['engagement']}%
- Consistency: {sub['consistency']}%
"""

    if fit_score['deal_breaker']:
        context += f"\nDEAL BREAKER: {fit_score['deal_breaker']}\n"

    if fit_score['missing']:
        context += f"\nMISSING: {', '.join(fit_score['missing'])}\n"

    context += f"""
=== YOUR TASK ===
You MUST accept the scores above as fact. Do not recalculate or override.
Your job is to EXPLAIN why the score is what it is, and give specific advice.

If score < 35%: Be honest that this isn't a good match. Suggest the creator focus elsewhere.
If score 35-55%: Acknowledge gaps but provide specific actions to improve.
If score 55-75%: Encourage pitching but note specific improvements.
If score > 75%: Enthusiastically recommend pitching with specific talking points.
"""

    return context
