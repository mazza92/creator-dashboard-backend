# Conversion Sprint Brief
## Goal: 0.95% → 2%+ conversion rate

**Current state:** $72 MRR · 6 Pro subscribers · 0.95% conversion · 12 at-limit users

## CONFIRMED CORRECTIONS — read before implementing

| Block | Action |
|---|---|
| **Blocks 1–2** | **SKIP** — keep `FREE_MONTHLY_LIMIT = 3`. Do not change to 1. |
| **Block 4** | **SKIP** — credit deduction on send is already implemented. |
| All other blocks | Implement as written. |

**Root causes identified:**
1. When limit is hit, the entire pitch interface is hidden behind an upgrade wall (creator can't see the pitch they'd be sending)
2. Pitch quality bugs reduce reply rates (niche mismatch, generic subject line)
3. Anxiety-inducing quota counter in modal header primes users to leave
4. Regeneration limit (3x) reduces perceived AI value

**Conversion model:**
- Keep: 3 contacts/month, credit deducted on send (already correct)
- Change: pitch always visible for all users, upgrade overlay fires at send when 0 credits remain

---

## Files changed
1. `pr_crm_routes.py` — Blocks 1–2
2. `src/creator-portal/AIPitchModal.js` — Blocks 3–10
3. `src/creator-portal/UpgradeModal.js` — Block 11
4. `src/creator-portal/PRBrandDiscovery.js` (or wherever match score is rendered) — Block 12

---

## Block 1 — Backend: change free limit from 3 to 1
**File:** `pr_crm_routes.py`
**Function:** `get_pitch_limits`

**FIND:**
```python
        # Determine limits based on tier - FREE users get 3 pitches per MONTH
        FREE_MONTHLY_LIMIT = 3
        is_pro = tier in ['pro', 'elite']

        return jsonify({
            'success': True,
            'used': pitches_used,
            'limit': FREE_MONTHLY_LIMIT if not is_pro else 999,
            'canPitch': is_pro or pitches_used < FREE_MONTHLY_LIMIT,
            'tier': tier,
            'period': 'month'
        })
```

**REPLACE WITH:**
```python
        # Free tier: 1 contact reveal per month. Pitch generation is always free.
        FREE_MONTHLY_LIMIT = 1
        is_pro = tier in ['pro', 'elite']

        return jsonify({
            'success': True,
            'used': pitches_used,
            'limit': FREE_MONTHLY_LIMIT if not is_pro else 999,
            'canPitch': is_pro or pitches_used < FREE_MONTHLY_LIMIT,
            'tier': tier,
            'period': 'month'
        })
```

---

## Block 2 — Backend: update quota email trigger + FREE_MONTHLY_LIMIT in track_pitch
**File:** `pr_crm_routes.py`
**Function:** `track_pitch`

**FIND:**
```python
        # Determine limits based on tier - FREE users get 3 pitches per MONTH
        FREE_MONTHLY_LIMIT = 3
```
*(inside track_pitch, separate from get_pitch_limits)*

**REPLACE WITH:**
```python
        # Must match FREE_MONTHLY_LIMIT in get_pitch_limits
        FREE_MONTHLY_LIMIT = 1
```

---

**FIND (same function, quota email trigger):**
```python
        # Per emailflowbrief.md Stage 5: When user hits 3rd pitch, schedule
        # quota email for 7 days later (not immediately)
        if new_pitch_count == 3 and tier == 'free':
```

**REPLACE WITH:**
```python
        # When user hits their 1 free contact, schedule follow-up email
        if new_pitch_count == 1 and tier == 'free':
```

---

## Block 3 — Frontend: remove modal-open credit deduction, remove upgrade wall gate
**File:** `src/creator-portal/AIPitchModal.js`
**Function:** `initializePitch`

**FIND:**
```javascript
    // If user can't pitch, just show upgrade prompt (don't generate pitch)
    if (!limits.canPitch) {
      setLoading(false);
      return;
    }

    // Fetch creator profile
    const profile = await fetchCreatorProfile();
    setCreatorProfile(profile);

    // Try AI endpoint, fall back to smart template
    const pitchData = await generatePitch(profile);

    // Check if brand has any contact method (email or application form from API response or brand prop)
    const hasEmail = pitchData?.brand_email || brand?.contact_email || brand?.email || brand?.pr_email;
    const hasAppForm = pitchData?.application_form_url || brand?.application_form_url || brand?.applicationUrl;

    // Only deduct credit if there's a way to contact the brand
    if (hasEmail || hasAppForm) {
      await trackPitchUsage();
      // Refresh limits to show updated count
      await fetchPitchLimits();
    }

    setLoading(false);
```

