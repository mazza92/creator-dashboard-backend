"""
Enrich pr_brands from a semicolon-delimited CSV export.
Only fills fields that are NULL or empty in the database (never overwrites existing data).

Usage:
  python scripts/enrich_brands_from_csv.py --csv "C:\\Users\\maher\\Downloads\\brands-toadd.csv"
  python scripts/enrich_brands_from_csv.py --csv path/to/file.csv --apply
  python scripts/enrich_brands_from_csv.py --csv path/to/file.csv --apply --insert-new
"""

import argparse
import csv
import json
import os
import re
import sys
from datetime import datetime

import psycopg2
from psycopg2.extras import RealDictCursor
from dotenv import load_dotenv

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

load_dotenv()

EMAIL_RE = re.compile(r'^[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}$')

# CSV column -> database column
FIELD_MAP = {
    'Brand Name': 'brand_name',
    'Application URL': 'application_form_url',
    'Contact Email': 'contact_email',
    'Website': 'website',
    'Logo URL': 'logo_url',
    'Cover Image': 'cover_image_url',
    'Description': 'description',
    'Instagram': 'instagram_handle',
    'TikTok': 'tiktok_handle',
    'YouTube': 'youtube_handle',
    'Category': 'category',
    'Product Types': 'product_types',
    'App Method': 'application_method',
    'App Requirements': 'application_requirements',
    'Notes': 'notes',
    'Success Stories': 'success_stories',
    'Source URL': 'source_url',
    'SEO Title': 'seo_title',
    'SEO Description': 'seo_description',
    'Collab Type': 'collaboration_type',
    'Payment Offered': 'payment_offered',
    'Status': 'status',
}

INT_FIELDS = {'Min Followers': 'min_followers', 'Max Followers': 'max_followers'}
FLOAT_FIELDS = {
    'Response Rate': 'response_rate',
    'Avg Response (days)': 'avg_response_time_days',
    'Avg Product Value': 'avg_product_value',
}
COUNT_FIELDS = {
    'Total Applications': 'total_applications',
    'Total Responses': 'total_responses',
}
BOOL_FIELDS = {
    'Has App Form': 'has_application_form',
    'Accepting PR': 'accepting_pr',
    'Featured': 'is_featured',
    'Open PR': 'open_pr_featured',
    'Premium': 'is_premium',
}
JSON_LIST_FIELDS = {
    'Niches': 'niches',
    'Platforms': 'platforms',
    'Regions': 'regions',
}


def get_connection():
    url = os.getenv('DATABASE_URL')
    if url:
        return psycopg2.connect(url, cursor_factory=RealDictCursor)
    return psycopg2.connect(
        host=os.getenv('DB_HOST'),
        port=os.getenv('DB_PORT', 5432),
        database=os.getenv('DB_NAME'),
        user=os.getenv('DB_USER'),
        password=os.getenv('DB_PASSWORD'),
        cursor_factory=RealDictCursor,
    )


def is_empty(value):
    if value is None:
        return True
    if isinstance(value, str):
        return not value.strip()
    return False


def clean_text(value):
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def parse_email(raw):
    if not raw:
        return None
    text = str(raw).strip()
    # Take first address when multiple are listed
    for part in re.split(r'[;,]', text):
        candidate = part.strip().strip('"').strip("'")
        if not candidate:
            continue
        candidate = candidate.lower()
        if EMAIL_RE.match(candidate):
            return candidate
    return None


def parse_bool(raw):
    if raw is None:
        return None
    text = str(raw).strip().lower()
    if not text:
        return None
    if text in ('yes', 'true', '1', 'y'):
        return True
    if text in ('no', 'false', '0', 'n'):
        return False
    return None


def parse_int(raw):
    if raw is None or str(raw).strip() == '':
        return None
    try:
        return int(float(str(raw).strip().replace(',', '')))
    except ValueError:
        return None


def parse_float(raw):
    if raw is None or str(raw).strip() == '':
        return None
    try:
        return float(str(raw).strip().replace('%', '').replace(',', ''))
    except ValueError:
        return None


def parse_json_list(raw):
    if not raw or not str(raw).strip():
        return None
    text = str(raw).strip()
    if text.startswith('['):
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            pass
    items = [p.strip() for p in re.split(r'[,;|]', text) if p.strip()]
    return items or None


