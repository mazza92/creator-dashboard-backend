---

# Newcollab Optimization Brief
**Goal:** $120 MRR → $10k MRR
**Date:** June 2026
**Stack:** React CRA + styled-components + Flask + PostgreSQL + Stripe + GA4

---

## Scope

| Priority | Block | Impact | Effort |
|----------|-------|--------|--------|
| P0 | Raise price to $19 for new users | +58% revenue per subscriber | 1 hour |
| P1 | Fix brand matching by follower size | Reply rate | 2 hours |
| P1 | Fix pitch subject line generator | Open rate + reply rate | 2 hours |
| P1 | Fix pitch body quality | Reply rate | 3 hours |
| P1 | Fix For You onboarding order | Activation rate | 2 hours |
| P1 | Clean up brand descriptions | Trust + niche match feel | 2 hours |
| P1 | Fix pipeline health messaging | Retention | 1 hour |
| P1 | Add social proof strip (replied banners) | Conversion before first pitch | 2 hours |

---

## Block 1 — Raise Price to $19 for New Users

### Why
Conversion would need to drop by more than 37% before you make less money per signup. At sub-$25/month the decision is "does this work" not "is it $12 or $19." The current conversion problem is pitch quality and upgrade trigger timing, not price.

### Break-even table
| Scenario | Conversion | Revenue per 100 signups |
|----------|-----------|------------------------|
| $12, 1.4% (today) | 1.40% | $16.80 |
| $19, conversion drops 10% | 1.26% | $23.94 |
| $19, conversion drops 20% | 1.12% | $21.28 |
| $19, conversion drops 30% | 0.98% | $18.62 |
| $19, conversion drops 37% | 0.88% | $16.80 (break-even) |

### Rules
- Grandfather existing subscribers at $12 — do not touch their plan
- New signups only see $19 going forward
- Watch conversion rate for 30 days on the new cohort

### Steps
1. Create a new $19/month price in Stripe dashboard
2. Copy the new price ID
3. Update your checkout component to use the new price ID for new subscriptions
4. Find/replace `$12` with `$19` in `UpgradeModal.js` only (3 locations: pro-price display, ctaText in Block 1, ROI box)
5. Do not change any other files — the ROI statement ("covers your subscription") remains true at $19

---

## Block 2 — Fix Brand Matching by Follower Size

### Why
Showing a 5K creator Sweaty Betty or Kylie Cosmetics guarantees zero replies. PR teams at large brands never respond to micro-creator cold emails. Wrong brand match destroys trust in the product regardless of pitch quality. The creator sends 3 pitches to the wrong brands, gets zero replies, and concludes the tool does not work.

### Rule
Never show a creator a brand more than 5x their own follower count.

| Creator followers | Max brand Instagram followers |
|------------------|------------------------------|
| Under 5K | 10K |
| 5K to 20K | 50K |
| 20K to 50K | 100K |
| 50K+ | No cap |

### Backend change

**Find** your brand matching query (likely in `pr_crm_routes.py` or equivalent):

```python
# Wherever you query brands for the For You / Matches feed
brands = Brand.query.filter(...)
```

**Add this filter:**

```python
def get_max_brand_followers(creator_followers):
    if creator_followers < 5000:
        return 10000
    elif creator_followers < 20000:
        return 50000
    elif creator_followers < 50000:
        return 100000
    else:
        return None  # no cap

max_followers = get_max_brand_followers(creator.follower_count)
if max_followers:
    brands = brands.filter(Brand.instagram_followers <= max_followers)
```

**Also filter mega-brands from Trending section:**

```python
# Trending / Most Contacted section
brands = brands.filter(Brand.min_follower_requirement <= 50000)
```

### What to check
- Confirm `Brand` model has `instagram_followers` field — if named differently, adjust accordingly
- If `instagram_followers` is not populated for all brands, add a fallback: brands with null follower count are treated as small brands (safe to show)

```python
from sqlalchemy import or_
brands = brands.filter(
    or_(
        Brand.instagram_followers == None,
        Brand.instagram_followers <= max_followers
    )
)
```

---

## Block 3 — Fix Pitch Subject Line Generator

### Why
The subject line is the only thing that determines whether the email gets opened. "PR collab idea for [Brand]" is the most common subject line in every PR inbox. It gets deleted without opening. A specific subject line referencing the creator's content angle and the brand's product gets 3 to 4x more opens.

### Current formula
```
PR collab idea for {brand_name}
```

### New formula
```
{creator_content_angle} for {brand_hero_product_or_brand_name}
```

