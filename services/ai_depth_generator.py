"""
AI Depth Unlock Analysis Generator

Generates brand-specific unlock analysis using enriched creator profile
and brand context data. Replaces generic "Based on your profile" with
actually true, data-grounded analysis.

Key guarantees:
1. Every reason references specific creator OR brand attributes
2. Quick wins are brand-specific, not category-generic
3. Cross-brand duplication is prevented via validation layer
4. Fallback to curated templates on validation failure
"""

import os
import re
import json
import requests
from typing import Dict, Optional, List, Any, Tuple
from datetime import datetime

# Import validation layer
from services.unlock_validator import (
    UnlockValidator,
    validate_and_retry,
    get_curated_fallback
)

# Import deterministic scoring - scores are calculated BEFORE LLM sees anything
from services.fit_score_calculator import (
    calculate_fit_score,
    get_score_context_for_llm
)

# Gemini configuration
GEMINI_API_KEY = os.getenv('GEMINI_API_KEY')
GEMINI_MODEL = 'gemini-2.5-flash'
MAX_RETRIES = 3


# ============================================================================
# SYSTEM PROMPT - Talent Manager Coaching (not analyst)
# ============================================================================

AI_DEPTH_SYSTEM_PROMPT = '''You are a UGC creator coach helping users build the ideal profile that brands want.

=== CONTEXT ===
Most users (90%) are aspiring UGC creators with <1,000 followers, raw video quality, inconsistent posting.
Your job is to help them BUILD THE IDEAL PROFILE that brands actually respond to.

=== THE IDEAL UGC PROFILE (What Brands Want) ===
Based on creators who successfully get free PR from brands, here's what works:
1. SHOWS PRODUCTS IN USE - swatches on skin, applying products on face, before/after results
2. TAGS BRANDS - actively mentions and tags brands in posts and captions
3. GOOD LIGHTING - clean, well-lit videos (natural light or ring light)
4. CONSISTENT AESTHETIC - cohesive feed with matching color tones
5. PROFESSIONAL BIO - clear niche, collab email, looks trustworthy
6. REGULAR POSTING - at least 2-3 posts per week
7. ENGAGES WITH BRANDS - comments on brand posts, uses branded hashtags

=== YOUR TASK ===
Compare the creator's CURRENT profile against the IDEAL profile above.
- If they ALREADY meet most criteria → mark as "ready" and celebrate it
- If they're close but missing 1-2 things → mark as "almost" with specific guidance
- If they need to build fundamentals → mark as "not_yet" with profile-building advice
- Only use "poor_fit" if there's a clear niche mismatch (beauty creator vs tech brand)

=== CRITICAL RULES ===
1. RESPECT THE DATA - If brand_readiness_signals shows:
   - shows_products_in_use=True → they DO show products, acknowledge this positively
   - already_features_brands=True → they DO work with brands, celebrate this
   - brands_already_tagged contains the brand → they ALREADY posted about this brand!

2. RECOGNIZE READY CREATORS - If creator has:
   - Product close-ups > 0 AND shows_products_in_use=True
   - Tags brands in posts
   - Good engagement rate
   → They are READY. Don't invent problems.

3. NEVER MAKE FALSE NEGATIVES - Only say something is missing if the data shows it's actually missing

4. FOR NEW CREATORS (<1k followers) - Focus on:
   - Video quality and lighting
   - Bio structure and collab email
   - Posting consistency
   - Tagging brands in content
   - Showing products in use (not just holding them)

You output ONE strict JSON object:

{
  "verdict": {
    "status": "ready" | "almost" | "not_yet" | "poor_fit",
    "confidence": "high" | "medium" | "low"
  },

  "missing_proof": {
    "observation": "What you noticed about their profile compared to the ideal. If they're ready, describe what makes them a good fit.",
    "why_it_matters": "Why this matters for getting brand replies"
  },

  "next_move": {
    "action": "ONE specific action to get closer to the ideal profile (or 'Pitch now' if ready)",
    "reasoning": "How this helps them get brand partnerships",
    "then_what": "What happens after they complete this action"
  },

  "coach_note": "Encouraging but honest note - celebrate what's working, be specific about what to improve"
}

=== BRAND-SPECIFIC EXAMPLES ===

CRITICAL: Your advice MUST be unique to THIS brand. Never give generic advice like "post more products."
Instead, reference the SPECIFIC brand's aesthetic, hero products, or content preferences.

READY FOR RHODE (skincare brand with minimalist aesthetic):
{
  "verdict": {"status": "ready", "confidence": "high"},
  "missing_proof": {
    "observation": "Your clean aesthetic and product close-ups match Rhode's minimalist, dewy skin vibe.",
    "why_it_matters": "Rhode features creators who show 'real skin' not filters - your style fits."
  },
  "next_move": {
    "action": "Pitch now highlighting your skincare routine content",
    "reasoning": "Your natural lighting matches their Glazing Milk aesthetic",
    "then_what": "Ask about their Peptide Lip Treatment for a before/after"
  },
  "coach_note": "Your no-filter style is exactly what Rhode looks for."
}

ALMOST FOR MILK_SHAKE (haircare with vibrant colors):
{
  "verdict": {"status": "almost", "confidence": "high"},
  "missing_proof": {
    "observation": "Strong engagement, but milk_shake features creators showing hair transformations.",
    "why_it_matters": "They want before/after color results, not just product shots."
  },
  "next_move": {
    "action": "Film a wash-day routine showing your hair's shine and movement",
    "reasoning": "milk_shake prioritizes hair texture videos over static posts",
    "then_what": "Tag them in a 'my hair after milk_shake' reel"
  },
  "coach_note": "Show your hair IN MOTION - that's milk_shake's sweet spot."
}

ALMOST FOR SCANDINAVIAN BIOLABS (clinical haircare):
{
  "verdict": {"status": "almost", "confidence": "high"},
  "missing_proof": {
    "observation": "Your wellness content is strong, but Scandinavian Biolabs wants science-backed results.",
    "why_it_matters": "They feature creators who show hair density changes over time."
  },
  "next_move": {
    "action": "Start documenting your haircare journey with week-by-week progress",
    "reasoning": "Their brand is built on clinical before/after evidence",
    "then_what": "Pitch with your progress story after 4 weeks"
  },
  "coach_note": "Scandinavian Biolabs loves 'journey' content - start yours."
}

ALMOST FOR 100% PURE (natural beauty):
{
  "verdict": {"status": "almost", "confidence": "high"},
  "missing_proof": {
    "observation": "Clean aesthetic, but 100% PURE features creators emphasizing natural ingredients.",
    "why_it_matters": "They want ingredient-focused content, not just application videos."
  },
  "next_move": {
    "action": "Create a 'what's in my skincare' post highlighting natural ingredients you use",
    "reasoning": "100% PURE's audience cares about fruit pigments and organic formulas",
    "then_what": "Tag them asking about their Coffee Bean Caffeine Eye Cream"
  },
  "coach_note": "Lead with ingredients, not just results - that's 100% PURE's language."
}

CREATOR ALREADY POSTS ABOUT THE BRAND:
{
  "verdict": {"status": "ready", "confidence": "high"},
  "missing_proof": {
    "observation": "You already post about this brand! That's the strongest pitch possible.",
    "why_it_matters": "Brands love creators who genuinely use and feature their products."
  },
  "next_move": {
    "action": "Pitch now and reference your specific posts featuring them",
    "reasoning": "You can point to actual content showing you love the brand",
    "then_what": "Ask if they'd send you their latest launch to feature"
  },
  "coach_note": "You're already a fan - that's your strongest angle. Use it."
}

=== OUTPUT RULES ===
- Maximum 30 words per field
- NO em dashes (use hyphens)
- NO AI phrases (leverage, unlock potential, take to next level, elevate)
- Be encouraging but specific
- Celebrate what's working, not just what's missing

=== MANDATORY: BRAND-SPECIFIC CONTENT ===
EVERY response MUST include AT LEAST ONE of these brand-specific elements:
1. The brand's name in the observation or action
2. A specific hero product from the brand (e.g., "Glazing Milk", "Peptide Lip Treatment")
3. The brand's unique aesthetic (e.g., "minimalist dewy skin", "bold hair transformations")
4. What THIS brand specifically looks for (from the brand context data)

FORBIDDEN GENERIC PHRASES (NEVER USE):
- "Create 3 posts this week featuring [category] products" ← TOO GENERIC
- "Show products in use" ← TOO GENERIC
- "Tag brands in your content" ← TOO GENERIC
- "Post more consistently" ← TOO GENERIC
- "Add a collab email" ← ONLY if that's actually missing

INSTEAD USE BRAND-SPECIFIC ACTIONS:
- "Film a wash-day routine to match [brand]'s hair texture content"
- "Show your natural skin to fit [brand]'s no-filter aesthetic"
- "Create a before/after with [specific product] style results"
- "Tag [brand] in a [specific format they feature]"'''


