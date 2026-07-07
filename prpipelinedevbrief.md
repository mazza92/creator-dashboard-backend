# NewCollab — PR Pipeline: Full Developer Brief

**Objective:** Transform the current "Saved" bookmark list into a PR Pipeline that becomes the creator's irreplaceable daily tool — and converts free users to Pro at the highest-value moments in their journey.

**Reference UI files:**
- `prpipelinesimple.html` — Pipeline page visual reference
- `newcollab-brand-v5.html` — Design system / tokens
- `newcollab-v5-dev-handoff.md` — Styled-components library

---

## 1. Context & Architecture

### Tech stack (existing)
| Layer | Stack |
|-------|-------|
| Frontend | React CRA — `app.newcollab.co` |
| Styling | styled-components throughout |
| Backend | Python / Flask — single `app.py` + route modules |
| Database | PostgreSQL |
| Email | Custom SMTP + Jinja2 templates, triggered via cron POST endpoints |
| Payments | Stripe Checkout + Customer Portal |
| Auth | Session-based + JWT |

### Files this feature touches
| File | Change type |
|------|------------|
| `src/cra-pages/SavedBrands.js` | Full rewrite — becomes Pipeline page |
| `src/cra-pages/UnifiedBrandDirectory.js` | Minor — update save handler to call new pipeline endpoint |
| `creator-dashboard-backend/pr_crm_routes.py` | Add pipeline stage endpoints |
| `creator-dashboard-backend/email_cron_routes.py` | Add 4 new cron endpoints |
| `creator-dashboard-backend/email_templates/` | Add 4 new Jinja2 email templates |
| DB | Migration: add columns to `saved_brands` table |

### Freemium tiers (existing + new gates)
| Feature | Free (3 contacts/month) | Pro ($12/month) |
|---------|------------------------|-----------------|
| Save brands | ✓ Unlimited | ✓ Unlimited |
| Contact brands | ✓ 3/month | ✓ Unlimited |
| AI pitch email | ✓ (counts toward limit) | ✓ Unlimited |
| Basic pipeline view | ✓ | ✓ |
| Manual stage updates | ✓ | ✓ |
| Day 7 follow-up reminder email | ✓ | ✓ |
| AI follow-up email generator | ✗ **→ Paywall** | ✓ |
| Media Kit auto-attach on follow-up | ✗ **→ Paywall** | ✓ |
| Day 14 + Day 21 retention emails | ✗ | ✓ |
| "$PR Value Earned" dashboard stat | ✗ **→ Paywall** | ✓ |
| Package value logging | ✗ **→ Paywall** | ✓ |

---

## 2. Database Migration

Run this migration. The `saved_brands` table already exists — these are additive columns only.

```sql
ALTER TABLE saved_brands
  ADD COLUMN IF NOT EXISTS pipeline_stage    VARCHAR(20)  NOT NULL DEFAULT 'saved',
  ADD COLUMN IF NOT EXISTS send_confirmed    BOOLEAN      NOT NULL DEFAULT FALSE,
  ADD COLUMN IF NOT EXISTS pitched_at        TIMESTAMP,
  ADD COLUMN IF NOT EXISTS followup_count    INT          NOT NULL DEFAULT 0,
  ADD COLUMN IF NOT EXISTS followup_sent_at  TIMESTAMP,
  ADD COLUMN IF NOT EXISTS replied_at        TIMESTAMP,
  ADD COLUMN IF NOT EXISTS reply_type        VARCHAR(20),
  ADD COLUMN IF NOT EXISTS package_confirmed_at TIMESTAMP,
  ADD COLUMN IF NOT EXISTS package_value     INT          NOT NULL DEFAULT 0,
  ADD COLUMN IF NOT EXISTS expected_delivery DATE,
  ADD COLUMN IF NOT EXISTS received_at       TIMESTAMP,
  ADD COLUMN IF NOT EXISTS notes             TEXT;

-- pipeline_stage enum values:
-- 'saved'       → bookmarked, not yet contacted
-- 'waiting'     → pitch sent and confirmed by user
-- 'followup'    → follow-up sent
-- 'replied'     → creator marked as replied (pending reply_type)
-- 'won'         → package confirmed coming
-- 'received'    → package physically received
-- 'archived'    → no response after 2 follow-ups, or "not a fit"

-- reply_type enum values:
-- 'package_coming' | 'need_info' | 'not_fit' | 'unsure'

CREATE INDEX IF NOT EXISTS idx_saved_brands_stage
  ON saved_brands(user_id, pipeline_stage);

CREATE INDEX IF NOT EXISTS idx_saved_brands_followup
  ON saved_brands(pitched_at)
  WHERE pipeline_stage IN ('waiting', 'followup') AND send_confirmed = TRUE;
```

