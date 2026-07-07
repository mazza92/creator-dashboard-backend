# Newcollab: Social Connect Requirement in /onboarding — Dev Brief

## Objective

Add a mandatory social account verification step to the existing `/onboarding` flow that filters out bots, empty accounts, and non-creators without breaking the current sign-up-to-first-value path. Every user who completes onboarding must have connected a real, active, public Instagram or TikTok account meeting minimum audience thresholds.

Downstream outcome: brand pitches sent through Newcollab come from verified real creators, restoring brand trust in the marketplace and lifting reply rates.

## ICP-derived design decisions

The Pro Tier ICP data shapes several specific choices in this brief:

| ICP signal | Design decision |
|---|---|
| **63% Instagram, 38% TikTok** primary platform | Instagram OAuth is the primary CTA. TikTok is secondary but present with equal visual weight in the connect step. |
| **Median 1,500 followers, 31% nano (<1K) payers** | Follower minimum is **500**, not 5,000 or 10,000. Setting the bar higher would exclude a third of paying users. |
| **63% aged 25-34, 19% aged 35-44** | Tone across all copy is peer-professional, not Gen-Z-casual. "Verify your account" not "let's check you out." |
| **69% Beauty, 63% Fashion, 50% Lifestyle** | Suggested niche picker (if step exists) pre-populates Beauty / Fashion / Lifestyle at the top. |
| **44% US, 13% AU, then EU spread** | Copy is English-only for v1. Instagram Business account availability is universal in these markets — no regional API gotchas. |
| **Only 19% have built a media kit** | Media kit remains a *post-onboarding* nudge, not a gate. Onboarding must not add friction beyond social connect. |

## Where this fits in the existing /onboarding flow

The current `/onboarding` route already has step-by-step account setup. This brief assumes the flow looks roughly like:

```
/onboarding  →  step 1 (creator handle, platforms, following)  →  step 2 (profile image, bio, audience, regions)  →  step 3 (niches selection)
```

Insert the new step within step 3. The reasoning: knowing the user's declared niche makes the social-connect explanation more relevant ("we'll verify your beauty account is real"), and audience data becomes redundant once we're pulling it from the OAuth response.

