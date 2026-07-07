# Upgrade Modal — Copy and Conversion Brief

**File:** `src/creator-portal/UpgradeModal.js`
**Goal:** Update copy to match all new features (Opportunities tab, real reply rate data, clearer feature framing). Add proof strip. Update context-specific headlines per trigger.

---

## What Changes and Why

| Element | Current | Updated | Reason |
|---------|---------|---------|--------|
| Subtext | Generic one-liner | Context-specific per trigger | Names the exact thing the user just ran out of |
| Feature 1 | "Unlimited brand contacts every month" | "Unlimited pitches to any brand every month" | "Contacts" sounds transactional, "pitches" is the action creators take |
| Feature 2 | "Custom outreach emails written for each brand" | "Apply to brand casting calls" (NEW) | Custom pitch is already free. Opportunities is a new Pro differentiator. |
| Feature 3 | "Direct PR manager contacts for 500+ brands" | "Unlock your top matches with 38 to 55% reply rates" | Uses real data from locked Pro match cards — specific and credible |
| Feature 4 | "Kit view tracking: see which brands checked you out" | Same, slightly tightened | Keep — it's a strong feature |
| Feature 5 | "Full portfolio builder with bento media kit" | "AI pitch written for every brand, direct to their PR inbox" | Portfolio builder is free. The AI pitch quality + delivery method is Pro's core value. |
| Proof strip | None | 3 stat tiles: 40%+ reply rate, ~$45 avg PR value, 30d avg to first PR | Answers the 3 buyer questions before they see the price |
| ROI box | "Most creators land their first gifted package within 30 days. That covers your $12 back in product." | Same message, slight reframe | Keep — high-converting copy. Just tighten. |
| Footer | "Cancel anytime" | "Cancel anytime · Secure checkout · No commitment" | Three micro-trust signals reinforce low risk |

---

## Block 1 — Context-Specific Headlines and Subtext

**File:** `UpgradeModal.js`

**FIND** the section that sets headline/subtext based on `feature` prop (will look something like):
```jsx
let headline = 'You have matched brands waiting.';
let subtext = 'Your free contacts this month have been used. Upgrade to contact all your matches.';
```

**REPLACE WITH:**
```jsx
let headline = 'Your matched brands are waiting.';
let subtext = '3 free pitches used this month. Upgrade to keep pitching.';
let ctaText = 'Get Pro for $12/month';

if (feature === 'opportunities') {
  headline = 'Brands are reviewing applications right now.';
  subtext = '3 free applications used this month. Pro gives you unlimited access.';
  ctaText = 'Unlock unlimited applications';

} else if (feature === 'locked' || feature === 'for_you') {
  headline = 'Your top matches reply 4x more than average.';
  subtext = 'These brands are locked to Pro. They reply to 38 to 55% of pitches.';
  ctaText = 'Unlock Pro matches for $12/month';

} else if (feature === 'kit') {
  headline = 'Creators in your niche are landing PR this month.';
  subtext = 'Pro unlocks your top matches, brand casting calls, and kit view tracking.';
  ctaText = 'Get Pro for $12/month';
}
```

---

## Block 2 — Progress Bar Label

**FIND:**
```jsx
<ProgressLabel>3 / 3 pitches used this month</ProgressLabel>
```

**REPLACE WITH:**
```jsx
<ProgressLabel>
  {feature === 'opportunities'
    ? `${pitchLimits?.used || 3} / ${pitchLimits?.limit || 3} applications used this month`
    : `${pitchLimits?.used || 3} / ${pitchLimits?.limit || 3} pitches used this month`
  }
</ProgressLabel>
```

---

## Block 3 — Add Proof Strip (insert before Pro card)

**FIND** the opening of the Pro card component:
```jsx
<ProCard>
```

**REPLACE WITH:**
```jsx
<ProofStrip>
  <ProofStat>
    <ProofVal>40%+</ProofVal>
    <ProofLbl>Avg reply rate for Pro pitches</ProofLbl>
  </ProofStat>
  <ProofStat>
    <ProofVal>~$45</ProofVal>
    <ProofLbl>Avg PR kit value received</ProofLbl>
  </ProofStat>
  <ProofStat>
    <ProofVal>30d</ProofVal>
    <ProofLbl>Avg time to first PR</ProofLbl>
  </ProofStat>
</ProofStrip>

<ProCard>
```

**Add styled components:**
```js
const ProofStrip = styled.div`
  display: grid;
  grid-template-columns: 1fr 1fr 1fr;
  border: 1px solid #e5e7eb;
  border-radius: 10px;
  overflow: hidden;
  margin-bottom: 14px;
`;

const ProofStat = styled.div`
  padding: 10px 8px;
  text-align: center;
  border-right: 1px solid #e5e7eb;
  &:last-child { border-right: none; }
`;

const ProofVal = styled.div`
  font-size: 16px;
  font-weight: 900;
  color: #0F0F0F;
  margin-bottom: 2px;
