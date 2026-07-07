# Newcollab: Credit Unlock Model — Dev Brief

## Objective

Re-monetize the existing product around a single, consistent unit of value: the **brand unlock**. No new features, no rebuilds. Wrap the existing "Generate Pitch → in-app email composer" flow with a credit-gating layer, swap copywriting across every surface to match the new mental model, and migrate existing users in a single coordinated change.

Goal: lift paid conversion from 1.3% to 4-6% within 60 days without breaking any existing feature.

## Strategic frame

The product already delivers the right thing — a verified brand contact + a tailored AI pitch — at the moment a user clicks Generate Pitch. The current free model frames this as "3 free pitches/month," which trains users to think value = sent pitch = brand reply. That ties our value delivery to something we don't control (brand replies).

The new framing reframes the *same exact moment* as "1 brand unlock." The user pays for the unlock; the brand reply becomes a quality signal, not the value contract. This is Hunter.io's mechanic, adapted to our existing flow with minimal code change.

**What changes:** vocabulary, counter mechanic, paywall trigger, and one new persistence rule (an unlocked brand stays unlocked for that user, no double-charge).

**What does not change:** the brand directory, the AI pitch generator, the in-app email composer, the inbox, the For You matching, the Pool, the media kit, the Discover tab, the existing Pro tier price.

## Core concept — the unlock

An **unlock** is created when a user clicks **Pitch Now** (or equivalent) on a brand they have not previously unlocked in the current billing cycle.

What an unlock delivers (same as today, just named):
- Reveals the brand's verified PR/contact email
- Generates the AI-tailored pitch
- Opens the existing in-app email composer
- Enables sending via Newcollab and inbox tracking

What an unlock costs: 1 credit, deducted at the moment Pitch Now is clicked. Free users get 5/month, Pro users get unlimited (matches current Pro promise).

**Critical persistence rule:** once a user has unlocked a brand, that unlock is theirs permanently. Clicking Pitch Now on the same brand later in the same month (or next month, or 6 months from now) does NOT consume another credit. They can re-open the pitch composer, regenerate the pitch, copy the email, anytime — free. This avoids the punitive "I have to pay again to access something I already unlocked" experience that would kill trust.

## Database changes — minimal

Add to `users` table:

```sql
ALTER TABLE users ADD COLUMN unlocks_remaining INT DEFAULT 5;
ALTER TABLE users ADD COLUMN unlocks_tier VARCHAR(20) DEFAULT 'free'; -- 'free' | 'pro'
ALTER TABLE users ADD COLUMN unlocks_reset_at TIMESTAMP;  
-- For free users: set to signup_date + 1 month, rolls monthly
-- For Pro users: NULL (unlimited, no reset needed)
```

New table:

```sql
CREATE TABLE brand_unlocks (
  id BIGSERIAL PRIMARY KEY,
  user_id UUID NOT NULL,
  brand_id UUID NOT NULL,
  unlocked_at TIMESTAMP DEFAULT NOW(),
  UNIQUE(user_id, brand_id)  -- enforces "unlock once, free forever"
);
CREATE INDEX idx_brand_unlocks_user ON brand_unlocks(user_id);
```

That's it. No new tables for pitches, no schema rewrite. The existing pitches/inbox tables stay untouched.

## Backend logic — the single function

One server-side function gates the entire flow:

```python
def attempt_unlock(user_id, brand_id):
    # Already unlocked? Free pass.
    existing = brand_unlocks.find(user_id=user_id, brand_id=brand_id)
    if existing:
        return {"status": "already_unlocked", "credits_used": 0}
    
    # Pro tier? Unlimited, just log.
    user = users.find(user_id)
    if user.unlocks_tier == 'pro':
        brand_unlocks.create(user_id=user_id, brand_id=brand_id)
        return {"status": "unlocked", "credits_used": 0}
    
    # Free tier: check + roll period if needed.
    if user.unlocks_reset_at and now() > user.unlocks_reset_at:
        user.unlocks_remaining = 5
        user.unlocks_reset_at = now() + 1.month
        user.save()
    
    if user.unlocks_remaining <= 0:
        return {"status": "paywall", "credits_used": 0}
    
    # Deduct + create unlock.
    user.unlocks_remaining -= 1
    user.save()
    brand_unlocks.create(user_id=user_id, brand_id=brand_id)
    return {"status": "unlocked", "credits_used": 1, "remaining": user.unlocks_remaining}
```

