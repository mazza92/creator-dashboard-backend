# Opportunities Feature — Full Dev Brief
## Brand casting calls as a sub-tab inside For You

**Goal:** Add a job-board layer to the app where brands post PR opportunities for free. Creators apply with their media kit. Newcollab reviews and approves listings before they go live. Creates exclusive proprietary demand that drives Pro upgrades.

**Key principle:** Reuse existing patterns (pitch credit system, admin dashboard, email templates). Minimal new code surface.

---

## Corrections / Constraints

| Rule | Detail |
|------|--------|
| No new top-level nav tab | Opportunities lives as a sub-tab inside For You only |
| Reuse credit system | Applications use same 3/month free limit logic as pitches |
| No marketplace | Brands post free, no payments, no platform fee |
| Admin approves before live | No opportunity goes live without manual review |
| Call it "Opportunities" | Not "casting calls", not "campaigns" |

---

## Files Affected

| File | Change |
|------|--------|
| `pr_crm_routes.py` | Add opportunity application endpoint |
| New: `opportunities_routes.py` | Brand submit + creator list + admin endpoints |
| `ForYou.js` | Add sub-tabs + conditional render |
| New: `OpportunitiesTab.js` | Opportunities feed component |
| New: `BrandSubmitForm.js` (or `/for-brands` route) | Public brand submission page |
| `admin/Reports.js` or new `admin/Opportunities.js` | Admin review queue |
| DB migration | 2 new tables |

---

## Phase 1 — Database

Run these migrations before any frontend work.

```sql
-- Opportunities posted by brands
CREATE TABLE opportunities (
  id                  SERIAL PRIMARY KEY,
  brand_name          VARCHAR(255) NOT NULL,
  brand_website       VARCHAR(500),
  brand_email         VARCHAR(255) NOT NULL,
  brand_category      VARCHAR(100),
  product_name        VARCHAR(255) NOT NULL,
  campaign_description TEXT NOT NULL,
  pr_value_usd        INTEGER,
  creator_count_range VARCHAR(50),        -- '3-5' | '5-10' | '10-20'
  shipping_regions    TEXT[],             -- ['US', 'UK', 'AU']
  follower_ranges     TEXT[],             -- ['1K-10K', '10K-50K']
  content_types       TEXT[],             -- ['TikTok', 'Reel']
  creator_niches      TEXT[],             -- ['Fitness', 'Wellness']
  additional_notes    TEXT,
  application_deadline DATE,
  spots_total         INTEGER NOT NULL DEFAULT 5,
  spots_filled        INTEGER NOT NULL DEFAULT 0,
  status              VARCHAR(50) NOT NULL DEFAULT 'pending',
                                          -- pending | live | paused | closed
  created_at          TIMESTAMP NOT NULL DEFAULT NOW(),
  published_at        TIMESTAMP,
  closes_at           TIMESTAMP
);

-- Creator applications to opportunities
CREATE TABLE opportunity_applications (
  id               SERIAL PRIMARY KEY,
  opportunity_id   INTEGER NOT NULL REFERENCES opportunities(id) ON DELETE CASCADE,
  creator_id       INTEGER NOT NULL REFERENCES creators(id) ON DELETE CASCADE,
  applied_at       TIMESTAMP NOT NULL DEFAULT NOW(),
  status           VARCHAR(50) NOT NULL DEFAULT 'pending',
                                          -- pending | approved | declined
  brand_notified_at   TIMESTAMP,
  creator_notified_at TIMESTAMP,
  UNIQUE(opportunity_id, creator_id)
);

-- Index for fast creator-side queries
CREATE INDEX idx_opp_status ON opportunities(status);
CREATE INDEX idx_opp_niches ON opportunities USING GIN(creator_niches);
CREATE INDEX idx_app_creator ON opportunity_applications(creator_id);
```

---

## Phase 2 — Backend

### New file: `opportunities_routes.py`

Create alongside `pr_crm_routes.py`. Register blueprint in `app.py` as `/api`.

