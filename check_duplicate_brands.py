#!/usr/bin/env python3
"""
Check for duplicate brands in Supabase database
"""
import psycopg2
from psycopg2.extras import RealDictCursor
import os
from dotenv import load_dotenv

load_dotenv()

def check_duplicates():
    """Check for duplicate brands"""
    try:
        database_url = os.getenv('DATABASE_URL')
        conn = psycopg2.connect(database_url)
        cursor = conn.cursor(cursor_factory=RealDictCursor)

        print("\n" + "=" * 60)
        print("CHECKING FOR DUPLICATE BRANDS")
        print("=" * 60)

        # Check for duplicate Instagram handles
        cursor.execute("""
            SELECT
                instagram_handle,
                COUNT(*) as count,
                STRING_AGG(brand_name, ', ') as brand_names,
                STRING_AGG(id::text, ', ') as ids
            FROM pr_brands
            WHERE instagram_handle IS NOT NULL
            GROUP BY instagram_handle
            HAVING COUNT(*) > 1
            ORDER BY COUNT(*) DESC
        """)

        duplicates = cursor.fetchall()

        if duplicates:
            print(f"\nFound {len(duplicates)} duplicate Instagram handles:\n")
            for dup in duplicates:
                print(f"Instagram: {dup['instagram_handle']}")
                print(f"  Count: {dup['count']}")
                print(f"  Brands: {dup['brand_names']}")
                print(f"  IDs: {dup['ids']}")
                print()
        else:
            print("\nNo duplicate Instagram handles found.")

        # Check for similar brand names (case-insensitive)
        cursor.execute("""
            SELECT
                LOWER(brand_name) as name_lower,
                COUNT(*) as count,
                STRING_AGG(brand_name, ', ') as brand_names,
                STRING_AGG(instagram_handle, ', ') as handles
            FROM pr_brands
            WHERE brand_name IS NOT NULL
            GROUP BY LOWER(brand_name)
            HAVING COUNT(*) > 1
            ORDER BY COUNT(*) DESC
            LIMIT 10
        """)

        name_duplicates = cursor.fetchall()

        if name_duplicates:
            print(f"\nFound {len(name_duplicates)} duplicate brand names (case-insensitive):\n")
            for dup in name_duplicates:
                print(f"Brand Name: {dup['name_lower']}")
                print(f"  Count: {dup['count']}")
                print(f"  Variations: {dup['brand_names']}")
                print(f"  Handles: {dup['handles']}")
                print()
        else:
            print("\nNo duplicate brand names found.")

        # Show all Instagram handles to check formatting
        cursor.execute("""
            SELECT instagram_handle, brand_name
            FROM pr_brands
            ORDER BY created_at DESC
            LIMIT 20
        """)

        handles = cursor.fetchall()
        print("\nLast 20 Instagram handles (to check formatting):\n")
        for h in handles:
            print(f"  {h['instagram_handle']:30} - {h['brand_name']}")

        cursor.close()
        conn.close()

        print("\n" + "=" * 60)

    except Exception as e:
        print(f"\nError: {str(e)}")
        import traceback
        traceback.print_exc()

if __name__ == '__main__':
    check_duplicates()
