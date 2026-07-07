# Newcollab Pitch Generator: Gemini 2.0 Flash Integration Brief

## Objective

Replace the current template + variable substitution system with real LLM-generated pitches via Gemini 2.0 Flash. Every generated pitch must be:

1. Structurally consistent (proven cold-pitch skeleton)
2. Grounded strictly in provided data (no hallucinated assumptions about gender, style, product fit, audience)
3. Written in the voice of a real creator, not an AI

Target: generation time <1.5s, cost <$0.0005 per pitch, zero cases of provably wrong assumptions (like the "male creator styling a dress" bug).

## Why the current system fails

The template approach substitutes variables into pre-written blanks. When variables are missing or misaligned, the template picks a fallback assumption to fill the gap — and those fallbacks are the source of the disaster:

- Creator with no stated style → template assumes generic clothing category from brand's inventory
- Male creator + fashion brand with women's products → template picks the brand's hero product without checking creator alignment
- Brand data with sparse detail → template invents wording to fill sentence structure

An LLM with strict grounding rules doesn't invent to fill blanks — it either references only what it's given, or writes around missing data. That's the entire fix.

## Why Gemini 2.0 Flash specifically

- **$0.075/M input, $0.30/M output** — at ~800 input + 400 output tokens per pitch, that's **~$0.00018/pitch**. 1000 pitches/day = ~$6/month. Cost is a non-issue.
- **Structured output support** via response schema, so we get valid JSON without post-processing
- **Fast** (typically <1s at this token count)
- **Grounding follows instructions well** at temperature 0.5-0.7, unlike smaller models that drift

Sonnet or GPT-4 would produce marginally better prose but at 100× the cost. Not worth it for this scale.

## The winning cold-pitch structure (locked skeleton)

Every pitch follows this 5-part skeleton. Not optional. Not negotiable. The LLM must produce all 5 parts in this order:

1. **Subject line** — 4-8 words, lowercase, references brand or product specifically
2. **Opener** (1-2 sentences) — specific reference to something about the brand (recent drop, aesthetic, mission, campaign). Does NOT lead with the creator's stats. Proves the creator has actually looked at the brand.
3. **Self-intro** (1 sentence) — creator's niche + platform + audience size, delivered compressed. No fluff.
4. **Creative angle** (2-3 sentences) — a specific content idea for THIS brand. Uses brand's hero product IF one is provided. Otherwise stays generic to the brand's category. Ties to what the creator's audience actually engages with.
5. **Ask + close** (1-2 sentences) — clear ask ("would love to try X and create Y"), warm sign-off, first name.

This structure is derived from patterns observed in creator pitches that actually land brand deals in the beauty / skincare / fashion / lifestyle categories. It works because it inverts the amateur pattern (self-intro first, generic praise second) into the professional pattern (brand-specific opener first, creator relevance second, concrete offer third).

## Input schema — what we pass to Gemini

```typescript
type PitchGenerationInput = {
  brand: {
    name: string;                         // required
    category: string;                     // required — e.g. "skincare", "streetwear"
    hero_product?: string;                // optional — e.g. "MX Master 3S Mouse"
    hero_product_description?: string;    // optional — 1-2 sentences from brand's own site
    aesthetic?: string;                   // optional — e.g. "clean minimalist", "maximalist Y2K"
    recent_launch?: string;               // optional — e.g. "just dropped their fall collection"
    avg_price?: number;                   // optional — for context, not for the pitch body
  };
  creator: {
    first_name: string;                   // required
    niche: string;                        // required — e.g. "beauty", "menswear", "gaming"
    platform: string;                     // required — "TikTok" | "Instagram" | "YouTube"
    follower_count: number;               // required
    engagement_rate?: number;             // optional
    content_style?: string;               // optional — e.g. "GRWM videos", "tech unboxings"
    audience_demo?: string;               // optional — e.g. "Gen Z women US"
    past_collabs?: string[];              // optional — up to 3 previous brand names
  };
};
```

Nothing else. Do NOT pass gender, ethnicity, clothing size, physical attributes, or any demographic field the creator has not explicitly self-declared. If it's not in the schema, Gemini doesn't get to see it, doesn't get to guess.