---

## 3. Backend — API Endpoints

Add all of the following to `pr_crm_routes.py`.

### 3a. GET /api/pipeline

Returns all pipeline items for the authenticated user, grouped by stage with summary stats.

```python
@pr_crm_bp.route('/api/pipeline', methods=['GET'])
@login_required
def get_pipeline():
    user_id = get_current_user_id()

    rows = db.execute("""
        SELECT
            sb.id, sb.pipeline_stage, sb.send_confirmed,
            sb.pitched_at, sb.followup_count, sb.followup_sent_at,
            sb.replied_at, sb.reply_type,
            sb.package_confirmed_at, sb.package_value,
            sb.expected_delivery, sb.received_at, sb.notes,
            sb.created_at AS saved_at,
            b.id AS brand_id, b.brand_name, b.category,
            b.logo_url, b.domain, b.response_rate,
            b.pr_email, b.instagram_handle, b.has_application_form,
            b.application_form_url, b.requires_pro,
            -- days since pitched (for nudge logic in frontend)
            EXTRACT(DAY FROM NOW() - sb.pitched_at)::INT AS days_since_pitched
        FROM saved_brands sb
        JOIN brands b ON b.id = sb.brand_id
        WHERE sb.user_id = %s
          AND sb.pipeline_stage != 'archived'
        ORDER BY
            CASE sb.pipeline_stage
                WHEN 'replied'   THEN 1
                WHEN 'followup'  THEN 2
                WHEN 'waiting'   THEN 3
                WHEN 'won'       THEN 4
                WHEN 'saved'     THEN 5
                WHEN 'received'  THEN 6
            END,
            sb.pitched_at DESC NULLS LAST
    """, (user_id,)).fetchall()

    # Summary stats
    stats = db.execute("""
        SELECT
            COUNT(*) FILTER (WHERE pipeline_stage = 'waiting' OR pipeline_stage = 'followup') AS waiting_count,
            COUNT(*) FILTER (WHERE pipeline_stage IN ('won','received')) AS wins_count,
            COUNT(*) FILTER (WHERE send_confirmed = TRUE) AS total_contacted,
            COALESCE(SUM(package_value) FILTER (WHERE pipeline_stage IN ('won','received')), 0) AS pr_value_earned
        FROM saved_brands
        WHERE user_id = %s AND pipeline_stage != 'archived'
    """, (user_id,)).fetchone()

    return jsonify({
        'items': [dict(r) for r in rows],
        'stats': dict(stats)
    })
```

### 3b. PATCH /api/pipeline/:saved_brand_id

Single endpoint to advance or update any pipeline field. Frontend sends only the fields it wants to change.

```python
@pr_crm_bp.route('/api/pipeline/<int:saved_brand_id>', methods=['PATCH'])
@login_required
def update_pipeline(saved_brand_id):
    user_id = get_current_user_id()
    data    = request.get_json()

    # Security: verify ownership
    row = db.execute(
        "SELECT id FROM saved_brands WHERE id=%s AND user_id=%s",
        (saved_brand_id, user_id)
    ).fetchone()
    if not row:
        return jsonify({'error': 'Not found'}), 404

    allowed_fields = {
        'pipeline_stage', 'send_confirmed', 'pitched_at',
        'followup_count', 'followup_sent_at', 'replied_at',
        'reply_type', 'package_confirmed_at', 'package_value',
        'expected_delivery', 'received_at', 'notes'
    }
    updates = {k: v for k, v in data.items() if k in allowed_fields}
    if not updates:
        return jsonify({'error': 'No valid fields'}), 400

    # Auto-set timestamps based on stage transitions
    stage = updates.get('pipeline_stage')
    if stage == 'waiting' and 'pitched_at' not in updates:
        updates['pitched_at'] = 'NOW()'
    if stage == 'won' and 'package_confirmed_at' not in updates:
        updates['package_confirmed_at'] = 'NOW()'
    if stage == 'received' and 'received_at' not in updates:
        updates['received_at'] = 'NOW()'

    set_clause = ', '.join(f"{k} = %s" for k in updates)
    db.execute(
        f"UPDATE saved_brands SET {set_clause} WHERE id = %s",
        (*updates.values(), saved_brand_id)
    )
    db.commit()

    # If stage → 'received', update brand response_rate (aggregate)
    if stage == 'received':
        _update_brand_response_rate(saved_brand_id)

    return jsonify({'success': True})


def _update_brand_response_rate(saved_brand_id):
    """Recalculate brand response rate from real outcomes."""
    db.execute("""
        UPDATE brands b SET
            response_rate = (
                SELECT ROUND(
                    COUNT(*) FILTER (WHERE pipeline_stage IN ('replied','won','received')) * 100.0
                    / NULLIF(COUNT(*) FILTER (WHERE send_confirmed = TRUE), 0)
                )
                FROM saved_brands
                WHERE brand_id = b.id AND send_confirmed = TRUE
            ),
            responses_received = (
                SELECT COUNT(*) FILTER (WHERE pipeline_stage IN ('replied','won','received'))
                FROM saved_brands WHERE brand_id = b.id
            )
        WHERE b.id = (SELECT brand_id FROM saved_brands WHERE id = %s)
    """, (saved_brand_id,))
    db.commit()
```

