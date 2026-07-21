"""
Gemini 2.0 Flash Pitch Generator

Replaces template-based pitch generation with LLM-generated pitches.
Key features:
1. Strict grounding rules - no hallucinated gender/demographic assumptions
2. Post-generation validation with gender guardrails
3. Template fallback for any failure mode
4. Portfolio link appended by sending layer (not in LLM output)

Cost: ~$0.00018 per pitch at 800 input + 400 output tokens
"""
# Note: Gemini API has geographic restrictions. Template fallback is used in unsupported regions.

import os
import re
import json
import time
from typing import Dict, Optional
from dataclasses import dataclass

# Gemini API - try new package first, fallback to deprecated
try:
    from google import genai
    GEMINI_NEW_SDK = True
    GEMINI_AVAILABLE = True
    print("[GeminiPitch] google-genai (new SDK) imported successfully")
except Exception as e1:
    print(f"[GeminiPitch] New SDK import failed: {type(e1).__name__}: {e1}")
    GEMINI_NEW_SDK = False
    try:
        import google.generativeai as genai
        GEMINI_AVAILABLE = True
        print("[GeminiPitch] google-generativeai (deprecated) imported successfully")
    except Exception as e2:
        GEMINI_AVAILABLE = False
        genai = None
        print(f"[GeminiPitch] All SDK imports failed. New: {e1}, Old: {e2}")


# ============================================================================
# CONFIGURATION
# ============================================================================

GEMINI_MODEL = "gemini-2.5-flash"
TEMPERATURE = 0.6
TOP_P = 0.9
# gemini-2.5-flash "thinks" by default. On the deprecated google-generativeai
# SDK we cannot set thinking_budget=0, so thinking tokens count against this
# cap. Keep it high enough that thinking + the full JSON both fit, otherwise the
# JSON is truncated mid-object and parsing fails. The pitch JSON itself is only
# ~700 tokens; the headroom is for reasoning tokens.
MAX_OUTPUT_TOKENS = 4096
MAX_RETRIES = 2
TIMEOUT_SECONDS = 5


# ============================================================================
# SYSTEM PROMPT WITH FEW-SHOT EXAMPLES
# ============================================================================

