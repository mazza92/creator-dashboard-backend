# For You — Matchmaking Algorithm Brief
**Goal:** Score every brand against every creator profile to surface the 10-20 most relevant brands per creator, with a pull-based Pro paywall on the bottom half.  
**Rule:** Extend existing DB tables and code. No new infrastructure. Keep UI consistent with existing brand card patterns.  
**Phased:** Phase 1 = DB + batch enrichment. Phase 2 = scoring algorithm + API. Phase 3 = frontend.

---

## What data drives the match

| Signal | Source | Weight |
|---|---|---|
| Niche relevance | `creator.niches` ↔ `brand.target_niches` | 35 pts |
| Follower range fit | `creator.follower_count` vs `brand.min_followers / max_followers` | 25 pts |
| Region match | `creator.country` ↔ `brand.target_regions` | 20 pts |
| Brand reply rate | `brand.reply_rate` | 10 pts |
| Creator engagement | `creator.engagement_rate` | 10 pts |
| **Total** | | **100 pts** |

---

## Phase 1 — Database

### 1A. Add missing columns to `brands` table

```sql
ALTER TABLE brands
  ADD COLUMN IF NOT EXISTS min_followers   INT     DEFAULT 1000,
  ADD COLUMN IF NOT EXISTS max_followers   INT     DEFAULT 500000,
  ADD COLUMN IF NOT EXISTS target_niches   JSONB   DEFAULT '[]',
  ADD COLUMN IF NOT EXISTS target_regions  JSONB   DEFAULT '["US","CA","AU","UK","NZ"]',
  ADD COLUMN IF NOT EXISTS target_gender   VARCHAR(10) DEFAULT 'any',
  ADD COLUMN IF NOT EXISTS collab_types    JSONB   DEFAULT '["gifted","paid"]';
```

> `avg_pr_value` and `reply_rate` assumed already present. If `reply_rate` is missing, add:
> ```sql
> ADD COLUMN IF NOT EXISTS reply_rate INT DEFAULT 0;
> ```

---

### 1B. Add missing columns to `creators` (or `users`) table

Check which table holds creator profile data. Add any missing:

```sql
ALTER TABLE creators
  ADD COLUMN IF NOT EXISTS niches          JSONB   DEFAULT '[]',
  ADD COLUMN IF NOT EXISTS country         VARCHAR(4) DEFAULT 'US',
  ADD COLUMN IF NOT EXISTS engagement_rate DECIMAL(5,2) DEFAULT 0.0,
  ADD COLUMN IF NOT EXISTS content_types   JSONB   DEFAULT '[]';
```

> `follower_count` assumed already present from Instagram connection.  
> `niches` should be populated from the media kit builder (user already selects niches on My Kit tab — sync those selections here on save).

---

### 1C. Niche taxonomy (reference — used in enrichment script and scoring)

