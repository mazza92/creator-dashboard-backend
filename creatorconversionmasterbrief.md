# Creator Conversion Master Brief
## Newcollab.co — 0.95% → 2%+ conversion

**Single source of truth for dev.**  
Implement phases in the order listed. Each phase builds on the previous.

---

## Confirmed current state

| Item | Status |
|---|---|
| Niche-first onboarding (4-question flow) | Built |
| For You tab as first screen after signup | Built |
| Pipeline with countdown, pulse signals, health score | Built |
| Credit deducted on send (not on modal open) | Built |
| 3 free contacts/month | Built — keep at 3, do not change |

---

## Why conversion is low

Free users send 3 pitches, cold reply rates are 5–15%, so most get 0 replies and leave thinking the product doesn't work. The app's value delivery depends entirely on brand behavior that Newcollab doesn't control.

Three specific friction points amplify this:

1. When all 3 contacts are used, the **entire pitch interface is replaced** by an upgrade wall. Creator can't even see what they'd be sending — no desire, just frustration.
2. Pitch quality bugs (niche mismatch, generic subject line) reduce reply rates, so the few pitches sent are less likely to convert.
3. No value signal in the 14-day dead zone between pitch and reply. Creator has no reason to return.

---

## Final confirmed creator flow

```
SIGNUP
└── 4-question onboarding: niche / platform / followers / country
      └── [ALREADY BUILT]

FOR YOU TAB — first screen after signup
└── "We found 16 brands that match your profile"
      ├── 2 cards UNLOCKED: brand name, reply rate, category, "Pitch" CTA
      └── 14 cards LOCKED: blurred name, reply rate visible, lock icon
            └── Tap locked card → UpgradeModal (feature="for_you")
                  "You have matched brands waiting"

TAP UNLOCKED BRAND TO PITCH
└── Kit nudge interstitial — shows ONCE if creator has 0 portfolio posts
      ├── "Build my kit (2 min)" → navigates to PortfolioBuilder tab
      └── "Pitch without kit" → opens AIPitchModal

PITCH MODAL (AIPitchModal)
├── Opens for ALL users, no gate, no quota wall on open
├── Header: brand logo · brand name · category · match score chip (e.g. "87% match")
│         NO quota counter in header
├── To: s•••••e@shopbop.com (masked email already shown)
├── Subject: editable — "Fashion content idea — 14.2K Instagram audience"
├── Pitch body: editable textarea (not locked read-only div)
├── Green personalization bar: niche + followers + platform
├── [FREE, credits remaining] "Open in email app · Use 1 contact"
│     └── On click: credit deducted, mailto opens, SUCCESS SCREEN shown
├── [PRO] "Open in email app"
│     └── On click: mailto opens, SUCCESS SCREEN shown
├── [FREE, 0 credits left] "Open in email app · Use 1 contact"
│     └── On click: upgrade overlay appears (pitch still visible behind it)
│           "You have matched brands waiting · Upgrade to Pro — $12/month"
└── Secondary row: "Copy pitch" · "Rewrite pitch" (unlimited, no counter)

POST-SEND SUCCESS SCREEN
├── Pipeline confirmation card: brand logo, name, 14-day reply window
├── Reply rate shown: "Shopbop replies to 42% of pitches"
├── "Media kit attached" if creator has a kit
├── "View my pipeline" CTA
└── Media kit nudge if 0 portfolio posts: "Add a portfolio post to stand out"

WAITING PERIOD — Day 1 to 14
├── Pipeline tab: countdown timer, pulse signal, best move advice [BUILT]
├── Day 7: nudge email "Did [Brand] reply yet?"
└── If kit views exist: dashboard banner "X brands viewed your kit this week"
      ├── [FREE] Tap → UpgradeModal (feature="kit_views") "See which brand"
      └── [PRO] Tap → My Kit tab with view detail

UPGRADE TRIGGERS — all desire-based, none frustration-based
├── All 3 contacts used + tap send → upgrade overlay inside pitch modal
│     Copy: "You have matched brands waiting"
├── Tap locked For You card → UpgradeModal (feature="for_you")
│     Copy: "These brands match your profile. Contact them all."
├── Kit view banner (free) → UpgradeModal (feature="kit_views")
│     Copy: "See which brand viewed your kit"
├── 4th portfolio post attempt → UpgradeModal (feature="portfolio_limit")
│     Copy: "Showcase your full range of work"
└── Day 7 email CTA → app.newcollab.co → upgrade
      Copy: "Send a follow-up. Pro creators get 2x more replies."
```

