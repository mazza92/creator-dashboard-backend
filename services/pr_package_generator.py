"""
PR Package Generator - Comprehensive PR Package generation for creators

This service generates complete PR Packages containing:
1. Verified PR contact (existing, not generated here)
2. Ready-to-send pitch in 3 tones (Short, Growing, Founder)
3. Optimal send time (deterministic from historical data)
4. Content Playbook: 5 content ideas (Gemini)
5. Follow-up sequence: Day 3, 8, 14 (Gemini)
6. Reply prediction (deterministic heuristic)

Design Principle: Personal talent agent, not chatbot.
Every piece must read as if written by an experienced, warm, specific human agent.
"""

import os
import re
import json
import logging
from typing import Dict, List, Optional, Any, Tuple
from dataclasses import dataclass, asdict
from datetime import datetime
from collections import defaultdict

# Gemini import
try:
    from google import genai
    from google.genai import types
    HAS_GEMINI = True
except ImportError:
    HAS_GEMINI = False

logger = logging.getLogger(__name__)

# =============================================================================
# AI-TELL SCRUBBER - Detects and blocks AI-obvious patterns
# =============================================================================

# Em dashes are THE #1 AI tell - absolute zero tolerance
FORBIDDEN_PATTERNS = [
    # Em/en dashes (the #1 AI tell)
    r"—",
    r"–",
    r" -- ",

    # Corporate/marketing stock phrases
    r"\belevat[e|ing]\b",
    r"\bunleash",
    r"\bleverag[e|ing]",
    r"\bempower",
    r"\bamplify",
    r"\brevolutioniz",
    r"\bcutting.edge\b",
    r"\bgame.chang",
    r"\bharness the power",
    r"\bunlock (your|the) potential",
    r"\bin today'?s fast.paced",
    r"\bthe .* space\b",           # "the beauty space", "the wellness space"
    r"\bthe .* landscape\b",
    r"\bcurated experience",
    r"\bseamless",
    r"\bsynerg",

    # AI-tell openers
    r"^I came across",
    r"^I noticed your",
    r"^I was browsing",
    r"^Love what you'?re doing",
    r"^Huge fan",
    r"^I'?m obsessed",
    r"^In today'?s",

    # Overclaims
    r"\bworld[- ]class",
    r"\bamazing\b",
    r"\bincredible\b",
    r"\bperfect fit\b",

    # NEW: AI-tells from brief refinement
    r"\bresonat[e|es|ing]\b",      # "resonate with your audience"
    r"\btarget audience\b",         # too corporate
    r"\bhighly engaged\b",          # robotic metric-speak
    r"\baligns? (perfectly )?(with|to)\b",  # "aligns perfectly with"
    r"\bexcited to\b",              # overused AI opener
    r"\bI'?d love to\b",            # passive/weak
    r"\bwould love to\b",           # passive/weak
    r"\bthrilled\b",                # corporate excitement
    r"\bpassionate about\b",        # overused
    r"\bvalues\b.*\balign\b",       # "our values align"
    r"\bmutually beneficial\b",     # too transactional
    r"\bwin[- ]win\b",              # cliche
    r"\bopportunity to collaborate\b",  # generic ask
    r"\bpartnership opportunity\b", # corporate speak
    r"\bfeel free to\b",            # passive/weak CTA
    r"\bdon'?t hesitate\b",         # passive/weak CTA
    r"\blet me know if\b",          # weak ask
]


def scrub_text(text: str, field_name: str) -> List[Tuple[str, str]]:
    """
    Check text for forbidden AI-tell patterns.
    Returns list of (field_name, matched_pattern) tuples.
    """
    issues = []
    for pattern in FORBIDDEN_PATTERNS:
        match = re.search(pattern, text, re.IGNORECASE | re.MULTILINE)
        if match:
            issues.append((field_name, match.group(0)))
    return issues


def auto_fix_package(package_json: Dict) -> Dict:
    """
    Auto-fix common AI-tell patterns in package before validation.
    This runs final_clean on all text fields to fix issues before scrubber rejects.
    Returns the fixed package (modified in-place).
    """
    # Fix all 3 pitch tones
    for pitch_key in ["pitch_short", "pitch_growing", "pitch_founder"]:
        pitch = package_json.get(pitch_key, {})
        if pitch.get("subject"):
            pitch["subject"] = final_clean(pitch["subject"])
        if pitch.get("body_plain"):
            pitch["body_plain"] = final_clean(pitch["body_plain"])
        if pitch.get("body_html"):
            pitch["body_html"] = final_clean(pitch["body_html"])

    # Fix content ideas
    for idea in package_json.get("content_ideas", []):
        if idea.get("title"):
            idea["title"] = final_clean(idea["title"])
        if idea.get("why_this_brand"):
            idea["why_this_brand"] = final_clean(idea["why_this_brand"])

    # Fix follow-ups
    follow_ups = package_json.get("follow_ups", {})
    for fu_key in ["day3", "day8", "day14"]:
        fu = follow_ups.get(fu_key, {})
        if fu.get("subject"):
            fu["subject"] = final_clean(fu["subject"])
        if fu.get("body_plain"):
            fu["body_plain"] = final_clean(fu["body_plain"])

    return package_json


