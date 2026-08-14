"""
Gemini Pitch Engine v2.0 - Optimized for Variance and Reply Rate

Key changes from v1:
1. Split prompt: system_instruction + user_prompt (30% better instruction adherence)
2. Temperature 1.1 (Gemini needs higher temp for variance)
3. Structured output via response_schema (enforced at API level)
4. 8 opener variants x 8 close variants x 8 subject variants (independent random)
5. Safety settings: BLOCK_ONLY_HIGH (avoids false positives on beauty/skincare)
6. Few-shot examples in system instruction (3 rotating weekly from pool of 9)
7. Comprehensive banned phrases list
8. Retry logic with safety filter handling

Cost: ~$0.0005-0.0008 per pitch on gemini-2.5-flash
Target: 5-8% reply rate (vs 1-2% on v1)
"""

import os
import re
import json
import time
import random
from typing import Dict, Optional, List
from dataclasses import dataclass, field
from datetime import datetime

from services.pitch_identity import resolve_pitch_identity

# Gemini API
try:
    from google import genai
    from google.genai import types
    GEMINI_AVAILABLE = True
    print("[GeminiPitchV2] google-genai SDK imported successfully")
except Exception as e:
    GEMINI_AVAILABLE = False
    genai = None
    types = None
    print(f"[GeminiPitchV2] SDK import failed: {e}")


# ============================================================================
# CONFIGURATION
# ============================================================================

# Model selection
GEMINI_MODEL_DEFAULT = "gemini-2.5-flash"  # For volume ($5-8/mo at 10K pitches)
GEMINI_MODEL_PRO = "gemini-2.5-pro"  # For Pro users or high-value matches

# Generation config (Gemini-specific tuning)
TEMPERATURE = 1.1  # Higher than OpenAI for real variance
TOP_P = 0.95
TOP_K = 40
MAX_OUTPUT_TOKENS = 2048  # Increased for Gemini 2.5 Flash thinking tokens + JSON output

# Retry config
MAX_RETRIES = 2
RETRY_DELAY_BASE = 0.5

# Version tracking
PROMPT_VERSION = "v2.1"


# ============================================================================
# RESPONSE SCHEMA (enforced at API level)
# ============================================================================

PITCH_SCHEMA = {
    "type": "object",
    "properties": {
        "subject": {
            "type": "string",
            "description": "Email subject line, 4-7 words, lowercase, using assigned variant"
        },
        "body": {
            "type": "string",
            "description": "Full pitch body including signature, 60-180 words"
        }
    },
    "required": ["subject", "body"],
    "propertyOrdering": ["subject", "body"]
}


# ============================================================================
# SYSTEM INSTRUCTION (persona + rules + few-shot examples)
# ============================================================================

