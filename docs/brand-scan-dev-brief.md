# Brand Scan: dev brief

**Owner:** Mazza · **Status:** v1 in build on `claude/analyze-newcollab-app-agoHM` (backend + frontend) · **Goal:** brand acquisition and brand MRR

---

## 1. Why we're building this

Creator MRR won't reach $10K alone, and churn keeps eating it. Brands pay **$299/mo** after a free first campaign, so **34 paying brands is $10K MRR**. Today the only brand channel is cold outreach, and "we have vetted creators" doesn't stand out in an inbox.

**Brand Scan** is a free tool that answers a question every DTC founder has:

> "Which creators are posting about my competitor on TikTok, what hooks are they using, and what's working?"

A brand types in any brand name (usually a competitor) and gets an instant, scannable report:
- **Who:** every creator posting about that brand, with followers and posts.
- **What works:** top videos by views and the hooks that repeat across creators. This exposes a brand's creator script, like the Thea example in Lightreel.
- **How:** whether the creators were gifted, paid or on affiliate.
- **The bridge:** "Newcollab creators who fit this profile and are open to gifting your brand", followed by **Try your first campaign free**.

Lightreel stops at research. We go from research to content: the scan shows the brand what's working, and the roster gets them the same creators.

### Success metrics (weekly)

| Metric | What it tells us |
|---|---|
| Scans run (unique brands) | Does the hook pull people in |
| Email unlocks / scans | Is the full report worth an email |
| Rosters started from the scan (`utm_campaign=brand_scan`) | Does the bridge convert |
| Paid brands from the scan | MRR |
| Reply rate on scan-led cold emails vs. the current template | Does it make outbound better |

---

## 2. Scope

### v1 (this build)
1. Public **Brand Scan** tool page at `/brands/scan`: an AI-chat-style input and a results feed.
2. Backend: database-first scan, an Apify top-up when the brand has no fresh data, and polling while it searches.
3. **Email unlock** for the full report, stored as a lead, with a notification to the team.
4. **Matched Newcollab creators** from the existing creator search, with a CTA to `/brands/launch` pre-filled with the niche.
5. **Admin warm endpoint** so Hermes can pre-run scans for outreach prospects. The cold email then links to `/brands/scan?b=<brand>`, which opens instantly.
6. **Landing page refresh** of `/brands/pr-packages` (our main brand SEO page, sitemap priority 0.9):
   - The Brand Scan input in the hero, like Lightreel's landing page.
   - Main CTA: **Try your first campaign free**.

> **Update (live):** the email unlock is removed from the page. Public scans return the full report (top 20 creators) and end on one CTA, **Try your first campaign free**, so nothing sits between the brand and the campaign signup. The `/unlock` endpoint stays in the API but the page no longer calls it.

### v2 (not now)
- **Alerts** (paid retention): "Email me when [competitor] gets a new creator video." It runs weekly off the same cache.
- Instagram source.
- OCR of on-screen text for better hook grouping.
- Saved scans in the brand dashboard.
- Scans as a Polly brand-side chat.

### Out of scope
- New auth, a new database stack, or new scraping vendors.
- Any change to the creator-side Radar behaviour.

---

## 3. Build on what exists (no heavy dev)

| Need | Already in the codebase | How Brand Scan uses it |
|---|---|---|
| TikTok search | `services/radar_source.py` (Apify `clockworks~tiktok-scraper`, async start/poll, `APIFY_TOKEN` server-side only) | Same actor, run with the brand's hashtags and searches |
| Video store | `tiktok_videos`, `tiktok_creators` (`migrations/add_gifting_radar_tables.sql`) | Scans read from it first. Every scan also warms the creator Radar |
| Query cache | `apify_query_cache` (24h hashtags, 3 days searches) | Same keys, so the same brand is never paid for twice inside the TTL |
| Run tracking and daily spend cap | `discovery_runs`, `gifting_radar.spend_today()`, `_finish()` (claim, ingest, cost) | A scan is a run with `niche = 'scan:<brand_key>'` and `params.kind = 'brand_scan'`. `finalize_stale()` already ingests runs whose tab was closed |
| Brand name normalising | `radar_extract.brand_key / display_name / key_variants`, `signal_types` (gifted/paid/affiliate regexes), `video_niches` | Matching posts to the brand and labelling each post |
| Brand directory | `pr_brands` via `gifting_radar.load_directory()` | Gives a known brand's TikTok handle, category and logo |
| Matched creators | `services/creator_directory_search.search_creators()` (cached, public-safe `serialize`) | The "creators like these" block |
| Roster CTA | `/brands/launch?niche=…` (`launch_url()`) | The CTA destination, with `utm_campaign=brand_scan` |
| Thumbnails | `media_proxy_routes.to_proxied_media_url` (TikTok covers expire) | All video covers go through the proxy |
| Email | `services/resend_mail.send_resend_email` | Lead alert to the team |
| Admin auth | `routes.admin_email.admin_required` (`X-Admin-Token`) | The Hermes warm endpoint |
| Rate limiting | Redis `SESSION_REDIS` pattern from `routes/integrations.py` | Per-IP limit on public scans |