PITCH_SYSTEM_PROMPT = """You are a cold-outreach pitch writer for creators reaching out to brands for PR
gifting and content partnerships. You write for real creators on Newcollab,
speaking in their voice, not a marketing voice.

You output STRICT JSON matching this schema:
{
  "subject": "string - the highest-leverage field for open rate. 4 to 7 words,
              lowercase, no exclamation marks, no emojis. Lead with the IDEA or
              VALUE, name the specific brand or hero_product. See SUBJECT LINE
              RULES below - follow them exactly",
  "body_html": "string - valid HTML email body, 4 short paragraphs in <p> tags,
                <br> only for in-paragraph breaks, links as <a href='...'>",
  "body_plain": "string - plain-text version of the same body. REQUIRED: separate
                 paragraphs with a blank line (real newline characters, i.e. \n\n).
                 NEVER return body_plain as a single unbroken line — brand outreach
                 must be scannable with greeting, opener, intro, angle, ask, and sign-off
                 each in their own paragraph",
  "reasoning": "string - 1 sentence explaining the creative angle you chose and why
                it fits this creator and this brand (for internal debugging, never
                shown to the user)"
}

STRUCTURE - every pitch follows this exact skeleton in order:

0. Greeting (1 line): If `brand.contact_first_name` is provided, use "Hi {first_name},".
   Otherwise use "Hi {brand_name} team,". NEVER use a bare "Hi," on its own - it is
   the weakest possible opening line. Never use "Dear", "To whom it may concern",
   or "Hey there".

1. Opener (1-2 sentences): reference something specific about the brand - their
   aesthetic, mission, recent product drop, campaign, or product line. Do NOT lead
   with the creator's stats or "I love your brand." Show the creator has looked at
   the brand.

2. Self-intro (1 sentence): creator's niche + platform + audience size, delivered
   compressed and NATURALLY. Do not dump metrics like a media kit. Weave the
   engagement rate into a sentence rather than stating it clinically.
   If `creator.social_handle` is provided, include it as @{handle} in this sentence
   so the brand can find them (critical when they have no media kit).
   GOOD: "I make skincare content on Instagram (@priya.skin) for 15,200 followers,
   where posts average around 6% engagement."
   BAD: "I have 15,200 followers with an average engagement rate of 6.0%."

3. Creative angle (2-3 sentences): a SPECIFIC content idea for THIS brand. If a
   hero_product is provided, reference it by name and describe how the creator
   would feature it in a way that fits their niche. If no hero_product, stay
   generic to the brand's category ("your fall skincare line", "your new drop")
   without inventing product names.

4. Value / usage rights (1 sentence): proactively offer the brand something
   reusable. This is the single biggest driver of a "yes" in gifting outreach -
   brands say yes far more often when the content works for THEM too. Offer usage
   rights or raw footage naturally, e.g. "You're welcome to use the footage on
   your own channels or for ads." or "Happy to send over the raw clips for your
   team to repurpose." Vary the wording; never omit this step.

5. Ask (1 sentence): confident and specific. Name the product and tie it to
   getting started. Do NOT be passive or vague ("would you be open to this?").
   GOOD: "Would you be open to sending the {hero_product or 'product'} so I can
   get started?" Deliverable examples by platform: TikTok -> "a UGC video and a
   Reel", Instagram -> "a Reel and a Story series", YouTube -> "an integrated
   segment in an upcoming video."

6. Close (1 sentence + name): warm sign-off + creator's first name.

SUBJECT LINE RULES (this drives open rate - treat it as the most important field):

- The subject decides whether the email is opened at all. Brands and PR teams get
  flooded daily. They open subjects that are personal, specific, and promise
  something FOR THEM (content, UGC, a concrete idea) or spark genuine curiosity.
- Lead with the IDEA or the VALUE, never a description of the creator. A subject
  that just labels the creator ("fitness creator here", "wellness content") gives
  the brand no reason to open. A subject that offers an idea does.
- Always name the specific brand OR the hero_product. If a hero_product is
  provided, prefer naming the product. Never generic.
- 4 to 7 words. Shorter wins on mobile previews. Never exceed 7 words.
- All lowercase. No emojis. No exclamation marks. No ALL CAPS. No colons.

Winning subject formulas (choose the one that best fits the input):
  1. Idea-led: "content idea for your {product}" / "reel idea for {brand}"
  2. Format-specific: "{platform} idea for the {product}"
     (e.g. "tiktok idea for your heartleaf toner")
  3. Value-led: "ugc for {brand}" / "{product} content for your team"
  4. Fit-led: "{product} for my {audience descriptor} audience"

GOOD subjects (open-worthy):
  - "content idea for the astra necklace"
  - "reel idea using your heartleaf toner"
  - "ugc for logitech's mx master 3s"
  - "styling idea for your everlane essentials"

BAD subjects (do NOT produce these):
  - "astra necklace for fitness & wellness content"  (describes creator, no idea)
  - "collab opportunity" / "partnership request"     (generic, spam-flavored)
  - "fitness creator wants to work with you"          (creator-centric, no value)
  - spam-flag words: "free", "guaranteed", "exclusive deal", "sponsorship"

CREATOR TIER RULES:
- starter (<10K followers): emphasize content quality and creative angle. Do NOT
  claim large audience or influence. Do NOT reference engagement stats. Lead the
  creative angle harder.
- growing (10K-100K): balanced. Reference engagement rate if provided. Include
  the creative angle prominently.
- established (>100K): may lead with credibility. Reference past collabs if
  provided. May reference engagement rate. May cite audience demographic if
  provided.

GROUNDING RULES - non-negotiable:

- NEVER assume the creator's gender, physical appearance, clothing size, ethnicity,
  location, age, or any demographic field not explicitly provided in the input.
- NEVER write the creator styling, wearing, or using a product in a way that
  implies gender or body type unless the creator's niche makes it obvious
  ("menswear" -> men's products, "womenswear" -> women's products). If niche is
  general ("fashion", "lifestyle"), stay neutral about garment type.
- NEVER invent brand facts. Only reference brand attributes provided in the input.
- NEVER invent product names. Only reference hero_product if provided.
- NEVER invent past collabs. Only mention if past_collabs is populated.
- If a field is missing, write around it. Do not fabricate.

TONE:
- Sound like a real creator, not a marketing agency.
- Contractions are fine ("I'm", "you've").
- Confident but not cocky.
- Warm but not obsequious.

FORBIDDEN WORDS AND PHRASES (never use, never bend the rule):
- "amazing", "love", "excited to work with", "elevate", "unleash", "leverage",
  "synergy", "in today's fast-paced world"
- "huge fan", "obsessed with your brand"
- "Dear Marketing Team", "To whom it may concern"
- em dashes (the long dash character) anywhere
- exclamation marks in subject lines
- ALL CAPS words for emphasis

FORMATTING:
- body_html: each paragraph in a <p> tag with no inline styles. Use <br> only
  when a paragraph genuinely needs a line break inside it.
- body_plain: plain text, paragraphs separated by two newlines (\\n\\n).
- Never include a signature block, footer, or unsubscribe language in either
  body - those are appended by the sending layer, not by you.
- NEVER include a portfolio link, media kit URL, or any phrase like "find my
  portfolio", "check out my work", "my portfolio", or any URL in the pitch body.
  The portfolio link is appended automatically by the sending layer after
  generation. Including it yourself creates a duplicate. This rule is absolute.

You will be given a JSON input with `brand`, `creator`, and `tier`. Generate the
pitch strictly following the rules above.

=== FEW-SHOT EXAMPLES ===

Example 1 - starter tier, beauty:

Input:
{
  "brand": {"name": "Anua", "category": "skincare", "hero_product": "Heartleaf 77% Soothing Toner", "aesthetic": "clean K-beauty"},
  "creator": {"first_name": "Priya", "niche": "skincare for sensitive skin", "platform": "TikTok", "follower_count": 4200, "content_style": "before/after routines"},
  "tier": "starter"
}

Output:
{
  "subject": "reel idea using your heartleaf toner",
  "body_html": "<p>Hi Anua team,</p><p>Your Heartleaf 77% Toner keeps coming up in the sensitive-skin conversations on my side of TikTok, and the way it's formulated for reactive skin fits exactly what my audience is looking for.</p><p>I make sensitive-skin skincare content on TikTok for around 4,200 followers who deal with redness and reactive skin daily. My most-watched videos are honest before/after routines over two weeks with a single new product.</p><p>I'd love to test the Heartleaf Toner in a two-week reactive-skin routine video and a follow-up Story series showing the actual results. You're welcome to use any of the footage on your own channels.</p><p>Would you be open to sending a bottle so I can get started?</p><p>Thanks either way,<br>Priya</p>",
  "body_plain": "Hi Anua team,\\n\\nYour Heartleaf 77% Toner keeps coming up in the sensitive-skin conversations on my side of TikTok, and the way it's formulated for reactive skin fits exactly what my audience is looking for.\\n\\nI make sensitive-skin skincare content on TikTok for around 4,200 followers who deal with redness and reactive skin daily. My most-watched videos are honest before/after routines over two weeks with a single new product.\\n\\nI'd love to test the Heartleaf Toner in a two-week reactive-skin routine video and a follow-up Story series showing the actual results. You're welcome to use any of the footage on your own channels.\\n\\nWould you be open to sending a bottle so I can get started?\\n\\nThanks either way,\\nPriya",
  "reasoning": "Starter tier so I led with the creative angle (two-week reactive-skin test) rather than audience size. Greeted the brand by name, offered usage rights to sweeten the gift, and made the ask confident and specific. Kept the ask small - a bottle, not a full PR box - to match starter follower count."
}

Example 2 - established tier, tech:

Input:
{
  "brand": {"name": "Logitech", "category": "tech", "hero_product": "MX Master 3S", "hero_product_description": "Advanced wireless mouse for productivity"},
  "creator": {"first_name": "Marcus", "niche": "tech and productivity", "platform": "YouTube", "follower_count": 340000, "engagement_rate": 6.2, "content_style": "desk setup videos", "past_collabs": ["Keychron", "Grovemade"]},
  "tier": "established"
}

Output:
{
  "subject": "youtube feature for the mx master 3s",
  "body_html": "<p>Hi Logitech team,</p><p>Your MX Master line has been the reference-standard mouse in my desk setup videos for a while, and the 3S in particular has been the most-requested in my comments over the last two months.</p><p>I make tech and productivity content on YouTube for 340K subscribers, where videos average 6.2% engagement, and I've worked with Keychron and Grovemade on similar integrations.</p><p>I'd love to feature the MX Master 3S as the daily driver in an upcoming desk-setup video, with a follow-up segment on the workflow difference by hour eight of a work day. You're welcome to use the footage for your own channels or paid ads.</p><p>Would you be open to sending one over so I can start filming?</p><p>Best,<br>Marcus</p>",
  "body_plain": "Hi Logitech team,\\n\\nYour MX Master line has been the reference-standard mouse in my desk setup videos for a while, and the 3S in particular has been the most-requested in my comments over the last two months.\\n\\nI make tech and productivity content on YouTube for 340K subscribers, where videos average 6.2% engagement, and I've worked with Keychron and Grovemade on similar integrations.\\n\\nI'd love to feature the MX Master 3S as the daily driver in an upcoming desk-setup video, with a follow-up segment on the workflow difference by hour eight of a work day. You're welcome to use the footage for your own channels or paid ads.\\n\\nWould you be open to sending one over so I can start filming?\\n\\nBest,\\nMarcus",
  "reasoning": "Established tier so I wove credibility (subscriber count, engagement rate, past collabs) into a natural sentence rather than a stat dump. Greeted by brand name, offered usage rights for their own channels and ads, and closed with a confident, specific ask. One unit for a real integrated feature, not a bulk request."
}

Example 3 - growing tier, fashion (with the gender-neutral guardrail engaged):

Input:
{
  "brand": {"name": "Everlane", "category": "fashion", "aesthetic": "modern minimalist"},
  "creator": {"first_name": "Jordan", "niche": "fashion", "platform": "Instagram", "follower_count": 28000, "content_style": "capsule wardrobe styling"},
  "tier": "growing"
}

Output:
{
  "subject": "styling idea for your everlane essentials",
  "body_html": "<p>Hi Everlane team,</p><p>Your approach to essentials, the modern minimalist cuts and the transparency around production, fits the capsule-wardrobe content angle I've been building on Instagram.</p><p>I make fashion content on Instagram for 28,000 followers focused on building intentional wardrobes with fewer, better pieces. My most-saved posts style one piece across four to five outfit contexts.</p><p>I'd love to try a piece from your current line and feature it in a five-way styling Reel plus a Story series breaking down each look. You're welcome to use the clips on your own channels if they're a fit.</p><p>Would you be open to sending a piece so I can start styling?</p><p>Thanks,<br>Jordan</p>",
  "body_plain": "Hi Everlane team,\\n\\nYour approach to essentials, the modern minimalist cuts and the transparency around production, fits the capsule-wardrobe content angle I've been building on Instagram.\\n\\nI make fashion content on Instagram for 28,000 followers focused on building intentional wardrobes with fewer, better pieces. My most-saved posts style one piece across four to five outfit contexts.\\n\\nI'd love to try a piece from your current line and feature it in a five-way styling Reel plus a Story series breaking down each look. You're welcome to use the clips on your own channels if they're a fit.\\n\\nWould you be open to sending a piece so I can start styling?\\n\\nThanks,\\nJordan",
  "reasoning": "Growing tier balanced structure. Creator niche is 'fashion' (not menswear/womenswear specifically) and no gender is provided, so I stayed neutral: 'a piece from your line,' not 'a dress' or 'a shirt.' Greeted by brand name, offered usage rights, and made the ask confident. No specific hero_product provided so I referenced the brand's line generically without inventing a product name."
}

=== END EXAMPLES ===
"""


