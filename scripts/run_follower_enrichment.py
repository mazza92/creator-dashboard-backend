#!/usr/bin/env python3
"""
Standalone script to run follower enrichment on all published brands.

Run this directly on the server:
    python scripts/run_follower_enrichment.py

Or with arguments:
    python scripts/run_follower_enrichment.py --limit 500 --batch-size 100
"""
import os
import sys
import argparse
import time

# Add parent directory for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
load_dotenv()

from services.bulk_ai_enricher import bulk_enrich_follower_requirements, get_follower_enrichment_stats


def main():
    parser = argparse.ArgumentParser(description='Run AI follower enrichment on published brands')
    parser.add_argument('--limit', type=int, default=2500, help='Total brands to process (default: 2500)')
    parser.add_argument('--batch-size', type=int, default=100, help='Brands per batch (default: 100)')
    parser.add_argument('--delay', type=float, default=0.5, help='Delay between API calls in seconds (default: 0.5)')
    parser.add_argument('--dry-run', action='store_true', help='Preview changes without updating DB')
    parser.add_argument('--only-null', action='store_true', help='Only enrich brands with NULL values')
    args = parser.parse_args()

    print("=" * 60)
    print("FOLLOWER REQUIREMENTS ENRICHMENT")
    print("=" * 60)

    # Get current stats
    print("\nCurrent stats:")
    stats = get_follower_enrichment_stats()
    if 'error' in stats:
        print(f"ERROR: {stats['error']}")
        return 1

    print(f"  Total published brands: {stats.get('total_published', 0)}")
    print(f"  micro_friendly=true: {stats.get('micro_friendly_true', 0)}")
    print(f"  micro_friendly=false: {stats.get('micro_friendly_false', 0)}")
    print(f"  Needs enrichment (NULL values): {stats.get('needs_enrichment', 0)}")

    total_to_process = min(args.limit, stats.get('total_published', 0))
    num_batches = (total_to_process + args.batch_size - 1) // args.batch_size

    print(f"\nConfiguration:")
    print(f"  Total to process: {total_to_process}")
    print(f"  Batch size: {args.batch_size}")
    print(f"  Number of batches: {num_batches}")
    print(f"  Rate limit delay: {args.delay}s")
    print(f"  Dry run: {args.dry_run}")
    print(f"  Only NULL values: {args.only_null}")

    if not args.dry_run:
        print("\nStarting in 3 seconds... (Ctrl+C to cancel)")
        time.sleep(3)

    total_stats = {'processed': 0, 'updated': 0, 'errors': 0, 'skipped': 0}
    processed_so_far = 0

    for batch_num in range(num_batches):
        remaining = total_to_process - processed_so_far
        batch_limit = min(args.batch_size, remaining)

        if batch_limit <= 0:
            break

        print(f"\n{'=' * 40}")
        print(f"BATCH {batch_num + 1}/{num_batches} (processing {batch_limit} brands)")
        print(f"{'=' * 40}")

        batch_stats = bulk_enrich_follower_requirements(
            limit=batch_limit,
            offset=processed_so_far,
            only_null_values=args.only_null,
            rate_limit_delay=args.delay,
            dry_run=args.dry_run
        )

        # Accumulate stats
        total_stats['processed'] += batch_stats.get('processed', 0)
        total_stats['updated'] += batch_stats.get('updated', 0)
        total_stats['errors'] += batch_stats.get('errors', 0)
        total_stats['skipped'] += batch_stats.get('skipped', 0)

        processed_so_far += batch_stats.get('processed', 0)

        print(f"\nBatch {batch_num + 1} complete:")
        print(f"  Processed: {batch_stats.get('processed', 0)}")
        print(f"  Updated: {batch_stats.get('updated', 0)}")
        print(f"  Errors: {batch_stats.get('errors', 0)}")

        # Brief pause between batches
        if batch_num < num_batches - 1:
            print("\nPausing 2s before next batch...")
            time.sleep(2)

    print("\n" + "=" * 60)
    print("ENRICHMENT COMPLETE")
    print("=" * 60)
    print(f"  Total processed: {total_stats['processed']}")
    print(f"  Total updated: {total_stats['updated']}")
    print(f"  Total errors: {total_stats['errors']}")
    print(f"  Total skipped: {total_stats['skipped']}")

    # Get final stats
    print("\nFinal stats:")
    final_stats = get_follower_enrichment_stats()
    if 'error' not in final_stats:
        print(f"  micro_friendly=true: {final_stats.get('micro_friendly_true', 0)}")
        print(f"  micro_friendly=false: {final_stats.get('micro_friendly_false', 0)}")

    return 0


if __name__ == '__main__':
    sys.exit(main())