**New code is small:**
- Backend: `services/brand_scan.py` (logic), `routes/brand_scan.py` (API), one `brand_scan_leads` table created idempotently at runtime, and tests.
- Frontend:
  - `src/app/brands/scan/*` (tool page)
  - shared UI in `src/app/brands/scan/components/*`
  - an edit to `src/app/brands/pr-packages/*`

---

## 4. Backend

### 4.1 Matching posts to a brand
Input: a free-text brand (e.g. `Glow Recipe`, `@glowrecipe`, `glowrecipe.com`).

1. Turn the input into a brand key with `brand_key()` (`glowrecipe`), plus a display name.
2. Resolve it against `pr_brands`, using the directory keyed by handle, name and domain. If it's found, use its TikTok handle, category and logo.
3. A video **matches** if any of these is true:
   - `mentions` contains the brand key or handle;
   - `hashtags` contains the key, or `<key>partner`, `<key>ambassador`, `<key>creator`, `<key>ugc` or `<key>pr`;
   - the caption contains the display name as whole words (min 4 chars, to avoid generic matches).
4. Exclude the brand's own account (`creator_handle` equal to the key or handle) and retail and agency accounts (`RETAILERS`).
5. Window: last 180 days. TikTok creator programs run long, and the radar window of 90 days is too short for a competitor view.

### 4.2 Database first, Apify top-up
- **Ready:** if the store has at least 8 matching videos and a scan of this brand finished in the last 72h, return the report instantly.
- **Otherwise,** start one Apify run:
  - hashtags `[key, key+"partner", key+"ambassador"]`;
  - searches `[display name, display name + " review"]`;
  - 20 per query, so about 100 results and roughly $0.38 at the estimate in `radar_source`.
- **Hashtags first:** branded hashtags are the strongest signal, and they're what reveals the creator script.
- **One run per brand at a time:** an in-flight run for the same `scan:<key>` is reused, so two people scanning the same brand share one run.
- **Guards:**
  - Global daily cap `BRAND_SCAN_DAILY_CAP_USD` (default $10), checked with `spend_today()`, which is shared with the Radar.
  - Per-IP Apify starts: 3/day.
  - Per-IP requests: 20/min.
- **When a guard trips,** return whatever the database has (it may be empty) with `note: "limit"`. The UI then says "Leave your email and we'll send the full scan", which still captures the lead.

### 4.3 Report shape (`GET`/`POST` responses)
```json
{
  "status": "ready",              // ready | searching | empty
  "scan_id": 123,
  "brand": {"key": "glowrecipe", "name": "Glow Recipe", "logo_url": null, "niche": "skincare"},
  "summary": "Glow Recipe has 41 creators posting on TikTok in the last 6 months. Most are small (median 8.2K followers) and 60% of posts are gifted. The top hook, “when my skin is basically clear but…”, ran on 9 videos with 820K views.",
  "stats": {"videos": 63, "creators": 41, "views": 3120000, "median_followers": 8200,
            "small_creator_share": 0.78, "mix": {"gifted": 0.6, "paid": 0.2, "affiliate": 0.1, "organic": 0.1}},
  "top_videos": [{"url": "…", "thumb": "…", "views": 444200, "likes": 1800, "creator": "@x",
                  "followers": 8200, "age": "76d", "hook": "when i say my skin is basically clear but i…",
                  "type": "gifted"}],
  "hooks": [{"hook": "when i say my skin is basically clear but i…", "videos": 9, "creators": 7, "views": 820000,
             "examples": [/* up to 3 video cards */]}],
  "creators": [{"handle": "@x", "followers": 8200, "videos": 3, "best_views": 444200, "types": ["gifted"]}],
  "matches": {"total": 214, "niche": "skincare", "creators": [/* creator_directory_search cards */],
              "cta_url": "https://newcollab.co/brands/launch?niche=skincare&utm_campaign=brand_scan…"},
  "locked": true,                 // free view: top 6 videos, top 3 hooks, top 5 creators, 3 matches
  "unlock": {"videos": 57, "hooks": 5, "creators": 36}
}
```
- **Summary:** built by a template from the stats, with no LLM call. It's instant, costs nothing and never invents numbers. It reads like an assistant reply, the way Lightreel's does.
- **Hooks:** the first line of each caption with hashtags, mentions and emoji removed, cut to the first 6 words and lowercased. Captions sharing that key are grouped, and a hook only counts if 2 or more creators used it. That's simple and explainable, and it catches scripted creator programs. Embeddings and OCR can come in v2.
- **Post type:** `signal_types()` gives gifted, paid or affiliate. Anything else counts as organic.

