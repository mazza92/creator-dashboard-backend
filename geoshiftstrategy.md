# Geo-Shift Strategy: India → US, Canada, AU
**Goal:** Reduce India traffic share, grow US/CA/AU organic rankings  
**Current state:** India 62% of traffic, US at position 32 with 6,173 impressions and 41 clicks

---

## Why India dominates right now

Your content is not geo-targeted. "How to get PR packages", "brands that send PR packages" are searched globally — India has a huge aspiring creator population, low local competition, and your content ranks easily there. You didn't target India; you just ranked there by default.

The fix is not to suppress India traffic. It's to create content that is inherently US/AU/CA specific — brand names, market data, local examples — so Google serves it to users in those markets instead.

---

## Part 1 — Technical fixes (this week)

### 1. Google Search Console geographic target
GSC lets you signal your primary market to Google.

```
Search Console → Settings → International Targeting → Country → United States
```

If you want all 3 markets equally, leave it unset. If US is the priority, set US. This has a moderate signal impact — content targeting matters more, but it's a free 2-minute fix.

### 2. Switch to US English sitewide
Google uses language signals. Audit your site for UK/generic spellings:

| Change | From | To |
|---|---|---|
| Spelling | colour, favourite, recognise | color, favorite, recognize |
| Currency default | £ / generic | $ USD |
| Examples | Generic | US brand names (Rhode, e.l.f., Rare Beauty) |
| Stats | Global | "In the US, nano-influencers..." |

### 3. Ensure fast load times in US/AU
Check your server latency from target regions using pingdom.com or gtmetrix.com (set test location to New York, Sydney). If slow, ensure your CDN has US/AU edge nodes. Vercel and Netlify do this automatically.

---

## Part 2 — Content strategy (highest leverage)

### The core principle
India traffic exists because your content has no geographic anchor. Every piece of new content you create should name-drop US/AU/CA brands, creators, and market context. Google will geo-route it accordingly.

### 4 pillar posts to write now

**Post 1 — US (highest priority)**
```
Title: 50 US Beauty Brands That Send PR Packages to Micro-Influencers (2026)
Target: "US brands that send PR packages", "beauty brands for micro influencers USA"
Why: US has 6,173 impressions at position 32 — one strong pillar will move this fast
Structure:
  - Intro: why US brands are most active for gifted collabs
  - 50 brands with category, follower requirement, and how to apply
  - CTA block: "Stop applying through forms — here's how to email them directly"
```

**Post 2 — Australia**
```
Title: Australian Brands Open to Influencer Gifting (2026 Directory)
Target: "Australian brands PR packages", "AU beauty brands micro influencers"
Why: AU has high creator engagement, low content competition, English-language
Structure:
  - Intro: AU creator economy stats
  - 30+ AU brands (Frank Body, Bondi Sands, The Ordinary AU, etc.)
  - How AU brand PR outreach differs from US
  - CTA block
```

**Post 3 — Canada**
```
Title: Canadian Beauty and Lifestyle Brands Accepting Influencer Collabs (2026)
Target: "Canadian brands PR packages", "Canada influencer gifting programs"
Structure: Same format as AU post
Notable CA brands: Nudestix, DECIEM/The Ordinary, Kaja Beauty CA
```

**Post 4 — "How to pitch US brands" (intent-driven)**
```
Title: How to Email a US Brand's PR Manager (Template + Direct Contacts)
Target: "how to contact brand PR manager", "influencer pitch email US brand"
Why: High purchase intent — someone searching this is ready to pitch
Structure:
  - Why forms don't work (conversion argument)
  - How to find the direct PR email
  - Template for US brands
  - CTA: "Newcollab has verified PR contacts for 500+ US brands"
```

### Stop writing content that attracts India traffic

Audit your existing posts. Any post about general "how to get PR packages" with no country anchor is pulling India traffic. Do not write more of these. Add a US/AU/CA geographic anchor to every future piece.

Also stop: gaming content, generic influencer tips, anything without a geographic or brand-specific anchor.

---

## Part 3 — On-page geo signals

### 5. Add country-specific landing pages

Create dedicated pages for each market. These will rank for country-specific queries and send strong geographic signals to Google.

```
/brands/us         — US brands directory (filterable)
/brands/australia  — AU brands directory
/brands/canada     — CA brands directory
/brands/uk         — UK brands directory (bonus)
```