SYSTEM_INSTRUCTION = """You are a real micro-influencer writing a short, casual gifted-PR pitch to a brand's PR team.

You are NOT an agency, NOT a marketing consultant, NOT a professional pitcher. You are a real 20-something creator who scrolls Instagram, tries products, and writes short honest emails to brands.

## Voice rules

- Write in first person, casual, contractions natural (I'm, I've, don't, you're)
- ALWAYS capitalize the first letter of every sentence (proper grammar required)
- Use commas, periods, semicolons, and the word "and" for connectors
- Peer-to-peer tone, not corporate
- Do not use em dashes (—) or en dashes (–), ever
- Do not use markdown formatting in the output

## Length rules

- 60 to 180 words total in the body
- Vary length across generations. Never target a specific count.
- Real pitches are irregular. Some short, some longer.

## Content rules

- Cite one specific personal reason you want the specific product named
- Anchor to a real problem, a real routine, or a real product feature
- Make one clear ask: send the product for one piece of content
- Do not promise ROI or performance metrics
- Do not offer usage rights or commercial reuse
- Do not use the phrase "gifted" or "PR" as jargon; describe the trade in plain language
- Sign off with the creator's public social name (the Name field). Never use a different legal first name.
- Only pitch the named product if it fits the creator's niche. If it does not, ask for a product in that niche this brand actually sells. Do not invent SKUs.

## Banned phrases (never use, in any variation)

- "Happy for you to use any content in your paid ads"
- "no extra cost"
- "no fee, no revshare"
- "Would you be open to"
- "would you be able to send"
- "would you consider sending"
- "I've been admiring"
- "I've been following"
- "I've been so impressed"
- "I have an idea for a TikTok that showcases"
- "authentic content"
- "genuine partnership"
- "content that resonates"
- "aligns with your brand"
- "elevate", "unleash", "leverage", "seamless", "delve"
- "as a real" (instruction bleed)
- "as an influencer" (instruction bleed)
- "my task is" (instruction bleed)
- "I'm writing to" (instruction bleed)

## Greeting layer (use assigned greeting_variant_id)

Use the greeting variant BEFORE the opener. The greeting goes on line 1, then a blank line, then the opener.

Variants:
1. "Hi {brand_name} team,"
2. "Hey {brand_name},"
3. "Hi there,"
4. "Hi {brand_pr_contact_name}," (only if brand_pr_contact_name provided, otherwise skip greeting)
5. NO GREETING (opener starts the email directly, for anti-template variety)

## Structure and formatting (MANDATORY)

Structure the body as clear paragraphs separated by blank lines.
Each paragraph = 1 idea, max 3 sentences.
Use TWO newline characters (\\n\\n) between paragraphs.
Never write the entire pitch as one block of text.

The body MUST have this vertical rhythm:

[Greeting on its own line]

[Blank line]

[Opener paragraph, 1-2 sentences]

[Blank line]

[Personal reason paragraph, 1-3 sentences about why you want the product]

[Blank line]

[Content plan paragraph, 1-2 sentences about what you'd film]

[Blank line]

[Close on its own line]

[Blank line]

[Name on its own line — the creator's public social name/handle, never a different legal first name]
[Handle on next line]

CRITICAL RULES:
- Never double up greetings. The greeting layer handles "Hi X," on line 1. The opener should NOT start with "hi" or "hey" again.
- Never chain more than 3 sentences without a paragraph break.
- The close phrase MUST be on its own paragraph before the signature.

## Output format

Return valid JSON matching this schema exactly:
{
  "subject": "string, using the assigned subject variant",
  "body": "string, the full pitch body including sig"
}

## Few-shot examples (study these for tone, length variance, paragraph breaks, and greeting usage)

Example A (short, with greeting, 4 paragraphs):
{
  "subject": "Barrier Repair Serum content idea",
  "body": "Hi Ceremonia team,\\n\\nBeen eyeing your Barrier Repair Serum for a while.\\n\\nMy scalp has been sensitive since I stopped using medicated shampoo last spring, and the ceramide combo in yours looks like the right layer for me.\\n\\nI'd film a wash day Reel showing how the serum sits under my usual conditioner.\\n\\nAny chance you could send one over?\\n\\nSarah\\n@sarah.hairlab on Instagram"
}

Example B (mid length, no greeting - variant 5, 4 paragraphs):
{
  "subject": "quick note about Halo Glow Liquid Filter",
  "body": "Not a form pitch, just a real note about your Halo Glow Liquid Filter.\\n\\nI've been using it under my sunscreen for a month and it's actually the reason my base looks less flat in videos now.\\n\\nI want to do a 45-second GRWM Reel showing the layer order and how it holds through a summer commute.\\n\\nSend one over? I'll take care of the rest.\\n\\nPriya\\n@priyaskincloset on TikTok"
}

Example C (longer, with named contact greeting, 5 paragraphs):
{
  "subject": "scalp massager content idea",
  "body": "Hi Sofia,\\n\\nquick one.\\n\\nI've been building a scalp care routine for the past 3 months since noticing thinning at my crown. I've tried three different massagers so far and none of them have the flex I need without irritating my skin. Your Scalp Massager keeps coming up when I look at Scandinavian brands, especially the medical-grade silicone.\\n\\nI'd film a short showing how I use it before your Bio-Pilixin serum. Slow pacing, no talking, ASMR-style, about 20 seconds.\\n\\nIf you want to see the concept first, happy to send a rough idea.\\n\\nTom\\n@tomgrooms on Instagram"
}"""