def normalize_row(row):
    """Normalize DictReader keys (strip BOM/whitespace)."""
    return {(k or '').strip(): (v or '').strip() if isinstance(v, str) else v for k, v in row.items()}


def csv_value_to_db(csv_col, raw):
    if csv_col == 'Contact Email':
        return parse_email(raw)
    if csv_col in BOOL_FIELDS:
        return parse_bool(raw)
    if csv_col in INT_FIELDS:
        return parse_int(raw)
    if csv_col in FLOAT_FIELDS:
        return parse_float(raw)
    if csv_col in JSON_LIST_FIELDS:
        return parse_json_list(raw)
    return clean_text(raw)


def build_enrichment(existing, row):
    """Return dict of db_column -> value to set (only for empty DB fields)."""
    updates = {}

    for csv_col, db_col in FIELD_MAP.items():
        val = csv_value_to_db(csv_col, row.get(csv_col))
        if val is not None and is_empty(existing.get(db_col)):
            updates[db_col] = val

    for csv_col, db_col in INT_FIELDS.items():
        val = csv_value_to_db(csv_col, row.get(csv_col))
        if val is not None and existing.get(db_col) is None:
            updates[db_col] = val

    for csv_col, db_col in FLOAT_FIELDS.items():
        val = csv_value_to_db(csv_col, row.get(csv_col))
        if val is not None and existing.get(db_col) is None:
            updates[db_col] = val

    for csv_col, db_col in COUNT_FIELDS.items():
        val = parse_int(row.get(csv_col))
        if val is not None and existing.get(db_col) in (None, 0):
            updates[db_col] = val

    for csv_col, db_col in BOOL_FIELDS.items():
        val = parse_bool(row.get(csv_col))
        if val is not None and existing.get(db_col) is None:
            updates[db_col] = val

    for csv_col, db_col in JSON_LIST_FIELDS.items():
        val = parse_json_list(row.get(csv_col))
        if val is not None and is_empty(existing.get(db_col)):
            updates[db_col] = json.dumps(val)

    # Infer has_application_form from application URL
    app_url = updates.get('application_form_url') or existing.get('application_form_url')
    if app_url and existing.get('has_application_form') is None and 'has_application_form' not in updates:
        updates['has_application_form'] = True

    return updates


def load_csv_rows(csv_path):
    rows = []
    with open(csv_path, 'r', encoding='utf-8-sig', newline='') as f:
        reader = csv.DictReader(f, delimiter=';')
        for raw in reader:
            row = normalize_row(raw)
            slug = clean_text(row.get('Slug'))
            name = clean_text(row.get('Brand Name'))
            if not slug and not name:
                continue
            rows.append(row)
    return rows


def insert_brand(cursor, row):
    slug = clean_text(row.get('Slug'))
    name = clean_text(row.get('Brand Name')) or slug
    if not slug:
        return None

    fields = {
        'brand_name': name,
        'slug': slug,
        'status': clean_text(row.get('Status')) or 'draft',
    }

    for csv_col, db_col in FIELD_MAP.items():
        if db_col in ('brand_name', 'slug', 'status'):
            continue
        val = csv_value_to_db(csv_col, row.get(csv_col))
        if val is not None:
            fields[db_col] = val

    for csv_col, db_col in {**INT_FIELDS, **FLOAT_FIELDS, **COUNT_FIELDS}.items():
        val = csv_value_to_db(csv_col, row.get(csv_col))
        if val is not None:
            fields[db_col] = val

    for csv_col, db_col in BOOL_FIELDS.items():
        val = parse_bool(row.get(csv_col))
        if val is not None:
            fields[db_col] = val

    for csv_col, db_col in JSON_LIST_FIELDS.items():
        val = parse_json_list(row.get(csv_col))
        if val is not None:
            fields[db_col] = json.dumps(val)

    if fields.get('application_form_url'):
        fields.setdefault('has_application_form', True)

    cols = list(fields.keys()) + ['created_at', 'updated_at']
    placeholders = ['%s'] * len(fields) + ['CURRENT_TIMESTAMP', 'CURRENT_TIMESTAMP']
    values = list(fields.values())

    cursor.execute(
        f"INSERT INTO pr_brands ({', '.join(cols)}) VALUES ({', '.join(placeholders)}) RETURNING id",
        values,
    )
    return cursor.fetchone()['id']


