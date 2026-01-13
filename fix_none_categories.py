"""
Fix brands showing 'None' as category
- Set 'None' and empty string categories to NULL
- Infer category from brand name/description where possible
"""

import psycopg2
from psycopg2.extras import RealDictCursor
import os
from dotenv import load_dotenv

load_dotenv()

def fix_none_categories():
    conn = psycopg2.connect(os.getenv('DATABASE_URL'))
    cursor = conn.cursor(cursor_factory=RealDictCursor)

    # Step 1: Update 'None' and empty string to NULL
    print("Step 1: Cleaning up 'None' and empty categories...")
    cursor.execute("""
        UPDATE pr_brands
        SET category = NULL
        WHERE category IN ('None', '') OR category IS NULL
    """)
    affected = cursor.rowcount
    print(f"  [OK] Cleaned {affected} brands")

    # Step 2: Try to infer categories from brand names/descriptions
    print("\nStep 2: Inferring categories from brand data...")

    # Get brands with NULL categories
    cursor.execute("""
        SELECT id, brand_name, notes, instagram_handle
        FROM pr_brands
        WHERE category IS NULL
    """)
    brands = cursor.fetchall()

    category_keywords = {
        'Beauty': ['beauty', 'cosmetics', 'makeup', 'skincare', 'sephora', 'ulta'],
        'Fashion': ['fashion', 'clothing', 'apparel', 'wear', 'style', 'boutique'],
        'Tech': ['tech', 'gadget', 'electronics', 'software', 'app', 'digital'],
        'Food': ['food', 'restaurant', 'cafe', 'snack', 'beverage', 'drink'],
        'Fitness': ['fitness', 'gym', 'workout', 'yoga', 'health', 'wellness'],
        'Lifestyle': ['lifestyle', 'home', 'living', 'decor'],
    }

    updated_count = 0
    for brand in brands:
        brand_text = f"{brand['brand_name']} {brand.get('notes', '')}".lower()

        # Try to match category
        matched_category = None
        for category, keywords in category_keywords.items():
            if any(keyword in brand_text for keyword in keywords):
                matched_category = category
                break

        if matched_category:
            cursor.execute(
                "UPDATE pr_brands SET category = %s WHERE id = %s",
                (matched_category, brand['id'])
            )
            updated_count += 1
            print(f"  [OK] {brand['brand_name']}: {matched_category}")

    print(f"\n  [OK] Inferred categories for {updated_count} brands")

    # Step 3: Set remaining NULL categories to 'Lifestyle' (default)
    print("\nStep 3: Setting default category for remaining brands...")
    cursor.execute("""
        UPDATE pr_brands
        SET category = 'Lifestyle'
        WHERE category IS NULL
    """)
    default_count = cursor.rowcount
    print(f"  [OK] Set {default_count} brands to default 'Lifestyle' category")

    conn.commit()
    cursor.close()
    conn.close()

    print("\n[SUCCESS] All done! Categories fixed.")
    print("\nFinal distribution:")

    # Show final distribution
    conn = psycopg2.connect(os.getenv('DATABASE_URL'))
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    cursor.execute('SELECT category, COUNT(*) as count FROM pr_brands GROUP BY category ORDER BY count DESC')
    results = cursor.fetchall()

    for row in results:
        print(f"  {row['category']}: {row['count']} brands")

    cursor.close()
    conn.close()

if __name__ == '__main__':
    fix_none_categories()