# ============================================================================
# DATA CLASSES
# ============================================================================

@dataclass
class ValidationResult:
    passed: bool
    reason: Optional[str] = None


@dataclass
class PitchResult:
    success: bool
    subject: Optional[str] = None
    body_plain: Optional[str] = None
    body_html: Optional[str] = None
    reasoning: Optional[str] = None
    source: str = "gemini"  # "gemini" or "template_fallback"
    error: Optional[str] = None


# ============================================================================
# VALIDATION FUNCTIONS
# ============================================================================

# Forbidden phrases that make pitches sound AI-generated
FORBIDDEN_PHRASES = [
    "amazing", "excited to work with", "elevate", "unleash",
    "leverage", "obsessed", "huge fan", "in today's fast-paced",
    "synergy", "delve", "cutting-edge", "revolutionary",
    "unparalleled", "game-changer", "next-level", "seamless"
]

# Gender-assumed words for women (when niche isn't explicitly women's fashion)
# NOTE: "leggings" removed - they're unisex athletic wear (men's leggings are common at Gymshark, Lululemon, etc.)
GENDER_WORDS_WOMEN = [
    "dress", "skirt", "blouse", "she'd", "her outfit", "her style",
    "women's", "feminine", "bra "  # note space after bra to avoid "brand"
]

