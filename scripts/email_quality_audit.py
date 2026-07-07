#!/usr/bin/env python3
"""
Email Quality Audit Script for PR Brands Database

This script analyzes and validates all brand contact emails to ensure high quality.
It identifies:
- Invalid email formats
- Generic/catch-all emails (info@, hello@, contact@)
- Bounced emails (from outreach logs)
- Unverified emails that need validation

Usage:
    python scripts/email_quality_audit.py --analyze    # Just analyze, don't verify
    python scripts/email_quality_audit.py --verify     # Verify with NeverBounce (costs credits)
    python scripts/email_quality_audit.py --report     # Generate detailed report
    python scripts/email_quality_audit.py --fix        # Update database with findings
"""

import os
import re
import sys
import json
import argparse
import requests
from datetime import datetime, timedelta
from collections import defaultdict
import psycopg2
from psycopg2.extras import RealDictCursor
from dotenv import load_dotenv
import time

load_dotenv()

# Email patterns that indicate lower quality (generic addresses)
GENERIC_PREFIXES = [
    'info', 'hello', 'contact', 'support', 'help', 'team', 'admin',
    'sales', 'marketing', 'general', 'enquiries', 'inquiries', 'office',
    'mail', 'webmaster', 'noreply', 'no-reply', 'donotreply'
]

# High-quality PR-specific prefixes
PR_PREFIXES = [
    'pr', 'press', 'media', 'influencer', 'creator', 'collab',
    'partnerships', 'talent', 'gifting', 'seeding', 'brand'
]

# Personal name patterns (higher quality)
NAME_PATTERN = re.compile(r'^[a-z]+\.[a-z]+@|^[a-z]{2,}@(?!info|hello|contact|support)')

