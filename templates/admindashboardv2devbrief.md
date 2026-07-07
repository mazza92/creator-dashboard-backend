# Admin KPI Dashboard v2 — Dev Brief

## Overview

Replace the existing admin dashboard with a 5-tab KPI dashboard that gives real-time visibility into user activation, pipeline health, and monetization. The preview is at `/home/user/admin-dashboard-v2.html`.

**Principle:** Every number must be queryable from existing tables. No new tracking tables required for Phase 1 — all KPIs derive from `users`, `saved_brands`, `ai_generations`, and `stripe_subscriptions` (or equivalent).

---

## 1. Architecture

| Layer | Location | Notes |
|---|---|---|
| Backend | `admin_routes.py` (or `routes/admin.py`) | All new endpoints prefixed `/api/admin/` |
| Frontend | `src/pages/AdminDashboard.js` | Replace existing component entirely |
| Auth guard | Existing `@admin_required` decorator | Keep as-is |

---

## 2. Database Queries — All KPIs

### 2.1 North Star Strip

```sql
-- MRR: sum active Pro subscriptions
SELECT COUNT(*) as pro_count,
       COUNT(*) * 12 as mrr
FROM users
WHERE subscription_status = 'active'
  AND subscription_tier = 'pro';

-- Free → Pro conversion rate
SELECT
  (SELECT COUNT(*) FROM users WHERE subscription_tier = 'pro') * 100.0
  / NULLIF((SELECT COUNT(*) FROM users), 0) AS conversion_rate;

-- Active creators last 30 days (any meaningful action)
SELECT COUNT(DISTINCT user_id) as active_30d
FROM (
  SELECT user_id FROM saved_brands WHERE created_at > NOW() - INTERVAL '30 days'
  UNION
  SELECT user_id FROM ai_generations WHERE created_at > NOW() - INTERVAL '30 days'
) AS activity;

-- Total pitches lifetime
SELECT COUNT(*) FROM saved_brands WHERE pipeline_stage != 'saved';
-- OR: SELECT COUNT(*) FROM ai_generations WHERE type = 'pitch';
-- Use whichever table tracks sent pitches in your schema
```

### 2.2 Daily Pulse (Today's stats)

```sql
-- New signups today
SELECT COUNT(*) FROM users
WHERE DATE(created_at) = CURRENT_DATE;

-- Active users today (any action)
SELECT COUNT(DISTINCT user_id) FROM (
  SELECT user_id FROM saved_brands WHERE DATE(updated_at) = CURRENT_DATE
  UNION
  SELECT user_id FROM ai_generations WHERE DATE(created_at) = CURRENT_DATE
) AS today_activity;

-- AI pitches generated today
SELECT COUNT(*) FROM ai_generations
WHERE DATE(created_at) = CURRENT_DATE
  AND type = 'pitch';  -- adjust column/value to match your schema

-- New Pro subs today
SELECT COUNT(*) FROM users
WHERE subscription_tier = 'pro'
  AND DATE(subscription_started_at) = CURRENT_DATE;

-- Pipeline saves today
SELECT COUNT(*) FROM saved_brands
WHERE DATE(created_at) = CURRENT_DATE;

-- Unique users who used AI contact today (pitch users)
SELECT COUNT(DISTINCT user_id) FROM ai_generations
WHERE DATE(created_at) = CURRENT_DATE;

-- Free users at monthly pitch limit today (3 pitches this month)
SELECT COUNT(*) FROM (
  SELECT user_id, COUNT(*) as pitch_count
  FROM saved_brands
  WHERE pipeline_stage != 'saved'
    AND DATE_TRUNC('month', pitched_at) = DATE_TRUNC('month', CURRENT_DATE)
  GROUP BY user_id
  HAVING COUNT(*) >= 3
) AS at_limit
JOIN users u ON u.id = at_limit.user_id
WHERE u.subscription_tier = 'free';

-- Free users near limit (2 of 3 used this month)
SELECT COUNT(*) FROM (
  SELECT user_id, COUNT(*) as pitch_count
  FROM saved_brands
  WHERE pipeline_stage != 'saved'
    AND DATE_TRUNC('month', pitched_at) = DATE_TRUNC('month', CURRENT_DATE)
  GROUP BY user_id
  HAVING COUNT(*) = 2
) AS near_limit
JOIN users u ON u.id = near_limit.user_id
WHERE u.subscription_tier = 'free';
```