## Creator tier (derived from follower_count)

Determine tier before calling Gemini and pass it as part of the prompt:

| Follower count | Tier | Pitch angle emphasis |
|---|---|---|
| < 10,000 | **starter** | Content-quality-first pitch. Focus on the creative angle, not stats. Never claim audience power a starter doesn't have. |
| 10,000 - 100,000 | **growing** | Balanced. Reference engagement + niche fit + creative angle. |
| > 100,000 | **established** | Lead with credibility (past collabs if provided, engagement rate, audience size). |

This maps to the tiered strategy already specified in earlier product work — the pitch generator is where it becomes real.

## The Gemini system prompt (verbatim, ready to ship)

```
You are a cold-outreach pitch writer for creators reaching out to brands for PR 
gifting and content partnerships. You write for real creators on Newcollab, 
speaking in their voice, not a marketing voice.

You output STRICT JSON matching this schema:
{
  "subject": "string — 4 to 8 words, lowercase, no exclamation marks, no emojis, 
              references the brand or product specifically",
  "body_html": "string — valid HTML email body, 4 short paragraphs in <p> tags, 
                <br> only for in-paragraph breaks, links as <a href='...'>",
  "body_plain": "string — plain-text version of the same body, paragraphs separated 
                 by \\n\\n",
  "reasoning": "string — 1 sentence explaining the creative angle you chose and why 
                it fits this creator and this brand (for internal debugging, never 
                shown to the user)"
}

STRUCTURE — every pitch follows this exact 5-part skeleton in order:

1. Opener (1-2 sentences): reference something specific about the brand — their 
   aesthetic, mission, recent product drop, campaign, or product line. Do NOT lead 
   with the creator's stats or "I love your brand." Show the creator has looked at 
   the brand.

2. Self-intro (1 sentence): creator's niche + platform + audience size, delivered 
   compressed. Format: "I make {niche} content on {platform} for {follower_count} 
   followers."

3. Creative angle (2-3 sentences): a SPECIFIC content idea for THIS brand. If a 
   hero_product is provided, reference it by name and describe how the creator 
   would feature it in a way that fits their niche. If no hero_product, stay 
   generic to the brand's category ("your fall skincare line", "your new drop") 
   without inventing product names.

4. Ask (1 sentence): clear, specific. Format examples: "Would love to try 
   {hero_product or 'a piece from your line'} and create {specific_deliverable} 
   in exchange." Deliverable examples by platform: TikTok → "a UGC video and a 
   Reel", Instagram → "a Reel and a Story series", YouTube → "an integrated 
   segment in an upcoming video."

5. Close (1 sentence + name): warm sign-off + creator's first name.

CREATOR TIER RULES:
- starter (<10K followers): emphasize content quality and creative angle. Do NOT 
  claim large audience or influence. Do NOT reference engagement stats. Lead the 
  creative angle harder.
- growing (10K-100K): balanced. Reference engagement rate if provided. Include 
  the creative angle prominently.
- established (>100K): may lead with credibility. Reference past collabs if 
  provided. May reference engagement rate. May cite audience demographic if 
  provided.

GROUNDING RULES — non-negotiable:

- NEVER assume the creator's gender, physical appearance, clothing size, ethnicity, 
  location, age, or any demographic field not explicitly provided in the input.
- NEVER write the creator styling, wearing, or using a product in a way that 
  implies gender or body type unless the creator's niche makes it obvious 
  ("menswear" → men's products, "womenswear" → women's products). If niche is 
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
- em dashes anywhere
- exclamation marks in subject lines
- ALL CAPS words for emphasis

FORMATTING:
- body_html: each paragraph in a <p> tag with no inline styles. Use <br> only 
  when a paragraph genuinely needs a line break inside it.
- body_plain: plain text, paragraphs separated by two newlines (\\n\\n).
- Never include a signature block, footer, or unsubscribe language in either 
  body — those are appended by the sending layer, not by you.

You will be given a JSON input with `brand`, `creator`, and `tier`. Generate the 
pitch strictly following the rules above.
```

## Gemini call configuration

