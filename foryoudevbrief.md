# NewCollab — "For You" Tab: Full Developer Brief

**Reference UI:** `for-you-preview.html` (open in browser)
**Core principle:** Build on top of existing code. Reuse everything possible. This feature is a new *view* of existing brand data — not a new system.

---

## 1. What This Is (and Isn't)

**Is:** A curated, personalised brand recommendation page that replaces the "PR Offers" tab. Three sections of brands surfaced via simple SQL queries against data you already have.

**Is not:** A new AI system, a new brand model, a new pitch flow. The pitch modal, quota logic, save flow, and brand cards are all existing components — just reused here with a new entry point.

**Single UX goal:** Creator opens this tab, sees brands curated for them, taps "Pitch Now", done. No searching, no filtering, no decisions. We make the work easy.

---

## 2. Files Touched

| File | Change |
|------|--------|
| `src/Layouts/CreatorDashboardLayout.js` | Rename "PR Offers" tab → "For You ✨" + update route |
| `src/App.js` | Add route `/creator/dashboard/for-you` |
| `src/cra-pages/ForYou.js` | **New file** — the page component |
| `src/cra-pages/UnifiedBrandDirectory.js` | Extract `BrandCard` into shared component (see §4) |
| `creator-dashboard-backend/pr_crm_routes.py` | Add `GET /api/for-you` endpoint |
| `creator-dashboard-backend/user_routes.py` | Add `PATCH /api/user/profile` for niche/follower update |
| DB | Add 2 columns to `users` table (see §3) |

**Do not touch:** UpgradeModal, pitch modal, quota logic, email crons, brand model.

---

## 3. Database — Minimal Migration

Two new columns on `users`. Everything else uses existing tables.

```sql
ALTER TABLE users
  ADD COLUMN IF NOT EXISTS creator_niches    TEXT[]  DEFAULT '{}',
  ADD COLUMN IF NOT EXISTS creator_followers INT     DEFAULT 0;

-- creator_niches: array of strings e.g. ['beauty', 'lifestyle']
-- creator_followers: integer e.g. 12000
-- These power the match score in Section 2
```

No other schema changes. The For You queries join against `brands`, `saved_brands`, and `users` — all existing tables.

---

## 4. Extract Shared BrandCard Component

Before building ForYou, extract the brand card from `UnifiedBrandDirectory.js` into a shared component so both pages use the same code.

**Create:** `src/components/BrandCard.js`

```jsx
// src/components/BrandCard.js
// Extract the existing brand card JSX + styled-components from UnifiedBrandDirectory.js
// Props interface:
//
// brand         — brand object from API
// isPro         — bool
// hasPitched    — bool (user already pitched this brand)
// isSaved       — bool
// atLimit       — bool (free user at 3/month)
// onPitch       — fn(brand) — opens pitch modal
// onSave        — fn(brand) — saves/unsaves
// onUpgrade     — fn()     — opens upgrade modal
// variant       — 'default' | 'compact' (compact = seasonal card layout)
// badge         — optional: { type: 'hot'|'seasonal'|'new', label: string }
// matchScore    — optional: int 0–100 (shows match badge top-right)
// momentumBar   — optional: { rate: int, label: string }

export default function BrandCard({ ... }) { ... }
```

Move the styled-components (`Card`, `LogoBox`, `BtnContact`, etc.) into this file.
Update `UnifiedBrandDirectory.js` to import from `src/components/BrandCard.js`.
The ForYou page will also import from the same place.

---

## 5. Backend — GET /api/for-you

Add to `pr_crm_routes.py`. One endpoint, three sections, all SQL — no external APIs.

