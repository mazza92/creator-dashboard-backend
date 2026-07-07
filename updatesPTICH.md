# Pitch Generator Optimisation Brief
**Goal:** Turn the AI pitch generator into a proven cold outreach email that gets brand PR replies.
**Core principle:** Every line must earn its place. Four lines. No filler. The creator's audience described in their words, not the brand's marketing copy. The hero product named exactly.

---

## What's Wrong With the Current Pitch

| Problem | Example | Why It Fails |
|---------|---------|--------------|
| AI opener | "I've been eyeing your Luxury Clean Fragrances" | Known template tell — PR managers delete on sight |
| Brand copy used as creator audience | "Women seeking luxury clean fragrances and premium home fragrance products" | That is the brand's target_audience field copy-pasted — makes it read as a bot |
| Filler line | "Authentic, on-brand, specific to what works for your audience" | Means nothing, every pitch has this line |
| Vague content idea | "a short-form video this month" | PR manager cannot picture what they would get |
| Grammatically wrong ask | "Would you be open to sending product?" | "Sending product" is not correct English — signals automated |
| Username as sign-off | "SOcial" | Creator's real first name is needed — username breaks trust |
| Generic subject line | "Routine Video for 27.5K beauty followers" | Does not reference the brand or their specific product |

---

## The Formula That Gets Replies

Four lines. Nothing else.

```
Line 1: Specific observation about their exact hero product by name
Line 2: Who you are + one stat proving your audience is their customer
Line 3: The exact video you would make, naming the hero product
Line 4: Direct ask naming the specific product
```

A PR manager opens emails that feel like the creator knows their product.
They reply to emails where the content idea is so specific they can picture the video.

---

## Block 1 — New Creator Fields

Add these to the Creator model. They directly control pitch quality.

```python
# Add to Creator model
first_name           = db.Column(db.String(100))
audience_description = db.Column(db.Text)
primary_format       = db.Column(db.String(100))
```

```sql
ALTER TABLE creators ADD COLUMN first_name VARCHAR(100);
ALTER TABLE creators ADD COLUMN audience_description TEXT;
ALTER TABLE creators ADD COLUMN primary_format VARCHAR(100);
```

### `first_name`
Used as the pitch sign-off. Must never be a username or handle.

**Populate at onboarding or from existing display_name:**
```python
def get_creator_first_name(creator):
    if creator.first_name:
        return creator.first_name
    # If display_name is a single word it is probably a username — do not use
    if creator.display_name and ' ' in creator.display_name:
        return creator.display_name.split()[0].capitalize()
    return ""
```

### `audience_description`
The single highest-impact field for pitch quality. One question asked at onboarding:

> **"Describe your audience in your own words — who follows you and why?"**
> e.g. "beauty lovers aged 20-35 who are switching away from mainstream brands"

Even 10 words from the creator beats any auto-generated demographic.
This replaces the current pattern of using `brand.target_audience` to describe the creator's followers — which is the most damaging bug in the current prompt.

### `primary_format`
The type of content the creator makes.

```
"TikTok videos"
"Instagram Reels"
"YouTube Shorts"
"Instagram posts"
"blog posts"
```

Collect at onboarding via dropdown. Used in Line 3 of the pitch to make the content idea concrete.

---

## Block 2 — Helper Functions

```python
def format_followers(count):
    if not count:
        return "growing"
    if count >= 1_000_000:
        return f"{count / 1_000_000:.1f}M"
    elif count >= 1_000:
        return f"{count / 1_000:.1f}K"
    return str(count)
```

---

## Block 3 — Updated Pitch Prompt

**Find** your pitch generation function (likely in a Flask route or utility file).

**Replace the prompt string with:**

