Here's the full raw text:

---

# Pitch Generator Optimisation Brief
**Goal:** Turn the AI pitch generator into a proven cold outreach email that gets brand PR replies.
**Core principle:** Every line must earn its place. Four lines. No filler. The creator's audience described in their words, not the brand's marketing copy. The hero product named exactly.

---

## What's Wrong With the Current Pitch

| Problem | Example | Why It Fails |
|---------|---------|--------------|
| AI opener | "I've been eyeing your Luxury Clean Fragrances" | Known template tell — PR managers delete on sight |
| Brand copy used as creator audience | "Women seeking luxury clean fragrances and premium home fragrance products" | That is the brand's target_audience field copy-pasted — reads as a bot |
| Filler line | "Authentic, on-brand, specific to what works for your audience" | Means nothing, every pitch has this line |
| Vague content idea | "a short-form video this month" | PR manager cannot picture what they would get |
| Grammatically wrong ask | "Would you be open to sending product?" | "Sending product" is not correct English |
| Username as sign-off | "SOcial" | Creator's real first name is needed |
| Generic subject line | "Routine Video for 27.5K beauty followers" | Does not reference the brand or their specific product |

---

## The Formula That Gets Replies

```
Line 1: Specific observation about their exact hero product by name
Line 2: Who you are + one stat proving your audience is their customer
Line 3: The exact video you would make, naming the hero product
Line 4: Direct ask naming the specific product
```

---

## Block 1 — New Creator Fields

```python
first_name           = db.Column(db.String(100))
audience_description = db.Column(db.Text)
primary_format       = db.Column(db.String(100))
```

```sql
ALTER TABLE creators ADD COLUMN first_name VARCHAR(100);
ALTER TABLE creators ADD COLUMN audience_description TEXT;
ALTER TABLE creators ADD COLUMN primary_format VARCHAR(100);
```

**`first_name`** — sign-off only, never a username

```python
def get_creator_first_name(creator):
    if creator.first_name:
        return creator.first_name
    if creator.display_name and ' ' in creator.display_name:
        return creator.display_name.split()[0].capitalize()
    return ""
```

**`audience_description`** — the single highest-impact field. Onboarding question:

> "Describe your audience in your own words — who follows you and why?"
> e.g. "beauty lovers aged 20-35 who are switching away from mainstream brands"

**`primary_format`** — collected at onboarding via dropdown: TikTok videos / Instagram Reels / YouTube Shorts / Instagram posts

---

## Block 2 — Helper Function

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

```python
pitch_prompt = f"""
You are writing a cold PR pitch email from a micro-creator to a brand's PR manager.
Goal: get the brand to reply and send a free product sample.

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
- Hero product (use this exact name): {brand.hero_product or brand.category}
- Brand tone: {brand.tone or 'premium'}
- Their customer: {brand.target_audience}
  (NOTE: this is the brand's customer — do NOT use this to describe the creator's audience)

---

WRITE:

SUBJECT LINE (under 10 words):
Reference {brand.hero_product or brand.name} and the creator's content angle.
Never: "PR collab idea", "collaboration request", "partnership opportunity"

EMAIL BODY (under 75 words):

Hi,

LINE 1 — Brand hook:
One specific observation about {brand.hero_product} by exact name.
Good: "Your No.04 Bois de Balincourt candle is the one my followers keep screenshotting from my shelf content."
Bad: "I've been eyeing your products." / "I came across your brand."
Never start with "I've been" or "I came across".

LINE 2 — Creator proof:
Exact format: "I create [niche] content on [platform] ([followers] followers, [X.X]% engagement — [audience_description in behavioural terms])."
Use creator.audience_description — never brand.target_audience.

LINE 3 — Content idea:
Name {brand.hero_product} exactly. State the format. State the angle.
Good: "I'd love to dedicate a video to the [hero product] — showing how it fits into a real daily ritual for someone who cares about clean ingredients."
Never: "this month", "authentic", "on-brand", filler phrases.

LINE 4 — Ask:
Good: "Would you be open to sending a sample of the [hero product]?"
Bad: "Would you be open to sending product?"

[First name only]
[Media kit URL]

---

HARD RULES:
- Under 75 words
- Never: "I've been eyeing", "I came across", "I think we'd be a great fit", "authentic, on-brand", "this month"
- Never: copy brand.target_audience as the creator's audience description
- Always: hero product named exactly in Line 1 and Line 3
- Always: Line 3 is a specific idea the brand can picture
- Always: real first name in sign-off, never username
- Always: ask names a specific product
"""
```

---

## Block 4 — Subject Line Prompt

```python
subject_prompt = f"""
Write a subject line under 10 words for a PR pitch email.
Creator niche: {creator.niche}
Creator platform: {creator.primary_platform}
Creator followers: {format_followers(creator.follower_count)}
Brand hero product: {brand.hero_product or brand.category}

Rules:
- Reference the hero product and creator's content angle specifically
- Never use "PR collab idea", "collaboration request", "partnership"
- Under 10 words, no punctuation at the end
"""
```

Good examples:
```
No.04 Bois de Balincourt for my clean beauty TikTok audience
Peptide Glazing Fluid — skincare creator, 27.5K TikTok
Your Power Leggings in my 30-day strength series
```

---

## Block 5 — Onboarding Question for `audience_description`

```jsx
<OnboardingStep>
  <StepTitle>Describe your audience in your own words</StepTitle>
  <StepSub>
    This goes directly into your pitch emails — brands read it.
    Write how you'd describe your followers to a friend.
  </StepSub>
  <TextArea
    placeholder="e.g. beauty lovers aged 20-35 switching away from mainstream brands who trust creator recommendations"
    maxLength={200}
    value={audienceDescription}
    onChange={e => setAudienceDescription(e.target.value)}
  />
</OnboardingStep>
```

For existing creators, show a nudge inside the pitch modal:

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
| 1 | Add 3 fields to Creator model + migration | 30 min |
| 2 | Add format_followers helper | 15 min |
| 3 | Replace pitch prompt | 1 hour |
| 4 | Replace subject line prompt | 30 min |
| 5 | Add audience_description to onboarding + pitch modal nudge | 1.5 hours |

**Total: ~3.5 hours**

---

## Expected Impact

| Metric | Before | After |
|--------|--------|-------|
| Pitch feels automated | Yes | No |
| Brand can picture the content | No | Yes |
| Ask is actionable | No | Yes |
| Sign-off feels human | No | Yes |
| Reply rate on matched pitches | Near zero | 15 to 30% |

The reply is the conversion event. One reply from the first 3 pitches converts a free user to Pro with confidence.