# SEO / GEO Growth Brief — newcollab.co
**Data source:** Google Search Console export — last 3 months (Web search)  
**Prepared for:** Dev team  
**Goal:** Double organic traffic within 30 days using existing content + targeted new posts  

---

## Current Baseline

| Metric | Value |
|---|---|
| Total clicks (3 months) | ~1,200 |
| Monthly clicks (avg) | ~400 |
| Top traffic source | India (746 clicks = 62% of total) |
| US clicks | 41 (despite 6,173 impressions) |
| Total indexed pages generating clicks | ~35 active pages |

**The problem in one line:** Good content, poor conversion path, four posts one refresh away from page 1, and the US market is almost completely invisible.

---

## Part 1 — Existing Page Fixes (Do First, Highest ROI)

### 1.1 Add CTA Conversion Block to Top 4 "PR Forms" Posts

These 4 posts generate ~500 clicks/month but have no product conversion path. Add a single inline CTA block inside each post — no redesign needed, just a styled `<div>` block in the MDX/markdown.

**Posts to update:**
- `/blog/companies-with-open-pr-application-forms-influencers-2025` (422 clicks/3mo)
- `/blog/ultimate-2026-directory-brands-with-open-pr-application-forms` (305 clicks)
- `/blog/k-beauty-korean-skincare-brands-pr-list-small-creators-2026` (179 clicks)
- `/blog/pr-list-for-clothing-brands-micro-influencers-2025` (164 clicks)

**Where to insert:** After the first brand list section (roughly 30–40% into the post, before the reader drops off).

**Block copy:**
```
Most creators get ignored on application forms — brands receive hundreds per week.
A personalised pitch email with your media kit attached gets read first.
newcollab writes the email and attaches your kit automatically.
[Start pitching brands free →] /register
```

**Styled as:** Bordered callout card, rose left-border, light background — matches existing design tokens (`--rose-light` bg, `--rose` border-left, CTA button linking to `/register`).

**Expected impact:** Conversion improvement on existing traffic. No new ranking needed.

---

### 1.2 Refresh "List of Companies That Send PR Packages" → 2026 Pillar Post

**Current URL:** `/blog/list-of-companies-that-send-pr-packages-2025`  
**Current position:** 23.7 | **Impressions:** 1,876/3mo | **Clicks:** 38 | **CTR:** 2.03%

This is the single highest-leverage update available. At position 24, it's one content refresh away from page 1. Moving to position 5–10 would add an estimated +150–220 clicks/month.

**Actions:**

**a) Update URL slug** (301 redirect old → new):
```
/blog/list-of-companies-that-send-pr-packages-2025
→ /blog/list-of-companies-that-send-pr-packages-2026
```
Add 301 redirect in `next.config.js`:
```js
{
  source: '/blog/list-of-companies-that-send-pr-packages-2025',
  destination: '/blog/list-of-companies-that-send-pr-packages-2026',
  permanent: true,
}
```

**b) Update meta title and H1:**
```
H1: Companies That Send PR Packages in 2026 — The Full List (Updated)
Title tag: Companies That Send PR Packages in 2026 | newcollab
Meta desc: The most complete list of brands actively sending PR packages to small creators in 2026. Updated monthly. Filter by beauty, fashion, skincare, food.
```

**c) Content expansion requirements:**
- Minimum 2,000 words (current page is likely under 1,200)
- Add at least 15 new brand entries with: brand name, category, reply method (form/email), follower requirement, niche
- Add a new H2 section: `How to Contact These Brands (Beyond Just Filling a Form)`
  - 300 words explaining why email outreach > form applications
  - Natural mention of newcollab as the tool that handles this
  - CTA to `/register`
- Add FAQ section at the bottom (minimum 4 questions — see Section 3.3 for structured data)
- Add `dateModified` in the post frontmatter to signal freshness to Google