`;

const ProofLbl = styled.div`
  font-size: 10px;
  font-weight: 500;
  color: #6b7280;
  line-height: 1.3;
`;
```

---

## Block 4 — Feature List

**FIND** the features array or JSX list (something like):
```jsx
const features = [
  'Unlimited brand contacts every month',
  'Custom outreach emails written for each brand',
  'Direct PR manager contacts for 500+ brands',
  'Kit view tracking: see which brands checked you out',
  'Full portfolio builder with bento media kit',
];
```

**REPLACE WITH:**
```jsx
const features = [
  {
    text: <><strong>Unlimited pitches</strong> to any brand every month</>,
    highlight: false
  },
  {
    text: <><strong>Apply to brand casting calls</strong> — exclusive opportunities posted by brands looking for creators now <NewTag>new</NewTag></>,
    highlight: true
  },
  {
    text: <><strong>Unlock your top matches</strong> with 38 to 55% reply rates</>,
    highlight: false
  },
  {
    text: <><strong>See which brands viewed your kit</strong> and when</>,
    highlight: false
  },
  {
    text: <>AI pitch written for every brand you contact, direct to their PR inbox</>,
    highlight: false
  },
];
```

**Update the feature render:**
```jsx
{features.map((f, i) => (
  <FeatureItem key={i}>
    <FeatureCheck highlight={f.highlight}>
      <CheckIcon />
    </FeatureCheck>
    <FeatureText>{f.text}</FeatureText>
  </FeatureItem>
))}
```

**Add styled components:**
```js
const FeatureCheck = styled.div`
  width: 18px;
  height: 18px;
  border-radius: 50%;
  background: ${p => p.highlight ? '#7C3AED' : '#059669'};
  display: flex;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
  margin-top: 1px;
`;

const NewTag = styled.span`
  background: #7C3AED;
  color: #fff;
  font-size: 9px;
  font-weight: 800;
  padding: 1px 5px;
  border-radius: 6px;
  letter-spacing: .04em;
  text-transform: uppercase;
  margin-left: 4px;
  vertical-align: middle;
`;
```

---

## Block 5 — ROI Box

**FIND:**
```jsx
Most creators land their first gifted package within 30 days. That covers your $12 back in product.
```

**REPLACE WITH:**
```jsx
Most Pro creators receive their first PR package within 30 days.<br />
<strong>That covers your $12 in product.</strong>
```

---

## Block 6 — CTA Button Text

**FIND:**
```jsx
<CtaButton>Unlock Pro for $12/month</CtaButton>
```

**REPLACE WITH:**
```jsx
<CtaButton>{ctaText}</CtaButton>
```

Where `ctaText` is the variable set in Block 1.

---

## Block 7 — Footer Trust Signals

**FIND:**
```jsx
<FooterText>Cancel anytime</FooterText>
```

**REPLACE WITH:**
```jsx
<ModalFooter>
  <FooterItem>
    <LockIcon /> Cancel anytime
  </FooterItem>
  <FooterItem>
    <ShieldIcon /> Secure checkout
  </FooterItem>
  <FooterItem>
    No commitment
  </FooterItem>
</ModalFooter>
```

**Add styled components:**
```js
const ModalFooter = styled.div`
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 16px;
  padding-top: 12px;
  border-top: 1px solid #f3f4f6;
  margin-top: 12px;
`;

const FooterItem = styled.div`
  display: flex;
  align-items: center;
  gap: 4px;
  font-size: 11px;
  color: #9ca3af;
`;
```

---

## Trigger Map — Where Each Feature Fires

| Trigger | Feature prop | Headline |
|---------|-------------|----------|
| Pitch limit reached (send button) | `'limit_reached'` | "Your matched brands are waiting." |
| Opportunity application limit | `'opportunities'` | "Brands are reviewing applications right now." |
| Locked Pro match card click | `'for_you'` | "Your top matches reply 4x more than average." |
| For You upgrade banner | `'for_you'` | "Your top matches reply 4x more than average." |
| Kit nudge interstitial | `'kit'` | "Creators in your niche are landing PR this month." |

Ensure the `UpgradeModal` is called with the correct `feature` prop at each trigger point.

---

## Price Token Note

All copy is written to work at $12/month (current) or $19/month (future). To update across the whole modal, the price only appears in:
- `pro-price` display
- `ctaText` in Block 1
- ROI box (hardcoded "$12")

When raising price, do a single find/replace: `$12` → `$19` in the modal file only. The ROI statement ("covers your subscription for the year") remains true at $19 — one gifted skincare kit at ~$45 still covers 2+ months.

---

## What to Keep (No Changes)

- Lightning bolt icon in gradient circle
- Gradient purple-to-rose progress bar
- Pro card design with gradient border
- Gradient purple-to-rose CTA button
- Overall modal size and padding