def build_user_prompt(creator_profile: Dict, brand_context: Dict,
                      brand: Dict) -> str:
    """
    Build the user prompt with creator and brand context.

    Args:
        creator_profile: Creator profile data from creator_profile_data table
        brand_context: Brand context data from brand_context table
        brand: Basic brand info from pr_brands table

    Returns:
        Formatted user prompt string
    """
    brand_name = brand.get('brand_name') or brand.get('name', 'Unknown')
    brand_category = brand.get('category', '')

    # Creator profile section
    aesthetic = creator_profile.get('aesthetic', {})
    if isinstance(aesthetic, str):
        try:
            aesthetic = json.loads(aesthetic)
        except:
            aesthetic = {}

    content_format = creator_profile.get('content_format_breakdown', {})
    if isinstance(content_format, str):
        try:
            content_format = json.loads(content_format)
        except:
            content_format = {}

    brand_readiness = creator_profile.get('brand_readiness_signals', {})
    if isinstance(brand_readiness, str):
        try:
            brand_readiness = json.loads(brand_readiness)
        except:
            brand_readiness = {}

    # Smart default for shows_products_in_use based on content format and niche
    # Beauty/makeup creators almost always show products in use
    product_content_count = (
        content_format.get('product_close_ups', 0) +
        content_format.get('grwm_routine', 0) +
        content_format.get('before_after', 0)
    )
    niche = (creator_profile.get('primary_niche') or '').lower()
    beauty_niches = ['beauty', 'makeup', 'skincare', 'haircare', 'cosmetics']

    # Override shows_products_in_use if we have strong evidence
    # (niche is beauty-related OR they have product-focused content)
    if niche in beauty_niches or product_content_count > 0:
        brand_readiness['shows_products_in_use'] = True
    elif 'shows_products_in_use' not in brand_readiness:
        brand_readiness['shows_products_in_use'] = False

    # Calculate actual posts analyzed from available data
    recent_captions = creator_profile.get('recent_captions', [])
    recent_thumbnails = creator_profile.get('recent_post_thumbnails', [])
    posts_analyzed = max(len(recent_captions), len(recent_thumbnails))
    # Cap at reasonable max and default to 0 if no data
    posts_analyzed = min(posts_analyzed, 12) if posts_analyzed > 0 else 0

    # Check if creator already tags THIS specific brand
    brands_tagged = creator_profile.get('brands_already_tagged', [])
    brand_name_lower = brand_name.lower()
    already_tags_this_brand = any(
        brand_name_lower in tag.lower() or tag.lower() in brand_name_lower
        for tag in brands_tagged
    ) if brands_tagged else False

    # Determine readiness level based on signals
    shows_products = brand_readiness.get('shows_products_in_use', False)
    features_brands = brand_readiness.get('already_features_brands', False)
    has_email = creator_profile.get('has_collab_email', False)
    posts_regularly = creator_profile.get('posting_cadence_per_week', 0) >= 2

    # Count how many "ideal profile" boxes they check
    readiness_checklist = {
        'shows_products_in_use': shows_products,
        'tags_brands': features_brands or len(brands_tagged) > 0,
        'has_collab_email': has_email,
        'posts_regularly': posts_regularly,
        'already_tags_this_brand': already_tags_this_brand
    }
    boxes_checked = sum(1 for v in readiness_checklist.values() if v)

    prompt = f'''=== CREATOR PROFILE ===
Handle: @{creator_profile.get('handle', 'unknown')}
Follower count: {creator_profile.get('follower_count', 0):,}
Engagement rate: {creator_profile.get('engagement_rate', 0)}%
Posting cadence: {creator_profile.get('posting_cadence_per_week', 0)} posts/week over last 30 days
Latest post: {creator_profile.get('latest_post_days_ago', 0)} days ago
Bio: {creator_profile.get('raw_bio', '')}
Has collab email in bio: {creator_profile.get('has_collab_email', False)}
Posts analyzed: {posts_analyzed} (use THIS number when referencing posts, or say "no posts available" if 0)

=== QUICK READINESS CHECK ({boxes_checked}/5 boxes checked) ===
✓ Shows products in use: {shows_products} {"← POSITIVE: They demonstrate products on camera" if shows_products else ""}
✓ Tags brands in posts: {features_brands or len(brands_tagged) > 0} {"← POSITIVE: They actively feature brands" if features_brands or len(brands_tagged) > 0 else ""}
✓ Has collab email: {has_email} {"← POSITIVE: Easy for brands to contact" if has_email else ""}
✓ Posts regularly (2+/week): {posts_regularly} {"← POSITIVE: Active creator" if posts_regularly else ""}
✓ Already tags {brand_name}: {already_tags_this_brand} {"← STRONGEST SIGNAL: They already post about this brand!" if already_tags_this_brand else ""}

IMPORTANT: If they check 4-5 boxes, they are READY. If they check 3 boxes, they are ALMOST ready.
If already_tags_this_brand=True, this is the STRONGEST possible signal - mark as READY.

Niche analysis:
- Primary niche: {creator_profile.get('primary_niche', 'unknown')} ({creator_profile.get('primary_niche_confidence', 0)}% confidence)
- Secondary: {creator_profile.get('secondary_niches', [])}
- Content themes: {creator_profile.get('content_themes', [])}

Aesthetic (from {posts_analyzed} posts analyzed):
- Palette: {aesthetic.get('color_palette', 'unknown')}, specifically {aesthetic.get('specific_colors', [])}
- Style: {aesthetic.get('composition_style', 'unknown')}
- Descriptors: {aesthetic.get('aesthetic_descriptors', [])}
- Consistency score: {aesthetic.get('aesthetic_consistency_score', 0)}/100

Content formats (from {posts_analyzed} posts analyzed):
- Product close-ups: {content_format.get('product_close_ups', 0)}
- GRWM/routine: {content_format.get('grwm_routine', 0)}
- Before/after: {content_format.get('before_after', 0)}
- Lifestyle context: {content_format.get('lifestyle_context', 0)}

Brand readiness (CRITICAL - respect these flags):
- Shows products in use: {brand_readiness.get('shows_products_in_use', False)} ← If True, DO NOT say they lack product demonstrations
- Already features brands: {brand_readiness.get('already_features_brands', False)}
- Brands already tagged: {creator_profile.get('brands_already_tagged', [])}

Content gaps: {creator_profile.get('content_gaps', [])}

=== BRAND CONTEXT ===
Brand: {brand_name}
Category: {brand_category}
Hero products: {brand_context.get('hero_products') or ([brand.get('hero_product')] if brand.get('hero_product') else [])}
Recent launches: {brand_context.get('recent_launches', [])}
Target audience: {brand_context.get('target_audience_desc') or brand.get('target_audience') or 'Not specified'}
Brand mission: {brand_context.get('brand_mission_summary') or brand.get('description') or 'Not available'}
Brand tone/style: {brand.get('tone') or 'Not specified'}
Website: {brand.get('website') or 'Not available'}

Aesthetic:
- Palette: {brand_context.get('aesthetic_color_palette', 'unknown')} ({brand_context.get('aesthetic_specific_colors', [])})
- Style: {brand_context.get('aesthetic_style', 'unknown')}
- Descriptors: {brand_context.get('aesthetic_descriptors', [])}

Content preferences (from creators they've historically featured):
- Preferred formats: {brand_context.get('preferred_content_formats', [])}
- Preferred themes: {brand_context.get('preferred_content_themes', [])}

Historical accepted creator profile:
- Follower range: {brand_context.get('accepted_follower_range_min', 'N/A')}-{brand_context.get('accepted_follower_range_max', 'N/A')}
- Engagement floor: {brand_context.get('accepted_engagement_rate_min', 'N/A')}%
- Engagement median: {brand_context.get('accepted_engagement_rate_median', 'N/A')}%
- Primary niches: {brand_context.get('accepted_niches_all', [])}

=== TASK ===
Compare this creator against {brand_name}. Generate the JSON.

CRITICAL REQUIREMENT - Your output MUST be unique to {brand_name}:
1. Mention "{brand_name}" by name in observation OR action
2. Reference a specific product, aesthetic, or content preference from the brand context
3. DO NOT give generic advice like "post more products" or "show products in use"
4. Your advice should only make sense for {brand_name}, not any random brand

If you cannot find brand-specific details, focus on:
- The brand's category-specific content style
- What makes {brand_name} different from generic {brand_category} brands
- How the creator's aesthetic matches or differs from {brand_name}'s vibe'''

    return prompt