```python
from flask import Blueprint, request, jsonify
from models import db, Opportunity, OpportunityApplication, Creator
from auth import require_creator_auth, require_admin_auth
from emails import send_email
from datetime import datetime

opportunities = Blueprint('opportunities', __name__)


# ── PUBLIC: Brand submits form ──────────────────────────────────────────────

@opportunities.route('/public/opportunities/submit', methods=['POST'])
def brand_submit():
    data = request.get_json()

    required = ['brand_name', 'brand_email', 'brand_website',
                'product_name', 'campaign_description', 'spots_total']
    for field in required:
        if not data.get(field):
            return jsonify({'error': f'{field} is required'}), 400

    opp = Opportunity(
        brand_name=data['brand_name'],
        brand_email=data['brand_email'],
        brand_website=data['brand_website'],
        brand_category=data.get('brand_category'),
        product_name=data['product_name'],
        campaign_description=data['campaign_description'],
        pr_value_usd=data.get('pr_value_usd'),
        creator_count_range=data.get('creator_count_range', '5-10'),
        shipping_regions=data.get('shipping_regions', []),
        follower_ranges=data.get('follower_ranges', []),
        content_types=data.get('content_types', []),
        creator_niches=data.get('creator_niches', []),
        additional_notes=data.get('additional_notes'),
        application_deadline=data.get('application_deadline'),
        spots_total=int(data['spots_total']),
        status='pending'
    )
    db.session.add(opp)
    db.session.commit()

    # Notify admin
    send_email(
        to='mahery@newcollab.co',
        subject=f'New opportunity to review: {opp.brand_name}',
        body=f'{opp.brand_name} submitted an opportunity for {opp.product_name}. '
             f'Review at: https://app.newcollab.co/admin/opportunities/{opp.id}'
    )
    # Confirm to brand
    send_email(
        to=opp.brand_email,
        subject='Your Newcollab listing is under review',
        body=f'Hi {opp.brand_name}, we received your opportunity listing for '
             f'{opp.product_name}. We will review and publish it within 24 hours.'
    )

    return jsonify({'success': True, 'id': opp.id}), 201


# ── CREATOR: List live opportunities ────────────────────────────────────────

@opportunities.route('/creator/opportunities', methods=['GET'])
@require_creator_auth
def list_opportunities(creator_id):
    creator = Creator.query.get(creator_id)
    creator_niches = creator.niches or []

    all_live = Opportunity.query.filter_by(status='live').all()

    # Matched to creator niche first, then others
    matched = []
    others = []
    for opp in all_live:
        if opp.spots_filled >= opp.spots_total:
            continue  # full, skip
        opp_niches = opp.creator_niches or []
        is_match = not opp_niches or any(n in creator_niches for n in opp_niches)
        (matched if is_match else others).append(opp)

    # Check which ones creator already applied to
    applied_ids = {
        a.opportunity_id
        for a in OpportunityApplication.query.filter_by(creator_id=creator_id).all()
    }

    def serialize(opp, is_matched):
        spots_left = opp.spots_total - opp.spots_filled
        days_left = None
        if opp.closes_at:
            delta = (opp.closes_at - datetime.utcnow()).days
            days_left = max(0, delta)
        return {
            'id': opp.id,
            'brand_name': opp.brand_name,
            'brand_category': opp.brand_category,
            'product_name': opp.product_name,
            'campaign_description': opp.campaign_description,
            'pr_value_usd': opp.pr_value_usd,
            'creator_count_range': opp.creator_count_range,
            'shipping_regions': opp.shipping_regions,
            'follower_ranges': opp.follower_ranges,
            'content_types': opp.content_types,
            'spots_total': opp.spots_total,
            'spots_left': spots_left,
            'days_left': days_left,
            'is_matched': is_matched,
            'already_applied': opp.id in applied_ids
        }

    return jsonify({
        'success': True,
        'matched': [serialize(o, True) for o in matched],
        'others': [serialize(o, False) for o in others]
    })


# ── CREATOR: Apply to opportunity ───────────────────────────────────────────

@opportunities.route('/creator/opportunities/<int:opp_id>/apply', methods=['POST'])
@require_creator_auth
def apply_opportunity(creator_id, opp_id):
    opp = Opportunity.query.get_or_404(opp_id)

    if opp.status != 'live':
        return jsonify({'error': 'This opportunity is no longer active'}), 400
    if opp.spots_filled >= opp.spots_total:
        return jsonify({'error': 'No spots remaining'}), 400

    existing = OpportunityApplication.query.filter_by(
        opportunity_id=opp_id, creator_id=creator_id
    ).first()
    if existing:
        return jsonify({'error': 'You have already applied'}), 409

    # Deduct one application credit (same pool as pitches)
    # Re-use existing pitch limit check
    from pr_crm_routes import get_pitch_limits_internal
    limits = get_pitch_limits_internal(creator_id)
    if not limits['canPitch']:
        return jsonify({
            'error': 'limit_reached',
            'message': 'You have used all your free applications this month'
        }), 403

    application = OpportunityApplication(
        opportunity_id=opp_id,
        creator_id=creator_id
    )
    db.session.add(application)
    opp.spots_filled += 1

    # Track the credit usage (same as pitch)
    from pr_crm_routes import track_pitch_usage_internal
    track_pitch_usage_internal(creator_id)

    db.session.commit()

    # Notify brand
    creator = Creator.query.get(creator_id)
    send_email(
        to=opp.brand_email,
        subject=f'New application from {creator.display_name} for {opp.product_name}',
        body=f'{creator.display_name} ({creator.follower_count} followers, '
             f'{", ".join(creator.niches or [])}) applied to your opportunity.\n\n'
             f'View their media kit: https://app.newcollab.co/kit/{creator.slug}\n\n'
             f'Reply to this email to approve or decline.'
    )

    return jsonify({'success': True}), 201


# ── ADMIN: List pending opportunities ───────────────────────────────────────

@opportunities.route('/admin/opportunities', methods=['GET'])
@require_admin_auth
def admin_list():
    status = request.args.get('status', 'pending')
    opps = Opportunity.query.filter_by(status=status)\
                            .order_by(Opportunity.created_at.desc()).all()
    return jsonify({'success': True, 'opportunities': [
        {
            'id': o.id,
            'brand_name': o.brand_name,
            'brand_email': o.brand_email,
            'product_name': o.product_name,
            'status': o.status,
            'created_at': o.created_at.isoformat(),
            'spots_total': o.spots_total,
            'spots_filled': o.spots_filled
        } for o in opps
    ]})


# ── ADMIN: Publish opportunity ───────────────────────────────────────────────

@opportunities.route('/admin/opportunities/<int:opp_id>/publish', methods=['PATCH'])
@require_admin_auth
def admin_publish(opp_id):
    opp = Opportunity.query.get_or_404(opp_id)
    days_open = request.get_json().get('days_open', 14)
    opp.status = 'live'
    opp.published_at = datetime.utcnow()
    opp.closes_at = datetime.utcnow() + __import__('datetime').timedelta(days=days_open)
    db.session.commit()

    send_email(
        to=opp.brand_email,
        subject=f'Your Newcollab listing is live',
        body=f'Your opportunity for {opp.product_name} is now live on Newcollab. '
             f'Creators will start applying within the next 24 hours.'
    )
    return jsonify({'success': True})


# ── ADMIN: Reject opportunity ────────────────────────────────────────────────

@opportunities.route('/admin/opportunities/<int:opp_id>/reject', methods=['PATCH'])
@require_admin_auth
def admin_reject(opp_id):
    opp = Opportunity.query.get_or_404(opp_id)
    reason = request.get_json().get('reason', '')
    opp.status = 'rejected'
    db.session.commit()

    send_email(
        to=opp.brand_email,
        subject='Your Newcollab listing needs some changes',
        body=f'Thanks for submitting to Newcollab. We were not able to publish '
             f'your listing for {opp.product_name} at this time. '
             + (f'Reason: {reason}' if reason else '')
             + '\n\nFeel free to resubmit or email us at brands@newcollab.co.'
    )
    return jsonify({'success': True})
```

