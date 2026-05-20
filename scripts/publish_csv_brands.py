"""Set brands from CSV to published status if not already."""
import argparse
import os
import sys

from dotenv import load_dotenv

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from scripts.enrich_brands_from_csv import load_csv_rows, clean_text, get_connection

load_dotenv()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--csv', required=True)
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()

    rows = load_csv_rows(args.csv)
    conn = get_connection()
    cur = conn.cursor()

    updated = 0
    already = 0
    not_found = 0

    for row in rows:
        slug = clean_text(row.get('Slug'))
        if not slug:
            continue

        cur.execute('SELECT id, status FROM pr_brands WHERE slug = %s', (slug,))
        r = cur.fetchone()
        if not r:
            name = clean_text(row.get('Brand Name'))
            if name:
                cur.execute(
                    'SELECT id, status FROM pr_brands WHERE LOWER(brand_name) = LOWER(%s)',
                    (name,),
                )
                r = cur.fetchone()
        if not r:
            not_found += 1
            continue

        status = (r['status'] or '').strip().lower()
        if status == 'published':
            already += 1
            continue

        if args.apply:
            cur.execute(
                """
                UPDATE pr_brands
                SET status = 'published', updated_at = CURRENT_TIMESTAMP
                WHERE id = %s
                """,
                (r['id'],),
            )
        updated += 1

    if args.apply:
        conn.commit()
        print('[OK] Changes committed')
    else:
        conn.rollback()
        print('[DRY RUN] No changes written')

    conn.close()
    print(f'Would publish / published now: {updated}')
    print(f'Already published: {already}')
    print(f'Not found: {not_found}')
    print(f'CSV rows: {len(rows)}')


if __name__ == '__main__':
    main()