### 3c. POST /api/pipeline/:saved_brand_id/confirm-send

Called when creator confirms they sent the email from their native email app.

```python
@pr_crm_bp.route('/api/pipeline/<int:saved_brand_id>/confirm-send', methods=['POST'])
@login_required
def confirm_send(saved_brand_id):
    user_id = get_current_user_id()

    db.execute("""
        UPDATE saved_brands
        SET pipeline_stage  = 'waiting',
            send_confirmed  = TRUE,
            pitched_at      = NOW()
        WHERE id = %s AND user_id = %s
    """, (saved_brand_id, user_id))
    db.commit()

    # Also increment pitches_sent_this_month (existing freemium counter)
    db.execute("""
        UPDATE users SET pitches_sent_this_month = pitches_sent_this_month + 1
        WHERE id = %s
    """, (user_id,))
    db.commit()

    return jsonify({'success': True, 'stage': 'waiting'})
```

### 3d. POST /api/pipeline/:saved_brand_id/log-reply

Called when creator taps one of the 4 reply-type options.

```python
@pr_crm_bp.route('/api/pipeline/<int:saved_brand_id>/log-reply', methods=['POST'])
@login_required
def log_reply(saved_brand_id):
    user_id  = get_current_user_id()
    is_pro   = get_subscription_status(user_id) == 'pro'
    data     = request.get_json()
    reply_type = data.get('reply_type')  # 'package_coming'|'need_info'|'not_fit'|'unsure'

    if reply_type not in ('package_coming', 'need_info', 'not_fit', 'unsure'):
        return jsonify({'error': 'Invalid reply_type'}), 400

    new_stage = {
        'package_coming': 'won',
        'need_info':      'replied',   # stays in replied — needs action
        'not_fit':        'archived',
        'unsure':         'replied',
    }[reply_type]

    db.execute("""
        UPDATE saved_brands
        SET pipeline_stage = %s,
            reply_type     = %s,
            replied_at     = NOW()
        WHERE id = %s AND user_id = %s
    """, (new_stage, reply_type, saved_brand_id, user_id))
    db.commit()

    # Paywall check: package_value logging is Pro only
    # Return flag so frontend can show upgrade prompt if needed
    return jsonify({
        'success':          True,
        'stage':            new_stage,
        'show_kit_prompt':  reply_type == 'need_info',
        'show_value_prompt': reply_type == 'package_coming' and is_pro,
        'show_upgrade':     reply_type == 'package_coming' and not is_pro,
    })
```

### 3e. GET /api/pipeline/stats

Lightweight endpoint for the journey header stats card.

```python
@pr_crm_bp.route('/api/pipeline/stats', methods=['GET'])
@login_required
def get_pipeline_stats():
    user_id = get_current_user_id()
    is_pro  = get_subscription_status(user_id) == 'pro'

    stats = db.execute("""
        SELECT
            COUNT(*) FILTER (WHERE send_confirmed = TRUE)                        AS total_contacted,
            COUNT(*) FILTER (WHERE pipeline_stage IN ('replied','won','received')) AS total_responded,
            COALESCE(SUM(package_value) FILTER (WHERE pipeline_stage IN ('won','received')), 0) AS pr_value_earned
        FROM saved_brands WHERE user_id = %s
    """, (user_id,)).fetchone()

    return jsonify({
        **dict(stats),
        'pr_value_visible': is_pro,  # frontend blurs this stat for free users
    })
```

---

## 4. Backend — Email Cron Endpoints

Add to `email_cron_routes.py`. These are called by your existing cron scheduler.

### 4a. Day 7 — Follow-up Nudge (Free + Pro)