```python
from datetime import datetime

@pr_crm_bp.route('/api/for-you', methods=['GET'])
@login_required
def get_for_you():
    user_id = get_current_user_id()
    is_pro  = get_subscription_status(user_id) == 'pro'

    user = db.execute(
        "SELECT creator_niches, creator_followers FROM users WHERE id = %s",
        (user_id,)
    ).fetchone()

    niches    = user['creator_niches'] or []
    followers = user['creator_followers'] or 0

    # IDs the user has already pitched (exclude from recommendations)
    already_pitched = db.execute("""
        SELECT brand_id FROM saved_brands
        WHERE user_id = %s AND send_confirmed = TRUE
    """, (user_id,)).fetchall()
    exclude_ids = [r['brand_id'] for r in already_pitched] or [0]

    # ── Section 1: Hot This Week ─────────────────────────────
    # Brands with the most pipeline wins (received/won) in last 7 days
    # from ANY user. Free for everyone.
    hot = db.execute("""
        SELECT
            b.*,
            COUNT(*) FILTER (
                WHERE sb.pipeline_stage IN ('won','received')
                AND sb.package_confirmed_at > NOW() - INTERVAL '7 days'
            ) AS wins_this_week,
            COUNT(*) FILTER (
                WHERE sb.send_confirmed = TRUE
                AND sb.pitched_at > NOW() - INTERVAL '7 days'
            ) AS pitched_this_week
        FROM brands b
        LEFT JOIN saved_brands sb ON sb.brand_id = b.id
        WHERE b.is_active = TRUE
          AND b.id != ALL(%s)
        GROUP BY b.id
        HAVING COUNT(*) FILTER (
            WHERE sb.send_confirmed = TRUE
            AND sb.pitched_at > NOW() - INTERVAL '7 days'
        ) >= 1
        ORDER BY wins_this_week DESC, b.response_rate DESC
        LIMIT 3
    """, (exclude_ids,)).fetchall()

    # Fallback: if not enough activity yet, fill with highest response rate
    if len(hot) < 3:
        fallback = db.execute("""
            SELECT b.*, 0 AS wins_this_week, 0 AS pitched_this_week
            FROM brands b
            WHERE b.is_active = TRUE
              AND b.id != ALL(%s)
              AND b.id != ALL(%s)
            ORDER BY b.response_rate DESC NULLS LAST
            LIMIT %s
        """, (
            exclude_ids,
            [r['id'] for r in hot] or [0],
            3 - len(hot)
        )).fetchall()
        hot = list(hot) + list(fallback)

    # ── Section 2: Matched for You ───────────────────────────
    # Pro only — personalised by niche + follower count.
    # Free users get 1 card visible, rest are blurred (frontend handles blur).
    # Match score: niche match (50pts) + follower fit (30pts) + response rate (20pts max)
    matched = []
    if niches or followers:
        matched = db.execute("""
            SELECT
                b.*,
                (
                    CASE WHEN b.category = ANY(%s) THEN 50 ELSE 15 END
                    + CASE WHEN %s >= COALESCE(b.min_followers_count, 0) THEN 30 ELSE 10 END
                    + LEAST(COALESCE(b.response_rate, 0) / 2, 20)
                ) AS match_score
            FROM brands b
            WHERE b.is_active = TRUE
              AND b.id != ALL(%s)
            ORDER BY match_score DESC, b.response_rate DESC
            LIMIT 8
        """, (niches or [''], followers, exclude_ids)).fetchall()
    else:
        # No profile yet — return top brands by response rate as default
        matched = db.execute("""
            SELECT b.*, 75 AS match_score
            FROM brands b
            WHERE b.is_active = TRUE AND b.id != ALL(%s)
            ORDER BY b.response_rate DESC NULLS LAST
            LIMIT 8
        """, (exclude_ids,)).fetchall()

    # ── Section 3: Right Season ──────────────────────────────
    # Brands in categories that match current month. Free for everyone.
    month = datetime.now().month
    seasonal_map = {
        1:  ['fitness', 'wellness', 'lifestyle'],
        2:  ['beauty', 'jewelry', 'fashion', 'skincare'],
        3:  ['beauty', 'fashion', 'skincare'],
        4:  ['fashion', 'lifestyle', 'beauty'],
        5:  ['fashion', 'lifestyle', 'skincare'],
        6:  ['lifestyle', 'beauty', 'skincare', 'fashion'],
        7:  ['lifestyle', 'beauty', 'fashion'],
        8:  ['beauty', 'lifestyle', 'fashion'],
        9:  ['fashion', 'beauty', 'lifestyle'],
        10: ['fashion', 'beauty', 'lifestyle', 'home'],
        11: ['beauty', 'fashion', 'home', 'jewelry'],
        12: ['beauty', 'fashion', 'home', 'jewelry', 'lifestyle'],
    }
    seasonal_cats = seasonal_map.get(month, ['beauty', 'lifestyle'])

    seasonal_reasons = {
        1:  "New Year reset — wellness brands gifting heavily in January",
        2:  "Valentine's season — beauty and jewelry brands seeking creators",
        3:  "Spring launch season — skincare brands partnering with creators",
        4:  "Spring fashion drops — brands seeking fresh campaign content",
        5:  "Pre-summer prep — lifestyle and skincare brands gifting now",
        6:  "Summer campaigns — SPF and fashion brands need content now",
        7:  "Peak summer — lifestyle brands seeking authentic summer content",
        8:  "Late summer push — fashion and beauty brands preparing for fall",
        9:  "Back to school/fall — fashion brands refreshing their creator roster",
        10: "Pre-holiday — beauty and home brands building gifting lists",
        11: "Holiday gifting season — brands most active for PR partnerships",
        12: "Year-end gifting — brands clearing PR budgets before January",
    }

    seasonal = db.execute("""
        SELECT b.*
        FROM brands b
        WHERE b.is_active = TRUE
          AND b.category = ANY(%s)
          AND b.id != ALL(%s)
        ORDER BY b.response_rate DESC NULLS LAST
        LIMIT 4
    """, (seasonal_cats, exclude_ids)).fetchall()

    return jsonify({
        'hot':              [dict(r) for r in hot],
        'matched':          [dict(r) for r in matched],
        'seasonal':         [dict(r) for r in seasonal],
        'seasonal_reason':  seasonal_reasons.get(month, ''),
        'seasonal_month':   datetime.now().strftime('%B'),
        'is_pro':           is_pro,
        'has_profile':      bool(niches or followers),
        'profile': {
            'niches':    niches,
            'followers': followers,
        }
    })
```

