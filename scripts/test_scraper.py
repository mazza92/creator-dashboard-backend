"""
Quick test script to verify scraper works
Tests with 1 brand before running full batch
"""

from free_brand_scraper import FreeBrandScraper

def main():
    print("=" * 60)
    print("BRAND SCRAPER TEST")
    print("=" * 60)
    print("\nTesting with 1 brand: glossier\n")

    scraper = FreeBrandScraper()

    try:
        brand_id = scraper.scrape_full_brand_free('glossier')

        if brand_id:
            print(f"\n✅ SUCCESS! Brand saved with ID: {brand_id}")
            print("\nVerify in database:")
            print("SELECT * FROM pr_brands WHERE id = {};".format(brand_id))
        else:
            print("\n❌ FAILED - Check error messages above")

    except Exception as e:
        print(f"\n❌ ERROR: {str(e)}")
        import traceback
        print(traceback.format_exc())


if __name__ == "__main__":
    main()
