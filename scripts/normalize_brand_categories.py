"""
Normalize pr_brands.category to canonical slugs (pet not pets, etc.).

Usage:
  python scripts/normalize_brand_categories.py
  python scripts/normalize_brand_categories.py --apply
"""
import argparse
import os
import sys
from collections import Counter

from dotenv import load_dotenv

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from brand_categories import normalize_category, category_label
from scripts.enrich_brands_from_csv import get_connection

load_dotenv()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()

    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT id, brand_name, category
        FROM pr_brands
        WHERE category IS NOT NULL AND TRIM(category) != ''
        """
    )
    rows = cur.fetchall()

    changes = []
    for row in rows:
        raw = row['category']
        canon = normalize_category(raw)
        if canon and canon != raw:
            changes.append((canon, row['id'], row['brand_name'], raw))

    print(f'Brands to normalize: {len(changes)} / {len(rows)}')
    by_transition = Counter((c[3], c[0]) for c in changes)
    for (old, new), n in by_transition.most_common(25):
        print(f'  {n:4d}  {old!r} -> {new!r} ({category_label(new)})')

    if not args.apply:
        print('\nDry run. Re-run with --apply to update.')
        cur.close()
        conn.close()
        return

    for canon, brand_id, _name, _raw in changes:
        cur.execute('UPDATE pr_brands SET category = %s WHERE id = %s', (canon, brand_id))

    conn.commit()
    cur.close()
    conn.close()
    print(f'\nUpdated {len(changes)} brands.')


if __name__ == '__main__':
    main()
