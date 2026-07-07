# Landing Page v2 — Dev Brief
**newcollab.co** · Design & copy revamp of existing landing page  
Status: Design complete (HTML prototype → `/home/user/landing-page-v2.html`)  
Scope: Revamp design and copy only. No new routes. No new backend. No structural changes.

---

## 1. Ground Rules

- **Do not** create new routes or page files. Edit the existing landing page only.
- **Do not** install new packages unless listed below.
- **Do not** change URL structure, slugs, or route layout — this protects existing SEO signals.
- All new components go into existing `components/landing/` (or wherever landing components currently live).
- Use `next/image` for every image (brand logos, lifestyle photos). Never use a plain `<img>` tag for content images.
- The existing `_app.tsx` / `layout.tsx` global styles, font imports, and meta structure stay in place — we layer on top.

---

## 2. File Map — What to Edit

### Public landing page
```
app/page.tsx                          ← main page file, replace section JSX
app/layout.tsx                        ← update <title>, meta description, OG tags, JSON-LD
components/landing/Hero.tsx           ← edit or replace
components/landing/Ticker.tsx         ← edit or replace
components/landing/ClarityStrip.tsx   ← edit or replace
components/landing/ProblemSection.tsx ← edit or replace
components/landing/LifestyleRow.tsx   ← NEW component (only new file allowed)
components/landing/SolutionFlow.tsx   ← edit or replace
components/landing/BrandDirectory.tsx ← edit or replace
components/landing/FeatureSection.tsx ← edit or replace (used 4×)
components/landing/SocialProof.tsx    ← edit or replace
components/landing/HowItWorks.tsx     ← edit or replace
components/landing/Pricing.tsx        ← edit or replace
components/landing/FAQ.tsx            ← edit or replace
components/landing/FinalCTA.tsx       ← edit or replace
```

> If the project uses a flat `pages/index.tsx` (Pages Router), same applies — edit that file in place.

### Assets to add
```
public/images/brands/rhode-skin.svg
public/images/brands/anua.svg
public/images/brands/oh-polly.svg
public/images/brands/fenty-beauty.svg
public/images/brands/nopalera.svg
public/images/brands/aura-bora.svg
public/images/brands/glow-recipe.svg
public/images/brands/princess-polly.svg

public/images/lifestyle/lifestyle-1.jpg   ← creator unboxing / PR haul
public/images/lifestyle/lifestyle-2.jpg   ← creator at desk / first brand email
public/images/lifestyle/lifestyle-3.jpg   ← package opened, product flat lay
public/images/lifestyle/lifestyle-4.jpg   ← content shoot / creator filming
```

---

## 3. Design Tokens (unchanged from existing)

```css
--rose: #E11D48
--rose-light: #FFF1F3
--violet: #7C3AED
--green: #059669
--green-light: #ECFDF5
--amber: #D97706
--black: #0F0F0F
--bg: #FAFAF9
--border: #EBEBEB
--text: #0F0F0F
--text2: #4A4A4A
--text3: #8A8A8A
--pro: linear-gradient(135deg, #E11D48, #7C3AED)
--sh: 0 1px 4px rgba(0,0,0,.05), 0 4px 16px rgba(0,0,0,.06)
--sh-lg: 0 8px 40px rgba(0,0,0,.10)
```

Font: Inter (already loaded). No font changes.

---

## 4. Section-by-Section Implementation

### 4.1 Nav
No changes. Keep existing nav component and routes.

---

### 4.2 Hero
**Layout:** Two-column grid (text left, GIF right). Collapses to single column on mobile ≤800px.

```tsx
// Hero text column
<h1>Land brand deals.<br/>On repeat.<br/><em>No guessing.</em></h1>
<p className="subline">
  The complete outreach system — brand contacts, AI pitch, auto media kit, deal tracking. All in one place.
</p>

// CTA row
<a href="/register" className="btn btn-black btn-lg">Start for free →</a>
<a href="#brands" className="btn btn-outline">See brands</a>

// Social proof row (below CTAs)
// 5 avatar circles + "900+ creators already getting brand deals · Free · No credit card"
```