```

**Critical rule: no user reaches `/onboarding/complete` without a `social_verified = true` status.** Skipping this step is not permitted. Users can abandon and return, but not proceed.

## The 4-gate architecture

Every connected account is validated against these four gates. All four must pass for `social_verified = true`.

| # | Gate | Threshold | API field |
|---|---|---|---|
| 1 | Connected via OAuth | Instagram Graph API OR TikTok Display API | Access token issued |
| 2 | Public account | `is_private == false` (TikTok), or Business/Creator account (Instagram — Personal accounts can't OAuth to Graph API since Dec 2024, so this is automatic on IG) | `privacy_level` (TikTok) / `account_type` (IG) |
| 3 | Follower minimum | ≥ 500 | `followers_count` (IG) / `follower_count` (TikTok) |
| 4 | Content minimum | ≥ 5 posts | `media_count` (IG) / `video_count` (TikTok) |

**Not in v1: account age.** Neither Instagram Graph API nor TikTok Display API expose account creation date directly. Adding it requires either fetching earliest media (extra API call, cost, complexity) or third-party enrichment (Phyllo, InsightIQ — paid). Defer to v2 if quality data shows sockpuppet accounts sneaking through the other 4 gates.

## Instagram-specific note

Instagram's Basic Display API was deprecated in December 2024. **The only viable OAuth path is Instagram Graph API via Facebook Login for Business.** This has one consequence that's actually a quality bonus:

- **Personal Instagram accounts cannot OAuth.** Users must have Business or Creator account type.
- Business and Creator accounts are always public by definition.
- This means for Instagram-connected users, gate #2 (public account) is satisfied automatically — the account type check IS the public check.

If a user has a Personal account and tries to connect, Instagram itself will prompt them to convert to Creator or Business before granting the OAuth token. Newcollab doesn't need to handle this — Instagram surfaces the conversion CTA natively. Our fail-state just shows "Instagram wants you to convert your account to Creator or Business. This takes 30 seconds and doesn't change how you use Instagram."

For TikTok, both public and private accounts can OAuth, so gate #2 must be enforced explicitly.

## Database changes

Add to `users` table:

```sql
ALTER TABLE users ADD COLUMN social_platform VARCHAR(20);          -- 'instagram' | 'tiktok'
ALTER TABLE users ADD COLUMN social_handle VARCHAR(60);
ALTER TABLE users ADD COLUMN social_follower_count INT;
ALTER TABLE users ADD COLUMN social_media_count INT;
ALTER TABLE users ADD COLUMN social_is_public BOOLEAN DEFAULT FALSE;
ALTER TABLE users ADD COLUMN social_account_type VARCHAR(20);      -- 'business' | 'creator' | 'personal' | null
ALTER TABLE users ADD COLUMN social_connected_at TIMESTAMP;
ALTER TABLE users ADD COLUMN social_last_checked_at TIMESTAMP;
ALTER TABLE users ADD COLUMN social_verified BOOLEAN DEFAULT FALSE;  -- computed field: all 4 gates passed
ALTER TABLE users ADD COLUMN social_oauth_token TEXT;               -- encrypted
ALTER TABLE users ADD COLUMN social_oauth_refresh_token TEXT;       -- encrypted
CREATE INDEX idx_users_social_verified ON users(social_verified);
```

Add new table for the weekly re-check log:

```sql
CREATE TABLE social_verification_checks (
  id BIGSERIAL PRIMARY KEY,
  user_id UUID NOT NULL,
  checked_at TIMESTAMP DEFAULT NOW(),
  passed BOOLEAN NOT NULL,
  fail_reason VARCHAR(50),           -- 'private' | 'below_follower_min' | 'below_post_min' | 'oauth_expired' | null
  follower_count INT,
  media_count INT,
  is_public BOOLEAN
);
CREATE INDEX idx_social_checks_user_time ON social_verification_checks(user_id, checked_at DESC);
```

The check history is valuable for debugging (why did user X lose access), for future ML signals, and for user-facing "you've been verified for 47 days" trust cues.

## The /onboarding/social-connect step — full UX spec

### Screen 1: The connect prompt

```
Verify your creator account

Brands trust Newcollab because we verify every creator's audience 
is real. Connect the account you create on to unlock pitching.

[ ↗ Connect Instagram ]     ← primary, Instagram brand color
[ ♪ Connect TikTok ]        ← secondary, TikTok brand color

Why do we ask?
• Brands are 3× more likely to reply to verified creators
• We check your audience is real (no bots, no fake accounts)
• Takes 30 seconds, one-tap OAuth
• We never post from your account
```

Design principles:
- Instagram button visually dominant (63% of ICP)
- TikTok visible but secondary
- Trust bullets address the 3 objections a creator has: what's in it for me, what are you checking, what will you access

### Screen 2: Loading / analysis

Show live during OAuth callback + validation:

```
Verifying your account...

✓ Instagram connected (@handle)
✓ Business account
⋯ Checking audience...
```

Progressive reveal as each gate resolves. Feels like real work is happening. Should take 2-4 seconds real time.

### Screen 3a: SUCCESS state

```
✅ Verified

@your_handle · 8,400 followers · Beauty

You're ready to start pitching brands. You have 5 free brand 
unlocks this month.

[ Continue → ]
```

Note: continues to the existing `/onboarding/audience` step, which is now mostly review-only since we auto-populated from OAuth.

### Screen 3b: FAIL — private account (TikTok only)

```
Your TikTok is set to private

Brands can't consider creators they can't see. Switch to public 
to complete verification.

How to switch:
1. Open TikTok → Profile → Menu (☰)
2. Settings and privacy → Privacy
3. Toggle "Private account" off