### 2.3 At-Limit User List (conversion priority)

```sql
SELECT
  u.id,
  u.username,
  u.email,
  u.follower_count,
  u.niche,
  COUNT(sb.id) as pitches_this_month
FROM users u
JOIN saved_brands sb ON sb.user_id = u.id
WHERE u.subscription_tier = 'free'
  AND sb.pipeline_stage != 'saved'
  AND DATE_TRUNC('month', sb.pitched_at) = DATE_TRUNC('month', CURRENT_DATE)
GROUP BY u.id, u.username, u.email, u.follower_count, u.niche
HAVING COUNT(sb.id) >= 3
ORDER BY COUNT(sb.id) DESC, u.follower_count DESC
LIMIT 20;
```

### 2.4 Top Pitched Brands Today

```sql
SELECT
  b.id,
  b.name,
  b.category,
  COUNT(sb.id) as pitches_today,
  b.response_rate
FROM brands b
JOIN saved_brands sb ON sb.brand_id = b.id
WHERE sb.pipeline_stage != 'saved'
  AND DATE(sb.pitched_at) = CURRENT_DATE
GROUP BY b.id, b.name, b.category, b.response_rate
ORDER BY pitches_today DESC
LIMIT 10;
```

### 2.5 Most Active Users Today

```sql
SELECT
  u.id,
  u.username,
  u.email,
  u.subscription_tier,
  u.follower_count,
  -- pitches today
  (SELECT COUNT(*) FROM saved_brands
   WHERE user_id = u.id
     AND pipeline_stage != 'saved'
     AND DATE(pitched_at) = CURRENT_DATE) as pitches_today,
  -- pitches this month
  (SELECT COUNT(*) FROM saved_brands
   WHERE user_id = u.id
     AND pipeline_stage != 'saved'
     AND DATE_TRUNC('month', pitched_at) = DATE_TRUNC('month', CURRENT_DATE)) as pitches_month,
  -- total pipeline saves
  (SELECT COUNT(*) FROM saved_brands WHERE user_id = u.id) as pipeline_saves
FROM users u
WHERE u.id IN (
  SELECT DISTINCT user_id FROM saved_brands WHERE DATE(updated_at) = CURRENT_DATE
  UNION
  SELECT DISTINCT user_id FROM ai_generations WHERE DATE(created_at) = CURRENT_DATE
)
ORDER BY pitches_today DESC, pipeline_saves DESC
LIMIT 20;
```

### 2.6 Growth Funnel

```sql
-- Step 1: Total signups
SELECT COUNT(*) FROM users;

-- Step 2: Saved ≥1 brand
SELECT COUNT(DISTINCT user_id) FROM saved_brands;

-- Step 3: Sent first pitch (pipeline_stage left 'saved')
SELECT COUNT(DISTINCT user_id) FROM saved_brands
WHERE pipeline_stage != 'saved';

-- Step 4: Pitched ≥2 brands
SELECT COUNT(*) FROM (
  SELECT user_id FROM saved_brands
  WHERE pipeline_stage != 'saved'
  GROUP BY user_id
  HAVING COUNT(*) >= 2
) AS multi_pitched;

-- Step 5: Package received
SELECT COUNT(DISTINCT user_id) FROM saved_brands
WHERE pipeline_stage IN ('won', 'received');
```

### 2.7 Feature Adoption (30 days)

```sql
-- Discover: users who viewed/saved a brand in last 30 days
SELECT COUNT(DISTINCT user_id) FROM saved_brands
WHERE created_at > NOW() - INTERVAL '30 days';

-- Pipeline: same as saved (it IS the pipeline)

-- AI Pitch Generator: users who generated a pitch in last 30 days
SELECT COUNT(DISTINCT user_id) FROM ai_generations
WHERE created_at > NOW() - INTERVAL '30 days'
  AND type = 'pitch';

-- For You tab: add event tracking (see Section 7 below)
-- My Kit: add event tracking (see Section 7 below)
```

### 2.8 Pipeline Stage Distribution

```sql
SELECT
  pipeline_stage,
  COUNT(*) as count
FROM saved_brands
GROUP BY pipeline_stage
ORDER BY count DESC;
```

### 2.9 Monetization Snapshot

