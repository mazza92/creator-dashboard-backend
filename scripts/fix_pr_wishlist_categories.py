#!/usr/bin/env python3
"""
Fix existing pr_wishlist entries that don't match valid PR_CATEGORIES.
This script will:
1. Find all creators with pr_wishlist entries
2. Map any invalid categories to valid PR_CATEGORIES
3. Update pr_wishlist with only valid categories

Usage:
    python scripts/fix_pr_wishlist_categories.py
"""

import sys
import os
import json

# Add parent directory to path to import app modules
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import get_db_connection, map_niche_to_pr_categories, VALID_PR_CATEGORIES
from psycopg2.extras import RealDictCursor

def fix_pr_wishlist_categories():
    """Fix pr_wishlist entries to only contain valid PR categories"""
    conn = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor(cursor_factory=RealDictCursor)
        
        # Get all creators with pr_wishlist
        cursor.execute("""
            SELECT id, pr_wishlist
            FROM creators
            WHERE pr_wishlist IS NOT NULL 
              AND pr_wishlist::text != '[]'
              AND pr_wishlist::text != 'null'
        """)
        
        creators = cursor.fetchall()
        print(f"Found {len(creators)} creators with pr_wishlist entries")
        
        fixed_count = 0
        for creator in creators:
            creator_id = creator['id']
            pr_wishlist_raw = creator['pr_wishlist']
            
            try:
                # Parse pr_wishlist
                if isinstance(pr_wishlist_raw, str):
                    try:
                        pr_wishlist = json.loads(pr_wishlist_raw) if pr_wishlist_raw else []
                    except:
                        pr_wishlist = []
                elif isinstance(pr_wishlist_raw, list):
                    pr_wishlist = pr_wishlist_raw
                else:
                    pr_wishlist = []
                
                if not pr_wishlist:
                    continue
                
                # Map to valid categories
                mapped_categories = []
                for category in pr_wishlist:
                    category_str = str(category).strip()
                    category_lower = category_str.lower()
                    
                    # Direct match
                    if category_str in VALID_PR_CATEGORIES:
                        if category_str not in mapped_categories:
                            mapped_categories.append(category_str)
                    else:
                        # Try fuzzy matching
                        for valid_category in VALID_PR_CATEGORIES:
                            if valid_category.lower() == category_lower:
                                if valid_category not in mapped_categories:
                                    mapped_categories.append(valid_category)
                                break
                        else:
                            # Use the mapping function
                            mapped = map_niche_to_pr_categories(category_str)
                            mapped_categories.extend([c for c in mapped if c not in mapped_categories])
                
                # Remove duplicates and ensure valid
                mapped_categories = [c for c in mapped_categories if c in VALID_PR_CATEGORIES]
                
                # Only update if categories changed
                original_set = set(str(c).strip() for c in pr_wishlist)
                mapped_set = set(mapped_categories)
                
                if original_set != mapped_set:
                    # Update pr_wishlist
                    try:
                        cursor.execute("""
                            UPDATE creators 
                            SET pr_wishlist = %s::jsonb
                            WHERE id = %s
                        """, (json.dumps(mapped_categories, ensure_ascii=False), creator_id))
                        fixed_count += 1
                        print(f"✓ Fixed creator {creator_id}: {list(original_set)} → {mapped_categories}")
                    except Exception as e:
                        print(f"✗ Error updating creator {creator_id}: {str(e)}")
                        # Try table approach
                        try:
                            cursor.execute("DELETE FROM pr_wishlist WHERE creator_id = %s", (creator_id,))
                            for category in mapped_categories:
                                cursor.execute(
                                    "INSERT INTO pr_wishlist (creator_id, category) VALUES (%s, %s) ON CONFLICT DO NOTHING",
                                    (creator_id, category)
                                )
                            fixed_count += 1
                            print(f"✓ Fixed creator {creator_id} (table): {list(original_set)} → {mapped_categories}")
                        except Exception as table_error:
                            print(f"✗ Error updating creator {creator_id} (table): {str(table_error)}")
                
            except Exception as e:
                print(f"✗ Error processing creator {creator_id}: {str(e)}")
                import traceback
                traceback.print_exc()
        
        conn.commit()
        print(f"\n✅ Successfully fixed {fixed_count} out of {len(creators)} creators")
        
        cursor.close()
        
    except Exception as e:
        print(f"❌ Error during fix: {str(e)}")
        if conn:
            conn.rollback()
        raise
    finally:
        if conn:
            conn.close()

if __name__ == '__main__':
    print("🔧 Fixing pr_wishlist categories to match valid PR_CATEGORIES...")
    fix_pr_wishlist_categories()
    print("✨ Fix complete!")

