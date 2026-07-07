"""
GA4 90-Day Traffic Analysis
Uses existing OAuth credentials from seo-assistant/tokens/ga4_token.json

Run: python scripts/ga4_90day_analysis.py > ga4_report.txt
Then paste the output into Claude for analysis.
"""
import os
import sys

# Add project root to path
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from scripts.ga4_credentials import load_user_credentials

GA4_PROPERTY_ID = os.environ.get('GA4_PROPERTY_ID', '499594920')
SCOPES = ['https://www.googleapis.com/auth/analytics.readonly']


def get_client():
    from google.analytics.data_v1beta import BetaAnalyticsDataClient
    creds = load_user_credentials(SCOPES)
    if not creds:
        print("ERROR: No OAuth credentials found. Run: python scripts/ga4_oauth_login.py")
        sys.exit(1)
    return BetaAnalyticsDataClient(credentials=creds)


def run(client, dimensions, metrics, order_metric=None, order_dim=None, limit=25, date_range="90daysAgo"):
    from google.analytics.data_v1beta.types import (
        RunReportRequest, DateRange, Dimension, Metric, OrderBy
    )
    dims = [Dimension(name=d) for d in dimensions]
    mets = [Metric(name=m) for m in metrics]
    order = []
    if order_metric:
        order = [OrderBy(metric=OrderBy.MetricOrderBy(metric_name=order_metric), desc=True)]
    elif order_dim:
        order = [OrderBy(dimension=OrderBy.DimensionOrderBy(dimension_name=order_dim))]

    req = RunReportRequest(
        property=f"properties/{GA4_PROPERTY_ID}",
        date_ranges=[DateRange(start_date=date_range, end_date="today")],
        dimensions=dims,
        metrics=mets,
        order_bys=order,
        limit=limit
    )
    return client.run_report(req)


def run_compare(client, dimensions, metrics, order_metric=None, limit=25):
    """Run with two date ranges for comparison: last 45 days vs previous 45 days"""
    from google.analytics.data_v1beta.types import (
        RunReportRequest, DateRange, Dimension, Metric, OrderBy
    )
    dims = [Dimension(name=d) for d in dimensions]
    mets = [Metric(name=m) for m in metrics]
    order = []
    if order_metric:
        order = [OrderBy(metric=OrderBy.MetricOrderBy(metric_name=order_metric), desc=True)]

    req = RunReportRequest(
        property=f"properties/{GA4_PROPERTY_ID}",
        date_ranges=[
            DateRange(start_date="45daysAgo", end_date="today"),
            DateRange(start_date="90daysAgo", end_date="46daysAgo"),
        ],
        dimensions=dims,
        metrics=mets,
        order_bys=order,
        limit=limit
    )
    return client.run_report(req)


def print_report(title, response, compare=False):
    print(f"\n{'='*70}")
    print(f"  {title}")
    print('='*70)

    headers = [d.name for d in response.dimension_headers]
    metric_names = [m.name for m in response.metric_headers]

    if compare:
        # Show metrics with (recent | prev) format
        col_headers = headers + [f"{m} (recent|prev)" for m in metric_names]
    else:
        col_headers = headers + metric_names

    print("  " + " | ".join(f"{h:<22}" for h in col_headers))
    print("  " + "-" * (25 * len(col_headers)))

    for row in response.rows:
        vals = [d.value for d in row.dimension_values]

        if compare and len(row.metric_values) == len(metric_names) * 2:
            # Interleave recent and prev values
            for i in range(len(metric_names)):
                recent = row.metric_values[i].value
                prev = row.metric_values[i + len(metric_names)].value if i + len(metric_names) < len(row.metric_values) else "0"
                vals.append(f"{recent}|{prev}")
        else:
            vals.extend([m.value for m in row.metric_values])

        print("  " + " | ".join(f"{v:<22}" for v in vals))