```python
@email_cron_bp.route('/cron/pipeline-followup-nudge', methods=['POST'])
def pipeline_followup_nudge():
    """
    Sends to ALL users (free + pro) who:
    - have a brand in 'waiting' or 'followup' stage
    - pitched exactly 7 days ago
    - have not already received this nudge
    """
    rows = db.execute("""
        SELECT
            sb.id AS saved_brand_id,
            sb.followup_count,
            u.id AS user_id, u.email, u.username,
            b.brand_name, b.response_rate
        FROM saved_brands sb
        JOIN users u  ON u.id  = sb.user_id
        JOIN brands b ON b.id  = sb.brand_id
        WHERE sb.pipeline_stage IN ('waiting', 'followup')
          AND sb.send_confirmed = TRUE
          AND sb.pitched_at::date = (NOW() - INTERVAL '7 days')::date
          AND sb.followup_sent_at IS NULL  -- haven't sent nudge yet
        LIMIT 200
    """).fetchall()

    sent = 0
    for row in rows:
        send_template_email(
            to_email    = row['email'],
            subject     = f"Time to follow up with {row['brand_name']} ⏰",
            template    = 'pipeline_followup_nudge.html',
            context     = {
                'username':    row['username'],
                'brand_name':  row['brand_name'],
                'response_rate': row['response_rate'],
                'pipeline_url': f"https://app.newcollab.co/creator/dashboard/pr-pipeline",
                'followup_count': row['followup_count'],
            }
        )
        # Mark nudge sent — prevent duplicate
        db.execute(
            "UPDATE saved_brands SET followup_sent_at = NOW() WHERE id = %s",
            (row['saved_brand_id'],)
        )
        sent += 1

    db.commit()
    return jsonify({'sent': sent})
```

### 4b. Day 14 — Status Check (Free + Pro)

```python
@email_cron_bp.route('/cron/pipeline-status-check', methods=['POST'])
def pipeline_status_check():
    """
    Sends to users who pitched 14 days ago with no reply logged yet.
    Goal: bring them back to update their pipeline status.
    """
    rows = db.execute("""
        SELECT sb.id, u.email, u.username, b.brand_name
        FROM saved_brands sb
        JOIN users u  ON u.id = sb.user_id
        JOIN brands b ON b.id = sb.brand_id
        WHERE sb.pipeline_stage IN ('waiting', 'followup')
          AND sb.send_confirmed = TRUE
          AND sb.pitched_at::date = (NOW() - INTERVAL '14 days')::date
        LIMIT 200
    """).fetchall()

    sent = 0
    for row in rows:
        send_template_email(
            to_email  = row['email'],
            subject   = f"Did {row['brand_name']} reply? Update your pipeline",
            template  = 'pipeline_status_check.html',
            context   = {
                'username':    row['username'],
                'brand_name':  row['brand_name'],
                'pipeline_url': "https://app.newcollab.co/creator/dashboard/pr-pipeline",
            }
        )
        sent += 1

    db.commit()
    return jsonify({'sent': sent})
```

### 4c. Day 21 — Discover New Brands (Pro only)

```python
@email_cron_bp.route('/cron/pipeline-discover-nudge', methods=['POST'])
def pipeline_discover_nudge():
    """
    Pro only. Sends to users 21 days after pitching with no response.
    Suggests 3 similar brands with higher response rates → drives back to Discover.
    """
    rows = db.execute("""
        SELECT
            sb.id, u.email, u.username,
            b.brand_name, b.category
        FROM saved_brands sb
        JOIN users u  ON u.id = sb.user_id
        JOIN brands b ON b.id = sb.brand_id
        WHERE sb.pipeline_stage IN ('waiting', 'followup')
          AND sb.send_confirmed = TRUE
          AND sb.pitched_at::date = (NOW() - INTERVAL '21 days')::date
          AND u.subscription_status = 'pro'
        LIMIT 200
    """).fetchall()

    sent = 0
    for row in rows:
        # Fetch 3 alternative brands in same category with higher response rate
        alternatives = db.execute("""
            SELECT brand_name, response_rate, slug
            FROM brands
            WHERE category = %s
              AND response_rate > 35
              AND id != (SELECT brand_id FROM saved_brands WHERE id = %s)
            ORDER BY response_rate DESC LIMIT 3
        """, (row['category'], row['id'])).fetchall()

        send_template_email(
            to_email  = row['email'],
            subject   = f"{row['brand_name']} may be slow — here are 3 better options",
            template  = 'pipeline_discover_nudge.html',
            context   = {
                'username':     row['username'],
                'brand_name':   row['brand_name'],
                'alternatives': [dict(a) for a in alternatives],
                'discover_url': "https://app.newcollab.co/creator/dashboard/pr-brands",
            }
        )
        sent += 1

    db.commit()
    return jsonify({'sent': sent})
```