### 4.4 Endpoints (`routes/brand_scan.py`, blueprint `brand_scan_bp`)

| Method | Path | Auth | Purpose |
|---|---|---|---|
| POST | `/api/public/brand-scan` | none, rate limited | `{brand, own_brand?, ref?}` returns a report (`ready`/`empty`) or `{status: "searching", scan_id}` |
| GET | `/api/public/brand-scan/<scan_id>` | none, rate limited | Poll every 3s. Moves the Apify run forward, then returns the report |
| POST | `/api/public/brand-scan/unlock` | none, rate limited | `{brand, email, website?, ref?}`: stores the lead, notifies the team, returns the full report (`locked: false`) |
| POST | `/api/admin/brand-scan/warm` | `X-Admin-Token` | `{brands: [..]}` (max 10). Pre-runs scans for Hermes. Returns `{brand, status, scan_id, videos}` per brand |

**Lead table** (created idempotently on first unlock):
```sql
CREATE TABLE IF NOT EXISTS brand_scan_leads (
  id SERIAL PRIMARY KEY,
  email TEXT NOT NULL,
  scanned_brand TEXT NOT NULL,
  website TEXT,
  ref TEXT,               -- 'hermes', 'seo', 'landing', utm_source…
  ip TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS brand_scan_leads_email_idx ON brand_scan_leads (lower(email));
ALTER TABLE brand_scan_leads ENABLE ROW LEVEL SECURITY;
```
- **Notify:** email `BRAND_SCAN_NOTIFY_EMAIL` (default `team@newcollab.co`): "New Brand Scan lead: <email> scanned <brand>". A 2-hour reply wins the deal.
- **Validation:** email format check, 254 chars max. Lowercase it.

### 4.5 Env
- `APIFY_TOKEN`: already set for the Radar.
- `BRAND_SCAN_DAILY_CAP_USD`: default 10.
- `BRAND_SCAN_NOTIFY_EMAIL`: default `team@newcollab.co`.

---

## 5. Frontend

### 5.1 Tool page `/brands/scan` (Next.js app dir, styled-components like the rest of `src/app/brands`)
Lightreel-clean: white, lots of air, one accent (our pink `#e8395f`), Inter/system font, 12–16px radius cards, subtle borders (`#e5e7eb`), no heavy shadows.

**Empty state, styled like an AI chat:**
- Title: **"Which brand's creators do you want to see?"**
- One large rounded input: "Type a brand, e.g. Glow Recipe or @glowrecipe", with a round pink submit arrow.
- Under the input, a small line: "Live TikTok data · free · no login".
- **Prompt library** (chips that run a scan):
  - "Glow Recipe"
  - "Summer Fridays"
  - "Bubble Skincare"
  - "Olipop"
  - "Ghost Energy"
  - "Your competitor →", which focuses the input.

**Searching state, styled like an LLM "thinking":**
- The user's query shows as a chat bubble on the right.
- A status block with steps that tick through, driven by poll responses:
  1. "Searching TikTok for posts about Glow Recipe…"
  2. "Found 63 posts. Grouping creators and hooks…"
  3. "Matching creators on Newcollab…"
- A skeleton feed shows underneath, so the page never looks frozen.

**Results feed, styled like an assistant answer.** Easy to scan, top to bottom:
1. **Summary paragraph**, with key numbers in bold.
2. **Stat chips:** Creators · Videos · Total views · Median followers · Gifted %.
3. **Top videos:** a horizontal row of 9:16 cover cards with views and age. Clicking opens the TikTok post.
4. **Hooks that repeat:** one row per hook (the quote, then "9 videos · 7 creators · 820K views"), with 3 small thumbs. This is the Thea-panel insight.
5. **Creators posting:** a compact list (handle, followers, posts, best views, type badge).
6. **The bridge card** (pink tint):
   - "**214 Newcollab creators in skincare fit this profile and are open to gifting.**"
   - 3 to 6 creator cards.
   - Primary button **Try your first campaign free**, linking to `matches.cta_url`. Secondary text: "5 creators · 5–10 videos · 6-month ad rights · $0 platform fee".