def main():
    print("=" * 70)
    print("  NEWCOLLAB GA4 ANALYSIS — LAST 90 DAYS")
    print("  Property ID:", GA4_PROPERTY_ID)
    print("=" * 70)

    client = get_client()
    print("\n[OK] Connected to GA4 API\n")

    # ── 1. WEEKLY TRAFFIC TREND (growth analysis) ─────────────────────
    r = run(client, ["yearWeek"],
            ["sessions", "newUsers", "activeUsers", "engagementRate", "bounceRate"],
            order_dim="yearWeek", limit=15)
    print_report("1. WEEKLY TRAFFIC TREND (oldest to newest)", r)

    # ── 2. ACQUISITION CHANNELS ───────────────────────────────────────
    r = run(client, ["sessionDefaultChannelGroup"],
            ["sessions", "newUsers", "engagementRate", "conversions", "engagedSessions"],
            order_metric="sessions")
    print_report("2. ACQUISITION CHANNELS", r)

    # ── 3. TRAFFIC SOURCES (source/medium detail) ─────────────────────
    r = run(client, ["sessionSource", "sessionMedium"],
            ["sessions", "newUsers", "engagementRate", "bounceRate"],
            order_metric="sessions", limit=20)
    print_report("3. TOP SOURCES / MEDIUM", r)

    # ── 4. LANDING PAGES ──────────────────────────────────────────────
    r = run(client, ["landingPage"],
            ["sessions", "newUsers", "bounceRate", "engagementRate", "conversions"],
            order_metric="sessions", limit=20)
    print_report("4. TOP LANDING PAGES", r)

    # ── 5. ALL PAGES (most visited) ───────────────────────────────────
    r = run(client, ["pagePath"],
            ["screenPageViews", "activeUsers", "averageSessionDuration", "engagementRate"],
            order_metric="screenPageViews", limit=25)
    print_report("5. MOST VISITED PAGES", r)

    # ── 6. GEOGRAPHY ──────────────────────────────────────────────────
    r = run(client, ["country"],
            ["sessions", "newUsers", "engagementRate", "conversions"],
            order_metric="sessions", limit=15)
    print_report("6. TOP COUNTRIES", r)

    # ── 7. DEVICE CATEGORY ────────────────────────────────────────────
    r = run(client, ["deviceCategory"],
            ["sessions", "newUsers", "engagementRate", "bounceRate", "conversions"])
    print_report("7. DEVICE BREAKDOWN", r)

    # ── 8. ALL EVENTS ─────────────────────────────────────────────────
    r = run(client, ["eventName"],
            ["eventCount", "totalUsers", "eventCountPerUser"],
            order_metric="eventCount", limit=30)
    print_report("8. ALL EVENTS (top 30)", r)

    # ── 9. NEW VS RETURNING ───────────────────────────────────────────
    r = run(client, ["newVsReturning"],
            ["sessions", "activeUsers", "engagementRate", "conversions", "screenPageViewsPerSession"])
    print_report("9. NEW VS RETURNING USERS", r)

    # ── 10. DAY OF WEEK PATTERN ───────────────────────────────────────
    r = run(client, ["dayOfWeek"],
            ["sessions", "activeUsers", "engagementRate", "conversions"],
            order_dim="dayOfWeek")
    print_report("10. ENGAGEMENT BY DAY OF WEEK (0=Sun)", r)

    # ── 11. HOUR OF DAY PATTERN ───────────────────────────────────────
    r = run(client, ["hour"],
            ["sessions", "activeUsers", "engagementRate"],
            order_dim="hour", limit=24)
    print_report("11. TRAFFIC BY HOUR (0-23)", r)

    # ── 12. FIRST USER SOURCE (attribution) ───────────────────────────
    r = run(client, ["firstUserSource", "firstUserMedium"],
            ["newUsers", "engagedSessions", "conversions"],
            order_metric="newUsers", limit=15)
    print_report("12. FIRST USER ATTRIBUTION (how new users found you)", r)

    # ── 13. PAGE PATH + TITLE (for understanding content) ─────────────
    r = run(client, ["pagePath", "pageTitle"],
            ["screenPageViews", "averageSessionDuration"],
            order_metric="screenPageViews", limit=20)
    print_report("13. PAGES WITH TITLES", r)

    # ── 14. CONVERSION EVENTS ─────────────────────────────────────────
    r = run(client, ["eventName"],
            ["conversions", "totalUsers"],
            order_metric="conversions", limit=15)
    print_report("14. CONVERSION EVENTS", r)

    # ── 15. 45-DAY COMPARISON (growth trend) ──────────────────────────
    print("\n" + "=" * 70)
    print("  15. PERIOD COMPARISON: Last 45 days vs Previous 45 days")
    print("=" * 70)

    from google.analytics.data_v1beta.types import (
        RunReportRequest, DateRange, Metric
    )
    totals = client.run_report(RunReportRequest(
        property=f"properties/{GA4_PROPERTY_ID}",
        date_ranges=[
            DateRange(start_date="45daysAgo", end_date="today"),
            DateRange(start_date="90daysAgo", end_date="46daysAgo"),
        ],
        metrics=[
            Metric(name="sessions"),
            Metric(name="newUsers"),
            Metric(name="activeUsers"),
            Metric(name="engagementRate"),
            Metric(name="bounceRate"),
            Metric(name="conversions"),
            Metric(name="screenPageViews"),
        ]
    ))

    if totals.rows:
        row = totals.rows[0]
        metrics = ["sessions", "newUsers", "activeUsers", "engagementRate", "bounceRate", "conversions", "screenPageViews"]
        print("\n  Metric              | Last 45d  | Prev 45d  | Change")
        print("  " + "-" * 60)
        for i, m in enumerate(metrics):
            recent = float(row.metric_values[i].value)
            prev = float(row.metric_values[i + len(metrics)].value) if i + len(metrics) < len(row.metric_values) else 0
            if prev > 0:
                change = ((recent - prev) / prev) * 100
                sign = "+" if change > 0 else ""
                print(f"  {m:<20} | {recent:>9.1f} | {prev:>9.1f} | {sign}{change:.1f}%")
            else:
                print(f"  {m:<20} | {recent:>9.1f} | {prev:>9.1f} | N/A")

    # ── 16. USER ENGAGEMENT METRICS ───────────────────────────────────
    r = run(client, [],
            ["engagedSessions", "userEngagementDuration", "sessionsPerUser", "screenPageViewsPerSession"])
    print("\n" + "=" * 70)
    print("  16. ENGAGEMENT SUMMARY (90 days)")
    print("=" * 70)
    if r.rows:
        row = r.rows[0]
        names = ["engagedSessions", "userEngagementDuration", "sessionsPerUser", "screenPageViewsPerSession"]
        for i, name in enumerate(names):
            val = row.metric_values[i].value
            print(f"  {name}: {val}")

    print("\n" + "=" * 70)
    print("  REPORT COMPLETE — Paste this output into Claude for analysis")
    print("=" * 70)


if __name__ == "__main__":
    main()