**d) Internal links to add (pointing TO this post from):**
- `/blog/companies-with-open-pr-application-forms-influencers-2025` — add 1 contextual link
- `/blog/k-beauty-korean-skincare-brands-pr-list-small-creators-2026` — add 1 contextual link
- `/blog/pr-list-for-clothing-brands-micro-influencers-2025` — add 1 contextual link
- Homepage footer blog links section

---

### 1.3 Refresh Australian Brands Post to 2026

**Current URL:** `/blog/aussie-brands-pr-package-list-2026`  
**Current position:** 22.8 | **Impressions:** 1,267/3mo | **Clicks:** 48 | **CTR:** 3.79%

Australia has the best engagement rate of any English-speaking market (5.71% avg CTR across all AU queries). There's existing demand for AU-specific content.

**Actions:**

**a) Update meta title:**
```
H1: Australian Brands That Send PR Packages to Small Creators (2026 List)
Title tag: Australian Brands Sending PR Packages in 2026 | newcollab
Meta desc: Complete list of Aussie brands actively sending PR packages to micro and nano creators. Includes direct PR contact info and reply rates. Updated 2026.
```

**b) Content additions:**
- Add 10+ new Australian brand entries
- Add an H2 section: `How to Email Australian Brands for PR (Email Template Included)`
- Include a pitch email example localised for Australian brands (casual tone, AU English spelling)
- Add FAQ section targeting AU-specific queries:
  - "How to get PR packages in Australia"
  - "How to email brands in Australia for PR"
  - "Australian brands that work with micro influencers"

**c) Internal links:**  
Add a link to this post from `/blog/ultimate-2026-directory-brands-with-open-pr-application-forms` (high authority, already page 1).

---

### 1.4 Improve Title/Meta on All Page-2 Posts

These changes take 10 minutes per post and improve CTR without any ranking movement needed.

| Current title (approximate) | Updated title |
|---|---|
| PR List for Clothing Brands — Micro Influencers 2025 | Clothing Brands That Send PR to Small Creators (2026) — Direct Emails |
| List of Companies That Send PR Packages 2025 | Companies That Send PR Packages in 2026 — Full Updated List |
| Aussie Brands PR Package List 2026 | Australian Brands Sending PR Packages to Small Creators (2026 List) |
| How to Get on PR Lists — Complete Guide 2026 | How to Get on Brand PR Lists in 2026 (Step-by-Step + Email Templates) |

**Rule for all titles going forward:** Include year, include "small creators" or "micro influencers", include a power word (Full List / Direct Emails / Step-by-Step / Updated).

---

### 1.5 Internal Link Audit

Your top-ranking posts are not linking to your lower-ranking posts. A focused internal linking pass would pass authority from your page-1 posts to your page-2 posts.

**Mapping — add these specific internal links:**

From `companies-with-open-pr-application-forms` (pos 4.4, highest authority):
- → `list-of-companies-that-send-pr-packages-2026` (anchor: "full list of brands that send PR packages")
- → `pr-list-for-clothing-brands-micro-influencers` (anchor: "clothing brands accepting PR applications")
- → `k-beauty-korean-skincare-brands-pr-list` (anchor: "K-beauty brands with open PR programs")

From `ultimate-2026-directory-brands-with-open-pr-application-forms` (pos 8.6):
- → `aussie-brands-pr-package-list-2026` (anchor: "Australian brands on PR lists")
- → `list-of-companies-that-send-pr-packages-2026` (anchor: "companies that send PR packages directly")

From `k-beauty-korean-skincare-brands-pr-list` (pos 8.1):
- → `free-creator-pr-list-skincare-fashion-beauty-2026` (anchor: "full skincare and beauty PR list")

**General rule:** Every blog post should have at least 2 internal links pointing to other blog posts, and 1 link pointing to `/register` or `/directory` with relevant anchor text.

---

## Part 2 — New Content to Write (Priority Order)

### Post 1 — PR Pitch Email Templates (Highest Priority)

