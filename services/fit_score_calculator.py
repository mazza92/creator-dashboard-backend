"""
Deterministic Fit Score Calculator

This module calculates brand-creator fit scores BEFORE passing to LLM.
The LLM only explains - it never decides the score.

Architecture:
1. Brand DNA profiles define what each brand/category needs
2. Creator profile vectors are extracted from scraped data
3. Weighted scoring compares creator vs brand requirements
4. Score is passed to Gemini for explanation only

Key insight: LLMs hate binary decisions and converge to middle.
Don't let them decide. Calculate deterministically, then explain.
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
        'niche_keywords': ['home', 'kitchen', 'cooking', 'food', 'recipe', 'interior', 'decor', 'organization'],
        'content_keywords': ['kitchen', 'cook', 'recipe', 'home', 'house', 'meal', 'prep', 'organize'],
        'deal_breaker': 'no_home_content',
    },
    'beauty': {
        'required_signals': ['beauty', 'skincare', 'makeup', 'cosmetics', 'routine'],
        'weights': {
            'niche_match': 0.30,
            'content_proof': 0.30,
            'engagement': 0.20,
            'consistency': 0.20,
        },
        'niche_keywords': ['beauty', 'skincare', 'makeup', 'cosmetics', 'skin', 'face', 'glow'],
        'content_keywords': ['skincare', 'makeup', 'routine', 'product', 'beauty', 'skin', 'glow', 'serum'],
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
        'niche_keywords': ['hair', 'haircare', 'styling', 'beauty'],
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
        'niche_keywords': ['wellness', 'health', 'fitness', 'nutrition', 'mindful', 'lifestyle'],
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
            'niche_match': 0.30,
            'content_proof': 0.30,
            'engagement': 0.20,
            'consistency': 0.20,
        },
        'niche_keywords': ['fashion', 'style', 'outfit', 'clothing', 'ootd'],
        'content_keywords': ['outfit', 'style', 'fashion', 'wear', 'look', 'fit', 'haul'],
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
        'niche_keywords': ['lifestyle', 'daily', 'life', 'routine', 'day'],
        'content_keywords': ['day', 'life', 'routine', 'lifestyle', 'vlog'],
        'deal_breaker': None,  # Lifestyle is very broad
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
    return CATEGORY_DNA.get(category_lower, DEFAULT_DNA)


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

    # Check brand readiness signals
    brand_readiness = creator_profile.get('brand_readiness_signals', {})
    if isinstance(brand_readiness, str):
        try:
            brand_readiness = json.loads(brand_readiness)
        except:
            brand_readiness = {}

    content_keywords = brand_dna.get('content_keywords', [])
    all_content = ' '.join(content_themes + aesthetic_descriptors).lower()

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

    all_content = ' '.join(content_themes + aesthetic_descriptors).lower()
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
        else:
            return True, f"No {category} content found"

    return False, ''


def calculate_fit_score(creator_profile: Dict, brand_category: str) -> Dict:
    """
    Calculate deterministic fit score for creator vs brand category.

    Returns:
        Dict with:
        - overall_score: 0-100
        - sub_scores: {niche, content, engagement, consistency}
        - tier: top_match/good_match/growth_match/stretch_match/not_recommended
        - status: ready/almost/not_yet/poor_fit/build_first
        - stars: 1-5
        - label: human-readable tier name
        - deal_breaker: optional reason why score is capped
        - missing: list of what's missing
    """
    category = (brand_category or '').lower().strip()
    brand_dna = get_brand_dna(category)
    weights = brand_dna['weights']

    # Calculate sub-scores
    niche_score = calculate_niche_score(creator_profile, brand_dna)
    content_score = calculate_content_proof_score(creator_profile, brand_dna)
    engagement_score = calculate_engagement_score(creator_profile)
    consistency_score = calculate_consistency_score(creator_profile)

    # Check for deal breakers
    has_deal_breaker, deal_breaker_reason = check_deal_breaker(creator_profile, brand_dna, category)

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