### 4d. Package Arrival Nudge

```python
@email_cron_bp.route('/cron/pipeline-package-nudge', methods=['POST'])
def pipeline_package_nudge():
    """
    Sends when expected_delivery date is reached and stage is still 'won'.
    Invites creator back to mark package as received.
    """
    rows = db.execute("""
        SELECT sb.id, u.email, u.username, b.brand_name
        FROM saved_brands sb
        JOIN users u  ON u.id = sb.user_id
        JOIN brands b ON b.id = sb.brand_id
        WHERE sb.pipeline_stage = 'won'
          AND sb.expected_delivery <= NOW()::date
          AND sb.received_at IS NULL
        LIMIT 200
    """).fetchall()

    sent = 0
    for row in rows:
        send_template_email(
            to_email  = row['email'],
            subject   = f"Your {row['brand_name']} package should be arriving! 📦",
            template  = 'pipeline_package_nudge.html',
            context   = {
                'username':    row['username'],
                'brand_name':  row['brand_name'],
                'pipeline_url': "https://app.newcollab.co/creator/dashboard/pr-pipeline",
            }
        )
        sent += 1

    db.commit()
    return jsonify({'sent': sent})
```

### Cron schedule (add to your scheduler)

```
POST /cron/pipeline-followup-nudge   → daily at 09:00
POST /cron/pipeline-status-check     → daily at 09:00
POST /cron/pipeline-discover-nudge   → daily at 09:00
POST /cron/pipeline-package-nudge    → daily at 09:00
```

---

## 5. Frontend — Complete Flow

### 5a. File: `src/cra-pages/SavedBrands.js`

Full rewrite. Rename the component to `PRPipeline`. Update the route in `App.js` if needed.

### 5b. State

```jsx
const [items, setItems]     = useState([]);
const [stats, setStats]     = useState({});
const [loading, setLoading] = useState(true);
const [activeFilter, setActiveFilter] = useState('all');
const [showUpgradeModal, setShowUpgradeModal] = useState(false);
const [upgradeReason, setUpgradeReason] = useState('');

// On mount
useEffect(() => {
  Promise.all([
    fetch('/api/pipeline').then(r => r.json()),
    fetch('/api/pipeline/stats').then(r => r.json()),
  ]).then(([pipelineData, statsData]) => {
    setItems(pipelineData.items);
    setStats(statsData);
    setLoading(false);
  });
}, []);
```

### 5c. Stage filter helper

```js
const STAGE_FILTERS = [
  { key: 'all',      label: 'All' },
  { key: 'action',   label: '⚡ Action Needed' },  // replied + overdue followup
  { key: 'waiting',  label: '⏳ Waiting' },
  { key: 'won',      label: '🎁 Won' },
  { key: 'saved',    label: '📌 Ready to Contact' },
];

const filteredItems = useMemo(() => {
  if (activeFilter === 'all') return items;
  if (activeFilter === 'action') {
    return items.filter(i =>
      i.pipeline_stage === 'replied' ||
      (i.pipeline_stage === 'waiting' && i.days_since_pitched >= 7)
    );
  }
  return items.filter(i => i.pipeline_stage === activeFilter);
}, [items, activeFilter]);

// Nudge items: overdue follow-ups surfaced at top
const nudgeItems = items.filter(i =>
  i.pipeline_stage === 'waiting' &&
  i.days_since_pitched >= 7 &&
  i.send_confirmed
);
```

### 5d. Stage advancement helper

```js
const advanceStage = async (itemId, updates) => {
  // Optimistic update
  setItems(prev => prev.map(i =>
    i.id === itemId ? { ...i, ...updates } : i
  ));
  try {
    await fetch(`/api/pipeline/${itemId}`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(updates),
    });
  } catch {
    // Revert on error
    setItems(prev => prev.map(i =>
      i.id === itemId ? { ...i } : i  // refetch to revert
    ));
  }
};
```

### 5e. Full 8-step flow per card

**Step 1 — Save from Discover (in `UnifiedBrandDirectory.js`)**

```jsx
// In handleSaveBrand:
const handleSaveBrand = async (brand) => {
  await fetch('/api/save-brand', {
    method: 'POST',
    body: JSON.stringify({ brand_id: brand.id }),
  });
  // Stage defaults to 'saved' in DB
  showToast(`${brand.brand_name} added to your pipeline ✓`);
};
```

**Step 2 — Contact button → AI email → mailto (existing flow)**

The existing AI pitch flow already opens the email generator and builds the `mailto:` link. Only change needed: after the mailto launches, show the confirmation dialog.