```python
# Canonical niche list (matches media kit tag options exactly)
NICHES = [
    'beauty', 'skincare', 'makeup', 'haircare', 'fashion', 'lifestyle',
    'fitness', 'health', 'wellness', 'food', 'travel', 'tech',
    'gaming', 'parenting', 'home', 'diy', 'finance', 'education'
]

# Niche adjacency — adjacent niches score 60% of a direct match
NICHE_ADJACENCY = {
    'beauty':    ['skincare', 'makeup', 'haircare', 'lifestyle'],
    'skincare':  ['beauty', 'wellness', 'health'],
    'makeup':    ['beauty', 'fashion', 'lifestyle'],
    'haircare':  ['beauty', 'wellness'],
    'fashion':   ['lifestyle', 'beauty', 'travel'],
    'lifestyle': ['beauty', 'fashion', 'wellness', 'travel', 'food'],
    'fitness':   ['health', 'wellness', 'lifestyle'],
    'health':    ['wellness', 'fitness', 'food'],
    'wellness':  ['health', 'fitness', 'beauty', 'lifestyle'],
    'food':      ['lifestyle', 'health', 'travel'],
    'travel':    ['lifestyle', 'fashion', 'food'],
    'tech':      ['gaming', 'lifestyle', 'education'],
    'gaming':    ['tech', 'lifestyle'],
    'parenting': ['lifestyle', 'health', 'home'],
    'home':      ['lifestyle', 'diy', 'parenting'],
    'diy':       ['home', 'lifestyle'],
    'finance':   ['lifestyle', 'education'],
    'education': ['tech', 'lifestyle', 'finance'],
}

# Brand category → inferred target niches
CATEGORY_NICHE_MAP = {
    'Beauty':          ['beauty', 'makeup', 'skincare', 'lifestyle'],
    'Skincare':        ['skincare', 'beauty', 'wellness', 'health'],
    'Makeup':          ['makeup', 'beauty', 'lifestyle'],
    'Haircare':        ['haircare', 'beauty', 'wellness'],
    'Fragrance':       ['beauty', 'lifestyle', 'fashion'],
    'Fashion':         ['fashion', 'lifestyle', 'beauty'],
    'Activewear':      ['fitness', 'lifestyle', 'fashion', 'wellness'],
    'Fitness':         ['fitness', 'health', 'wellness', 'lifestyle'],
    'Health':          ['health', 'wellness', 'fitness', 'lifestyle'],
    'Wellness':        ['wellness', 'health', 'beauty', 'lifestyle'],
    'Supplements':     ['fitness', 'health', 'wellness'],
    'Food':            ['food', 'lifestyle', 'health'],
    'Snacks':          ['food', 'lifestyle'],
    'Beverages':       ['food', 'fitness', 'lifestyle'],
    'Home':            ['home', 'lifestyle', 'diy'],
    'Travel':          ['travel', 'lifestyle', 'fashion'],
    'Tech':            ['tech', 'lifestyle', 'gaming'],
    'Finance':         ['finance', 'lifestyle', 'education'],
    'Education':       ['education', 'lifestyle', 'parenting'],
    'Parenting':       ['parenting', 'lifestyle', 'health'],
    'Pet':             ['lifestyle', 'parenting'],
    'Sustainability':  ['lifestyle', 'wellness', 'home'],
}

# Brand category → realistic follower range for micro tier
CATEGORY_FOLLOWER_RANGE = {
    'Beauty':       (3000, 150000),
    'Skincare':     (3000, 200000),
    'Makeup':       (3000, 100000),
    'Haircare':     (3000, 100000),
    'Fragrance':    (5000, 300000),
    'Fashion':      (5000, 200000),
    'Activewear':   (5000, 300000),
    'Fitness':      (5000, 250000),
    'Health':       (3000, 200000),
    'Wellness':     (3000, 150000),
    'Supplements':  (5000, 300000),
    'Food':         (3000, 150000),
    'Snacks':       (2000, 100000),
    'Beverages':    (3000, 200000),
    'Home':         (5000, 200000),
    'Travel':       (5000, 500000),
    'Tech':         (5000, 500000),
    'Finance':      (10000, 500000),
    'Education':    (3000, 200000),
    'Parenting':    (3000, 200000),
    'Pet':          (3000, 150000),
    'Sustainability': (3000, 200000),
}
```

---

## Phase 2A — Batch brand enrichment script (run once)

This script fills `target_niches`, `min_followers`, `max_followers` for all brands that have empty values. Run it as a one-time migration. Safe to re-run — only updates rows where the field is empty/null.

**File:** `scripts/enrich_brands.py`

```python
import json
from app import db
from models import Brand  # adjust import to your structure

# --- Taxonomy maps from Phase 1C above ---
# (paste CATEGORY_NICHE_MAP and CATEGORY_FOLLOWER_RANGE here)

def enrich_brands():
    brands = Brand.query.all()
    updated = 0

    for brand in brands:
        changed = False
        category = (brand.category or '').strip()

        # Fill target_niches if empty
        existing_niches = brand.target_niches if brand.target_niches else []
        if not existing_niches:
            inferred = CATEGORY_NICHE_MAP.get(category)
            if inferred:
                brand.target_niches = inferred
                changed = True

        # Fill follower range if default/null
        if not brand.min_followers or brand.min_followers == 1000:
            range_tuple = CATEGORY_FOLLOWER_RANGE.get(category, (3000, 300000))
            brand.min_followers = range_tuple[0]
            brand.max_followers = range_tuple[1]
            changed = True

        # Default target_regions if empty
        if not brand.target_regions:
            brand.target_regions = ["US", "CA", "AU", "UK", "NZ"]
            changed = True

        if changed:
            updated += 1

        # Commit every 200 rows
        if updated % 200 == 0:
            db.session.commit()
            print(f"Committed {updated} brands...")

    db.session.commit()
    print(f"Done. {updated} brands enriched out of {len(brands)} total.")

if __name__ == '__main__':
    enrich_brands()
```