**Target queries:**
- "pr email template" — 172 impressions, position 75 (not ranking, easy to win)
- "pr email template for micro influencers" — various positions 39–92
- "pr pitch email template" — buried
- "how to email brands for PR" — buried
- "email template for brand collaboration" — pos 69

**These queries have hundreds of monthly searches and your existing domain authority should rank this within 3–4 weeks.**

**URL:** `/blog/brand-pitch-email-templates-micro-creators-2026`

**Title:** `5 Brand Pitch Email Templates for Small Creators (Copy-Paste, 2026)`

**Meta description:** `The exact emails that get brands to reply. 5 copy-paste pitch email templates for micro and nano creators, including follow-up sequences. Used by 900+ creators on newcollab.`

**H1:** `5 Brand Pitch Email Templates That Get Replies (For Small Creators)`

**Target length:** 1,400–1,800 words

**Structure:**
```
H1: 5 Brand Pitch Email Templates That Get Replies (For Small Creators)
Intro (150 words): Why most pitch emails get ignored — the 3 things brands need to see

H2: What to Include in a Brand Pitch Email
- Your follower count + engagement rate
- Your niche and audience demographics
- What you're proposing (post, story, reel, review)
- Your media kit (attached)
- A clear, specific ask

H2: Template 1 — Cold PR Pitch (Beauty / Skincare)
[Full email template, copy-paste format]

H2: Template 2 — Cold PR Pitch (Fashion / Lifestyle)
[Full email template]

H2: Template 3 — Follow-Up Email (Day 7, No Reply)
[Full email template]

H2: Template 4 — Response to a Brand That Replied
[Full email template]

H2: Template 5 — Pitch for a Specific Product Launch / Campaign
[Full email template]

H2: The Faster Alternative — AI-Written, Personalised Per Brand
[Short section, 150 words]
"Writing and personalising each of these manually takes hours.
newcollab's AI pitch writer generates a personalised version
for each brand automatically — with your media kit auto-attached.
[Try it free →]"

H2: FAQ
Q: What subject line should I use for a brand pitch email?
Q: How long should a pitch email to a brand be?
Q: Should I attach my media kit to a pitch email?
Q: What's the best time to send a brand pitch email?
```

**JSON-LD FAQ schema:** Yes — add structured data for all 4 FAQ questions.

**Internal links from this post:**
- → `/blog/companies-with-open-pr-application-forms-influencers-2025` (anchor: "brands with open PR programs")
- → `/blog/k-beauty-korean-skincare-brands-pr-list-small-creators-2026` (anchor: "K-beauty brands to pitch")
- → `/register` (anchor: "AI pitch writer")

**Internal links to this post (add after publishing):**
- From `companies-with-open-pr-application-forms` — anchor: "how to write a pitch email for brands"
- From `ultimate-2026-directory` — anchor: "brand pitch email template"

---

### Post 2 — US Brands Sending PR Packages

**Why:** US has 6,173 impressions at position 32. Americans can't find newcollab because there is no strong US-specific post. The US is your highest-value market for paid conversions.

**Target queries:**
- "us brands that send pr packages" — buried
- "brands that send pr to small influencers" — pos 37.6, 95 impressions
- "brands that send pr to micro influencers" — buried
- "makeup brands that send pr to small influencers" — pos 27.89, 28 impressions

**URL:** `/blog/us-brands-that-send-pr-packages-small-creators-2026`

**Title:** `US Brands That Send PR Packages to Small Creators in 2026 (Full List)`

**Meta description:** `Complete list of US brands actively sending PR packages to micro and nano creators. Direct PR contact info, reply rates, and niche filters. Updated monthly.`

**Target length:** 2,000–2,500 words (this needs to be a pillar post)