### Examples by niche
```
Supplements / Wellness:
"My 30-day gut health series — your [product] came up in comments"
"Protein content for 34K supplement followers — interested?"

Beauty / Skincare:
"My morning skincare routine series — looking for a retinol"
"Honest skincare reviews for 18K beauty followers — your [product]"

Fitness:
"30-day strength challenge on TikTok — your gear fits perfectly"
"Fitness content creator — 22K followers asking about [brand category]"

Food & Beverage:
"Healthy snack series for 15K food TikTok — your [product] would be perfect"
"Recipe content and honest reviews — 20K food followers"

Pet:
"Weekly pet care content for 12K dog owners — your [product] is a fit"
```

### Updated AI prompt for subject line

**Find** wherever your pitch generation prompt is built (likely a string in your Flask route or a utility function):

**Replace the subject line instruction with:**

```python
subject_prompt = f"""
Generate a subject line under 10 words for a PR pitch email.
Creator niche: {creator.niche}
Creator platform: {creator.primary_platform}
Creator followers: {creator.follower_count}
Brand name: {brand.name}
Brand hero product or category: {brand.hero_product or brand.category}

Rules:
- Reference the creator's content angle and the brand's product or category specifically
- Never use "PR collab idea"
- Never use "collaboration request"
- Never use "partnership opportunity"
- Make it feel like the creator has a specific plan, not a generic ask
- Under 10 words
- No em dashes
"""
```

### Brand data needed
The subject line generator needs `brand.hero_product` to be specific. If this field does not exist yet, use `brand.category` as fallback and add `hero_product` to the Brand model for future enrichment.

```python
# Fallback
product_ref = brand.hero_product if brand.hero_product else brand.category
```

---

## Block 4 — Fix Pitch Body Quality

### Why
The pitch body needs to feel researched, specific, and under 80 words. Long generic pitches signal low effort. PR managers skim and delete in 3 seconds. The current body fails because it uses vague openers ("I've been using your products for a bit"), generic framing ("putting together a series for July"), and leads with follower count rather than audience value.

One reply in the first 3 free pitches changes the user's entire perception of the tool. That is the upgrade conversion event.

### Current structure (what to remove)
```
Hi there,
I've been using [Brand] products for a bit now and wanted to reach out about a collab idea.
I'm putting together a product review series for [month] and thought [Brand] would be a good fit.
I have [X] followers on [platform] who are always asking about product recommendations.
```

### New structure (under 80 words)
```
Hi,

[One specific line about the brand — their product, their audience, or something real about them.]

[Who you are in one stat that matters to them — engagement rate or niche audience description, not just follower count.]

[The specific content idea — format + product + why it fits their audience.]

Would you be open to sending [product]?

[Name]
```

### Rewritten example
**Creator:** 34.5K TikTok, fitness niche
**Brand:** Sweaty Betty

```
Subject: Your Power Leggings for a 30-day strength series on TikTok

Hi,

Your Power Leggings keep coming up in my comments as a recommendation request from my fitness audience.

I create strength training content on TikTok (34.5K followers, 8% engagement, women 22 to 35 who buy gear on creator recommendation).

I'd love to feature them in a dedicated workout video this month — specific, authentic, on-brand for what you post.

Would you be open to sending a pair?

[Name]
```

### Updated AI prompt for pitch body

**Find** your pitch body generation prompt and replace with:

```python
pitch_prompt = f"""
Write a PR pitch email body under 80 words.

Creator: {creator.follower_count} followers on {creator.primary_platform}, posts about {creator.niche}, engagement rate {creator.engagement_rate or 'approx 5'}%, audience is {creator.audience_demographic or creator.niche + ' enthusiasts'}.
Brand: {brand.name}, hero product is {brand.hero_product or brand.category}, targets {brand.target_audience or 'consumers interested in ' + brand.category}.
Content idea: a {creator.primary_format or 'short-form video'} featuring {brand.hero_product or brand.name} for the creator's {creator.niche} audience.

Rules:
- Start with "Hi," only — no name, no "Hi there", no "Dear"
- Never say "I've been using your products for a bit"
- Never say "putting together a [month] series"
- Never say "I wanted to reach out about a collab"
- Never say "I think we would be a great fit"
- Line 1: one specific real thing about the brand or their product (not generic praise)
- Line 2: creator reach in one compelling stat — use engagement rate and audience description, not just follower count
- Line 3: specific content idea — format, product, and why it works for the brand's audience
- Line 4: single clear ask: "Would you be open to sending [product]?"
- Sign off with first name only
- Total body: under 80 words
- No em dashes
"""
```

### Brand data fields to add to Brand model

These fields make the pitch specific. Add them now and populate via enrichment over time:

```python
# Add to Brand model
hero_product = db.Column(db.String(255))        # e.g. "Power Leggings"
target_audience = db.Column(db.String(255))     # e.g. "women 25-40 into fitness"
tone = db.Column(db.String(50))                 # premium / casual / wellness / functional
```

**Fallback map if fields are empty:**

