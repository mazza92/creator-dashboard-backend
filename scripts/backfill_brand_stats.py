"""
Backfill response_rate and avg_response_time_days for brands with empty stats.
Values are deterministic per slug based on category vertical averages.
"""
import argparse
import os
import sys

from dotenv import load_dotenv

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from brand_stats_synthesis import (
    generate_synthetic_stats,
    needs_synthetic_avg_days,
    needs_synthetic_response_rate,
)
from scripts.enrich_brands_from_csv import get_connection

load_dotenv()


def main():
    parser = argparse.ArgumentParser(description='Backfill synthetic brand stats where empty')
    parser.add_argument('--apply', action='store_true', help='Write changes to database')
    parser.add_argument('--limit', type=int, default=0, help='Max brands to update (0 = all)')
    args = parser.parse_args()

    conn = get_connection()
    cur = conn.cursor()

    cur.execute("""
        SELECT id, slug, brand_name, category, response_rate, avg_response_time_days
        FROM pr_brands
        ORDER BY id
    """)
    rows = cur.fetchall()

    to_update = []
    for row in rows:
        rate = row['response_rate']
        days = row['avg_response_time_days']
        if not needs_synthetic_response_rate(rate) and not needs_synthetic_avg_days(days):
            continue
        synth_rate, synth_days = generate_synthetic_stats(row['slug'], row['category'])
        new_rate = synth_rate if needs_synthetic_response_rate(rate) else rate
        new_days = synth_days if needs_synthetic_avg_days(days) else days
        to_update.append((new_rate, new_days, row['id'], row['brand_name'], row['slug']))

    if args.limit:
        to_update = to_update[: args.limit]

    print(f'Brands needing stats backfill: {len(to_update)} / {len(rows)}')

    for new_rate, new_days, brand_id, name, slug in to_update[:15]:
        print(f'  - {name} ({slug}): {new_rate}% / {new_days}d')

    if len(to_update) > 15:
        print(f'  ... and {len(to_update) - 15} more')

    if not args.apply:
        print('\nDry run. Re-run with --apply to update the database.')
        cur.close()
        conn.close()
        return

    updated = 0
    for new_rate, new_days, brand_id, _name, _slug in to_update:
        cur.execute(
            """
            UPDATE pr_brands
            SET response_rate = %s, avg_response_time_days = %s
            WHERE id = %s
            """,
            (new_rate, new_days, brand_id),
        )
        updated += 1

    conn.commit()
    cur.close()
    conn.close()
    print(f'\nUpdated {updated} brands.')


if __name__ == '__main__':
    main()
