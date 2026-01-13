"""Test improved scraper - should find MORE emails"""
import sys
sys.path.insert(0, 'scripts')

from improved_scraper import ImprovedBrandScraper
from brand_websites import BRAND_DATA

# Test with brands that currently have no email
test_brands = {
    'Poppi': BRAND_DATA.get('Poppi', {'website': 'https://drinkpoppi.com', 'category': 'Lifestyle'}),
    'Sephora': BRAND_DATA.get('Sephora', {'website': 'https://www.sephora.com', 'category': 'Lifestyle'}),
    'Glossier': BRAND_DATA.get('Glossier', {'website': 'https://www.glossier.com', 'category': 'Beauty'}),
}

print("\n" + "="*80)
print("TESTING IMPROVED SCRAPER - Email Extraction")
print("="*80)
print(f"Testing {len(test_brands)} brands\n")

scraper = ImprovedBrandScraper()

results = {'found_email': 0, 'no_email': 0}

for brand_name, brand_info in test_brands.items():
    website = brand_info.get('website')
    print(f"\n[TEST] {brand_name}")
    print(f"Website: {website}")

    # Just test email extraction
    brand_data = scraper.scrape_website_comprehensive(website)

    if brand_data:
        email = brand_data.get('contact_email')
        if email:
            print(f"[SUCCESS] Found email: {email}")
            results['found_email'] += 1
        else:
            print(f"[NO EMAIL] Could not find contact email")
            results['no_email'] += 1
    else:
        print(f"[FAILED] Could not scrape website")
        results['no_email'] += 1

    print("-" * 80)

print("\n" + "="*80)
print("RESULTS")
print("="*80)
print(f"Found emails: {results['found_email']}/{len(test_brands)}")
print(f"Missing: {results['no_email']}/{len(test_brands)}")

if results['found_email'] >= 2:
    print("\n[SUCCESS] Improved scraper is finding more emails!")
    print("\nRun full scraper to update all brands:")
    print("  cd scripts")
    print("  python improved_scraper.py all")
else:
    print("\n[NEEDS MORE WORK] Still not finding enough emails")