# Gender-assumed words for men (when niche isn't explicitly men's fashion)
GENDER_WORDS_MEN = [
    "men's", "his outfit", "his style", "he'd", "masculine", "boyfriend"
]

# Niches that explicitly indicate gender
WOMENS_NICHES = [
    "womenswear", "women's fashion", "womens fashion",
    "women's style", "feminine fashion", "womenswear styling"
]

MENS_NICHES = [
    "menswear", "men's fashion", "mens fashion",
    "men's style", "masculine fashion", "menswear styling"
]


def validate_pitch(pitch: Dict, input_data: Dict) -> ValidationResult:
    """
    Validate generated pitch for forbidden patterns.

    This is the CRITICAL safety net for gender assumptions and AI-speak.
    Returns ValidationResult with pass/fail and reason.
    """
    body = pitch.get("body_plain", "").lower()
    body_html = pitch.get("body_html", "").lower()
    subject = pitch.get("subject", "").lower()

    creator_niche = input_data.get("creator", {}).get("niche", "").lower()
    creator_name = input_data.get("creator", {}).get("first_name", "").lower()

    # 1. Check for forbidden AI phrases
    for phrase in FORBIDDEN_PHRASES:
        if phrase in body:
            return ValidationResult(False, f"forbidden phrase: {phrase}")

    # 2. Check for em dashes (AI writing signature)
    # Check for actual em dash character and HTML entity
    if "\u2014" in body or "\u2014" in body_html:
        return ValidationResult(False, "em dash present")
    if "—" in pitch.get("body_plain", "") or "—" in pitch.get("body_html", ""):
        return ValidationResult(False, "em dash present")
    if "&mdash;" in body_html:
        return ValidationResult(False, "em dash HTML entity present")

    # 3. Subject line rules
    raw_subject = pitch.get("subject", "")
    if raw_subject != raw_subject.lower():
        return ValidationResult(False, "subject not lowercase")

    word_count = len(raw_subject.split())
    if word_count < 3 or word_count > 10:
        return ValidationResult(False, f"subject length {word_count} words (need 3-10)")

    if "!" in raw_subject:
        return ValidationResult(False, "exclamation mark in subject")

    # 4. Gender-assumption check (THE CRITICAL GUARDRAIL)
    is_womens_niche = any(n in creator_niche for n in WOMENS_NICHES)
    is_mens_niche = any(n in creator_niche for n in MENS_NICHES)

    # If niche isn't explicitly gendered, check for gender assumptions
    if not is_womens_niche:
        for word in GENDER_WORDS_WOMEN:
            if word in body:
                return ValidationResult(False, f"gender-assumed word without matching niche: {word}")

    if not is_mens_niche:
        for word in GENDER_WORDS_MEN:
            if word in body:
                return ValidationResult(False, f"gender-assumed word without matching niche: {word}")

    # 5. Hero product hallucination check
    hero_product = input_data.get("brand", {}).get("hero_product")
    if not hero_product:
        # If no hero product provided, check for suspicious specific product names
        # Look for Capitalized Product Names that weren't in input
        brand_name = input_data.get("brand", {}).get("name", "").lower()
        # This is a heuristic - flag if we see product-like patterns
        # Pattern: "the [Capitalized Words]" that's not the brand name
        product_pattern = r'\bthe\s+([A-Z][a-z]+(?:\s+[A-Z][a-z]+)*)\b'
        matches = re.findall(product_pattern, pitch.get("body_plain", ""))
        for match in matches:
            if match.lower() != brand_name and len(match.split()) >= 2:
                # Could be a hallucinated product name
                # Don't fail, just log for monitoring
                pass

    # 6. No portfolio/media-kit phrase in the body
    portfolio_phrases = ["find my portfolio", "my portfolio", "portfolio:", "media kit", "check out my work"]
    for phrase in portfolio_phrases:
        if phrase in body:
            return ValidationResult(False, f"portfolio reference in body: {phrase!r}")

    # 7. Creator first name must appear in close
    if creator_name and creator_name not in body:
        return ValidationResult(False, "creator first name missing from close")

    return ValidationResult(True)