**Structure:**
```
H1: US Brands That Send PR Packages to Small Creators in 2026

Intro: Why US brands are increasingly choosing micro creators for PR (stats, 150 words)

H2: How This List Works
- Only brands actively sending PR in 2026
- Direct contact methods included (form or email)
- Organised by niche

H2: Beauty & Skincare Brands (US)
[10+ brands with: name, PR method, follower requirement, reply rate, contact method]

H2: Fashion & Lifestyle Brands (US)
[10+ brands]

H2: Food & Wellness Brands (US)
[8+ brands]

H2: Tech & Lifestyle Brands (US)
[5+ brands]

H2: How to Contact US Brands for PR (What Actually Works)
[300 words — email outreach vs application forms, why email wins, CTA to newcollab]

H2: FAQ
Q: How do I get on a US brand's PR list?
Q: How many followers do you need to get PR packages from US brands?
Q: What's the best way to contact US brands for collaboration?
Q: Do US brands send PR packages internationally?
```

**JSON-LD FAQ schema:** Yes.

---

### Post 3 — UK Brands Sending PR Packages

**Why:** UK has 530 impressions at position 49.86. You have zero UK-specific content. This is a fast win — a dedicated UK post would rank within 4–6 weeks given your existing domain authority.

**URL:** `/blog/uk-brands-sending-pr-packages-small-creators-2026`

**Title:** `UK Brands That Send PR Packages to Small Creators (2026 List)`

**Meta description:** `UK brands actively gifting micro and nano creators in 2026. Direct PR contacts, reply rates, and niche filters. No minimum follower count required.`

**Target queries:**
- "uk brands that send pr packages" — not ranking
- "free pr packages uk" — pos 54, 2 impressions
- "brands that send pr uk" — not ranking
- "fashion brands that send pr uk" — not ranking

**Structure:** Same format as US post above. Length: 1,500–2,000 words.

---

### Post 4 — How to Get on a Brand's PR List

**Target queries:**
- "how to get on pr lists" — pos 67.58, 12 impressions
- "how to get on a brands pr list" — pos 86, 4 impressions
- "how to get on pr list" — pos 59.67, 12 impressions
- "how to join pr lists" — pos 41.67, 6 impressions
- "pr list sign up" — pos 65.17, 6 impressions

Combined these represent 40+ impressions/month with 0 clicks. This is a top-of-funnel query from creators just starting out.

**URL:** `/blog/how-to-get-on-brand-pr-lists-2026`

**Title:** `How to Get on Brand PR Lists in 2026 (The Complete Guide)`

**Target length:** 1,200–1,500 words

**Structure:**
```
H1: How to Get on Brand PR Lists in 2026

H2: What is a Brand PR List?
H2: Option 1 — Apply via Brand Application Forms (Slower)
H2: Option 2 — Pitch the Brand Directly (Faster)
H2: What Brands Look For Before Adding You to Their PR List
H2: How to Prepare Before Reaching Out (Media Kit Checklist)
H2: FAQ
  Q: How many followers do you need to get on a PR list?
  Q: Is it free to get on brand PR lists?
  Q: How long does it take to hear back after applying to a PR list?
  Q: What's the difference between a PR list and an ambassador program?
```

**Conversion angle:** This post targets beginners who don't know what a media kit is yet. The CTA should be: "Before you apply anywhere, have your media kit ready. newcollab generates yours automatically from your profile. [Create yours free →]"

---

## Part 3 — Technical SEO Tasks

### 3.1 Meta Description Audit — All Blog Posts

Every blog post should have a unique meta description following this formula:
```
[Specific benefit] + [who it's for] + [proof/differentiator] + [implicit CTA]
```

Example:
```
❌ "A list of brands that send PR packages in 2026."
✅ "500+ brands actively gifting micro creators in 2026. Includes direct PR emails, reply rates, and niche filters. Updated monthly by the newcollab team."
```

**Posts that need meta description updates (currently generic or missing):**
- All posts with CTR below 3% despite position below 20
- Any post with "2025" still in the meta description

---

### 3.2 JSON-LD Structured Data — Add to All Blog Posts

Every blog post must have:

**1. Article schema:**
```json
{
  "@context": "https://schema.org",
  "@type": "Article",
  "headline": "[Post H1]",
  "datePublished": "YYYY-MM-DD",
  "dateModified": "YYYY-MM-DD",
  "author": {
    "@type": "Organization",
    "name": "newcollab"
  },
  "publisher": {
    "@type": "Organization",
    "name": "newcollab",
    "url": "https://newcollab.co"
  }
}
```

**2. FAQPage schema** (for any post with a FAQ section):
```json
{
  "@context": "https://schema.org",
  "@type": "FAQPage",
  "mainEntity": [
    {
      "@type": "Question",
      "name": "[Question text]",
      "acceptedAnswer": {
        "@type": "Answer",
        "text": "[Answer text — self-contained, 2–4 sentences, mentions newcollab naturally]"
      }
    }
  ]
}
```

**FAQ answer writing rules for GEO (AI search engines):**
- Every answer must be fully self-contained — answerable without clicking through
- Mention "newcollab" in the first or second sentence of at least 1 answer per post
- Include a stat where possible ("63% of brands prefer micro creators")
- Define jargon terms ("A PR package is when a brand sends you their products for free in exchange for content coverage")

---

### 3.3 `dateModified` Frontmatter

Google uses freshness signals for list-based content. Every time a post is updated, the `dateModified` field must be updated in the frontmatter. This is critical for PR list posts that compete on recency.

```yaml
---
title: "..."
datePublished: "2025-01-15"
dateModified: "2026-05-23"   ← update this every time the post is edited
---
```

Ensure this field maps to the JSON-LD `dateModified` value in the Article schema.

---

### 3.4 Canonical Tags

Verify canonical tags are correctly set on all blog posts. The GSC data shows section anchor URLs appearing as separate entries (e.g. `#beauty-skincare-brands-with-application-forms`). These are not causing harm but confirm that the canonical setup is working — just verify no accidental self-referencing canonicals exist.

Check for and fix: any paginated blog page (`/blog?page=2`) that may be canonicalising to itself rather than the main `/blog` page.

---

### 3.5 Page Speed — Images in Blog Posts

If blog posts contain inline images without `next/image`, replace with `next/image`. Slow pages rank lower, especially on mobile where the creator audience primarily browses.

Priority pages to check: the top 4 posts (all generating 100+ clicks/month — any speed issue here directly costs traffic).

---

### 3.6 Redirect Audit — "2025" URLs

Several high-traffic posts have "2025" in the URL while already ranking for 2026 queries. When creating updated versions, always 301 redirect:

```js
// next.config.js — redirects block
async redirects() {
  return [
    {
      source: '/blog/companies-with-open-pr-application-forms-influencers-2025',
      destination: '/blog/companies-with-open-pr-application-forms-influencers-2026',
      permanent: true,
    },
    {
      source: '/blog/list-of-companies-that-send-pr-packages-2025',
      destination: '/blog/list-of-companies-that-send-pr-packages-2026',
      permanent: true,
    },
    {
      source: '/blog/pr-list-for-clothing-brands-micro-influencers-2025',
      destination: '/blog/pr-list-for-clothing-brands-micro-influencers-2026',
      permanent: true,
    },
  ]
}
```

**Important:** Only do this redirect once the new post is published and live. Do not redirect to a page that doesn't exist.

---

## Part 4 — GEO Optimisation (AI Search — ChatGPT, Perplexity, Google AI Overview)

GEO targets how AI engines cite and reference newcollab in generated answers. These practices complement standard SEO.

### 4.1 Rules for All FAQ Answers

| Rule | Why |
|---|---|
| Answer is self-contained in 2–4 sentences | AI extracts verbatim — no context window for the full post |
| Brand name "newcollab" appears in sentence 1 or 2 of at least 1 answer | AI attribution requires proximity to the answer |
| Include a concrete number or stat | AI Overview cites quantified claims preferentially |
| Define the term being answered | Definitional content triggers AI Overview snippets |