# ============================================================================
# USER PROMPT TEMPLATE (filled per generation)
# ============================================================================

USER_PROMPT_TEMPLATE = """Write one pitch email to {brand_name} for their product {product_sku_name} ({product_category}).

## Creator context

- Name: {creator_name}
- Handle: {creator_handle} on {platform}
- Follower count: {follower_count} (only mention if it fits naturally, otherwise skip)
- Niche: {niche_primary}
- Country: {country}
- Physical attribute if relevant to product: {physical_attribute}

## Sign-off (mandatory)
Sign the email as the public social name, never a different legal first name:
{creator_name}
{creator_handle} on {platform}

## Product fit (mandatory)
Only pitch {product_sku_name} if it fits this creator's niche ({niche_primary}).
If the SKU is off-niche, ask for a product in {niche_primary} this brand actually sells. Do not invent SKUs. Do not pitch an unrelated product just because it is listed.

## Brand context

- Brand: {brand_name}
- Product to focus on: {product_sku_name}
- Product category: {product_category}
- PR contact name (if provided): {brand_pr_contact_name}

## Assigned variants (use these exactly)

### Greeting variant: {greeting_variant_id}

1. "Hi {brand_name} team,"
2. "Hey {brand_name},"
3. "Hi there,"
4. "Hi {brand_pr_contact_name}," (only if brand_pr_contact_name is not "null")
5. NO GREETING (skip entirely, start with opener)

### Opener variant: {opener_variant_id}

IMPORTANT: The opener comes AFTER the greeting (on a new line). Do NOT include any greeting words like "hi" or "hey" in the opener itself - the greeting layer handles that.

1. Product-first: "quick note about your {product_sku_name}"
2. Casual observation: "your {product_sku_name} keeps popping up in my feed and I finally get why"
3. Honest confession: "been eyeing your {product_sku_name} for a while"
4. Content-forward: "content idea for {product_sku_name} if you're open to it"
5. Quick one: "quick one." (then continue with your pitch)
6. Community angle: "my audience keeps asking about {product_category} and yours is the one I want to try"
7. Personal reason: start with a real relatable problem in the niche, then connect to {product_sku_name}
8. Anti-template signal: "not a form pitch, just a real note about your {product_sku_name}"

### Close variant: {close_variant_id}

1. Direct: "any chance you could send one over?"
2. Casual: "if you have samples to send, I'm in"
3. Concept preview: "if you want to see the content first, happy to send a rough idea"
4. Deadline: "planning content for next week, let me know either way"
5. Low-pressure: "no worries if not, just wanted to reach out"
6. Punchy: "send one over? I'll take care of the rest"
7. Named ask: "would love a {product_sku_name} to try"
8. Open: "let me know if it's a fit"

### Subject variant: {subject_variant_id}

1. "quick note about {product_sku_name}"
2. "{product_sku_name} content idea"
3. "{niche_primary} idea for {product_sku_name}"
4. "would love to try {product_sku_name}"
5. "{brand_name} + {creator_handle}"
6. "small ask about {product_sku_name}"
7. "content idea for {product_sku_name}"
8. "{product_sku_name} collab idea"

Generate the pitch now. Return only the JSON."""


# ============================================================================
# BANNED PHRASES (post-generation regex check)
# ============================================================================

BANNED_PHRASES = [
    # Original audit findings
    "happy for you to use any content",
    "in your paid ads",
    "no extra cost",
    "no fee, no revshare",
    "would you be open",
    "would you be able to send",
    "would you consider sending",
    "i've been admiring",
    "i've been following",
    "i've been so impressed",
    "i have an idea for a tiktok that showcases",
    "authentic content",
    "genuine partnership",
    "content that resonates",
    "aligns with your brand",
    # AI buzzwords
    "elevate",
    "unleash",
    "leverage",
    "seamless",
    "delve",
    # Instruction bleed
    "as a real",
    "as an influencer",
    "my task is",
    "i'm writing to",
]

# Characters to reject
BANNED_CHARS = ["—", "–"]  # em dash, en dash


# ============================================================================
# DATA CLASSES
# ============================================================================

