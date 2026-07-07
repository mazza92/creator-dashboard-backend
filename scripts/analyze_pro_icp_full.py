"""
Complete ICP Analysis - Database + Stripe CSV
Run: python scripts/analyze_pro_icp_full.py
"""

import os
import sys
import csv
import json
from collections import Counter, defaultdict
from datetime import datetime

sys.stdout.reconfigure(encoding='utf-8')
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import psycopg2
from psycopg2.extras import RealDictCursor
from dotenv import load_dotenv

load_dotenv()

# Country code to name mapping
COUNTRY_NAMES = {
    'US': 'United States',
    'AU': 'Australia',
    'NG': 'Nigeria',
    'FI': 'Finland',
    'SE': 'Sweden',
    'IT': 'Italy',
    'CA': 'Canada',
    'PT': 'Portugal',
    'ZA': 'South Africa',
    'FR': 'France',
    'EG': 'Egypt',
    'GB': 'United Kingdom',
    'IN': 'India',
    'DE': 'Germany',
}


def get_db_connection():
    return psycopg2.connect(os.getenv('DATABASE_URL'), cursor_factory=RealDictCursor)


def parse_json(value, default=None):
    if value is None:
        return default
    if isinstance(value, (list, dict)):
        return value
    if isinstance(value, str):
        try:
            return json.loads(value)
        except:
            return default
    return default


def parse_spend(value):
    """Parse European-style currency (comma decimal) to float"""
    if not value:
        return 0.0
    return float(value.replace(',', '.').replace('"', ''))


def categorize_followers(count):
    if count is None or count == 0:
        return "Unknown"
    if count < 1000:
        return "Nano (<1K)"
    if count < 5000:
        return "Micro (1K-5K)"
    if count < 10000:
        return "Micro (5K-10K)"
    if count < 50000:
        return "Mid (10K-50K)"
    if count < 100000:
        return "Mid (50K-100K)"
    return "Macro (100K+)"