> After running, spot-check 10-20 brands in the admin panel to verify niches and follower ranges look reasonable. Adjust `CATEGORY_NICHE_MAP` and re-run if needed — it only overwrites empty rows.

---

## Phase 2B — Match scoring function

**File:** `services/matchmaker.py` (new file — or add to existing `services/` module)

```python
from typing import Optional

def compute_match_score(creator: dict, brand: dict) -> int:
    """
    Returns a 0-100 match score for a creator-brand pair.
    creator: dict with keys: niches, follower_count, country, engagement_rate
    brand:   dict with keys: target_niches, min_followers, max_followers,
                             target_regions, reply_rate
    """
    score = 0

    # ── 1. Niche match (35 pts) ───────────────────────────────
    creator_niches = set(n.lower() for n in (creator.get('niches') or []))
    brand_niches   = set(n.lower() for n in (brand.get('target_niches') or []))

    if creator_niches and brand_niches:
        # Direct overlap
        direct_overlap = creator_niches & brand_niches
        if direct_overlap:
            score += 35
        else:
            # Adjacent niche check
            for cn in creator_niches:
                adjacent = set(NICHE_ADJACENCY.get(cn, []))
                if adjacent & brand_niches:
                    score += 21  # 60% of 35
                    break

    # ── 2. Follower range (25 pts) ────────────────────────────
    followers     = creator.get('follower_count') or 0
    min_followers = brand.get('min_followers') or 0
    max_followers = brand.get('max_followers') or 999999

    if followers and min_followers <= followers <= max_followers:
        score += 25
    elif followers:
        # Within 30% outside the range
        lower_bound = min_followers * 0.7
        upper_bound = max_followers * 1.3
        if lower_bound <= followers <= upper_bound:
            score += 12

    # ── 3. Region match (20 pts) ──────────────────────────────
    creator_country  = (creator.get('country') or '').upper()
    brand_regions    = [r.upper() for r in (brand.get('target_regions') or [])]

    if creator_country and brand_regions:
        if creator_country in brand_regions:
            score += 20
        else:
            # Same broad region (rough continent map)
            REGION_GROUPS = {
                'OCEANIA': ['AU', 'NZ'],
                'NORTHAM': ['US', 'CA'],
                'EUROPE':  ['UK', 'FR', 'DE', 'IT', 'ES', 'NL', 'SE', 'NO', 'DK'],
            }
            for group in REGION_GROUPS.values():
                if creator_country in group and any(r in group for r in brand_regions):
                    score += 8
                    break

    # ── 4. Brand reply rate (10 pts) ─────────────────────────
    reply_rate = brand.get('reply_rate') or 0
    if reply_rate >= 50:
        score += 10
    elif reply_rate >= 30:
        score += 6
    elif reply_rate >= 15:
        score += 3

    # ── 5. Creator engagement rate (10 pts) ──────────────────
    er = creator.get('engagement_rate') or 0
    if er >= 5.0:
        score += 10
    elif er >= 3.0:
        score += 6
    elif er >= 1.5:
        score += 3

    return min(100, max(0, score))


def get_for_you_brands(creator: dict, all_brands: list, limit: int = 20) -> list:
    """
    Returns ranked list of brand dicts with match_score added.
    Filters out brands the creator has already pitched or saved.
    """
    pitched_ids = set(creator.get('pitched_brand_ids') or [])
    saved_ids   = set(creator.get('saved_brand_ids') or [])
    exclude     = pitched_ids | saved_ids

    scored = []
    for brand in all_brands:
        if brand['id'] in exclude:
            continue
        if not brand.get('is_active', True):
            continue
        score = compute_match_score(creator, brand)
        if score >= 30:  # minimum relevance threshold
            scored.append({**brand, 'match_score': score})

    scored.sort(key=lambda b: b['match_score'], reverse=True)
    return scored[:limit]
```

---

## Phase 2C — API endpoint

Add to your existing creator API routes (wherever `/api/creator/` routes live):

