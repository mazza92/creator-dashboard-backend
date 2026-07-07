# Saved Tab Optimisation Brief
**File:** `src/creator-portal/Saved.js` (or equivalent)
**Goal:** Convert the #2 most-visited tab into a conversion driver by removing friction, fixing empty states, and placing the upgrade hook at the right moment.

---

## Summary of Changes

| Element | Current | Updated | Reason |
|---------|---------|---------|--------|
| Empty state header | "0 contacted, 0 responded" | "2 brands ready to contact this month" | Zeros signal nothing is happening |
| Pipeline visual | Always visible with zeros | Hidden until first pitch is sent | Demoralising at zero state |
| Pitch quota | Buried upgrade banner at bottom, shows at 0/3 | Pip-dot quota bar at top, always visible | 0/3 has no urgency — pip dots create natural build |
| CTA per card | Two buttons (Contact + Open PR Form) | One button: "Pitch [Brand] · Use 1 credit" | Two options create decision paralysis |
| Credit counter | None | "X credits remaining after this pitch" under CTA | Makes the limit feel real without being a wall |
| Brand descriptions | Scraped SEO taglines | One real sentence per brand | Scraped copy destroys trust at highest-intent moment |
| Social proof | None | "X creators got a reply this month" per card | Turns response rate % into evidence |
| Upgrade prompt | Bottom banner, fires at 0/3 | Locked card below pitchable brands, always | Converts on desire not frustration |
| Post-pitch state | No differentiation | Sent card dims, shows "Waiting for reply" disabled CTA | Removes anxiety about what happens after sending |

---

## Block 1 — Fix Empty State Header

### Current
```jsx
<PageTitle>My Pitches</PageTitle>
<PageSub>Contact your first brand to get started</PageSub>
```

### Replace with
```jsx
<PageTitle>My Pitches</PageTitle>
<PageSub>
  {savedBrands.length > 0
    ? `${savedBrands.filter(b => b.status === 'unsent').length} brands ready to contact this month`
    : 'Save brands from For You to start pitching'
  }
</PageSub>
```

---

## Block 2 — Hide Pipeline Until First Pitch

### Current
Always renders the Contacted / Waiting / Won pipeline with zeros.

### Replace with
```jsx
{pitches.length > 0 ? (
  <PipelineRow>
    <PipeStage>
      <PipeNum green>{pitches.filter(p => p.status !== 'unsent').length}</PipeNum>
      <PipeLabel>Contacted</PipeLabel>
    </PipeStage>
    <PipeStage>
      <PipeNum violet>{pitches.filter(p => p.status === 'waiting').length}</PipeNum>
      <PipeLabel>Waiting</PipeLabel>
    </PipeStage>
    <PipeStage>
      <PipeNum>{pitches.filter(p => p.status === 'won').length}</PipeNum>
      <PipeLabel>Won</PipeLabel>
    </PipeStage>
  </PipelineRow>
) : null}
```

---

## Block 3 — Replace Upgrade Banner with Quota Bar

### Remove
```jsx
{/* Bottom upgrade banner */}
<UpgradeBanner>
  <LockIcon />
  <div>
    <BannerTitle>Pitch more brands this month</BannerTitle>
    <BannerSub>You've used {used} of {limit} free contacts. Pro removes the limit.</BannerSub>
  </div>
  <UpgradeButton>$19/mo</UpgradeButton>
</UpgradeBanner>
```

### Add at top of page, below header
```jsx
<QuotaBar>
  <QuotaIcon>📨</QuotaIcon>
  <QuotaText>
    <QuotaTitle>
      {limit - used > 0
        ? `${limit - used} free ${limit - used === 1 ? 'pitch' : 'pitches'} left this month`
        : 'Monthly pitches used — upgrade to keep going'
      }
    </QuotaTitle>
    <QuotaSub>{used} used · resets {nextResetDate}</QuotaSub>
  </QuotaText>
  <QuotaPips>
    {Array.from({ length: limit }).map((_, i) => (
      <Pip key={i} used={i < used} />
    ))}
  </QuotaPips>
</QuotaBar>
```

**Styled components:**
```js
const QuotaBar = styled.div`
  background: #fff;
  border: 1px solid #e5e7eb;
  border-radius: 12px;
  padding: 12px 14px;
  margin-bottom: 16px;
  display: flex;
  align-items: center;
  gap: 12px;
`;

const QuotaIcon = styled.div`
  width: 36px;
  height: 36px;
  border-radius: 10px;
  background: #f0fdf4;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 18px;
  flex-shrink: 0;
`;

const QuotaTitle = styled.div`
  font-size: 13px;
  font-weight: 700;
  color: #0f0f0f;
`;

const QuotaSub = styled.div`
  font-size: 11.5px;
  color: #6b7280;
  margin-top: 1px;