---

## 6. Backend — PATCH /api/user/profile

Quick endpoint to save creator's niche + follower count (needed to power match scores).
Add to `user_routes.py` or `subscription_routes.py` — wherever user profile updates live.

```python
@user_bp.route('/api/user/profile', methods=['PATCH'])
@login_required
def update_creator_profile():
    user_id = get_current_user_id()
    data    = request.get_json()

    allowed = {'creator_niches', 'creator_followers'}
    updates = {k: v for k, v in data.items() if k in allowed}
    if not updates: return jsonify({'error': 'Nothing to update'}), 400

    set_clause = ', '.join(f"{k} = %s" for k in updates)
    db.execute(
        f"UPDATE users SET {set_clause} WHERE id = %s",
        (*updates.values(), user_id)
    )
    db.commit()
    return jsonify({'success': True})
```

---

## 7. Frontend — ForYou.js

**Create:** `src/cra-pages/ForYou.js`

### 7a. Imports — reuse everything

```jsx
'use client'; // only if Next.js App Router

import { useState, useEffect, useCallback } from 'react';
import styled from 'styled-components';
import BrandCard from '../components/BrandCard';           // extracted in §4
import UpgradeModal from '../creator-portal/UpgradeModal'; // existing, unchanged
// Reuse the same pitch modal component from UnifiedBrandDirectory
import PitchModal from '../creator-portal/PitchModal';     // existing, unchanged
```

### 7b. State