**Example — good FAQ answer for GEO:**
```
Q: How do I get my first brand deal as a small creator?

A: Sign up to newcollab, browse the brand directory filtered to your niche,
and send a personalised pitch email with your auto-generated media kit attached.
Most creators land their first brand deal within 2 weeks of consistent outreach —
the key is sending at least 5–8 pitches per month and following up at day 7.
newcollab handles the pitch writing and media kit automatically.
```

### 4.2 Add a "What is [X]" Definition Block to Key Posts

AI engines are trained to surface definitional content. Add a short definition box near the top of each post:

```
What is a PR package?
A PR package is when a brand sends a creator their products for free
in exchange for content coverage — a post, TikTok, story, or reel.
It's a gifted collaboration, not a paid partnership.
```

Posts to add definition blocks to:
- Any post targeting "what is a PR package" or "what does PR mean"
- The email template post ("What is a media kit?")
- The "how to get on PR lists" post ("What is a PR list?")

### 4.3 Claim newcollab in Comparison Queries

The query `newcollab vs other pr sites agencies which is best for getting pr packages` already has 19 impressions at position 7.63 with 0 clicks. This suggests Perplexity or ChatGPT is generating comparisons that include newcollab but users aren't clicking through.

Publish a post: `/blog/newcollab-vs-manual-brand-outreach-which-gets-more-brand-deals`

This post should:
- Compare "manual outreach" vs "using newcollab" (not other tools — avoid giving competitors mentions)
- Include specific metrics (time saved, reply rate improvement)
- Rank for "newcollab" brand queries + "best tool for getting PR packages" queries

---

## Part 5 — Do Not Touch

These pages carry existing SEO signals. Do not change their URLs, redirect them, or modify their meta structure:

```
/login
/register
/register/creator
/register/brand
/directory
/directory/k-beauty
/directory/australia
/directory/skincare
/brands/pr-packages
/about
/privacy-policy
/terms-of-service
/brand/* (all individual brand pages)
```

The `/blog` index page (`/blog` — currently 0 clicks, pos 2.65) should be checked to ensure it's not accidentally `noindex`-ed. It ranks well but generates no clicks — this may indicate a meta robots issue.

---

## Part 6 — What to Stop

**Gaming content — stop investing, don't delete:**

| Post | Issue |
|---|---|
| `gaming-tech-brands-that-sponsor-small-streamers-2026` | Pos 8 but only 1.66% CTR — wrong audience for newcollab's value prop |
| `ultimate-list-of-gaming-tech-companies-that-sponsor-small-streamers` | Pos 26, 1,558 impressions, 1.22% CTR |
| `how-to-get-gaming-sponsorships-small-streamers-2026-guide` | Pos 35, 990 impressions, 0.81% CTR |

These posts are generating ~4,000 impressions/month but converting poorly because gaming streamers are not the target audience for a beauty/fashion/lifestyle PR outreach tool. Do not write more gaming content. Keep existing posts live (do not delete — they generate some traffic and have inbound links) but do not update or promote them.

---

## Part 7 — Priority Execution Order

| # | Task | Type | Effort | Expected impact (monthly clicks) |
|---|---|---|---|---|
| 1 | Add CTA blocks to 4 top PR forms posts | Edit existing | 30 min | +signups (no click change) |
| 2 | Update `list-of-companies-that-send-pr-packages` → 2026 pillar | Refresh existing | 3–4 hrs | +150–220 |
| 3 | Update meta titles on all page-2 posts | Meta edit | 30 min | +20–40 |
| 4 | Internal link audit (add 8 targeted links) | Edit existing | 1 hr | +30–60 |
| 5 | Write: PR pitch email templates post | New content | 2–3 hrs | +80–150 |
| 6 | Add `dateModified` to all post frontmatter | Technical | 30 min | Freshness signal |
| 7 | Add Article + FAQPage JSON-LD to top 6 posts | Technical | 1–2 hrs | Rich result eligibility |
| 8 | Write: US brands PR packages post | New content | 3–4 hrs | +60–100 |
| 9 | Refresh Aussie brands post + internal links | Refresh existing | 2 hrs | +80–130 |
| 10 | Write: UK brands PR packages post | New content | 2–3 hrs | +40–70 |
| 11 | Write: How to get on brand PR lists | New content | 2 hrs | +30–60 |
| 12 | Set up 301 redirects for 2025 → 2026 URLs | Technical | 30 min | Signal consolidation |