```python
from services.matchmaker import get_for_you_brands
from flask_login import login_required, current_user

@app.route('/api/creator/for-you', methods=['GET'])
@login_required
def get_for_you():
    # Build creator profile dict
    creator = {
        'niches':          current_user.niches or [],
        'follower_count':  current_user.follower_count or 0,
        'country':         current_user.country or 'US',
        'engagement_rate': float(current_user.engagement_rate or 0),
        'pitched_brand_ids': [
            p.brand_id for p in current_user.pipeline_items
            if p.stage == 'pitched' or p.pitched_at
        ],
        'saved_brand_ids': [
            p.brand_id for p in current_user.pipeline_items
            if p.stage == 'saved'
        ],
    }

    # Fetch active brands with required fields
    brands_raw = Brand.query.filter_by(is_active=True).all()
    brands = [{
        'id':              b.id,
        'brand_name':      b.brand_name,
        'category':        b.category,
        'description':     b.description,
        'logo_url':        b.logo_url,
        'reply_rate':      b.reply_rate or 0,
        'avg_pr_value':    b.avg_pr_value or 0,
        'target_niches':   b.target_niches or [],
        'min_followers':   b.min_followers or 0,
        'max_followers':   b.max_followers or 999999,
        'target_regions':  b.target_regions or [],
        'is_active':       b.is_active,
    } for b in brands_raw]

    ranked = get_for_you_brands(creator, brands, limit=20)

    # Determine user's Pro status
    is_pro = getattr(current_user, 'is_pro', False) or \
             getattr(current_user, 'subscription_status', '') == 'active'

    # Free tier: first 2 brands fully visible, rest locked
    FREE_LIMIT = 2

    result = []
    for i, brand in enumerate(ranked):
        locked = (not is_pro) and (i >= FREE_LIMIT)
        result.append({
            'id':           brand['id'] if not locked else None,
            'brand_name':   brand['brand_name'] if not locked else None,
            'category':     brand['category'],
            'description':  brand['description'] if not locked else None,
            'logo_url':     brand['logo_url'] if not locked else None,
            'reply_rate':   brand['reply_rate'],
            'avg_pr_value': brand['avg_pr_value'],
            'match_score':  brand['match_score'],
            'locked':       locked,
        })

    return jsonify({
        'brands': result,
        'total_matches': len(ranked),
        'pro_count': max(0, len(ranked) - FREE_LIMIT),
        'creator_niches': creator['niches'],
    })
```

> **Note on Pro check:** Adjust `is_pro` logic to match your actual subscription field (Stripe `subscription_status`, a boolean `is_pro`, etc.).

---

## Phase 3 — Frontend: ForYou.js

**File:** `src/creator-portal/ForYou.js` (new file — same folder as `PRPipeline.js`)

Keep the same imports, styled-component patterns, and `getApiBase()` / `getBrandLogoUrl()` helpers as `PRPipeline.js`. The component is intentionally lean — it reuses existing styled primitives where possible.

### Full component

```jsx
import React, { useState, useEffect } from 'react';
import styled, { keyframes } from 'styled-components';
import axios from 'axios';
import { motion, AnimatePresence } from 'framer-motion';
import { message } from 'antd';

// Reuse from your shared utils or inline:
const getApiBase = () =>
  process.env.NODE_ENV === 'production'
    ? 'https://api.newcollab.co'
    : 'http://localhost:5000';

const getBrandLogoUrl = (brand) => {
  if (brand.logo_url) return brand.logo_url;
  const domain = brand.website?.replace(/https?:\/\//, '').split('/')[0];
  return domain ? `https://logo.clearbit.com/${domain}` : null;
};

// ── Shimmer animation ────────────────────────────────────────
const shimmerAnim = keyframes`
  0%   { background-position: 200% 0; }
  100% { background-position: -200% 0; }
`;