**REPLACE WITH:**
```javascript
    // Always generate the pitch — contact reveal is what consumes the credit
    const profile = await fetchCreatorProfile();
    setCreatorProfile(profile);
    await generatePitch(profile);
    setLoading(false);
```

---

## Block 4 — Frontend: move credit deduction to actual send action
**File:** `src/creator-portal/AIPitchModal.js`
**Function:** `handleSendEmail`

**FIND:**
```javascript
  const handleSendEmail = async () => {
    if (!pitchLimits.canPitch) {
      message.warning('You\'ve used all your free contacts this week. Upgrade to continue!');
      return;
    }

    setSending(true);

    try {
      // Build mailto URL
      const mailtoUrl = buildMailtoUrl();

      // Open email client
      window.location.href = mailtoUrl;

      message.success('Opening your email app...');

      // Close modal after short delay (handleClose notifies parent)
      setTimeout(() => {
        handleClose();
      }, 1000);

    } catch (error) {
      // Still open email even if something fails
      window.location.href = buildMailtoUrl();
      handleClose();
    } finally {
      setSending(false);
    }
  };
```

**REPLACE WITH:**
```javascript
  const [showUpgradePrompt, setShowUpgradePrompt] = useState(false);

  const handleSendEmail = async () => {
    if (!pitchLimits.canPitch) {
      setShowUpgradePrompt(true);
      return;
    }

    setSending(true);

    try {
      // Deduct credit at send time, not modal open
      await trackPitchUsage();
      await fetchPitchLimits();

      const mailtoUrl = buildMailtoUrl();
      window.location.href = mailtoUrl;

      message.success('Opening your email app...');

      setTimeout(() => {
        handleClose();
      }, 800);
    } catch (error) {
      window.location.href = buildMailtoUrl();
      handleClose();
    } finally {
      setSending(false);
    }
  };
```

---

## Block 5 — Frontend: remove UpgradePrompt from JSX body
**File:** `src/creator-portal/AIPitchModal.js`

Remove the entire `!pitchLimits.canPitch` branch from the render. The pitch interface is now always shown.

**FIND:**
```javascript
          ) : !pitchLimits.canPitch ? (
            <UpgradePrompt>
              <UpgradeIcon><FiZap /></UpgradeIcon>
              <UpgradeTitle>You've Used All Free Contacts!</UpgradeTitle>
              <UpgradeText>
                Upgrade to Pro for unlimited brand contacts and land more PR deals.
              </UpgradeText>
              <UpgradeFeatures>
                <UpgradeFeature>✓ Unlimited brand contacts</UpgradeFeature>
                <UpgradeFeature>✓ Direct PR manager emails</UpgradeFeature>
                <UpgradeFeature>✓ Priority brand matching</UpgradeFeature>
              </UpgradeFeatures>
              <UpgradeButton
                as="button"
                onClick={handleUpgrade}
                disabled={upgrading}
              >
                {upgrading ? 'Processing...' : 'Upgrade to Pro - $12/month'}
              </UpgradeButton>
              <UpgradeNote>💡 One PR package pays for 6+ months of Pro!</UpgradeNote>
            </UpgradePrompt>
          ) : (
```

**REPLACE WITH:**
```javascript
          ) : (
```

Then, **inside the `<>` block that previously only showed for paying/canPitch users**, add the inline upgrade overlay just before the closing `</>`. This renders on top of the pitch when the user tries to send without credits:

**FIND** (the last line before the closing `</>` of the main content area, which is the FooterTip closing):
```javascript
          {/* Footer tip */}
          <FooterTip>
            💡 Personalized emails get 3x more responses than generic templates
          </FooterTip>
```