```python
pitch_prompt = f"""
You are writing a cold PR pitch email from a micro-creator to a brand's PR manager.
Goal: get the brand to reply and send a free product sample.
The creator is a real person. The content idea is genuine. The ask is direct.

CREATOR:
- First name (sign-off only): {get_creator_first_name(creator)}
- Platform: {creator.primary_platform}
- Followers: {format_followers(creator.follower_count)}
- Niche: {creator.niche}
- Content format: {creator.primary_format or 'short-form video'}
- Audience (use this to describe who follows the creator):
  {creator.audience_description or f'people interested in {creator.niche} aged 20-35'}

BRAND:
- Name: {brand.name}
- Hero product (use this exact name in the pitch): {brand.hero_product or brand.category}
- Brand tone: {brand.tone or 'premium'}
- Their customer: {brand.target_audience}
  (NOTE: this is the brand's customer — do NOT use this to describe the creator's audience)

---

WRITE THE FOLLOWING:

SUBJECT LINE (under 10 words):
Reference {brand.hero_product or brand.name} and the creator's content angle.
Good format: "[Hero product] for my [niche] audience on [platform]"
Never use: "PR collab idea", "collaboration request", "partnership opportunity"

EMAIL BODY (under 75 words):

Hi,

LINE 1 — Brand hook:
Write one specific observation about {brand.hero_product or brand.name} by exact name.
Must feel like a real person who knows this product wrote it.
Good: "Your No.04 Bois de Balincourt candle is the one my followers keep screenshotting from my shelf content."
Good: "The [hero product] keeps coming up in my DMs — my audience has been asking for exactly this."
Bad: "I've been eyeing your products."
Bad: "I came across your brand."
Bad: "I love what you're building."
Never start with "I've been" or "I came across".

LINE 2 — Creator proof:
One sentence only. Exact format:
"I create [niche] content on [platform] ([followers] followers, [X.X]% engagement — [audience_description in behavioural terms])."
Use creator.audience_description to describe the followers — never use brand.target_audience here.
Good audience description: "beauty followers who actively buy based on creator recommendations"
Bad audience description: "Women seeking luxury clean fragrances and premium home fragrance products" (that is brand copy)

LINE 3 — Content idea:
One sentence. Name {brand.hero_product or brand.name} exactly.
State the format ({creator.primary_format}). State the specific angle.
Good: "I'd love to dedicate a video to the [hero product] — showing how it fits into a real daily ritual for someone who cares about clean ingredients."
Bad: "I'd love to feature your products in a short-form video. Authentic, on-brand, specific to what works for your audience."
Never use: "this month", "authentic", "on-brand", or any filler phrase.

LINE 4 — Ask:
One sentence. Name the product or say "a sample of the [hero product]".
Good: "Would you be open to sending a bottle?"
Good: "Would you be open to sending a sample of the [hero product]?"
Bad: "Would you be open to sending product?" (grammatically wrong)
Never write "sending product" without an article.

[First name only — never a username or handle]
[Media kit URL on its own line]

---

HARD RULES:
- Total body: under 75 words
- Never: "I've been eyeing"
- Never: "I came across your brand"
- Never: "I think we'd be a great fit"
- Never: "authentic, on-brand" or any filler phrase
- Never: "this month" as a time pressure line
- Never: copy brand.target_audience into the creator's audience description
- Always: name the hero product by exact name in Line 1 and Line 3
- Always: make Line 3 a specific video idea the brand can picture
- Always: sign off with creator's real first name only
- Always: the ask must name a specific product, not just "product"
"""
```

---

## Block 4 — Subject Line Formula

The subject line determines whether the email gets opened. Update the subject generation prompt separately:

```python
subject_prompt = f"""
Write a subject line under 10 words for a PR pitch email.

Creator niche: {creator.niche}
Creator platform: {creator.primary_platform}
Creator followers: {format_followers(creator.follower_count)}
Brand hero product: {brand.hero_product or brand.category}

Rules:
- Reference the hero product and the creator's content angle specifically
- Never use "PR collab idea", "collaboration request", "partnership"
- Make it feel like the creator has a specific plan
- Under 10 words
- No punctuation at the end
"""
```