```jsx
const [data, setData]               = useState(null);
const [loading, setLoading]         = useState(true);
const [pitchingBrand, setPitchingBrand] = useState(null);   // opens pitch modal
const [showUpgrade, setShowUpgrade] = useState(false);
const [upgradeReason, setUpgradeReason] = useState('');
const [savedIds, setSavedIds]       = useState(new Set());
const [pitchedIds, setPitchedIds]   = useState(new Set());

// Pull from existing UserContext (already available everywhere in the app)
const { user, subscriptionStatus, pitchesSentThisMonth } = useContext(UserContext);
const isPro    = subscriptionStatus === 'pro';
const atLimit  = !isPro && pitchesSentThisMonth >= 3;

useEffect(() => {
  fetch('/api/for-you')
    .then(r => r.json())
    .then(d => { setData(d); setLoading(false); });
}, []);
```

### 7c. "Pitch Now" handler — single action replaces Save + Contact

This is the key UX simplification. One tap does what previously required two (Save + Contact).

```jsx
const handlePitchNow = useCallback((brand) => {
  // 1. Check quota — reuse exact same logic as UnifiedBrandDirectory
  if (atLimit) {
    setUpgradeReason('limit');
    setShowUpgrade(true);
    return;
  }
  if (brand.requires_pro && !isPro) {
    setUpgradeReason('pro_brand');
    setShowUpgrade(true);
    return;
  }

  // 2. Auto-save to pipeline (stage: 'saved') if not already saved
  if (!savedIds.has(brand.id)) {
    fetch('/api/save-brand', {            // existing endpoint, unchanged
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ brand_id: brand.id }),
    });
    setSavedIds(prev => new Set([...prev, brand.id]));
  }

  // 3. Open pitch modal — same component as Discover page
  setPitchingBrand(brand);
}, [atLimit, isPro, savedIds]);

const handlePitchSent = useCallback((brand) => {
  // Called by PitchModal after user confirms send
  // Reuse the same post-pitch logic from UnifiedBrandDirectory:
  // - increment pitchesSentThisMonth in context
  // - mark brand as pitched
  setPitchedIds(prev => new Set([...prev, brand.id]));
  setPitchingBrand(null);
  // confirm-send advances pipeline stage to 'waiting' (handled in PitchModal already)
}, []);
```

### 7d. Profile prompt — shown when no niche/follower data

Simple inline prompt, not a modal. Keeps page lightweight.

```jsx
function ProfilePrompt({ onSave }) {
  const [niches, setNiches] = useState([]);
  const [followers, setFollowers] = useState('');

  const NICHE_OPTIONS = [
    'beauty', 'fashion', 'lifestyle', 'fitness',
    'food', 'travel', 'home', 'skincare', 'haircare', 'jewelry'
  ];

  const handleSave = () => {
    fetch('/api/user/profile', {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        creator_niches:    niches,
        creator_followers: parseInt(followers) || 0,
      }),
    }).then(() => onSave());
  };

  return (
    <ProfilePromptCard>
      <PromptIcon>🎯</PromptIcon>
      <PromptTitle>Tell us about your niche</PromptTitle>
      <PromptSub>Takes 10 seconds — unlocks brands matched specifically to you</PromptSub>

      <NicheGrid>
        {NICHE_OPTIONS.map(n => (
          <NicheChip
            key={n}
            $selected={niches.includes(n)}
            onClick={() => setNiches(prev =>
              prev.includes(n) ? prev.filter(x => x !== n) : [...prev, n]
            )}
          >
            {n}
          </NicheChip>
        ))}
      </NicheGrid>

      <FollowerInput
        type="number"
        placeholder="Your total followers (e.g. 8000)"
        value={followers}
        onChange={e => setFollowers(e.target.value)}
      />

      <SaveProfileBtn
        disabled={niches.length === 0}
        onClick={handleSave}
      >
        Show my matches →
      </SaveProfileBtn>
    </ProfilePromptCard>
  );
}
```

### 7e. Pro paywall on Matched section — frontend only