---

## Phase 1 — Pitch modal fixes + upgrade model
**Priority: Ship first. No new DB tables, no new routes.**
**Detailed blocks in:** `conversion-sprint-brief.md` + `pitch-modal-upgrade-brief.md`

### Block status

| Block | File | Change | Status |
|---|---|---|---|
| ~~1–2~~ | ~~pr_crm_routes.py~~ | ~~FREE_MONTHLY_LIMIT 3→1~~ | **SKIP — keep 3 contacts** |
| 3 | AIPitchModal.js | Remove early return in `initializePitch` when `!canPitch` | **Implement** |
| ~~4~~ | ~~AIPitchModal.js~~ | ~~Move credit deduction to send~~ | **SKIP — already implemented** |
| 5 | AIPitchModal.js | Remove `UpgradePrompt` JSX gate, add `UpgradeOverlay` state | **Implement** |
| 6 | AIPitchModal.js | Fix `getHumanOpeners` — use creator niche, not brand category | **Implement** |
| 7 | AIPitchModal.js | Fix subject line with niche + follower count | **Implement** |
| 8 | AIPitchModal.js | Remove `MAX_REGENERATES` gate, rename to "Rewrite pitch" | **Implement** |
| 9 | AIPitchModal.js | Remove `PitchCounter` from header, add `MatchChip` (cap at 100) | **Implement** |
| 10 | AIPitchModal.js | Update send button label + upgrade overlay styled components | **Implement** |
| 11 | UpgradeModal.js | Update `limit_reached` copy + add `for_you` variant | **Implement** |
| 12 | PRBrandDiscovery.js | Cap match score at 100 in all render locations | **Implement** |

### Pitch quality changes (from `pitch-modal-upgrade-brief.md`)

These blocks implement alongside the above:

- **Editable body** — `PitchTextarea` replacing read-only `EmailBody` div, synced to `editedBody` state
- **Editable subject** — `SubjectInput` replacing static label, synced to `editedSubject` state
- **Post-send success screen** — `pitchSent` state, `SuccessScreen` JSX with pipeline card, reply rate, kit nudge
- **Black send button** — replace purple gradient `SendButton` with flat `#0F0F0F` button consistent with app style

### What this achieves
Creators who hit their contact limit still see their pitch and feel the pull to send it. That emotional state converts at 3–5x the rate of a blank upgrade wall.

---

## Phase 2 — For You matchmaking
**Priority: High. Ships alongside or immediately after Phase 1.**
**Full implementation in:** `for-you-matchmaking-brief.md`

### Summary

**DB migrations:**
```sql
ALTER TABLE brands
  ADD COLUMN IF NOT EXISTS min_followers  INT   DEFAULT 1000,
  ADD COLUMN IF NOT EXISTS max_followers  INT   DEFAULT 500000,
  ADD COLUMN IF NOT EXISTS target_niches  JSONB DEFAULT '[]',
  ADD COLUMN IF NOT EXISTS target_regions JSONB DEFAULT '["US","CA","AU","UK","NZ"]',
  ADD COLUMN IF NOT EXISTS reply_rate     FLOAT DEFAULT 0.15;
```

**Batch enrichment:** run `enrich_brands.py` once — fills `target_niches` and follower ranges for all 2,148 brands from existing `category` field.

**Backend:** `compute_match_score()` + `get_for_you_brands()` + `GET /api/creator/for-you` endpoint. Free tier: first 2 results unlocked, rest locked.

**ForYou.js:** unlocked cards (full info + pitch CTA) + locked cards (blurred name, reply rate visible, lock icon). Locked card tap → `UpgradeModal` with `feature="for_you"`.