```jsx
// After mailto: link is clicked, show confirmation
const handleMailtoLaunched = (item) => {
  setConfirmingItem(item);  // triggers the modal below
};
```

**Step 3 — "Did you send it?" confirmation modal**

```jsx
function SendConfirmModal({ item, onConfirm, onDismiss }) {
  return (
    <Modal>
      <ModalIcon>📧</ModalIcon>
      <ModalTitle>Did you send the email?</ModalTitle>
      <ModalSub>
        We'll remind you to follow up at the right time — but only if you confirm you sent it.
      </ModalSub>
      <PrimaryBtn onClick={async () => {
        await fetch(`/api/pipeline/${item.id}/confirm-send`, { method: 'POST' });
        advanceStage(item.id, { pipeline_stage: 'waiting', send_confirmed: true });
        onConfirm();
      }}>
        ✓ Yes, I sent it
      </PrimaryBtn>
      <SecondaryBtn onClick={onDismiss}>Not yet — I'll send later</SecondaryBtn>
      <ModalHint>You'll get a reminder in 7 days to follow up</ModalHint>
    </Modal>
  );
}
```

**Step 4 — Waiting state card**

```jsx
function WaitingCard({ item, isPro, onReply, onFollowup, onRemove }) {
  const isOverdue = item.days_since_pitched >= 7;

  return (
    <BrandCard>
      <CardTop>
        <BrandLogo brand={item} />
        <CardInfo>
          <CardName>{item.brand_name}</CardName>
          <CardMeta>{item.category} · Pitched {formatDate(item.pitched_at)}</CardMeta>
        </CardInfo>
        <StatusBadge $overdue={isOverdue}>
          {isOverdue ? `⚠ ${item.days_since_pitched}d` : '📧 Waiting'}
        </StatusBadge>
      </CardTop>

      <InfoRow>
        {isOverdue
          ? <InfoPill $warn>⚠ Follow-up overdue</InfoPill>
          : <InfoPill>Brands usually reply in ~7 days</InfoPill>}
        {item.response_rate && (
          <InfoPill $green={item.response_rate >= 40}>
            {item.response_rate}% response rate
          </InfoPill>
        )}
      </InfoRow>

      {isOverdue ? (
        <PrimaryBtn $followup onClick={() => onFollowup(item)}>
          🔄 Send Follow-up
        </PrimaryBtn>
      ) : (
        <PrimaryBtn $success onClick={() => onReply(item)}>
          💬 They Replied!
        </PrimaryBtn>
      )}

      <SecondaryRow>
        <SecondaryBtn onClick={() => onReply(item)}>
          {isOverdue ? '💬 They Replied' : '✏ Add Note'}
        </SecondaryBtn>
        <RemoveBtn onClick={() => onRemove(item)}>×</RemoveBtn>
      </SecondaryRow>
    </BrandCard>
  );
}
```

**Step 5 — Follow-up: AI generator + Media Kit gate (paywall)**

```jsx
const handleFollowup = (item) => {
  if (!isPro) {
    // PAYWALL — AI follow-up + media kit is Pro only
    setUpgradeReason('followup');
    setShowUpgradeModal(true);
    return;
  }
  openFollowupGenerator(item);  // opens existing AI email modal with follow-up template
};
```

**In UpgradeModal — follow-up reason copy:**

```jsx
const UPGRADE_COPY = {
  followup: {
    headline: "Double your reply rate with a Pro follow-up",
    sub: "Get an AI-written follow-up email with your Media Kit auto-attached — the combo that gets brands to actually respond.",
    features: [
      "AI follow-up email personalised to your pitch",
      "Media Kit auto-attached as a link",
      "Unlimited contacts every month",
    ]
  },
  pr_value: {
    headline: "Track your PR value with Pro",
    sub: "See exactly how much free product you've earned through NewCollab — and never lose track of a collab again.",
    features: [
      "$PR Value Earned dashboard",
      "Package value logging",
      "Full pipeline history",
    ]
  },
  // ... other reasons
};
```

**Step 6 — "They Replied!" modal with 4 options**