```sql
-- MRR + pro count: reuse from North Star query above

-- At limit this month (cumulative, not just today)
SELECT COUNT(*) FROM (
  SELECT user_id FROM saved_brands
  WHERE pipeline_stage != 'saved'
    AND DATE_TRUNC('month', pitched_at) = DATE_TRUNC('month', CURRENT_DATE)
  GROUP BY user_id
  HAVING COUNT(*) >= 3
) AS at_limit
JOIN users u ON u.id = at_limit.user_id
WHERE u.subscription_tier = 'free';

-- Near limit this month
SELECT COUNT(*) FROM (
  SELECT user_id FROM saved_brands
  WHERE pipeline_stage != 'saved'
    AND DATE_TRUNC('month', pitched_at) = DATE_TRUNC('month', CURRENT_DATE)
  GROUP BY user_id
  HAVING COUNT(*) = 2
) AS near_limit
JOIN users u ON u.id = near_limit.user_id
WHERE u.subscription_tier = 'free';
```

---

## 3. Backend API Endpoints

Add all of these to `admin_routes.py`. Each is protected by `@admin_required`.

### `GET /api/admin/dashboard`

Returns all data for the Today tab in a single call to minimize requests on load.

```python
@app.route('/api/admin/dashboard', methods=['GET'])
@admin_required
def admin_dashboard():
    today = date.today()
    month_start = today.replace(day=1)

    # Run all queries and return combined JSON
    return jsonify({
        "north_star": {
            "mrr": ...,
            "pro_count": ...,
            "conversion_rate": ...,
            "active_30d": ...,
            "total_pitches": ...
        },
        "daily_pulse": {
            "new_signups_today": ...,
            "active_users_today": ...,
            "ai_pitches_today": ...,
            "new_pro_today": ...,
            "pipeline_saves_today": ...,
            "pitch_users_today": ...,
            "at_limit_today": ...,
            "near_limit_today": ...
        },
        "at_limit_users": [...],        # list of user objects
        "top_brands_today": [...],      # list of brand objects
        "active_users_today": [...]     # list of user objects
    })
```

### `GET /api/admin/funnel`

```python
@app.route('/api/admin/funnel', methods=['GET'])
@admin_required
def admin_funnel():
    return jsonify({
        "signups": ...,
        "saved_brand": ...,
        "sent_first_pitch": ...,
        "pitched_multi": ...,
        "package_received": ...,
        "feature_adoption": {
            "discover": ...,
            "pipeline": ...,
            "ai_pitch": ...,
            "for_you": ...,   # 0 until tracking added
            "my_kit": ...     # 0 until tracking added
        }
    })
```

### `GET /api/admin/pipeline-health`

```python
@app.route('/api/admin/pipeline-health', methods=['GET'])
@admin_required
def admin_pipeline_health():
    return jsonify({
        "stage_distribution": {
            "saved": ...,
            "waiting": ...,
            "followup": ...,
            "replied": ...,
            "won": ...,
            "received": ...
        },
        "response_rate_overall": ...,   # replied / waiting * 100
        "avg_days_to_reply": ...,       # AVG(replied_at - pitched_at) in days
        "followup_overdue": ...         # waiting > 7 days with no follow-up
    })
```

### `GET /api/admin/monetization`

```python
@app.route('/api/admin/monetization', methods=['GET'])
@admin_required
def admin_monetization():
    return jsonify({
        "mrr": ...,
        "pro_count": ...,
        "at_limit_month": ...,
        "near_limit_month": ...,
        "conversion_rate": ...,
        "at_limit_list": [...]   # same query as dashboard but full month
    })
```

### `GET /api/admin/users`

```python
@app.route('/api/admin/users', methods=['GET'])
@admin_required
def admin_users():
    page = request.args.get('page', 1, type=int)
    per_page = 50
    tier = request.args.get('tier')        # 'free' | 'pro' | None
    sort = request.args.get('sort', 'created_at')

    # Return paginated user list with pitch counts and pipeline stats
    return jsonify({
        "users": [...],
        "total": ...,
        "page": page
    })
```

### `POST /api/admin/nudge/:user_id`

Trigger the existing upgrade nudge email for a specific user.

```python
@app.route('/api/admin/nudge/<int:user_id>', methods=['POST'])
@admin_required
def admin_nudge_user(user_id):
    user = User.query.get_or_404(user_id)
    # Call existing email send function
    send_upgrade_nudge_email(user)
    return jsonify({"ok": True})
```