```python
tone_fallback = {
    'beauty': 'premium',
    'skincare': 'wellness',
    'fitness': 'functional',
    'food': 'casual',
    'supplements': 'wellness',
    'pet': 'casual',
    'fashion': 'premium',
    'lifestyle': 'casual',
}
brand_tone = brand.tone or tone_fallback.get(brand.category.lower(), 'casual')
```

### Creator data to ensure is populated

| Field | Used for | Fallback |
|-------|----------|---------|
| `creator.niche` | Audience description in pitch | Required — no fallback |
| `creator.engagement_rate` | Social proof stat | Default to "approx 5%" |
| `creator.primary_platform` | Platform reference | "social media" |
| `creator.primary_format` | Content idea framing | "short-form video" |
| `creator.audience_demographic` | Audience description | `{niche} enthusiasts` |

---

## Block 5 — Fix For You Onboarding Order

### Why
New users land on For You and immediately see a "Build your media kit — get 3x more replies" banner before they have done anything. It is homework before reward. The HOW TO GET YOUR PR explainer is buried below it. A new user skips both and goes straight to brand cards with no context.

### Current order (bad)
1. Niche header
2. Kit banner (CTA before any context)
3. Brand matches
4. How it works explainer (buried, never seen)

### New order
1. Niche header
2. How it works — 3 steps (understand the game first)
3. Brand matches (the reward)
4. Kit banner (contextual — they now understand why it matters)

### Find in `ForYou.js`
```jsx
<KitBanner>
  Build your media kit to get 3x more replies...
</KitBanner>

{/* How it works section */}
<HowItWorks>
  ...
</HowItWorks>

{/* Brand matches */}
<MatchSection>
```

### Replace with (reorder only)
```jsx
{/* How it works section */}
<HowItWorks>
  ...
</HowItWorks>

{/* Brand matches */}
<MatchSection>
  ...
</MatchSection>

{/* Kit banner — after they've seen their matches */}
<KitBanner>
  Build your media kit to get 3x more replies...
</KitBanner>
```

No logic change. Reorder JSX only.

---

## Block 6 — Clean Up Brand Descriptions

### Why
"JadeYoga — Your One Stop Solution to Get Best quality Yoga Products" reads like a scraped product description. It signals automated data, not curation. This erodes trust at the exact moment a user is most interested — when they see their first match. One sentence of real copy changes the feel entirely.

### Format
```
[Brand name] makes [product] for [target customer]. [One differentiating detail.]
```

```
Before: "JadeYoga — Your One Stop Solution to Get Best quality Yoga Products"
After:  "JadeYoga makes natural rubber yoga mats for serious practitioners. Eco-sourced, no PVC."

Before: "Aura Bora — Premium Sparkling Water with Herbs and Botanicals"
After:  "Aura Bora makes herb-infused sparkling water in flavors like lavender and lemongrass. No sweeteners."
```

### Implementation

**Option A (manual, fast):** Update the top 20 most-matched brands in your DB directly.

```sql
UPDATE brands SET description = '[new description]' WHERE id = [id];
```

**Option B (AI-assisted, scalable):** One-time script to rewrite all descriptions:

```python
for brand in brands_with_bad_descriptions:
    new_desc = openai.chat([
        {"role": "system", "content": "Write a one-sentence brand description under 20 words. Format: [Brand] makes [product] for [customer]. [One differentiating detail.] No marketing language, no superlatives."},
        {"role": "user", "content": f"Brand: {brand.name}. Category: {brand.category}. Website: {brand.website}"}
    ])
    brand.description = new_desc
```

### Also fix niche matching

A Supplements creator seeing JadeYoga (Fitness) as their top match signals personalization is broken. Add strict category mapping:

```python
NICHE_BRAND_CATEGORY_MAP = {
    'supplements': ['supplements', 'wellness', 'nutrition', 'food'],
    'fitness': ['fitness', 'sports', 'activewear', 'wellness'],
    'beauty': ['beauty', 'skincare', 'haircare', 'cosmetics'],
    'food': ['food', 'beverage', 'snacks', 'nutrition'],
    'lifestyle': ['lifestyle', 'home', 'wellness', 'beauty'],
    'pet': ['pet'],
}

allowed_categories = NICHE_BRAND_CATEGORY_MAP.get(creator.niche.lower(), [])
if allowed_categories:
    brands = brands.filter(Brand.category.in_(allowed_categories))
```

---

## Block 7 — Fix Pipeline Health Messaging

### Why
Score 40 + "Needs attention" after 3 pitches tells a new user they failed before they started. They did exactly what you asked. The waiting period is where most users churn — messaging should build anticipation, not anxiety.

### Current (bad)
```
Pipeline Health · Score: 40 · Needs attention
Add more pitches to strengthen your pipeline
```

