"""
Seed PR Brands Database
This script seeds the pr_brands table with initial brand data
"""

import psycopg2
from psycopg2.extras import RealDictCursor
import os
import json
from dotenv import load_dotenv

load_dotenv()

# Database connection
conn = psycopg2.connect(
    host=os.getenv('DB_HOST'),
    database=os.getenv('DB_NAME'),
    user=os.getenv('DB_USER'),
    password=os.getenv('DB_PASSWORD')
)

cursor = conn.cursor(cursor_factory=RealDictCursor)

# ============================================
# INITIAL BRAND SEED DATA
# ============================================

brands = [
    # AUSTRALIAN BEAUTY BRANDS
    {
        'brand_name': 'Mecca',
        'website': 'https://www.mecca.com.au',
        'category': 'Beauty',
        'niches': json.dumps(['makeup', 'skincare', 'haircare', 'fragrance']),
        'regions': json.dumps(['Australia']),
        'min_followers': 3000,
        'platforms': json.dumps(['instagram', 'tiktok']),
        'contact_email': 'pr@mecca.com.au',
        'instagram_handle': '@mecca',
        'has_application_form': True,
        'application_form_url': 'https://www.mecca.com.au/influencer-program',
        'product_types': json.dumps(['makeup', 'skincare', 'beauty tools']),
        'response_rate': 38,
        'avg_response_time_days': 7,
        'is_premium': False,
        'source_url': 'https://newcollab.co/blog/aussie-brands-pr-package-list-2026',
        'notes': 'One of Australia\'s largest beauty retailers. Responds well to polished media kits.'
    },
    {
        'brand_name': 'Frank Body',
        'website': 'https://frankbody.com',
        'category': 'Beauty',
        'niches': json.dumps(['skincare', 'body care']),
        'regions': json.dumps(['Australia', 'US', 'UK']),
        'min_followers': 5000,
        'platforms': json.dumps(['instagram', 'tiktok']),
        'contact_email': 'hello@frankbody.com',
        'instagram_handle': '@frank_bod',
        'tiktok_handle': '@frank_bod',
        'has_application_form': False,
        'product_types': json.dumps(['body scrubs', 'skincare', 'face masks']),
        'response_rate': 45,
        'avg_response_time_days': 5,
        'is_premium': False,
        'source_url': 'https://newcollab.co/blog/aussie-brands-pr-package-list-2026',
        'notes': 'Known for coffee scrubs. Loves authentic, unfiltered content.'
    },
    {
        'brand_name': 'Bondi Sands',
        'website': 'https://bondisands.com',
        'category': 'Beauty',
        'niches': json.dumps(['tanning', 'skincare', 'body care']),
        'regions': json.dumps(['Australia', 'US', 'UK']),
        'min_followers': 10000,
        'platforms': json.dumps(['instagram', 'tiktok']),
        'contact_email': 'pr@bondisands.com',
        'instagram_handle': '@bondisands',
        'has_application_form': True,
        'application_form_url': 'https://bondisands.com/pages/ambassador',
        'product_types': json.dumps(['self-tanner', 'gradual tan', 'tan removal']),
        'response_rate': 52,
        'avg_response_time_days': 4,
        'is_premium': False,
        'source_url': 'https://newcollab.co/blog/aussie-brands-pr-package-list-2026',
        'notes': 'Popular self-tanning brand. Prefers beach/summer aesthetic.'
    },

    # AUSTRALIAN FASHION BRANDS
    {
        'brand_name': 'Princess Polly',
        'website': 'https://www.princesspolly.com',
        'category': 'Fashion',
        'niches': json.dumps(['fashion', 'streetwear', 'activewear']),
        'regions': json.dumps(['Australia', 'US']),
        'min_followers': 15000,
        'platforms': json.dumps(['instagram', 'tiktok']),
        'contact_email': 'influencers@princesspolly.com',
        'instagram_handle': '@princesspolly',
        'tiktok_handle': '@princesspolly',
        'has_application_form': True,
        'application_form_url': 'https://www.princesspolly.com/pages/influencer-program',
        'product_types': json.dumps(['dresses', 'tops', 'bottoms', 'activewear']),
        'response_rate': 35,
        'avg_response_time_days': 10,
        'is_premium': False,
        'source_url': 'https://newcollab.co/blog/pr-list-for-clothing-brands-micro-influencers-2025',
        'notes': 'Gen-Z focused fashion. Loves OOTD and styling content.'
    },
    {
        'brand_name': 'Showpo',
        'website': 'https://www.showpo.com',
        'category': 'Fashion',
        'niches': json.dumps(['fashion', 'party wear', 'casual']),
        'regions': json.dumps(['Australia', 'US']),
        'min_followers': 8000,
        'platforms': json.dumps(['instagram']),
        'contact_email': 'pr@showpo.com',
        'instagram_handle': '@showpo',
        'has_application_form': False,
        'product_types': json.dumps(['dresses', 'playsuits', 'tops']),
        'response_rate': 40,
        'avg_response_time_days': 6,
        'is_premium': False,
        'source_url': 'https://newcollab.co/blog/pr-list-for-clothing-brands-micro-influencers-2025',
        'notes': 'Affordable fast fashion. Quick to respond to pitches.'
    },

    # US BEAUTY BRANDS
    {
        'brand_name': 'Glossier',
        'website': 'https://www.glossier.com',
        'category': 'Beauty',
        'niches': json.dumps(['skincare', 'makeup', 'beauty']),
        'regions': json.dumps(['US', 'UK', 'Canada']),
        'min_followers': 5000,
        'platforms': json.dumps(['instagram', 'tiktok']),
        'contact_email': 'pr@glossier.com',
        'instagram_handle': '@glossier',
        'tiktok_handle': '@glossier',
        'has_application_form': True,
        'application_form_url': 'https://www.glossier.com/influencer',
        'product_types': json.dumps(['skincare', 'makeup', 'fragrance']),
        'response_rate': 42,
        'avg_response_time_days': 7,
        'is_premium': True,
        'source_url': 'https://newcollab.co/blog/us-brands-send-pr-micro-influencers-2026-list',
        'notes': 'Cult beauty brand. Loves minimal, natural beauty aesthetic.'
    },
    {
        'brand_name': 'ColourPop',
        'website': 'https://colourpop.com',
        'category': 'Beauty',
        'niches': json.dumps(['makeup', 'beauty']),
        'regions': json.dumps(['US', 'Global']),
        'min_followers': 3000,
        'platforms': json.dumps(['instagram', 'tiktok', 'youtube']),
        'contact_email': 'pr@colourpop.com',
        'instagram_handle': '@colourpopcosmetics',
        'has_application_form': True,
        'application_form_url': 'https://colourpop.com/pages/pr-application',
        'product_types': json.dumps(['eyeshadow', 'lipstick', 'blush', 'highlighter']),
        'response_rate': 65,
        'avg_response_time_days': 3,
        'is_premium': False,
        'source_url': 'https://newcollab.co/blog/15-beauty-brands-actively-sending-pr-to-micro-influencers-2025',
        'notes': 'Very responsive to micro-influencers. Affordable makeup brand.',
        'success_stories': 'Sarah received PR package in 3 days with 4k followers'
    },
    {
        'brand_name': 'Fenty Beauty',
        'website': 'https://fentybeauty.com',
        'category': 'Beauty',
        'niches': json.dumps(['makeup', 'skincare', 'beauty']),
        'regions': json.dumps(['US', 'UK', 'Australia', 'Canada']),
        'min_followers': 10000,
        'platforms': json.dumps(['instagram', 'tiktok', 'youtube']),
        'contact_email': 'influencer@fentybeauty.com',
        'instagram_handle': '@fentybeauty',
        'tiktok_handle': '@fentybeauty',
        'has_application_form': False,
        'product_types': json.dumps(['foundation', 'highlighter', 'lipstick', 'skincare']),
        'response_rate': 38,
        'avg_response_time_days': 10,
        'is_premium': True,
        'source_url': 'https://newcollab.co/blog/15-beauty-brands-actively-sending-pr-to-micro-influencers-2025',
        'notes': 'Rihanna\'s makeup line. High-value PR packages.'
    },
    {
        'brand_name': 'The Ordinary',
        'website': 'https://theordinary.com',
        'category': 'Beauty',
        'niches': json.dumps(['skincare', 'beauty']),
        'regions': json.dumps(['Global']),
        'min_followers': 5000,
        'platforms': json.dumps(['instagram', 'tiktok', 'youtube']),
        'contact_email': 'pr@theordinary.com',
        'instagram_handle': '@theordinary',
        'has_application_form': True,
        'application_form_url': 'https://theordinary.com/en-us/influencer-program.html',
        'product_types': json.dumps(['serums', 'moisturizers', 'cleansers', 'treatments']),
        'response_rate': 50,
        'avg_response_time_days': 5,
        'is_premium': False,
        'source_url': 'https://newcollab.co/blog/skincare-pr-list-small-creators-2026',
        'notes': 'Affordable, science-based skincare. Loves educational content.'
    },
    {
        'brand_name': 'Rare Beauty',
        'website': 'https://rarebeauty.com',
        'category': 'Beauty',
        'niches': json.dumps(['makeup', 'beauty']),
        'regions': json.dumps(['US', 'Canada']),
        'min_followers': 8000,
        'platforms': json.dumps(['instagram', 'tiktok']),
        'contact_email': 'pr@rarebeauty.com',
        'instagram_handle': '@rarebeauty',
        'tiktok_handle': '@rarebeauty',
        'has_application_form': False,
        'product_types': json.dumps(['blush', 'lipstick', 'foundation', 'highlighter']),
        'response_rate': 44,
        'avg_response_time_days': 8,
        'is_premium': True,
        'source_url': 'https://newcollab.co/blog/15-beauty-brands-actively-sending-pr-to-micro-influencers-2025',
        'notes': 'Selena Gomez\'s brand. Focuses on mental health awareness.'
    },

    # FASHION BRANDS (US)
    {
        'brand_name': 'Gymshark',
        'website': 'https://www.gymshark.com',
        'category': 'Fashion',
        'niches': json.dumps(['activewear', 'fitness', 'athleisure']),
        'regions': json.dumps(['Global']),
        'min_followers': 15000,
        'platforms': json.dumps(['instagram', 'tiktok', 'youtube']),
        'contact_email': 'influencer@gymshark.com',
        'instagram_handle': '@gymshark',
        'tiktok_handle': '@gymshark',
        'has_application_form': True,
        'application_form_url': 'https://www.gymshark.com/pages/athletes',
        'product_types': json.dumps(['leggings', 'sports bras', 'shorts', 'hoodies']),
        'response_rate': 30,
        'avg_response_time_days': 14,
        'is_premium': True,
        'source_url': 'https://newcollab.co/blog/pr-list-for-clothing-brands-micro-influencers-2025',
        'notes': 'Massive fitness brand. Competitive but worth applying.'
    },
    {
        'brand_name': 'ASOS',
        'website': 'https://www.asos.com',
        'category': 'Fashion',
        'niches': json.dumps(['fashion', 'streetwear', 'formal']),
        'regions': json.dumps(['UK', 'US', 'Australia']),
        'min_followers': 10000,
        'platforms': json.dumps(['instagram', 'tiktok']),
        'contact_email': 'influencermarketing@asos.com',
        'instagram_handle': '@asos',
        'has_application_form': True,
        'application_form_url': 'https://www.asos.com/discover/insiders/',
        'product_types': json.dumps(['dresses', 'tops', 'bottoms', 'accessories']),
        'response_rate': 28,
        'avg_response_time_days': 12,
        'is_premium': False,
        'source_url': 'https://newcollab.co/blog/pr-list-for-clothing-brands-micro-influencers-2025',
        'notes': 'Global fashion retailer. ASOS Insiders program.'
    },

    # GAMING/TECH BRANDS
    {
        'brand_name': 'Razer',
        'website': 'https://www.razer.com',
        'category': 'Tech',
        'niches': json.dumps(['gaming', 'peripherals', 'tech']),
        'regions': json.dumps(['Global']),
        'min_followers': 5000,
        'platforms': json.dumps(['twitch', 'youtube', 'tiktok', 'instagram']),
        'contact_email': 'influencer@razer.com',
        'instagram_handle': '@razer',
        'tiktok_handle': '@razer',
        'has_application_form': True,
        'application_form_url': 'https://www.razer.com/ambassadors',
        'product_types': json.dumps(['keyboards', 'mice', 'headsets', 'laptops']),
        'response_rate': 35,
        'avg_response_time_days': 10,
        'is_premium': False,
        'source_url': 'https://newcollab.co/blog/ultimate-list-of-gaming-tech-companies-that-sponsor-small-streamers',
        'notes': 'Gaming peripherals leader. Prefers gaming content creators.'
    },
    {
        'brand_name': 'HyperX',
        'website': 'https://www.hyperx.com',
        'category': 'Tech',
        'niches': json.dumps(['gaming', 'audio', 'peripherals']),
        'regions': json.dumps(['Global']),
        'min_followers': 3000,
        'platforms': json.dumps(['twitch', 'youtube', 'tiktok']),
        'contact_email': 'sponsorships@hyperx.com',
        'instagram_handle': '@hyperx',
        'has_application_form': False,
        'product_types': json.dumps(['headsets', 'keyboards', 'mice', 'memory']),
        'response_rate': 42,
        'avg_response_time_days': 7,
        'is_premium': False,
        'source_url': 'https://newcollab.co/blog/ultimate-list-of-gaming-tech-companies-that-sponsor-small-streamers',
        'notes': 'Known for gaming headsets. Very responsive to streamers.'
    },
    {
        'brand_name': 'SteelSeries',
        'website': 'https://steelseries.com',
        'category': 'Tech',
        'niches': json.dumps(['gaming', 'peripherals', 'audio']),
        'regions': json.dumps(['Global']),
        'min_followers': 5000,
        'platforms': json.dumps(['twitch', 'youtube']),
        'contact_email': 'partnerships@steelseries.com',
        'instagram_handle': '@steelseries',
        'has_application_form': True,
        'application_form_url': 'https://steelseries.com/sponsorships',
        'product_types': json.dumps(['headsets', 'keyboards', 'mice', 'mousepads']),
        'response_rate': 38,
        'avg_response_time_days': 9,
        'is_premium': False,
        'source_url': 'https://newcollab.co/blog/ultimate-list-of-gaming-tech-companies-that-sponsor-small-streamers',
        'notes': 'Pro gaming gear. Established sponsorship program.'
    },

    # SUSTAINABLE/ECO BRANDS
    {
        'brand_name': 'Pela Case',
        'website': 'https://pelacase.com',
        'category': 'Lifestyle',
        'niches': json.dumps(['sustainability', 'tech accessories', 'eco-friendly']),
        'regions': json.dumps(['US', 'Canada', 'Global']),
        'min_followers': 3000,
        'platforms': json.dumps(['instagram', 'tiktok']),
        'contact_email': 'influencer@pelacase.com',
        'instagram_handle': '@pela',
        'has_application_form': False,
        'product_types': json.dumps(['phone cases', 'airpod cases', 'watch bands']),
        'response_rate': 55,
        'avg_response_time_days': 4,
        'is_premium': False,
        'source_url': 'https://newcollab.co/blog/10-sustainable-eco-friendly-brands-looking-for-creator-partnerships',
        'notes': 'Compostable phone cases. Loves eco-conscious creators.'
    },

    # Add more brands to reach 150+ total
    # This is a starter set - we'll add more brands in batches
]

