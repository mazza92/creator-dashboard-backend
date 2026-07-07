"""
Analyze Pro tier subscribers to generate Ideal Customer Profile (ICP)
Run: python scripts/analyze_pro_icp.py
"""

import os
import sys
import json
from collections import Counter, defaultdict
from datetime import datetime

# Fix Windows console encoding
sys.stdout.reconfigure(encoding='utf-8')

# Add parent directory for imports
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import psycopg2
from psycopg2.extras import RealDictCursor
from dotenv import load_dotenv

load_dotenv()


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


def analyze_pro_users():
    conn = get_db_connection()
    cursor = conn.cursor()

    # Query all paid users with profile data
    cursor.execute('''
        SELECT
            c.id,
            c.username,
            u.email,
            u.first_name,
            u.last_name,
            u.country,
            c.niche,
            c.regions,
            c.social_links,
            c.followers_count,
            c.primary_age_range,
            c.subscription_tier,
            c.subscription_status,
            c.subscription_started_at,
            c.pitches_sent_total,
            c.brands_saved_count,
            c.has_media_kit,
            u.created_at as signup_date
        FROM creators c
        JOIN users u ON c.user_id = u.id
        WHERE c.subscription_tier IN ('pro', 'elite')
          AND c.subscription_status = 'active'
        ORDER BY c.subscription_started_at DESC
    ''')

    paid_users = cursor.fetchall()
    cursor.close()
    conn.close()

    if not paid_users:
        print("❌ No paid users found!")
        return

    print(f"\n{'='*60}")
    print(f"PRO TIER ICP ANALYSIS - {len(paid_users)} Paid Users")
    print(f"{'='*60}\n")

    # Initialize counters
    countries = Counter()
    regions = Counter()
    niches = Counter()
    platforms = Counter()
    follower_ranges = Counter()
    audience_ages = Counter()
    has_media_kit = 0
    total_followers = []
    platform_followers = defaultdict(list)

    # Individual user details
    print("📋 PAID USER LIST:\n")
    print(f"{'Username':<20} {'Country':<12} {'Followers':<12} {'Niche':<30} {'Subscribed'}")
    print("-" * 100)

    for user in paid_users:
        username = user['username'] or 'N/A'
        country = user['country'] or 'Unknown'
        followers = user['followers_count'] or 0
        niche_raw = parse_json(user['niche'], [])
        niche_str = ', '.join(niche_raw[:2]) if niche_raw else 'Not set'
        sub_date = user['subscription_started_at'].strftime('%Y-%m-%d') if user['subscription_started_at'] else 'N/A'

        print(f"{username:<20} {country:<12} {followers:<12,} {niche_str:<30} {sub_date}")

        # Aggregate data
        countries[country] += 1

        # Regions
        user_regions = parse_json(user['regions'], [])
        for r in user_regions:
            regions[r] += 1

        # Niches
        for n in niche_raw:
            niches[n] += 1

        # Social links
        social = parse_json(user['social_links'], [])
        for link in social:
            if isinstance(link, dict):
                platform = link.get('platform', 'Unknown')
                platform_fc = link.get('followersCount') or link.get('followers_count') or 0
                platforms[platform] += 1
                if platform_fc:
                    platform_followers[platform].append(platform_fc)

        # Followers
        if followers:
            total_followers.append(followers)
            follower_ranges[categorize_followers(followers)] += 1
        else:
            follower_ranges['Unknown'] += 1

        # Audience age
        age = user['primary_age_range']
        if age:
            audience_ages[age] += 1

        # Media kit
        if user['has_media_kit']:
            has_media_kit += 1

    # Print aggregated analysis
    print(f"\n\n{'='*60}")
    print("📊 AGGREGATE ANALYSIS")
    print(f"{'='*60}\n")

    # Countries
    print("🌍 TOP COUNTRIES:")
    for country, count in countries.most_common(10):
        pct = (count / len(paid_users)) * 100
        print(f"   {country}: {count} ({pct:.1f}%)")

    # Follower distribution
    print("\n👥 FOLLOWER DISTRIBUTION:")
    for range_name, count in sorted(follower_ranges.items(), key=lambda x: x[1], reverse=True):
        pct = (count / len(paid_users)) * 100
        print(f"   {range_name}: {count} ({pct:.1f}%)")

    if total_followers:
        avg = sum(total_followers) / len(total_followers)
        sorted_fc = sorted(total_followers)
        median = sorted_fc[len(sorted_fc) // 2]
        print(f"\n   Average followers: {avg:,.0f}")
        print(f"   Median followers: {median:,}")
        print(f"   Range: {min(total_followers):,} - {max(total_followers):,}")

    # Niches
    print("\n🎯 TOP NICHES:")
    for niche, count in niches.most_common(10):
        pct = (count / len(paid_users)) * 100
        print(f"   {niche}: {count} ({pct:.1f}%)")

    # Platforms
    print("\n📱 PLATFORMS USED:")
    for platform, count in platforms.most_common():
        pct = (count / len(paid_users)) * 100
        avg_fc = 0
        if platform_followers[platform]:
            avg_fc = sum(platform_followers[platform]) / len(platform_followers[platform])
        print(f"   {platform}: {count} users ({pct:.1f}%) - Avg followers: {avg_fc:,.0f}")

    # Audience age
    if audience_ages:
        print("\n👶 AUDIENCE AGE DEMOGRAPHICS:")
        for age, count in audience_ages.most_common():
            pct = (count / len(paid_users)) * 100
            print(f"   {age}: {count} ({pct:.1f}%)")

    # Media kit adoption
    mk_pct = (has_media_kit / len(paid_users)) * 100
    print(f"\n📄 MEDIA KIT ADOPTION: {has_media_kit}/{len(paid_users)} ({mk_pct:.1f}%)")

    # ICP Summary
    print(f"\n\n{'='*60}")
    print("🎯 IDEAL CUSTOMER PROFILE (ICP) SUMMARY")
    print(f"{'='*60}\n")

    top_country = countries.most_common(1)[0] if countries else ('Unknown', 0)
    top_niche = niches.most_common(1)[0] if niches else ('Unknown', 0)
    top_follower_range = follower_ranges.most_common(1)[0] if follower_ranges else ('Unknown', 0)
    top_platform = platforms.most_common(1)[0] if platforms else ('Unknown', 0)

    print(f"Your typical Pro subscriber is:")
    print(f"   📍 Location: {top_country[0]} (most common)")
    print(f"   👥 Follower range: {top_follower_range[0]}")
    print(f"   🎯 Primary niche: {top_niche[0]}")
    print(f"   📱 Main platform: {top_platform[0]}")
    if total_followers:
        print(f"   📊 Median followers: {sorted(total_followers)[len(total_followers)//2]:,}")

    print("\n")


if __name__ == '__main__':
    analyze_pro_users()