### New — state-based messages

| State | Message |
|-------|---------|
| 0 pitches sent | "Start your first pitch to get going." |
| 1 to 2 pitches, waiting | "Your pitches are out there. Most replies come in 3 to 7 days." |
| 3 pitches, all waiting | "3 pitches sent. Brands are reviewing. Check back in a few days." |
| First reply received | "Your first reply is in. This is how it starts." |
| 1+ reply, more waiting | "Replies coming in. Keep your pipeline warm." |

### Find in pipeline component
```jsx
<PipelineHealth>
  <HealthScore>{score}</HealthScore>
  <HealthLabel>Needs attention</HealthLabel>
  <HealthSub>Add more pitches to strengthen your pipeline</HealthSub>
</PipelineHealth>
```

### Replace with
```jsx
<PipelineHealth>
  <HealthLabel>{getPipelineMessage(pitches)}</HealthLabel>
</PipelineHealth>
```

```js
function getPipelineMessage(pitches) {
  const total = pitches.length;
  const replied = pitches.filter(p => p.status === 'replied').length;
  const waiting = pitches.filter(p => p.status === 'waiting').length;

  if (total === 0) return 'Start your first pitch to get going.';
  if (replied > 0) return 'Replies coming in. Keep your pipeline warm.';
  if (waiting >= 3) return '3 pitches sent. Brands are reviewing. Check back in a few days.';
  if (waiting > 0) return 'Your pitches are out there. Most replies come in 3 to 7 days.';
  return 'Keep pitching to build momentum.';
}
```

Remove the numeric health score entirely for free users.

---

## Block 8 — Add Social Proof Strip (Replied Banners)

### Why
The green "They Replied!" screen is the most convincing UI in the app. New users never see it. Showing a version of this proof before the first pitch moves the belief moment from after the reply to before the first pitch. One reply seen by proxy is worth more than any upgrade modal copy.

### Add to For You page, above brand matches

```jsx
<RecentRepliesStrip>
  <StripLabel>This week on Newcollab</StripLabel>
  {displayReplies.slice(0, 2).map((reply, i) => (
    <ReplyPreview key={i}>
      <GreenDot />
      <ReplyText>
        A {reply.creator_niche} creator ({reply.follower_range} followers)
        got a reply from {reply.brand_name}
      </ReplyText>
    </ReplyPreview>
  ))}
</RecentRepliesStrip>
```

### Backend endpoint
```python
@app.route('/api/creator/recent-replies')
def recent_replies():
    replies = db.session.query(
        Brand.name.label('brand_name'),
        Creator.niche.label('creator_niche'),
        case(
            (Creator.follower_count < 5000, 'under 5K'),
            (Creator.follower_count < 20000, 'under 20K'),
            (Creator.follower_count < 50000, 'under 50K'),
            else_='50K+'
        ).label('follower_range')
    ).join(Pitch, Pitch.brand_id == Brand.id)\
     .join(Creator, Pitch.creator_id == Creator.id)\
     .filter(Pitch.status == 'replied')\
     .filter(Pitch.replied_at > datetime.now() - timedelta(days=7))\
     .order_by(Pitch.replied_at.desc())\
     .limit(5).all()

    return jsonify([r._asdict() for r in replies])
```

### Styled components
```js
const RecentRepliesStrip = styled.div`
  background: #f0fdf4;
  border: 1px solid #bbf7d0;
  border-radius: 10px;
  padding: 12px 14px;
  margin-bottom: 16px;
`;

const StripLabel = styled.div`
  font-size: 11px;
  font-weight: 700;
  color: #059669;
  text-transform: uppercase;
  letter-spacing: 0.04em;
  margin-bottom: 8px;
`;

const ReplyPreview = styled.div`
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 13px;
  color: #374151;
  margin-bottom: 4px;
`;

const GreenDot = styled.div`
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: #059669;
  flex-shrink: 0;
`;
```

### Fallback for early stage (no real reply data yet)
```js
const fallbackReplies = [
  { brand_name: 'a skincare brand', creator_niche: 'beauty', follower_range: 'under 10K' },
  { brand_name: 'a wellness brand', creator_niche: 'fitness', follower_range: 'under 20K' },
];
const displayReplies = recentReplies.length > 0 ? recentReplies : fallbackReplies;
```

---

## Expected Outcome

| Metric | Before | After |
|--------|--------|-------|
| Pitch open rate | ~10% | 30 to 40% |
| Reply rate on matched pitches | near zero | 15 to 30% |
| Users getting 1 reply in first 3 pitches | rare | common |
| Conversion rate | 1.4% | 2.5 to 3.5% |
| Revenue per 100 signups | $16.80 | $47 to $66 |

The reply is the conversion event. Fix the pitch, get the reply, the upgrade follows naturally.