The blur + overlay is purely CSS/JSX. The backend still returns all 8 matched brands (needed for the count). Frontend renders all but blurs index >= 1 for free users.

```jsx
// Inside the Matched section render:
{data.matched.map((brand, i) => {
  const isBlurred = !isPro && i >= 1;
  return (
    <div key={brand.id} style={isBlurred ? { filter: 'blur(5px)', pointerEvents: 'none', userSelect: 'none' } : {}}>
      <BrandCard
        brand={brand}
        isPro={isPro}
        hasPitched={pitchedIds.has(brand.id)}
        isSaved={savedIds.has(brand.id)}
        atLimit={atLimit}
        onPitch={handlePitchNow}
        onUpgrade={() => { setUpgradeReason('matched'); setShowUpgrade(true); }}
        matchScore={brand.match_score}
        badge={null}
      />
    </div>
  );
})}

{/* Paywall overlay — only shown to free users */}
{!isPro && (
  <PaywallOverlay>
    <PaywallCard>
      <PaywallIcon>🎯</PaywallIcon>
      <PaywallTitle>See your full brand matches</PaywallTitle>
      <PaywallSub>
        {data.matched.length - 1} more brands matched to your niche and following —
        updated every week.
      </PaywallSub>
      <PaywallFeatures>
        <Feature>Match score explained for each brand</Feature>
        <Feature>8 personalised matches, refreshed weekly</Feature>
        <Feature>Unlimited contacts + AI follow-up emails</Feature>
      </PaywallFeatures>
      <UpgradeBtn onClick={() => { setUpgradeReason('matched'); setShowUpgrade(true); }}>
        ⚡ Upgrade to Pro — $12/mo
      </UpgradeBtn>
      <PaywallHint>Most creators land their first collab within 2 weeks</PaywallHint>
    </PaywallCard>
  </PaywallOverlay>
)}
```

### 7f. Full page render structure

```jsx
return (
  <PageWrap>
    <PageInner>

      {/* Profile prompt — only shown if no niche data yet */}
      {!loading && !data.has_profile && (
        <ProfilePrompt onSave={() => {
          // Refetch recommendations after profile saved
          setLoading(true);
          fetch('/api/for-you').then(r => r.json()).then(d => {
            setData(d);
            setLoading(false);
          });
        }} />
      )}

      {/* Slim quota strip — reuse QuotaStrip from UnifiedBrandDirectory */}
      {!isPro && <QuotaStrip compact />}

      {/* Section 1: Hot This Week */}
      <Section>
        <SectionHeader icon="🔥" iconBg="#FFF7ED" title="Hot This Week"
          desc="Brands where creators are landing collabs right now" />
        <CardGrid cols={3}>
          {data?.hot.map(brand => (
            <BrandCard key={brand.id} brand={brand}
              isPro={isPro} hasPitched={pitchedIds.has(brand.id)}
              isSaved={savedIds.has(brand.id)} atLimit={atLimit}
              onPitch={handlePitchNow}
              onUpgrade={() => { setUpgradeReason('limit'); setShowUpgrade(true); }}
              badge={{ type: 'hot', label: brand.wins_this_week > 0 ? `🔥 ${brand.wins_this_week} wins this week` : '🔥 Rising fast' }}
              momentumBar={{ rate: brand.response_rate, label: 'Response rate this week' }}
            />
          ))}
        </CardGrid>
      </Section>

      {/* Section 2: Matched for You (Pro gate) */}
      <Section>
        <SectionHeader icon="🎯" iconBg="#F5F3FF" title="Matched for You"
          desc="Scored by fit with your niche, following and pitch history"
          proLabel />
        <MatchedGrid>   {/* relative positioned for overlay */}
          {/* cards with blur logic — see §7e */}
        </MatchedGrid>
      </Section>

      {/* Section 3: Right Season */}
      <Section>
        <SectionHeader icon="📅" iconBg="#ECFDF5"
          title={`Right Season — ${data?.seasonal_month}`}
          desc={data?.seasonal_reason} />
        <SeasonalGrid cols={2}>
          {data?.seasonal.map(brand => (
            <BrandCard key={brand.id} brand={brand}
              isPro={isPro} hasPitched={pitchedIds.has(brand.id)}
              isSaved={savedIds.has(brand.id)} atLimit={atLimit}
              onPitch={handlePitchNow}
              onUpgrade={() => { setUpgradeReason('limit'); setShowUpgrade(true); }}
              variant="compact"
              badge={{ type: 'seasonal', label: '📅 Seasonal pick' }}
            />
          ))}
        </SeasonalGrid>
      </Section>

    </PageInner>

    {/* Reuse existing modals — zero new modal code */}
    {pitchingBrand && (
      <PitchModal
        brand={pitchingBrand}
        onClose={() => setPitchingBrand(null)}
        onSent={handlePitchSent}
      />
    )}
    {showUpgrade && (
      <UpgradeModal
        reason={upgradeReason}
        onClose={() => setShowUpgrade(false)}
      />
    )}

  </PageWrap>
);
```