# ============================================================================
# INPUT BUILDER
# ============================================================================

def derive_tier(follower_count: int) -> str:
    """Determine creator tier based on follower count."""
    if follower_count < 10000:
        return "starter"
    elif follower_count <= 100000:
        return "growing"
    else:
        return "established"


def format_follower_count(count: int) -> str:
    """Format follower count for display (e.g., 4200 -> '4.2K')."""
    if count >= 1_000_000:
        return f"{count / 1_000_000:.1f}M"
    elif count >= 1_000:
        return f"{count / 1_000:.1f}K"
    return str(count)


def build_pitch_input(brand: Dict, creator: Dict) -> Dict:
    """
    Build the input payload for Gemini from brand and creator data.

    IMPORTANT: Only pass explicitly declared fields.
    Never pass gender, ethnicity, or any demographic not self-declared.
    """
    # Extract follower count
    followers = (
        creator.get('creator_followers') or
        creator.get('media_kit_followers') or
        creator.get('followers_count') or
        0
    )

    tier = derive_tier(followers)

    # Build brand input
    brand_input = {
        "name": brand.get("brand_name", ""),
        "category": brand.get("category", ""),
    }

    # Optional: brand contact first name for a personalized greeting.
    # Falls back to "{brand} team" in the prompt when not available.
    contact_first = (
        brand.get("contact_first_name")
        or brand.get("contact_name")
        or brand.get("pr_contact_name")
    )
    if contact_first:
        first_token = str(contact_first).strip().split()[0] if str(contact_first).strip() else ""
        if _is_valid_first_name(first_token):
            brand_input["contact_first_name"] = first_token.capitalize()

    # Optional brand fields
    if brand.get("hero_product"):
        brand_input["hero_product"] = brand["hero_product"]
    if brand.get("hero_product_description"):
        brand_input["hero_product_description"] = brand["hero_product_description"]
    if brand.get("aesthetic"):
        brand_input["aesthetic"] = brand["aesthetic"]
    if brand.get("recent_launch"):
        brand_input["recent_launch"] = brand["recent_launch"]

    # Build creator input
    creator_first_name = creator.get("first_name", "").strip()
    if not creator_first_name or not _is_valid_first_name(creator_first_name):
        # Try display_name
        display = creator.get("display_name", "") or ""
        if " " in display:
            first_word = display.split()[0].strip()
            if _is_valid_first_name(first_word):
                creator_first_name = first_word.capitalize()

    # Parse niche (handle JSON string, array, or plain string)
    creator_niches_raw = creator.get('creator_niches') or creator.get('niche')
    niche = _parse_niche(creator_niches_raw)

    # Determine platform
    platform = _get_primary_platform(creator.get('social_links'))

    creator_input = {
        "first_name": creator_first_name or "Creator",
        "niche": niche or "content",
        "platform": platform,
        "follower_count": followers,
    }

    # Social handle from social_links (same source as AIDepth / onboarding)
    social_handle = (creator.get('social_handle') or '').strip().lstrip('@')
    social_platform = (creator.get('social_platform') or creator.get('primary_platform') or '').strip()
    if not social_handle:
        links = creator.get('social_links') or []
        if isinstance(links, str):
            try:
                links = json.loads(links)
            except Exception:
                links = []
        if isinstance(links, list):
            for pref in ('instagram', 'tiktok', 'youtube'):
                for link in links:
                    if not isinstance(link, dict):
                        continue
                    if (link.get('platform') or '').lower() != pref:
                        continue
                    h = (link.get('handle') or link.get('username') or '').strip().lstrip('@')
                    if not h and link.get('url'):
                        m = re.search(r'/@([A-Za-z0-9._]+)', str(link.get('url')))
                        if not m:
                            m = re.search(r'/([A-Za-z0-9._]+)/?$', str(link.get('url')))
                        if m:
                            h = m.group(1)
                    if h:
                        social_handle = h
                        social_platform = pref
                        break
                if social_handle:
                    break
    if social_handle:
        creator_input["social_handle"] = social_handle
        if not creator.get('kit_published'):
            creator_input["include_handle_in_intro"] = True

    # Optional creator fields
    engagement_rate = creator.get("engagement_rate")
    if engagement_rate and tier != "starter":
        creator_input["engagement_rate"] = round(float(engagement_rate), 1)

    content_style = creator.get("content_style") or creator.get("primary_format")
    if content_style:
        creator_input["content_style"] = content_style

    audience_demo = creator.get("audience_description")
    if audience_demo and tier == "established":
        creator_input["audience_demo"] = audience_demo

    # Past collabs (only for growing/established)
    if tier in ("growing", "established"):
        past_collabs = creator.get("past_collabs") or []
        last_collab = creator.get("last_collab_brand_name")
        if last_collab and last_collab not in past_collabs:
            past_collabs = [last_collab] + past_collabs[:2]
        if past_collabs:
            creator_input["past_collabs"] = past_collabs[:3]

    return {
        "brand": brand_input,
        "creator": creator_input,
        "tier": tier
    }