# Valid email regex
EMAIL_REGEX = re.compile(r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$')


def get_db_connection():
    """Get database connection"""
    return psycopg2.connect(
        host=os.getenv('DB_HOST'),
        port=os.getenv('DB_PORT', 5432),
        database=os.getenv('DB_NAME'),
        user=os.getenv('DB_USER'),
        password=os.getenv('DB_PASSWORD')
    )


def analyze_email_quality(email):
    """
    Analyze email quality without external verification.
    Returns quality assessment dict.
    """
    if not email:
        return {
            'status': 'missing',
            'score': 0,
            'issues': ['No email provided']
        }

    email = email.lower().strip()
    issues = []
    score = 50  # Start at neutral

    # Check format
    if not EMAIL_REGEX.match(email):
        return {
            'status': 'invalid',
            'score': 0,
            'issues': ['Invalid email format']
        }

    local_part = email.split('@')[0]
    domain = email.split('@')[1]

    # Check for generic prefixes (lower quality)
    for prefix in GENERIC_PREFIXES:
        if local_part == prefix or local_part.startswith(f"{prefix}.") or local_part.startswith(f"{prefix}_"):
            issues.append(f"Generic prefix: {prefix}")
            score -= 20
            break

    # Check for PR-specific prefixes (higher quality)
    for prefix in PR_PREFIXES:
        if local_part == prefix or local_part.startswith(f"{prefix}.") or local_part.startswith(f"{prefix}_"):
            score += 30
            break

    # Check for personal name pattern (higher quality)
    if NAME_PATTERN.match(email):
        score += 20

    # Check for suspicious patterns
    if len(local_part) < 2:
        issues.append("Very short local part")
        score -= 10

    if local_part.count('.') > 3:
        issues.append("Excessive dots in local part")
        score -= 5

    # Check domain quality
    free_domains = ['gmail.com', 'yahoo.com', 'hotmail.com', 'outlook.com', 'aol.com']
    if domain in free_domains:
        issues.append("Free email domain (not company email)")
        score -= 15

    # Normalize score
    score = max(0, min(100, score))

    # Determine status
    if score >= 70:
        status = 'likely_valid'
    elif score >= 40:
        status = 'needs_verification'
    else:
        status = 'risky'

    return {
        'status': status,
        'score': score,
        'issues': issues
    }


def verify_email_neverbounce(email, api_key):
    """
    Verify email using NeverBounce API
    """
    if not api_key:
        return {'status': 'unknown', 'score': 25, 'source': 'no_api_key'}

    url = "https://api.neverbounce.com/v4/single/check"
    params = {
        'key': api_key,
        'email': email
    }

    try:
        response = requests.get(url, params=params, timeout=30)
        response.raise_for_status()
        data = response.json()

        result = data.get('result', 'unknown')

        status_map = {
            'valid': ('valid', 100),
            'invalid': ('invalid', 0),
            'disposable': ('invalid', 0),
            'catchall': ('catch-all', 50),
            'unknown': ('unknown', 25)
        }

        status, score = status_map.get(result, ('unknown', 25))

        return {
            'status': status,
            'score': score,
            'raw_result': result,
            'source': 'neverbounce'
        }

    except Exception as e:
        print(f"  NeverBounce error for {email}: {str(e)}")
        return {'status': 'error', 'score': 25, 'error': str(e)}


def get_bounced_emails(conn):
    """Get emails that have bounced from outreach logs"""
    cursor = conn.cursor(cursor_factory=RealDictCursor)

    cursor.execute("""
        SELECT DISTINCT email_sent_to, COUNT(*) as bounce_count
        FROM brand_outreach_log
        WHERE status = 'bounced'
        GROUP BY email_sent_to
    """)

    bounced = {row['email_sent_to'].lower(): row['bounce_count'] for row in cursor.fetchall()}
    cursor.close()
    return bounced


def run_analysis(conn, verbose=False):
    """Run full email quality analysis"""
    cursor = conn.cursor(cursor_factory=RealDictCursor)

    print("\n" + "="*60)
    print("EMAIL QUALITY AUDIT REPORT")
    print("="*60)
    print(f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")

    # Get all brands with emails
    cursor.execute("""
        SELECT id, brand_name, contact_email, category,
               email_status, email_quality_score, email_bounce_count
        FROM pr_brands
        WHERE contact_email IS NOT NULL AND contact_email != ''
        ORDER BY brand_name
    """)
    brands = cursor.fetchall()

    # Get bounced emails from logs
    bounced_emails = get_bounced_emails(conn)

    # Analysis buckets
    stats = {
        'total': len(brands),
        'valid': 0,
        'invalid_format': 0,
        'generic': 0,
        'pr_specific': 0,
        'bounced': 0,
        'risky': 0,
        'needs_verification': 0
    }

    issues_by_brand = []

    print(f"Total brands with emails: {stats['total']}\n")
    print("-"*60)

    # Analyze each email
    for brand in brands:
        email = brand['contact_email'].lower().strip()
        analysis = analyze_email_quality(email)

        # Check if bounced
        if email in bounced_emails:
            analysis['status'] = 'bounced'
            analysis['score'] = 0
            analysis['issues'].append(f"Bounced {bounced_emails[email]} time(s)")
            stats['bounced'] += 1

        # Categorize
        if analysis['status'] == 'invalid':
            stats['invalid_format'] += 1
        elif analysis['status'] == 'bounced':
            pass  # Already counted
        elif analysis['score'] >= 70:
            stats['valid'] += 1
        elif analysis['score'] < 40:
            stats['risky'] += 1
        else:
            stats['needs_verification'] += 1

        # Check for generic
        local_part = email.split('@')[0]
        if any(local_part == p or local_part.startswith(f"{p}.") for p in GENERIC_PREFIXES):
            stats['generic'] += 1

        # Check for PR-specific
        if any(local_part == p or local_part.startswith(f"{p}.") for p in PR_PREFIXES):
            stats['pr_specific'] += 1

        if analysis['issues'] or analysis['score'] < 50:
            issues_by_brand.append({
                'id': brand['id'],
                'brand_name': brand['brand_name'],
                'email': email,
                'category': brand['category'],
                'score': analysis['score'],
                'status': analysis['status'],
                'issues': analysis['issues']
            })

    # Print summary
    print("\nSUMMARY STATISTICS")
    print("-"*40)
    print(f"✅ Likely Valid (score >= 70):    {stats['valid']:>5} ({stats['valid']/stats['total']*100:.1f}%)")
    print(f"⚠️  Needs Verification (40-69):   {stats['needs_verification']:>5} ({stats['needs_verification']/stats['total']*100:.1f}%)")
    print(f"❌ Risky (score < 40):            {stats['risky']:>5} ({stats['risky']/stats['total']*100:.1f}%)")
    print(f"🚫 Invalid Format:                {stats['invalid_format']:>5} ({stats['invalid_format']/stats['total']*100:.1f}%)")
    print(f"📧 Bounced (from logs):           {stats['bounced']:>5} ({stats['bounced']/stats['total']*100:.1f}%)")
    print()
    print(f"📋 Generic emails (info@, etc):   {stats['generic']:>5} ({stats['generic']/stats['total']*100:.1f}%)")
    print(f"🎯 PR-specific (pr@, press@):     {stats['pr_specific']:>5} ({stats['pr_specific']/stats['total']*100:.1f}%)")

    # Print problematic emails
    if issues_by_brand:
        print("\n" + "="*60)
        print("PROBLEMATIC EMAILS (sorted by score)")
        print("="*60)

        issues_by_brand.sort(key=lambda x: x['score'])

        for item in issues_by_brand[:50]:  # Top 50 worst
            print(f"\n[Score: {item['score']:>3}] {item['brand_name']}")
            print(f"         Email: {item['email']}")
            print(f"         Category: {item['category']}")
            if item['issues']:
                print(f"         Issues: {', '.join(item['issues'])}")

    cursor.close()

    return {
        'stats': stats,
        'issues': issues_by_brand
    }


def verify_emails(conn, limit=None, dry_run=True):
    """
    Verify emails using NeverBounce API
    Only verifies emails that haven't been verified recently
    """
    api_key = os.getenv('NEVERBOUNCE_API_KEY')
    if not api_key:
        print("ERROR: NEVERBOUNCE_API_KEY not set in environment")
        return

    cursor = conn.cursor(cursor_factory=RealDictCursor)

    # Get unverified emails (or verified more than 90 days ago)
    query = """
        SELECT id, brand_name, contact_email
        FROM pr_brands
        WHERE contact_email IS NOT NULL
          AND contact_email != ''
          AND (email_status = 'unverified' OR email_status IS NULL
               OR email_verified_at < NOW() - INTERVAL '90 days')
        ORDER BY
            CASE WHEN email_status = 'unverified' OR email_status IS NULL THEN 0 ELSE 1 END,
            id
    """
    if limit:
        query += f" LIMIT {limit}"

    cursor.execute(query)
    brands = cursor.fetchall()

    print(f"\nFound {len(brands)} emails to verify")

    if dry_run:
        print("\n[DRY RUN] Would verify these emails:")
        for brand in brands[:20]:
            print(f"  - {brand['brand_name']}: {brand['contact_email']}")
        if len(brands) > 20:
            print(f"  ... and {len(brands) - 20} more")
        return

    print("\nStarting verification (1 second delay between requests)...")

    verified_count = 0
    for i, brand in enumerate(brands):
        print(f"\n[{i+1}/{len(brands)}] Verifying: {brand['contact_email']}")

        result = verify_email_neverbounce(brand['contact_email'], api_key)

        print(f"  Result: {result['status']} (score: {result['score']})")

        # Update database
        cursor.execute("""
            UPDATE pr_brands
            SET email_status = %s,
                email_quality_score = %s,
                email_verified_at = NOW(),
                email_verification_source = %s,
                updated_at = NOW()
            WHERE id = %s
        """, (result['status'], result['score'], result.get('source', 'neverbounce'), brand['id']))

        verified_count += 1

        # Commit every 10 records
        if verified_count % 10 == 0:
            conn.commit()
            print(f"  [Committed {verified_count} records]")

        # Rate limiting
        time.sleep(1)

    conn.commit()
    print(f"\n✅ Verified {verified_count} emails")

    cursor.close()


def update_from_analysis(conn, analysis_results, dry_run=True):
    """Update database with analysis findings"""
    cursor = conn.cursor()

    if dry_run:
        print("\n[DRY RUN] Would update these records:")

    updated = 0
    for item in analysis_results['issues']:
        if item['status'] in ('invalid', 'bounced') or item['score'] < 30:
            if dry_run:
                print(f"  - {item['brand_name']}: {item['email']} -> {item['status']} (score: {item['score']})")
            else:
                cursor.execute("""
                    UPDATE pr_brands
                    SET email_status = %s,
                        email_quality_score = %s,
                        updated_at = NOW()
                    WHERE id = %s
                """, (item['status'], item['score'], item['id']))
                updated += 1

    if not dry_run:
        conn.commit()
        print(f"\n✅ Updated {updated} records")

    cursor.close()


def generate_report(conn, output_file=None):
    """Generate a detailed CSV report"""
    cursor = conn.cursor(cursor_factory=RealDictCursor)

    cursor.execute("""
        SELECT
            id, brand_name, contact_email, category,
            email_status, email_quality_score, email_bounce_count,
            email_verified_at, email_verification_source,
            created_at, updated_at
        FROM pr_brands
        WHERE contact_email IS NOT NULL AND contact_email != ''
        ORDER BY email_quality_score ASC NULLS FIRST, brand_name
    """)
    brands = cursor.fetchall()

    if output_file:
        import csv
        with open(output_file, 'w', newline='', encoding='utf-8') as f:
            writer = csv.DictWriter(f, fieldnames=brands[0].keys())
            writer.writeheader()
            writer.writerows(brands)
        print(f"\n✅ Report saved to: {output_file}")
    else:
        print("\n" + "="*80)
        print("FULL EMAIL QUALITY REPORT")
        print("="*80)
        for brand in brands:
            analysis = analyze_email_quality(brand['contact_email'])
            print(f"\n{brand['brand_name']} ({brand['category']})")
            print(f"  Email: {brand['contact_email']}")
            print(f"  DB Status: {brand['email_status'] or 'unverified'}")
            print(f"  DB Score: {brand['email_quality_score'] or 'N/A'}")
            print(f"  Analysis Score: {analysis['score']}")
            if analysis['issues']:
                print(f"  Issues: {', '.join(analysis['issues'])}")

    cursor.close()


def main():
    parser = argparse.ArgumentParser(description='Email Quality Audit for PR Brands')
    parser.add_argument('--analyze', action='store_true', help='Run analysis (no external verification)')
    parser.add_argument('--verify', action='store_true', help='Verify with NeverBounce API')
    parser.add_argument('--verify-limit', type=int, default=100, help='Limit for verification (default: 100)')
    parser.add_argument('--report', action='store_true', help='Generate detailed report')
    parser.add_argument('--report-file', type=str, help='Output CSV file for report')
    parser.add_argument('--fix', action='store_true', help='Update database with findings')
    parser.add_argument('--dry-run', action='store_true', default=True, help='Dry run (default: True)')
    parser.add_argument('--execute', action='store_true', help='Actually execute changes')

    args = parser.parse_args()

    # If --execute is passed, disable dry run
    dry_run = not args.execute

    if not any([args.analyze, args.verify, args.report, args.fix]):
        # Default to analyze
        args.analyze = True

    conn = get_db_connection()

    try:
        if args.analyze:
            results = run_analysis(conn, verbose=True)

            if args.fix:
                update_from_analysis(conn, results, dry_run=dry_run)

        if args.verify:
            verify_emails(conn, limit=args.verify_limit, dry_run=dry_run)

        if args.report:
            generate_report(conn, output_file=args.report_file)

    finally:
        conn.close()


if __name__ == '__main__':
    main()