---

## 8. Nav Update — CreatorDashboardLayout.js

One line change. Rename the tab and update its path.

```jsx
// FIND (in navItems array):
{ label: 'PR Offers', icon: Gift, path: '/creator/dashboard/pr-offers' },

// REPLACE WITH:
{ label: 'For You', icon: Sparkles, path: '/creator/dashboard/for-you' },

// Import Sparkles from lucide-react (already installed)
// Mobile bottom bar: same change — label 'For You', icon Sparkles
```

---

## 9. Route — App.js

```jsx
// FIND the PR Offers route:
<Route path="/creator/dashboard/pr-offers" element={<PROffers />} />

// REPLACE WITH:
<Route path="/creator/dashboard/for-you" element={<ForYou />} />

// Add import at top:
import ForYou from './cra-pages/ForYou';
```

---

## 10. QuotaStrip Compact Variant

The quota strip already exists in `UnifiedBrandDirectory.js`. Add a `compact` prop so it renders as a slim single line on the For You page (less visual noise, creators are here to act — not to be reminded of limits).

```jsx
// In the existing QuotaStrip component, add compact mode:
// compact = true: single line, no progress bar, just text + upgrade link
// compact = false (default): full strip with bar (existing behaviour)

{compact ? (
  <QuotaCompact>
    <span>{FREE_PITCH_LIMIT - pitchesSentThisMonth} free contacts left this month</span>
    <UpgradeLink onClick={onUpgrade}>Upgrade for unlimited →</UpgradeLink>
  </QuotaCompact>
) : (
  // existing full quota strip JSX
)}
```

---

## 11. Styled Components — New Only

Only the components that don't exist anywhere yet. Everything else is imported from existing files.

```jsx
// All in ForYou.js

const PageWrap  = styled.div`background:#F5F5F7;min-height:100vh;`;
const PageInner = styled.div`
  max-width:1160px;margin:0 auto;padding:32px 24px 80px;
  @media(max-width:640px){padding:20px 14px 80px;}
`;

const Section = styled.div`margin-bottom:36px;`;

const SectionHeaderWrap = styled.div`
  display:flex;align-items:center;justify-content:space-between;margin-bottom:16px;
`;
const SectionLeft  = styled.div`display:flex;align-items:center;gap:10px;`;
const SectionIcon  = styled.div`
  width:34px;height:34px;border-radius:10px;
  background:${p=>p.$bg||'#F4F4F4'};
  display:grid;place-items:center;font-size:17px;flex-shrink:0;
`;
const SectionTitle = styled.div`font-size:16px;font-weight:800;letter-spacing:-.2px;`;
const SectionDesc  = styled.div`font-size:12px;color:#8C8C8C;margin-top:1px;`;
const ProLabel     = styled.span`
  font-size:11px;font-weight:800;
  background:linear-gradient(135deg,#E11D48,#7C3AED);
  -webkit-background-clip:text;-webkit-text-fill-color:transparent;
`;

