"""Test multi-platform scraper with brands that don't exist yet"""
import sys
sys.path.insert(0, 'scripts')

from multi_platform_scraper import MultiPlatformScraper
from brand_websites import BRAND_DATA

# Test with brands likely not in database
test_brands = {
    'Merit Beauty': BRAND_DATA.get('Merit Beauty', {'website': 'https://meritbeauty.com', 'category': 'Beauty'}),
    'Jones Road Beauty': BRAND_DATA.get('Jones Road Beauty', {'website': 'https://jonesroadbeauty.com', 'category': 'Beauty'}),
    'Refy': BRAND_DATA.get('Refy', {'website': 'https://refybeauty.com', 'category': 'Beauty'}),
}

print("\n" + "="*60)
print("TESTING NEW BRANDS - Multi-Platform Scraper")
print("="*60)
print(f"Testing with {len(test_brands)} brands")
print("="*60 + "\n")

scraper = MultiPlatformScraper()

results = {'new': 0, 'skipped': 0, 'failed': 0}

for brand_name, brand_info in test_brands.items():
    website = brand_info.get('website')
    print(f"\nTesting: {brand_name}")
    print(f"Website: {website}\n")

    result = scraper.scrape_brand(brand_name, website)

    if result == 'SKIPPED':
        print("[SKIPPED] Brand already exists")
        results['skipped'] += 1
    elif result:
        print(f"[SUCCESS] Brand ID: {result}")
        results['new'] += 1
    else:
        print("[FAILED]")
        results['failed'] += 1

    print("\n" + "-"*60)

# Check total brands in database
cursor = scraper.db_conn.cursor()
cursor.execute('SELECT COUNT(*) FROM pr_brands')
total = cursor.fetchone()[0]
cursor.close()

print("\n" + "="*60)
print("TEST RESULTS")
print("="*60)
print(f"New brands added: {results['new']}")
print(f"Already existed: {results['skipped']}")
print(f"Failed: {results['failed']}")
print(f"\nTotal brands in database: {total}")

if results['new'] > 0:
    print(f"\n[SUCCESS] Multi-platform scraper is working!")
    print("\nReady to scrape all 213 brands:")
    print("  cd scripts")
    print("  python multi_platform_scraper.py all")
elif results['skipped'] == len(test_brands):
    print("\n[INFO] All test brands already exist (good duplicate prevention)")
    print("\nTry running full scraper to add remaining brands:")
    print("  cd scripts")
    print("  python multi_platform_scraper.py all")
else:
    print("\n[WARNING] Some brands failed to scrape")