This function is called from a single place: the existing `Pitch Now` button click handler, before the existing pitch-generation logic runs.

- `already_unlocked` or `unlocked` → proceed to existing pitch generator + email composer flow (no other change)
- `paywall` → open paywall modal instead of pitch generator

## Vocabulary lock — copywriting MUST use these exact terms

To maintain consistency across the product and prevent confusion during the migration, every surface uses the same vocabulary. Lock these:

| Concept | Approved term | NEVER write |
|---|---|---|
| The unit of value | **unlock** (noun), **unlock** (verb) | "pitch," "credit," "search," "token," "tap" |
| The action | **Pitch Now** (button label, unchanged) | "Send pitch," "Open" |
| The counter | **brand unlocks** | "credits," "pitches left" |
| The plan label | **Free · 5 unlocks/month** OR **Pro · unlimited unlocks** | "Free tier," "Pro plan" alone |
| The benefit | **verified brand contact + AI pitch** | "PR email," "introduction" |
| The reset | **resets {{date}}** | "renews," "refills" |
| The upgrade trigger | **Out of unlocks? Get unlimited.** | "You've hit your limit," "Subscription required" |

Apply this dictionary to every screen. Inconsistency here is what makes monetization changes feel broken to users; consistency is what makes them feel intentional.

## Frontend surfaces — exact changes per screen

### 1. For You feed — top banner

**Current:**
```
0 of 3 free pitches sent · Creators who send all 3 are 2.4× more likely to land a reply.
```

**New (Free user):**
```
4 of 5 brand unlocks left this month · Resets July 30
[Get unlimited unlocks →]
```

**New (Pro user):**
```
Pro · unlimited unlocks
[View plan]
```

### 2. Brand card — the Pitch Now button

The button label **stays "Pitch Now."** Do not change it. The user already trained on this label. The change happens *behind* the button.

Add a small annotation under the button for free users only:
```
[ ✉ Pitch Now ]
1 unlock · 4 left
```

For brands already unlocked, the annotation reads:
```
[ ✉ Pitch Now ]
✓ unlocked
```

For Pro users, no annotation needed.

### 3. The pitch composer (existing screen) — no changes

The in-app email composer that opens after Pitch Now stays exactly as it is. Don't touch this UI. It's the existing flow.

The only addition: at the top of the composer, a tiny status line:
```
✓ Brand unlocked · Verified contact below
```
This anchors the value-delivery moment ("I just got something tangible") to the existing composer screen. One line. No layout change.

### 4. The 5-more-matches lock-out section

**Current:**
```
🔒 5 more matches
Unlock 5 more high-converting matches
[Upgrade to Pro →]
Pro members pitch unlimited brands · Average 25% reply rate
```

**New:**
```
🔒 5 more high-quality matches
Unlock them with Pro
[Get unlimited unlocks →]
Pro members unlock unlimited verified contacts · $19/mo
```

### 5. Paywall modal (triggered when free user hits 0 unlocks)

The new modal copy, verbatim:

```
You've used all 5 brand unlocks this month.

Each unlock gives you a verified PR contact and a tailored pitch — 
the things brands actually read.

Get unlimited unlocks for $19/month.

[ Unlock Unlimited — $19/mo ]
[ Wait until {{reset_date}} ]
```

Three things to note in the copy:
- Reaffirms what an unlock IS (re-anchors value at the moment of friction)
- Soft second option ("wait") keeps it non-punitive
- Reset date is explicit (no mystery)

### 6. Pricing page — copywriting