7. **Locked block:** blurred extra rows with "See all 41 creators, 57 videos and every hook". Email field plus a **Unlock full report** button. After unlock, the full feed renders in place.
8. **Follow-up chips** at the bottom, like LLM suggestions: "Scan another brand", "Compare with [competitor]" (prefilled), "Try your first campaign free".

**Behaviour:**
- `?b=<brand>` auto-runs a scan. Hermes links and landing-page searches use it.
- `?ref=` is kept for lead attribution.
- Polling runs every 3s and stops after 4 minutes. The timeout shows "Taking longer than usual. Leave your email and we'll send it."
- **Events:** GA `gtag` events `brand_scan_run`, `brand_scan_ready`, `brand_scan_unlock`, and `brand_cta_click` (location `scan_bridge`), using the existing `trackBrandCtaClick`.
- **SEO:** page metadata targets "see creators posting about any brand", "TikTok creator lookup", "competitor influencer analysis". Indexable, and added to the sitemap.

### 5.2 Landing page refresh `/brands/pr-packages`
Keep the SEO content (FAQ, compare table, pricing, JSON-LD). Change the top half:
- **Hero:**
  - H1: **"See who's posting for your competitors. Get them posting for you."**
  - Sub: "Scan any brand to see the TikTok creators, hooks and videos behind its growth. Then gift vetted creators who'll make the same content for you. First campaign free."
  - **Scan input** right in the hero, like Lightreel's landing page. Submitting goes to `/brands/scan?b=…&ref=landing`.
  - **Main CTA: Try your first campaign free.** Secondary: "Scan a brand".
- **New section "What a scan shows you":** 3 cards (Creators · Hooks that repeat · Videos that win) with a static product preview built in HTML, no screenshot.
- **Everywhere:** every "Start free campaign" button becomes **"Try your first campaign free"**.
- **Metadata:** adds Brand Scan keywords. The title stays on our core money keyword.

---

## 6. Outreach use (Hermes)
1. Hermes calls `POST /api/admin/brand-scan/warm` with the prospect's top competitor (and the prospect itself) a few minutes before sending.
2. If the scan has at least 5 videos, the email uses its numbers and links to `https://newcollab.co/brands/scan?b=<competitor>&ref=hermes`. That link opens instantly from cache.
3. If the scan is thin (fewer than 5 videos), Hermes falls back to the current template.

Email 1 (no link, ask for the reply):
> Subject: [Competitor]'s creator script
>
> Hi [Name], [Competitor] has [N] creators posting on TikTok right now, and [hook count] of them use the same hook ("[hook]"). Best one hit [views] views.
>
> I pulled the breakdown for [Brand], plus creators in [niche] who are open to doing the same for you. Want it?

Email 2 (day 3): the scan link. Email 3 (day 7): "Your first campaign is free".

---

## 7. QA checklist
- [ ] A known brand (in `pr_brands`) resolves to its handle and logo. An unknown brand still scans by name.
- [ ] A brand's own account and retailers are excluded from creators.
- [ ] A second scan of the same brand within 72h is instant and starts no Apify run (check `discovery_runs.served_from = 'database'`).
- [ ] Two simultaneous scans of the same brand share one run.
- [ ] The daily cap and per-IP limit return the database result with `note`, never a 500.
- [ ] Locked responses never include the locked rows (check the network tab).
- [ ] Unlock stores a lead, sends the team email, and returns `locked: false`.
- [ ] Thumbnails load through the media proxy and fall back to a neutral tile.
- [ ] Mobile at 375px: the input, feed and video rows scroll horizontally without page overflow.
- [ ] `/brands/pr-packages` still renders its JSON-LD and FAQ, and the CTAs carry UTMs.

---

## 8. Rollout
1. Deploy the backend. The lead table creates itself on first unlock.
2. Deploy the frontend, and run 5 scans by hand on niche leaders to warm the cache (skincare, haircare, supplements, drinks, pet).
3. Turn on the Hermes warm step for 50 prospects and compare reply rates against the current template.
4. Review metrics weekly. Build v2 Alerts once 10 or more brands have unlocked a report.