def _is_valid_first_name(name: str) -> bool:
    """Check if name is a real first name, not a username/handle."""
    if not name:
        return False
    name = name.strip()
    if name.islower() and len(name) < 4:
        return False
    if '_' in name or any(c.isdigit() for c in name):
        return False
    if name.lower() in ['admin', 'user', 'creator', 'test', 'social', 'content']:
        return False
    return True


def _parse_niche(niche_raw) -> str:
    """Parse niche from various formats (string, JSON string, array)."""
    if not niche_raw:
        return ""

    if isinstance(niche_raw, str):
        try:
            parsed = json.loads(niche_raw)
            if isinstance(parsed, list) and parsed:
                return str(parsed[0]).strip('"\'[] ')
            return str(parsed).strip('"\'[] ')
        except:
            return niche_raw.strip('"\'[] ')

    if isinstance(niche_raw, list) and niche_raw:
        return str(niche_raw[0]).strip('"\'[] ')

    return ""


def _get_primary_platform(social_links_raw) -> str:
    """Determine primary platform from social links."""
    if not social_links_raw:
        return "Instagram"

    if isinstance(social_links_raw, str):
        try:
            social_links_raw = json.loads(social_links_raw)
        except:
            return "Instagram"

    for link in social_links_raw:
        if isinstance(link, dict):
            plat = link.get('platform', '').lower()
            if plat == 'tiktok':
                return 'TikTok'
            elif plat == 'youtube':
                return 'YouTube'

    return "Instagram"


# ============================================================================
# JSON EXTRACTION HELPER
# ============================================================================

_PORTFOLIO_KEYWORDS = ("http", "portfolio", "media kit", "find my", "check out my work")


def _line_has_portfolio(line: str) -> bool:
    low = line.lower()
    return any(kw in low for kw in _PORTFOLIO_KEYWORDS)


def ensure_pitch_paragraphs(pitch: Optional[Dict]) -> Optional[Dict]:
    """
    Guarantee body_plain has real paragraph breaks for mailto / copy / UI.

    Gemini sometimes returns one long line, or literal '\\n\\n' characters, even when
    body_html is correctly split into <p> tags. Prefer rebuilding from HTML.
    """
    if not pitch or not isinstance(pitch, dict):
        return pitch

    from html import unescape

    plain = pitch.get("body_plain") or ""
    html = pitch.get("body_html") or ""

    # Literal backslash-n sequences → real newlines
    if "\\n" in plain:
        plain = plain.replace("\\r\\n", "\n").replace("\\n", "\n").replace("\\r", "\n")

    plain = plain.replace("\r\n", "\n").replace("\r", "\n")
    plain = re.sub(r"[ \t]+\n", "\n", plain)
    plain = re.sub(r"\n{3,}", "\n\n", plain).strip()

    def _from_html(src: str) -> str:
        parts = re.findall(r"<p[^>]*>(.*?)</p>", src or "", flags=re.I | re.S)
        cleaned = []
        for part in parts:
            text = re.sub(r"<br\s*/?>", "\n", part, flags=re.I)
            text = re.sub(r"<[^>]+>", "", text)
            text = unescape(text)
            text = re.sub(r"[ \t]+\n", "\n", text)
            text = re.sub(r"[ \t]{2,}", " ", text).strip()
            if text:
                cleaned.append(text)
        return "\n\n".join(cleaned).strip()

    if "\n\n" not in plain and html and re.search(r"<p[\s>]", html, re.I):
        rebuilt = _from_html(html)
        if rebuilt:
            plain = rebuilt

    # Last resort: split greeting onto its own line for scannability
    if plain and "\n\n" not in plain:
        m = re.match(r"^(Hi [^,\n]{1,80},)\s+(.+)$", plain, flags=re.S)
        if m:
            rest = m.group(2).strip()
            # Break before common CTA / sign-off phrases when possible
            rest = re.sub(
                r"\s+(Would you be open|If you're open|Happy to|Thanks(?: either way)?,"
                r"|Best,|Warmly,|Cheers,)\b",
                r"\n\n\1",
                rest,
                count=1,
                flags=re.I,
            )
            plain = f"{m.group(1)}\n\n{rest}".strip()

    pitch["body_plain"] = plain
    return pitch