```jsx
function ReplyModal({ item, onClose }) {
  const options = [
    { key: 'package_coming', emoji: '📦', label: 'Package is coming!',    sub: 'They confirmed sending you a PR package' },
    { key: 'need_info',      emoji: '❓', label: 'They need more info',   sub: "They asked for your media kit or stats" },
    { key: 'not_fit',        emoji: '❌', label: 'Not a fit right now',   sub: "They declined or it wasn't the right time" },
    { key: 'unsure',         emoji: '🤷', label: "Not sure yet",          sub: "Still in conversation — mark later" },
  ];

  const handleSelect = async (option) => {
    const res = await fetch(`/api/pipeline/${item.id}/log-reply`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ reply_type: option.key }),
    }).then(r => r.json());

    if (option.key === 'package_coming') {
      if (res.show_upgrade) {
        setUpgradeReason('pr_value');
        setShowUpgradeModal(true);
      } else {
        showPackageValuePrompt(item);  // Pro: ask for estimated value
      }
    }
    if (option.key === 'need_info') {
      showKitPrompt(item);  // prompt to send media kit
    }
    if (option.key === 'not_fit') {
      showToast('Archived — want to try a similar brand?', { action: 'Browse brands', href: '/creator/dashboard/pr-brands' });
    }
    onClose();
  };

  return (
    <Modal>
      <ModalTitle>What did {item.brand_name} say?</ModalTitle>
      {options.map(opt => (
        <ReplyOption key={opt.key} onClick={() => handleSelect(opt)}>
          <span>{opt.emoji}</span>
          <div>
            <div>{opt.label}</div>
            <small>{opt.sub}</small>
          </div>
        </ReplyOption>
      ))}
    </Modal>
  );
}
```

**Step 7 — Won card**

```jsx
function WonCard({ item, isPro, onReceived, onRemove }) {
  return (
    <WinCard>
      <CardTop>
        <BrandLogo brand={item} />
        <CardInfo>
          <CardName>{item.brand_name}</CardName>
          <CardMeta>Package confirmed ✓</CardMeta>
        </CardInfo>
        {isPro && item.package_value > 0
          ? <PackageValue>🎁 ~${item.package_value}</PackageValue>
          : isPro
            ? <PackageValueBtn onClick={() => promptPackageValue(item)}>+ Add value</PackageValueBtn>
            : <ProBlurValue onClick={() => { setUpgradeReason('pr_value'); setShowUpgradeModal(true); }}>
                🎁 ~$?? <LockIcon />
              </ProBlurValue>
        }
      </CardTop>
      <PrimaryBtn $rose onClick={() => onReceived(item)}>
        ✅ Mark as Received
      </PrimaryBtn>
      <SecondaryRow>
        <SecondaryBtn>📝 Add content note</SecondaryBtn>
        <RemoveBtn onClick={() => onRemove(item)}>×</RemoveBtn>
      </SecondaryRow>
    </WinCard>
  );
}
```

**Step 8 — Received: celebration + share (paywall on value)**

```jsx
function ReceivedCelebration({ item, isPro, prValueTotal }) {
  return (
    <CelebrationModal>
      <CelebEmoji>🎉</CelebEmoji>
      <CelebTitle>You landed a collab with {item.brand_name}!</CelebTitle>

      {isPro ? (
        <CelebStat>
          You've earned <strong>${prValueTotal} in PR value</strong> through NewCollab
        </CelebStat>
      ) : (
        <CelebUpgrade onClick={() => { setUpgradeReason('pr_value'); setShowUpgradeModal(true); }}>
          Upgrade to Pro to track your total PR value earned →
        </CelebUpgrade>
      )}

      <PrimaryBtn onClick={() => navigate('/creator/dashboard/pr-brands')}>
        🔍 Find Your Next Brand
      </PrimaryBtn>
      <SecondaryBtn onClick={openShareSheet}>
        📲 Share your win
      </SecondaryBtn>
    </CelebrationModal>
  );
}
```

### 5f. Journey Header (stats card)

```jsx
function JourneyHeader({ stats, isPro }) {
  return (
    <JourneyCard>
      <JourneyTitle>Your PR Journey ✨</JourneyTitle>
      <JourneySub>
        {stats.total_contacted === 0
          ? "Contact your first brand to get started"
          : "Keep going — most creators land their first package within 3 pitches"}
      </JourneySub>
      <StatsRow>
        <Stat value={stats.total_contacted} label="Contacted" />
        <Stat value={stats.total_responded} label="Responded" color="green" />
        <Stat
          value={isPro ? `$${stats.pr_value_earned}` : '$??'}
          label="PR Value"
          color="purple"
          locked={!isPro}
          onLockedClick={() => { setUpgradeReason('pr_value'); setShowUpgradeModal(true); }}
        />
      </StatsRow>
    </JourneyCard>
  );
}
```

---

## 6. Paywall Gates — Summary