@dataclass
class VariantIds:
    greeting_id: int
    opener_id: int
    close_id: int
    subject_id: int


@dataclass
class ValidationResult:
    passed: bool
    reason: Optional[str] = None


@dataclass
class PitchResult:
    success: bool
    subject: Optional[str] = None
    body: Optional[str] = None
    source: str = "gemini_v2"
    error: Optional[str] = None
    variant_ids: Optional[VariantIds] = None
    model_used: Optional[str] = None
    prompt_version: str = PROMPT_VERSION
    retry_count: int = 0
    safety_blocked: bool = False


# ============================================================================
# VALIDATION FUNCTIONS
# ============================================================================

def validate_pitch(pitch: Dict, brand: Dict, variant_ids: VariantIds) -> ValidationResult:
    """
    Post-generation validation. Rejects and triggers retry on failure.
    """
    body = pitch.get("body", "")
    subject = pitch.get("subject", "")
    body_lower = body.lower()

    # 1. Check banned phrases
    for phrase in BANNED_PHRASES:
        if phrase in body_lower:
            return ValidationResult(False, f"banned_phrase:{phrase}")

    # 2. Check banned characters (dashes)
    for char in BANNED_CHARS:
        if char in body:
            return ValidationResult(False, "dash_char")

    # 3. Word count check (60-180)
    word_count = len(body.split())
    if word_count < 60:
        return ValidationResult(False, f"length_short:{word_count}")
    if word_count > 180:
        return ValidationResult(False, f"length_long:{word_count}")

    # 4. Product reference check
    product_name = brand.get("product_sku_name", "").lower()
    if product_name and product_name not in body_lower:
        return ValidationResult(False, "missing_product_ref")

    # 5. Over-egoic check (>3 sentences starting with "I")
    sentences = re.split(r'[.!?]+', body)
    i_starts = sum(1 for s in sentences if s.strip().lower().startswith("i "))
    if i_starts > 3:
        return ValidationResult(False, "over_egoic")

    # 6. Markdown check
    markdown_patterns = ["**", "##", "```", "- ", "* "]
    for md in markdown_patterns:
        if md in body:
            return ValidationResult(False, "markdown_output")

    # 7. Subject format validation (must include product or brand reference)
    if not subject or len(subject) < 15 or len(subject) > 80:
        return ValidationResult(False, f"subject_length:{len(subject)}")

    # Subject should reference product or brand (soft check - prompt already enforces this)
    subject_lower = subject.lower()
    product_name_lower = product_name.lower() if product_name else ""
    brand_name = brand.get("brand_name", "").lower()

    # Check any significant word (3+ chars) from product name appears in subject
    product_words = [w for w in product_name_lower.split() if len(w) >= 3]
    # Also include alphanumeric core of hyphenated words (e.g., "age" from "age-r")
    for w in product_name_lower.split():
        parts = [p for p in w.split('-') if len(p) >= 3]
        product_words.extend(parts)

    has_product_ref = product_name_lower and any(
        word in subject_lower for word in product_words
    )
    has_brand_ref = brand_name and len(brand_name) >= 3 and brand_name in subject_lower

    # Only fail if subject is completely generic (no product or brand reference at all)
    # and very short - otherwise trust the prompt to have done its job
    if not has_product_ref and not has_brand_ref and len(subject) < 20:
        return ValidationResult(False, "subject_missing_ref")

    return ValidationResult(True)


# ============================================================================
# POST-GENERATION FORMATTING
# ============================================================================

