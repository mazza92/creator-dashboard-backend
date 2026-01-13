#!/usr/bin/env python3
"""
One-time script to backfill pr_wishlist from niche for all existing creators.
Run this script once to populate PR wishlist for creators who have a niche but empty PR wishlist.

Usage:
    python scripts/backfill_pr_wishlist_from_niche.py
"""

import sys
import os
import json

# Add parent directory to path to import app modules
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import get_db_connection, sync_niche_to_pr_wishlist
from psycopg2.extras import RealDictCursor

def backfill_pr_wishlist():
    """Backfill pr_wishlist from niche for all creators"""
    conn = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor(cursor_factory=RealDictCursor)
        
        # Get all creators with niche but empty pr_wishlist
        cursor.execute("""
            SELECT id, niche, pr_wishlist
            FROM creators
            WHERE niche IS NOT NULL 
              AND niche != ''
              AND (
                pr_wishlist IS NULL 
                OR pr_wishlist::text = '[]'
                OR pr_wishlist::text = 'null'
                OR jsonb_array_length(pr_wishlist) = 0
              )
        """)
        
        creators = cursor.fetchall()
        print(f"Found {len(creators)} creators with niche but empty PR wishlist")
        
        updated_count = 0
        for creator in creators:
            creator_id = creator['id']
            niche = creator['niche']
            
            try:
                # Pass niche as-is to sync function - it will handle parsing and mapping
                # The sync_niche_to_pr_wishlist function now includes map_niche_to_pr_categories
                # which will convert niche values to valid PR_CATEGORIES
                sync_niche_to_pr_wishlist(creator_id, niche, conn)
                updated_count += 1
                print(f"✓ Synced niche '{niche}' to pr_wishlist for creator {creator_id}")
            except Exception as e:
                print(f"✗ Error syncing creator {creator_id}: {str(e)}")
                import traceback
                traceback.print_exc()
        
        conn.commit()
        print(f"\n✅ Successfully synced {updated_count} out of {len(creators)} creators")
        
        # Verify results
        cursor.execute("""
            SELECT 
                COUNT(*) as total_creators,
                COUNT(CASE WHEN niche IS NOT NULL AND niche != '' THEN 1 END) as creators_with_niche,
                COUNT(CASE WHEN pr_wishlist IS NOT NULL AND pr_wishlist::text != '[]' AND pr_wishlist::text != 'null' THEN 1 END) as creators_with_pr_wishlist
            FROM creators
        """)
        stats = cursor.fetchone()
        print(f"\n📊 Statistics:")
        print(f"   Total creators: {stats['total_creators']}")
        print(f"   Creators with niche: {stats['creators_with_niche']}")
        print(f"   Creators with PR wishlist: {stats['creators_with_pr_wishlist']}")
        
        cursor.close()
        
    except Exception as e:
        print(f"❌ Error during backfill: {str(e)}")
        if conn:
            conn.rollback()
        raise
    finally:
        if conn:
            conn.close()

if __name__ == '__main__':
    print("🚀 Starting PR wishlist backfill from niche...")
    backfill_pr_wishlist()
    print("✨ Backfill complete!")