**REPLACE WITH:**
```javascript
          {/* Inline upgrade overlay — shown only when user tries to send without credits */}
          {showUpgradePrompt && (
            <UpgradeOverlay>
              <UpgradeOverlayCard>
                <UpgradeOverlayClose onClick={() => setShowUpgradePrompt(false)}>×</UpgradeOverlayClose>
                <UpgradeOverlayTitle>You've used your free contact</UpgradeOverlayTitle>
                <UpgradeOverlayText>
                  You have matched brands waiting. Upgrade to contact them all.
                </UpgradeOverlayText>
                <UpgradeOverlayBtn
                  onClick={handleUpgrade}
                  disabled={upgrading}
                >
                  {upgrading ? 'Processing...' : 'Upgrade to Pro — $12/month'}
                </UpgradeOverlayBtn>
                <UpgradeOverlayNote>One gifted package covers your Pro for the year.</UpgradeOverlayNote>
              </UpgradeOverlayCard>
            </UpgradeOverlay>
          )}

          {/* Footer tip */}
          <FooterTip>
            💡 Personalized emails get 3x more responses than generic templates
          </FooterTip>
```

---

## Block 6 — Frontend: fix niche mismatch in getHumanOpeners
**File:** `src/creator-portal/AIPitchModal.js`

**FIND (call site in generateGoldenTemplate):**
```javascript
    // Pick a random opener variation to avoid pattern detection
    const openers = getHumanOpeners(brand.brand_name, brand.category);
```

**REPLACE WITH:**
```javascript
    const openers = getHumanOpeners(brand.brand_name, niche);
```

**FIND (function definition):**
```javascript
  const getHumanOpeners = (brandName, category) => {
    const openers = [
      `I've been using ${brandName} products for a bit now and wanted to reach out about a collab idea.`,
      `Found ${brandName} a few months back and it's become a staple in my routine - figured I'd shoot my shot.`,
      `Quick intro - I'm a ${category?.toLowerCase() || 'content'} creator and I've had my eye on ${brandName} for a while.`,
      `Hope this finds the right person! I create ${category?.toLowerCase() || ''} content and ${brandName} keeps coming up in my comments.`,
      `I've been wanting to reach out for a while - ${brandName} fits really well with the content I make.`
    ];
    return openers;
  };
```

**REPLACE WITH:**
```javascript
  const getHumanOpeners = (brandName, creatorNiche) => {
    const nicheLabel = creatorNiche?.toLowerCase() || 'content';
    const openers = [
      `I've been using ${brandName} products for a bit now and wanted to reach out about a collab idea.`,
      `Found ${brandName} a few months back and figured I'd shoot my shot.`,
      `Quick intro - I create ${nicheLabel} content and I've had my eye on ${brandName} for a while.`,
      `Hope this finds the right person! I make ${nicheLabel} content and ${brandName} keeps coming up in my comments.`,
      `I've been wanting to reach out for a while — ${brandName} fits really well with the content I make.`
    ];
    return openers;
  };
```

---

## Block 7 — Frontend: fix subject line
**File:** `src/creator-portal/AIPitchModal.js`
**Function:** `generateGoldenTemplate`

**FIND:**
```javascript
    // Build the personalized subject - clean, no brackets or quotes
    const nicheDisplay = niche || brand.category || 'content';
    const subject = `PR collab idea for ${brand.brand_name}`;
```

**REPLACE WITH:**
```javascript
    const nicheDisplay = niche || brand.category || 'content';
    const platformLabel = platform || 'Instagram';
    const followersDisplay = followers
      ? `${followers} ${platformLabel} audience`
      : `${platformLabel} creator`;
    const subject = `${nicheDisplay} content idea — ${followersDisplay}`;
```

---

## Block 8 — Frontend: remove MAX_REGENERATES limit
**File:** `src/creator-portal/AIPitchModal.js`

**FIND:**
```javascript
  const MAX_REGENERATES = 3; // Limit regenerations to save API credits
```

**REPLACE WITH:**
```javascript
  const MAX_REGENERATES = 999;
```

**FIND:**
```javascript
                  <SecondaryButton
                    onClick={handleRegenerate}
                    disabled={regenerateCount >= MAX_REGENERATES}
                  >
                    <FiRefreshCw /> {regenerateCount >= MAX_REGENERATES ? 'Limit reached' : `Regenerate (${MAX_REGENERATES - regenerateCount} left)`}
                  </SecondaryButton>
