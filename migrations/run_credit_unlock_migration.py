#!/usr/bin/env python3
"""
Credit Unlock System Migration Script
=====================================
Run this on STAGING FIRST before production!

Usage:
    python run_credit_unlock_migration.py --dry-run    # Preview only, no changes
    python run_credit_unlock_migration.py              # Run with confirmation prompt

The script will show backfill counts before committing.
"""

import os
import sys
import argparse
import psycopg2
from dotenv import load_dotenv

# Load environment variables from parent directory
load_dotenv(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), '.env'))


def get_db_connection():
    """Get database connection from environment variables."""
    return psycopg2.connect(
        host=os.getenv('DB_HOST'),
        port=os.getenv('DB_PORT', 5432),
        database=os.getenv('DB_NAME'),
        user=os.getenv('DB_USER'),
        password=os.getenv('DB_PASSWORD')
    )


def run_migration(dry_run=False):
    """Run the credit unlock migration with verification."""

    conn = get_db_connection()
    cur = conn.cursor()

    try:
        print("\n" + "="*60)
        print("CREDIT UNLOCK SYSTEM MIGRATION")
        print("="*60)

        # Step 1: Add columns to creators table
        print("\n[1/5] Adding unlock columns to creators table...")
        cur.execute("""
            ALTER TABLE creators ADD COLUMN IF NOT EXISTS unlocks_remaining INT DEFAULT 5;
            ALTER TABLE creators ADD COLUMN IF NOT EXISTS unlocks_tier VARCHAR(20) DEFAULT 'free';
            ALTER TABLE creators ADD COLUMN IF NOT EXISTS unlocks_reset_at TIMESTAMP;
        """)
        print("      [OK] Columns added (or already exist)")

        # Step 2: Create brand_unlocks table
        print("\n[2/5] Creating brand_unlocks table...")
        cur.execute("""
            CREATE TABLE IF NOT EXISTS brand_unlocks (
                id BIGSERIAL PRIMARY KEY,
                creator_id INT NOT NULL REFERENCES creators(id) ON DELETE CASCADE,
                brand_id INT NOT NULL,
                unlocked_at TIMESTAMP DEFAULT NOW(),
                UNIQUE(creator_id, brand_id)
            );
            CREATE INDEX IF NOT EXISTS idx_brand_unlocks_creator ON brand_unlocks(creator_id);
            CREATE INDEX IF NOT EXISTS idx_brand_unlocks_brand ON brand_unlocks(brand_id);
        """)
        print("      [OK] Table and indexes created (or already exist)")

        # Step 3: Set Pro users to unlimited
        print("\n[3/5] Setting Pro/Elite users to unlimited tier...")
        cur.execute("""
            UPDATE creators
            SET unlocks_tier = 'pro',
                unlocks_remaining = NULL,
                unlocks_reset_at = NULL
            WHERE subscription_tier IN ('pro', 'elite')
        """)
        pro_count = cur.rowcount
        print(f"      [OK] {pro_count} Pro/Elite users set to unlimited")

        # Step 4: Set Free users to 5 unlocks
        print("\n[4/5] Setting Free users to 5 unlocks (30-day reset)...")
        cur.execute("""
            UPDATE creators
            SET unlocks_tier = 'free',
                unlocks_remaining = 5,
                unlocks_reset_at = NOW() + INTERVAL '30 days'
            WHERE subscription_tier IS NULL
               OR subscription_tier = 'free'
               OR subscription_tier = ''
        """)
        free_count = cur.rowcount
        print(f"      [OK] {free_count} Free users set to 5 unlocks")

        # Step 5: Backfill existing pitches as unlocks
        print("\n[5/5] Backfilling existing pitches as permanent unlocks...")

        # Drop any lingering FK constraint from previous runs (we don't want FK on brand_id)
        cur.execute("""
            ALTER TABLE brand_unlocks DROP CONSTRAINT IF EXISTS brand_unlocks_brand_id_fkey
        """)

        # First, count what we're about to backfill (for verification)
        # Only count brands that exist in pr_brands to avoid orphan issues
        cur.execute("""
            SELECT
                COUNT(DISTINCT (cp.creator_id, cp.brand_id)) as unlock_count,
                COUNT(DISTINCT cp.creator_id) as creator_count
            FROM creator_pipeline cp
            INNER JOIN pr_brands pb ON pb.id = cp.brand_id
            WHERE cp.brand_id IS NOT NULL
              AND (cp.pitched_at IS NOT NULL OR cp.send_confirmed = TRUE)
        """)
        expected = cur.fetchone()
        expected_unlocks = expected[0] if expected else 0
        expected_creators = expected[1] if expected else 0

        # Now do the actual backfill - only include brands that exist in pr_brands
        cur.execute("""
            INSERT INTO brand_unlocks (creator_id, brand_id, unlocked_at)
            SELECT DISTINCT
                cp.creator_id,
                cp.brand_id,
                MIN(COALESCE(cp.pitched_at, cp.created_at)) as unlocked_at
            FROM creator_pipeline cp
            INNER JOIN pr_brands pb ON pb.id = cp.brand_id
            WHERE cp.brand_id IS NOT NULL
              AND (cp.pitched_at IS NOT NULL OR cp.send_confirmed = TRUE)
            GROUP BY cp.creator_id, cp.brand_id
            ON CONFLICT (creator_id, brand_id) DO NOTHING
        """)
        backfilled = cur.rowcount

        # Verification output
        print("\n" + "-"*60)
        print("VERIFICATION RESULTS")
        print("-"*60)
        print(f"Backfilled {backfilled} unlocks across {expected_creators} creators")
        print(f"(Expected from pipeline: {expected_unlocks} unique brand-creator pairs)")

        # Show tier breakdown
        cur.execute("""
            SELECT unlocks_tier, COUNT(*)
            FROM creators
            WHERE unlocks_tier IS NOT NULL
            GROUP BY unlocks_tier
            ORDER BY unlocks_tier
        """)
        tier_counts = cur.fetchall()
        print("\nTier breakdown:")
        for tier, count in tier_counts:
            print(f"  {tier}: {count} creators")

        # Show sample unlocks for sanity check
        cur.execute("""
            SELECT u.email, COUNT(bu.id) as unlock_count
            FROM brand_unlocks bu
            JOIN creators c ON c.id = bu.creator_id
            JOIN users u ON u.id = c.user_id
            GROUP BY u.id, u.email
            ORDER BY unlock_count DESC
            LIMIT 5
        """)
        top_users = cur.fetchall()
        if top_users:
            print("\nTop 5 users by unlock count (sanity check):")
            for email, count in top_users:
                if email:
                    masked = email[:3] + "***" + email[email.index('@'):] if '@' in email else email[:6] + "***"
                    print(f"  {masked}: {count} brands unlocked")
                else:
                    print(f"  [no email]: {count} brands unlocked")

        print("-"*60)

        if dry_run:
            print("\n[DRY RUN] Rolling back all changes")
            conn.rollback()
            print("   No changes were committed to the database.")
        else:
            print("\n[!] Review the numbers above carefully!")
            confirm = input("Type 'COMMIT' to save changes, anything else to rollback: ")

            if confirm.strip() == 'COMMIT':
                conn.commit()
                print("\n[SUCCESS] Migration COMMITTED successfully!")
            else:
                conn.rollback()
                print("\n[CANCELLED] Migration ROLLED BACK - no changes saved.")

    except Exception as e:
        conn.rollback()
        print(f"\n[ERROR] {e}")
        print("   Migration rolled back.")
        raise
    finally:
        cur.close()
        conn.close()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Run Credit Unlock System migration')
    parser.add_argument('--dry-run', action='store_true',
                        help='Preview changes without committing')
    args = parser.parse_args()

    run_migration(dry_run=args.dry_run)