### Register blueprint in `app.py`

**FIND:**
```python
from pr_crm_routes import pr_crm
app.register_blueprint(pr_crm, url_prefix='/api')
```

**REPLACE WITH:**
```python
from pr_crm_routes import pr_crm
from opportunities_routes import opportunities
app.register_blueprint(pr_crm, url_prefix='/api')
app.register_blueprint(opportunities, url_prefix='/api')
```

### Add helper to `pr_crm_routes.py`

Expose internal pitch limit functions so opportunities can reuse them without circular imports.

**FIND** the end of the `get_pitch_limits` function and **ADD** after it:

```python
def get_pitch_limits_internal(creator_id):
    """Reusable by other blueprints."""
    from models import Creator, PitchTracking
    creator = Creator.query.get(creator_id)
    tier = getattr(creator, 'tier', 'free')
    FREE_MONTHLY_LIMIT = 3
    is_pro = tier in ['pro', 'elite']
    # Count pitches + opportunity applications this month
    from datetime import datetime
    now = datetime.utcnow()
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    used = PitchTracking.query.filter(
        PitchTracking.creator_id == creator_id,
        PitchTracking.created_at >= month_start
    ).count()
    return {
        'used': used,
        'limit': FREE_MONTHLY_LIMIT if not is_pro else 999,
        'canPitch': is_pro or used < FREE_MONTHLY_LIMIT,
        'tier': tier
    }


def track_pitch_usage_internal(creator_id):
    """Reusable credit deduction for opportunities."""
    from models import PitchTracking
    record = PitchTracking(creator_id=creator_id)
    db.session.add(record)
    # Do not commit here — caller commits
```

---

## Phase 3 — Frontend: For You Sub-Tabs

Minimal change to `ForYou.js`. Add state + sub-tab UI + conditional render.

### 3a — Add state