```

**REPLACE WITH:**
```javascript
                  <SecondaryButton onClick={handleRegenerate}>
                    <FiRefreshCw /> Rewrite pitch
                  </SecondaryButton>
```

---

## Block 9 — Frontend: remove PitchCounter, add match score chip
**File:** `src/creator-portal/AIPitchModal.js`

**FIND:**
```javascript
            <PitchCounter canPitch={pitchLimits.canPitch}>
              <FiZap />
              <span>{pitchLimits.limit - pitchLimits.used} / {pitchLimits.limit} contacts left</span>
            </PitchCounter>
```

**REPLACE WITH:**
```javascript
            {brand?.match_score && (
              <MatchChip>{Math.min(Math.round(brand.match_score), 100)}% match</MatchChip>
            )}
```

---

## Block 10 — Frontend: update send button label + add new styled components
**File:** `src/creator-portal/AIPitchModal.js`

**FIND (send button label inside SendButton):**
```javascript
                      <>
                        <FiSend /> <span>Send Email to {brandName}</span>
                      </>
```

**REPLACE WITH:**
```javascript
                      <>
                        <FiSend />
                        <span>
                          {pitchLimits.tier === 'free' && pitchLimits.canPitch
                            ? 'Open in email app · Use 1 contact'
                            : 'Open in email app'}
                        </span>
                      </>
```

**ADD these styled components** at the bottom of the file, before `export default AIPitchModal;`:

```javascript
const MatchChip = styled.div`
  padding: 6px 12px;
  background: #F0FDF4;
  color: #15803D;
  border-radius: 20px;
  font-size: 13px;
  font-weight: 600;
  flex-shrink: 0;
`;

const UpgradeOverlay = styled.div`
  position: absolute;
  inset: 0;
  background: rgba(255, 255, 255, 0.92);
  backdrop-filter: blur(6px);
  border-radius: 24px;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 24px;
  z-index: 20;
`;

const UpgradeOverlayCard = styled.div`
  background: white;
  border: 1px solid #E5E7EB;
  border-radius: 20px;
  padding: 32px 24px;
  text-align: center;
  max-width: 320px;
  width: 100%;
  position: relative;
  box-shadow: 0 8px 32px rgba(0,0,0,0.12);
`;

const UpgradeOverlayClose = styled.button`
  position: absolute;
  top: 12px;
  right: 14px;
  background: none;
  border: none;
  font-size: 22px;
  color: #9CA3AF;
  cursor: pointer;
  line-height: 1;
  padding: 0;
  &:hover { color: #111827; }
`;

const UpgradeOverlayTitle = styled.h3`
  font-size: 18px;
  font-weight: 700;
  color: #111827;
  margin: 0 0 8px;
`;

const UpgradeOverlayText = styled.p`
  font-size: 14px;
  color: #6B7280;
  margin: 0 0 20px;
  line-height: 1.5;
`;

const UpgradeOverlayBtn = styled.button`
  width: 100%;
  padding: 14px 20px;
  background: #0F0F0F;
  color: white;
  border: none;
  border-radius: 12px;
  font-size: 15px;
  font-weight: 600;
  cursor: pointer;
  transition: opacity 0.2s;
  &:hover:not(:disabled) { opacity: 0.85; }
  &:disabled { opacity: 0.5; cursor: not-allowed; }
`;

const UpgradeOverlayNote = styled.p`
  margin: 10px 0 0;
  font-size: 12px;
  color: #9CA3AF;
`;
```

Also add `position: relative;` to the existing `Modal` styled component so the overlay positions correctly:

**FIND:**
```javascript
const Modal = styled(motion.div)`
  background: white;
  border-radius: 24px;
  max-width: 600px;
  width: 100%;
  max-height: 90vh;
  overflow-y: auto;
  position: relative;
```

*(already has `position: relative` — no change needed)*

---

## Block 11 — UpgradeModal.js: add For You paywall copy + improve limit_reached
**File:** `src/creator-portal/UpgradeModal.js`

**FIND:**
```javascript
  limit_reached: {
    headline: "You're on a roll. Keep going.",
    sub: "You've sent all your brand pitches this month.",
    features: [
      "Unlimited pitches. Every brand, every month.",
      "Custom outreach emails written for each brand",
      "Direct PR manager contacts for 500+ brands",
      "Your media kit and portfolio in one shareable link",
      "Personal creator assistant for follow-ups and deal negotiations"
    ],
    valueProp: "Most creators land their first gifted package within 30 days. That covers your $12 back in product."
  },
```