// ── Component ────────────────────────────────────────────────
const ForYou = ({ currentUser, onUpgrade }) => {
  const [brands, setBrands] = useState([]);
  const [meta, setMeta] = useState({ total_matches: 0, pro_count: 0, creator_niches: [] });
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    fetchForYou();
  }, []);

  const fetchForYou = async () => {
    try {
      const res = await axios.get(`${getApiBase()}/api/creator/for-you`, {
        withCredentials: true,
      });
      setBrands(res.data.brands);
      setMeta({
        total_matches: res.data.total_matches,
        pro_count: res.data.pro_count,
        creator_niches: res.data.creator_niches || [],
      });
    } catch (err) {
      console.error('Error fetching For You:', err);
    } finally {
      setLoading(false);
    }
  };

  const handleSave = async (brandId) => {
    try {
      await axios.post(`${getApiBase()}/api/pr-crm/pipeline`, {
        brand_id: brandId, stage: 'saved',
      }, { withCredentials: true });
      message.success('Brand saved!');
      fetchForYou();
    } catch {
      message.error('Failed to save');
    }
  };

  const getScoreColor = (score) => {
    if (score >= 80) return '#059669';
    if (score >= 60) return '#D97706';
    return '#6B7280';
  };

  const getScoreLabel = (score) => {
    if (score >= 85) return 'Top match';
    if (score >= 70) return 'Strong match';
    if (score >= 55) return 'Good match';
    return 'Possible match';
  };

  if (loading) return (
    <Container>
      {[...Array(4)].map((_, i) => (
        <SkeletonCard key={i}>
          <SkeletonImg />
          <SkeletonLine w="60%" h={14} style={{ margin: '14px 14px 8px' }} />
          <SkeletonLine w="80%" h={10} style={{ margin: '0 14px 14px' }} />
        </SkeletonCard>
      ))}
    </Container>
  );

  const unlockedBrands = brands.filter(b => !b.locked);
  const lockedBrands   = brands.filter(b => b.locked);

  return (
    <Container>
      {/* Header */}
      <Header>
        <Title>For You</Title>
        <Subtitle>
          {meta.total_matches > 0
            ? `${meta.total_matches} brands match your profile`
            : 'Add your niches in My Kit to unlock matches'}
        </Subtitle>
      </Header>

      {/* Profile match tags */}
      {meta.creator_niches.length > 0 && (
        <TagStrip>
          {meta.creator_niches.slice(0, 4).map(n => (
            <Tag key={n}>{n}</Tag>
          ))}
          <Tag muted>matched</Tag>
        </TagStrip>
      )}

      {/* No niches state */}
      {meta.creator_niches.length === 0 && (
        <EmptyNiches>
          <div style={{ fontSize: 32, marginBottom: 10 }}>✨</div>
          <EmptyTitle>Set your niches to unlock matches</EmptyTitle>
          <EmptyBody>Go to My Kit, select your content niches, and we'll match you with brands that reply to creators like you.</EmptyBody>
        </EmptyNiches>
      )}

      {/* Unlocked brands */}
      {unlockedBrands.length > 0 && (
        <>
          <SectionLabel>🔓 Matched for you</SectionLabel>
          <AnimatePresence>
            {unlockedBrands.map((brand, i) => (
              <BrandCard
                key={brand.id}
                initial={{ opacity: 0, y: 16 }}
                animate={{ opacity: 1, y: 0 }}
                transition={{ delay: i * 0.05 }}
              >
                <CardImg>
                  {getBrandLogoUrl(brand) ? (
                    <BrandLogo
                      src={getBrandLogoUrl(brand)}
                      onError={e => { e.target.style.display = 'none'; e.target.nextSibling.style.display = 'flex'; }}
                    />
                  ) : null}
                  <LogoPlaceholder style={{ display: getBrandLogoUrl(brand) ? 'none' : 'flex' }}>
                    {brand.brand_name?.charAt(0)}
                  </LogoPlaceholder>
                  <MatchBadge color={getScoreColor(brand.match_score)}>
                    {brand.match_score}% · {getScoreLabel(brand.match_score)}
                  </MatchBadge>
                </CardImg>
                <CardBody>
                  <BrandName>{brand.brand_name}</BrandName>
                  <BrandDesc>{brand.description}</BrandDesc>
                  <PillRow>
                    {brand.reply_rate > 0 && (
                      <Pill fire>🔥 {brand.reply_rate}% reply rate</Pill>
                    )}
                    {brand.avg_pr_value > 0 && (
                      <Pill value>💸 ~${brand.avg_pr_value} value</Pill>
                    )}
                    {brand.category && (
                      <Pill niche>{brand.category}</Pill>
                    )}
                  </PillRow>
                  <Actions>
                    <SaveBtn onClick={() => handleSave(brand.id)}>❤ Save</SaveBtn>
                    <PitchBtn onClick={() => onUpgrade && onUpgrade(brand)}>
                      📧 Pitch this brand
                    </PitchBtn>
                  </Actions>
                </CardBody>
              </BrandCard>
            ))}
          </AnimatePresence>
        </>
      )}

      {/* Locked brands */}
      {lockedBrands.length > 0 && (
        <>
          <SectionLabel>🔒 Pro matches ({lockedBrands.length} brands)</SectionLabel>

          {lockedBrands.slice(0, 4).map((brand, i) => (
            <LockedCard key={i}>
              <LockedLeft>
                <LockedAvatar>{['🛁','✨','🌿','💄','💪','🌸'][i % 6]}</LockedAvatar>
                <LockedInfo>
                  <LockedNameBar />
                  <LockedMetaBar />
                </LockedInfo>
              </LockedLeft>
              <LockedRight>
                {brand.reply_rate > 0 && (
                  <VisibleStat>🔥 {brand.reply_rate}% reply rate</VisibleStat>
                )}
                {brand.avg_pr_value > 0 && !brand.reply_rate && (
                  <VisibleStat>💸 ~${brand.avg_pr_value} value</VisibleStat>
                )}
                <LockIcon>🔒</LockIcon>
              </LockedRight>
            </LockedCard>
          ))}

          {lockedBrands.length > 4 && (
            <MoreLocked>+{lockedBrands.length - 4} more matched brands</MoreLocked>
          )}

          {/* Upgrade CTA */}
          <UpgradeBanner onClick={onUpgrade}>
            <UpgradeText>
              <UpgradeTitle>Unlock {lockedBrands.length} matched brands</UpgradeTitle>
              <UpgradeSub>Brands that reply to creators your size · Unlimited pitches</UpgradeSub>
            </UpgradeText>
            <UpgradeBtn>$12/mo →</UpgradeBtn>
          </UpgradeBanner>
        </>
      )}
    </Container>
  );
};