| Moment | Trigger | Copy |
|--------|---------|------|
| **Follow-up generator** | Free user taps "Send Follow-up" | *"Double your reply rate — AI follow-up + Media Kit is Pro"* |
| **Package value log** | Free user taps package_coming | *"Track your PR value with Pro"* |
| **$PR Value stat** | Free user sees blurred stat | *"Upgrade to see your total PR value earned"* |
| **Day 14 + 21 email** | Automatic — Pro only receives these | N/A (backend filter) |

All paywall gates route to `UpgradeModal` with `upgradeReason` prop for contextual copy (see section 5e).

The **highest-converting paywall moment** is Step 6 when a brand replies and the creator wants to log "Package coming" — they are at peak emotional excitement. That is when to show the `pr_value` upgrade prompt.

---

## 7. Email Templates Required

Create these 4 Jinja2 templates in `email_templates/`:

### `pipeline_followup_nudge.html`
```
Subject: Time to follow up with {{ brand_name }} ⏰

Hi {{ username }},

You pitched {{ brand_name }} 7 days ago. Brands that receive a 
follow-up are 2× more likely to respond.

[Open your pipeline and send a follow-up →]

{{ brand_name }} has a {{ response_rate }}% response rate — 
worth the nudge.
```

### `pipeline_status_check.html`
```
Subject: Did {{ brand_name }} reply? Update your pipeline

Hi {{ username }},

It's been 2 weeks since you pitched {{ brand_name }}.
Did they get back to you?

[Update your pipeline →]

Tap "They Replied" or "Send Follow-up" to keep your 
pipeline accurate.
```

### `pipeline_discover_nudge.html` (Pro only)
```
Subject: {{ brand_name }} may be slow — here are 3 better options

Hi {{ username }},

It's been 3 weeks since your {{ brand_name }} pitch. 
Some brands are just slow — but here are 3 in the same 
category with higher response rates:

{% for brand in alternatives %}
• {{ brand.brand_name }} — {{ brand.response_rate }}% response rate
{% endfor %}

[Discover these brands →]
```

### `pipeline_package_nudge.html`
```
Subject: Your {{ brand_name }} package should be arriving! 📦

Hi {{ username }},

Your collab with {{ brand_name }} was confirmed and your 
package should be arriving around now.

[Mark as received in your pipeline →]

Don't forget to log it — your PR value stat depends on it!
```

---

## 8. Dev Checklist

**Database**
- [ ] Run migration (section 2)
- [ ] Verify `saved_brands` table has all new columns
- [ ] Verify index on `(user_id, pipeline_stage)`

**Backend**
- [ ] Add `GET /api/pipeline` endpoint
- [ ] Add `PATCH /api/pipeline/:id` endpoint
- [ ] Add `POST /api/pipeline/:id/confirm-send` endpoint
- [ ] Add `POST /api/pipeline/:id/log-reply` endpoint
- [ ] Add `GET /api/pipeline/stats` endpoint
- [ ] Add `_update_brand_response_rate()` helper (fires on 'received')
- [ ] Add 4 cron endpoints to `email_cron_routes.py`
- [ ] Add 4 Jinja2 email templates
- [ ] Register cron endpoints in scheduler (daily 09:00)
- [ ] Confirm `get_subscription_status()` is importable in `pr_crm_routes.py`

**Frontend — `SavedBrands.js` (rewrite)**
- [ ] Rename component to `PRPipeline`
- [ ] Journey header with 3 stats (`$PR Value` blurred for free users)
- [ ] Stage filter tabs
- [ ] Nudge banner for overdue follow-ups (days_since_pitched >= 7)
- [ ] Cards render correctly per stage (saved / waiting / won / received)
- [ ] "Did you send it?" confirmation modal after mailto launch (Step 3)
- [ ] Overdue card shows "Send Follow-up" → paywall for free users
- [ ] "They Replied!" modal with 4 options (Step 6)
- [ ] "Package Coming" → `pr_value` paywall for free users
- [ ] Won card: package value visible Pro / blurred Free
- [ ] Received: celebration modal + share button + "find next brand" CTA
- [ ] `UpgradeModal` wired with `upgradeReason` prop and contextual copy

**Frontend — `UnifiedBrandDirectory.js` (minor)**
- [ ] `handleSaveBrand` calls `/api/save-brand` (already exists, no change if working)
- [ ] After save, show toast: *"Added to your pipeline → Contact now or later"*

**QA**
- [ ] Test full 8-step flow as free user — confirm all paywalls trigger
- [ ] Test full 8-step flow as Pro user — confirm no false paywalls
- [ ] Test cron endpoints manually via POST before scheduling
- [ ] Confirm `pitches_sent_this_month` increments on `confirm-send` (not on mailto click)
- [ ] Confirm brand `response_rate` recalculates when stage → 'received'