def build_retry_prompt(previous_issues: List[str], previous_output: Dict) -> str:
    """
    Build additional prompt section for retry attempts.

    Args:
        previous_issues: Validation issues from previous attempt
        previous_output: Previous output that failed

    Returns:
        Retry prompt section
    """
    prev_reasons = [r.get('chip_text', '') for r in previous_output.get('reasons_you_fit', [])]
    prev_qw = previous_output.get('quick_win', {}).get('action_title', '')

    return f'''
=== AVOID ===
Your previous attempt produced these generic outputs that failed validation:
Reasons: {prev_reasons}
Quick win: {prev_qw}
Issues: {previous_issues}

Generate genuinely brand-specific alternatives. Reference specific
brand aesthetics, historical creator patterns, or content format
preferences that would not apply to a random brand.'''


def call_gemini(prompt: str, system_prompt: str = AI_DEPTH_SYSTEM_PROMPT) -> Dict:
    """
    Call Gemini API with the prompt.

    Args:
        prompt: User prompt
        system_prompt: System prompt

    Returns:
        Parsed JSON response

    Raises:
        ValueError on API or parsing errors
    """
    if not GEMINI_API_KEY:
        raise ValueError("GEMINI_API_KEY not configured")

    url = f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_MODEL}:generateContent?key={GEMINI_API_KEY}"

    payload = {
        'contents': [{
            'parts': [
                {'text': f"{system_prompt}\n\n{prompt}"}
            ]
        }],
        'generationConfig': {
            'temperature': 0.4,
            'topK': 1,
            'topP': 0.8,
            'maxOutputTokens': 4096,
        }
    }

    try:
        response = requests.post(url, json=payload, timeout=30)
        response.raise_for_status()

        result = response.json()
        text = result.get('candidates', [{}])[0].get('content', {}).get('parts', [{}])[0].get('text', '')

        # Parse JSON from response
        if '```json' in text:
            text = text.split('```json')[1].split('```')[0].strip()
        elif '```' in text:
            text = text.split('```')[1].split('```')[0].strip()

        return json.loads(text)

    except json.JSONDecodeError as e:
        raise ValueError(f"Failed to parse Gemini response as JSON: {e}")
    except requests.RequestException as e:
        raise ValueError(f"Gemini API request failed: {e}")