const CardGrid = styled.div`
  display:grid;
  grid-template-columns:repeat(${p=>p.$cols||3},1fr);
  gap:14px;
  @media(max-width:1024px){grid-template-columns:repeat(2,1fr);}
  @media(max-width:640px){grid-template-columns:1fr;}
`;

const MatchedGrid = styled.div`
  display:grid;grid-template-columns:repeat(3,1fr);gap:14px;
  position:relative;
  @media(max-width:1024px){grid-template-columns:repeat(2,1fr);}
  @media(max-width:640px){grid-template-columns:1fr;}
`;

// Paywall overlay
const PaywallOverlay = styled.div`
  position:absolute;inset:0;
  display:flex;align-items:center;justify-content:center;
  z-index:10;
  background:linear-gradient(to bottom,rgba(245,245,247,0) 0%,rgba(245,245,247,.6) 25%,rgba(245,245,247,.97) 55%);
  border-radius:18px;
`;
const PaywallCard = styled.div`
  background:#fff;border:1px solid #E8E8E8;border-radius:20px;
  padding:28px 32px;text-align:center;max-width:380px;width:90%;
  box-shadow:0 8px 40px rgba(15,15,15,.1);margin-top:60px;
`;
const PaywallIcon  = styled.div`font-size:36px;margin-bottom:12px;`;
const PaywallTitle = styled.div`font-size:18px;font-weight:800;letter-spacing:-.3px;margin-bottom:6px;`;
const PaywallSub   = styled.div`font-size:13px;color:#4B4B4B;line-height:1.6;margin-bottom:18px;`;
const PaywallFeatureList = styled.div`display:flex;flex-direction:column;gap:8px;text-align:left;margin-bottom:20px;`;
const PaywallFeature = styled.div`
  display:flex;align-items:center;gap:9px;
  font-size:13px;font-weight:500;color:#4B4B4B;
  &::before{content:'✓';width:18px;height:18px;border-radius:50%;
    background:#ECFDF5;border:1px solid #A7F3D0;color:#059669;
    display:grid;place-items:center;font-size:11px;font-weight:800;flex-shrink:0;}
`;
const UpgradeBtn = styled.button`
  width:100%;padding:14px;
  background:linear-gradient(135deg,#E11D48,#7C3AED);color:#fff;
  border:none;border-radius:12px;
  font-size:14.5px;font-weight:700;cursor:pointer;font-family:inherit;
  box-shadow:0 4px 14px rgba(225,29,72,.25);transition:all .2s;margin-bottom:10px;
  &:hover{transform:translateY(-1px);box-shadow:0 6px 20px rgba(225,29,72,.35);}
`;
const PaywallHint  = styled.div`font-size:11.5px;color:#8C8C8C;`;

// Profile prompt
const ProfilePromptCard = styled.div`
  background:#fff;border:1px solid #E8E8E8;border-radius:18px;
  padding:24px;margin-bottom:24px;text-align:center;
  box-shadow:0 1px 3px rgba(15,15,15,.05);
`;
const PromptIcon  = styled.div`font-size:32px;margin-bottom:10px;`;
const PromptTitle = styled.div`font-size:16px;font-weight:800;margin-bottom:4px;`;
const PromptSub   = styled.div`font-size:13px;color:#8C8C8C;margin-bottom:18px;`;
const NicheGrid   = styled.div`display:flex;flex-wrap:wrap;gap:8px;justify-content:center;margin-bottom:16px;`;
const NicheChip   = styled.button`
  padding:7px 14px;border-radius:100px;font-size:13px;font-weight:600;
  cursor:pointer;font-family:inherit;transition:all .15s;
  background:${p=>p.$selected?'#0F0F0F':'#F4F4F4'};
  color:${p=>p.$selected?'#fff':'#4B4B4B'};
  border:${p=>p.$selected?'1px solid #0F0F0F':'1px solid #E8E8E8'};
`;
const FollowerInput = styled.input`
  width:100%;max-width:280px;padding:11px 16px;
  border:1px solid #E8E8E8;border-radius:12px;
  font-size:14px;font-family:inherit;outline:none;margin-bottom:16px;
  &:focus{border-color:#0F0F0F;}
  &::placeholder{color:#8C8C8C;}
`;
const SaveProfileBtn = styled.button`
  padding:12px 28px;border-radius:100px;
  background:${p=>p.disabled?'#F4F4F4':'#0F0F0F'};
  color:${p=>p.disabled?'#8C8C8C':'#fff'};
  border:none;font-size:14px;font-weight:700;
  cursor:${p=>p.disabled?'not-allowed':'pointer'};font-family:inherit;
  transition:all .15s;
`;