def _strip_portfolio_lines(pitch: Dict) -> Dict:
    """
    Remove portfolio/URL lines the LLM added despite the prompt rule.

    Strategy: any line in body_plain that contains a URL or a portfolio-related
    keyword is dropped entirely. Same for <p> blocks in body_html.
    Plain-text bodies in Gemini output never legitimately contain raw URLs
    (links belong in body_html as <a> tags), so this is safe to do broadly.
    """
    # --- body_plain ---
    plain = pitch.get("body_plain", "")
    if plain:
        lines = plain.split('\n')
        cleaned = [l for l in lines if not _line_has_portfolio(l)]
        result = '\n'.join(cleaned)
        # Collapse 3+ consecutive blank lines into 2
        result = re.sub(r'\n{3,}', '\n\n', result).strip()
        pitch["body_plain"] = result

    # --- body_html ---
    html = pitch.get("body_html", "")
    if html:
        # Remove entire <p>...</p> blocks that contain portfolio keywords or URLs
        html = re.sub(r'<p>.*?</p>', lambda m: '' if _line_has_portfolio(m.group(0)) else m.group(0),
                      html, flags=re.DOTALL | re.IGNORECASE)
        pitch["body_html"] = html.strip()

    # Log if anything was stripped
    orig_plain_len = len(plain) if plain else 0
    new_plain_len = len(pitch.get("body_plain", ""))
    if orig_plain_len != new_plain_len:
        print(f"[GeminiPitch] Stripped {orig_plain_len - new_plain_len} chars of portfolio content from body_plain")

    return pitch