def main():
    parser = argparse.ArgumentParser(description='Enrich pr_brands from CSV')
    parser.add_argument('--csv', required=True, help='Path to semicolon-delimited CSV')
    parser.add_argument('--apply', action='store_true', help='Write changes to database')
    parser.add_argument('--insert-new', action='store_true', help='Insert brands not found by slug')
    args = parser.parse_args()

    if not os.path.isfile(args.csv):
        print(f'File not found: {args.csv}')
        sys.exit(1)

    rows = load_csv_rows(args.csv)
    print(f'Loaded {len(rows)} rows from CSV')

    conn = get_connection()
    cursor = conn.cursor()

    stats = {
        'matched': 0,
        'enriched': 0,
        'no_changes': 0,
        'not_found': 0,
        'inserted': 0,
        'skipped_no_slug': 0,
        'email_added': 0,
        'fields_updated': 0,
    }
    samples = {'enriched': [], 'not_found': [], 'email_added': []}

    for row in rows:
        slug = clean_text(row.get('Slug'))
        if not slug:
            stats['skipped_no_slug'] += 1
            continue

        cursor.execute('SELECT * FROM pr_brands WHERE slug = %s', (slug,))
        existing = cursor.fetchone()

        if not existing:
            name = clean_text(row.get('Brand Name'))
            if name:
                cursor.execute(
                    'SELECT * FROM pr_brands WHERE LOWER(brand_name) = LOWER(%s)',
                    (name,),
                )
                existing = cursor.fetchone()

        if not existing:
            stats['not_found'] += 1
            if len(samples['not_found']) < 15:
                samples['not_found'].append(slug)
            if args.apply and args.insert_new:
                cursor.execute('SAVEPOINT brand_insert')
                try:
                    insert_brand(cursor, row)
                    cursor.execute('RELEASE SAVEPOINT brand_insert')
                    stats['inserted'] += 1
                except psycopg2.Error as e:
                    cursor.execute('ROLLBACK TO SAVEPOINT brand_insert')
                    print(f'Insert skipped for {slug}: {e}')
            continue

        stats['matched'] += 1
        updates = build_enrichment(existing, row)

        if not updates:
            stats['no_changes'] += 1
            continue

        stats['enriched'] += 1
        stats['fields_updated'] += len(updates)

        if 'contact_email' in updates:
            stats['email_added'] += 1
            if len(samples['email_added']) < 20:
                samples['email_added'].append({
                    'slug': slug,
                    'name': existing.get('brand_name'),
                    'email': updates['contact_email'],
                })

        if len(samples['enriched']) < 10:
            samples['enriched'].append({'slug': slug, 'fields': list(updates.keys())})

        if args.apply:
            set_clause = ', '.join(f'{col} = %s' for col in updates)
            params = list(updates.values()) + [existing['id']]
            cursor.execute(
                f'UPDATE pr_brands SET {set_clause}, updated_at = CURRENT_TIMESTAMP WHERE id = %s',
                params,
            )

    if args.apply:
        conn.commit()
        print('\n[OK] Changes committed to database')
    else:
        conn.rollback()
        print('\n[DRY RUN] No changes written (use --apply to commit)')

    conn.close()

    print('\n--- Summary ---')
    for key, val in stats.items():
        print(f'  {key}: {val}')

    if samples['email_added']:
        print('\nSample contact emails added:')
        for s in samples['email_added'][:10]:
            print(f"  {s['slug']}: {s['email']}")

    if samples['enriched']:
        print('\nSample enrichments:')
        for s in samples['enriched'][:8]:
            print(f"  {s['slug']}: {', '.join(s['fields'])}")

    if samples['not_found'] and not args.insert_new:
        print('\nNot in DB (use --apply --insert-new to create):')
        print('  ' + ', '.join(samples['not_found'][:12]))
        if stats['not_found'] > 12:
            print(f'  ... and {stats["not_found"] - 12} more')


if __name__ == '__main__':
    main()