// Compact quota strip (new variant)
const QuotaCompact = styled.div`
  display:flex;align-items:center;justify-content:space-between;
  background:#fff;border:1px solid #E8E8E8;border-radius:12px;
  padding:10px 16px;margin-bottom:24px;font-size:13px;color:#4B4B4B;
`;
```

---

## 12. Dev Checklist

**Database**
- [ ] Run migration: add `creator_niches TEXT[]` and `creator_followers INT` to `users`

**Backend**
- [ ] Add `GET /api/for-you` to `pr_crm_routes.py`
- [ ] Add `PATCH /api/user/profile` to user routes
- [ ] Test `/api/for-you` returns all 3 sections with correct data
- [ ] Verify fallback logic fires when `hot` section has < 3 results
- [ ] Confirm `exclude_ids` correctly filters already-pitched brands

**Frontend — Shared component (do this first)**
- [ ] Extract `BrandCard` from `UnifiedBrandDirectory.js` into `src/components/BrandCard.js`
- [ ] Update `UnifiedBrandDirectory.js` to import `BrandCard` from shared location
- [ ] Confirm `UnifiedBrandDirectory.js` still works identically after extraction
- [ ] Add `compact` prop to `QuotaStrip`

**Frontend — ForYou.js**
- [ ] Create `src/cra-pages/ForYou.js`
- [ ] Profile prompt renders when `data.has_profile === false`
- [ ] Section 1 (Hot): 3 cards, `badge` shows wins count or "Rising fast"
- [ ] Section 2 (Matched): all 8 cards rendered, cards at index >= 1 blurred for free users
- [ ] Section 2: paywall overlay with upgrade CTA visible for free users
- [ ] Section 2: no paywall for Pro users, all 8 cards interactive
- [ ] Section 3 (Seasonal): 4 cards in 2-column grid, `seasonal_reason` in header
- [ ] "Pitch Now" triggers save-to-pipeline + opens existing PitchModal
- [ ] `atLimit` check fires before opening modal (reuses existing quota logic)
- [ ] `UpgradeModal` opens with correct `reason` prop
- [ ] Quota compact strip visible for free users only

**Nav + Routing**
- [ ] `CreatorDashboardLayout.js`: rename tab "PR Offers" → "For You", icon → `Sparkles`, path → `/for-you`
- [ ] Mobile bottom bar: same rename
- [ ] `App.js`: replace old route with `/creator/dashboard/for-you` → `<ForYou />`
- [ ] Confirm old `/pr-offers` route removed (no broken links)

**QA**
- [ ] Free user: Section 2 blurred after card 1, paywall visible
- [ ] Pro user: all sections fully interactive, no paywalls
- [ ] Free user at limit: "Pitch Now" opens UpgradeModal, not pitch modal
- [ ] Profile prompt: selecting niches + followers → saves → page refetches with personalised data
- [ ] Page loads correctly with zero pipeline history (new user)
- [ ] `seasonal_month` and `seasonal_reason` display correctly for current month