def _calculate_readiness_floor(creator_profile: Dict, brand: Dict) -> str:
    """
    Calculate deterministic status floor based on readiness signals + niche match.

    This ensures consistency - if objective criteria are met, status
    can't be worse than this floor (AI can upgrade but not downgrade).

    Key principle: If a creator's niche matches the brand category, they should
    get at least 'almost' status. Matching niches is valuable signal.

    Returns:
        'ready', 'almost', or 'not_yet' as the minimum status
    """
    brand_name = (brand.get('brand_name') or brand.get('name', '')).lower()
    brand_category = (brand.get('category') or '').lower()

    # Parse brand_readiness_signals
    brand_readiness = creator_profile.get('brand_readiness_signals', {})
    if isinstance(brand_readiness, str):
        try:
            brand_readiness = json.loads(brand_readiness)
        except:
            brand_readiness = {}

    # Parse content_format_breakdown
    content_format = creator_profile.get('content_format_breakdown', {})
    if isinstance(content_format, str):
        try:
            content_format = json.loads(content_format)
        except:
            content_format = {}

    # Calculate readiness signals
    creator_niche = (creator_profile.get('primary_niche') or '').lower()
    secondary_niches = creator_profile.get('secondary_niches', []) or []
    if isinstance(secondary_niches, str):
        try:
            secondary_niches = json.loads(secondary_niches)
        except:
            secondary_niches = []
    all_creator_niches = [creator_niche] + [n.lower() for n in secondary_niches if n]

    # Expanded product-showing niches (not just beauty)
    product_niches = [
        'beauty', 'makeup', 'skincare', 'haircare', 'cosmetics',
        'wellness', 'fitness', 'health', 'lifestyle', 'fashion',
        'food', 'home', 'tech', 'gaming', 'travel'
    ]
    product_content = (
        content_format.get('product_close_ups', 0) +
        content_format.get('grwm_routine', 0) +
        content_format.get('before_after', 0) +
        content_format.get('lifestyle_context', 0)
    )

    # Check niche match between creator and brand category
    niche_match = False
    niche_match_categories = {
        'beauty': ['beauty', 'makeup', 'skincare', 'cosmetics'],
        'haircare': ['beauty', 'haircare'],
        'wellness': ['wellness', 'health', 'fitness', 'lifestyle'],
        'fitness': ['wellness', 'health', 'fitness', 'lifestyle', 'sports'],
        'lifestyle': ['lifestyle', 'wellness', 'home', 'fashion', 'travel'],
        'fashion': ['fashion', 'lifestyle', 'beauty'],
        'food': ['food', 'lifestyle', 'wellness', 'health'],
        'home': ['home', 'lifestyle', 'wellness'],
        'tech': ['tech', 'gaming', 'lifestyle'],
    }

    # Check if any creator niche matches the brand category
    for creator_n in all_creator_niches:
        # Direct match
        if creator_n == brand_category:
            niche_match = True
            break
        # Category group match
        for category, related in niche_match_categories.items():
            if creator_n in related and brand_category in related:
                niche_match = True
                break
        if niche_match:
            break

    shows_products = brand_readiness.get('shows_products_in_use', False) or \
                     creator_niche in product_niches or product_content > 0
    features_brands = brand_readiness.get('already_features_brands', False)
    brands_tagged = creator_profile.get('brands_already_tagged', []) or []
    has_email = creator_profile.get('has_collab_email', False)
    posts_regularly = (creator_profile.get('posting_cadence_per_week') or 0) >= 1  # Lowered to 1/week

    # Check if already tags this specific brand
    already_tags_this_brand = any(
        brand_name in tag.lower() or tag.lower() in brand_name
        for tag in brands_tagged
    ) if brands_tagged and brand_name else False

    # Count boxes checked
    boxes_checked = sum([
        shows_products,
        features_brands or len(brands_tagged) > 0,
        has_email,
        posts_regularly,
        already_tags_this_brand,
        niche_match  # Niche match counts as a box!
    ])

    print(f"[StatusFloor] creator_niche={creator_niche}, brand_category={brand_category}, "
          f"niche_match={niche_match}, has_email={has_email}, posts_regularly={posts_regularly}, "
          f"shows_products={shows_products}, boxes_checked={boxes_checked}")

    # Determine status floor - MORE GENEROUS
    if already_tags_this_brand:
        return 'ready'  # Strongest signal - already posts about this brand
    elif boxes_checked >= 4:
        return 'ready'
    elif boxes_checked >= 2:
        return 'almost'  # 2+ boxes = almost ready (includes niche match)
    elif niche_match or has_email:
        return 'almost'  # Niche match alone OR email alone = almost
    else:
        return 'not_yet'