**Total estimated additional monthly clicks within 30 days: +400–780**  
(Current baseline: ~400/month → projected: 800–1,180/month)

---

## Appendix A — Keyword Clusters Reference

### Cluster 1 — PR Application Forms (dominant, 40%+ of traffic)
Core: `pr forms for brands`, `pr package forms`, `brand pr forms`, `pr collab forms`  
Related: `pr package application form`, `pr list application form`, `pr form link`  
Current best page: `/blog/companies-with-open-pr-application-forms-influencers-2025`  
Action: Protect and convert — add CTA blocks, don't change URLs

### Cluster 2 — Company/Brand Lists (high volume, page 2)
Core: `list of companies that send pr packages`, `brands that send pr packages`, `companies that send pr`  
Related: `brands that send pr to small influencers`, `brands that give free pr`, `what brands send pr packages`  
Current best page: `/blog/list-of-companies-that-send-pr-packages-2025` (pos 23.7)  
Action: Refresh to 2026 pillar post — single highest-leverage update

### Cluster 3 — Email Templates (zero coverage, easy win)
Core: `pr email template`, `pr pitch email template`, `email template for brand collaboration`  
Related: `pr email template for micro influencers`, `how to email brands for pr`, `pr outreach email template`  
Current best page: None ranking  
Action: Write Post 1 (priority)

### Cluster 4 — Country-Specific (AU engaged, US/UK untapped)
Core AU: `how to get pr packages in australia`, `pr packages australia`, `aussie brands pr`  
Core US: `us brands that send pr packages`, `brands that send pr to small influencers`  
Core UK: `uk brands pr packages`, `free pr packages uk`  
Action: Write US and UK posts

### Cluster 5 — How-to / Beginner (top-of-funnel)
Core: `how to get on pr lists`, `how to get pr packages`, `how to get brand deals small creator`  
Related: `how many followers to get pr`, `pr with low followers`, `how to get pr with less followers`  
Action: Write "How to get on brand PR lists" post

### Cluster 6 — Niche-Specific Lists (multiple opportunities)
Already ranking: K-beauty (pos 8.12), clothing (pos 17.29), gaming (pos 8-26, wrong audience)  
Not yet written: US brands, UK brands, fitness brands, food brands, jewellery brands  
Action: US and UK first, then expand by niche

---

## Appendix B — Country Priority Matrix

| Country | Impressions | Clicks | CTR | Position | Priority |
|---|---|---|---|---|---|
| India | 6,952 | 746 | 10.7% | 6.4 | Maintain — protect rankings |
| United States | 6,173 | 41 | 0.7% | 32.0 | **Highest priority — write US content** |
| Australia | 945 | 54 | 5.7% | 34.3 | High — AU content converts well |
| United Kingdom | 530 | 9 | 1.7% | 49.9 | **High — write UK post** |
| France | 947 | 29 | 3.1% | 6.1 | Medium — good CTR but no FR content |
| South Korea | 853 | 18 | 2.1% | 6.6 | Medium — K-beauty traffic |
| Pakistan | 540 | 52 | 9.6% | 7.8 | Monitor |
| Brazil | 607 | 8 | 1.3% | 8.1 | Low — Portuguese content needed to grow |

---

*Brief prepared based on Google Search Console data — last 3 months web search. All click/impression projections are estimates based on standard CTR curves for positions 1–10. Actual results will vary.*