**FIND:**
```jsx
const [selectedBrand, setSelectedBrand] = useState(null);
```

**REPLACE WITH:**
```jsx
const [selectedBrand, setSelectedBrand] = useState(null);
const [forYouTab, setForYouTab] = useState('matches'); // 'matches' | 'opportunities'
const [opportunities, setOpportunities] = useState({ matched: [], others: [] });
const [oppsLoading, setOppsLoading] = useState(false);
```

### 3b — Fetch opportunities when tab switches

**FIND:**
```jsx
useEffect(() => {
  fetchBrands();
}, []);
```

**REPLACE WITH:**
```jsx
useEffect(() => {
  fetchBrands();
}, []);

useEffect(() => {
  if (forYouTab === 'opportunities' && opportunities.matched.length === 0) {
    setOppsLoading(true);
    api.get('/creator/opportunities')
      .then(res => setOpportunities(res.data))
      .finally(() => setOppsLoading(false));
  }
}, [forYouTab]);
```

### 3c — Insert sub-tabs between refresh row and section content

**FIND** (the line just before the `Matched for You` section header):
```jsx
<RefreshStatus>
  ...
</RefreshStatus>
```

**REPLACE WITH:**
```jsx
<RefreshStatus>
  ...
</RefreshStatus>

<SubTabRow>
  <SubTab
    active={forYouTab === 'matches'}
    onClick={() => setForYouTab('matches')}
  >
    🎯 Matches
  </SubTab>
  <SubTab
    active={forYouTab === 'opportunities'}
    onClick={() => setForYouTab('opportunities')}
  >
    ⚡ Opportunities
    {opportunities.matched.length + opportunities.others.length > 0 && (
      <TabCount>{opportunities.matched.length + opportunities.others.length}</TabCount>
    )}
  </SubTab>
</SubTabRow>

{forYouTab === 'opportunities' ? (
  <OpportunitiesTab
    opportunities={opportunities}
    loading={oppsLoading}
    pitchLimits={pitchLimits}
  />
) : (
  /* existing matches content — no changes needed */
  <>
```

Close the fragment at the end of the existing matches render block:
```jsx
  </>
)}
```

### 3d — Add styled components

```js
const SubTabRow = styled.div`
  display: flex;
  gap: 0;
  border-bottom: 1px solid #e5e7eb;
  margin-bottom: 20px;