def _apply_status_floor(output: Dict, floor_status: str) -> Dict:
    """Apply status floor - upgrade AI status if floor is better."""
    status_rank = {'ready': 3, 'almost': 2, 'not_yet': 1, 'poor_fit': 0}

    ai_status = output.get('verdict', {}).get('status', 'not_yet')
    ai_rank = status_rank.get(ai_status, 1)
    floor_rank = status_rank.get(floor_status, 1)

    if floor_rank > ai_rank:
        # Upgrade status to floor
        if 'verdict' not in output:
            output['verdict'] = {}
        output['verdict']['status'] = floor_status
        output['verdict']['status_upgraded'] = True
        print(f"[AIDepth] Status upgraded from {ai_status} to {floor_status} based on readiness floor")

    return output


def generate_unlock_analysis(creator_id: str, creator_profile: Dict,
                             brand: Dict, brand_context: Dict,
                             db_conn) -> Tuple[Dict, bool]:
    """
    Generate brand-specific unlock analysis with validation and retry.

    Architecture (per user feedback):
    1. Calculate deterministic fit score FIRST (don't let LLM decide)
    2. Pass pre-computed score to Gemini for EXPLANATION only
    3. Gemini explains why, but cannot override the score

    Args:
        creator_id: Creator UUID
        creator_profile: Creator profile data
        brand: Brand basic info
        brand_context: Enriched brand context
        db_conn: Database connection

    Returns:
        Tuple of (analysis_dict, used_fallback)
    """
    brand_name = brand.get('brand_name') or brand.get('name', '')
    brand_category = brand.get('category', '')
    previous_output = None
    previous_issues = []

    # STEP 1: Calculate deterministic fit score BEFORE calling Gemini
    # This is the key architectural change - LLM doesn't decide the score
    fit_score = calculate_fit_score(creator_profile, brand_category)
    score_context = get_score_context_for_llm(fit_score, brand_name)

    print(f"[AIDepth] Deterministic score for {brand_name}: {fit_score['overall_score']}% "
          f"({fit_score['label']}) - {fit_score['status']}")

    # Use deterministic status - this is the source of truth
    deterministic_status = fit_score['status']

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            # Build prompt with pre-computed score context
            user_prompt = build_user_prompt(creator_profile, brand_context, brand)

            # CRITICAL: Prepend score context so Gemini knows the verdict is pre-decided
            user_prompt = score_context + "\n\n" + user_prompt

            # Add retry context if not first attempt
            if attempt > 1 and previous_output:
                user_prompt += build_retry_prompt(previous_issues, previous_output)

            # Call Gemini - it explains, it doesn't decide
            output = call_gemini(user_prompt)

            # Validate output
            is_valid, issues = validate_and_retry(
                output, creator_id, brand, attempt, db_conn
            )

            if is_valid:
                # FORCE the deterministic status - override whatever Gemini said
                if 'verdict' not in output:
                    output['verdict'] = {}
                output['verdict']['status'] = deterministic_status
                output['fit_score'] = fit_score  # Include full score breakdown

                # Add fit tier emoji mapping
                output = _add_verdict_details(output, brand_name, status=deterministic_status)
                return output, False

            # Store for retry
            previous_output = output
            previous_issues = issues
            print(f"[AIDepth] Attempt {attempt} failed validation: {issues}")

        except Exception as e:
            print(f"[AIDepth] Attempt {attempt} error: {e}")
            previous_issues = [str(e)]

    # All attempts failed - use curated fallback with deterministic score
    print(f"[AIDepth] All attempts failed, using curated fallback for {brand_name}")

    creator_niche = creator_profile.get('primary_niche')
    fallback = get_curated_fallback(brand.get('id'), creator_niche, db_conn)

    if fallback:
        # Use deterministic status from our scoring, not default
        fallback['fit_score'] = fit_score
        fallback = _add_verdict_details(fallback, brand_name, status=deterministic_status)

        # Log fallback usage
        validator = UnlockValidator(db_conn)
        validator.log_validation(
            creator_id, brand.get('id'), MAX_RETRIES + 1,
            ['used_fallback'], used_fallback=True
        )

        return fallback, True

    # No fallback available - return safe default with deterministic score
    default = _get_default_fallback(brand_name)
    default['fit_score'] = fit_score
    default = _add_verdict_details(default, brand_name, status=deterministic_status)
    return default, True


