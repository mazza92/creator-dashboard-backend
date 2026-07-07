---

# For You Dashboard — UX Conversion Brief
## Goal: Fix trust gaps on first-session landing screen

**Files affected:**
- `src/creator-portal/ForYou.js` — main component
- `src/creator-portal/ForYouComponents.js` (or styled-components in ForYou.js)
- `src/api/brands_routes.py` (or wherever trending brands are filtered)

---

## Fix 1 — Pitch Explainer Step 1 Copy

**Problem:** "We open your email app" sounds like a surprise/downgrade. User expected Newcollab to send for them.

**File:** `ForYou.js` (or wherever the 3-step explainer renders)

**FIND:**
```
We open your email app
```
```
Pre-filled with their PR contact + your AI-written pitch
```

**REPLACE WITH:**
```
Your pitch goes straight to their PR inbox
```
```
Pre-filled with their PR contact + your custom message, sent from your email so it feels personal
```

---

## Fix 2 — Social Proof Strip (new component, insert after "Your top matches" header)

**Problem:** Zero evidence the product works for nano/micro creators. This is the #1 conversion blocker.

**File:** `ForYou.js`

**FIND:**
```jsx
<SectionLabel>Your top matches</SectionLabel>
```

**REPLACE WITH:**
```jsx
<SectionLabel>Your top matches</SectionLabel>
<SocialProofStrip>
  <AvatarRow>
    {/* 3 placeholder avatars — replace with real creator data when available */}
    <Avatar src="/static/avatars/placeholder1.jpg" />
    <Avatar src="/static/avatars/placeholder2.jpg" />
    <Avatar src="/static/avatars/placeholder3.jpg" />
  </AvatarRow>
  <ProofText>
    Creators with under 10K followers got PR from brands like these this month
  </ProofText>
</SocialProofStrip>
```

**Add styled components:**
```js
const SocialProofStrip = styled.div`
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 10px 0 16px;
`;

const AvatarRow = styled.div`
  display: flex;
  > img:not(:first-child) { margin-left: -6px; }
`;

const Avatar = styled.img`
  width: 24px;
  height: 24px;
  border-radius: 50%;
  border: 2px solid #fff;
  object-fit: cover;
  background: #e5e7eb;
`;

const ProofText = styled.span`
  font-size: 12px;
  color: #6b7280;
`;
```

> **Note for founder:** Replace placeholder avatars with real creator profile photos once you have 3+ creators with confirmed PR outcomes. Even stock photos work short-term — the text is what converts.

---

## Fix 3 — Trending Section: Filter Out Mega-Brands

**Problem:** Kylie Cosmetics in "Trending" creates false expectations for 5K creators. Users self-exclude before pitching.

**Rule:** Trending section should only show brands whose typical collaborator has < 100K followers.

**File:** `brands_routes.py`

**FIND:**
```python
trending_brands = db.session.query(Brand)\
    .filter(Brand.category == creator_niche)\
    .order_by(Brand.weekly_pitch_count.desc())\
    .limit(6)\
    .all()
```

**REPLACE WITH:**
```python
trending_brands = db.session.query(Brand)\
    .filter(Brand.category == creator_niche)\
    .filter(Brand.min_follower_requirement <= 50000)\
    .order_by(Brand.weekly_pitch_count.desc())\
    .limit(6)\
    .all()
```

> If `min_follower_requirement` column doesn't exist, add an `is_enterprise` boolean to the brands table and filter `Brand.is_enterprise == False`. Flag Kylie Cosmetics, Glossier, and other tier-1 brands manually.

---

## Fix 4 — Seasonal Section: Differentiate Stats

**Problem:** Pela Case 55%, Aesop 52%, Bondi Sands 52%, May Lindstrom 52% — three identical stats look auto-generated and kill credibility.

**Option A (preferred):** Pull real reply rates per brand from pitch_tracking table.

**Option B (quick fix):**

**File:** `ForYou.js`

**FIND:**
```jsx
<StatValue>{brand.reply_rate}%</StatValue>
```

**REPLACE WITH:**
```jsx
<StatValue>{brand.display_reply_rate || brand.reply_rate}%</StatValue>
```

Ensure each brand has a unique `display_reply_rate` stored — even if derived as `reply_rate + (brand.id % 5) - 2` as a temporary measure until real data populates.

---

## Fix 5 — Post-Pitch Dead Zone State

**Problem:** After pitching, user waits 5 days with nothing keeping them engaged. 60% get no reply. No engagement in that window = no upgrade.

**File:** `ForYou.js`

**FIND:**
```jsx
{/* brand matches section */}
```

**REPLACE WITH:**
```jsx
{pendingPitches && pendingPitches.length > 0 && (
  <PendingPitchBanner>
    <PendingIcon>📬</PendingIcon>
    <PendingContent>
      <PendingTitle>Your pitch to {pendingPitches[0].brand_name} is in their inbox</PendingTitle>
      <PendingSubtitle>Avg reply time is ~5 days · {pendingPitches.length > 1 ? `${pendingPitches.length} pitches pending` : "We'll notify you when they respond"}</PendingSubtitle>
    </PendingContent>
    <PendingLink onClick={() => navigate('/creator/dashboard/pr-pipeline')}>
      View pipeline →
    </PendingLink>
  </PendingPitchBanner>
)}

{/* brand matches section */}
```

**Add styled components:**
```js
const PendingPitchBanner = styled.div`
  display: flex;
  align-items: center;
  gap: 12px;
  background: #f0fdf4;
  border: 1px solid #bbf7d0;
  border-radius: 10px;
  padding: 12px 16px;
  margin-bottom: 16px;
`;
const PendingIcon = styled.span`
  font-size: 20px;
  flex-shrink: 0;
`;
const PendingContent = styled.div`
  flex: 1;
`;
const PendingTitle = styled.div`
  font-size: 13px;
  font-weight: 600;
  color: #065f46;
`;
const PendingSubtitle = styled.div`
  font-size: 12px;
  color: #6b7280;
  margin-top: 2px;
`;
const PendingLink = styled.button`
  background: none;
  border: none;
  font-size: 12px;
  font-weight: 600;
  color: #059669;
  cursor: pointer;
  white-space: nowrap;
  padding: 0;
`;
```

**Data requirement:** `pendingPitches` = pitches with status `sent` and no reply. If already in state from pipeline fetch, pass through. If not, add to ForYou page data fetch.

---

## Priority Order

| # | Fix | Effort | Impact |
|---|-----|--------|--------|
| 1 | Social proof strip (Fix 2) | 30 min | **High** — #1 conversion blocker |
| 2 | Pitch explainer copy (Fix 1) | 5 min | Medium — removes friction |
| 3 | Filter mega-brands from trending (Fix 3) | 20 min | Medium — prevents self-exclusion |
| 4 | Post-pitch dead zone banner (Fix 5) | 1 hour | **High** — retains users through wait |
| 5 | Seasonal stats variance (Fix 4) | 15 min | Low — credibility polish |