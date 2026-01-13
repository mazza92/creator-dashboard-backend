"""Test multi-platform scraper with a few brands"""
import sys
sys.path.insert(0, 'scripts')

from multi_platform_scraper import MultiPlatformScraper
from brand_websites import BRAND_DATA

# Test with 3 brands
test_brands = {
    'Glossier': BRAND_DATA['Glossier'],
    'CeraVe': BRAND_DATA['CeraVe'],
    'Gymshark': BRAND_DATA.get('Gymshark', {'website': 'https://www.gymshark.com', 'category': 'Fashion'})
}

print("\n" + "="*60)
print("TESTING MULTI-PLATFORM SCRAPER")
print("="*60)
print(f"Testing with {len(test_brands)} brands")
print("="*60 + "\n")

scraper = MultiPlatformScraper()

for brand_name, brand_info in test_brands.items():
    website = brand_info.get('website')
    print(f"\nTesting: {brand_name}")
    print(f"Website: {website}\n")

    result = scraper.scrape_brand(brand_name, website)

    if result == 'SKIPPED':
        print("[SKIPPED] Brand already exists (duplicate prevention working)")
    elif result:
        print(f"[SUCCESS] Brand ID: {result}")
    else:
        print("[FAILED]")

    print("\n" + "-"*60)

print("\n[DONE] Test complete!")
print("\nIf all 3 brands worked, you can run the full scraper with:")
print("  cd scripts")
print("  python multi_platform_scraper.py all")