def _add_verdict_details(output: Dict, brand_name: str,
                         status: Optional[str] = None) -> Dict:
    """Add UI-friendly verdict details based on status."""
    if not status:
        status = output.get('verdict', {}).get('status', 'almost')

    # Map new status to UI display
    verdict_map = {
        'ready': {
            'hero_emoji': '🟢',
            'hero_headline': f'Ready to Pitch {brand_name}',
            'verdict_pill': 'Pitch Now',
            'fit_tier': 'high'
        },
        'almost': {
            'hero_emoji': '🟡',
            'hero_headline': f'Almost Ready for {brand_name}',
            'verdict_pill': 'One Step Away',
            'fit_tier': 'medium'
        },
        'not_yet': {
            'hero_emoji': '🟠',
            'hero_headline': f'Build First for {brand_name}',
            'verdict_pill': 'Need More Content',
            'fit_tier': 'low'
        },
        'poor_fit': {
            'hero_emoji': '🔴',
            'hero_headline': f'Skip {brand_name}',
            'verdict_pill': 'Wrong Fit',
            'fit_tier': 'low'
        }
    }

    verdict_details = verdict_map.get(status, verdict_map['almost'])

    if 'verdict' not in output:
        output['verdict'] = {}

    output['verdict'].update({
        'status': status,
        **verdict_details
    })

    return output