// ── Styled components ────────────────────────────────────────
// (Reuse Container, Header, Title, Subtitle from PRPipeline if shared,
//  otherwise define below to stay self-contained)

const Container = styled.div`
  max-width: 800px;
  margin: 0 auto;
  padding-bottom: 80px;
`;

const Header = styled.div`
  padding: 20px 16px 4px;
`;

const Title = styled.h1`
  font-size: 22px;
  font-weight: 900;
  color: #0F0F0F;
  margin-bottom: 4px;
`;

const Subtitle = styled.p`
  font-size: 13px;
  color: #6B7280;
`;

const TagStrip = styled.div`
  display: flex;
  gap: 6px;
  padding: 8px 16px 12px;
  flex-wrap: wrap;
`;

const Tag = styled.span`
  background: ${p => p.muted ? '#F3F4F6' : '#F5F3FF'};
  color: ${p => p.muted ? '#9CA3AF' : '#7C3AED'};
  border: 1px solid ${p => p.muted ? '#E5E7EB' : '#DDD6FE'};
  padding: 4px 11px;
  border-radius: 20px;
  font-size: 12px;
  font-weight: 600;
  text-transform: capitalize;
`;

const SectionLabel = styled.div`
  font-size: 11px;
  font-weight: 800;
  color: #6B7280;
  text-transform: uppercase;
  letter-spacing: 0.4px;
  padding: 12px 16px 6px;
`;

const BrandCard = styled(motion.div)`
  margin: 0 16px 12px;
  background: #fff;
  border-radius: 18px;
  overflow: hidden;
  box-shadow: 0 1px 4px rgba(0,0,0,0.06);
`;

const CardImg = styled.div`
  height: 90px;
  background: #F9FAFB;
  display: flex;
  align-items: center;
  justify-content: center;
  position: relative;
`;

const BrandLogo = styled.img`
  max-height: 56px;
  max-width: 140px;
  object-fit: contain;
`;

const LogoPlaceholder = styled.div`
  width: 52px;
  height: 52px;
  border-radius: 14px;
  background: linear-gradient(135deg, #7C3AED22, #E11D4822);
  color: #7C3AED;
  font-size: 22px;
  font-weight: 800;
  align-items: center;
  justify-content: center;
`;