print(f"🌱 Seeding {len(brands)} brands into database...")

inserted_count = 0
for brand in brands:
    try:
        cursor.execute('''
            INSERT INTO pr_brands (
                brand_name, website, category, niches, regions,
                min_followers, max_followers, platforms,
                contact_email, instagram_handle, tiktok_handle, youtube_handle,
                has_application_form, application_form_url,
                product_types, response_rate, avg_response_time_days,
                is_premium, source_url, notes, success_stories
            ) VALUES (
                %(brand_name)s, %(website)s, %(category)s, %(niches)s, %(regions)s,
                %(min_followers)s, %(max_followers)s, %(platforms)s,
                %(contact_email)s, %(instagram_handle)s, %(tiktok_handle)s, %(youtube_handle)s,
                %(has_application_form)s, %(application_form_url)s,
                %(product_types)s, %(response_rate)s, %(avg_response_time_days)s,
                %(is_premium)s, %(source_url)s, %(notes)s, %(success_stories)s
            )
        ''', {
            **brand,
            'max_followers': brand.get('max_followers'),
            'youtube_handle': brand.get('youtube_handle'),
            'success_stories': brand.get('success_stories')
        })
        inserted_count += 1
        print(f"✅ Added: {brand['brand_name']}")
    except Exception as e:
        print(f"❌ Error adding {brand['brand_name']}: {str(e)}")

conn.commit()
print(f"\n🎉 Successfully seeded {inserted_count} brands!")

# Show stats
cursor.execute("SELECT COUNT(*) as total FROM pr_brands")
total = cursor.fetchone()['total']

cursor.execute("SELECT COUNT(*) as premium FROM pr_brands WHERE is_premium = true")
premium = cursor.fetchone()['premium']

cursor.execute("SELECT COUNT(*) as has_form FROM pr_brands WHERE has_application_form = true")
has_form = cursor.fetchone()['has_form']

print(f"\n📊 Database Stats:")
print(f"   Total brands: {total}")
print(f"   Premium brands: {premium}")
print(f"   Brands with application forms: {has_form}")

cursor.close()
conn.close()

print("\n✨ Seed complete!")