def _get_default_fallback(brand_name: str) -> Dict:
    """Return a safe coaching default when no curated fallback exists."""
    return {
        'verdict': {
            'status': 'almost',
            'confidence': 'medium',
            'hero_emoji': '🟡',
            'hero_headline': f'Almost Ready for {brand_name}',
            'verdict_pill': 'One Step Away',
            'fit_tier': 'medium'
        },
        'missing_proof': {
            'observation': f'We need to analyze your recent posts to give you specific advice for {brand_name}.',
            'why_it_matters': f'Understanding your content style helps us match you with {brand_name}\'s aesthetic.'
        },
        'next_move': {
            'action': f'Make sure your profile is public so we can analyze your content',
            'reasoning': f'We can give better {brand_name}-specific advice once we see your posts',
            'then_what': 'Check back and we\'ll have personalized insights'
        },
        'coach_note': 'Connect your profile for personalized brand matching.',
        # Legacy format for backwards compatibility
        'reasons_you_fit': [
            {'chip_text': 'Active profile', 'detail': 'Recent posting activity'}
        ],
        'quick_win': {
            'emoji': '📸',
            'action_title': f'Post content matching {brand_name}\'s style',
            'note': 'Create content that fits their aesthetic',
            'gain_pill': 'Better chance of reply'
        },
        'used_fallback': True
    }


class AIDepthGenerator:
    """Main generator class for AI Depth unlock analysis."""

    def __init__(self, db_conn=None):
        self.db_conn = db_conn

    def generate(self, creator_id: str, creator_profile: Dict,
                 brand: Dict, brand_context: Optional[Dict] = None) -> Dict:
        """
        Generate brand-specific unlock analysis.

        Args:
            creator_id: Creator UUID
            creator_profile: Creator profile data
            brand: Brand basic info
            brand_context: Optional enriched brand context

        Returns:
            Analysis dict with verdict, reasons, and quick_win
        """
        # Use empty context if not provided
        if not brand_context:
            brand_context = {}

        analysis, used_fallback = generate_unlock_analysis(
            creator_id, creator_profile, brand, brand_context, self.db_conn
        )

        analysis['used_fallback'] = used_fallback
        return analysis


def get_ai_depth_generator(db_conn=None) -> AIDepthGenerator:
    """Factory function for AIDepthGenerator."""
    return AIDepthGenerator(db_conn)
