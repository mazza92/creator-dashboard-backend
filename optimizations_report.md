Now I have everything. Here's the complete combined analysis.

---

## Newcollab 90-Day Traffic & Growth Analysis
### GA4 + Google Search Console — March–June 2026

---

## The Big Picture First

You have **two separate traffic realities** that look broken but are actually two separate stories:

| | GSC (Blog) | GA4 (App) |
|---|---|---|
| **Traffic** | 1,295 clicks | 3,394 sessions |
| **Trend** | Declining (-87% from peak) | Growing (+1,400% from W10) |
| **Top channel** | Organic search | Direct (actually blog misattributed) |
| **Top country** | India (53%) | USA (35%) |

The blog and the app are two separate audiences. The blog is declining. The app is growing. This context changes everything.

---

## Critical Bug #1 — 95% Attribution Gap (The Most Important Fix)

**GA4 says:** 60 organic search sessions.  
**GSC says:** 1,295 blog clicks.  
**What's actually happening:** Cross-domain tracking is broken between `newcollab.co` (blog) and `app.newcollab.co` (app).

**Proof from GA4 Section 13 (Pages with Titles):**

```
/login           → title: "The Ultimate PR List Directory: 500+ Brands Sending PR (2026)"
/register/creator → title: "The Ultimate PR List Directory: 500+ Brands Sending PR (2026)"
/dashboard       → title: "The Ultimate PR List Directory: 500+ Brands Sending PR (2026)"
```

The blog post title is bleeding into the app pages. This happens because:
1. User finds your blog post on Google
2. Clicks CTA → redirects to `app.newcollab.co/register/creator`
3. GA4 loses the referrer (cross-domain = no UTM = classified as "Direct")
4. But the page title carries over from the blog session

**The 2,214 "Direct" sessions likely contain ~1,100+ actual blog-to-app visitors.**

**Fix required:** Add UTM parameters to every CTA link from the blog to the app.

All blog CTAs should link to:
```
https://app.newcollab.co/register/creator?utm_source=blog&utm_medium=organic&utm_campaign=pr-list-directory
```

Each blog post gets its own `utm_campaign`. This is a 15-minute fix that will immediately reveal your real conversion funnel.

---

## Critical Bug #2 — pro_upgrade Event Missing

GA4 tracks `onboarding_complete` as the "conversion" event. This means GA4 shows **288 conversions** — but these are profile completions, not paid subscribers.

You have 6 actual paying users. GA4 has no way to tell you which channel, which blog post, or which campaign produced those 6.

**Fix required:** Add a `gtag('event', 'pro_upgrade', {...})` call in your Stripe webhook handler when a subscription activates. Until this is in place, you're flying blind on what actually converts to revenue.

---

## App Growth: The Real Story (Positive)

The app is genuinely growing. GA4 weekly sessions:

| Week | Sessions | Note |
|------|----------|------|
| W10 | 21 | Start of tracking |
| W11 | 129 | +514% |
| W14 | 162 | Growing |
| W16 | 394 | Spike — something worked |
| W17 | 435 | Peak |
| W18–23 | 250–333 | Stabilized at ~300/week |

**300 sessions/week is your new baseline.** That's not a decline from peak — that's a healthy plateau after a growth spike. The app is not in trouble.

---

## Blog SEO: The Real Story (Declining — Needs Attention)

GSC weekly clicks:

| Week | Clicks | Impressions |
|------|--------|-------------|
| W11 | 162 | 3,007 |
| W12 | 166 | 3,102 (peak) |
| W13 | 106 | 2,428 |
| W17–20 | 53–82 | Declining |
| W21 | 144 | 2,012 (bounce?) |
| W22 | 21 | 416 (alarming) |

The decline from 166 → 21 clicks/week is an **87% drop**. W22 could be incomplete data (if this week just started), but the trend from W13 onward is clearly down.

**Likely cause:** Your 2025-dated blog posts are aging out. Posts titled "2025" are losing to fresher "2026" content. You've already published some 2026 posts (`ultimate-2026-directory`) which rank well — you need to refresh or 301-redirect the 2025 URLs.

**Biggest opportunity:** Your top 4 blog posts drive 980 of 1,295 clicks (76% of all traffic). They are:
1. `ultimate-2026-directory-brands-with-open-pr-application-forms` — 364 clicks, pos 8.1
2. `companies-with-open-pr-application-forms-influencers-2025` — 268 clicks, pos 20.2 ← **"2025" is hurting this**
3. `k-beauty-korean-skincare-brands-pr-list-small-creators-2026` — 201 clicks, pos 8.8
4. `pr-list-for-clothing-brands-micro-influencers-2025` — 147 clicks, pos 19.7 ← **"2025" again**

Posts at position 20 with 10%+ CTR are ranking on page 2 and still getting clicks. Move them to page 1 and you 3-5x your organic traffic.

---

## What's Working — Double Down On These

**1. Email is your best acquisition channel**

| Campaign | Sessions | Engagement | Bounce | Conversions |
|----------|----------|------------|--------|-------------|
| weekly_roundup | 794 | 78% | 22% | 18 |
| trigger | 23 | 87% | 13% | 0 |
| jffxyl_ebhaehc | 49 | 31% | **69%** | **0** |