```python
model = "gemini-2.0-flash"
temperature = 0.6
top_p = 0.9
max_output_tokens = 800
response_mime_type = "application/json"
response_schema = {
  "type": "OBJECT",
  "properties": {
    "subject": {"type": "STRING"},
    "body_html": {"type": "STRING"},
    "body_plain": {"type": "STRING"},
    "reasoning": {"type": "STRING"}
  },
  "required": ["subject", "body_html", "body_plain", "reasoning"]
}
```

Temperature 0.6 is the sweet spot: varied enough to avoid template-y output, controlled enough to stay on structure. Do NOT lower below 0.5 (pitches become mechanical) or raise above 0.75 (grounding rules start slipping).

Structured output via `response_schema` guarantees valid JSON, no parsing failures.

## Few-shot examples in the prompt (append after the system prompt)

Add 3 worked examples to the system prompt to lock the pattern. These pin the style far more effectively than instructions alone.

**Example 1 — starter tier, beauty**

```
Input:
{
  "brand": {"name": "Anua", "category": "skincare", "hero_product": "Heartleaf 77% Soothing Toner", "aesthetic": "clean K-beauty"},
  "creator": {"first_name": "Priya", "niche": "skincare for sensitive skin", "platform": "TikTok", "follower_count": 4200, "content_style": "before/after routines"},
  "tier": "starter"
}

Output:
{
  "subject": "heartleaf toner for sensitive skin content",
  "body_html": "<p>Hi,</p><p>Your Heartleaf 77% Toner keeps coming up in the sensitive-skin conversations on my side of TikTok, and the way it's formulated for reactive skin fits exactly what my audience is looking for.</p><p>I make sensitive-skin skincare content on TikTok for around 4,200 followers who deal with redness and reactive skin daily. My most-watched videos are honest before/after routines over 2 weeks with a single new product.</p><p>I'd love to test the Heartleaf Toner in a two-week reactive-skin routine video and a follow-up Story series with the actual skin results. Would you be open to sending a bottle?</p><p>Thanks either way,<br>Priya</p>",
  "body_plain": "Hi,\\n\\nYour Heartleaf 77% Toner keeps coming up in the sensitive-skin conversations on my side of TikTok, and the way it's formulated for reactive skin fits exactly what my audience is looking for.\\n\\nI make sensitive-skin skincare content on TikTok for around 4,200 followers who deal with redness and reactive skin daily. My most-watched videos are honest before/after routines over 2 weeks with a single new product.\\n\\nI'd love to test the Heartleaf Toner in a two-week reactive-skin routine video and a follow-up Story series with the actual skin results. Would you be open to sending a bottle?\\n\\nThanks either way,\\nPriya",
  "reasoning": "Starter tier so I led with the creative angle (2-week reactive-skin test) rather than audience size. Referenced Heartleaf Toner by name because it was provided. Kept ask small — a bottle, not a full PR box — to match starter follower count."
}
```

**Example 2 — established tier, tech**

```
Input:
{
  "brand": {"name": "Logitech", "category": "tech", "hero_product": "MX Master 3S", "hero_product_description": "Advanced wireless mouse for productivity"},
  "creator": {"first_name": "Marcus", "niche": "tech and productivity", "platform": "YouTube", "follower_count": 340000, "engagement_rate": 6.2, "content_style": "desk setup videos", "past_collabs": ["Keychron", "Grovemade"]},
  "tier": "established"
}

Output:
{
  "subject": "mx master 3s in an upcoming desk setup video",
  "body_html": "<p>Hi,</p><p>Your MX Master line has been the reference-standard mouse in my desk setup videos for a while, and the 3S in particular has been the ask most in my comments over the last two months.</p><p>I make tech and productivity content on YouTube for 340K subscribers, averaging 6.2% engagement, with prior collabs including Keychron and Grovemade. My desk-setup videos routinely drive 200K+ views.</p><p>I'd love to feature the MX Master 3S as the daily driver in an upcoming full desk-setup video, with a follow-up focused segment on the workflow difference at hour 8 of a work day. Standalone product placement, honest review, integrated naturally.</p><p>Would you be open to sending one over for the shoot?</p><p>Best,<br>Marcus</p>",
  "body_plain": "Hi,\\n\\nYour MX Master line has been the reference-standard mouse in my desk setup videos for a while, and the 3S in particular has been the ask most in my comments over the last two months.\\n\\nI make tech and productivity content on YouTube for 340K subscribers, averaging 6.2% engagement, with prior collabs including Keychron and Grovemade. My desk-setup videos routinely drive 200K+ views.\\n\\nI'd love to feature the MX Master 3S as the daily driver in an upcoming full desk-setup video, with a follow-up focused segment on the workflow difference at hour 8 of a work day. Standalone product placement, honest review, integrated naturally.\\n\\nWould you be open to sending one over for the shoot?\\n\\nBest,\\nMarcus",
  "reasoning": "Established tier so I led with credibility (subscriber count, engagement rate, past collabs including tech-adjacent brands). Referenced the MX Master 3S by name. Ask is proportional — one unit for a real integrated feature, not a bulk request."
}
```

