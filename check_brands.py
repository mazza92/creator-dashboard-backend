#!/usr/bin/env python3
"""
Quick script to check brands in database
"""
import psycopg2
from psycopg2.extras import RealDictCursor

# Database configuration
DB_CONFIG = {
    'dbname': 'creator_db',
    'user': 'postgres',
    'password': 'Mahermaz1',
    'host': 'localhost',
    'port': '5432',
    'client_encoding': 'utf8'
}

def check_brands():
    """Check brands in pr_brands table"""
    try:
        conn = psycopg2.connect(**DB_CONFIG)
        cursor = conn.cursor(cursor_factory=RealDictCursor)

        # Count total brands
        cursor.execute("SELECT COUNT(*) as total FROM pr_brands")
        total = cursor.fetchone()['total']
        print(f"\nTotal brands in database: {total}")

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

        if brands:
            print("\nMost recent 10 brands:")
            print("=" * 80)
            for brand in brands:
                print(f"\nID: {brand['id']}")
                print(f"  Name: {brand['brand_name']}")
                print(f"  Instagram: {brand['instagram_handle']}")
                print(f"  Email: {brand['contact_email']}")
                print(f"  Category: {brand['category']}")
                print(f"  Created: {brand['created_at']}")
        else:
            print("\nNo brands found in database!")

        # Check if table structure is correct
        cursor.execute("""
            SELECT column_name, data_type
            FROM information_schema.columns
            WHERE table_name = 'pr_brands'
            ORDER BY ordinal_position
        """)

        columns = cursor.fetchall()
        print(f"\nTable structure ({len(columns)} columns):")
        for col in columns[:10]:  # Show first 10 columns
            print(f"  - {col['column_name']}: {col['data_type']}")

        cursor.close()
        conn.close()

    except Exception as e:
        print(f"\nError: {str(e)}")
        import traceback
        traceback.print_exc()

if __name__ == '__main__':
    check_brands()