def scrub_pr_package(package_json: Dict) -> List[Tuple[str, str]]:
    """
    Validate entire PR Package for AI-tells.
    Returns list of issues. Empty list = clean.
    """
    issues = []

    # Check all 3 pitch tones
    for pitch_key in ["pitch_short", "pitch_growing", "pitch_founder"]:
        pitch = package_json.get(pitch_key, {})
        if pitch.get("subject"):
            issues.extend(scrub_text(pitch["subject"], f"{pitch_key}.subject"))
        if pitch.get("body_plain"):
            issues.extend(scrub_text(pitch["body_plain"], f"{pitch_key}.body_plain"))

    # Check 5 content ideas
    for idx, idea in enumerate(package_json.get("content_ideas", [])):
        if idea.get("title"):
            issues.extend(scrub_text(idea["title"], f"content_ideas[{idx}].title"))
        if idea.get("why_this_brand"):
            issues.extend(scrub_text(idea["why_this_brand"], f"content_ideas[{idx}].why_this_brand"))

    # Check 3 follow-ups
    follow_ups = package_json.get("follow_ups", {})
    for fu_key in ["day3", "day8", "day14"]:
        fu = follow_ups.get(fu_key, {})
        if fu.get("subject"):
            issues.extend(scrub_text(fu["subject"], f"follow_ups.{fu_key}.subject"))
        if fu.get("body_plain"):
            issues.extend(scrub_text(fu["body_plain"], f"follow_ups.{fu_key}.body_plain"))

    return issues