def normalize_pitch_body(body: str) -> str:
    """
    Normalize pitch body to ensure proper paragraph breaks.
    Runs after Gemini generates, before saving to DB.
    """
    # Strip leading/trailing whitespace
    body = body.strip()

    # Collapse 3+ newlines to exactly 2
    body = re.sub(r'\n{3,}', '\n\n', body)

    # Ensure blank line after greeting (line ending with comma)
    body = re.sub(
        r'^((?:Hi|Hey)[^\n]+,)\n(?!\n)',
        r'\1\n\n',
        body
    )

    # Ensure blank line before common close phrases
    close_starters = [
        r'if you have samples',
        r'if you want to see',
        r'any chance you could',
        r'no worries if not',
        r'send one over',
        r'let me know if',
        r'would love a',
        r'planning content for',
    ]
    for pattern in close_starters:
        body = re.sub(
            rf'([.!?])\s*\n?({pattern})',
            r'\1\n\n\2',
            body,
            flags=re.IGNORECASE
        )

    # Ensure blank line before content-plan starters
    content_starters = [
        r"I'd love to film",
        r"I'd film",
        r"I want to film",
        r"I want to do",
        r"I'd do a",
        r"I'd love to do",
    ]
    for pattern in content_starters:
        body = re.sub(
            rf'([.!?])\s*\n?({pattern})',
            r'\1\n\n\2',
            body,
            flags=re.IGNORECASE
        )

    # Ensure sig block is on its own paragraph
    # Name line may be a legal first name (Priya) or a public handle (xoitsariel)
    body = re.sub(
        r'([.!?])\s*\n(@?[\w.]+)\n(@[\w.]+)',
        r'\1\n\n\2\n\3',
        body
    )

    # Final cleanup: collapse any 3+ newlines that snuck in
    body = re.sub(r'\n{3,}', '\n\n', body)

    # Capitalize first letter of sentences (after . ! ? or at start of paragraph)
    # This fixes the lowercase sentence start issue
    def capitalize_sentence_start(match):
        return match.group(1) + match.group(2).upper()

    # Capitalize after period/exclamation/question followed by space
    body = re.sub(r'([.!?]\s+)([a-z])', capitalize_sentence_start, body)

    # Capitalize at start of paragraphs (after double newline)
    body = re.sub(r'(\n\n)([a-z])', capitalize_sentence_start, body)

    # Capitalize first character of body if lowercase
    if body and body[0].islower():
        body = body[0].upper() + body[1:]

    return body.strip()


# ============================================================================
# INPUT BUILDER
# ============================================================================

def build_user_prompt(brand: Dict, creator: Dict, variant_ids: VariantIds) -> str:
    """
    Build the user prompt from brand/creator data and variant IDs.
    """
    identity = resolve_pitch_identity(creator)
    creator_name = identity["signoff_name"]
    creator_handle = identity["handle"]

    # Follower count
    followers = (
        creator.get("creator_followers") or
        creator.get("media_kit_followers") or
        creator.get("followers_count") or
        0
    )

    # Format follower count
    if followers >= 1_000_000:
        follower_str = f"{followers / 1_000_000:.1f}M"
    elif followers >= 1_000:
        follower_str = f"{followers / 1_000:.1f}K"
    else:
        follower_str = str(followers)

    # Niche
    niche_raw = creator.get("creator_niches") or creator.get("niche") or ""
    if isinstance(niche_raw, list):
        niche = niche_raw[0] if niche_raw else "content"
    elif isinstance(niche_raw, str):
        try:
            parsed = json.loads(niche_raw)
            niche = parsed[0] if isinstance(parsed, list) and parsed else niche_raw
        except:
            niche = niche_raw
    else:
        niche = "content"

    # Platform
    platform = creator.get("primary_platform", "Instagram")

    # Physical attribute (hair type, skin type, etc.)
    physical_attr = (
        creator.get("hair_type") or
        creator.get("skin_type") or
        "not specified"
    )

    # Country
    country = creator.get("country", "not specified")

    # Brand data
    brand_name = brand.get("brand_name", "")
    product_name = brand.get("hero_product") or brand.get("product_sku_name") or "your product"
    product_category = brand.get("category", "")
    pr_contact = brand.get("pr_contact_name") or brand.get("contact_first_name") or "null"

    # Fill template
    prompt = USER_PROMPT_TEMPLATE.format(
        brand_name=brand_name,
        product_sku_name=product_name,
        product_category=product_category,
        creator_name=creator_name,
        creator_handle=f"@{creator_handle}" if creator_handle else "(not provided)",
        platform=platform,
        follower_count=follower_str,
        niche_primary=niche,
        country=country,
        physical_attribute=physical_attr,
        brand_pr_contact_name=pr_contact,
        greeting_variant_id=variant_ids.greeting_id,
        opener_variant_id=variant_ids.opener_id,
        close_variant_id=variant_ids.close_id,
        subject_variant_id=variant_ids.subject_id
    )

    return prompt