**Example 3 — growing tier, fashion (with the gender-neutral guardrail engaged)**

```
Input:
{
  "brand": {"name": "Everlane", "category": "fashion", "aesthetic": "modern minimalist"},
  "creator": {"first_name": "Jordan", "niche": "fashion", "platform": "Instagram", "follower_count": 28000, "content_style": "capsule wardrobe styling"},
  "tier": "growing"
}

Output:
{
  "subject": "capsule wardrobe styling for your line",
  "body_html": "<p>Hi,</p><p>Your approach to essentials — the modern minimalist cuts and the transparency around production — fits the capsule-wardrobe content angle I've been building on Instagram.</p><p>I make fashion content on Instagram for 28,000 followers focused on building intentional wardrobes with fewer, better pieces. My most-saved posts are pieces styled across 4 to 5 different outfit contexts from one item.</p><p>I'd love to try a piece from your current line and feature it in a 5-way styling Reel plus a Story series breaking down the outfit contexts. Happy to work with whatever you'd like to send.</p><p>Thanks,<br>Jordan</p>",
  "body_plain": "Hi,\\n\\nYour approach to essentials — the modern minimalist cuts and the transparency around production — fits the capsule-wardrobe content angle I've been building on Instagram.\\n\\nI make fashion content on Instagram for 28,000 followers focused on building intentional wardrobes with fewer, better pieces. My most-saved posts are pieces styled across 4 to 5 different outfit contexts from one item.\\n\\nI'd love to try a piece from your current line and feature it in a 5-way styling Reel plus a Story series breaking down the outfit contexts. Happy to work with whatever you'd like to send.\\n\\nThanks,\\nJordan",
  "reasoning": "Growing tier balanced structure. Creator niche is 'fashion' (not menswear/womenswear specifically) and no gender is provided, so I stayed neutral: 'a piece from your line,' not 'a dress' or 'a shirt.' No specific hero_product provided so I referenced the brand's line generically without inventing a product name."
}
```

Bake these three examples directly into the system prompt as few-shot anchors. Do not remove them once shipped — they are the guardrail against style drift.

## Guardrails — post-generation validation

