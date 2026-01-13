#!/usr/bin/env python3
"""
Check brands in Supabase database (production)
"""
import psycopg2
from psycopg2.extras import RealDictCursor
import os
from dotenv import load_dotenv

load_dotenv()

def check_brands():
    """Check brands in pr_brands table on Supabase"""
    try:
        # Use the same DATABASE_URL that Flask app uses
        database_url = os.getenv('DATABASE_URL')

        if not database_url:
            print("ERROR: DATABASE_URL not found in .env")
            return

        print("\n" + "=" * 60)
        print("SUPABASE DATABASE (Production)")
        print("=" * 60)
        print(f"Connecting to: {database_url.split('@')[1].split('/')[0]}")  # Show host without password
        print("=" * 60)

        conn = psycopg2.connect(database_url)
        cursor = conn.cursor(cursor_factory=RealDictCursor)

        # Count total brands
        cursor.execute("SELECT COUNT(*) as total FROM pr_brands")
        result = cursor.fetchone()
        total = result['total'] if result else 0

        print(f"\nTotal brands in Supabase: {total}")

        if total > 0:
            # Get recent brands
            cursor.execute("""
                SELECT
                    id,
                    brand_name,
                    instagram_handle,
                    contact_email,
                    category,
                    created_at
                FROM pr_brands
                ORDER BY created_at DESC
                LIMIT 10
            """)

            brands = cursor.fetchall()

            print("\nMost recent 10 brands:\n")
            for i, brand in enumerate(brands, 1):
                print(f"{i}. {brand['brand_name']}")
                print(f"   Instagram: {brand['instagram_handle'] or 'N/A'}")
                print(f"   Email: {brand['contact_email'] or 'N/A'}")
                print(f"   Category: {brand['category'] or 'N/A'}")
                print(f"   Created: {brand['created_at']}")
                print()

            print("=" * 60)
            print(f"✓ {total} brands available in production database")
            print("=" * 60)
        else:
            print("\nNo brands found in Supabase!")
            print("This is unexpected - the scraper reported 22 brands.")

        cursor.close()
        conn.close()

    except Exception as e:
        print(f"\nError connecting to Supabase: {str(e)}")
        import traceback
        traceback.print_exc()

if __name__ == '__main__':
    check_brands()
