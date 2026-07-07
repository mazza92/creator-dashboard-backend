#!/usr/bin/env python3
"""
Batch enrich cover images for all published brands with empty cover_image_url.
Calls the AI enricher endpoint for each brand.

Usage:
    python scripts/enrich_cover_images.py
"""

import os
import sys
import time
import requests
import psycopg2
from psycopg2.extras import RealDictCursor
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Database connection
def get_db_connection():
    return psycopg2.connect(
        host=os.getenv('DB_HOST'),
        port=os.getenv('DB_PORT', 5432),
        database=os.getenv('DB_NAME'),
        user=os.getenv('DB_USER'),
        password=os.getenv('DB_PASSWORD')
    )

# API configuration
API_BASE = os.getenv('API_BASE_URL', 'http://localhost:5000')
ADMIN_TOKEN = os.getenv('ADMIN_TOKEN', 'pr-hunter-admin-2026')

def get_brands_without_cover_image():
    """Fetch all published brands with empty cover_image_url"""
    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=RealDictCursor)

    cursor.execute("""
        SELECT id, brand_name, website, slug
        FROM pr_brands
        WHERE (cover_image_url IS NULL OR cover_image_url = '')
        AND COALESCE(status, 'published') = 'published'
        AND website IS NOT NULL
        ORDER BY id ASC
    """)

    brands = cursor.fetchall()
    cursor.close()
    conn.close()

    return brands

def enrich_brand(brand_id, brand_name):
    """Call the AI enricher endpoint for a single brand"""
    try:
        response = requests.post(
            f"{API_BASE}/api/admin/brands/{brand_id}/enrich",
            headers={
                'Content-Type': 'application/json',
                'X-Admin-Token': ADMIN_TOKEN
            },
            timeout=60
        )

        if response.status_code == 200:
            data = response.json()
            if data.get('success'):
                cover_image = data.get('data', {}).get('cover_image_url')
                if cover_image:
                    return True, f"Found cover image: {cover_image[:60]}..."
                else:
                    return False, "No suitable cover image found"
            else:
                return False, data.get('error', 'Unknown error')
        else:
            return False, f"HTTP {response.status_code}"

    except requests.Timeout:
        return False, "Request timed out"
    except Exception as e:
        return False, str(e)

def main():
    print("=" * 60)
    print("BATCH COVER IMAGE ENRICHMENT")
    print("=" * 60)

    # Get brands without cover images
    print("\nFetching published brands with empty cover_image_url...")
    brands = get_brands_without_cover_image()

    if not brands:
        print("No brands found that need cover image enrichment.")
        return

    print(f"Found {len(brands)} brands to process.\n")

    # Process each brand
    success_count = 0
    fail_count = 0

    for i, brand in enumerate(brands, 1):
        brand_id = brand['id']
        brand_name = brand['brand_name']

        print(f"[{i}/{len(brands)}] Enriching: {brand_name} (ID: {brand_id})...", end=" ", flush=True)

        success, message = enrich_brand(brand_id, brand_name)

        if success:
            print(f"OK - {message}")
            success_count += 1
        else:
            print(f"SKIP - {message}")
            fail_count += 1

        # Rate limiting - wait 2 seconds between requests to avoid overwhelming the API
        if i < len(brands):
            time.sleep(2)

    # Summary
    print("\n" + "=" * 60)
    print("SUMMARY")
    print("=" * 60)
    print(f"Total processed: {len(brands)}")
    print(f"Successfully enriched: {success_count}")
    print(f"Failed/No image found: {fail_count}")
    print("=" * 60)

if __name__ == "__main__":
    main()
