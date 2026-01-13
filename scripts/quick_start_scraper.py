"""
Quick Start Brand Scraper
Run this to immediately start populating your database with real brands
NO API KEYS REQUIRED for initial setup
"""

import sys
import os

# Add parent directory to path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from free_brand_scraper import FreeBrandScraper


def load_brand_list(filename):
    """Load brands from text file"""
    brands = []
    filepath = os.path.join('brand_lists', filename)

    try:
        with open(filepath, 'r') as f:
            for line in f:
                line = line.strip()
                # Skip comments and empty lines
                if line and not line.startswith('#'):
                    brands.append(line)
        return brands
    except FileNotFoundError:
        print(f"Error: {filepath} not found")
        return []


def main():
    print("=" * 60)
    print("QUICK START BRAND SCRAPER")
    print("=" * 60)
    print("\nThis will scrape real brand data and populate your database.")
    print("100% FREE - No API keys required!\n")

    # Choose category
    print("Select category to scrape:")
    print("1. Beauty & Cosmetics (~50 brands)")
    print("2. Fashion & Apparel (~40 brands)")
    print("3. Both categories (~90 brands)")
    print("4. Small test (5 brands)")

    choice = input("\nEnter choice (1-4): ").strip()

    scraper = FreeBrandScraper()
    all_brands = []

    if choice == '1':
        all_brands = load_brand_list('beauty_brands.txt')
        print(f"\nLoaded {len(all_brands)} beauty brands")
    elif choice == '2':
        all_brands = load_brand_list('fashion_brands.txt')
        print(f"\nLoaded {len(all_brands)} fashion brands")
    elif choice == '3':
        beauty = load_brand_list('beauty_brands.txt')
        fashion = load_brand_list('fashion_brands.txt')
        all_brands = beauty + fashion
        print(f"\nLoaded {len(all_brands)} brands total")
    elif choice == '4':
        # Quick test with 5 known good brands
        all_brands = ['glossier', 'fentybeauty', 'gymshark', 'fashionnova', 'kyliecosmetics']
        print(f"\nTest mode: 5 brands")
    else:
        print("Invalid choice!")
        return

    if not all_brands:
        print("No brands to scrape!")
        return

    print(f"\nStarting scraping process...")
    print(f"This will take approximately {len(all_brands) * 3 / 60:.1f} minutes")
    print("(3 seconds per brand to avoid rate limiting)\n")

    confirm = input("Continue? (y/n): ").strip().lower()
    if confirm != 'y':
        print("Cancelled.")
        return

    # Track progress
    successful = 0
    failed = 0
    total = len(all_brands)

    print("\n" + "=" * 60)
    print("SCRAPING IN PROGRESS")
    print("=" * 60 + "\n")

    for i, brand in enumerate(all_brands, 1):
        print(f"[{i}/{total}] Processing: {brand}")

        try:
            brand_id = scraper.scrape_full_brand_free(brand)

            if brand_id:
                successful += 1
                print(f"   ✓ Success (ID: {brand_id})")
            else:
                failed += 1
                print(f"   ✗ Failed")

        except KeyboardInterrupt:
            print("\n\nScraping interrupted by user!")
            break
        except Exception as e:
            failed += 1
            print(f"   ✗ Error: {str(e)}")

        # Progress update every 10 brands
        if i % 10 == 0:
            print(f"\n--- Progress: {i}/{total} ({i/total*100:.1f}%) ---")
            print(f"Successful: {successful} | Failed: {failed}\n")

    # Final summary
    print("\n" + "=" * 60)
    print("SCRAPING COMPLETE!")
    print("=" * 60)
    print(f"\nResults:")
    print(f"  ✓ Successful: {successful}")
    print(f"  ✗ Failed: {failed}")
    print(f"  📊 Success rate: {successful/total*100:.1f}%")
    print(f"\nTotal brands in database: {successful}")

    if successful > 0:
        print(f"\n🎉 Success! You now have {successful} real brands with contact data!")
        print("\nNext steps:")
        print("1. Check your database: SELECT * FROM brands ORDER BY id DESC LIMIT 10;")
        print("2. Verify emails: Check the 'contact_email' column")
        print("3. Test the frontend: Visit /creator/dashboard/pr-brands")
        print("\nNote: Some emails may need manual verification.")
    else:
        print("\n⚠️  No brands were scraped successfully.")
        print("Possible issues:")
        print("1. Instagram rate limiting (try again in 1 hour)")
        print("2. Database connection issues")
        print("3. Missing dependencies (run: pip install -r scraper_requirements.txt)")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n\nExiting...")
    except Exception as e:
        print(f"\nFatal error: {str(e)}")
        print("Please check your setup and try again.")