**GIF visual:**
- Use `<Image src="/assets/hero.gif" ... unoptimized />` (Next.js doesn't optimize GIFs by default — set `unoptimized`)
- Add two floating badges on the GIF frame:
  - Bottom-left: `Package received! — Rhode Skin → @carolstyle`
  - Top-right: `6 pitches sent today`
- CSS `animation: float 3s ease-in-out infinite` on each badge

**Floating badge animation:**
```css
@keyframes float {
  0%, 100% { transform: translateY(0); }
  50%       { transform: translateY(-5px); }
}
```

---

### 4.3 Ticker (activity feed strip)
Black background, white text, infinite CSS scroll.

```
carolstyle landed Rhode Skin — 3 days after joining
glowwith_m got 2 brand replies in 48 hours  
zionne019 received an Anua PR package this week
sarahlooks pitched 8 brands in one afternoon
dailybyzoe landed Oh Polly with media kit auto-attached
plates.co got a food brand collab after first pitch
```

Duplicate the list once in the DOM for seamless loop:
```css
@keyframes ticker { 0% { transform: translateX(0); } 100% { transform: translateX(-50%); } }
.ticker-inner { animation: ticker 28s linear infinite; display: flex; }
```

**Emoji note:** Use sparingly — one per item max. Use `·` separator between items, not emoji separators.

---

### 4.4 Clarity Strip
4-item horizontal strip on white background, arrow connectors between items.

| Icon | Label | Sub-label |
|---|---|---|
| (search icon SVG) | Find brands | 500+ PR contacts, your niche |
| (document icon SVG) | Auto media kit | Generated, always ready |
| (pen icon SVG) | AI pitch email | Personalised in 60 seconds |
| (chart icon SVG) | Track everything | Pipeline · follow-ups · wins |

> Use inline SVG icons here instead of emoji — cleaner, more professional.

Mobile: 2×2 grid. Remove arrow connectors on mobile.

---

### 4.5 Lifestyle Photo Row
**NEW component — `LifestyleRow.tsx`** (the only truly new component).  
Full-bleed horizontal row of 4 portrait-ratio images between the problem section and solution flow.

```tsx
const images = [
  { src: '/images/lifestyle/lifestyle-1.jpg', caption: 'PR haul incoming', aspect: '3/4' },
  { src: '/images/lifestyle/lifestyle-2.jpg', caption: 'First brand email reply', aspect: '3/4', flex: 1.4 },
  { src: '/images/lifestyle/lifestyle-3.jpg', caption: 'Package opened', aspect: '3/4' },
  { src: '/images/lifestyle/lifestyle-4.jpg', caption: 'Content day', aspect: '3/4', flex: 1.2 },
];
```

Each image:
- `<Image fill objectFit="cover" />` inside a relative container
- Caption overlaid at bottom with `rgba(0,0,0,0.45)` backdrop
- Border-radius: 16px
- No padding on section — full-bleed feel

Mobile: horizontal scroll (`overflow-x: auto; display: flex; gap: 7px`). Min-width 140px per image.

**Image sourcing:** Get real creator lifestyle photos. Suggested sources:
- Unsplash (search: "influencer unboxing", "content creator flat lay", "beauty PR package")
- Or actual newcollab user-submitted content (best)

---

### 4.6 Problem Section — "Relatable moments"
2×2 card grid. Each card: bold relatable question headline + one short sentence.

```
Card 1: "Where do I even find brand PR emails?"
→ Googled it for hours. No real contacts, no idea who's open to new creators.

Card 2: "They want a media kit and I don't have one."
→ Brands expect your stats, demographics, niche. Most creators lose deals here.

Card 3: "I open a blank email and freeze every time."
→ Too formal? Too casual? Most creators spend hours on one pitch — or never send it.

Card 4: "I sent emails last month and forgot to follow up."
→ No system. No visibility. Warm leads went cold. Not laziness — just no tool.
```

Each card: white background, 1px border, 16px border-radius, hover lift effect.  
Visual accent per card: small colored icon area (no emoji) — use a small SVG icon or colored dot.  
Closing block: dark `#0F0F0F` pill across full width with "newcollab handles all of this — automatically." + CTA.

Mobile: single column.

---

### 4.7 Solution Flow (dark section)
5-step horizontal grid on `#0F0F0F` background.

| Step | Icon | Title | Body |
|---|---|---|---|
| 1 | Search SVG | Discover | 500+ brand PR contacts filtered to your niche |
| 2 | Document SVG | Prepare | Auto-generated media kit from your profile |
| 3 | Pen SVG | Pitch | AI email + media kit attached · batch-send 10 at once |
| 4 | Chart SVG | Track | Pipeline + auto follow-up reminders at day 7 |
| 5 | Gift box SVG | Win | Package arrives · log your PR value · repeat |

Each step icon: semi-transparent colored circle background, step number badge (small circle, rose bg) in top-right.

Mobile: horizontal scroll with `overflow-x: auto` — steps don't wrap, they scroll.

---

### 4.8 Brand Directory Section

**Copy:**
- H2: `500+ brands open to creators. Real PR contacts.`
- Subline: `Real email addresses. Real reply rates. Verified weekly. Filter by niche — pitch in one click.`

**Interactive filter tabs:** All · Beauty · Fashion · Skincare · Food · Wellness  
Filter logic: JS `data-cat` attribute matching (already implemented in prototype).

**Brand cards — 3×3 grid (2×2 on tablet, 1 col on mobile):**

Each card uses the brand's **official logo** via `<Image>`:

```tsx
interface BrandCard {
  name: string;
  slug: string;          // matches /images/brands/{slug}.svg
  logoFallbackBg: string; // color if logo fails to load
  logoFallbackText: string; // initials
  category: string;
  replyTime: string;
  replyRate: number;
  replyRateClass: 'hi' | 'mid' | 'lo'; // hi≥35%, mid≥25%, lo<25%
  locked: boolean;
}
```

Logo implementation:
```tsx
<div className="brand-logo-wrap">
  <Image
    src={`/images/brands/${brand.slug}.svg`}
    alt={`${brand.name} logo`}
    width={44} height={44}
    onError={() => setLogoError(true)}
  />
  {/* fallback: colored letter block */}
  {logoError && (
    <div className="brand-logo-fallback" style={{ background: brand.logoFallbackBg }}>
      {brand.logoFallbackText}
    </div>
  )}
</div>
```

Logo sources (free/official):
- Rhode Skin: `rhode.com` brand assets
- Anua: brand press kit
- Oh Polly: `ohpolly.com` press
- Fenty Beauty: Fenty press kit
- For missing logos: use clean colored letter block as fallback (already styled)

9th card: locked "492 more brands" call-to-action card.

Directory footer: `Showing 8 of 500+ brands · Updated weekly` + `Unlock all contacts →` button → `/register`

---

### 4.9 Feature Sections (4 repeating)

Alternating layout: text-left/visual-right, then text-right/visual-left.  
Use a single `<FeatureSection>` component with props:

```tsx
interface FeatureSectionProps {
  tag: string;
  headline: string;
  body: string;
  bullets: string[];
  callout?: string;
  visual: React.ReactNode;
  reverse?: boolean;
  bg?: string;
}
```

**F1 — Auto Media Kit** (text left, bg `#FAFAF9`)  
Headline: `Your media kit, generated automatically. Every pitch looks professional.`  
Body: `Brands won't reply without one. newcollab builds your media kit from your profile — stats, audience, niche — and attaches it to every pitch automatically. No design tools. Done in 30 seconds.`

**F2 — AI Pitch Writer** (text right, bg `#fff`)  
Headline: `A perfect pitch in 60 seconds. Send to 10 brands at once.`  
Body: `AI writes a personalised email for each brand — their name, their niche, why you're the right fit. Media kit attaches automatically. Batch-pitch up to 10 brands in one session on Pro.`

**F3 — PR Pipeline** (text left, bg `#FAFAF9`)  
Headline: `Track every pitch. Follow up before it goes cold.`  
Body: `Every brand you contact lives in your pipeline. We remind you to follow up at day 7 — when most brands respond. Never lose a warm lead again.`  
Callout: `Creators who follow up are 3× more likely to land the deal. We make sure you never forget.`

**F4 — For You Feed** (text right, bg `#fff`)  
Headline: `The right brands, delivered to you every week.`  
Body: `Based on your niche, platform, and following — newcollab surfaces the brands most likely to reply to you right now. Hot this week. Seasonal campaigns. Brands that just responded to creators like you.`

**Mockup visuals** in the HTML prototype are accurate — implement as styled-components matching the prototype exactly.  
Brand logos inside mockups: same `<Image>` with fallback approach as directory.

---

### 4.10 Social Proof

**Activity feed (notification wall):**  
4 pill-style notifications, staggered `animation-delay` for slide-in effect.

```
@carolstyle received a Rhode Skin PR package · 2 hours ago
@glowwith_m got a reply from Anua · 5 hours ago
@sarahlooks pitched 8 brands this afternoon · today
@zionne019 landed her first brand deal — 5.3K followers · yesterday
```

**Stats row (4 cards):**
- 900+ Active creators
- 500+ Brand PR contacts
- 52% Top reply rate
- 363 Pitches sent this month

**Testimonials (3 cards):**  
Format: avatar + handle + niche → star rating → green result box → italic quote.

All 3 testimonials are real creator stories (see HTML prototype for exact copy).  
If actual creator photos available — use `<Image>` in the avatar slot instead of initial.

---

### 4.11 How It Works
3-step horizontal grid.

```
1. Find your brands
Browse 500+ PR contacts by niche and reply speed. Or check your For You feed — matched brands, every week.

2. Send a complete pitch
AI writes a personalised email. Media kit auto-attached. One brand or ten — done in the same session.

3. Track, follow up, win
Your pipeline tracks everything. Day-7 reminder fires automatically. Brand replies? Log it. Package ships? Mark it won.
```

CTA below: `Start landing brand deals →` + `Free plan · No credit card · Ready in 2 minutes`

---

### 4.12 Pricing
Two-column card grid (stacks on mobile).

Free card: standard white, `$0`, list of 5 included + 4 locked features.  
Pro card: 2px black border, `Most popular` badge, gradient `$12` price text, 7 included features.

Pro price gradient CSS:
```css
.pricing-price-pro {
  background: linear-gradient(135deg, #E11D48, #7C3AED);
  -webkit-background-clip: text;
  -webkit-text-fill-color: transparent;
}
```

---

### 4.13 FAQ
5 questions using native `<details>/<summary>` — no JS accordion needed. Preserves SEO crawlability.

```
Q: How do I get my first brand deal as a small creator?
Q: Do I need a media kit to pitch brands?
Q: How many followers do you need to work with brands?
Q: What is a PR package?
Q: Is newcollab free to use?
```

Answers in the HTML prototype are final copy. Keep them.

---

### 4.14 Final CTA + Footer
No changes from existing footer structure.  
Update footer description to: `The complete brand outreach tool for nano and micro creators.`  
Keep all existing footer links — they carry SEO value.

---

## 5. Emoji Usage Guidelines

The rule: **emoji where it aids scansion or mimics a UI element — never as decoration.**

| Context | Emoji use | Reason |
|---|---|---|
| Ticker items | One per item max | Social feed convention — feels native |
| Floating badges on hero GIF | One per badge | Mimics app notifications |
| Notification wall items | One per item | Mimics app notifications |
| Pipeline mockup (nudge row) | One clock icon | Matches app UI |
| Feature tags | None | Text tags read cleaner |
| Problem card headlines | None | Human copywriting, not AI listicle |
| Section eyebrows | None | Remove all emoji from eyebrow labels |
| Clarity strip | SVG icons only | More professional than emoji |
| Solution flow steps | SVG icons only | Consistency with rest of page |
| How it works steps | None | Clean numbered steps |
| Pricing check marks | ✓ / — text characters | Not emoji |
| Footer | None | |
| CTA buttons | → arrow only | Standard UX convention |

**Copy tone:** Write like a founder talking to a creator friend — direct, warm, zero filler words. No AI giveaways: "dive into", "seamlessly", "leverage", "game-changing", "robust", "comprehensive". Read every line out loud before shipping.

---

## 6. SEO / GEO Optimization

### 6.1 Existing signals to preserve
- Keep all existing URL slugs and meta tags as the base
- Do not rename, move, or redirect the homepage
- Keep all existing `<link rel="canonical">` tags
- Keep all existing internal links in footer (they build topic authority)

### 6.2 Meta updates — `app/layout.tsx`

```tsx
export const metadata: Metadata = {
  title: 'newcollab — Brand Outreach Tool for Micro Creators | Find Brands, Pitch, Track',
  description: 'Find 500+ brand PR contacts, auto-generate your media kit, send AI-powered pitch emails, and track every outreach in one place. The complete brand deal system for micro creators.',
  openGraph: {
    title: 'newcollab — The Complete Brand Outreach Tool for Creators',
    description: 'Stop guessing how to get brand deals. Brand contacts, AI pitch emails, auto media kit, outreach tracking — all in one place.',
    url: 'https://newcollab.co',
    siteName: 'newcollab',
    images: [{ url: '/og-image.jpg', width: 1200, height: 630 }],
    type: 'website',
  },
  twitter: {
    card: 'summary_large_image',
    title: 'newcollab — Brand Outreach for Micro Creators',
    description: 'The complete brand deal system for nano and micro creators.',
  },
  alternates: {
    canonical: 'https://newcollab.co',
  },
};
```

### 6.3 JSON-LD structured data
Add to `app/page.tsx` (or `layout.tsx`). Keep the existing FAQ schema. Add a `WebSite` schema alongside it:

```tsx
const websiteSchema = {
  "@context": "https://schema.org",
  "@type": "WebSite",
  "name": "newcollab",
  "url": "https://newcollab.co",
  "description": "Brand outreach tool for nano and micro creators",
  "potentialAction": {
    "@type": "SearchAction",
    "target": "https://newcollab.co/brands?q={search_term_string}",
    "query-input": "required name=search_term_string"
  }
};

const faqSchema = { /* existing FAQ JSON-LD from prototype */ };

// In page.tsx:
<Script type="application/ld+json" id="schema-website">
  {JSON.stringify(websiteSchema)}
</Script>
<Script type="application/ld+json" id="schema-faq">
  {JSON.stringify(faqSchema)}
</Script>
```

### 6.4 H1/H2/H3 hierarchy
The page must have exactly **one H1**. Every section heading is H2. Feature subheadings are H3.

```
H1: Land brand deals. On repeat. No guessing.
H2: Getting brand deals feels impossible. It doesn't have to.   (problem)
H2: Not just a tool. The complete outreach flow.                (solution)
H2: 500+ brands open to creators. Real PR contacts.            (directory)
  H3: Your media kit, generated automatically.                  (feature 1)
  H3: A perfect pitch in 60 seconds.                            (feature 2)
  H3: Track every pitch. Follow up before it goes cold.         (feature 3)
  H3: The right brands, delivered to you every week.            (feature 4)
H2: Real results. Real creators. Just like you.                (proof)
H2: Your first brand deal in 3 steps.                          (how it works)
H2: Free to start. Upgrade when you're ready.                  (pricing)
H2: Quick answers.                                              (FAQ)
H2: Your first brand deal is one pitch away.                   (CTA)
```

### 6.5 Target keywords (primary)
Map each to a heading or section — don't stuff, just ensure natural coverage:

| Keyword | Target section |
|---|---|
| how to get brand deals as a creator | H1 + problem section |
| brand outreach tool for influencers | Meta description + hero subline |
| PR packages for micro creators | Problem section + FAQ |
| how to contact brands for PR | FAQ answer |
| media kit for content creators | Feature 1 heading |
| micro influencer brand deals | Social proof section |
| how to pitch brands as a small creator | FAQ answer + how it works |
| brand PR contacts list | Brand directory H2 |

### 6.6 GEO (Generative Engine Optimization — AI search)
These practices help newcollab appear in ChatGPT, Perplexity, and Google AI Overviews:

1. **FAQ answers should be self-contained.** Each answer should fully answer the question in 2–4 sentences without requiring the user to click through. AI extracts these verbatim.

2. **Use "newcollab" in the first sentence of key answers.** AI attribution requires brand mentions close to the answer.
   - Example: "newcollab gives you direct PR contact details for 500+ brands..."

3. **Define terms.** Add a brief definition of "PR package" and "media kit" in their relevant FAQ answers — AI Overview triggers on definitional content.

4. **Add a stat per key claim.** "63% of brands prefer working with micro creators" — sourced stats get cited by AI engines.

5. **Blog posts (existing `/blog/`):** Keep existing posts. Ensure they link back to the homepage with anchor text matching target keywords.  
   - `/blog/how-to-get-brand-deals` → link: "brand outreach tool for creators"
   - `/blog/media-kit-guide` → link: "auto-generated media kit"

---

## 7. Mobile Optimization Checklist

All breakpoints already defined in the CSS prototype. During implementation, verify:

- [ ] Nav: links + sign in hidden on ≤768px, logo + CTA only
- [ ] Hero: single column at ≤800px, GIF above text on mobile
- [ ] Clarity strip: 2×2 grid at ≤580px, no arrow connectors
- [ ] Lifestyle row: horizontal scroll on mobile (overflow-x: auto), min-width per image
- [ ] Problem cards: single column at ≤560px
- [ ] Solution flow: horizontal scroll (overflow-x: auto) — steps don't wrap, they scroll sideways
- [ ] Brand directory grid: 3-col → 2-col at ≤680px → 1-col at ≤420px
- [ ] Feature rows: single column at ≤800px, reverse-direction resets to ltr
- [ ] Stats row: 2×2 at ≤560px
- [ ] Testimonials: single column at ≤700px
- [ ] Pricing cards: single column at ≤520px
- [ ] Footer columns: 2-col grid at ≤600px
- [ ] Touch targets: all buttons and links minimum 44px tap area
- [ ] Ticker: overflow hidden, no horizontal scroll leak
- [ ] Font sizes: use `clamp()` for H1 and H2 (already in CSS)
- [ ] Floating badges on hero: hide on screens <400px to avoid overlap

---

## 8. Image Optimization

### Brand logos
- Format: SVG preferred (sharp at all sizes, tiny file)
- Fallback: `onError` callback renders colored letter block
- Dimensions: 44×44px display size, serve at 88×88 for retina
- Alt text: `"{Brand Name} logo"`

```tsx
<Image
  src={`/images/brands/${slug}.svg`}
  alt={`${name} logo`}
  width={44}
  height={44}
  onError={() => setImgError(true)}
/>
```

### Lifestyle photos
- Format: JPEG, compress to ≤150KB per image before adding to `/public`
- Use `next/image` with `fill` prop inside a `position:relative` container
- Add `sizes` prop for responsive delivery:
  ```tsx
  sizes="(max-width: 600px) 140px, 25vw"
  ```
- Alt text: descriptive, no keyword stuffing
  - `"Content creator unboxing a beauty PR package"`
  - `"Creator writing a brand pitch email on laptop"`
  - `"Skincare PR package contents laid out on table"`
  - `"Creator filming content for brand collaboration"`

### Hero GIF
```tsx
<Image
  src="/assets/hero.gif"
  alt="Creator landing a brand deal using newcollab"
  width={600}
  height={450}
  unoptimized   // required — Next.js cannot optimize GIFs
  priority      // above the fold, load immediately
/>
```

### OG image
Create `/public/og-image.jpg` at 1200×630px:
- Background: `#0F0F0F`
- Logo top-left
- Large text: "Land brand deals. On repeat."
- Sub text: "The complete brand outreach system for creators."
- Rose gradient accent element

---

## 9. Existing Routes / Pages — Do Not Touch

These pages carry existing SEO juice. Do not modify their URLs, meta, or content:

```
/login          → existing
/register       → existing (signup flow v4 — separate brief)
/dashboard      → existing
/brands         → existing brand directory full page
/blog/*         → existing blog posts
/pricing        → existing (or redirect to #pricing anchor on homepage)
/privacy        → existing
/terms          → existing
```

Only `app/page.tsx` (the homepage) and `app/layout.tsx` (shared meta) are in scope for this brief.

---

## 10. Implementation Order (recommended)

1. Update `layout.tsx` — meta, OG, JSON-LD, canonical
2. Add brand logo files to `/public/images/brands/`
3. Add lifestyle photos to `/public/images/lifestyle/`
4. Update `app/page.tsx` — section by section, top to bottom
5. Style updates — apply all CSS changes via styled-components (or existing CSS module approach)
6. Run Lighthouse mobile audit — target ≥90 Performance, ≥95 SEO, ≥95 Accessibility
7. Test all filter interactions in brand directory
8. Test all internal links (pricing anchor, #brands anchor, /register CTAs)
9. Verify JSON-LD with Google Rich Results Test
10. QA mobile: test on iPhone SE (375px) and standard (390px) viewports

---

## 11. Copy — Emoji Audit (find/replace in final code)

Remove or replace the following before shipping:

| Find | Replace with |
|---|---|
| `🔍` in eyebrow/section labels | Remove — text only |
| `📄` in feature tags | Remove — text only |
| `✍️` in feature tags | Remove — text only |
| `📊` in feature tags | Remove — text only |
| `✨` in "For You" feature tag | Remove — "For You Feed" |
| Problem card mood icons (🔍📄✍️📭) | Replace with small SVG icon or colored dot |
| Solution flow step icons (🔍📄✍️📊🎁) | Replace with SVG icons |
| Clarity strip icons | Replace with SVG icons |
| Ticker items | Keep one emoji per item — social/notification context |
| Floating hero badges | Keep — mimics app notification UI |
| Notification wall | Keep — mimics app notification UI |
| Pipeline mockup (⏰) | Keep — matches actual app UI |
| Pricing check marks (✓) | Keep — standard UI pattern, not decorative emoji |

---

## 12. Deliverables

- [ ] Updated `app/page.tsx` with all section JSX
- [ ] Updated `app/layout.tsx` with new meta + JSON-LD
- [ ] New `components/landing/LifestyleRow.tsx`
- [ ] 8× brand logo SVGs in `/public/images/brands/`
- [ ] 4× lifestyle JPEGs in `/public/images/lifestyle/`
- [ ] OG image at `/public/og-image.jpg`
- [ ] Lighthouse mobile score ≥90 on all categories
- [ ] Verified in Google Rich Results Test (FAQ schema)
- [ ] QA pass: mobile (375px), tablet (768px), desktop (1280px)