`;

const SubTab = styled.button`
  display: flex;
  align-items: center;
  gap: 7px;
  padding: 9px 16px;
  font-size: 13px;
  font-weight: 600;
  color: ${p => p.active ? '#0F0F0F' : '#6b7280'};
  background: none;
  border: none;
  border-bottom: 2px solid ${p => p.active ? '#0F0F0F' : 'transparent'};
  margin-bottom: -1px;
  cursor: pointer;
  transition: all 0.15s;

  &:hover { color: #0F0F0F; }
`;

const TabCount = styled.span`
  background: #E11D48;
  color: #fff;
  font-size: 10px;
  font-weight: 700;
  padding: 1px 6px;
  border-radius: 10px;
`;
```

---

## Phase 4 — New Component: `OpportunitiesTab.js`

Create `src/creator-portal/OpportunitiesTab.js`.

```jsx
import React, { useState } from 'react';
import styled from 'styled-components';
import api from '../api';

export default function OpportunitiesTab({ opportunities, loading, pitchLimits }) {
  const [applying, setApplying] = useState(null);
  const [applied, setApplied] = useState(new Set());

  const handleApply = async (oppId) => {
    if (!pitchLimits.canPitch) {
      // Reuse existing UpgradeModal trigger
      window.dispatchEvent(new CustomEvent('show-upgrade-modal', {
        detail: { feature: 'opportunities' }
      }));
      return;
    }
    setApplying(oppId);
    try {
      await api.post(`/creator/opportunities/${oppId}/apply`);
      setApplied(prev => new Set([...prev, oppId]));
    } catch (err) {
      if (err.response?.data?.error === 'limit_reached') {
        window.dispatchEvent(new CustomEvent('show-upgrade-modal', {
          detail: { feature: 'opportunities' }
        }));
      }
    } finally {
      setApplying(null);
    }
  };

  const creditsLeft = pitchLimits
    ? pitchLimits.limit - pitchLimits.used
    : 0;
  const isPro = pitchLimits?.tier === 'pro';

  if (loading) return <LoadingText>Finding opportunities for you...</LoadingText>;

  const allOpps = [...(opportunities.matched || []), ...(opportunities.others || [])];
  if (allOpps.length === 0) {
    return (
      <EmptyState>
        <EmptyIcon>📣</EmptyIcon>
        <EmptyTitle>No open opportunities right now</EmptyTitle>
        <EmptyText>Check back soon. New campaigns are added weekly.</EmptyText>
        <BrandInviteBtn
          href="/for-brands"
          target="_blank"
        >
          Are you a brand? Post an opportunity
        </BrandInviteBtn>
      </EmptyState>
    );
  }

  return (
    <Wrap>
      <IntroCard>
        <IntroIcon>📣</IntroIcon>
        <IntroText>
          <IntroTitle>Brands looking for creators</IntroTitle>
          <IntroSub>Apply with your kit. The brand reviews your profile and ships product if you're selected.</IntroSub>
        </IntroText>
      </IntroCard>

      {!isPro && (
        <CreditRow>
          <CreditPips>
            {[...Array(3)].map((_, i) => (
              <Pip key={i} used={i >= creditsLeft} />
            ))}
          </CreditPips>
          <CreditText>
            <strong>{creditsLeft} application{creditsLeft !== 1 ? 's' : ''}</strong> left this month
          </CreditText>
          <CreditUpgrade onClick={() => window.dispatchEvent(new CustomEvent('show-upgrade-modal', { detail: { feature: 'opportunities' } }))}>
            Unlimited with Pro &rsaquo;
          </CreditUpgrade>
        </CreditRow>
      )}

      {opportunities.matched?.length > 0 && (
        <SectionLabel>Matched to your niche</SectionLabel>
      )}

      {(opportunities.matched || []).map(opp => (
        <OppCard
          key={opp.id}
          opp={opp}
          isPro={isPro}
          applying={applying === opp.id}
          applied={applied.has(opp.id) || opp.already_applied}
          onApply={() => handleApply(opp.id)}
        />
      ))}

      {opportunities.others?.length > 0 && (
        <SectionLabel style={{ marginTop: 8 }}>More opportunities</SectionLabel>
      )}

      {(opportunities.others || []).map(opp => (
        <OppCard
          key={opp.id}
          opp={opp}
          isPro={isPro}
          applying={applying === opp.id}
          applied={applied.has(opp.id) || opp.already_applied}
          onApply={() => handleApply(opp.id)}
        />
      ))}

      <BrandInviteBanner>
        <BrandInviteTitle>Are you a brand looking for creators?</BrandInviteTitle>
        <BrandInviteSub>Post your opportunity for free. You approve every creator before we share your details.</BrandInviteSub>
        <BrandInviteBtn href="/for-brands" target="_blank">
          Post an opportunity &rsaquo;
        </BrandInviteBtn>
        <BrandInviteNote>Free to post · Reviewed within 24 hours</BrandInviteNote>
      </BrandInviteBanner>
    </Wrap>
  );
}

function OppCard({ opp, isPro, applying, applied, onApply }) {
  const spotsLeft = opp.spots_left;
  const spotsPercent = Math.round(((opp.spots_total - spotsLeft) / opp.spots_total) * 100);
  const urgency = spotsLeft <= 1 ? 'critical' : spotsLeft <= 2 ? 'high' : 'normal';

  return (
    <Card>
      <CardTop>
        <BrandLogo>{opp.brand_name.substring(0, 2).toUpperCase()}</BrandLogo>
        <BrandBlock>
          <BrandNameRow>
            <BrandName>{opp.brand_name}</BrandName>
            <VerifiedDot>
              <svg width="8" height="8" viewBox="0 0 10 10" fill="white">
                <path d="M2 5l2.5 2.5L8 3" stroke="white" strokeWidth="1.5" fill="none"/>
              </svg>
            </VerifiedDot>
          </BrandNameRow>
          <BrandSub>{opp.brand_category} · Ships {(opp.shipping_regions || []).join(' / ')}</BrandSub>
        </BrandBlock>
        <BadgeStack>
          <OpenBadge>Live</OpenBadge>
          {urgency === 'critical' && <UrgencyBadge>Last spot</UrgencyBadge>}
          {urgency === 'high' && <UrgencyBadge level="medium">{spotsLeft} spots left</UrgencyBadge>}
          {opp.days_left !== null && opp.days_left <= 5 && (
            <ClosingBadge>Closes in {opp.days_left}d</ClosingBadge>
          )}
        </BadgeStack>
      </CardTop>

      <CardDesc>{opp.campaign_description}</CardDesc>

      <ChipRow>
        {opp.follower_ranges?.length > 0 && (
          <Chip>👥 {opp.follower_ranges.join(' or ')} followers</Chip>
        )}
        {opp.content_types?.length > 0 && (
          <Chip>📱 {opp.content_types.join(' · ')}</Chip>
        )}
        {opp.shipping_regions?.length > 0 && (
          <Chip>🌍 {opp.shipping_regions.join(' · ')}</Chip>
        )}
        {opp.application_deadline && (
          <Chip>📅 Post by {opp.application_deadline}</Chip>
        )}
      </ChipRow>

      <StatsRow>
        <Stat>
          <StatVal green>~${opp.pr_value_usd || '?'}</StatVal>
          <StatLbl>PR Value</StatLbl>
        </Stat>
        <StatDivider />
        <Stat>
          <StatVal amber>{opp.days_left !== null ? `${opp.days_left}d` : 'Open'}</StatVal>
          <StatLbl>Closes in</StatLbl>
        </Stat>
        <StatDivider />
        <Stat>
          <StatVal violet>{opp.spots_left}</StatVal>
          <StatLbl>Spots left</StatLbl>
        </Stat>
      </StatsRow>

      <SpotsRow>
        <SpotsLabel>Spots</SpotsLabel>
        <SpotsTrack>
          <SpotsFill percent={spotsPercent} urgency={urgency} />
        </SpotsTrack>
        <SpotsCount urgency={urgency}>
          {spotsLeft} of {opp.spots_total} left
        </SpotsCount>
      </SpotsRow>

      {applied ? (
        <AppliedState>Application sent · Waiting for brand approval</AppliedState>
      ) : (
        <ApplyBtn onClick={onApply} disabled={applying}>
          {applying ? 'Sending...' : 'Apply Now'}
        </ApplyBtn>
      )}
    </Card>
  );
}

/* Styled components — extend existing patterns */
const Wrap = styled.div``;
const LoadingText = styled.p`font-size:13px;color:#6b7280;padding:20px 0;text-align:center;`;
const SectionLabel = styled.div`
  font-size: 11px; font-weight: 700; color: #6b7280;
  text-transform: uppercase; letter-spacing: .07em;
  margin-bottom: 10px;
`;
const IntroCard = styled.div`
  background: #fff; border: 1px solid #e5e7eb; border-radius: 12px;
  padding: 14px 16px; display: flex; align-items: center;
  gap: 12px; margin-bottom: 14px;
`;
const IntroIcon = styled.span`font-size: 24px; flex-shrink: 0;`;
const IntroText = styled.div`flex: 1;`;
const IntroTitle = styled.div`font-size: 13px; font-weight: 700; color: #0F0F0F; margin-bottom: 2px;`;
const IntroSub = styled.div`font-size: 12px; color: #6b7280; line-height: 1.5;`;

const CreditRow = styled.div`
  display: flex; align-items: center; gap: 10px;
  background: #fff; border: 1px solid #e5e7eb;
  border-radius: 10px; padding: 10px 14px; margin-bottom: 16px;
`;
const CreditPips = styled.div`display: flex; gap: 4px;`;
const Pip = styled.div`
  width: 8px; height: 8px; border-radius: 50%;
  background: ${p => p.used ? '#e5e7eb' : '#0F0F0F'};
`;
const CreditText = styled.div`font-size: 12px; color: #6b7280; flex: 1;`;
const CreditUpgrade = styled.button`
  font-size: 12px; font-weight: 700; color: #7C3AED;
  background: none; border: none; cursor: pointer;
`;

const Card = styled.div`
  background: #fff; border: 1px solid #e5e7eb;
  border-radius: 14px; padding: 18px; margin-bottom: 12px;
`;
const CardTop = styled.div`display: flex; align-items: flex-start; gap: 12px; margin-bottom: 12px;`;
const BrandLogo = styled.div`
  width: 44px; height: 44px; border-radius: 12px;
  background: #f3f4f6; display: flex; align-items: center;
  justify-content: center; font-size: 13px; font-weight: 800;
  color: #6b7280; flex-shrink: 0;
`;
const BrandBlock = styled.div`flex: 1;`;
const BrandNameRow = styled.div`display: flex; align-items: center; gap: 5px; margin-bottom: 2px;`;
const BrandName = styled.div`font-size: 15px; font-weight: 800; color: #0F0F0F;`;
const VerifiedDot = styled.div`
  width: 15px; height: 15px; border-radius: 50%;
  background: #7C3AED; display: flex; align-items: center; justify-content: center;
`;
const BrandSub = styled.div`font-size: 12px; color: #6b7280;`;
const BadgeStack = styled.div`display: flex; flex-direction: column; align-items: flex-end; gap: 4px;`;
const OpenBadge = styled.span`
  background: #d1fae5; color: #065f46;
  font-size: 10px; font-weight: 700; padding: 2px 7px; border-radius: 8px;
`;
const UrgencyBadge = styled.span`
  background: ${p => p.level === 'medium' ? '#fef3c7' : '#fee2e2'};
  color: ${p => p.level === 'medium' ? '#92400e' : '#991b1b'};
  font-size: 10px; font-weight: 700; padding: 2px 7px; border-radius: 8px;
`;
const ClosingBadge = styled.span`
  background: #fef3c7; color: #92400e;
  font-size: 10px; font-weight: 700; padding: 2px 7px; border-radius: 8px;
`;
const CardDesc = styled.p`font-size: 13px; color: #374151; line-height: 1.6; margin-bottom: 12px;`;
const ChipRow = styled.div`display: flex; flex-wrap: wrap; gap: 6px; margin-bottom: 12px;`;
const Chip = styled.div`
  background: #f9fafb; border: 1px solid #e5e7eb; border-radius: 8px;
  padding: 4px 9px; font-size: 11px; color: #374151;
`;
const StatsRow = styled.div`
  display: flex; align-items: stretch;
  background: #f9fafb; border: 1px solid #e5e7eb;
  border-radius: 10px; overflow: hidden; margin-bottom: 12px;
`;
const Stat = styled.div`flex: 1; padding: 9px 10px; text-align: center;`;
const StatDivider = styled.div`width: 1px; background: #e5e7eb;`;
const StatVal = styled.div`
  font-size: 15px; font-weight: 800; margin-bottom: 2px;
  color: ${p => p.green ? '#059669' : p.amber ? '#d97706' : p.violet ? '#7C3AED' : '#0F0F0F'};
`;
const StatLbl = styled.div`font-size: 9px; font-weight: 600; color: #9ca3af; text-transform: uppercase; letter-spacing: .06em;`;
const SpotsRow = styled.div`display: flex; align-items: center; gap: 10px; margin-bottom: 12px;`;
const SpotsLabel = styled.span`font-size: 11px; color: #6b7280; flex-shrink: 0;`;
const SpotsTrack = styled.div`flex: 1; height: 5px; background: #e5e7eb; border-radius: 4px; overflow: hidden;`;
const SpotsFill = styled.div`
  height: 100%; border-radius: 4px;
  width: ${p => p.percent}%;
  background: ${p => p.urgency === 'critical' ? '#E11D48' : p.urgency === 'high' ? '#d97706' : '#059669'};
`;
const SpotsCount = styled.span`
  font-size: 11px; font-weight: 700; flex-shrink: 0;
  color: ${p => p.urgency === 'critical' ? '#E11D48' : p.urgency === 'high' ? '#d97706' : '#374151'};
`;
const ApplyBtn = styled.button`
  width: 100%; background: #0F0F0F; color: #fff;
  border: none; border-radius: 10px; padding: 12px;
  font-size: 13px; font-weight: 700; cursor: pointer;
  &:hover { opacity: .85; }
  &:disabled { opacity: .5; cursor: not-allowed; }
`;
const AppliedState = styled.div`
  text-align: center; font-size: 13px; font-weight: 600;
  color: #059669; padding: 12px; background: #f0fdf4;
  border-radius: 10px; border: 1px solid #bbf7d0;
`;
const EmptyState = styled.div`text-align: center; padding: 40px 0;`;
const EmptyIcon = styled.div`font-size: 32px; margin-bottom: 12px;`;
const EmptyTitle = styled.div`font-size: 16px; font-weight: 700; margin-bottom: 6px;`;
const EmptyText = styled.div`font-size: 13px; color: #6b7280; margin-bottom: 16px;`;
const BrandInviteBanner = styled.div`
  background: #f9fafb; border: 1.5px dashed #d1d5db;
  border-radius: 14px; padding: 20px; text-align: center; margin-top: 8px;
`;
const BrandInviteTitle = styled.div`font-size: 14px; font-weight: 700; margin-bottom: 5px;`;
const BrandInviteSub = styled.div`font-size: 12px; color: #6b7280; line-height: 1.6; margin-bottom: 12px;`;
const BrandInviteBtn = styled.a`
  display: inline-block; background: #0F0F0F; color: #fff;
  border-radius: 10px; padding: 10px 20px;
  font-size: 13px; font-weight: 700; cursor: pointer; text-decoration: none;
  margin-bottom: 8px;
`;
const BrandInviteNote = styled.div`font-size: 11px; color: #9ca3af;`;
```

---

## Phase 5 — Public Brand Form Page

Create route `/for-brands` in the frontend router and a new page component `BrandSubmitPage.js`.

The 4-step form collects:

| Step | Fields |
|------|--------|
| 1. Brand | name, website (required for verification), email, category |
| 2. Campaign | product name, description (2-4 sentences), retail value, creator count (3-5 / 5-10 / 10-20), shipping regions, deadline |
| 3. Creators | follower range (multi-select), content type (multi-select), niche (optional multi-select), open notes |
| 4. Review | live preview of listing, "what happens next" steps, privacy note, submit |

On submit, calls `POST /api/public/opportunities/submit`.

On success, shows confirmation screen with:
- "Reviewed within 24 hours"
- "You will receive applications at [email] with each creator's media kit"
- "Reply to approve or decline each one"
- Contact: `brands@newcollab.co`

**Trust signals to include on every step:**
- "Free to post" in the header
- "Reviewed within 24 hours" badge
- "Your address is never shared until you approve each creator" on step 4

> The full interactive HTML mockup of this form is in `/home/user/brand-submit-form.html`. Use it as the design reference.

---

## Phase 6 — Admin Review UI

Add a minimal review queue to the existing admin dashboard (or a new `/admin/opportunities` route).

**Minimum viable admin view:**

```
Pending opportunities (3)

[Brand name]  [Product]  [Submitted]  [Actions]
Aura Bora     12-pack    2h ago       [Publish 14d] [Reject]
ILIA Beauty   Mascara    5h ago       [Publish 14d] [Reject]
Byoma         Cleanser   1d ago       [Publish 14d] [Reject]
```

"Publish 14d" button calls `PATCH /api/admin/opportunities/:id/publish` with `{ days_open: 14 }`.

---

## Phase 7 — UpgradeModal: Opportunities Variant

When a free user tries to apply with 0 credits remaining, fire the existing `UpgradeModal` with a new `feature='opportunities'` prop.

**File:** `UpgradeModal.js`

**FIND** the section handling `feature === 'for_you'` (or similar) and add:

```jsx
} else if (feature === 'opportunities') {
  headline = 'Unlock unlimited applications';
  subtext = 'You have used your 3 free applications this month. Pro gives you unlimited access to every open opportunity.';
  ctaText = 'Get Pro — $19/mo';
}
```

---

## Implementation Order

| Priority | Task | Effort |
|----------|------|--------|
| 1 | DB migration | 10 min |
| 2 | `opportunities_routes.py` — brand submit + admin endpoints | 2 hrs |
| 3 | Admin review queue (minimal table UI) | 1 hr |
| 4 | `BrandSubmitPage.js` — public form at `/for-brands` | 3 hrs |
| 5 | `ForYou.js` — sub-tab state and UI (find/replace blocks above) | 30 min |
| 6 | `OpportunitiesTab.js` — creator feed component | 2 hrs |
| 7 | Creator apply endpoint + credit deduction | 1 hr |
| 8 | Email notifications (brand submit confirm, brand on new application, creator on approval) | 1 hr |
| 9 | `UpgradeModal` opportunities variant | 15 min |

**Total estimated: 11 to 12 hours across backend and frontend.**

---

## Launch Checklist

Before going live with the first real brand:

- [ ] DB migration run on production
- [ ] Brand submit form reachable at `/for-brands` without auth
- [ ] Admin can see pending submissions and publish them
- [ ] Published opportunity appears in For You Opportunities tab for matching creators
- [ ] Application deducts 1 credit from pitch pool
- [ ] Apply button shows "Application sent" state after success
- [ ] Brand receives email when creator applies (includes media kit link)
- [ ] Creator receives confirmation email when approved by brand
- [ ] Free user hitting limit sees UpgradeModal with `feature='opportunities'`
- [ ] Spots counter increments correctly on each application
- [ ] Closed/expired opportunities do not appear in creator feed

---

## Seed Data: First 5 Brands to Reach Out To

Post-build, manually create 5 approved opportunities so the tab is not empty on day 1.

Reach out to these brand types (small DTC, active PR programs):
1. **Beija Flor Naturals** — already in conversation, has budget intent
2. Any K-beauty brand with an open PR form in your existing directory
3. A DTC fitness supplement brand (check your Food and Beverage matches)
4. An eco lifestyle brand (Pela Case-type)
5. A nano-creator-friendly skincare brand

For each: send a short email explaining the feature, confirm they want applicants, create the opportunity record manually via admin, publish it.

---

## Notes

- The brand email address collected at submission is never shown to creators. Only the media kit link is shared in the brand notification email.
- Opportunities use the same 3-credit monthly pool as pitches. This is intentional: one shared limit keeps the mental model simple and the upgrade pressure consistent.
- The `closes_at` timestamp is set by admin at publish time (default 14 days). Brand-specified deadlines are stored separately and displayed as context, but `closes_at` controls when the listing stops appearing.
- Do not add a brand-facing dashboard in v1. All brand interaction happens via email. Keep scope minimal.