### Why this converts
Locked cards showing "42% reply rate — beauty niche" create desire before the creator has used a single contact. This is the pull-based paywall. The upgrade ask becomes "contact the brands that are already matched to you" not "you've run out."

---

## Phase 3 — Kit nudge interstitial
**Priority: High. Ships with Phase 2.**
**Full code below — not in any other brief.**

### Behaviour
- Fires once per creator lifetime (localStorage `nc_kit_nudge_seen`)
- Only if creator has 0 portfolio posts
- Intercepts the pitch CTA on brand cards in `PRBrandDiscovery.js` and `ForYou.js`

### State to add (both files)
```javascript
const [kitNudgeBrand, setKitNudgeBrand] = useState(null);
const [showKitNudge, setShowKitNudge] = useState(false);
```

### Replace pitch open handler (both files)

**FIND** — the function that opens `AIPitchModal` on brand card CTA click:
```javascript
const handlePitchClick = (brand) => {
  setSelectedBrand(brand);
  setShowPitchModal(true);
};
```

**REPLACE WITH:**
```javascript
const handlePitchClick = (brand) => {
  const hasSeenNudge = localStorage.getItem('nc_kit_nudge_seen');
  const hasKit = creatorProfile?.kit_published ||
                 (portfolioPosts && portfolioPosts.length > 0);

  if (!hasKit && !hasSeenNudge) {
    localStorage.setItem('nc_kit_nudge_seen', 'true');
    setKitNudgeBrand(brand);
    setShowKitNudge(true);
  } else {
    setSelectedBrand(brand);
    setShowPitchModal(true);
  }
};

const handleKitNudgeSkip = () => {
  setShowKitNudge(false);
  setSelectedBrand(kitNudgeBrand);
  setShowPitchModal(true);
  setKitNudgeBrand(null);
};

const handleKitNudgeBuild = () => {
  setShowKitNudge(false);
  setKitNudgeBrand(null);
  setActiveTab('my-kit'); // adjust tab key to match your nav
};
```

### JSX — add before closing return tag
```jsx
<AnimatePresence>
  {showKitNudge && (
    <KitNudgeOverlay
      initial={{ opacity: 0 }}
      animate={{ opacity: 1 }}
      exit={{ opacity: 0 }}
      onClick={handleKitNudgeSkip}
    >
      <KitNudgeCard
        initial={{ scale: 0.95, y: 20 }}
        animate={{ scale: 1, y: 0 }}
        exit={{ scale: 0.95, y: 20 }}
        onClick={(e) => e.stopPropagation()}
      >
        <KitNudgeStat>3×</KitNudgeStat>
        <KitNudgeTitle>
          Pitches with a media kit get 3x more replies
        </KitNudgeTitle>
        <KitNudgeSub>
          Build yours in 2 minutes before pitching{' '}
          {kitNudgeBrand?.brand_name || 'this brand'}.
        </KitNudgeSub>
        <KitNudgePrimary onClick={handleKitNudgeBuild}>
          Build my kit
        </KitNudgePrimary>
        <KitNudgeSecondary onClick={handleKitNudgeSkip}>
          Pitch without kit
        </KitNudgeSecondary>
      </KitNudgeCard>
    </KitNudgeOverlay>
  )}
</AnimatePresence>
```

### Styled components
```javascript
const KitNudgeOverlay = styled(motion.div)`
  position: fixed;
  inset: 0;
  background: rgba(0, 0, 0, 0.55);
  backdrop-filter: blur(4px);
  display: flex;
  align-items: flex-end;
  justify-content: center;
  z-index: 9999;
  padding: 0 0 env(safe-area-inset-bottom);

  @media (min-width: 600px) {
    align-items: center;
  }
`;

const KitNudgeCard = styled(motion.div)`
  background: white;
  border-radius: 24px 24px 0 0;
  padding: 32px 24px 40px;
  width: 100%;
  max-width: 480px;
  text-align: center;

  @media (min-width: 600px) {
    border-radius: 24px;
    padding: 40px 32px;
  }
`;

const KitNudgeStat = styled.div`
  font-size: 48px;
  font-weight: 800;
  color: #0F0F0F;
  line-height: 1;
  margin-bottom: 12px;