---

## 4. Frontend — React Component

### 4.1 File to Replace

`src/pages/AdminDashboard.js` (or wherever the current admin dashboard lives — find with `grep -r "AdminDashboard" src/`)

Replace the entire component. Do not keep old code.

### 4.2 Component Structure

```
AdminDashboard.js
  ├── Header (sticky, logo + date picker + refresh + export)
  ├── TabBar (sticky below header)
  └── TabContent (conditional render by activeTab)
        ├── TodayTab
        │     ├── AlertStrip (at-limit count)
        │     ├── NorthStarStrip (5 metrics)
        │     ├── DailyPulseGrid (8 stat cards with sparklines)
        │     ├── AtLimitTable (with "Send nudge →" per row)
        │     ├── TopBrandsTable
        │     └── ActiveUsersTable
        ├── GrowthFunnelTab
        │     ├── TrafficNotice
        │     ├── FunnelCard (5 steps with horizontal bars)
        │     └── FeatureAdoptionCard + PipelineStagesCard
        ├── PipelineHealthTab
        │     └── Stage cards + response rate metrics
        ├── MonetizationTab
        │     └── 4 stat cards + at-limit user list
        └── UsersTab
              └── Paginated users table with filters
```

### 4.3 Data Fetching

```javascript
// In AdminDashboard.js
const [activeTab, setActiveTab] = useState('today');
const [dashboardData, setDashboardData] = useState(null);
const [funnelData, setFunnelData] = useState(null);

// Load Today data on mount
useEffect(() => {
  fetch('/api/admin/dashboard')
    .then(r => r.json())
    .then(setDashboardData);
}, []);

// Load tab data lazily when tab switches
useEffect(() => {
  if (activeTab === 'funnel' && !funnelData) {
    fetch('/api/admin/funnel').then(r => r.json()).then(setFunnelData);
  }
  // similar for pipeline, monetization, users tabs
}, [activeTab]);
```

### 4.4 Tab Definitions

```javascript
const TABS = [
  { id: 'today',        label: 'Today',           icon: '📊' },
  { id: 'funnel',       label: 'Growth Funnel',   icon: '📈' },
  { id: 'pipeline',     label: 'Pipeline Health', icon: '🔄' },
  { id: 'monetization', label: 'Monetization',    icon: '💰' },
  { id: 'users',        label: 'Users',           icon: '👥' },
];
```

### 4.5 Sparkline Component

The CSS sparklines are the simplest approach — no chart library needed.

```javascript
// SparkBar: pass array of 7 values (last 7 days), auto-normalizes height
const SparkLine = ({ data = [], color = 'var(--action)' }) => {
  const max = Math.max(...data, 1);
  return (
    <div style={{ display:'flex', alignItems:'flex-end', gap:3, height:36, marginTop:8 }}>
      {data.map((v, i) => (
        <div key={i} style={{
          flex: 1,
          height: `${Math.max((v / max) * 100, 4)}%`,
          background: i === data.length - 1 ? color : 'var(--subtle)',
          borderRadius: '3px 3px 0 0',
          minHeight: 3
        }} />
      ))}
    </div>
  );
};
```

Backend should include 7-day arrays for each sparkline metric:
```python
"sparklines": {
    "signups_7d": [2, 4, 1, 3, 5, 2, 3],      # today is last element
    "active_7d": [7, 12, 8, 15, 9, 8, 9],
    "pitches_7d": [30, 12, 45, 22, 31, 18, 13],
    "pro_7d": [0, 0, 0, 0, 0, 0, 1]
}
```

### 4.6 "Send Nudge" Button

```javascript
const sendNudge = async (userId, username) => {
  if (!confirm(`Send upgrade nudge to ${username}?`)) return;
  await fetch(`/api/admin/nudge/${userId}`, { method: 'POST' });
  // Show inline success: replace button text with "Sent ✓"
};
```

### 4.7 Funnel Bar Widths

Funnel bars scale relative to total signups (step 1 = 100%). Calculate in frontend:

```javascript
const funnelSteps = [
  { label: 'Signed up',       count: data.signups,           color: '#0F0F0F' },
  { label: 'Saved ≥1 brand',  count: data.saved_brand,       color: '#2563EB' },
  { label: 'Sent first pitch',count: data.sent_first_pitch,  color: '#7C3AED' },
  { label: 'Pitched ≥2 brands',count: data.pitched_multi,    color: '#E11D48' },
  { label: 'Package received',count: data.package_received,  color: '#059669' },
];

// width = (count / signups) * 100 + '%'
// drop rate between steps = (prev - current) / prev * 100
```

---

## 5. Design Tokens

Copy the CSS variables from the HTML preview directly into your styled-components theme or as a global style block:

```css
:root {
  --primary: #E11D48; --primary-light: #FFF1F3;
  --action:  #0F0F0F;
  --accent:  #7C3AED; --accent-light: #F5F3FF;
  --success: #059669; --success-light: #ECFDF5; --success-border: #A7F3D0;
  --warn:    #D97706; --warn-light: #FFFBEB; --warn-border: #FDE68A;
  --info:    #2563EB; --info-light: #EFF6FF; --info-border: #BFDBFE;
  --bg:      #F5F5F7; --surface: #FFFFFF;
  --border:  #E8E8E8; --border2: #F0F0F0;
  --text:    #0F0F0F; --text-2: #4B4B4B; --text-3: #8C8C8C;
}
```

---

## 6. Traffic Monitoring Setup

The "Growth Funnel" tab has a placeholder section for web traffic. To activate it:

**Option A — Plausible (recommended, privacy-first)**
1. Sign up at plausible.io
2. Add to `newcollab.co` Next.js layout (`app/layout.js`):
```html
<script defer data-domain="newcollab.co" src="https://plausible.io/js/script.js"></script>
```
3. Use Plausible API to pull pageviews, sources, and top pages into the admin dashboard
4. Key metrics to display: total pageviews, unique visitors, top sources (organic/direct/social), top landing pages

**Option B — Google Analytics 4**
1. Create GA4 property
2. Add to Next.js layout:
```javascript
// app/layout.js
import { GoogleAnalytics } from '@next/third-parties/google'
// In body: <GoogleAnalytics gaId="G-XXXXXXXX" />
```
3. Pull data via GA4 Data API (requires service account)

**Until traffic monitoring is connected**, the dashboard shows the inline notice explaining what's needed. Do not show empty charts — the notice is better UX.

---

## 7. Event Tracking for New Features

To populate the "For You tab" and "My Kit" adoption bars, add lightweight event logging:

**Option A — Simple DB table (no external dependency)**

```sql
CREATE TABLE IF NOT EXISTS feature_events (
  id SERIAL PRIMARY KEY,
  user_id INT REFERENCES users(id) ON DELETE CASCADE,
  feature VARCHAR(50) NOT NULL,  -- 'for_you_view', 'kit_view', 'pitch_now', etc.
  created_at TIMESTAMP DEFAULT NOW()
);
CREATE INDEX ON feature_events(feature, created_at);
CREATE INDEX ON feature_events(user_id, created_at);
```

**Backend call** (add to relevant route handlers):
```python
def log_feature_event(user_id, feature):
    db.session.execute(
        "INSERT INTO feature_events (user_id, feature) VALUES (:uid, :f)",
        {"uid": user_id, "f": feature}
    )
    db.session.commit()
```

**Events to track:**
| Event | Where to log |
|---|---|
| `for_you_view` | `GET /api/for-you` |
| `for_you_pitch_now` | `POST /api/pipeline` from for-you context |
| `kit_view` | `GET /api/user/kit` |
| `kit_generated` | `POST /api/user/kit` |
| `pipeline_stage_changed` | `PATCH /api/pipeline/:id` |

---

## 8. Implementation Order

1. **Backend first** — write all 5 admin API endpoints with the SQL queries above. Test with Postman/curl before touching frontend.
2. **Replace AdminDashboard.js** — start with the Today tab only (NorthStar + DailyPulse + AtLimitTable). Get real data rendering.
3. **Wire up "Send nudge"** — this is the highest business value feature; connects to existing email system.
4. **Add Growth Funnel tab** — pure read-only SQL, no side effects.
5. **Add Pipeline Health tab** — uses `saved_brands.pipeline_stage` counts.
6. **Add Monetization tab** — reuses most queries already written.
7. **Add Users tab** — paginated, add filter by tier.
8. **Add sparkline arrays** — once core data is working, add 7-day history arrays to the backend response.

