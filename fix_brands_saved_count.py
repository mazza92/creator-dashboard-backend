"""
Script to recalculate brands_saved_count for all creators
This counts the actual number of brands in their pipeline
"""
import os
import psycopg2
from psycopg2.extras import RealDictCursor
from dotenv import load_dotenv

load_dotenv()

def fix_brands_saved_count():
    try:
        conn = psycopg2.connect(os.getenv('DATABASE_URL'))
        cursor = conn.cursor(cursor_factory=RealDictCursor)

        print("\n" + "="*60)
        print("RECALCULATING BRANDS_SAVED_COUNT")
        print("="*60 + "\n")

        # Get all creators
        cursor.execute("SELECT id FROM creators")
        creators = cursor.fetchall()

        print(f"Found {len(creators)} creators\n")

        updated_count = 0
        for creator in creators:
            creator_id = creator['id']

            # Count actual brands in pipeline
            cursor.execute("""
                SELECT COUNT(*) as actual_count
                FROM creator_pipeline
                WHERE creator_id = %s
            """, (creator_id,))

            actual_count = cursor.fetchone()['actual_count']

            # Get current stored count
            cursor.execute("""
                SELECT brands_saved_count
                FROM creators
                WHERE id = %s
            """, (creator_id,))

            current_count = cursor.fetchone()['brands_saved_count'] or 0

            # Update if different
            if actual_count != current_count:
                cursor.execute("""
                    UPDATE creators
                    SET brands_saved_count = %s
                    WHERE id = %s
                """, (actual_count, creator_id))

                print(f"Creator {creator_id}: {current_count} → {actual_count}")
                updated_count += 1

        conn.commit()
        cursor.close()
        conn.close()

        print(f"\n✅ Updated {updated_count} creators")
        print("="*60 + "\n")

    except Exception as e:
        print(f"\n❌ Error: {str(e)}\n")

if __name__ == '__main__':
    fix_brands_saved_count()