`;

const KitNudgeTitle = styled.h3`
  font-size: 20px;
  font-weight: 700;
  color: #0F0F0F;
  margin: 0 0 8px;
  line-height: 1.3;
`;

const KitNudgeSub = styled.p`
  font-size: 14px;
  color: #6B7280;
  margin: 0 0 28px;
  line-height: 1.5;
`;

const KitNudgePrimary = styled.button`
  width: 100%;
  padding: 16px;
  background: #0F0F0F;
  color: white;
  border: none;
  border-radius: 14px;
  font-size: 16px;
  font-weight: 600;
  cursor: pointer;
  margin-bottom: 12px;
  transition: opacity 0.2s;
  &:hover { opacity: 0.85; }
`;

const KitNudgeSecondary = styled.button`
  width: 100%;
  padding: 14px;
  background: none;
  color: #6B7280;
  border: none;
  font-size: 14px;
  font-weight: 500;
  cursor: pointer;
  transition: color 0.2s;
  &:hover { color: #0F0F0F; }
`;
```

---

## Phase 4 — Media kit portfolio builder
**Priority: High.**
**Full implementation in:** `media-kit-builder-brief.md`

### Summary
- New DB tables: `portfolio_posts`, `kit_views`
- `ALTER TABLE users`: add `kit_tagline`, `kit_published`, `kit_slug`, rate fields
- 8 Flask endpoints: portfolio CRUD, public kit, view tracking, URL detection
- New component: `PortfolioBuilder.js` — 3 views (dashboard / add / guide)
- New component: `PublicMediaKit.js` — public bento portfolio at `/kit/:username`

### Thumbnail fetching addition (not in existing brief)

Add to `portfolio_routes.py`:

```python
import requests, re

@portfolio.route('/detect-url', methods=['POST'])
def detect_url():
    data = request.get_json()
    url = data.get('url', '').strip()
    if not url:
        return jsonify({'success': False, 'error': 'URL required'}), 400

    result = {'platform': None, 'thumbnail_url': None, 'title': None}

    try:
        if 'tiktok.com' in url:
            r = requests.get(f'https://www.tiktok.com/oembed?url={url}', timeout=5)
            if r.status_code == 200:
                d = r.json()
                result.update({
                    'platform': 'TikTok',
                    'thumbnail_url': d.get('thumbnail_url'),
                    'title': d.get('title'),
                    'author': d.get('author_name')
                })
        elif 'youtube.com' in url or 'youtu.be' in url:
            m = re.search(r'(?:v=|youtu\.be/|embed/)([a-zA-Z0-9_-]{11})', url)
            if m:
                result.update({
                    'platform': 'YouTube',
                    'thumbnail_url': f'https://img.youtube.com/vi/{m.group(1)}/maxresdefault.jpg'
                })
        elif 'instagram.com' in url:
            result['platform'] = 'Instagram'
            # No public thumbnail API — frontend shows manual upload option
    except Exception:
        pass

    return jsonify({'success': True, **result})
```

---

## Phase 5 — Notification triggers
**Priority: Medium. Ships after Phase 4.**

### 5A — Kit view dashboard banner

Fetch `views_this_week` from `GET /api/portfolio/views`. Show banner in dashboard/home screen if > 0.

```jsx
{kitViewsThisWeek > 0 && (
  <KitViewBanner onClick={handleKitViewBannerClick}>
    <KitViewBannerText>
      <strong>{kitViewsThisWeek} brand{kitViewsThisWeek > 1 ? 's' : ''}</strong>
      {' '}viewed your media kit this week
    </KitViewBannerText>
    <KitViewBannerCta>See details →</KitViewBannerCta>
  </KitViewBanner>
)}
```

```javascript
const handleKitViewBannerClick = () => {
  if (creatorTier === 'free') {
    setUpgradeModalFeature('kit_views');
    setShowUpgradeModal(true);
  } else {
    setActiveTab('my-kit');
  }
};
```

```javascript
const KitViewBanner = styled.div`
  background: #F0FDF4;
  border: 1px solid #BBF7D0;
  border-radius: 14px;
  padding: 14px 16px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  cursor: pointer;
  margin-bottom: 16px;
  transition: background 0.2s;
  &:hover { background: #DCFCE7; }
`;

const KitViewBannerText = styled.span`
  font-size: 14px;
  color: #15803D;
`;

const KitViewBannerCta = styled.span`
  font-size: 13px;
  font-weight: 600;
  color: #15803D;
  white-space: nowrap;
`;
```

### 5B — Day 7 follow-up nudge email

**DB migration:**
```sql
ALTER TABLE pr_pipeline
  ADD COLUMN IF NOT EXISTS followup_nudge_sent BOOLEAN DEFAULT FALSE;
```

**Cron job (daily):**
```python
def send_followup_nudges():
    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    cursor.execute('''
        SELECT p.creator_id, p.brand_name, p.pitched_at, c.email, c.username
        FROM pr_pipeline p
        JOIN creators c ON c.id = p.creator_id
        WHERE p.pitch_status = 'waiting'
          AND p.pitched_at <= NOW() - INTERVAL '7 days'
          AND p.pitched_at >= NOW() - INTERVAL '8 days'
          AND p.followup_nudge_sent IS NOT TRUE
          AND c.subscription_tier = 'free'
    ''')
    for pitch in cursor.fetchall():
        send_followup_nudge_email(
            to=pitch['email'],
            creator_name=pitch['username'],
            brand_name=pitch['brand_name']
        )
        cursor.execute('''
            UPDATE pr_pipeline SET followup_nudge_sent = TRUE
            WHERE creator_id = %s AND brand_name = %s AND pitched_at = %s
        ''', (pitch['creator_id'], pitch['brand_name'], pitch['pitched_at']))
    conn.commit()
    cursor.close()
    conn.close()
```

**Email copy:**
```
Subject: Did [BrandName] get back to you?

Hey [username],

It's been a week since you pitched [BrandName]. No reply yet is normal — most brands take 7–14 days.

One thing that consistently helps: a follow-up. Creators who send a second message get 2x more replies.

Log in to send a follow-up → app.newcollab.co

— Newcollab
```

---

## Phase 6 — Activation tracking
**Priority: Low. Add alongside any backend work.**

```sql
ALTER TABLE creators ADD COLUMN IF NOT EXISTS first_pitch_at TIMESTAMPTZ;
```

In `track_pitch` (`pr_crm_routes.py`), after updating `pitches_sent_this_week`:
```python
cursor.execute('''
    UPDATE creators SET first_pitch_at = NOW()
    WHERE id = %s AND first_pitch_at IS NULL
''', (creator_id,))
```

Measure weekly with:
```sql
SELECT
  COUNT(*) AS signups,
  COUNT(first_pitch_at) AS activated,
  ROUND(COUNT(first_pitch_at)::numeric / COUNT(*) * 100, 1) AS activation_pct,
  COUNT(CASE WHEN first_pitch_at <= created_at + INTERVAL '24 hours'
        THEN 1 END) AS activated_same_day
FROM creators
WHERE created_at >= NOW() - INTERVAL '30 days';
```

---

## Upgrade trigger map — all five moments

| Trigger moment | Feature prop | Headline |
|---|---|---|
| 3 contacts used, tap send | `limit_reached` | "You have matched brands waiting" |
| Tap locked For You card | `for_you` | "These brands match your profile" |
| Kit view banner (free) | `kit_views` | "See which brand viewed your kit" |
| 4th portfolio post | `portfolio_limit` | "Showcase your full range of work" |
| Day 7 email CTA | `followup` | "Send a follow-up. 2x reply rate." |

All route to `UpgradeModal.js` with the relevant `feature` prop. No new modals needed.

---

## Sprint map

| Sprint | Phase | Effort |
|---|---|---|
| 1 | Phase 1 — Pitch modal fixes (conversion-sprint-brief.md Blocks 3,5–12 + pitch-modal-upgrade-brief.md) | 2–3 days |
| 1 | Phase 2 — For You matching (for-you-matchmaking-brief.md) | 3–4 days |
| 2 | Phase 3 — Kit nudge interstitial | 0.5 days |
| 2 | Phase 4 — Portfolio builder (media-kit-builder-brief.md + thumbnail endpoint above) | 5–7 days |
| 3 | Phase 5A — Kit view banner | 1 day |
| 3 | Phase 5B — Day 7 email nudge | 1 day |
| 3 | Phase 6 — Activation tracking | 0.5 days |

---

## Master checklist

### Phase 1 — Pitch modal
- [ ] `AIPitchModal.js` — remove early return in `initializePitch` when `!canPitch` (Block 3)
- [ ] `AIPitchModal.js` — remove `UpgradePrompt` JSX branch, add `showUpgradePrompt` state + `UpgradeOverlay` (Block 5)
- [ ] `AIPitchModal.js` — fix `getHumanOpeners` to use creator niche not brand category (Block 6)
- [ ] `AIPitchModal.js` — fix subject line: niche + follower count format (Block 7)
- [ ] `AIPitchModal.js` — remove `MAX_REGENERATES` gate, rename button "Rewrite pitch" (Block 8)
- [ ] `AIPitchModal.js` — remove `PitchCounter`, add `MatchChip` capped at 100 (Block 9)
- [ ] `AIPitchModal.js` — send button: "Use 1 contact" label for free users with credits (Block 10)
- [ ] `AIPitchModal.js` — editable `SubjectInput` + `PitchTextarea` with `editedSubject`/`editedBody` state
- [ ] `AIPitchModal.js` — `pitchSent` state triggers success screen with pipeline card
- [ ] `AIPitchModal.js` — success screen: brand, 14-day window, reply rate, kit nudge
- [ ] `AIPitchModal.js` — black `#0F0F0F` send button (replace purple gradient)
- [ ] `UpgradeModal.js` — update `limit_reached` copy (Block 11)
- [ ] `UpgradeModal.js` — add `for_you` copy variant + route in `copyKey` (Block 11)
- [ ] Match score capped at 100 everywhere it renders (Block 12)

### Phase 2 — For You
- [ ] DB: 5 new columns on `brands` table
- [ ] Run `enrich_brands.py` batch enrichment
- [ ] `compute_match_score()` Python function
- [ ] `get_for_you_brands()` Python function
- [ ] `GET /api/creator/for-you` endpoint (free: 2 unlocked, 18 locked)
- [ ] `ForYou.js` — unlocked cards with pitch CTA
- [ ] `ForYou.js` — locked cards (blurred name, reply rate visible)
- [ ] Locked card tap → `UpgradeModal` with `feature="for_you"`
- [ ] Empty state for < 3 matches

### Phase 3 — Kit nudge
- [ ] `kitNudgeBrand` + `showKitNudge` state in `PRBrandDiscovery.js`
- [ ] `kitNudgeBrand` + `showKitNudge` state in `ForYou.js`
- [ ] `handlePitchClick` gating logic in both files
- [ ] `KitNudge` JSX + styled components in both files
- [ ] "Build my kit" routes to `PortfolioBuilder` tab

### Phase 4 — Portfolio builder
- [ ] DB: `portfolio_posts` table
- [ ] DB: `kit_views` table
- [ ] DB: `ALTER TABLE users` for kit + rate fields
- [ ] 8 Flask endpoints in `portfolio_routes.py`
- [ ] `POST /api/portfolio/detect-url` with TikTok oEmbed + YouTube thumbnail
- [ ] `PortfolioBuilder.js` — 3-view component (dashboard / add / guide)
- [ ] `PublicMediaKit.js` — public bento kit at `/kit/:username`
- [ ] Routing wired in `App.js`

### Phase 5 — Notifications
- [ ] `GET /api/portfolio/views` returns `views_this_week`
- [ ] `KitViewBanner` in dashboard — free users routed to upgrade modal
- [ ] `followup_nudge_sent` column added to `pr_pipeline`
- [ ] Day 7 nudge cron job

### Phase 6 — Tracking
- [ ] `first_pitch_at` column on `creators`
- [ ] `track_pitch` sets `first_pitch_at` on first send
- [ ] Admin activation query saved