def final_clean(text: str) -> str:
    """
    Belt-and-suspenders cleanup: Replace any em/en dash and AI-tell patterns.
    This runs AFTER Gemini output to fix common issues that slip through.
    """
    if not text:
        return text

    # Replace any em/en dash with period + space
    text = text.replace("—", ". ").replace("–", ". ").replace(" -- ", ". ")

    # Auto-fix common AI-tell patterns with natural alternatives
    AI_REPLACEMENTS = [
        # Overclaims - remove entirely or simplify
        (r"\bgame[- ]?chang(ing|er|ed)\b", "solid"),
        (r"\bcutting[- ]?edge\b", "modern"),
        (r"\bworld[- ]?class\b", "quality"),
        (r"\bincredible\b", "great"),
        (r"\bamazing\b", "solid"),
        (r"\bperfect fit\b", "good fit"),  # "perfect fit" -> less AI-obvious
        # "I'd love to" -> more direct alternatives
        (r"\bI'?d love to\b", "I want to"),
        (r"\bwould love to\b", "want to"),
        # "Let me know if" -> direct CTA
        (r"\bLet me know if\b", "Just reply if"),
        (r"\blet me know if\b", "just reply if"),
        # "aligns with/perfectly with" -> simpler
        (r"\baligns? perfectly with\b", "fits"),
        (r"\baligns? with\b", "fits"),
        # "resonate" -> simpler (specific patterns first, then catch-all)
        (r"\bresonates? with\b", "works for"),
        (r"\bwould resonate\b", "would work"),
        (r"\bresonating\b", "working"),
        (r"\bresonates?\b", "works"),  # catch-all for standalone resonate/resonates
        # "target audience" -> simpler
        (r"\btarget audience\b", "audience"),
        # "highly engaged" -> simpler
        (r"\bhighly engaged\b", "engaged"),
        # "excited to" -> direct
        (r"\bexcited to\b", "ready to"),
        # "feel free to" -> direct
        (r"\bfeel free to\b", "you can"),
        (r"\bFeel free to\b", "You can"),
        # "don't hesitate" -> direct
        (r"\bdon'?t hesitate to\b", "just"),
        # "opportunity to collaborate" -> simple
        (r"\bopportunity to collaborate\b", "working together"),
        # "partnership opportunity" -> simple
        (r"\bpartnership opportunity\b", "collab"),
        # "mutually beneficial" -> remove
        (r"\bmutually beneficial\b", "great"),
        # "win-win" -> remove
        (r"\bwin[- ]win\b", "great fit"),
        # "passionate about" -> simpler
        (r"\bpassionate about\b", "into"),
        # "thrilled" -> simpler
        (r"\bthrilled\b", "excited"),
    ]

    for pattern, replacement in AI_REPLACEMENTS:
        text = re.sub(pattern, replacement, text, flags=re.IGNORECASE)
    # Normalize multiple spaces within lines only — keep paragraph breaks
    text = re.sub(r"[^\S\n]{2,}", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


# =============================================================================
# GEMINI SYSTEM PROMPT - The unified prompt for all generated sections
# =============================================================================

SYSTEM_PROMPT = """You are a warm, sharp, experienced talent agent representing a specific creator.
You know this creator's voice, niche, and audience. You know the brand they're
about to pitch. Your job is to deliver a complete PR outreach package: pitch
variants, content strategy, and follow-up sequence, all in the creator's voice,
all pointed at THIS specific brand.

You output ONE strict JSON object with the schema below. Every field must be filled.
Missing fields fail the response.

You write like a real human agent, not an AI. Your writing sounds specific, warm,
tactical, and confident. It never sounds like a template, a marketing brochure,
or a chatbot.

═══════════════════════════════════════════════════════════════════════════════════
ABSOLUTE RULES: violating any of these fails the response
═══════════════════════════════════════════════════════════════════════════════════

NEVER USE:
- Em dashes (—) or en dashes (–) or double hyphens (--)
  Use commas, periods, or parentheses instead. This rule is absolute.
- These words/phrases: "elevate," "unleash," "leverage," "empower," "amplify,"
  "revolutionize," "cutting-edge," "game-changing," "unlock your potential,"
  "harness the power," "in today's fast-paced world," "landscape," "space"
  (as in "the beauty space"), "synergy," "curated experience," "seamless"
- Openers: "I came across," "I noticed your," "I was browsing," "Love what
  you're doing," "Huge fan of your brand," "I'm obsessed with"
- Exclamation marks in subject lines
- ALL CAPS words for emphasis
- Emoji in pitch bodies (fine in subject fields if authentic, sparingly)
- Corporate speak: "leverage synergies," "align on deliverables"
- The words "amazing" or "incredible" in any context

ALWAYS:
- Reference something specific and observable about this brand (product,
  aesthetic, recent activity) provided in the input
- Sound like the creator wrote it themselves in their voice
- Match the creator's tier (starter/growing/established), never over-claim audience power
- Use contractions naturally (I'm, you've, it's)

═══════════════════════════════════════════════════════════════════════════════════
OUTPUT STRUCTURE (strict JSON)
═══════════════════════════════════════════════════════════════════════════════════

You return a single JSON object with these fields:

{
  "pitch_short": {
    "subject": "string, 4-7 words, lowercase, no punctuation",
    "body_html": "string, valid HTML with <p> tags, ~3 short paragraphs, 60-90 words total",
    "body_plain": "same content as body_html in plain text. REQUIRED blank line between paragraphs (real \\n\\n). Never one unbroken block"
  },
  "pitch_growing": {
    "subject": "string, 4-7 words, lowercase",
    "body_html": "string, ~4 paragraphs, 100-140 words",
    "body_plain": "plain text version"
  },
  "pitch_founder": {
    "subject": "string, 4-7 words, lowercase",
    "body_html": "string, ~4-5 paragraphs, 130-180 words, warmer and more personal than growing, references specific product or aesthetic detail",
    "body_plain": "plain text version"
  },
  "content_ideas": [
    {
      "title": "string, the actual TikTok/IG hook or caption idea, 8-14 words",
      "format": "string, e.g. 'GRWM Reel', 'Voice-over routine', 'Static carousel'",
      "why_this_brand": "string, 1 sentence explaining WHY posting this before pitching increases odds with THIS brand specifically"
    }
    // exactly 5 items in this array
  ],
  "follow_ups": {
    "day3": {
      "subject": "string, 3-6 words, references the earlier email lightly",
      "body_plain": "string, 40-70 words. Warm nudge, no pressure, adds a small value data point or thought"
    },
    "day8": {
      "subject": "string, 3-6 words",
      "body_plain": "string, 50-90 words. Adds concrete new value: a fresh content angle, a data point about their audience, a relevant post the creator just made"
    },
    "day14": {
      "subject": "string, 3-6 words",
      "body_plain": "string, 30-50 words. Soft close. Not begging. Offers an easy out AND a lightweight future intent"
    }
  },
  "reasoning": "string, 1-2 sentences, internal only, explains the creative strategy chosen for this creator + brand combination"
}

═══════════════════════════════════════════════════════════════════════════════════
VOICE + STRUCTURE FOR EACH SECTION
═══════════════════════════════════════════════════════════════════════════════════

CRITICAL RULES FOR ALL PITCHES:

1. GREETING + OPENER RULE: Every pitch MUST start with a greeting line, then
   the first content sentence must reference the BRAND (product, aesthetic, launch).

   REQUIRED FORMAT:
   Hi [Brand] team,

   [First sentence about the brand, NOT self-intro]

   Bad: "Your Gloss Bomb is amazing..." (missing greeting)
   Bad: "Hi, I'm Sarah and I create beauty content..." (self-intro first)
   Good: "Hi Fenty team,\n\nYour Gloss Bomb has been all over my feed..."

2. ASK RULE: The ask must request a SPECIFIC product or deliverable.
   Bad: "Would you be open to a collaboration?"
   Good: "Would you be open to sending one of your serums for me to feature?"

3. SIGN-OFF RULE: Every pitch MUST end with the creator's first name.
   Bad: "Best regards," or "Thanks!"
   Good: "Best,\nSarah" or "Talk soon,\nMike"

4. STYLE TARGET: Include ONE specific believable moment that feels real.
   Examples: "I tried the mini at Sephora last month", "my roommate won't
   stop borrowing mine", "I've been eyeing the olive colorway"

PITCHES: 3-tone spectrum:

IMPORTANT - PORTFOLIO & WHITELISTING LINES:
Every pitch MUST include these two lines (IN THIS ORDER) right BEFORE the final ask:
1. Portfolio line: "You can see my recent work here: {{PORTFOLIO_LINK}}"
2. Whitelisting line: "Happy for you to use any content in your paid ads, no extra cost."

These lines go BEFORE the ask, NOT at the very end.

- SHORT: 70-100 words. Confident, direct, minimal. Feels like it was tapped
  out from a phone. Great for creators who want speed. Structure:
  * Greeting line: "Hi [Brand] team," (REQUIRED, own line)
  * Opener (1 sentence, brand-specific observation, NOT self-intro)
  * Compressed self-intro (1 sentence)
  * Portfolio line: "You can see my recent work here: {{PORTFOLIO_LINK}}"
  * Whitelisting offer: "Happy for you to use any content in your paid ads, no extra cost."
  * Specific ask (request a specific product, NOT "collaboration")
  * Sign-off with creator first name

- GROWING: 110-150 words. Balanced. Includes creator's stats/context,
  a creative angle for this brand, a clear ask. Default for most creators.
  Structure:
  * Greeting line: "Hi [Brand] team," (REQUIRED, own line)
  * Brand-specific opener (1-2 sentences, reference something observable)
  * Self-intro compressed (1 sentence with niche + platform + audience size)
  * Creative angle for THIS brand (2-3 sentences with specific content idea)
  * Portfolio line: "You can see my recent work here: {{PORTFOLIO_LINK}}"
  * Whitelisting offer: "Happy for you to use any content in your paid ads, no extra cost."
  * Specific ask (name a product category or item)
  * Warm sign-off (1 line + creator first name, REQUIRED)

- FOUNDER-TONE: 140-190 words. Warmer, more personal, treats the reader as
  another founder/human. References a specific product or aesthetic detail.
  Includes a small vulnerability or specific memory (the "believable moment").
  This is for creators who want the pitch to feel like it came from their
  own hand at 11pm. Structure:
  * Greeting line: "Hi [Brand] team," (REQUIRED, own line)
  * Personal observation about brand/product
  * Self-intro with authentic voice
  * Believable moment / specific memory
  * Portfolio line: "You can see my recent work here: {{PORTFOLIO_LINK}}"
  * Whitelisting offer: "Happy for you to use any content in your paid ads, no extra cost."
  * Warm ask for specific product
  * Sign-off with creator first name (REQUIRED)

CONTENT IDEAS: 5 specific hooks:

Each idea must be:
- A specific TikTok/IG hook, not a generic content theme
- Posted BEFORE pitching (this is the whole point)
- Something a brand PR team would notice when checking the creator's recent posts
- Achievable in one session (not "a 10-episode series")
- Format-tagged (Reel, TikTok, carousel, story)
- Include a "why_this_brand" that ties the idea to the specific brand's aesthetic, audience overlap, or product context

Bad example: "Post about wellness" (generic)
Good example: {
  "title": "the 6am routine gear I actually reach for",
  "format": "GRWM Reel",
  "why_this_brand": "Lole's team screens for creators who wear activewear in real training contexts, not styled shoots. This hook signals authentic use."
}

FOLLOW-UPS: 3 message sequence:

The follow-ups are the difference between hobbyist creators and creators who
actually land deals. Each has a distinct emotional register:

- DAY 3 (NUDGE): light, warm, no guilt. Adds ONE thing: a data point, a
  new content link, a thought. Never restates the ask.

- DAY 8 (VALUE ADD): moves the conversation forward. Adds concrete new
  content (e.g., "I posted this yesterday and it hit X views"), or reframes
  the offer slightly with a different angle. This is the most important follow-up.

- DAY 14 (SOFT CLOSE): acknowledges timing, offers easy no, leaves future
  door open. Never ends with "hope to hear from you" (weak). Ends with
  something specific about future timing or a small piece of value.

═══════════════════════════════════════════════════════════════════════════════════
FINAL CHECK BEFORE OUTPUTTING
═══════════════════════════════════════════════════════════════════════════════════

Before returning your JSON, silently verify:
- Zero em dashes anywhere in any field
- Zero forbidden words/phrases
- Every pitch has 3 required fields (subject, body_html, body_plain)
- Exactly 5 content_ideas with all 3 fields each
- All 3 follow-ups have subject + body_plain
- reasoning field is filled
"""


def _parse_niche(raw_niche) -> str:
    """Parse niche field which could be JSON array string, list, or plain string."""
    if not raw_niche:
        return 'content'
    if isinstance(raw_niche, str):
        # Try to parse as JSON array
        try:
            parsed = json.loads(raw_niche)
            if isinstance(parsed, list) and len(parsed) > 0:
                return parsed[0]  # Use first niche
            return raw_niche
        except (json.JSONDecodeError, TypeError):
            return raw_niche
    elif isinstance(raw_niche, list) and len(raw_niche) > 0:
        return raw_niche[0]
    return 'content'


def build_user_prompt(creator: Dict, brand: Dict) -> str:
    """Build the user prompt with creator and brand context."""

    # Determine creator tier
    followers = creator.get('followers_count', 0) or 0
    past_collabs = creator.get('past_collabs_count', 0) or 0

    if followers >= 10000:
        tier = "established"
    elif followers >= 1000 or past_collabs >= 1:
        tier = "growing"
    else:
        tier = "starter"

    # Format engagement rate
    engagement_rate = creator.get('engagement_rate')
    if engagement_rate:
        engagement_str = f"{engagement_rate:.1f}%"
    else:
        engagement_str = "not specified"

    # Build recent themes summary
    recent_themes = creator.get('recent_themes') or creator.get('niche') or 'unspecified'
    if isinstance(recent_themes, list):
        recent_themes = ', '.join(recent_themes[:3])

    # Build past collabs list
    past_collabs_list = creator.get('past_collabs') or []
    if isinstance(past_collabs_list, list) and past_collabs_list:
        past_collabs_str = ', '.join(past_collabs_list[:5])
    else:
        past_collabs_str = 'none listed'

    # Brand context
    brand_name = brand.get('brand_name') or brand.get('name', 'Unknown Brand')
    category = brand.get('category', 'general')
    hero_product = brand.get('hero_product') or 'not specified'
    aesthetic = brand.get('aesthetic') or brand.get('description', '')[:100] or 'not specified'
    recent_activity = brand.get('recent_activity') or brand.get('recent_launch') or 'nothing specific'
    about_snippet = brand.get('about_snippet') or brand.get('description', '')[:500] or 'no about info available'

    # Historical reply patterns (will be filled by deterministic function)
    top_reply_day = brand.get('top_reply_day', 'Tuesday')
    top_reply_hours = brand.get('top_reply_hours', '2-5pm ET')
    reply_rate_avg = brand.get('reply_rate_avg', 25)

    # Parse niche properly
    niche = _parse_niche(creator.get('niche'))

    prompt = f"""CREATOR CONTEXT
- Name: {creator.get('first_name') or creator.get('username', 'Creator')}
- Niche: {niche}
- Platform: {creator.get('primary_platform') or creator.get('platforms', 'TikTok/Instagram')}
- Followers: {followers:,}
- Engagement rate: {engagement_str}
- Tier: {tier}
- Content style: {creator.get('content_style', 'unspecified')}
- Recent post themes: {recent_themes}
- Past collabs: {past_collabs_str}

BRAND CONTEXT
- Name: {brand_name}
- Category: {category}
- Hero product: {hero_product}
- Aesthetic: {aesthetic}
- Recent launch or campaign: {recent_activity}
- Brand's own site copy (first 500 chars from /about): {about_snippet}
- Historical reply patterns: this brand replies most on {top_reply_day} between {top_reply_hours}, with a base reply rate of {reply_rate_avg}%

Generate the JSON now."""

    return prompt


# =============================================================================
# DETERMINISTIC FUNCTIONS (No Gemini - grounded in real data)
# =============================================================================

def compute_optimal_send_time(brand_id: int, cursor) -> Dict:
    """
    Section 3: Optimal send time based on historical reply data.
    This is deterministic, NOT Gemini-generated.
    """
    try:
        # Pull past reply timestamps for this brand
        cursor.execute('''
            SELECT cp.pitched_at
            FROM creator_pipeline cp
            WHERE cp.brand_id = %s
              AND cp.responded_at IS NOT NULL
              AND cp.pitched_at IS NOT NULL
        ''', (brand_id,))

        replies = cursor.fetchall()

        if len(replies) < 10:
            # Fall back to category baseline
            return get_category_baseline_timing(brand_id, cursor)

        # Bucket replies by day-of-week and hour range
        day_counts = defaultdict(int)
        time_counts = defaultdict(int)

        for row in replies:
            if isinstance(row, dict):
                pitched_at = row.get('pitched_at')
            else:
                pitched_at = row[0] if row else None
            if not pitched_at:
                continue
            day_counts[pitched_at.strftime("%A")] += 1
            hour_bucket = _bucket_hour(pitched_at.hour)
            time_counts[hour_bucket] += 1

        if not day_counts:
            return get_category_baseline_timing(brand_id, cursor)

        top_day = max(day_counts, key=day_counts.get)
        top_hour_range = max(time_counts, key=time_counts.get) if time_counts else "2-5pm ET"

        # Calculate multiplier vs. lowest-performing day
        min_day_count = min(day_counts.values())
        multiplier = round(day_counts[top_day] / max(min_day_count, 1), 1)

        return {
            "day": top_day,
            "time_range": top_hour_range,
            "sample_size": len(replies),
            "uplift_multiplier": min(multiplier, 5.0),  # cap to avoid silly numbers
        }

    except Exception as e:
        logger.error(f"Error computing optimal send time: {e}")
        return {
            "day": "Tuesday",
            "time_range": "2-5pm ET",
            "sample_size": 0,
            "uplift_multiplier": 1.0,
        }


def _bucket_hour(hour: int) -> str:
    """Convert hour to readable time range."""
    if 6 <= hour < 9:
        return "6-9am ET"
    elif 9 <= hour < 12:
        return "9am-12pm ET"
    elif 12 <= hour < 14:
        return "12-2pm ET"
    elif 14 <= hour < 17:
        return "2-5pm ET"
    elif 17 <= hour < 20:
        return "5-8pm ET"
    else:
        return "8pm-6am ET"


def get_category_baseline_timing(brand_id: int, cursor) -> Dict:
    """Fallback timing based on category averages."""
    try:
        cursor.execute('''
            SELECT category FROM pr_brands WHERE id = %s
        ''', (brand_id,))
        row = cursor.fetchone()
        if row is None:
            category = 'general'
        elif isinstance(row, dict):
            category = row.get('category') or 'general'
        else:
            category = row[0] or 'general'
    except:
        category = 'general'

    # Category-based defaults (based on industry data)
    CATEGORY_TIMING = {
        'beauty': {'day': 'Tuesday', 'time_range': '2-5pm ET'},
        'fashion': {'day': 'Wednesday', 'time_range': '9am-12pm ET'},
        'fitness': {'day': 'Tuesday', 'time_range': '6-9am ET'},
        'food': {'day': 'Thursday', 'time_range': '12-2pm ET'},
        'tech': {'day': 'Tuesday', 'time_range': '9am-12pm ET'},
        'lifestyle': {'day': 'Tuesday', 'time_range': '2-5pm ET'},
    }

    timing = CATEGORY_TIMING.get(category.lower() if category else '', CATEGORY_TIMING['lifestyle'])

    return {
        "day": timing['day'],
        "time_range": timing['time_range'],
        "sample_size": 0,  # indicates fallback
        "uplift_multiplier": 1.0,
    }


def predict_reply_rate(creator: Dict, brand: Dict, cursor) -> Dict:
    """
    Section 6: Reply prediction.
    Brand average = free tier visible
    Personalized = Pro-only, adjusted by creator factors

    This is deterministic/heuristic, NOT Gemini-generated.
    """
    brand_id = brand.get('id')

    # Calculate brand average from historical data
    try:
        cursor.execute('''
            SELECT
                COUNT(*) as total_pitches,
                COUNT(CASE WHEN responded_at IS NOT NULL THEN 1 END) as replied
            FROM creator_pipeline
            WHERE brand_id = %s AND pitched_at IS NOT NULL
        ''', (brand_id,))

        row = cursor.fetchone()
        if row is None:
            total = 0
            replied = 0
        elif isinstance(row, dict):
            total = row.get('total_pitches', 0) or 0
            replied = row.get('replied', 0) or 0
        else:
            total = row[0] or 0
            replied = row[1] or 0

        if total >= 5:
            base = round((replied / total) * 100, 1)
            sample_size = total
        else:
            # Not enough data, use category baseline
            base = 25.0  # Default baseline
            sample_size = total
    except Exception as e:
        logger.error(f"Error calculating brand reply rate: {e}")
        base = 25.0
        sample_size = 0

    # Determine confidence level
    if sample_size >= 50:
        confidence = 'high'
    elif sample_size >= 15:
        confidence = 'medium'
    else:
        confidence = 'low'

    # Calculate personalized prediction (Pro feature)
    # v1 heuristic multipliers

    # Niche match multiplier
    creator_niche = _parse_niche(creator.get('niche')).lower()
    brand_category = (brand.get('category') or '').lower()
    niche_multiplier = 1.15 if creator_niche and brand_category and creator_niche in brand_category else 1.0

    # Tier multiplier
    followers = creator.get('followers_count', 0) or 0
    if followers >= 10000:
        tier_multiplier = 1.25
    elif followers >= 1000:
        tier_multiplier = 1.0
    else:
        tier_multiplier = 0.85

    # Engagement multiplier
    engagement_rate = creator.get('engagement_rate')
    if engagement_rate:
        if engagement_rate > 5:
            engagement_multiplier = 1.15
        elif engagement_rate < 2:
            engagement_multiplier = 0.85
        else:
            engagement_multiplier = 1.0
    else:
        engagement_multiplier = 1.0

    personalized = base * niche_multiplier * tier_multiplier * engagement_multiplier
    personalized = min(max(personalized, 5), 85)  # clamp to sane range

    return {
        "brand_avg": round(base, 1),
        "personalized": round(personalized, 1),
        "confidence": confidence,
    }


# =============================================================================
# MAIN GENERATOR CLASS
# =============================================================================

@dataclass
class PRPackageResult:
    """Result of PR Package generation."""
    success: bool
    package: Optional[Dict] = None
    error: Optional[str] = None
    source: str = "gemini"  # "gemini" or "fallback_template"
    scrub_failures: int = 0


class PRPackageGenerator:
    """
    Generates complete PR Packages using Gemini for AI sections
    and deterministic functions for timing/prediction.
    """

    def __init__(self):
        self.client = None
        if HAS_GEMINI:
            api_key = os.getenv('GEMINI_API_KEY')
            if api_key:
                self.client = genai.Client(api_key=api_key)
                logger.info("PRPackageGenerator initialized with Gemini")
            else:
                logger.warning("GEMINI_API_KEY not set")
        else:
            logger.warning("Gemini SDK not available")

    def generate(
        self,
        creator: Dict,
        brand: Dict,
        cursor,  # DB cursor for deterministic queries
        max_attempts: int = 2
    ) -> PRPackageResult:
        """
        Generate complete PR Package.

        Args:
            creator: Creator dict with profile data
            brand: Brand dict with company data
            cursor: Database cursor for deterministic queries
            max_attempts: Max Gemini retries before fallback

        Returns:
            PRPackageResult with full package or error
        """
        scrub_failures = 0

        # Run deterministic sections in parallel (they don't need Gemini)
        timing = compute_optimal_send_time(brand.get('id'), cursor)
        prediction = predict_reply_rate(creator, brand, cursor)

        # Enrich brand with timing data for Gemini prompt
        brand_enriched = {**brand}
        brand_enriched['top_reply_day'] = timing['day']
        brand_enriched['top_reply_hours'] = timing['time_range']
        brand_enriched['reply_rate_avg'] = prediction['brand_avg']

        # Try Gemini generation
        if self.client:
            logger.info(f"Attempting Gemini generation (max {max_attempts} attempts)")
            for attempt in range(max_attempts):
                try:
                    gemini_result = self._call_gemini(creator, brand_enriched)

                    if not gemini_result:
                        continue

                    # Auto-fix common AI patterns BEFORE validation
                    gemini_result = auto_fix_package(gemini_result)

                    # Validate with scrubber (after auto-fix)
                    issues = scrub_pr_package(gemini_result)

                    if not issues:
                        # Clean and combine with deterministic sections
                        package = self._finalize_package(
                            gemini_result, timing, prediction
                        )
                        return PRPackageResult(
                            success=True,
                            package=package,
                            source="gemini",
                            scrub_failures=scrub_failures
                        )
                    else:
                        scrub_failures += 1
                        logger.warning(
                            f"Scrubber caught issues on attempt {attempt + 1}: {issues}"
                        )

                except Exception as e:
                    logger.error(f"Gemini error on attempt {attempt + 1}: {e}")
                    scrub_failures += 1

        # Fallback to templates
        if not self.client:
            logger.warning("Gemini client not available - using fallback templates")
        else:
            logger.info("Gemini generation failed after all attempts - using fallback templates")
        fallback = self._generate_fallback(creator, brand, timing, prediction)

        return PRPackageResult(
            success=True,
            package=fallback,
            source="fallback_template",
            scrub_failures=scrub_failures
        )

    def _call_gemini(self, creator: Dict, brand: Dict) -> Optional[Dict]:
        """Call Gemini API with unified prompt."""
        try:
            user_prompt = build_user_prompt(creator, brand)
            logger.info(f"Calling Gemini for brand: {brand.get('brand_name', 'unknown')}")

            # Disable thinking tokens - gemini-2.5-flash "thinks" by default which
            # can contaminate JSON output or cause truncation
            try:
                thinking_cfg = types.ThinkingConfig(thinking_budget=0)
            except Exception as e:
                logger.warning(f"Could not create ThinkingConfig: {e}")
                thinking_cfg = None

            # Build config
            config_kwargs = dict(
                system_instruction=SYSTEM_PROMPT,
                temperature=0.65,
                top_p=0.9,
                max_output_tokens=8192,
                response_mime_type="application/json",
            )
            if thinking_cfg is not None:
                config_kwargs["thinking_config"] = thinking_cfg

            response = self.client.models.generate_content(
                model="gemini-2.5-flash",
                contents=user_prompt,
                config=types.GenerateContentConfig(**config_kwargs),
            )

            if not response.text:
                logger.error("Empty Gemini response")
                return None

            logger.info(f"Gemini response length: {len(response.text)} chars")

            # Parse JSON response - handle markdown fences and thinking token wrappers
            package = self._extract_json(response.text)
            if package:
                logger.info(f"Successfully parsed Gemini JSON with keys: {list(package.keys())}")
            return package

        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse Gemini JSON: {e}")
            logger.error(f"Raw response (first 500 chars): {response.text[:500] if response and response.text else 'None'}")
            return None
        except Exception as e:
            logger.error(f"Gemini API error: {type(e).__name__}: {e}")
            import traceback
            logger.error(f"Traceback: {traceback.format_exc()}")
            return None

    def _extract_json(self, text: str) -> Optional[Dict]:
        """
        Robustly extract a JSON object from Gemini response.

        Gemini 2.5-flash may prefix JSON with thinking tokens or markdown
        fences even when response_mime_type='application/json' is set.
        """
        if not text:
            return None

        # Strip leading/trailing whitespace
        text = text.strip()

        # Remove markdown code fences (```json ... ``` or ``` ... ```)
        fence_pattern = re.compile(r'^```(?:json)?\s*(.*?)\s*```$', re.DOTALL)
        m = fence_pattern.match(text)
        if m:
            text = m.group(1).strip()

        # Try direct parse first
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            pass

        # Find the first { ... } block in case there's preamble text
        start = text.find('{')
        end = text.rfind('}')
        if start != -1 and end != -1 and end > start:
            try:
                return json.loads(text[start:end + 1])
            except json.JSONDecodeError as e:
                logger.error(f"Could not extract valid JSON: {e}")
                logger.error(f"Text between braces (first 300 chars): {text[start:start+300]}")

        return None

    def _finalize_package(
        self,
        gemini_result: Dict,
        timing: Dict,
        prediction: Dict
    ) -> Dict:
        """
        Combine Gemini output with deterministic sections.
        Apply final cleanup to all text fields.
        """
        package = {}

        # Pitches (apply final_clean to all text)
        try:
            from services.gemini_pitch_generator import ensure_pitch_paragraphs
        except ImportError:
            ensure_pitch_paragraphs = lambda p: p  # noqa: E731

        for tone in ['pitch_short', 'pitch_growing', 'pitch_founder']:
            pitch = ensure_pitch_paragraphs(dict(gemini_result.get(tone, {}) or {})) or {}
            package[f'{tone}_subject'] = final_clean(pitch.get('subject', ''))
            package[f'{tone}_body_html'] = pitch.get('body_html', '')
            # Preserve paragraph breaks — final_clean must not collapse newlines
            package[f'{tone}_body_plain'] = final_clean(pitch.get('body_plain', ''))

        # Timing (deterministic)
        package['optimal_send_day'] = timing['day']
        package['optimal_send_time_range'] = timing['time_range']
        package['timing_sample_size'] = timing['sample_size']
        package['timing_uplift_multiplier'] = timing['uplift_multiplier']

        # Content ideas
        ideas = gemini_result.get('content_ideas', [])
        cleaned_ideas = []
        for idea in ideas[:5]:
            cleaned_ideas.append({
                'title': final_clean(idea.get('title', '')),
                'format': idea.get('format', ''),
                'why_this_brand': final_clean(idea.get('why_this_brand', ''))
            })
        package['content_ideas'] = cleaned_ideas

        # Follow-ups (apply final_clean)
        follow_ups = gemini_result.get('follow_ups', {})
        for day_key in ['day3', 'day8', 'day14']:
            fu = follow_ups.get(day_key, {})
            package[f'followup_{day_key}_subject'] = final_clean(fu.get('subject', ''))
            package[f'followup_{day_key}_body'] = final_clean(fu.get('body_plain', ''))

        # Prediction (deterministic)
        package['reply_rate_brand_avg'] = prediction['brand_avg']
        package['reply_rate_personalized'] = prediction['personalized']
        package['reply_rate_confidence'] = prediction['confidence']

        # Meta
        package['generation_reasoning'] = gemini_result.get('reasoning', '')
        package['generated_by'] = 'gemini'

        return package

    def _generate_fallback(
        self,
        creator: Dict,
        brand: Dict,
        timing: Dict,
        prediction: Dict
    ) -> Dict:
        """Generate template-based fallback when Gemini fails."""
        creator_name = creator.get('first_name') or creator.get('username', 'there')
        brand_name = brand.get('brand_name') or brand.get('name', 'your brand')
        niche = _parse_niche(creator.get('niche'))
        followers = creator.get('followers_count', 0) or 0
        category = brand.get('category', niche).lower()

        # SHORT tone: 70-100 words, direct, brand-first opener
        short_subject = f"quick {category} collab idea"
        short_body = f"""Hi {brand_name} team,

Been seeing your products pop up in my feed lately. I create {niche} content for {followers:,} followers.

You can see my recent work here: {{{{PORTFOLIO_LINK}}}}

Happy for you to use any content in your paid ads, no extra cost.

Would you be open to sending one of your bestsellers for me to feature in an upcoming post?

Best,
{creator_name}"""

        # GROWING tone: 110-150 words, balanced, brand-first opener with specific ask
        growing_subject = f"{niche} creator, {followers:,} followers"
        growing_body = f"""Hi {brand_name} team,

Your recent launches caught my attention. The aesthetic fits really well with the {niche} content I create for my {followers:,} followers.

I'm thinking a product review or styling post could work well. My audience tends to engage most with honest takes on products I actually use.

You can see my recent work here: {{{{PORTFOLIO_LINK}}}}

Happy for you to use any content in your paid ads, no extra cost.

Would you be open to sending one of your hero products for me to try?

Best,
{creator_name}"""

        # FOUNDER tone: 140-190 words, warmer, more personal with believable moment
        founder_subject = f"been meaning to reach out"
        founder_body = f"""Hi there,

I keep coming back to {brand_name}'s stuff. Actually tried one of your products at a friend's place last month and have been thinking about it since.

I'm {creator_name}. I create {niche} content for around {followers:,} people, and I think there's something here. Your products fit the vibe my audience responds to, genuine stuff that actually works.

You can see my recent work here: {{{{PORTFOLIO_LINK}}}}

Happy for you to use any content in your paid ads, no extra cost.

If you're open to it, I'd put together something real for my audience. Either way, keep making good stuff.

Warmly,
{creator_name}"""

        def to_html(text):
            return f"<p>{text.replace(chr(10)+chr(10), '</p><p>')}</p>"

        # Fallback content ideas (generic but useful)
        content_ideas = [
            {
                'title': f'my honest {category} routine, no filter',
                'format': 'GRWM Reel',
                'why_this_brand': f'Shows {brand_name} your content style is authentic, not staged.'
            },
            {
                'title': f'the {category} products I actually repurchase',
                'format': 'Carousel post',
                'why_this_brand': f'{brand_name} PR teams look for creators who post about products they genuinely use.'
            },
            {
                'title': f'unpopular {category} opinion: things I stopped buying',
                'format': 'TikTok/Reel',
                'why_this_brand': f'Shows you have taste and standards. {brand_name} wants creators who are selective.'
            },
            {
                'title': f'{category} haul but make it real',
                'format': 'Story series',
                'why_this_brand': f'Demonstrates how you naturally integrate products into content.'
            },
            {
                'title': f'what I wish I knew about {category} sooner',
                'format': 'Voice-over Reel',
                'why_this_brand': f'Positions you as knowledgeable in the space {brand_name} operates in.'
            },
        ]

        # Fallback follow-ups
        followup_day3_subject = 'quick follow up'
        followup_day3_body = f"""Hi {brand_name} team,

Just floating this back up in case it got buried. Still interested in creating something with your products if you're open to it.

Best,
{creator_name}"""

        followup_day8_subject = 'one more idea'
        followup_day8_body = f"""Hi again,

I posted some {niche} content this week that got solid engagement. Made me think again about how your products would fit naturally into what I create.

Happy to share the post if you want to see the vibe. Either way, no pressure.

Best,
{creator_name}"""

        followup_day14_subject = 'last note from me'
        followup_day14_body = f"""Hi {brand_name} team,

Totally get if the timing isn't right. I'll keep creating {niche} content either way.

If things change on your end, I'm around. Wishing you a solid Q.

Best,
{creator_name}"""

        package = {
            'pitch_short_subject': short_subject,
            'pitch_short_body_html': to_html(short_body),
            'pitch_short_body_plain': short_body,
            'pitch_growing_subject': growing_subject,
            'pitch_growing_body_html': to_html(growing_body),
            'pitch_growing_body_plain': growing_body,
            'pitch_founder_subject': founder_subject,
            'pitch_founder_body_html': to_html(founder_body),
            'pitch_founder_body_plain': founder_body,

            'optimal_send_day': timing['day'],
            'optimal_send_time_range': timing['time_range'],
            'timing_sample_size': timing['sample_size'],
            'timing_uplift_multiplier': timing['uplift_multiplier'],

            'content_ideas': content_ideas,

            'followup_day3_subject': followup_day3_subject,
            'followup_day3_body': followup_day3_body,
            'followup_day8_subject': followup_day8_subject,
            'followup_day8_body': followup_day8_body,
            'followup_day14_subject': followup_day14_subject,
            'followup_day14_body': followup_day14_body,

            'reply_rate_brand_avg': prediction['brand_avg'],
            'reply_rate_personalized': prediction['personalized'],
            'reply_rate_confidence': prediction['confidence'],

            'generation_reasoning': 'Fallback template used due to Gemini unavailability',
            'generated_by': 'fallback_template',
        }

        return package


# Singleton instance
_generator = None

def get_pr_package_generator() -> PRPackageGenerator:
    """Get or create singleton PR Package generator."""
    global _generator
    if _generator is None:
        _generator = PRPackageGenerator()
    return _generator