Each page:
- H1: "US Brands Open to PR Gifting — [Year]"
- 20+ brands visible, rest behind signup
- Country-specific stats ("US nano-influencers land 3x more gifted packages...")
- Schema: ItemList with brand names + URLs

### 6. Update your homepage geo signals

Your homepage likely has no geographic anchor. Add:
- "Trusted by creators in the US, UK, and Australia"
- US brand logos in your brand directory section (Rhode, Rare Beauty, e.l.f.)
- A stat with a US anchor: "437 US brands have open PR programs this month"

### 7. Schema markup with geo context

Add `areaServed` to your Organization schema:
```json
{
  "@type": "Organization",
  "name": "Newcollab",
  "areaServed": ["US", "CA", "AU", "GB"],
  "description": "Newcollab helps creators in the US, Canada, and Australia land brand PR packages."
}
```

---

## Part 4 — Link building in target markets

This is the highest-impact but slowest lever. One strong US backlink outweighs 50 India backlinks for US rankings.

### Priority targets

**US creator economy publications:**
- Creator Economy Newsletter (by Avi Gandhi) — pitch a data story
- The Information's creator coverage — data angle
- Later.com blog — guest post on "how micro-influencers land brand deals"
- Glossy.co — pitch as a tool for indie creators
- Business of Fashion — longshot but high DA

**AU creator / beauty media:**
- Broadsheet — AU lifestyle publication
- Pedestrian.tv — AU youth culture, covers creator economy
- BeautyBay AU blog

**CA creator communities:**
- Daily Hive (CA lifestyle)
- Post on r/PersonalFinanceCanada, r/beauty (CA-focused threads)

### HARO / journalist outreach
Sign up for HARO (now Connectively). Respond to any query about:
- Influencer marketing
- Creator economy
- Brand-creator partnerships
- Micro-influencer statistics

One placement in Forbes, Entrepreneur, or Glossy = significant US domain authority boost.

---

## Part 5 — Community presence in target markets

Organic rankings take 60-90 days. Community drives traffic immediately and sends engagement signals Google notices.

### US
- Reddit: r/Influencermarketing, r/SmallYoutubers, r/Tiktokhelp
- Facebook groups: "Creator Economy", "Influencer Marketing Hub community"
- Twitter/X: engage with US creator coaches (Josh Richards, Paddy Galloway threads)

### Australia
- Reddit: r/AusFinance (creator economy angle), r/australia
- Facebook: "Australian Content Creators" group (50k+ members)
- LinkedIn: AU marketing professionals

### Canada
- Reddit: r/PersonalFinanceCanada, r/digitalnomad CA threads
- Facebook: "Canadian Content Creators" group

**Tactic:** Post your US brands post in these communities the week it publishes. Frame it as useful info not self-promotion. "Put together a list of 50 US brands with open PR programs — sharing in case useful." First 48-hour engagement tells Google this content is relevant to US/AU/CA audiences.

---

## Part 6 — What NOT to do

- **Do not block India traffic** — harmful to overall SEO, against Google guidelines
- **Do not create separate domains** (newcollab.us, newcollab.com.au) — splits domain authority
- **Do not use hreflang** — only needed for same-content in different languages, you're English-only
- **Do not use Google Ads yet** — funnel conversion needs fixing first (covered separately)
- **Do not redirect Indian IPs** — terrible for SEO and user experience

India's share will shrink naturally as US/AU/CA content grows. You don't need to fight it.

---

## Timeline

| Week | Action |
|---|---|
| Week 1 | GSC geographic target, US English audit, schema update |
| Week 2 | Publish US brands pillar post, share in 3 US creator communities |
| Week 3 | Publish AU brands post + CA brands post |
| Week 4 | Publish "how to pitch US PR manager" post |
| Month 2 | Add /brands/us, /brands/australia, /brands/canada pages |
| Month 2 | Begin HARO responses (weekly) |
| Month 3 | First US/AU backlink placements showing in GSC |

---

## What to track in GSC

Filter impressions and clicks by country weekly:
```
GSC → Search results → Filter → Country → United States / Australia / Canada
```

Target in 90 days:
- US: position 32 → position 15 on "US brands PR packages" cluster
- AU: appear in impressions for "Australian brands gifting influencers"
- CA: appear in impressions for "Canadian brands influencer program"
- India share: drops from 62% to under 40% (not from losing India traffic, from growing US/AU/CA)