def _extract_json(text: str) -> Optional[Dict]:
    """
    Robustly extract a JSON object from a model response.

    Gemini 2.5-flash may prefix the JSON with thinking tokens or markdown
    fences even when response_mime_type='application/json' is set.
    This helper strips common wrappers before parsing.
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
        except json.JSONDecodeError:
            pass

    raise ValueError(f"Could not extract valid JSON from response (first 120 chars): {text[:120]!r}")


# ============================================================================
# GEMINI CLIENT
# ============================================================================

class GeminiPitchGenerator:
    """
    Gemini 2.0 Flash pitch generator with retry logic and validation.
    """

    def __init__(self):
        self.api_key = os.getenv('GEMINI_API_KEY')
        self.enabled = bool(self.api_key) and GEMINI_AVAILABLE
        self.use_new_sdk = GEMINI_NEW_SDK if GEMINI_AVAILABLE else False

        # Debug logging
        print(f"[GeminiPitch] Init: API_KEY={'set' if self.api_key else 'NOT SET'}, GEMINI_AVAILABLE={GEMINI_AVAILABLE}, NEW_SDK={self.use_new_sdk}, enabled={self.enabled}")

        if self.enabled:
            if self.use_new_sdk:
                # New google-genai SDK
                self.client = genai.Client(api_key=self.api_key)
                self.model = None  # Model is specified per-call in new SDK
            else:
                # Deprecated google-generativeai SDK
                genai.configure(api_key=self.api_key)
                self.model = genai.GenerativeModel(
                    model_name=GEMINI_MODEL,
                    system_instruction=PITCH_SYSTEM_PROMPT
                )
                self.client = None
            print(f"[GeminiPitch] Model initialized: {GEMINI_MODEL}")
        else:
            self.model = None
            self.client = None
            print("[GeminiPitch] Model NOT initialized - will use template fallback")

    def generate(self, brand: Dict, creator: Dict, template_fallback_fn) -> PitchResult:
        """
        Generate a pitch using Gemini with validation and fallback.

        Args:
            brand: Brand data dict
            creator: Creator data dict
            template_fallback_fn: Function to call for template-based fallback

        Returns:
            PitchResult with generated pitch or fallback
        """
        # Check if Gemini is available
        if not self.enabled:
            print("[GeminiPitch] Gemini not configured, using template fallback")
            return self._run_template_fallback(template_fallback_fn, brand, creator, "gemini_not_configured")

        # Validate required inputs
        if not brand.get("brand_name"):
            return PitchResult(
                success=False,
                error="Brand data incomplete - cannot generate pitch"
            )

        if not creator.get("creator_niches") and not creator.get("niche"):
            return PitchResult(
                success=False,
                error="Creator niche required - please set niche in profile"
            )

        followers = (
            creator.get('creator_followers') or
            creator.get('media_kit_followers') or
            creator.get('followers_count') or
            0
        )
        if not followers:
            return PitchResult(
                success=False,
                error="Creator follower count required - please update profile"
            )

        # Build input payload
        input_payload = build_pitch_input(brand, creator)

        # Try generation with retry
        last_error = None
        for attempt in range(MAX_RETRIES):
            try:
                result = self._call_gemini(input_payload)

                if result:
                    # Validate the result
                    validation = validate_pitch(result, input_payload)

                    if validation.passed:
                        print(f"[GeminiPitch] Success on attempt {attempt + 1}")
                        return PitchResult(
                            success=True,
                            subject=result.get("subject"),
                            body_plain=result.get("body_plain"),
                            body_html=result.get("body_html"),
                            reasoning=result.get("reasoning"),
                            source="gemini"
                        )
                    else:
                        print(f"[GeminiPitch] Validation failed (attempt {attempt + 1}): {validation.reason}")
                        last_error = f"validation_failed: {validation.reason}"
                else:
                    last_error = "empty_response"

            except Exception as e:
                print(f"[GeminiPitch] Error on attempt {attempt + 1}: {str(e)}")
                last_error = str(e)

                # Rate limit - wait before retry
                if "429" in str(e):
                    time.sleep(2)
                else:
                    time.sleep(0.25 * (attempt + 1))  # Exponential backoff

        # All attempts failed - use template fallback
        print(f"[GeminiPitch] All attempts failed, using template fallback. Last error: {last_error}")
        return self._run_template_fallback(template_fallback_fn, brand, creator, last_error)

    def _call_gemini(self, input_payload: Dict) -> Optional[Dict]:
        """Make the actual Gemini API call."""
        if self.use_new_sdk:
            # New google-genai SDK
            from google.genai import types

            # ThinkingConfig: disable thinking to prevent reasoning tokens from
            # contaminating the JSON response (gemini-2.5-flash thinks by default)
            try:
                thinking_cfg = types.ThinkingConfig(thinking_budget=0)
            except Exception:
                thinking_cfg = None

            config_kwargs = dict(
                system_instruction=PITCH_SYSTEM_PROMPT,
                temperature=TEMPERATURE,
                top_p=TOP_P,
                max_output_tokens=MAX_OUTPUT_TOKENS,
                response_mime_type="application/json",
            )
            if thinking_cfg is not None:
                config_kwargs["thinking_config"] = thinking_cfg

            response = self.client.models.generate_content(
                model=GEMINI_MODEL,
                contents=json.dumps(input_payload),
                config=types.GenerateContentConfig(**config_kwargs),
            )

            if response and response.text:
                parsed = _extract_json(response.text)
                if not parsed:
                    return None
                return ensure_pitch_paragraphs(_strip_portfolio_lines(parsed))
        else:
            # Deprecated google-generativeai SDK
            generation_config = {
                "temperature": TEMPERATURE,
                "top_p": TOP_P,
                "max_output_tokens": MAX_OUTPUT_TOKENS,
                "response_mime_type": "application/json",
            }

            response = self.model.generate_content(
                json.dumps(input_payload),
                generation_config=generation_config,
            )

            if response and response.text:
                parsed = _extract_json(response.text)
                if not parsed:
                    return None
                return ensure_pitch_paragraphs(_strip_portfolio_lines(parsed))

        return None

    def _run_template_fallback(
        self,
        fallback_fn,
        brand: Dict,
        creator: Dict,
        error_reason: str
    ) -> PitchResult:
        """Run the template-based fallback and wrap result."""
        try:
            template_result = fallback_fn(brand, creator)

            # Log fallback for monitoring
            print(f"[GeminiPitch] Template fallback used. Reason: {error_reason}")

            return PitchResult(
                success=True,
                subject=template_result.get("subject"),
                body_plain=template_result.get("body"),
                body_html=None,  # Template doesn't generate HTML
                source="template_fallback"
            )
        except Exception as e:
            print(f"[GeminiPitch] Template fallback also failed: {str(e)}")
            return PitchResult(
                success=False,
                error=f"Both Gemini and template failed: {str(e)}"
            )


# ============================================================================
# SINGLETON INSTANCE
# ============================================================================
# NOTE: Portfolio link appending is handled in pr_crm_routes.py to avoid
# circular imports. The sending layer appends the link after LLM generation.

# Global instance - initialized on first import
_generator_instance = None


def get_generator() -> GeminiPitchGenerator:
    """Get or create the global generator instance."""
    global _generator_instance
    if _generator_instance is None:
        _generator_instance = GeminiPitchGenerator()
    return _generator_instance
