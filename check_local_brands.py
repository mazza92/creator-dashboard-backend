#!/usr/bin/env python3
"""
Check brands in local database using direct connection string
"""
import psycopg2
from psycopg2.extras import RealDictCursor

def check_brands():
    """Check brands in pr_brands table"""
    try:
        # Use connection string to avoid encoding issues
        conn_string = "postgresql://postgres:Mahermaz1@localhost:5432/creator_db"
        conn = psycopg2.connect(conn_string)
        cursor = conn.cursor(cursor_factory=RealDictCursor)

        # Count total brands
        cursor.execute("SELECT COUNT(*) as total FROM pr_brands")
        result = cursor.fetchone()
        total = result['total'] if result else 0

        print("\n" + "=" * 60)
        print(f"LOCAL DATABASE: creator_db")
        print("=" * 60)
        print(f"Total brands: {total}")
        print("=" * 60)

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
        else:
            print("\n>>> NO BRANDS FOUND IN LOCAL DATABASE <<<")
            print("\nThis confirms brands were saved to Supabase, not locally.")
            print("\nTo fix:")
            print("  python scrape_to_local.py beauty")

        cursor.close()
        conn.close()

    except Exception as e:
        print(f"\nError connecting to local database: {str(e)}")
        print("\nMake sure PostgreSQL is running locally")
        import traceback
        traceback.print_exc()

if __name__ == '__main__':
    check_brands()
