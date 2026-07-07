# Discover Tab: Universal Brand Lookup, Dev Brief

## Problem

The Discover tab already does the right thing for the 431 brands in the curated directory: search, see response rate, hit Contact, get the email and a generated pitch, all without waiting on a reply. That part works and should not change.

The gap is what happens when a creator searches for a brand that isn't in those 431. Right now that's a dead end. Per the GSC keyword data pulled from the marketing site, the actual demand is long tail: hundreds of distinct queries for specific brand PR contacts (tower 28, summer fridays, poppi, milk makeup, good molecules, aloyoga, and many more), most of which will never make it into a manually curated list of 431. Creators already know which brand they want to pitch. The product currently only helps them if that brand happens to already be curated.

This brief covers extending Discover so a search for any brand, not just the curated 431, returns a usable result: a contact and a pitch, with the same quota and trust bar as the existing curated flow.

## Goal

When a creator searches a brand name with no match in the curated directory, trigger a fallback discovery flow that attempts to find a real, verifiable PR/press contact for that brand and generate a pitch for it, using the same Contact button and quota mechanic already live. Curated and discovered brands should feel like one system to the creator, not two different tools.

## Scope

### 1. Search fallback flow

- Current behavior: search filters within the curated 431.
- New behavior: if a search returns zero matches in the curated set, show a distinct "Not in our directory yet, search the web for [brand name]" state instead of an empty results page.
- Tapping that triggers the discovery pipeline (section 2) for that specific brand name.
- Cache the result (section 5) so the next person searching the same brand gets an instant curated-style result instead of re-running discovery.

### 2. Contact discovery pipeline

Three-tier approach, in order of preference, stop at the first tier that returns a verified result:

**Tier 1, mine existing data.** Cross-check the searched brand name against your own GSC keyword history and any existing creator-submitted contact data. A meaningful number of real brand PR emails are already sitting in your keyword reports because creators literally searched the exact email address (e.g. `pr@goodmolecules.com`, `pr@milkmakeup.com`, `pr@aloyoga.com`). Seed your contact database from this before building anything else; it's free, already-validated-by-search-behavior data.

**Tier 2, structured web lookup.** For brands not already known, search the brand's own domain for a press/PR/contact/partnerships page (pattern match on common paths: `/press`, `/pr`, `/contact`, `/partnerships`, `/influencers`, `/affiliates`) and extract any listed email. This is higher trust than guessing, since it's the brand's own published contact.

**Tier 3, pattern inference, lowest trust.** If no published contact is found, infer a likely address from common patterns (`pr@`, `press@`, `partnerships@`, `marketing@` + domain) but flag this tier explicitly as unverified in the UI (see section 4) and never present it with the same confidence styling as Tier 1 or 2 results.

Do not fabricate a contact when none of the three tiers return anything. Show "We couldn't find a verified contact for this brand yet" rather than guessing blind, since a wrong email sent on the creator's behalf damages their credibility, not yours.

### 3. Pitch generation for discovered brands

- Reuse the existing AI pitch generator and the tiered Starter/Growing/Established strategy already specified for curated brands. The discovery flow should pass the brand name, category (infer from the brand's own site copy if not otherwise known), and the creator's tier into the same generation logic, not a separate prompt path.
- If brand category can't be confidently inferred, default to a generic-but-still-tier-appropriate pitch rather than blocking the flow.

### 4. UI requirements

- Discovered-brand cards should visually match curated-brand cards (same layout, Contact/Save buttons) but carry a small "Found via web search" badge so creators understand the trust level is different from a vetted directory entry.
- For Tier 3 (inferred, unverified) contacts specifically, show an explicit confidence flag, e.g. "Best guess contact, not confirmed" with a tooltip explaining this, so creators aren't misled into thinking it's as reliable as a curated brand.
- The existing quota banner ("3 of 3 brand contacts used this month") applies identically to discovered-brand contacts. Do not create a separate quota; this stays one system.

### 5. Caching and compounding the directory

- Every discovered brand with a verified (Tier 1 or 2) contact gets written into the main brand database, marked `source: discovered`, and becomes searchable like any curated brand for future users.
- This means the long tail compounds over time: the first creator to search "Tower 28" pays the discovery cost once, every creator after gets an instant curated-style hit.
- Track search volume per discovered brand. Any discovered brand that crosses a meaningful search/contact threshold (e.g. searched or contacted 10+ times) should be flagged for manual review and promotion to a fully curated entry with response-rate tracking, the same as your existing 431.

### 6. Rate limiting and cost control

- Tier 2/3 lookups likely involve a paid web search or scraping call. Cap discovery attempts per creator per day (e.g. 5) separate from the monthly contact quota, to prevent abuse (someone spamming random brand names to burn your lookup budget without ever using a contact).
- Log every discovery attempt with outcome (found Tier 1/2/3, or not found) for cost monitoring and to build the backlog described in section 5.

### 7. Analytics to add

- Discovery attempts vs. successful contact found, broken down by tier.
- Top searched-but-not-found brand names, reviewed weekly, since this is a direct signal of where to prioritize manual curation or partnership outreach (the same brand appearing repeatedly in this list is a strong BD lead).
- Discovered-brand contact-to-reply rate once enough volume exists, to validate whether Tier 2/3 contacts perform meaningfully worse than curated Tier 1 ones. If Tier 3 reply rate is near zero, consider removing it rather than letting low-trust contacts erode overall product credibility.

## Implementation sequencing

1. **Phase 1 (fast):** Build the Tier 1 lookup only (mine existing keyword/contact data) plus the fallback search UI and caching. This requires no scraping infrastructure and ships the "not a dead end anymore" experience immediately, even with limited coverage.
2. **Phase 2:** Add Tier 2 structured web lookup and wire pitch generation into the discovered-brand flow.
3. **Phase 3:** Add Tier 3 inferred-pattern fallback with explicit unverified labeling, plus the promotion-to-curated pipeline and analytics dashboard described in sections 5-7.

Ship Phase 1 before building 2 and 3; it validates demand (how often does fallback actually get triggered, and for which brands) before investing in scraping/lookup infrastructure.

## Why this is the right next feature

This is what turns "upgrade to unlock unlimited" from a quota increase on a fixed list into a genuinely open-ended capability: any brand a creator already wants to reach, found and pitched in the same flow they already use, regardless of whether that brand was ever manually curated. It also builds a compounding data asset, every discovery becomes a permanent directory entry, so the curated list grows from real demand instead of manual guesswork about which brands to add next.