| Free | Pro |
|---|---|
| 5 brand unlocks per month | **Unlimited brand unlocks** |
| Browse all brands | Browse all brands |
| AI pitch generator | AI pitch generator |
| Send via Newcollab inbox | Send via Newcollab inbox |
| Reply tracking | Reply tracking + analytics |
| | + Pitch Lab scoring (when shipped) |
| | + Media kit advanced templates (when shipped) |
| **$0** | **$19/month** |
| [ Current plan ] | [ Get unlimited unlocks → ] |

### 7. Inbox — no changes

The Inbox tab and reply tracking stay identical. This is critical: the unlock model doesn't break the existing "see brand replies in one place" UX. Inbox just becomes a downstream benefit of having unlocked brands.

### 8. Discover tab — no changes to layout

The Discover search + filter + brand grid stays identical. The only change is the same button-level annotation as the For You cards: "1 unlock · 4 left" for free users on un-unlocked brands.

### 9. Pool, My Kit — no changes

Both stay free for everyone. They're retention features, not monetization gates.

## Existing user migration

All ~1,084 existing users get migrated in a single cron job on launch day:

```sql
-- Existing Pro subscribers: tier = pro, no reset
UPDATE users 
SET unlocks_tier = 'pro', unlocks_remaining = NULL, unlocks_reset_at = NULL
WHERE has_active_subscription = TRUE;

-- All other users: tier = free, 5 unlocks, reset 30d from now
UPDATE users
SET unlocks_tier = 'free', unlocks_remaining = 5, 
    unlocks_reset_at = NOW() + INTERVAL '30 days'
WHERE has_active_subscription = FALSE;

-- Backfill: any brand they've previously sent a pitch to is "already unlocked"
-- so they don't lose access to brands they already worked with
INSERT INTO brand_unlocks (user_id, brand_id, unlocked_at)
SELECT DISTINCT user_id, brand_id, MIN(created_at)
FROM pitches
GROUP BY user_id, brand_id
ON CONFLICT (user_id, brand_id) DO NOTHING;
```

The third statement is the trust-preserver: every brand a user has ever pitched stays accessible to them forever, no credit needed. This prevents the "you took away access to things I already used" rage.

### Announcement email to existing users (send 24h before flipping the gate)

```
Subject: a small change to Newcollab — and a free gift

Hey {{first_name}},

We're switching the free plan from "3 pitches per month" to "5 brand 
unlocks per month."

Same thing, clearer mechanic: each time you reveal a brand's PR contact 
and generate a pitch, that's 1 unlock. Once you unlock a brand, it's 
yours — you can revisit anytime, no cost.

What's actually changing:
- You now get 5 unlocks/month instead of 3 pitches (yes, more)
- Every brand you've ever pitched before stays unlocked, free, forever
- Pro stays $19/mo with unlimited unlocks

What's not changing:
- All your matches, your inbox, your media kit, the Pool — all here.
- The AI pitch generator — still does the work.
- Any brand you've previously contacted — still in your library.

This flips live tomorrow at 9am UTC. Reply to this email if anything's 
unclear.

— Mazza
```

The framing matters: this is a *more generous* free tier (5 > 3), packaged as a clarity upgrade. No one feels something was taken away.

## Edge cases — handle these explicitly

1. **User clicks Pitch Now on a brand they unlocked last month.** Free pass — they keep that unlock forever. No credit charge. The annotation reads "✓ unlocked."

2. **User downgrades from Pro to Free.** They keep every brand they unlocked while Pro. Their `unlocks_remaining` resets to 5 for the next cycle. Future new unlocks count against the 5.

3. **User upgrades from Free to Pro mid-month.** Their `unlocks_remaining` becomes irrelevant immediately. All future clicks are free under Pro.

4. **User hits 0 unlocks and waits.** When `now() > unlocks_reset_at`, the next click triggers reset to 5 (handled inside `attempt_unlock`). They never see a "your account is paused" state.

5. **User clicks Pitch Now twice in rapid succession on the same brand (double-tap).** The UNIQUE constraint on `brand_unlocks(user_id, brand_id)` prevents duplicate inserts. The second call returns `already_unlocked`, no double-charge.

