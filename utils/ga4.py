# utils/ga4.py
"""
Google Analytics 4 Data API helper for founder dashboard traffic metrics.

Auth (pick one):
  Production: GA4_SERVICE_ACCOUNT_PATH + grant Viewer on the property
              (scripts/grant_ga4_access.py if the GA4 UI rejects the SA email)
  Local dev:  GA4_USE_USER_CREDENTIALS=true + GA4_TOKEN_PATH (or reuse
              seo-assistant/tokens/ga4_token.json automatically)

Requires GA4_PROPERTY_ID (or GA4_PROPERTY) in the environment.
"""
import os
import time

GA4_READONLY_SCOPE = "https://www.googleapis.com/auth/analytics.readonly"

# In-memory cache to avoid hitting GA4 API rate limits
_cache = {"data": None, "expires": 0, "fetched_at": 0}
CACHE_TTL = 900  # 15 minutes — balance freshness vs GA4 API rate limits


def _use_user_credentials():
    # Skip if production service account is configured
    if os.environ.get("GA4_SERVICE_ACCOUNT_JSON") or os.environ.get("GA4_SERVICE_ACCOUNT_PATH"):
        return False
    if os.environ.get("GA4_USE_USER_CREDENTIALS", "").lower() in ("1", "true", "yes"):
        return True
    # Auto-use seo-assistant token when explicitly pointed or discoverable
    if os.environ.get("GA4_TOKEN_PATH"):
        return True
    seo_token = os.path.normpath(
        os.path.join(os.path.dirname(__file__), "..", "..", "seo-assistant", "tokens", "ga4_token.json")
    )
    return os.path.isfile(seo_token)


def _get_ga4_client():
    """Service account for production; optional user ADC for local dev."""
    from google.analytics.data_v1beta import BetaAnalyticsDataClient
    from google.oauth2 import service_account

    if _use_user_credentials():
        # Prefer OAuth token from scripts/ga4_oauth_login.py (avoids blocked gcloud app)
        try:
            import sys
            root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            if root not in sys.path:
                sys.path.insert(0, root)
            from scripts.ga4_credentials import load_user_credentials

            credentials = load_user_credentials([GA4_READONLY_SCOPE])
            if credentials:
                print("[GA4] Using saved OAuth user token")
                return BetaAnalyticsDataClient(credentials=credentials)
        except Exception as e:
            print(f"[GA4] OAuth token load failed ({e}), trying ADC...")

        import google.auth
        credentials, _ = google.auth.default(scopes=[GA4_READONLY_SCOPE])
        print("[GA4] Using Application Default Credentials")
        return BetaAnalyticsDataClient(credentials=credentials)

    # Production: inline JSON via env var (Vercel / Railway / Render)
    sa_json = os.environ.get("GA4_SERVICE_ACCOUNT_JSON")
    if sa_json:
        import json
        sa_info = json.loads(sa_json)
        credentials = service_account.Credentials.from_service_account_info(
            sa_info,
            scopes=[GA4_READONLY_SCOPE],
        )
        print("[GA4] Using service account credentials from GA4_SERVICE_ACCOUNT_JSON")
        return BetaAnalyticsDataClient(credentials=credentials)

    # Local dev: file path
    sa_path = os.environ.get("GA4_SERVICE_ACCOUNT_PATH")
    if sa_path and os.path.isfile(sa_path):
        credentials = service_account.Credentials.from_service_account_file(
            sa_path,
            scopes=[GA4_READONLY_SCOPE],
        )
        return BetaAnalyticsDataClient(credentials=credentials)

    # GCP default credentials (Cloud Run, etc.)
    return BetaAnalyticsDataClient()


def get_traffic_data(bust_cache=False):
    """
    Returns traffic data for the last 7 days vs previous 7 days.
    Raises on auth error - caller should catch and return empty state.

    Returns None if GA4 is not configured.
    """
    global _cache

    # GA4_PROPERTY_ID (canonical) or GA4_PROPERTY alias
    property_id = os.environ.get('GA4_PROPERTY_ID') or os.environ.get('GA4_PROPERTY')
    if not property_id:
        return None

    # Check cache (unless bust_cache is True)
    now = time.time()
    if not bust_cache and _cache["data"] and now < _cache["expires"]:
        return _cache["data"]

    try:
        from google.analytics.data_v1beta.types import (
            RunReportRequest, DateRange, Metric, Dimension, OrderBy
        )
        from google.api_core.exceptions import PermissionDenied
    except ImportError:
        print("[GA4] google-analytics-data package not installed. Run: pip install google-analytics-data")
        return None

    try:
        client = _get_ga4_client()
    except Exception as e:
        print(f"[GA4] Failed to build API client: {e}")
        return None

    totals_request = RunReportRequest(
        property=f"properties/{property_id}",
        date_ranges=[
            DateRange(start_date="7daysAgo", end_date="today"),
            DateRange(start_date="14daysAgo", end_date="8daysAgo"),
        ],
        metrics=[
            Metric(name="activeUsers"),
            Metric(name="screenPageViews"),
            Metric(name="sessions"),
        ],
    )

    try:
        totals = client.run_report(totals_request)
    except PermissionDenied:
        if _use_user_credentials():
            raise
        print(
            "[GA4] Service account lacks property access. "
            "Run: python scripts/grant_ga4_access.py "
            "OR set GA4_USE_USER_CREDENTIALS=true and gcloud auth application-default login "
            "as team@newcollab.co (local dev only)."
        )
        return None

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
    CONVERT_PAGES = {'/', '/signup', '/register', '/pricing', '/creator/signup'}

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

    result = {
        "visitors_this_week": visitors_this,
        "visitors_last_week": visitors_last,
        "pageviews_this_week": pageviews_this,
        "pageviews_last_week": pageviews_last,
        "daily_visitors_7d": daily_visitors,   # for sparkline, oldest first
        "organic_pct": organic_pct,
        "sources": sources,
        "top_pages": top_pages,
    }

    result["fetched_at"] = int(now)

    # Update cache
    _cache = {"data": result, "expires": now + CACHE_TTL, "fetched_at": now}

    return result


def bust_cache():
    """Force refresh on next get_traffic_data() call"""
    global _cache
    _cache["expires"] = 0