**Examples of good subject lines:**
```
No.04 Bois de Balincourt for my clean beauty TikTok
Peptide Glazing Fluid — skincare creator, 27.5K TikTok
Your Power Leggings in my 30-day strength series
Barrel Aged Maple Syrup for my 18K food audience
```

---

## Block 5 — Onboarding Question to Collect `audience_description`

Add one question to the creator onboarding flow. This is the highest-ROI change — one question that fixes the most damaging bug in every pitch.

**Where to add:** After niche and follower count, before finishing onboarding.

```jsx
<OnboardingStep>
  <StepLabel>One last thing</StepLabel>
  <StepTitle>Describe your audience in your own words</StepTitle>
  <StepSub>
    This goes directly into your pitch emails — brands read it.
    Write how you'd describe your followers to a friend.
  </StepSub>
  <TextArea
    placeholder="e.g. beauty lovers aged 20-35 who are switching away from mainstream brands and trust creator recommendations"
    maxLength={200}
    value={audienceDescription}
    onChange={e => setAudienceDescription(e.target.value)}
  />
  <CharCount>{audienceDescription.length}/200</CharCount>
</OnboardingStep>
```

For existing creators without this field, add a prompt inside the pitch modal:

```jsx
{!creator.audience_description && (
  <AudienceNudge>
    <NudgeText>Add your audience description to get better pitches</NudgeText>
    <NudgeLink onClick={() => openAudienceModal()}>Add now →</NudgeLink>
  </AudienceNudge>
)}
```

---

## Before vs After

**Before:**
```
Subject: Routine Video for 27.5K beauty followers

Hi,

I've been eyeing your Luxury Clean Fragrances — my audience keeps
asking for recommendations in this space.

I create beauty content on TikTok (27.5K followers, 9.3100%
engagement, Women seeking luxury clean fragrances and premium
home fragrance products).

I'd love to feature your Luxury Clean Fragrances in a short-form
video this month. Authentic, on-brand, specific to what works
for your audience.

Would you be open to sending product?

SOcial
```

**After:**
```
Subject: No.04 Bois de Balincourt for my clean beauty TikTok audience

Hi,

Your No.04 Bois de Balincourt candle is the one my followers keep
screenshotting from my shelf content.

I create clean beauty content on TikTok (27.5K followers, 9.3%
engagement — beauty followers who actively seek non-toxic
alternatives to mainstream fragrance and buy based on creator
recommendations).

I'd love to dedicate a video to the No.04 — showing how it fits
into a real daily ritual for someone who cares about clean ingredients.

Would you be open to sending a candle?

[Name]
[media kit link]
```

---

## Implementation Order

| Block | Task | Time |
|-------|------|------|
| 1 | Add first_name, audience_description, primary_format to Creator model + migration | 30 min |
| 2 | Add format_followers helper | 15 min |
| 3 | Replace pitch prompt with updated version | 1 hour |
| 4 | Replace subject line prompt | 30 min |
| 5 | Add audience_description question to onboarding | 1 hour |
| 5b | Add audience nudge inside pitch modal for existing creators | 30 min |

**Total: ~3.5 hours**

---

## Expected Impact

| Metric | Before | After |
|--------|--------|-------|
| Pitch feels automated | Yes — AI opener, brand copy as audience | No — specific product, creator's own words |
| Brand can picture the content | No — "short-form video this month" | Yes — exact product, exact angle |
| Ask is actionable | No — "sending product?" | Yes — names the specific product |
| Sign-off feels human | No — username "SOcial" | Yes — first name |
| Reply rate | Near zero on wrong-fit brands | 15 to 30% on correctly matched brands |

The reply is the conversion event. One reply from the first 3 pitches converts a free user to Pro with confidence. This prompt change is the highest-leverage improvement in the entire product.