6. **A brand is removed from the directory after a user unlocked it.** The unlock persists in `brand_unlocks` — they can still see the email they previously revealed via their inbox or pitch history. Don't delete unlocks when brands are deactivated.

## Pricing structure (v1)

Keep the single Pro tier at $19/month for now. Do not introduce Growth or Scale tiers in this rollout — adding them at the same time as the model change muddles the conversion signal you're trying to measure.

| Tier | Price | Unlocks/month | Notes |
|---|---|---|---|
| Free | $0 | 5 | Resets monthly on signup-anniversary date |
| Pro | $19/mo | Unlimited | Existing Pro subscribers grandfathered, no change |

Once you have 30 days of clean data on conversion under the new model, consider adding a $9/mo "Starter" tier (25 unlocks/month) to catch users who balk at $19 but engage above 5. That's a v2 experiment.

## Build sequence — 3-4 days

| Day | Task |
|---|---|
| Day 1 (morning) | DB migration: add 3 columns to `users`, create `brand_unlocks` table, run the migration backfill (existing pitches → unlocks). |
| Day 1 (afternoon) | Implement `attempt_unlock()` server function. Wire it as the first call inside the existing Pitch Now click handler. |
| Day 2 (morning) | Build paywall modal component with exact copy from this brief. |
| Day 2 (afternoon) | Update all 6 vocabulary surfaces: For You banner, brand card annotations, pitch composer status line, lock-out section, paywall modal, pricing page. |
| Day 3 (morning) | Existing user migration cron + announcement email scheduled. |
| Day 3 (afternoon) | Internal QA: walk through every edge case in the table above on a staging account. |
| Day 4 | Send announcement email at T-24h. Flip the feature flag live at T-0. Monitor for 48h. |

## What we are explicitly NOT building

- ❌ Multiple pricing tiers beyond Free and existing $19 Pro (defer Growth/Scale to v2)
- ❌ A credits-purchase modal ("buy 10 more unlocks for $5") — keep it strictly subscription
- ❌ Per-brand variable credit cost (each unlock = 1 credit, flat, simple)
- ❌ Overage charges on Pro (Pro is unlimited; never bill them for excess)
- ❌ A dedicated "credits dashboard" page — the For You banner is the only counter UI needed
- ❌ Any change to the in-app email composer, the inbox, the AI pitch generator, Pool, My Kit, or the Discover layout
- ❌ A new "Unlock" button that replaces "Pitch Now" — we keep the trained CTA label
- ❌ Email notifications when credits get low (premature; revisit if data says it helps)

## Success metrics — measure for 30 days

| Metric | Pre-launch baseline | Day 30 target |
|---|---|---|
| Free → Pro conversion rate | 1.3% | 4-6% |
| Average unlocks/free user/month | n/a | 3.5-4.5 of 5 |
| Free users hitting 0 unlocks | n/a | 25-35% |
| Of those hitting 0, % who upgrade within 7 days | n/a | 12-20% |
| Pro churn rate | current | unchanged or lower |
| User-reported confusion / support tickets | baseline | <5% increase |

If Free → Pro conversion is below 3% by day 30, the issue is NOT the model — it's brand directory quality, brand reply rates, or top-of-funnel acquisition. Don't tweak the credit limit; investigate the prior signal.

If Pro churn rises, examine specifically: did Pro users lose any feature they used? They shouldn't have — Pro is purely additive under this model. If they're churning, the cause is unrelated and pre-existing.

## Rollback plan

The migration is reversible:

1. Set `unlocks_tier = 'free'` and `unlocks_remaining = 999` for all users (effectively removes the gate)
2. Revert the Pitch Now click handler to skip the `attempt_unlock` call
3. Revert the 6 frontend copy changes via feature flag

Total rollback time: under 2 hours. Have this scripted before launch so it's a one-command revert if reply data unexpectedly spikes to "users are confused and angry."
