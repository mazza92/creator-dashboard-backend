"""
Fetch and update brand logos for pr_brands table
Uses Clearbit Logo API and other sources to populate logo_url field
"""

import psycopg2
from psycopg2.extras import RealDictCursor
import os
from urllib.parse import urlparse, urljoin
import requests
from time import sleep

def get_db_connection():
    """Get database connection"""
    return psycopg2.connect(
        host=os.getenv('DB_HOST'),
        port=os.getenv('DB_PORT', 5432),
        database=os.getenv('DB_NAME'),
        user=os.getenv('DB_USER'),
        password=os.getenv('DB_PASSWORD')
    )

def extract_domain(website_url):
    """Extract clean domain from website URL"""
    if not website_url:
        return None

    # Remove protocol and www
    try:
        parsed = urlparse(website_url if website_url.startswith('http') else f'https://{website_url}')
        domain = parsed.netloc or parsed.path
        domain = domain.replace('www.', '')
        return domain
    except:
        return None

def get_logo_url(website_url, brand_name):
    """
    Try multiple sources to get brand logo:
    1. Clearbit Logo API (free, no API key needed)
    2. Google Favicon API
    3. DuckDuckGo Icon API
    """
    domain = extract_domain(website_url)

    if not domain:
        print(f"  ❌ No domain found for {brand_name}")
        return None

    # Method 1: Clearbit Logo API (best quality, free)
    clearbit_url = f"https://logo.clearbit.com/{domain}"
    try:
        response = requests.head(clearbit_url, timeout=5, allow_redirects=True)
        if response.status_code == 200:
            print(f"  ✅ Found logo via Clearbit: {clearbit_url}")
            return clearbit_url
    except:
        pass

    # Method 2: Google Favicon API (fallback, smaller size)
    google_url = f"https://www.google.com/s2/favicons?domain={domain}&sz=128"
    try:
        response = requests.head(google_url, timeout=5)
        if response.status_code == 200:
            print(f"  ✅ Found logo via Google: {google_url}")
            return google_url
    except:
        pass

    # Method 3: DuckDuckGo Icon API (fallback)
    duckduckgo_url = f"https://icons.duckduckgo.com/ip3/{domain}.ico"
    try:
        response = requests.head(duckduckgo_url, timeout=5)
        if response.status_code == 200:
            print(f"  ✅ Found logo via DuckDuckGo: {duckduckgo_url}")
            return duckduckgo_url
    except:
        pass

    print(f"  ❌ No logo found for {brand_name} ({domain})")
    return None

def update_brand_logos(limit=None):
    """Fetch and update logos for brands without logo_url"""
    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=RealDictCursor)

    # Get brands without logos
    query = """
        SELECT id, brand_name, website, logo_url
        FROM pr_brands
        WHERE (logo_url IS NULL OR logo_url = '')
        AND website IS NOT NULL
        AND website != ''
        ORDER BY id
    """

    if limit:
        query += f" LIMIT {limit}"

    cursor.execute(query)
    brands = cursor.fetchall()

    print(f"\n🔍 Found {len(brands)} brands without logos")
    print("=" * 60)

    updated_count = 0
    failed_count = 0

    for i, brand in enumerate(brands, 1):
        print(f"\n[{i}/{len(brands)}] Processing: {brand['brand_name']}")
        print(f"  Website: {brand['website']}")

        logo_url = get_logo_url(brand['website'], brand['brand_name'])

        if logo_url:
            try:
                cursor.execute(
                    "UPDATE pr_brands SET logo_url = %s WHERE id = %s",
                    (logo_url, brand['id'])
                )
                conn.commit()
                updated_count += 1
                print(f"  ✅ Updated database")
            except Exception as e:
                print(f"  ❌ Failed to update database: {e}")
                failed_count += 1
        else:
            failed_count += 1

        # Rate limiting - be nice to the APIs
        sleep(0.5)

    cursor.close()
    conn.close()

    print("\n" + "=" * 60)
    print(f"✅ Successfully updated: {updated_count} brands")
    print(f"❌ Failed: {failed_count} brands")
    print("=" * 60)

def show_brands_without_logos():
    """Show brands that still don't have logos"""
    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=RealDictCursor)

    cursor.execute("""
        SELECT id, brand_name, website, logo_url
        FROM pr_brands
        WHERE (logo_url IS NULL OR logo_url = '')
        ORDER BY brand_name
    """)

    brands = cursor.fetchall()

    print(f"\n📋 Brands without logos: {len(brands)}")
    for brand in brands[:20]:  # Show first 20
        print(f"  • {brand['brand_name']} - {brand['website'] or 'No website'}")

    cursor.close()
    conn.close()

if __name__ == "__main__":
    import sys

    print("🎨 Brand Logo Fetcher")
    print("=" * 60)

    if len(sys.argv) > 1:
        if sys.argv[1] == "list":
            show_brands_without_logos()
        else:
            try:
                limit = int(sys.argv[1])
                update_brand_logos(limit=limit)
            except ValueError:
                print("Usage: python fetch_brand_logos.py [limit]")
                print("       python fetch_brand_logos.py list")
    else:
        # Default: update all brands
        update_brand_logos()
