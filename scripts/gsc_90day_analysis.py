"""
Google Search Console 90-Day Analysis
Compares GSC clicks to GA4 organic sessions to detect attribution issues.

Run: python scripts/gsc_90day_analysis.py
"""
import os
import sys
from datetime import datetime, timedelta

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

# Use seo-assistant's GSC token
SEO_ASSISTANT_GSC_TOKEN = os.path.normpath(
    os.path.join(ROOT, '..', 'seo-assistant', 'tokens', 'gsc_token.json')
)

# Your verified site in GSC (main domain, not app subdomain)
SITE_URL = os.environ.get('GSC_SITE_URL', 'https://newcollab.co/')

GSC_SCOPE = 'https://www.googleapis.com/auth/webmasters.readonly'


def get_gsc_service():
    from googleapiclient.discovery import build
    from google.oauth2.credentials import Credentials
    from google.auth.transport.requests import Request

    if not os.path.isfile(SEO_ASSISTANT_GSC_TOKEN):
        print(f"ERROR: GSC token not found at {SEO_ASSISTANT_GSC_TOKEN}")
        sys.exit(1)

    creds = Credentials.from_authorized_user_file(SEO_ASSISTANT_GSC_TOKEN, [GSC_SCOPE])

    # Refresh if expired
    if creds.expired and creds.refresh_token:
        creds.refresh(Request())
        with open(SEO_ASSISTANT_GSC_TOKEN, 'w') as f:
            f.write(creds.to_json())

    return build('searchconsole', 'v1', credentials=creds)


def run_query(service, site_url, start_date, end_date, dimensions, row_limit=100):
    """Run a GSC query and return results."""
    request = {
        'startDate': start_date,
        'endDate': end_date,
        'dimensions': dimensions,
        'rowLimit': row_limit
    }

    response = service.searchanalytics().query(siteUrl=site_url, body=request).execute()
    return response.get('rows', [])


def print_report(title, rows, dimensions):
    print(f"\n{'='*80}")
    print(f"  {title}")
    print('='*80)

    if not rows:
        print("  No data found")
        return

    # Headers
    headers = dimensions + ['clicks', 'impressions', 'ctr', 'position']
    print("  " + " | ".join(f"{h:<30}" for h in headers))
    print("  " + "-" * (33 * len(headers)))

    for row in rows:
        keys = row.get('keys', [])
        vals = keys + [
            str(int(row.get('clicks', 0))),
            str(int(row.get('impressions', 0))),
            f"{row.get('ctr', 0)*100:.1f}%",
            f"{row.get('position', 0):.1f}"
        ]
        print("  " + " | ".join(f"{v:<30}" for v in vals))


def main():
    print("=" * 80)
    print("  GOOGLE SEARCH CONSOLE ANALYSIS - LAST 90 DAYS")
    print("  Site:", SITE_URL)
    print("=" * 80)

    try:
        service = get_gsc_service()
        print("\n[OK] Connected to Search Console API\n")
    except Exception as e:
        print(f"\nERROR: Failed to connect to GSC API: {e}")
        print("\nPossible issues:")
        print("1. OAuth token doesn't have GSC scope - re-run oauth login with webmasters.readonly scope")
        print("2. Site URL format is wrong - check GSC dashboard for exact URL")
        print("3. You don't have access to this property in GSC")
        sys.exit(1)

    # Date range
    end_date = datetime.now().strftime('%Y-%m-%d')
    start_date = (datetime.now() - timedelta(days=90)).strftime('%Y-%m-%d')

    print(f"  Date range: {start_date} to {end_date}")

    # ── 1. TOTALS ─────────────────────────────────────────────────────
    print("\n" + "=" * 80)
    print("  1. TOTALS (90 days)")
    print("=" * 80)

    rows = run_query(service, SITE_URL, start_date, end_date, [], row_limit=1)
    if rows:
        row = rows[0]
        total_clicks = int(row.get('clicks', 0))
        total_impressions = int(row.get('impressions', 0))
        avg_ctr = row.get('ctr', 0) * 100
        avg_position = row.get('position', 0)

        print(f"  Total Clicks:      {total_clicks:,}")
        print(f"  Total Impressions: {total_impressions:,}")
        print(f"  Average CTR:       {avg_ctr:.2f}%")
        print(f"  Average Position:  {avg_position:.1f}")
        print()
        print(f"  >>> Compare to GA4: Organic Search showed 60 sessions")
        print(f"  >>> GSC shows {total_clicks} clicks = {total_clicks - 60} clicks NOT tracked in GA4")
        if total_clicks > 60:
            pct_lost = ((total_clicks - 60) / total_clicks) * 100
            print(f"  >>> That's {pct_lost:.0f}% of organic traffic being misattributed!")

    # ── 2. TOP QUERIES ────────────────────────────────────────────────
    rows = run_query(service, SITE_URL, start_date, end_date, ['query'], row_limit=30)
    print_report("2. TOP SEARCH QUERIES", rows, ['query'])

    # ── 3. TOP PAGES ──────────────────────────────────────────────────
    rows = run_query(service, SITE_URL, start_date, end_date, ['page'], row_limit=20)
    print_report("3. TOP LANDING PAGES (from search)", rows, ['page'])

    # ── 4. BY COUNTRY ─────────────────────────────────────────────────
    rows = run_query(service, SITE_URL, start_date, end_date, ['country'], row_limit=15)
    print_report("4. BY COUNTRY", rows, ['country'])

    # ── 5. BY DEVICE ──────────────────────────────────────────────────
    rows = run_query(service, SITE_URL, start_date, end_date, ['device'], row_limit=5)
    print_report("5. BY DEVICE", rows, ['device'])

    # ── 6. WEEKLY TREND ───────────────────────────────────────────────
    rows = run_query(service, SITE_URL, start_date, end_date, ['date'], row_limit=100)

    # Aggregate by week
    weekly = {}
    for row in rows:
        date_str = row['keys'][0]
        week = datetime.strptime(date_str, '%Y-%m-%d').strftime('%Y-W%W')
        if week not in weekly:
            weekly[week] = {'clicks': 0, 'impressions': 0}
        weekly[week]['clicks'] += row.get('clicks', 0)
        weekly[week]['impressions'] += row.get('impressions', 0)

    print("\n" + "=" * 80)
    print("  6. WEEKLY TREND")
    print("=" * 80)
    print("  week                 | clicks    | impressions")
    print("  " + "-" * 50)
    for week in sorted(weekly.keys()):
        data = weekly[week]
        print(f"  {week:<20} | {int(data['clicks']):<9} | {int(data['impressions'])}")

    # ── 7. QUERY + PAGE COMBINATIONS ──────────────────────────────────
    rows = run_query(service, SITE_URL, start_date, end_date, ['query', 'page'], row_limit=25)
    print_report("7. TOP QUERY + PAGE COMBINATIONS", rows, ['query', 'page'])

    print("\n" + "=" * 80)
    print("  REPORT COMPLETE")
    print("=" * 80)


if __name__ == "__main__":
    main()