def analyze():
    # Load Stripe CSV
    stripe_path = r"C:\Users\maher\Downloads\unified_customers (1).csv"
    stripe_customers = {}

    with open(stripe_path, 'r', encoding='utf-8') as f:
        reader = csv.DictReader(f)
        for row in reader:
            email = row['Email'].lower().strip()
            spend = parse_spend(row['Total Spend'])
            payments = int(row['Payment Count'])
            country = row['Address Country']
            city = row['Address City']

            # Only count as paid if they actually paid
            if payments > 0 and spend > 0:
                stripe_customers[email] = {
                    'name': row['Name'],
                    'country': country,
                    'city': city,
                    'spend': spend,
                    'payments': payments,
                    'created': row['Created (UTC)']
                }

    print(f"\n{'='*70}")
    print(f"COMPLETE ICP ANALYSIS - Stripe + Database")
    print(f"{'='*70}")
    print(f"\nStripe paying customers found: {len(stripe_customers)}")

    # Load database data
    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute('''
        SELECT
            c.id,
            c.username,
            u.email,
            u.first_name,
            u.last_name,
            c.niche,
            c.regions,
            c.social_links,
            c.followers_count,
            c.primary_age_range,
            c.subscription_tier,
            c.subscription_status,
            c.subscription_started_at,
            c.pitches_sent_total,
            c.has_media_kit
        FROM creators c
        JOIN users u ON c.user_id = u.id
        WHERE c.subscription_tier IN ('pro', 'elite')
          AND c.subscription_status = 'active'
        ORDER BY c.subscription_started_at DESC
    ''')

    db_users = cursor.fetchall()
    cursor.close()
    conn.close()

    # Merge data
    merged_users = []
    for user in db_users:
        email = user['email'].lower().strip()
        stripe_data = stripe_customers.get(email, {})

        merged_users.append({
            **user,
            'stripe_country': stripe_data.get('country', ''),
            'stripe_city': stripe_data.get('city', ''),
            'stripe_spend': stripe_data.get('spend', 0),
            'stripe_payments': stripe_data.get('payments', 0),
            'stripe_name': stripe_data.get('name', '')
        })

    # Analysis
    countries = Counter()
    niches = Counter()
    platforms = Counter()
    follower_ranges = Counter()
    audience_ages = Counter()
    total_followers = []
    platform_followers = defaultdict(list)
    total_revenue = 0
    has_media_kit = 0

    print(f"\n{'='*70}")
    print("📋 PAID USER DETAILS (with Stripe data)")
    print(f"{'='*70}\n")

    print(f"{'Username':<22} {'Country':<8} {'City':<18} {'Followers':<10} {'Spend':<8} {'Niche'}")
    print("-" * 100)

    for user in merged_users:
        username = user['username'] or 'N/A'
        country = user['stripe_country'] or '??'
        city = user['stripe_city'] or ''
        followers = user['followers_count'] or 0
        spend = user['stripe_spend']
        total_revenue += spend

        niche_raw = parse_json(user['niche'], [])
        niche_str = ', '.join(niche_raw[:2]) if niche_raw else 'Not set'

        print(f"{username:<22} {country:<8} {city:<18} {followers:<10,} ${spend:<7.2f} {niche_str}")

        # Aggregate
        country_name = COUNTRY_NAMES.get(country, country) if country else 'Unknown'
        countries[country_name] += 1

        for n in niche_raw:
            niches[n.lower()] += 1

        social = parse_json(user['social_links'], [])
        for link in social:
            if isinstance(link, dict):
                platform = (link.get('platform') or 'Unknown').lower()
                fc = link.get('followersCount') or link.get('followers_count') or 0
                platforms[platform] += 1
                if fc:
                    platform_followers[platform].append(fc)

        if followers:
            total_followers.append(followers)
            follower_ranges[categorize_followers(followers)] += 1
        else:
            follower_ranges['Unknown'] += 1

        age = user['primary_age_range']
        if age:
            audience_ages[age] += 1

        if user['has_media_kit']:
            has_media_kit += 1

    # Print Analysis
    print(f"\n\n{'='*70}")
    print("📊 AGGREGATE ANALYSIS")
    print(f"{'='*70}")

    print(f"\n💰 REVENUE: ${total_revenue:.2f} total from {len(merged_users)} subscribers")
    print(f"   Average LTV: ${total_revenue/len(merged_users):.2f}")

    print("\n\n🌍 COUNTRIES (from Stripe billing):")
    for country, count in countries.most_common(15):
        pct = (count / len(merged_users)) * 100
        bar = '█' * int(pct / 5)
        print(f"   {country:<20} {count:>2} ({pct:>5.1f}%) {bar}")

    print("\n\n👥 FOLLOWER DISTRIBUTION:")
    for range_name, count in sorted(follower_ranges.items(), key=lambda x: x[1], reverse=True):
        pct = (count / len(merged_users)) * 100
        bar = '█' * int(pct / 5)
        print(f"   {range_name:<20} {count:>2} ({pct:>5.1f}%) {bar}")

    if total_followers:
        avg = sum(total_followers) / len(total_followers)
        sorted_fc = sorted(total_followers)
        median = sorted_fc[len(sorted_fc) // 2]
        print(f"\n   📈 Average: {avg:,.0f}")
        print(f"   📊 Median: {median:,}")
        print(f"   📉 Range: {min(total_followers):,} - {max(total_followers):,}")

    print("\n\n🎯 TOP NICHES:")
    for niche, count in niches.most_common(12):
        pct = (count / len(merged_users)) * 100
        bar = '█' * int(pct / 5)
        print(f"   {niche:<20} {count:>2} ({pct:>5.1f}%) {bar}")

    print("\n\n📱 PLATFORMS:")
    for platform, count in platforms.most_common():
        if platform:
            pct = (count / len(merged_users)) * 100
            avg_fc = sum(platform_followers[platform]) / len(platform_followers[platform]) if platform_followers[platform] else 0
            print(f"   {platform:<15} {count:>2} users ({pct:>5.1f}%) - Avg: {avg_fc:>8,.0f} followers")

    if audience_ages:
        print("\n\n👶 TARGET AUDIENCE AGE:")
        for age, count in audience_ages.most_common():
            pct = (count / len(merged_users)) * 100
            bar = '█' * int(pct / 5)
            print(f"   {age:<15} {count:>2} ({pct:>5.1f}%) {bar}")

    mk_pct = (has_media_kit / len(merged_users)) * 100
    print(f"\n\n📄 MEDIA KIT ADOPTION: {has_media_kit}/{len(merged_users)} ({mk_pct:.1f}%)")

    # ICP Summary
    print(f"\n\n{'='*70}")
    print("🎯 IDEAL CUSTOMER PROFILE (ICP) SUMMARY")
    print(f"{'='*70}\n")

    top_country = countries.most_common(1)[0] if countries else ('Unknown', 0)
    top_niche = niches.most_common(1)[0] if niches else ('Unknown', 0)
    top_follower = follower_ranges.most_common(1)[0] if follower_ranges else ('Unknown', 0)
    top_platform = platforms.most_common(1)[0] if platforms else ('Unknown', 0)
    top_age = audience_ages.most_common(1)[0] if audience_ages else ('Unknown', 0)

    us_count = countries.get('United States', 0)
    us_pct = (us_count / len(merged_users)) * 100

    print("Your typical Pro subscriber is:\n")
    print(f"   🌍 Location:      {top_country[0]} ({us_pct:.0f}% US-based)")
    print(f"   👥 Follower size: {top_follower[0]}")
    print(f"   🎯 Primary niche: {top_niche[0]}")
    print(f"   📱 Main platform: {top_platform[0]}")
    print(f"   👶 Audience age:  {top_age[0]}")
    if total_followers:
        print(f"   📊 Median followers: {sorted(total_followers)[len(total_followers)//2]:,}")

    print("\n\n📌 KEY TAKEAWAYS:")
    print(f"   1. {us_pct:.0f}% of paying users are from the US")
    print(f"   2. {niches.most_common(1)[0][0].title()} is the dominant niche ({niches.most_common(1)[0][1]}/{len(merged_users)} users)")
    print(f"   3. Median follower count is {sorted(total_followers)[len(total_followers)//2]:,} - they're true micro-creators")
    print(f"   4. {top_platform[0].title()} is the primary platform")
    print(f"   5. Only {mk_pct:.0f}% have built a media kit - opportunity to drive adoption")

    print("\n")


if __name__ == '__main__':
    analyze()
