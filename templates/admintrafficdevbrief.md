# Founder Dashboard — Traffic Section (GA4) Dev Brief

Adds a traffic section between "This week's health" and "Where you're losing people".
Pulls data from Google Analytics 4 via the GA4 Data API.

---

## 1. GA4 Setup (one-time, ~15 minutes)

### Step 1 — Create a Service Account

1. Go to [console.cloud.google.com](https://console.cloud.google.com)
2. Select your project (or create one called `newcollab-admin`)
3. Go to **IAM & Admin → Service Accounts**
4. Click **Create Service Account**
   - Name: `newcollab-dashboard`
   - Role: leave blank for now → click Done
5. Click the new service account → **Keys tab** → **Add Key → JSON**
6. Download the JSON file — save as `ga4-service-account.json`
7. **Never commit this file to git** — add to `.gitignore`:
   ```
   ga4-service-account.json
   ```

### Step 2 — Grant the Service Account access to GA4

1. Go to [analytics.google.com](https://analytics.google.com)
2. Open your GA4 property → **Admin** (gear icon bottom-left)
3. Under **Property**, click **Property Access Management**
4. Click **+** → Add users
5. Paste the service account email (looks like `newcollab-dashboard@your-project.iam.gserviceaccount.com`)
6. Role: **Viewer**
7. Click Add

### Step 3 — Get your GA4 Property ID

1. In GA4 Admin → **Property Settings**
2. Copy the **Property ID** (numeric, e.g. `123456789`)
3. Add to your environment variables:
   ```
   GA4_PROPERTY_ID=123456789
   GA4_SERVICE_ACCOUNT_PATH=/path/to/ga4-service-account.json
   ```
   On Vercel: add these as Environment Variables in the project settings.

### Step 4 — Install the GA4 Python library

```bash
pip install google-analytics-data
```

Add to `requirements.txt`:
```
google-analytics-data==0.18.3
```

---

## 2. Backend — GA4 Helper

Create a new file `utils/ga4.py`:

```python
# utils/ga4.py
import os
from google.analytics.data_v1beta import BetaAnalyticsDataClient
from google.analytics.data_v1beta.types import (
    RunReportRequest, DateRange, Metric, Dimension, OrderBy
)
from google.oauth2 import service_account

def _get_client():
    sa_path = os.environ.get('GA4_SERVICE_ACCOUNT_PATH')
    if sa_path:
        credentials = service_account.Credentials.from_service_account_file(
            sa_path,
            scopes=["https://www.googleapis.com/auth/analytics.readonly"]
        )
        return BetaAnalyticsDataClient(credentials=credentials)
    # Falls back to application default credentials (useful on GCP)
    return BetaAnalyticsDataClient()


def get_traffic_data():
    """
    Returns traffic data for the last 7 days vs previous 7 days.
    Raises on auth error — caller should catch and return empty state.
    """
    property_id = os.environ.get('GA4_PROPERTY_ID')
    if not property_id:
        return None

    client = _get_client()

    # ── REPORT 1: weekly totals + comparison ──────────────────────
    totals_request = RunReportRequest(
        property=f"properties/{property_id}",
        date_ranges=[
            DateRange(start_date="7daysAgo", end_date="today"),       # this week
            DateRange(start_date="14daysAgo", end_date="8daysAgo"),   # last week
        ],
        metrics=[
            Metric(name="activeUsers"),
            Metric(name="screenPageViews"),
            Metric(name="sessions"),
        ]
    )
    totals = client.run_report(totals_request)

    this_week = totals.rows[0].metric_values if totals.rows else None
    last_week = totals.rows[1].metric_values if len(totals.rows) > 1 else None

    visitors_this  = int(this_week[0].value) if this_week else 0
    pageviews_this = int(this_week[1].value) if this_week else 0
    visitors_last  = int(last_week[0].value) if last_week else 0
    pageviews_last = int(last_week[1].value) if last_week else 0

    # ── REPORT 2: daily visitors for sparkline (last 7 days) ──────
    daily_request = RunReportRequest(
        property=f"properties/{property_id}",
        date_ranges=[DateRange(start_date="6daysAgo", end_date="today")],
        dimensions=[Dimension(name="date")],
        metrics=[Metric(name="activeUsers")],
        order_bys=[OrderBy(dimension=OrderBy.DimensionOrderBy(dimension_name="date"))]
    )
    daily = client.run_report(daily_request)
    # Returns 7 values oldest→today
    daily_visitors = [int(r.metric_values[0].value) for r in daily.rows]
    # Pad to 7 if fewer days returned
    while len(daily_visitors) < 7:
        daily_visitors.insert(0, 0)

    # ── REPORT 3: traffic sources ─────────────────────────────────
    sources_request = RunReportRequest(
        property=f"properties/{property_id}",
        date_ranges=[DateRange(start_date="7daysAgo", end_date="today")],
        dimensions=[Dimension(name="sessionDefaultChannelGroup")],
        metrics=[Metric(name="sessions")],
        order_bys=[OrderBy(metric=OrderBy.MetricOrderBy(metric_name="sessions"), desc=True)],
        limit=6
    )
    sources_resp = client.run_report(sources_request)
    total_sessions = sum(int(r.metric_values[0].value) for r in sources_resp.rows) or 1

    # Normalize GA4 channel names to friendly labels
    channel_map = {
        "Organic Search":  "Organic",
        "Direct":          "Direct",
        "Organic Social":  "Social",
        "Paid Social":     "Social",
        "Referral":        "Referral",
        "Email":           "Email",
        "Paid Search":     "Paid",
        "Unassigned":      "Other",
    }
    sources_agg = {}
    for row in sources_resp.rows:
        channel = row.dimension_values[0].value
        label = channel_map.get(channel, "Other")
        count = int(row.metric_values[0].value)
        sources_agg[label] = sources_agg.get(label, 0) + count

    # Sort descending and compute percentages
    sources = [
        {
            "label": k,
            "sessions": v,
            "pct": round(v / total_sessions * 100)
        }
        for k, v in sorted(sources_agg.items(), key=lambda x: -x[1])
    ]

    # ── REPORT 4: top pages ───────────────────────────────────────
    pages_request = RunReportRequest(
        property=f"properties/{property_id}",
        date_ranges=[DateRange(start_date="7daysAgo", end_date="today")],
        dimensions=[Dimension(name="pagePath")],
        metrics=[Metric(name="screenPageViews")],
        order_bys=[OrderBy(metric=OrderBy.MetricOrderBy(metric_name="screenPageViews"), desc=True)],
        limit=5
    )
    pages_resp = client.run_report(pages_request)

    # Pages that are conversion-oriented (signup/landing)
    CONVERT_PAGES = {'/', '/signup', '/register', '/pricing'}

    top_pages = [
        {
            "path": row.dimension_values[0].value,
            "views": int(row.metric_values[0].value),
            "converts": row.dimension_values[0].value in CONVERT_PAGES,
        }
        for row in pages_resp.rows
    ]

    # ── ORGANIC SEARCH % ─────────────────────────────────────────
    organic_pct = next(
        (s["pct"] for s in sources if s["label"] == "Organic"), 0
    )

    return {
        "visitors_this_week": visitors_this,
        "visitors_last_week": visitors_last,
        "pageviews_this_week": pageviews_this,
        "pageviews_last_week": pageviews_last,
        "daily_visitors_7d": daily_visitors,   # for sparkline, oldest first
        "organic_pct": organic_pct,
        "sources": sources,
        "top_pages": top_pages,
    }
```

---

## 3. Backend — Add Traffic to Dashboard Endpoint

In `admin_routes.py`, update the `founder_dashboard` function.

**Find:**
```python
    return jsonify({
        "goal": {
```

**Add BEFORE it** (inside the function, before the return):

```python
    # ── TRAFFIC from GA4 ──────────────────────────────────────────
    traffic = None
    try:
        from utils.ga4 import get_traffic_data
        traffic_raw = get_traffic_data()
        if traffic_raw:
            # Visitor → signup rate: combine GA4 visitors with DB signups
            visitors = traffic_raw["visitors_this_week"]
            signups_this_week_count = month_signups  # reuse query already run above
            visitor_signup_rate = round(
                (signups_this_week_count / visitors * 100), 1
            ) if visitors else 0

            traffic = {
                **traffic_raw,
                "visitor_signup_rate": visitor_signup_rate,
                "signups_this_week": signups_this_week_count,
                "connected": True,
            }
    except Exception as e:
        print(f"[GA4] Error fetching traffic data: {e}")
        traffic = {"connected": False, "error": str(e)}
```

**Then in the return statement, add `"traffic": traffic`:**

```python
    return jsonify({
        "goal": { ... },
        "hot_leads": [ ... ],
        "health": { ... },
        "traffic": traffic,   # <-- add this line
        "funnel": { ... },
        "month": { ... },
    })
```

---

## 4. Frontend — Traffic Section

Add these styled-components near the others in `AdminDashboard.js`:

```javascript
// ── TRAFFIC STYLED COMPONENTS ─────────────────────────────────────

const TrafficGrid = styled.div`
  display: grid;
  grid-template-columns: repeat(4, 1fr);
  gap: 12px; margin-bottom: 12px;
  @media (max-width: 760px) { grid-template-columns: 1fr 1fr; }
`;

const TrafficCard = styled(HealthCard)``;  // reuse HealthCard

const TrafficBottom = styled.div`
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 12px; margin-bottom: 20px;
  @media (max-width: 640px) { grid-template-columns: 1fr; }
`;

const TrafficPanel = styled.div`
  background: #fff; border: 1px solid ${T.border};
  border-radius: 14px; padding: 18px 20px;
  box-shadow: 0 1px 3px rgba(0,0,0,.04);
`;

const PanelTitle = styled.div`
  font-size: 13px; font-weight: 700; margin-bottom: 14px;
`;

const SourceRow = styled.div`
  display: flex; align-items: center; gap: 10px; margin-bottom: 10px;
  &:last-child { margin-bottom: 0; }
`;

const SourceDot = styled.div`
  width: 8px; height: 8px; border-radius: 50%;
  background: ${p => p.$color}; flex-shrink: 0;
`;

const SourceLabel = styled.div`
  font-size: 12px; font-weight: 600; color: ${T.text2};
  width: 80px; flex-shrink: 0;
`;

const SourceTrack = styled.div`
  flex: 1; height: 8px; background: #F0F0F0; border-radius: 4px; overflow: hidden;
`;

const SourceFill = styled.div`
  height: 100%; border-radius: 4px;
  background: ${p => p.$color};
  width: ${p => p.$pct}%;
  transition: width .6s ease;
`;

const SourcePct = styled.div`
  font-size: 12px; font-weight: 800;
  color: ${p => p.$color || T.text};
  width: 34px; text-align: right; flex-shrink: 0;
`;

const PageRow = styled.div`
  display: flex; align-items: center; justify-content: space-between;
  padding: 7px 0; border-bottom: 1px solid #F5F5F5;
  &:last-child { border-bottom: none; }
`;

const PagePath = styled.span`
  font-size: 12px; font-weight: 600; color: ${T.text2};
  font-family: monospace;
`;

const PageViews = styled.span`
  font-size: 12px; font-weight: 800;
`;

const ConvertsBadge = styled.span`
  font-size: 10px; font-weight: 700;
  padding: 2px 7px; border-radius: 20px;
  background: #ECFDF5; color: ${T.green};
  margin-left: 6px;
`;

const GA4Badge = styled.span`
  display: inline-flex; align-items: center; gap: 4px;
  background: #EFF6FF; border: 1px solid #BFDBFE;
  border-radius: 20px; padding: 3px 9px;
  font-size: 11px; font-weight: 700; color: #1D4ED8;
  margin-left: 8px;
`;

const NotConnectedBox = styled.div`
  background: #EFF6FF; border: 1px solid #BFDBFE;
  border-radius: 12px; padding: 16px 20px;
  margin-bottom: 20px;
  font-size: 13px; color: #1E3A5F; line-height: 1.6;
  strong { font-weight: 700; }
  code {
    background: rgba(37,99,235,.1); padding: 1px 6px;
    border-radius: 4px; font-size: 11px; font-weight: 600;
  }
`;
```

### Source color map (add as constant)

```javascript
const SOURCE_COLORS = {
  Organic:  T.green,
  Direct:   T.black,
  Social:   T.rose,
  Referral: T.violet,
  Email:    '#2563EB',
  Paid:     '#D97706',
  Other:    '#999',
};
```

### Traffic section JSX

Add this block in the component's return, after the `</HealthGrid>` closing tag and before the funnel `<SectionLabel>`:

```jsx
{/* ── TRAFFIC ─────────────────────────────────────────── */}
<SectionLabel>
  Traffic — last 7 days
  {traffic?.connected && <GA4Badge>● GA4</GA4Badge>}
</SectionLabel>

{!traffic || !traffic.connected ? (
  <NotConnectedBox>
    <strong>Connect GA4 to see traffic data.</strong><br />
    Add <code>GA4_PROPERTY_ID</code> and <code>GA4_SERVICE_ACCOUNT_PATH</code> to your environment variables,
    then grant the service account Viewer access in GA4 Admin → Property Access Management.
    See the dev brief for full setup steps.
  </NotConnectedBox>
) : (
  <>
    <TrafficGrid>
      {/* Unique visitors */}
      <TrafficCard>
        <HcLabel>Unique visitors</HcLabel>
        <HcValue>{traffic.visitors_this_week.toLocaleString()}</HcValue>
        <HcDelta
          $up={traffic.visitors_this_week >= traffic.visitors_last_week}
          $down={traffic.visitors_this_week < traffic.visitors_last_week}
        >
          {traffic.visitors_this_week >= traffic.visitors_last_week ? '↑' : '↓'}{' '}
          {deltaText(traffic.visitors_this_week, traffic.visitors_last_week)}
        </HcDelta>
        <SparkLine data={traffic.daily_visitors_7d} color={T.black} />
      </TrafficCard>

      {/* Pageviews */}
      <TrafficCard>
        <HcLabel>Pageviews</HcLabel>
        <HcValue>{traffic.pageviews_this_week.toLocaleString()}</HcValue>
        <HcDelta
          $up={traffic.pageviews_this_week >= traffic.pageviews_last_week}
          $down={traffic.pageviews_this_week < traffic.pageviews_last_week}
        >
          {traffic.pageviews_this_week >= traffic.pageviews_last_week ? '↑' : '↓'}{' '}
          {deltaText(traffic.pageviews_this_week, traffic.pageviews_last_week)}
        </HcDelta>
        <SparkLine data={traffic.daily_visitors_7d} color="#2563EB" />
      </TrafficCard>

      {/* Visitor → signup rate */}
      <TrafficCard>
        <HcLabel>Visitor → Signup</HcLabel>
        <HcValue $color={traffic.visitor_signup_rate >= 2 ? T.green : T.violet}>
          {traffic.visitor_signup_rate}%
        </HcValue>
        <HcDelta $flat>
          {traffic.signups_this_week} signups / {traffic.visitors_this_week.toLocaleString()} visitors
        </HcDelta>
        <div style={{ marginTop:10, fontSize:11, color: T.text3, lineHeight:1.5 }}>
          Target: 2–3%<br />
          {traffic.visitor_signup_rate < 2 && (
            <span style={{ color: T.rose, fontWeight:700 }}>Improve signup page CTA</span>
          )}
        </div>
      </TrafficCard>

      {/* Organic search % */}
      <TrafficCard>
        <HcLabel>Organic search</HcLabel>
        <HcValue $color={T.green}>{traffic.organic_pct}%</HcValue>
        <HcDelta $up={traffic.organic_pct >= 25}>
          {traffic.organic_pct >= 25 ? '↑ SEO working' : 'Grow organic traffic'}
        </HcDelta>
        <div style={{ marginTop:10, fontSize:11, color: T.text3, lineHeight:1.5 }}>
          of all traffic<br />
          <span style={{ color: T.green, fontWeight:700 }}>Best free acquisition channel</span>
        </div>
      </TrafficCard>
    </TrafficGrid>

    <TrafficBottom>
      {/* Traffic sources */}
      <TrafficPanel>
        <PanelTitle>Traffic sources</PanelTitle>
        {traffic.sources.map(s => (
          <SourceRow key={s.label}>
            <SourceDot $color={SOURCE_COLORS[s.label] || T.text3} />
            <SourceLabel>{s.label}</SourceLabel>
            <SourceTrack>
              <SourceFill $pct={s.pct} $color={SOURCE_COLORS[s.label] || T.text3} />
            </SourceTrack>
            <SourcePct $color={SOURCE_COLORS[s.label]}>{s.pct}%</SourcePct>
          </SourceRow>
        ))}
      </TrafficPanel>

      {/* Top pages */}
      <TrafficPanel>
        <PanelTitle>Top pages this week</PanelTitle>
        {traffic.top_pages.map(p => (
          <PageRow key={p.path}>
            <PagePath>{p.path.length > 22 ? p.path.slice(0, 22) + '…' : p.path}</PagePath>
            <span>
              <PageViews>{p.views.toLocaleString()}</PageViews>
              {p.converts && <ConvertsBadge>converts</ConvertsBadge>}
            </span>
          </PageRow>
        ))}
      </TrafficPanel>
    </TrafficBottom>
  </>
)}
```

### Update the data fetch

In your `fetchData` function, the traffic data already comes back in the same `founder-dashboard` response — no extra API call needed. Just destructure it:

```javascript
// FIND:
const { goal, hot_leads, health, funnel, month } = data;

// REPLACE WITH:
const { goal, hot_leads, health, traffic, funnel, month } = data;
```

---

## 5. Caching (important — GA4 API has rate limits)

The GA4 Data API has a free quota of ~200,000 tokens/day. For a low-traffic admin dashboard this is fine, but don't call it on every page load without caching.

Add simple in-memory caching to `utils/ga4.py`:

```python
import time

_cache = {"data": None, "expires": 0}
CACHE_TTL = 3600  # 1 hour — traffic data doesn't need to be live

def get_traffic_data():
    global _cache
    now = time.time()
    if _cache["data"] and now < _cache["expires"]:
        return _cache["data"]

    # ... (rest of existing function) ...

    result = { ... }  # the dict you already return

    _cache = {"data": result, "expires": now + CACHE_TTL}
    return result
```

The Refresh button on the dashboard should have an option to bust the cache if needed:

```python
# Add ?bust=1 support to the endpoint:
if request.args.get('bust') == '1':
    _cache["expires"] = 0  # force refresh
```

```javascript
// In your RefreshBtn onClick:
const fetchData = useCallback((bust = false) => {
  const url = bust
    ? '/api/admin/founder-dashboard?bust=1'
    : '/api/admin/founder-dashboard';
  fetch(url).then(r => r.json()).then(setData);
}, []);

// RefreshBtn:
<RefreshBtn onClick={() => fetchData(true)}>↻ Refresh</RefreshBtn>
```

---

## 6. Implementation Checklist

```
□ 1. Create service account in Google Cloud Console
□ 2. Download service-account JSON → add to .gitignore immediately
□ 3. Grant service account Viewer access in GA4 Admin
□ 4. Add GA4_PROPERTY_ID and GA4_SERVICE_ACCOUNT_PATH to .env (local)
□ 5. Add same env vars to Vercel / your hosting environment
□ 6. pip install google-analytics-data → add to requirements.txt
□ 7. Create utils/ga4.py with the helper above
□ 8. Update founder_dashboard() endpoint to call get_traffic_data()
□ 9. Add traffic styled-components to AdminDashboard.js
□ 10. Add traffic JSX block between health and funnel sections
□ 11. Destructure traffic from API response
□ 12. Test: curl the endpoint — traffic key should appear in JSON
□ 13. Check "not connected" state renders correctly if GA4 returns None
□ 14. Test on mobile (sources bars and page list should stack cleanly)
```

---

## 7. Troubleshooting

| Error | Fix |
|---|---|
| `google.auth.exceptions.DefaultCredentialsError` | Check `GA4_SERVICE_ACCOUNT_PATH` points to the right file |
| `403 The caller does not have permission` | Service account not added as Viewer in GA4 Admin |
| `404 Property not found` | Wrong `GA4_PROPERTY_ID` — check it's numeric, not "UA-..." |
| Traffic shows zeros | GA4 may take 24–48h to populate a new property. Check GA4 Realtime report first to confirm data is flowing. |
| Sparkline has fewer than 7 bars | Normal if property is new — the `while len < 7` padding in `get_traffic_data()` handles this |