**REPLACE WITH:**
```javascript
  limit_reached: {
    headline: "You have matched brands waiting.",
    sub: "Your 1 free contact this month has been used. Upgrade to contact all your matches.",
    features: [
      "Unlimited brand contacts every month",
      "Custom outreach emails written for each brand",
      "Direct PR manager contacts for 500+ brands",
      "Kit view tracking — see which brands checked you out",
      "Full portfolio builder with bento media kit"
    ],
    valueProp: "Most creators land their first gifted package within 30 days. That covers your $12 back in product."
  },
  for_you: {
    headline: "You have brand matches waiting.",
    sub: "These brands are looking for creators exactly like you. Upgrade to contact them.",
    features: [
      "Contact all your For You matches",
      "Unlock reply rates and avg. deal value for every brand",
      "Unlimited pitches every month",
      "Kit view tracking — see which brands checked you out",
      "Full portfolio builder with bento media kit"
    ],
    valueProp: "Your top match has a 40%+ reply rate. That's 4x the industry average — worth a $12 bet."
  },
```

**FIND (copyKey logic):**
```javascript
  const copyKey = feature === 'followup' ? 'followup' :
                  feature === 'pr_value' ? 'pr_value' :
                  feature === 'kit_views' ? 'kit_views' :
                  feature === 'portfolio_limit' ? 'portfolio_limit' :
                  limitReached ? 'limit_reached' : 'default';
```

**REPLACE WITH:**
```javascript
  const copyKey = feature === 'followup' ? 'followup' :
                  feature === 'pr_value' ? 'pr_value' :
                  feature === 'kit_views' ? 'kit_views' :
                  feature === 'portfolio_limit' ? 'portfolio_limit' :
                  feature === 'for_you' ? 'for_you' :
                  limitReached ? 'limit_reached' : 'default';
```

---

## Block 12 — Cap match score at 100 (bug fix)
**File:** Wherever `match_score` or `matchScore` is rendered as a percentage chip/badge.

Search for:
```
match_score
matchScore
% match
```

Wherever the score value is output to the UI, wrap it:
```javascript
// BEFORE (example pattern):
{brand.match_score}% match

// AFTER:
{Math.min(Math.round(brand.match_score), 100)}% match
```

Apply to every location: brand cards in PRBrandDiscovery.js, For You cards, and the MatchChip in AIPitchModal (already capped in Block 9 above).

---

## Summary of behavioral changes after this sprint

| Before | After |
|---|---|
| Credit consumed on modal open | Credit consumed on actual send |
| 3 contacts/month, hard wall | 1 contact/month, pitch always visible |
| Upgrade wall hides the pitch | Upgrade overlay appears on send attempt |
| "X / 3 contacts left" in header | Match score chip, no quota anxiety |
| Niche mismatch in openers | Creator's own niche used |
| "PR collab idea for Shopbop" subject | "Food & Nutrition content idea — 14.2K Instagram audience" |
| 3 regeneration limit shown as counter | Unlimited, labeled "Rewrite pitch" |
| 105% match score possible | Capped at 100% everywhere |

---

## Test checklist
- [ ] Free user opens modal: pitch generates without credit deducted
- [ ] Free user with 0 contacts used: send button shows "Open in email app · Use 1 contact"
- [ ] Free user clicks send: credit deducted, mailto opens, modal closes
- [ ] Free user opens modal again after 1 send: pitch still shows, send button shows upgrade overlay
- [ ] Upgrade overlay closes with X button
- [ ] Upgrade overlay "Upgrade" button redirects to Stripe
- [ ] Pro user: no contact limit, send button shows "Open in email app"
- [ ] Subject line contains creator niche and follower count
- [ ] Opener text references creator's niche, not brand's category
- [ ] "Rewrite pitch" button works unlimited times
- [ ] Match score shows 100% max everywhere
- [ ] Backend /api/pr-crm/pitch-limits returns limit: 1 for free users
- [ ] track-pitch endpoint still called correctly from handleSendEmail