---

## 9. Find/Replace Blocks

### 9.1 Add admin routes to `admin_routes.py`

Find the end of your existing admin routes file and add before the final line:

```python
# FIND (last line of your existing admin routes, e.g.):
# if __name__ == '__main__':

# ADD BEFORE IT:

@app.route('/api/admin/dashboard', methods=['GET'])
@admin_required
def admin_dashboard_v2():
    from datetime import date
    today = date.today()
    month_start = today.replace(day=1)

    # North star
    pro_count = db.session.execute(
        "SELECT COUNT(*) FROM users WHERE subscription_tier = 'pro' AND subscription_status = 'active'"
    ).scalar() or 0
    mrr = pro_count * 12

    total_users = db.session.execute("SELECT COUNT(*) FROM users").scalar() or 0
    conversion_rate = round((pro_count / total_users * 100), 2) if total_users else 0

    active_30d = db.session.execute("""
        SELECT COUNT(DISTINCT user_id) FROM (
            SELECT user_id FROM saved_brands WHERE created_at > NOW() - INTERVAL '30 days'
            UNION
            SELECT user_id FROM ai_generations WHERE created_at > NOW() - INTERVAL '30 days'
        ) AS a
    """).scalar() or 0

    total_pitches = db.session.execute(
        "SELECT COUNT(*) FROM saved_brands WHERE pipeline_stage != 'saved'"
    ).scalar() or 0

    # Daily pulse
    new_signups_today = db.session.execute(
        "SELECT COUNT(*) FROM users WHERE DATE(created_at) = CURRENT_DATE"
    ).scalar() or 0

    active_today = db.session.execute("""
        SELECT COUNT(DISTINCT user_id) FROM (
            SELECT user_id FROM saved_brands WHERE DATE(updated_at) = CURRENT_DATE
            UNION
            SELECT user_id FROM ai_generations WHERE DATE(created_at) = CURRENT_DATE
        ) AS a
    """).scalar() or 0

    ai_pitches_today = db.session.execute(
        "SELECT COUNT(*) FROM ai_generations WHERE DATE(created_at) = CURRENT_DATE"
    ).scalar() or 0

    new_pro_today = db.session.execute(
        "SELECT COUNT(*) FROM users WHERE subscription_tier = 'pro' AND DATE(subscription_started_at) = CURRENT_DATE"
    ).scalar() or 0

    pipeline_saves_today = db.session.execute(
        "SELECT COUNT(*) FROM saved_brands WHERE DATE(created_at) = CURRENT_DATE"
    ).scalar() or 0

    # At-limit users
    at_limit_rows = db.session.execute("""
        SELECT u.id, u.username, u.email, u.follower_count, u.niche, COUNT(sb.id) as pitch_count
        FROM users u
        JOIN saved_brands sb ON sb.user_id = u.id
        WHERE u.subscription_tier = 'free'
          AND sb.pipeline_stage != 'saved'
          AND DATE_TRUNC('month', sb.pitched_at) = DATE_TRUNC('month', CURRENT_DATE)
        GROUP BY u.id, u.username, u.email, u.follower_count, u.niche
        HAVING COUNT(sb.id) >= 3
        ORDER BY COUNT(sb.id) DESC
        LIMIT 20
    """).fetchall()

    at_limit_today = len(at_limit_rows)

    near_limit_today = db.session.execute("""
        SELECT COUNT(*) FROM (
            SELECT user_id FROM saved_brands
            WHERE pipeline_stage != 'saved'
              AND DATE_TRUNC('month', pitched_at) = DATE_TRUNC('month', CURRENT_DATE)
            GROUP BY user_id HAVING COUNT(*) = 2
        ) AS nl
        JOIN users u ON u.id = nl.user_id
        WHERE u.subscription_tier = 'free'
    """).scalar() or 0

    # Top brands today
    top_brands = db.session.execute("""
        SELECT b.name, b.category, COUNT(sb.id) as pitches_today, b.response_rate
        FROM brands b
        JOIN saved_brands sb ON sb.brand_id = b.id
        WHERE sb.pipeline_stage != 'saved' AND DATE(sb.pitched_at) = CURRENT_DATE
        GROUP BY b.id, b.name, b.category, b.response_rate
        ORDER BY pitches_today DESC
        LIMIT 10
    """).fetchall()

    return jsonify({
        "north_star": {
            "mrr": mrr,
            "pro_count": pro_count,
            "conversion_rate": conversion_rate,
            "active_30d": active_30d,
            "total_users": total_users,
            "total_pitches": total_pitches
        },
        "daily_pulse": {
            "new_signups_today": new_signups_today,
            "active_users_today": active_today,
            "ai_pitches_today": ai_pitches_today,
            "new_pro_today": new_pro_today,
            "pipeline_saves_today": pipeline_saves_today,
            "at_limit_today": at_limit_today,
            "near_limit_today": near_limit_today
        },
        "at_limit_users": [
            {"id": r.id, "username": r.username, "email": r.email,
             "followers": r.follower_count, "niche": r.niche,
             "pitch_count": r.pitch_count}
            for r in at_limit_rows
        ],
        "top_brands_today": [
            {"name": r.name, "category": r.category,
             "pitches": r.pitches_today, "response_rate": r.response_rate}
            for r in top_brands
        ]
    })


@app.route('/api/admin/funnel', methods=['GET'])
@admin_required
def admin_funnel():
    signups = db.session.execute("SELECT COUNT(*) FROM users").scalar() or 0

    saved = db.session.execute(
        "SELECT COUNT(DISTINCT user_id) FROM saved_brands"
    ).scalar() or 0

    pitched = db.session.execute(
        "SELECT COUNT(DISTINCT user_id) FROM saved_brands WHERE pipeline_stage != 'saved'"
    ).scalar() or 0

    multi_pitched = db.session.execute("""
        SELECT COUNT(*) FROM (
            SELECT user_id FROM saved_brands WHERE pipeline_stage != 'saved'
            GROUP BY user_id HAVING COUNT(*) >= 2
        ) AS mp
    """).scalar() or 0

    won = db.session.execute(
        "SELECT COUNT(DISTINCT user_id) FROM saved_brands WHERE pipeline_stage IN ('won', 'received')"
    ).scalar() or 0

    discover_adoption = db.session.execute(
        "SELECT COUNT(DISTINCT user_id) FROM saved_brands WHERE created_at > NOW() - INTERVAL '30 days'"
    ).scalar() or 0

    ai_adoption = db.session.execute(
        "SELECT COUNT(DISTINCT user_id) FROM ai_generations WHERE created_at > NOW() - INTERVAL '30 days'"
    ).scalar() or 0

    return jsonify({
        "signups": signups,
        "saved_brand": saved,
        "sent_first_pitch": pitched,
        "pitched_multi": multi_pitched,
        "package_received": won,
        "feature_adoption": {
            "discover": discover_adoption,
            "pipeline": saved,
            "ai_pitch": ai_adoption,
            "for_you": 0,
            "my_kit": 0
        }
    })


@app.route('/api/admin/nudge/<int:user_id>', methods=['POST'])
@admin_required
def admin_nudge_user(user_id):
    user = User.query.get_or_404(user_id)
    # Replace with your actual email function name:
    # send_upgrade_nudge_email(user)
    return jsonify({"ok": True, "user": user.username})
```

---

## 10. Column Name Assumptions

The queries above assume these column names. Adjust to match your actual schema:

| Table | Column assumed | Notes |
|---|---|---|
| `users` | `subscription_tier` | `'free'` or `'pro'` |
| `users` | `subscription_status` | `'active'` when paid |
| `users` | `subscription_started_at` | timestamp of first pro payment |
| `users` | `follower_count` | total followers (any platform) |
| `users` | `niche` | primary content category |
| `saved_brands` | `pipeline_stage` | varchar, defaults to `'saved'` |
| `saved_brands` | `pitched_at` | timestamp when stage left 'saved' |
| `ai_generations` | `type` | `'pitch'` for outreach emails |
| `brands` | `response_rate` | float 0–100 |

If your column names differ, do a find/replace on the SQL strings.

---

## 11. Quick Wins (do these first, 30 minutes total)

1. **At-limit list + Send Nudge button** — Highest direct business value. One email to a user who just hit their limit converts at 10–15x baseline rate.
2. **North Star strip** — Shows MRR, Pro count, conversion rate. Critical for daily founder check-in.
3. **Growth Funnel** — The 79% saved→pitched drop is the most important metric to track over time. See if it improves as you roll out For You tab and follow-up emails.