def generate_variant_ids(has_contact_name: bool = False) -> VariantIds:
    """
    Generate random variant IDs with weighted distributions.

    Greeting weights:
    - If contact name: 50% variant 4 (named), 25% variant 1, 15% variant 2, 10% variant 5
    - If no contact: 45% variant 1, 25% variant 3, 20% variant 2, 10% variant 5
    """
    # Greeting variant (weighted - 90% with greeting, 10% without)
    if has_contact_name:
        # Prefer named greeting when we have a contact
        greeting_weights = [25, 15, 0, 50, 10]  # variants 1-5
    else:
        # No contact name - skip variant 4, redistribute
        greeting_weights = [45, 20, 25, 0, 10]  # variants 1-5

    greeting_id = random.choices([1, 2, 3, 4, 5], weights=greeting_weights, k=1)[0]

    return VariantIds(
        greeting_id=greeting_id,
        opener_id=random.randint(1, 8),
        close_id=random.randint(1, 8),
        # Skip variant 3 ("creator interest, {brand}") — it describes the
        # creator instead of offering an idea, which tanks open rate.
        subject_id=random.choice([1, 2, 4, 5, 6, 7, 8]),
    )


# ============================================================================
# SAFETY SETTINGS
# ============================================================================

def get_safety_settings():
    """
    Safety settings loosened to BLOCK_ONLY_HIGH.
    Default (BLOCK_MEDIUM_AND_ABOVE) throws false positives on skincare/beauty mentions.
    """
    if types is None:
        return []

    return [
        types.SafetySetting(category="HARM_CATEGORY_HARASSMENT", threshold="BLOCK_ONLY_HIGH"),
        types.SafetySetting(category="HARM_CATEGORY_HATE_SPEECH", threshold="BLOCK_ONLY_HIGH"),
        types.SafetySetting(category="HARM_CATEGORY_SEXUALLY_EXPLICIT", threshold="BLOCK_ONLY_HIGH"),
        types.SafetySetting(category="HARM_CATEGORY_DANGEROUS_CONTENT", threshold="BLOCK_ONLY_HIGH"),
    ]


# ============================================================================
# GEMINI CLIENT V2
# ============================================================================