`weekly_roundup` drives your most engaged traffic. At 78% engagement vs 69% for organic, your email list is your highest-quality audience. Thursday/Friday 4-7pm is your peak window — schedule sends there.

`jffxyl_ebhaehc`: 69% bounce, 0 conversions on 49 sessions — this campaign is actively hurting deliverability. Investigate what list/trigger this is (looks like an automated sequence that went wrong).

**2. PR brands page is sticky as hell**

- `/creator/dashboard/pr-brands`: 4,169 views from 565 users = **7.4 page visits per user**
- Average session: 262 seconds on that page
- It's the core product loop. Users keep coming back.

**3. Media kit page has the highest engagement per session**

- `/creator/dashboard/my-kit`: 343 seconds average — highest of any page
- But only 21 users have reached it
- This is your hidden power feature. Users who find it love it.

**4. For You page has 99% engagement rate**

When users reach `/creator/dashboard/for-you`, they stay (262s avg, 99% engagement rate). Only 88 users have found it. It's not a discovery problem on the page — it's a navigation/onboarding problem getting users to it.

---

## Geography: Two Separate Markets

**SEO audience (GSC):** India-first (53%), then Pakistan, Philippines, South Africa — budget-conscious, high-volume searchers looking for PR lists to apply to themselves.

**App audience (GA4):** USA-first (35%), France (15%), India (17%), UK (4%), Canada (3%).

These are very different people. Your blog is optimized for the wrong market but it's still driving sign-ups. The **USA in GSC: 39 clicks at position 36.7** is the problem — your target market can't find your blog. India finds it because they search at much higher volume for these terms.

**The position 36.7 for USA** means your content isn't indexed as US-relevant. This is partly a domain/backlinks problem (Indian traffic = Indian link profile signals). The US-focused post `us-brands-send-pr-micro-influencers-2026-list` has 583 impressions but only 5 clicks at position 11.8 — it's close. One strong US backlink could flip this.

---

## The Funnel (Reconstructed)

Here's what's actually happening, combining both data sources:

```
1,295 blog clicks (GSC)
    ↓ cross-domain (no UTM) → appears as "Direct" in GA4
~843 reach /register/creator (GA4 landing page data)
    ↓ 25% bounce
~630 start registration
    ↓
466 new_account events
    ↓ 62% activation rate (strong!)
288 onboarding_complete
    ↓
~2% upgrade rate (estimated)
6 Pro subscribers ($72 MRR)
```

The funnel is actually **not broken at the top or middle.** 466 signups from ~1,295 blog clicks = 36% sign-up rate from blog readers is very high. The break is at the **onboarding → paid** step.

This validates that the conversion sprint work (pitch modal, For You, kit nudge) is the right priority. The traffic and sign-up engine is working. The monetization engine is not.

---

## The 12 At-Limit Users — Your Highest Priority Right Now

Your admin dashboard shows 12 users who have hit the free contact limit. These are **warm leads who have already used the product and want more.** 

From GA4: `onboarding_complete` = 288 users. 6 are paid. 12 are at-limit. That means ~4% of your activated user base is at the upgrade moment right now.

A personal email from you to each of these 12 — not an automated sequence, a real founder email — will convert some of them this week. Something like:

> "Hey [name], I noticed you've used all 3 of your free contacts on Newcollab — that means you've been actively pitching brands, which is exactly what the app is built for. I wanted to reach out personally. If you're seeing value, I'd love to offer you a discounted first month of Pro to keep the momentum going. Reply here and I'll set it up."

Even a 3/12 response (25%) = $21-45 MRR added this week, plus data on what they liked/didn't.

---

## Priority Action List

**This week (fixes that compound everything else):**

1. **Add UTMs to all blog CTAs** — 15 min fix, reveals your real funnel immediately
2. **Add `pro_upgrade` GA4 event in Stripe webhook** — start tracking real revenue attribution
3. **Email the 12 at-limit users personally** — fastest path to this week's MRR

**This month (SEO recovery):**

4. **Refresh 2025-dated blog posts** — update titles, dates, brand lists. 301-redirect if needed. The "2025" posts at positions 20 are sitting on page 2 with good CTR — they just need freshness signals.
5. **Investigate the 95% bounce on jffxyl_ebhaehc email campaign** — likely a misconfigured drip sequence hitting cold/stale contacts. Fix or pause it before it damages sender reputation.
6. **Fix US SEO signal** — publish one piece specifically targeting US brands ("US brands sending PR to micro-influencers 2026") with a US-focused hook and seek one US creator/marketing blog backlink.

**This quarter (growth infrastructure):**

7. **Create public `/pr-list` landing page** — capture SEO traffic before the auth redirect wall. Currently the homepage has a 51.6% bounce rate because blog visitors hit a login screen. A public, SEO-friendly landing page with a sample brand list converts better and ranks independently.
8. **Cross-domain tracking setup** — configure GA4 cross-domain tracking between `newcollab.co` and `app.newcollab.co` so all attribution is accurate going forward.
9. **Thursday/Friday 6-8pm email sends** — move weekly_roundup to align with peak engagement window (currently Friday shows 53 conversions vs Monday's 34).

---

## One-Line Summary

Your blog is declining and attracting the wrong market, your app is growing and attracting the right market, your email is your best channel, and 95% of your funnel is invisible to your analytics — fix the UTMs and email the 12 at-limit users this week.