`;

const QuotaPips = styled.div`
  display: flex;
  gap: 4px;
  align-items: center;
  flex-shrink: 0;
`;

const Pip = styled.div`
  width: 10px;
  height: 10px;
  border-radius: 50%;
  background: ${p => p.used ? '#059669' : '#e5e7eb'};
  transition: background .3s;
`;
```

---

## Block 4 — Single CTA with Credit Signal

### Current
```jsx
<ContactButton>Contact {brand.name}</ContactButton>
<OpenFormButton>Open PR Form</OpenFormButton>
```

### Replace with
```jsx
<CtaPrimary onClick={() => handlePitch(brand)} disabled={used >= limit}>
  ✉️ Pitch {brand.name} · Use 1 credit
</CtaPrimary>
<CtaCredit>
  {limit - used - 1 >= 0
    ? `${limit - used - 1} credit${limit - used - 1 !== 1 ? 's' : ''} remaining after this pitch`
    : 'No credits remaining — upgrade to pitch'
  }
</CtaCredit>
```

**Note:** The PR form (if brand has one) should be used as the delivery method behind the scenes inside `handlePitch`. The user should not see or choose between email vs form — pick the best method automatically.

**Styled components:**
```js
const CtaPrimary = styled.button`
  width: 100%;
  background: ${p => p.disabled ? '#f3f4f6' : '#0f0f0f'};
  color: ${p => p.disabled ? '#9ca3af' : '#fff'};
  border: none;
  border-radius: 11px;
  padding: 13px;
  font-size: 14px;
  font-weight: 700;
  cursor: ${p => p.disabled ? 'not-allowed' : 'pointer'};
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 7px;
  transition: background .15s;
  &:hover:not(:disabled) { background: #E11D48; }
`;

const CtaCredit = styled.div`
  font-size: 11px;
  font-weight: 500;
  color: #9ca3af;
  text-align: center;
  margin-top: 6px;
`;
```

---

## Block 5 — Add Brand Description

### Current
Brand descriptions are scraped SEO taglines (e.g. "Your One Stop Solution to Get Best quality Yoga Products").

### Fix
Use `brand.description` field. If not populated, run the one-time rewrite script from the optimization brief (Block 6).

**Format:** `[Brand] makes [product] for [customer]. [One differentiating detail.]`

```jsx
{brand.description && (
  <BrandDesc>{brand.description}</BrandDesc>
)}
```

```js
const BrandDesc = styled.div`
  font-size: 12px;
  color: #6b7280;
  line-height: 1.4;
  margin-bottom: 10px;
`;
```

---

## Block 6 — Add Social Proof Per Card

### What to add below the response rate chip
```jsx
<SocialProofLine>
  <GreenDotSm />
  {brand.recent_creator_replies > 0
    ? `${brand.recent_creator_replies} ${brand.category} creator${brand.recent_creator_replies > 1 ? 's' : ''} got a reply this month`
    : `Active this week · ${brand.reply_rate}% reply rate`
  }
</SocialProofLine>
```

### Backend — add `recent_creator_replies` to brand response
```python
# In brand serialization / saved brands endpoint
from datetime import datetime, timedelta

def get_recent_replies_count(brand_id, niche=None):
    query = db.session.query(func.count(Pitch.id))\
        .filter(Pitch.brand_id == brand_id)\
        .filter(Pitch.status == 'replied')\
        .filter(Pitch.replied_at >= datetime.now() - timedelta(days=30))
    if niche:
        query = query.join(Creator).filter(Creator.niche == niche)
    return query.scalar() or 0

# Add to brand dict in response
brand_data['recent_creator_replies'] = get_recent_replies_count(
    brand.id,
    niche=current_creator.niche
)
```

**Styled components:**
```js
const SocialProofLine = styled.div`
  display: flex;
  align-items: center;
  gap: 5px;
  font-size: 11.5px;
  color: #6b7280;
`;

const GreenDotSm = styled.div`
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: #059669;
  flex-shrink: 0;
`;
```

---

## Block 7 — Post-Pitch Card State

When a brand has been pitched and is waiting for a reply, the card should visually change state so the user knows the pitch is live.

```jsx
{brand.status === 'waiting' ? (
  <>
    <StatusChip waiting>📬 Pitch sent</StatusChip>
    <CtaPrimary disabled>
      ⏳ Waiting for reply
    </CtaPrimary>
    <CtaCredit>Reply expected in ~{brand.avg_reply_days || 5} days</CtaCredit>
  </>
) : (
  <>
    <CtaPrimary onClick={() => handlePitch(brand)}>
      ✉️ Pitch {brand.name} · Use 1 credit
    </CtaPrimary>
    <CtaCredit>{limit - used - 1} credits remaining after this pitch</CtaCredit>
  </>
)}
```

```js
const StatusChip = styled.div`
  display: inline-flex;
  align-items: center;
  gap: 4px;
  background: ${p => p.waiting ? '#f0fdf4' : '#fff'};
  border: 1px solid ${p => p.waiting ? '#bbf7d0' : '#e5e7eb'};
  border-radius: 20px;
  padding: 3px 9px;
  font-size: 10.5px;
  font-weight: 700;
  color: ${p => p.waiting ? '#059669' : '#6b7280'};
  margin-bottom: 8px;
`;
```

---

## Block 8 — Locked Upgrade Card (below pitchable brands)

Replace the bottom upgrade banner with a desire-driven locked card that sits below the brands the user can actually pitch.

```jsx
{isPro ? null : (
  <LockedCard onClick={() => openUpgradeModal('saved')}>
    <LockIconWrap>🔒</LockIconWrap>
    <LockedText>
      <LockedTitle>
        {lockedBrandCount > 0
          ? `${lockedBrandCount} more brands match your saved categories`
          : 'Unlock unlimited pitches every month'
        }
      </LockedTitle>
      <LockedSub>Pro removes the 3-pitch limit so you can contact all of them.</LockedSub>
    </LockedText>
    <LockedCta>$19/mo</LockedCta>
  </LockedCard>
)}
```

**Styled components:**
```js
const LockedCard = styled.div`
  background: linear-gradient(135deg, #1f1135 0%, #0f0f0f 100%);
  border-radius: 14px;
  padding: 16px 14px;
  margin-bottom: 10px;
  display: flex;
  align-items: center;
  gap: 12px;
  cursor: pointer;
  transition: transform .15s;
  &:hover { transform: scale(1.01); }
`;

const LockIconWrap = styled.div`
  width: 42px;
  height: 42px;
  border-radius: 10px;
  background: rgba(124, 58, 237, .25);
  border: 1px solid rgba(124, 58, 237, .4);
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 18px;
  flex-shrink: 0;
`;

const LockedTitle = styled.div`
  font-size: 13.5px;
  font-weight: 800;
  color: #fff;
  line-height: 1.25;
`;

const LockedSub = styled.div`
  font-size: 11.5px;
  color: #9ca3af;
  margin-top: 3px;
  line-height: 1.35;
`;

const LockedCta = styled.button`
  background: linear-gradient(135deg, #7c3aed, #E11D48);
  color: #fff;
  border: none;
  border-radius: 8px;
  padding: 8px 12px;
  font-size: 12px;
  font-weight: 700;
  cursor: pointer;
  white-space: nowrap;
  flex-shrink: 0;
`;
```

---

## Block 9 — Filter Tab Update

### Current
```jsx
<FilterTab active>All</FilterTab>
<FilterTab>Action Needed 0</FilterTab>
<FilterTab>Waiting 0</FilterTab>
```

### Replace with
```jsx
<FilterTab active={filter === 'all'} onClick={() => setFilter('all')}>
  All ({savedBrands.length})
</FilterTab>
<FilterTab active={filter === 'waiting'} onClick={() => setFilter('waiting')}>
  Waiting ({pitches.filter(p => p.status === 'waiting').length})
</FilterTab>
<FilterTab active={filter === 'replied'} onClick={() => setFilter('replied')}>
  Replied ({pitches.filter(p => p.status === 'replied').length})
</FilterTab>
```

Remove "Action Needed" tab — it fires at zero and means nothing to a new user. Replace with "Replied" which is the tab users most want to check.

---

## Implementation Order

| Block | Task | Time |
|-------|------|------|
| 1 | Fix empty state header copy | 15 min |
| 2 | Hide pipeline until first pitch | 30 min |
| 3 | Replace upgrade banner with quota pip bar | 1 hour |
| 4 | Single CTA + credit counter | 45 min |
| 5 | Clean brand descriptions (top 20 brands in DB) | 30 min |
| 6 | Social proof line per card | 1 hour |
| 7 | Post-pitch waiting state on card | 45 min |
| 8 | Locked upgrade card | 30 min |
| 9 | Filter tab update | 15 min |

**Total: ~6 hours**

---

## Expected Conversion Impact

The Saved tab is the highest-intent surface in the app — users chose these brands. Every change here targets the gap between "saved a brand" and "sent a pitch" which your funnel shows as 96 users stuck (459 saved, 361 pitched).

| Metric | Expected change |
|--------|----------------|
| Saved → pitched conversion | +20 to 30% |
| Upgrade click-through from locked card | Higher than bottom banner (desire vs friction) |
| User anxiety during waiting period | Reduced by post-pitch card state |
| Trust at pitch decision moment | Improved by real brand descriptions + social proof |