const MatchBadge = styled.div`
  position: absolute;
  bottom: 8px;
  right: 10px;
  background: ${p => p.color};
  color: #fff;
  font-size: 10px;
  font-weight: 800;
  padding: 3px 9px;
  border-radius: 20px;
`;

const CardBody = styled.div`
  padding: 12px 14px 14px;
`;

const BrandName = styled.div`
  font-size: 15px;
  font-weight: 800;
  color: #0F0F0F;
  margin-bottom: 4px;
`;

const BrandDesc = styled.div`
  font-size: 12px;
  color: #6B7280;
  line-height: 1.45;
  margin-bottom: 10px;
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
`;

const PillRow = styled.div`
  display: flex;
  gap: 6px;
  flex-wrap: wrap;
  margin-bottom: 12px;
`;

const Pill = styled.span`
  padding: 4px 10px;
  border-radius: 20px;
  font-size: 11px;
  font-weight: 600;
  background: ${p => p.fire ? '#FEF3C7' : p.value ? '#FCE7F3' : '#EFF6FF'};
  color: ${p => p.fire ? '#92400E' : p.value ? '#9D174D' : '#1D4ED8'};
`;

const Actions = styled.div`
  display: flex;
  gap: 8px;
`;

const SaveBtn = styled.button`
  flex: 1;
  padding: 10px;
  border-radius: 11px;
  background: #FFF1F2;
  color: #E11D48;
  border: 1.5px solid #FECDD3;
  font-size: 13px;
  font-weight: 700;
  cursor: pointer;
`;

const PitchBtn = styled.button`
  flex: 2;
  padding: 10px;
  border-radius: 11px;
  background: #0F0F0F;
  color: #fff;
  border: none;
  font-size: 13px;
  font-weight: 700;
  cursor: pointer;
`;

const LockedCard = styled.div`
  margin: 0 16px 8px;
  background: #F9FAFB;
  border: 1.5px dashed #E5E7EB;
  border-radius: 16px;
  padding: 12px 14px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
`;

const LockedLeft = styled.div`
  display: flex;
  align-items: center;
  gap: 10px;
  flex: 1;
`;

const LockedAvatar = styled.div`
  width: 40px;
  height: 40px;
  border-radius: 12px;
  background: #E5E7EB;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 18px;
  flex-shrink: 0;
`;

const LockedInfo = styled.div`
  flex: 1;
`;

const LockedNameBar = styled.div`
  height: 12px;
  width: 110px;
  border-radius: 6px;
  background: linear-gradient(90deg, #E5E7EB 25%, #F3F4F6 50%, #E5E7EB 75%);
  background-size: 200% 100%;
  animation: ${shimmerAnim} 1.5s infinite;
  margin-bottom: 7px;
`;

const LockedMetaBar = styled.div`
  height: 10px;
  width: 75px;
  border-radius: 5px;
  background: #F3F4F6;
`;

const LockedRight = styled.div`
  display: flex;
  flex-direction: column;
  align-items: flex-end;
  gap: 5px;
  flex-shrink: 0;
`;

const VisibleStat = styled.div`
  font-size: 11px;
  font-weight: 700;
  color: #059669;
`;

const LockIcon = styled.div`
  font-size: 16px;
`;

const MoreLocked = styled.div`
  text-align: center;
  font-size: 12px;
  font-weight: 600;
  color: #9CA3AF;
  padding: 6px 0 10px;
`;

const UpgradeBanner = styled.div`
  margin: 4px 16px 20px;
  background: linear-gradient(135deg, #4C1D95, #831843);
  border-radius: 18px;
  padding: 18px;
  display: flex;
  align-items: center;
  gap: 14px;
  cursor: pointer;
`;

const UpgradeText = styled.div`
  flex: 1;
`;

const UpgradeTitle = styled.div`
  font-size: 14px;
  font-weight: 800;
  color: #fff;
  margin-bottom: 3px;
`;

const UpgradeSub = styled.div`
  font-size: 11.5px;
  color: rgba(255,255,255,0.7);
`;

const UpgradeBtn = styled.button`
  background: #fff;
  color: #4C1D95;
  font-size: 13px;
  font-weight: 800;
  padding: 10px 16px;
  border-radius: 12px;
  border: none;
  cursor: pointer;
  white-space: nowrap;
  flex-shrink: 0;
`;