[ I've switched it — recheck ]
[ Connect Instagram instead ]
```

The `Recheck` button re-runs the API call without a full re-OAuth. The `Connect Instagram instead` gives them an out if they refuse to make TikTok public.

### Screen 3c: FAIL — below follower minimum

```
Your account needs a bit more growth

We require a minimum of 500 followers to make sure brand pitches 
come from real creators. You currently have {follower_count}.

Come back once you cross 500 — meanwhile, you can browse brands 
and build your media kit.

[ Browse brands ] [ Complete profile ]
```

Send them to browse mode. Do NOT let them proceed to `/onboarding/finish`. Their account stays in a "waiting for verification" state. When they check back (or a nudge email lands), the recheck can rerun.

### Screen 3d: FAIL — below post minimum

```
Almost there — {media_count} of 5 posts

We require at least 5 posts on your connected account, so brands 
can see the content style before saying yes to a PR box.

Post a few more times on {platform}, then come back and hit 
recheck.

[ Recheck ] [ Try TikTok instead ]
```

### Screen 3e: FAIL — Instagram Personal account

```
Instagram wants you to convert your account

Newcollab requires an Instagram Business or Creator account. 
This is Instagram's standard for anyone working with brands.

Instagram will walk you through it in ~30 seconds:

1. Open Instagram → Settings
2. Account → Switch to Creator (or Business)
3. Come back here and connect again

[ I've switched — reconnect ]
[ Use TikTok instead ]
```

This is actually Instagram's requirement, not yours — you're just surfacing it. Frame it that way to avoid feeling gatekept.

### Screen 3f: FAIL — OAuth error

```
Something went wrong

We couldn't complete the connection. This usually means Instagram 
asked for a permission you didn't grant.

[ Try again ] [ Contact support ]
```

Log the exact error server-side. Never surface OAuth internals to the user.

## OAuth integration specifics

### Instagram (via Facebook Login)

- App type: Business
- Required scopes: `instagram_basic`, `instagram_manage_insights` (optional but nice for future features), `pages_show_list`, `business_management`
- Callback URL: `https://app.newcollab.co/onboarding/social-connect/callback/instagram`
- After callback:
  1. Exchange code for short-lived token
  2. Exchange short-lived for long-lived (60-day)
  3. Fetch `me?fields=id,username,account_type`
  4. Fetch `me/accounts` to find connected Instagram Business account
  5. Fetch `{ig_business_id}?fields=followers_count,media_count`
  6. Run 4-gate validation
  7. Store encrypted tokens

### TikTok (via Login Kit)

- App type: Consumer / Business
- Required scopes: `user.info.basic`, `user.info.profile`, `user.info.stats`, `video.list`
- Callback URL: `https://app.newcollab.co/onboarding/social-connect/callback/tiktok`
- After callback:
  1. Exchange code for access token + refresh token
  2. Fetch `/user/info/` with fields: `open_id`, `union_id`, `avatar_url`, `display_name`, `follower_count`, `following_count`, `likes_count`, `video_count`, `privacy_level`
  3. Run 4-gate validation
  4. Store encrypted tokens

Both flows use PKCE (Proof Key for Code Exchange) for the OAuth challenge — standard security, no session-based state passing.

### Token storage

- Encrypt access + refresh tokens at rest using AWS KMS or equivalent
- Never log raw tokens
- Refresh tokens 7 days before expiry via cron

## The gate-check function (single source of truth)

Server-side function called after OAuth completes AND every weekly re-check:

```python
def validate_social_gates(social_data: dict, platform: str) -> ValidationResult:
    gates = {
        "connected": bool(social_data.get("access_token")),
        "public": _is_public(social_data, platform),
        "follower_min": social_data.get("follower_count", 0) >= 500,
        "post_min": social_data.get("media_count", 0) >= 5,
    }
    
    passed = all(gates.values())
    fail_reason = None
    if not passed:
        # Report the FIRST gate that failed for cleanest UX
        if not gates["connected"]:
            fail_reason = "oauth_expired"
        elif not gates["public"]:
            fail_reason = "private"
        elif not gates["follower_min"]:
            fail_reason = "below_follower_min"
        elif not gates["post_min"]:
            fail_reason = "below_post_min"
    
    return ValidationResult(
        passed=passed,
        fail_reason=fail_reason,
        follower_count=social_data.get("follower_count"),
        media_count=social_data.get("media_count"),
        is_public=gates["public"],
    )

def _is_public(social_data: dict, platform: str) -> bool:
    if platform == "instagram":
        # Business/Creator accounts on Instagram are always public
        return social_data.get("account_type") in ["BUSINESS", "CREATOR"]
    elif platform == "tiktok":
        return not social_data.get("is_private", True)
```

This function is the ONLY place gate logic lives. Both onboarding flow and weekly re-check call it.

## Weekly re-check cron

Runs every Monday at 03:00 UTC:

```
1. Query all users where social_verified = true
2. For each user:
   a. If token expired → attempt refresh
   b. If refresh fails → set social_verified = false, fail_reason = 'oauth_expired'
   c. Otherwise fetch fresh social_data
   d. Run validate_social_gates
   e. Write to social_verification_checks table
   f. If result != current social_verified state → update users table
3. For every user who newly failed → trigger notification email + in-app banner
```

Batch users at 500 per run to stay under API rate limits. Instagram allows 200 calls/hour per token; TikTok allows similar. At 4,000+ users, spread the cron across the day (03:00, 09:00, 15:00, 21:00).

## In-app state changes when a user newly fails re-check

If someone was verified but now isn't (e.g., they went private, dropped below 500 followers, or their token expired):

```
Banner at top of every page:
⚠ Your account needs re-verification

Your {platform} account is no longer meeting one of our verification 
requirements. Pitching is paused until you fix it.

[ See what's needed → ]
```

Clicking sends them back to the fail-state screen matching their new failure reason. Same UX as onboarding failures.

They can still browse, still see brands, still use inbox for existing conversations. They cannot send new pitches until re-verified.

## Existing user migration

The 1,084 existing users bypass onboarding. Migration plan:

1. **Grandfather everyone for 21 days.** No forced re-verification on existing users initially. This avoids a hostile forced OAuth on people who joined under different rules.
2. **Day 0:** Announcement email to all existing users:
   ```
   Subject: quick update — new verification for pitching
   
   Hey {first_name},
   
   Starting {date}, Newcollab requires a verified Instagram or 
   TikTok account before sending brand pitches. Nothing changes 
   for browsing, matches, or your existing conversations.
   
   Takes about 30 seconds:
   1. Log in
   2. Connect your account when prompted
   3. You're set
   
   Reply if you have any questions.
   
   — Mazza
   ```
3. **Day 0-21:** Soft nudge banner in-app when they log in. Non-blocking. Explains the change, asks them to connect.
4. **Day 22:** Gate flips on. Existing users without social_verified see the same fail states new users see. They can complete verification and continue.
5. **Day 22 onward:** All users, old and new, are on the same rule set.

## Copy dictionary — vocabulary lock

To keep consistency with the existing "unlocks" vocabulary from the credit model:

| Concept | Approved term | Never use |
|---|---|---|
| The gate | **verify / verified / verification** | "approve," "review," "check" |
| The status | **verified creator** | "certified," "accepted," "qualified" |
| The prompt | **Connect your account** | "Link account," "Sign in with," "Authenticate" |
| The output | **you're ready to pitch** | "You're approved," "Access granted" |
| The fail | **not yet — here's what's needed** | "Rejected," "Denied," "Failed" |

Apply this dictionary to every screen, every email, every notification.

## Success metrics — 30 days after launch

| Metric | Baseline | Target |
|---|---|---|
| % of new signups completing onboarding | current | -15% to -25% (expected, this is the noise falling out) |
| % of onboarding completions that are social_verified | 0% | 100% |
| Average pitch reply rate (all brands) | 1-4% | 6-10% |
| Support tickets citing "can't send pitch" | 0 baseline | <2% of active users |
| Brand-side complaints about pitch quality | current baseline | -60% |
| Free → Pro conversion rate | 1.3% | 2.5-4% |

The signup completion drop is intentional and does not reflect product failure. The users who abandon at social connect are the users who were never going to pitch anyway. The remaining users are dramatically more valuable per unit.

## Non-goals

- ❌ Do not require both Instagram AND TikTok. One or the other is sufficient.
- ❌ Do not require account age > X. Defer to v2.
- ❌ Do not require verified/blue-check status. Excludes 90% of your ICP.
- ❌ Do not build a manual review queue for edge cases. If gates fail, the automated fail state handles it.
- ❌ Do not persist the user's raw social data beyond what's needed (follower count, media count, privacy status, handle). No content archives, no post metadata, no follower lists.
- ❌ Do not surface OAuth tokens to the frontend, ever. All social API calls are server-side.
- ❌ Do not gate signup itself. Sign up stays frictionless. Only the pitch action requires verified status.
- ❌ Do not add a separate "verified badge" surfaced to other creators. This is internal validation, not social status. May be a v2 feature for the marketplace but adds complexity now.

## Build sequence — 5 days

| Day | Task |
|---|---|
| 1 (morning) | DB migration: add 10 columns to `users`, create `social_verification_checks` table. Encrypted token storage utility using KMS. |
| 1 (afternoon) | Instagram Graph API OAuth flow: register Facebook app, wire callback, exchange codes, token refresh. |
| 2 (morning) | TikTok Login Kit OAuth flow: register app, wire callback, exchange codes, token refresh. |
| 2 (afternoon) | `validate_social_gates()` function + unit tests. Server-side API endpoint that runs it. |
| 3 (morning) | `/onboarding/social-connect` screen (screen 1). Wire OAuth CTAs. |
| 3 (afternoon) | Loading screen (screen 2) + success state (screen 3a). |
| 4 (morning) | All 5 fail states (screens 3b, 3c, 3d, 3e, 3f). |
| 4 (afternoon) | Weekly re-check cron job + failure notification email template. |
| 5 (morning) | Existing user migration: announcement email scheduled, soft-nudge banner, 21-day countdown. |
| 5 (afternoon) | Internal QA: hand-walk 20 real accounts (mix of pass/fail conditions), verify every gate reports correctly. Run on staging OAuth apps. |
| Post-day-5 | Sunday: send existing user announcement. Monday day 22: flip gate on for existing users. |

## Rollback plan

The migration is reversible:

1. Feature flag on the gate check in the Pitch Now click handler → set to skip
2. Feature flag on `/onboarding/social-connect` → route to `/onboarding/audience` directly
3. Users with `social_verified = false` regain pitch access immediately

Rollback time: under 30 minutes if issues surface. Script this before launch.

## What to hand-verify before ship

Before flipping the gate on for existing users (day 22), verify with 20 real accounts covering these scenarios:

- [ ] Instagram Creator account with 8K followers, 40 posts → passes cleanly
- [ ] Instagram Business account with 800 followers, 12 posts → passes cleanly
- [ ] Instagram Personal account → gets Instagram's prompt to convert (verify the OAuth actually blocks)
- [ ] TikTok public with 5K followers, 20 videos → passes cleanly
- [ ] TikTok private → fail state 3b shows correctly, recheck works after switch
- [ ] Instagram account with 320 followers → fail state 3c
- [ ] TikTok account with 2 videos → fail state 3d
- [ ] OAuth deny during flow → fail state 3f
- [ ] User completes verification, revisits `/onboarding/social-connect` → sees already-verified state, not re-prompted
- [ ] User with valid session but token expired → re-OAuth prompt, no data loss

If any of these behave unexpectedly, do NOT proceed to launch. Fix and re-QA.
