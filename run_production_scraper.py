#!/usr/bin/env python3
"""
Production Brand Scraper - Scrape 500+ brands for production launch
Usage:
    python run_production_scraper.py all        # Scrape all 580 brands
    python run_production_scraper.py beauty     # Scrape 230 beauty brands
    python run_production_scraper.py fashion    # Scrape 220 fashion brands
    python run_production_scraper.py lifestyle  # Scrape 130 lifestyle brands
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from brand_lists_500plus import BEAUTY_BRANDS, FASHION_BRANDS, LIFESTYLE_BRANDS, ALL_BRANDS
from scripts.free_brand_scraper import FreeBrandScraper

def main():
    # Determine which category to scrape
    category = sys.argv[1] if len(sys.argv) > 1 else 'all'

    if category.lower() == 'beauty':
        brands_to_scrape = BEAUTY_BRANDS
        category_name = "BEAUTY"
    elif category.lower() == 'fashion':
        brands_to_scrape = FASHION_BRANDS
        category_name = "FASHION"
    elif category.lower() == 'lifestyle':
        brands_to_scrape = LIFESTYLE_BRANDS
        category_name = "LIFESTYLE"
    elif category.lower() == 'all':
        brands_to_scrape = ALL_BRANDS
        category_name = "ALL CATEGORIES"
    else:
        print(f"❌ Unknown category: {category}")
        print("\nAvailable categories:")
        print("  - all       (580 brands)")
        print("  - beauty    (230 brands)")
        print("  - fashion   (220 brands)")
        print("  - lifestyle (130 brands)")
        return

    print("\n" + "="*60)
    print(f"PRODUCTION BRAND SCRAPER - {category_name}")
    print("="*60)
    print(f"Total brands to scrape: {len(brands_to_scrape)}")
    print(f"Target: Build library of 500+ brands for production")
    print("="*60 + "\n")

    scraper = FreeBrandScraper()
    successful = 0
    skipped = 0
    failed = 0
    total_brands = len(brands_to_scrape)

    print("SCRAPING IN PROGRESS")
    print("="*60 + "\n")

    for idx, brand in enumerate(brands_to_scrape, 1):
        print(f"[{idx}/{total_brands}] Processing: {brand}\n")

        try:
            result = scraper.scrape_full_brand_free(brand)
            if result == 'SKIPPED':
                skipped += 1
            elif result is None:
                failed += 1
            else:
                successful += 1

            # Show running totals every 10 brands
            if idx % 10 == 0:
                print(f"\n--- Progress Update ---")
                print(f"Processed: {idx}/{total_brands}")
                print(f"New: {successful} | Skipped: {skipped} | Failed: {failed}")
                print(f"-" * 60 + "\n")

        except Exception as e:
            print(f"❌ Error scraping {brand}: {str(e)}\n")
            failed += 1
            continue

    # Get final count from database
    cursor = scraper.db_conn.cursor()
    cursor.execute('SELECT COUNT(*) FROM pr_brands')
    total_in_db = cursor.fetchone()[0]
    cursor.close()

    # Final summary
    print("\n" + "="*60)
    print("SCRAPING COMPLETE!")
    print("="*60)
    print(f"\nResults:")
    print(f"  ✓ New brands created: {successful}")
    print(f"  ⊘ Already existed (skipped): {skipped}")
    print(f"  ✗ Failed: {failed}")

    if successful + failed > 0:
        success_rate = (successful / (successful + failed)) * 100
        print(f"  📊 Success rate: {success_rate:.1f}%")

    print(f"\nTotal brands in database: {total_in_db}")

    if successful > 0:
        print(f"\n🎉 Added {successful} new brands! Total: {total_in_db}")
    elif skipped > 0:
        print(f"\n✓ All {skipped} brands already exist in database")
    else:
        print(f"\n⚠️  No new brands were added")

    # Production readiness check
    print("\n" + "="*60)
    print("PRODUCTION READINESS CHECK")
    print("="*60)
    if total_in_db >= 500:
        print(f"✅ READY FOR PRODUCTION ({total_in_db} brands)")
    else:
        remaining = 500 - total_in_db
        print(f"⚠️  Need {remaining} more brands to reach 500 minimum")
        print(f"   Current: {total_in_db} brands")
    print("="*60 + "\n")


if __name__ == "__main__":
    main()