const EmptyNiches = styled.div`
  margin: 24px 16px;
  background: #fff;
  border-radius: 18px;
  padding: 28px 20px;
  text-align: center;
  border: 1.5px dashed #E5E7EB;
`;

const EmptyTitle = styled.div`
  font-size: 15px;
  font-weight: 800;
  color: #0F0F0F;
  margin-bottom: 8px;
`;

const EmptyBody = styled.div`
  font-size: 13px;
  color: #6B7280;
  line-height: 1.5;
`;

// Skeleton
const SkeletonCard = styled.div`
  margin: 0 16px 12px;
  background: #fff;
  border-radius: 18px;
  overflow: hidden;
`;

const SkeletonImg = styled.div`
  height: 90px;
  background: linear-gradient(90deg, #F3F4F6 25%, #E5E7EB 50%, #F3F4F6 75%);
  background-size: 200% 100%;
  animation: ${shimmerAnim} 1.5s infinite;
`;

const SkeletonLine = styled.div`
  height: ${p => p.h || 12}px;
  width: ${p => p.w || '60%'};
  border-radius: 6px;
  background: linear-gradient(90deg, #F3F4F6 25%, #E5E7EB 50%, #F3F4F6 75%);
  background-size: 200% 100%;
  animation: ${shimmerAnim} 1.5s infinite;
`;

export default ForYou;
```

---

## Phase 3B — Wire up in main app routing

Find where the "For You" tab currently renders in your navigation component (likely `App.js`, `Dashboard.js`, or wherever `PRPipeline` is imported).

**FIND (wherever For You tab content renders — may be an empty div or placeholder):**
```jsx
{activeTab === 'for-you' && (
  <div>For You coming soon</div>
)}
```

**REPLACE WITH:**
```jsx
{activeTab === 'for-you' && (
  <ForYou
    currentUser={currentUser}
    onUpgrade={() => setShowUpgradeModal(true)}
  />
)}
```

**Also add import at top of that file:**
```jsx
import ForYou from './ForYou';
```

---

## Phase 3C — Sync niches from media kit to creator record

When the user saves their media kit (the `handleSave` or `handlePublish` function in your media kit component), also write their selected niches back to the creator record:

**FIND** (in your media kit save function, wherever you PATCH/POST the kit data):
```python
# In your media kit save endpoint
kit.niches = data.get('niches', [])
kit.content_types = data.get('content_types', [])
db.session.commit()
```

**ADD AFTER:**
```python
# Sync niches to creator record for matchmaking
if current_user.niches != data.get('niches'):
    current_user.niches = data.get('niches', [])
    current_user.content_types = data.get('content_types', [])
```

---

## Checklist

### Phase 1 — DB
- [ ] `brands` table: `min_followers`, `max_followers`, `target_niches`, `target_regions` columns added
- [ ] `creators` table: `niches`, `country`, `engagement_rate` columns confirmed/added
- [ ] Niche taxonomy defined in shared constants file

### Phase 2 — Backend
- [ ] `scripts/enrich_brands.py` run on staging, spot-checked, run on production
- [ ] `services/matchmaker.py` created with `compute_match_score` and `get_for_you_brands`
- [ ] `GET /api/creator/for-you` endpoint live
- [ ] Pro check uses correct subscription field
- [ ] Locked brands have `id`, `brand_name`, `description`, `logo_url` set to `null`

### Phase 3 — Frontend
- [ ] `src/creator-portal/ForYou.js` created
- [ ] Wired into main tab routing
- [ ] `onUpgrade` prop connects to existing `UpgradeModal`
- [ ] "Pitch this brand" calls existing `AIPitchModal` flow
- [ ] Media kit save syncs `niches` → `creator.niches`
- [ ] Empty state shows when `creator_niches` is empty (prompts to fill My Kit)

### Data quality checks
- [ ] Spot-check 20 brands: do `target_niches` match their actual product?
- [ ] Spot-check 5 creators: does their match list feel relevant?
- [ ] Confirm `FREE_LIMIT = 2` is the right balance (2 unlocked, rest locked)
- [ ] Verify locked brands expose `reply_rate` and `avg_pr_value` only — not `brand_name` or `id`