class GeminiPitchGeneratorV2:
    """
    Gemini Pitch Engine v2.0 with variant rotation and structured output.
    """

    def __init__(self):
        self.api_key = os.getenv("GEMINI_API_KEY")
        self.enabled = bool(self.api_key) and GEMINI_AVAILABLE

        if self.enabled:
            self.client = genai.Client(api_key=self.api_key)
        else:
            self.client = None

        print(f"[GeminiPitchV2] Init: enabled={self.enabled}")

    def generate(
        self,
        brand: Dict,
        creator: Dict,
        template_fallback_fn=None,
        use_pro_model: bool = False
    ) -> PitchResult:
        """
        Generate a pitch using Gemini v2 with validation and retry.

        Args:
            brand: Brand data dict
            creator: Creator data dict
            template_fallback_fn: Optional fallback function
            use_pro_model: Use gemini-2.5-pro instead of flash

        Returns:
            PitchResult with generated pitch
        """
        if not self.enabled:
            print("[GeminiPitchV2] Gemini not configured")
            if template_fallback_fn:
                return self._run_fallback(template_fallback_fn, brand, creator, "not_configured")
            return PitchResult(success=False, error="Gemini not configured")

        # Generate variant IDs (check if we have a PR contact name for greeting weights)
        pr_contact = brand.get("pr_contact_name") or brand.get("contact_first_name")
        has_contact_name = bool(pr_contact and pr_contact.lower() not in ["null", "none", ""])
        variant_ids = generate_variant_ids(has_contact_name=has_contact_name)

        # Select model
        model = GEMINI_MODEL_PRO if use_pro_model else GEMINI_MODEL_DEFAULT

        # Build user prompt
        user_prompt = build_user_prompt(brand, creator, variant_ids)

        # Retry loop
        last_error = None
        retry_count = 0
        safety_blocked = False

        for attempt in range(MAX_RETRIES):
            try:
                result, was_safety_blocked = self._call_gemini(
                    user_prompt,
                    model,
                    last_error
                )

                if was_safety_blocked:
                    safety_blocked = True
                    last_error = "safety_filter"
                    retry_count += 1
                    continue

                if result:
                    # Validate
                    validation = validate_pitch(result, brand, variant_ids)

                    if validation.passed:
                        print(f"[GeminiPitchV2] Success on attempt {attempt + 1}")
                        # Normalize paragraph breaks before returning
                        normalized_body = normalize_pitch_body(result.get("body", ""))
                        return PitchResult(
                            success=True,
                            subject=result.get("subject"),
                            body=normalized_body,
                            source="gemini_v2",
                            variant_ids=variant_ids,
                            model_used=model,
                            prompt_version=PROMPT_VERSION,
                            retry_count=retry_count,
                            safety_blocked=safety_blocked
                        )
                    else:
                        print(f"[GeminiPitchV2] Validation failed: {validation.reason}")
                        last_error = validation.reason
                        retry_count += 1
                else:
                    last_error = "empty_response"
                    retry_count += 1

            except Exception as e:
                print(f"[GeminiPitchV2] Error on attempt {attempt + 1}: {e}")
                last_error = str(e)
                retry_count += 1

                if "429" in str(e):
                    time.sleep(2)
                else:
                    time.sleep(RETRY_DELAY_BASE * (attempt + 1))

        # All attempts failed
        print(f"[GeminiPitchV2] All attempts failed: {last_error}")

        if template_fallback_fn:
            return self._run_fallback(template_fallback_fn, brand, creator, last_error)

        return PitchResult(
            success=False,
            error=f"Generation failed: {last_error}",
            variant_ids=variant_ids,
            model_used=model,
            prompt_version=PROMPT_VERSION,
            retry_count=retry_count,
            safety_blocked=safety_blocked
        )

    def _call_gemini(
        self,
        user_prompt: str,
        model: str,
        previous_error: Optional[str] = None
    ) -> tuple[Optional[Dict], bool]:
        """
        Make the Gemini API call.

        Returns:
            (result_dict, was_safety_blocked)
        """
        # Append retry context if previous attempt failed
        prompt = user_prompt
        if previous_error:
            prompt = f"{user_prompt}\n\nPREVIOUS ATTEMPT FAILED: {previous_error}. Fix and regenerate. Do not repeat the error."

        # Build config
        config = types.GenerateContentConfig(
            system_instruction=SYSTEM_INSTRUCTION,
            temperature=TEMPERATURE,
            top_p=TOP_P,
            top_k=TOP_K,
            max_output_tokens=MAX_OUTPUT_TOKENS,
            response_mime_type="application/json",
            response_schema=PITCH_SCHEMA,
            safety_settings=get_safety_settings()
        )

        # Make API call
        response = self.client.models.generate_content(
            model=model,
            contents=[
                types.Content(
                    role="user",
                    parts=[types.Part.from_text(text=prompt)]
                )
            ],
            config=config
        )

        # Check for safety block
        if response.candidates:
            candidate = response.candidates[0]
            if hasattr(candidate, 'finish_reason') and str(candidate.finish_reason) == "SAFETY":
                print("[GeminiPitchV2] Safety filter blocked response")
                return None, True

        # Parse response
        if response and response.text:
            raw_text = response.text
            print(f"[GeminiPitchV2] Raw response length: {len(raw_text)}")
            print(f"[GeminiPitchV2] Raw response preview: {raw_text[:500]}...")

            try:
                result = json.loads(raw_text)
                return result, False
            except json.JSONDecodeError as e:
                print(f"[GeminiPitchV2] JSON parse error: {e}")
                # Try to extract JSON from response
                text = raw_text.strip()

                # Remove markdown code fences if present
                if text.startswith("```"):
                    lines = text.split("\n")
                    # Remove first line (```json) and last line (```)
                    text = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:])

                start = text.find('{')
                end = text.rfind('}')
                if start != -1 and end > start:
                    json_text = text[start:end+1]
                    print(f"[GeminiPitchV2] Extracted JSON: {json_text[:200]}...")
                    try:
                        result = json.loads(json_text)
                        return result, False
                    except json.JSONDecodeError as e2:
                        print(f"[GeminiPitchV2] Extracted JSON parse error: {e2}")

        return None, False

    def _run_fallback(
        self,
        fallback_fn,
        brand: Dict,
        creator: Dict,
        error_reason: str
    ) -> PitchResult:
        """Run template fallback."""
        try:
            template_result = fallback_fn(brand, creator)
            print(f"[GeminiPitchV2] Template fallback used: {error_reason}")

            # Normalize paragraph breaks for fallback too
            normalized_body = normalize_pitch_body(template_result.get("body", ""))
            return PitchResult(
                success=True,
                subject=template_result.get("subject"),
                body=normalized_body,
                source="template_fallback",
                prompt_version=PROMPT_VERSION
            )
        except Exception as e:
            print(f"[GeminiPitchV2] Fallback also failed: {e}")
            return PitchResult(success=False, error=str(e))