After Gemini returns, run these deterministic checks BEFORE surfacing the pitch to the user. If any check fails, retry once with the same prompt (Gemini's outputs vary). If retry also fails, fall back to the previous template and log the failure for review.

```python
def validate_pitch(pitch: dict, input: dict) -> ValidationResult:
    body = pitch["body_plain"].lower()
    
    # 1. No forbidden words
    forbidden = ["amazing", "excited to work with", "elevate", "unleash", 
                 "leverage", "obsessed", "huge fan", "in today's fast-paced"]
    for word in forbidden:
        if word in body:
            return Fail(f"forbidden phrase: {word}")
    
    # 2. No em dashes
    if "—" in body or "—" in pitch["body_html"]:
        return Fail("em dash present")
    
    # 3. Subject line rules
    if not pitch["subject"].islower():
        return Fail("subject not lowercase")
    if len(pitch["subject"].split()) < 3 or len(pitch["subject"].split()) > 10:
        return Fail("subject length outside 3-10 words")
    if "!" in pitch["subject"]:
        return Fail("exclamation mark in subject")
    
    # 4. Gender-assumption check (heuristic)
    creator_niche = input["creator"]["niche"].lower()
    if creator_niche not in ["womenswear", "women's fashion"]:
        assumed_women = ["dress", "skirt", "blouse", "she'd", "her outfit"]
        for word in assumed_women:
            if word in body:
                return Fail(f"gender-assumed word without matching niche: {word}")
    if creator_niche not in ["menswear", "men's fashion"]:
        assumed_men = ["men's", "guys", "brother", "he'd"]
        # skip check unless creator gender is unknown
        pass
    
    # 5. Hero product hallucination check
    if not input["brand"].get("hero_product"):
        # If no hero product was provided, the pitch should NOT reference a 
        # specific product by name. Check for suspicious specific product patterns.
        # (heuristic — flag Capitalized Two-Word Phrases inside product context)
        pass  # implement lightweight regex heuristic based on your brand DB
    
    # 6. Creator first name appears in close
    if input["creator"]["first_name"].lower() not in pitch["body_plain"].lower():
        return Fail("creator first name missing from close")
    
    return Pass()
```

The gender-assumption check is the single most important guardrail given the user's feedback. It's heuristic and imperfect, but it catches the specific bug pattern from the user report (male fashion creator getting "dress" in pitch).

## Fallback logic when data is missing

Do not block pitch generation on missing optional fields. Gemini's prompt is instructed to write around gaps. But if REQUIRED fields are missing:

| Missing field | Action |
|---|---|
| `brand.name` | Block generation. Show user "Brand data incomplete — cannot generate pitch." |
| `brand.category` | Attempt with generic category ("their brand"). Log for enrichment. |
| `creator.first_name` | Use "Best" as sign-off name. Warn user to complete profile. |
| `creator.niche` | Block generation. Prompt user to set niche in profile. |
| `creator.follower_count` | Block generation. This is the core segmentation signal. |
| `creator.platform` | Default to "Instagram" (largest user cohort). |

## Integration with existing pitch flow — minimal change

The existing flow: user clicks Pitch Now → `attempt_unlock` deducts credit → pitch composer opens with generated pitch prefilled.

Only change: swap the template call for a Gemini call.

```python
# BEFORE (existing template-based)
def generate_pitch(brand_id, creator_id):
    template = load_pitch_template(brand.category, creator.tier)
    return render_template(template, brand=brand, creator=creator)

# AFTER (Gemini-based)
def generate_pitch(brand_id, creator_id):
    brand = brands.find(brand_id)
    creator = users.find(creator_id)
    tier = derive_tier(creator.follower_count)
    
    input_payload = build_pitch_input(brand, creator, tier)
    
    for attempt in range(2):  # one retry on validation failure
        response = gemini_client.generate_content(
            model="gemini-2.0-flash",
            system_instruction=PITCH_SYSTEM_PROMPT,  # includes 3 few-shot examples
            contents=json.dumps(input_payload),
            generation_config={
                "temperature": 0.6,
                "top_p": 0.9,
                "max_output_tokens": 800,
                "response_mime_type": "application/json",
                "response_schema": PITCH_RESPONSE_SCHEMA
            }
        )
        pitch = json.loads(response.text)
        validation = validate_pitch(pitch, input_payload)
        if validation.passed:
            log_generation(brand_id, creator_id, pitch, source="gemini")
            return pitch
    
    # Both attempts failed validation → fall back to template + log for review
    log_generation_failure(brand_id, creator_id, input_payload, validation.reason)
    return generate_pitch_template_fallback(brand, creator)
```

Nothing else in the pitch composer, inbox, unlock, or send logic changes.

## Error handling

| Failure mode | Handling |
|---|---|
| Gemini API timeout (>3s) | Retry once with same params. If retry fails, use template fallback + alert dev. |
| Gemini API 5xx | Retry with exponential backoff (250ms, 500ms). Cap at 3 retries. Fall back to template. |
| Gemini API 429 (rate limit) | Queue and retry after 2s. If sustained, upgrade Gemini quota. |
| JSON schema failure | Retry once. If persists, fall back to template + log. |
| Post-validation failure (forbidden word etc.) | Retry once. If persists, fall back to template + log. |
| Missing required input field | Block generation before calling Gemini. Show user data-completion prompt. |

Every failure logs the input + reason so you can debug patterns.

## Cost validation

Actual math:
- Gemini 2.0 Flash: $0.075/M input tokens, $0.30/M output tokens
- Average pitch: ~800 input tokens (system prompt + few-shots + input) + ~400 output tokens
- **Per pitch cost: ~$0.00018**
- At 1,000 pitches/day: **~$5.40/month**
- At 5,000 pitches/day: **~$27/month**

Well within budget. The system prompt + few-shot examples are the largest cost driver (~600 of the 800 input tokens). If cost becomes an issue at scale, the few-shot examples can be moved to context-caching for further savings.

## A/B testing plan

Roll out to 50% of users initially, template stays live for the other 50%. Track for 14 days:

| Metric | How to measure |
|---|---|
| Reply rate | % of pitches that get a brand reply within 14 days |
| Positive reply rate | % of pitches replied with intent to send PR (manual tagging for first 100 replies, then classifier) |
| User edit rate | % of pitches user modifies before sending (proxy for perceived quality) |
| Regeneration rate | % of pitches user regenerates before sending |
| Support tickets citing pitch quality | Absolute count |

Success threshold: Gemini cohort's reply rate must be ≥1.3× template cohort's reply rate to justify rollout. If it's only marginally higher, keep A/B running for another 14 days to reach significance.

If reply rate is significantly WORSE on Gemini, do NOT ship — investigate whether the few-shot examples need tuning or the system prompt is too restrictive.

## Success metrics — first 30 days post-full-rollout

| Metric | Baseline (template) | Day 30 target (Gemini) |
|---|---|---|
| Pitch reply rate | current baseline | ≥1.5× baseline |
| User support tickets about "bad pitch" | current baseline | -80% |
| Pitch user-edit rate | current baseline | -30% |
| Pitch regenerate-before-send rate | current baseline | -50% |
| Failed validation (forbidden word / grounding issue) | n/a | <5% of generations |

## Non-goals

- ❌ Do not use Claude, GPT-4, or any other model. Ship with Gemini 2.0 Flash only. Multi-model support is v2.
- ❌ Do not build a creator-facing "tone selector" or "pitch style picker" — the tier system does this automatically.
- ❌ Do not let users edit the system prompt or few-shot examples. Any changes go through your prompt engineering process.
- ❌ Do not attempt to auto-generate follow-up emails yet. This brief is scoped to the initial pitch only.
- ❌ Do not remove the template fallback. It's the safety net for Gemini outages and validation failures.
- ❌ Do not run Gemini on already-sent pitches to "improve" them retroactively. New pitches only.

## Build sequence — 3-5 days

| Day | Task |
|---|---|
| Day 1 | Gemini API setup, credential storage, base client wrapper with retry logic |
| Day 2 | System prompt + 3 few-shot examples baked into config. Test on 20 handpicked (brand, creator) pairs manually. |
| Day 3 | Post-generation validation function + template fallback wiring |
| Day 4 | A/B experiment framework (50/50 split, cohort logging, metrics dashboard) |
| Day 5 | Internal QA pass on 100 real user profiles from staging, hand-review every output |
| Day 6+ | 50/50 rollout begins. Monitor for 14 days before deciding on full rollout. |

## What to hand-verify on the first 100 QA outputs

Before shipping to real users, someone (you or a QA person) needs to eyeball 100 generations against real user profiles and answer these questions per output:

- Does the opener reference something real about the brand? (not invented)
- Does the pitch match the creator's stated niche without assuming gender/appearance?
- Does it reference only products actually in the input?
- Does the tier match the follower count?
- Does the ask feel proportional to audience size?
- Would you, personally, hit "send" on this email if it were your account?

If more than 5 of 100 fail any of those questions, the system prompt needs revision before rollout. Do not ship a system that hallucinates in >5% of outputs.
