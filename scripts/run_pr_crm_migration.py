"""
Run PR CRM Migration
Executes the database migration for PR CRM tables
"""

import psycopg2
from psycopg2 import sql
import os
from dotenv import load_dotenv

load_dotenv()

def run_migration():
    print("Starting PR CRM migration...")

    try:
        # Connect to database
        conn = psycopg2.connect(
            host=os.getenv('DB_HOST'),
            port=os.getenv('DB_PORT', 5432),
            database=os.getenv('DB_NAME'),
            user=os.getenv('DB_USER'),
            password=os.getenv('DB_PASSWORD')
        )

        conn.autocommit = False
        cursor = conn.cursor()

        print("Connected to database")

        # Read migration file
        migration_path = os.path.join(os.path.dirname(__file__), '..', 'migrations', 'add_pr_crm_tables.sql')

        with open(migration_path, 'r', encoding='utf-8') as f:
            migration_sql = f.read()

        # Execute migration
        print("Executing migration...")
        cursor.execute(migration_sql)

        # Commit changes
        conn.commit()

        print("Migration completed successfully!")

        # Verify tables created
        cursor.execute("""
            SELECT table_name
            FROM information_schema.tables
            WHERE table_schema = 'public'
            AND table_name IN ('pr_brands', 'creator_pipeline', 'email_templates', 'creator_custom_templates', 'creator_analytics')
            ORDER BY table_name
        """)

        tables = cursor.fetchall()
        print(f"\nCreated tables:")
        for table in tables:
            print(f"  - {table[0]}")

        cursor.close()
        conn.close()

        return True

    except Exception as e:
        print(f"Error during migration: {str(e)}")
        if 'conn' in locals():
            conn.rollback()
            conn.close()
        return False

if __name__ == '__main__':
    success = run_migration()
    if success:
        print("\nReady to seed brands!")
    else:
        print("\nMigration failed - please check errors above")