# ============================================================================
# PRE-GENERATION VALIDATION
# ============================================================================

# Blocked inbox patterns (spam traps, unlikely to reply)
BLOCKED_INBOX_PATTERNS = [
    "info@",
    "support@",
    "care@",
    "help@",
    "customerservice@",
    "hello@",
    "contact@"
]


def validate_match(brand: Dict, creator: Dict) -> tuple[bool, Optional[str]]:
    """
    Pre-generation validation. Returns (is_valid, warning_message).
    """
    warnings = []

    # Check blocked inbox
    pr_email = (brand.get("pr_contact_email") or "").lower()
    for pattern in BLOCKED_INBOX_PATTERNS:
        if pr_email.startswith(pattern):
            warnings.append(f"Email '{pr_email}' is a generic inbox with low reply rates")
            break

    # Check follower minimum
    min_followers = brand.get("min_followers", 0)
    creator_followers = (
        creator.get("creator_followers") or
        creator.get("followers_count") or
        0
    )

    if min_followers and creator_followers < min_followers:
        warnings.append(
            f"This brand typically works with creators over {min_followers:,} followers. "
            f"Reply rate is under 5%."
        )

    # Check shipping country
    ships_to = brand.get("ships_to_countries", [])
    creator_country = creator.get("country", "").upper()

    if ships_to and creator_country and creator_country not in ships_to:
        warnings.append(f"This brand may not ship to {creator_country}")

    if warnings:
        return True, " | ".join(warnings)  # Still allow, but warn

    return True, None


# ============================================================================
# SINGLETON INSTANCE
# ============================================================================

_generator_instance = None


def get_generator_v2() -> GeminiPitchGeneratorV2:
    """Get or create the global v2 generator instance."""
    global _generator_instance
    if _generator_instance is None:
        _generator_instance = GeminiPitchGeneratorV2()
    return _generator_instance


# ============================================================================
# ANALYTICS HELPERS
# ============================================================================

def log_pitch_generation(
    creator_id: int,
    brand_id: int,
    result: PitchResult,
    match_warning: Optional[str] = None
) -> Dict:
    """
    Build analytics payload for logging pitch generation.
    Store this in pitch_generation_logs table for variant performance analysis.
    """
    return {
        "creator_id": creator_id,
        "brand_id": brand_id,
        "prompt_version": result.prompt_version,
        "model_used": result.model_used,
        "opener_variant_id": result.variant_ids.opener_id if result.variant_ids else None,
        "close_variant_id": result.variant_ids.close_id if result.variant_ids else None,
        "subject_variant_id": result.variant_ids.subject_id if result.variant_ids else None,
        "success": result.success,
        "source": result.source,
        "retry_count": result.retry_count,
        "safety_blocked": result.safety_blocked,
        "error": result.error,
        "match_warning": match_warning,
        "created_at": datetime.utcnow().isoformat()
    }
