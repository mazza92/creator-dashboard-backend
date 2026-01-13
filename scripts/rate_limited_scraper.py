"""
Rate-Limited Brand Scraper for Production
Handles Instagram rate limits with delays and retries
"""

import time
import random
from typing import Optional, Dict
from free_brand_scraper import FreeBrandScraper

class RateLimitedScraper(FreeBrandScraper):
    """
    Extended scraper with rate limiting to avoid Instagram blocks
    """

    def __init__(self, delay_min=3, delay_max=7, max_retries=3):
        super().__init__()
        self.delay_min = delay_min  # Minimum delay between requests (seconds)
        self.delay_max = delay_max  # Maximum delay between requests
        self.max_retries = max_retries
        self.requests_made = 0
        self.last_request_time = 0

    def _wait_before_request(self):
        """Add random delay to avoid rate limiting"""
        current_time = time.time()
        time_since_last = current_time - self.last_request_time

        # If we've made too many requests recently, wait longer
        if self.requests_made > 0 and self.requests_made % 10 == 0:
            # Every 10 requests, take a longer break
            wait_time = random.uniform(15, 25)
            print(f"   [PAUSE] Taking a {wait_time:.1f}s break after {self.requests_made} requests...")
            time.sleep(wait_time)
        elif time_since_last < self.delay_min:
            # Regular delay between requests
            wait_time = random.uniform(self.delay_min, self.delay_max)
            time.sleep(wait_time)

        self.last_request_time = time.time()
        self.requests_made += 1

    def scrape_instagram_free(self, username: str) -> Optional[Dict]:
        """
        Scrape Instagram with rate limiting and retries
        """
        for attempt in range(self.max_retries):
            try:
                # Wait before making request
                self._wait_before_request()

                # Call parent method
                result = super().scrape_instagram_free(username)

                if result:
                    return result
                else:
                    if attempt < self.max_retries - 1:
                        print(f"   [RETRY] Attempt {attempt + 1} failed, trying again...")
                        time.sleep(random.uniform(5, 10))
                    continue

            except Exception as e:
                error_msg = str(e).lower()

                # Check if it's a rate limit error
                if '401' in error_msg or 'unauthorized' in error_msg or 'wait a few minutes' in error_msg:
                    wait_time = 60 * (attempt + 1)  # Exponential backoff: 60s, 120s, 180s
                    print(f"   [RATE LIMIT] Instagram rate limit hit. Waiting {wait_time}s...")
                    time.sleep(wait_time)

                    if attempt < self.max_retries - 1:
                        print(f"   [RETRY] Attempting again (attempt {attempt + 2}/{self.max_retries})...")
                        continue
                    else:
                        print(f"   [SKIP] Max retries reached for {username}, skipping...")
                        return None
                else:
                    # Other error, fail fast
                    print(f"   [ERROR] {str(e)}")
                    return None

        return None

    def scrape_full_brand_free(self, instagram_handle: str):
        """
        Override to add rate limiting to full scraping process
        """
        # Call parent method which now uses our rate-limited scrape_instagram_free
        return super().scrape_full_brand_free(instagram_handle)


def main():
    """Run production scraper with rate limiting"""
    import sys
    sys.path.insert(0, '..')

    from brand_lists_500plus import BEAUTY_BRANDS, FASHION_BRANDS, LIFESTYLE_BRANDS, ALL_BRANDS

    # Determine category
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
        print("\nAvailable: all, beauty, fashion, lifestyle")
        return

    print("\n" + "="*60)
    print(f"RATE-LIMITED PRODUCTION SCRAPER - {category_name}")
    print("="*60)
    print(f"Total brands to scrape: {len(brands_to_scrape)}")
    print(f"Rate limiting: 3-7s between requests, 15-25s every 10 brands")
    print(f"Retries: 3 attempts with exponential backoff on rate limits")
    print("="*60 + "\n")

    # Initialize rate-limited scraper
    scraper = RateLimitedScraper(delay_min=3, delay_max=7, max_retries=3)

    successful = 0
    skipped = 0
    failed = 0
    rate_limited = 0
    total_brands = len(brands_to_scrape)

    print("SCRAPING IN PROGRESS")
    print("="*60 + "\n")

    start_time = time.time()

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

            # Progress update every 20 brands
            if idx % 20 == 0:
                elapsed = time.time() - start_time
                avg_time = elapsed / idx
                remaining = (total_brands - idx) * avg_time

                print(f"\n{'='*60}")
                print(f"PROGRESS UPDATE - {idx}/{total_brands} ({(idx/total_brands)*100:.1f}%)")
                print(f"{'='*60}")
                print(f"New: {successful} | Skipped: {skipped} | Failed: {failed}")
                print(f"Elapsed: {elapsed/60:.1f}m | Est. Remaining: {remaining/60:.1f}m")
                print(f"{'='*60}\n")

        except KeyboardInterrupt:
            print("\n\n⚠️  Scraping interrupted by user")
            break
        except Exception as e:
            print(f"❌ Error: {str(e)}\n")
            failed += 1
            continue

    # Final summary
    cursor = scraper.db_conn.cursor()
    cursor.execute('SELECT COUNT(*) FROM pr_brands')
    total_in_db = cursor.fetchone()[0]
    cursor.close()

    elapsed_total = time.time() - start_time

    print("\n" + "="*60)
    print("SCRAPING COMPLETE!")
    print("="*60)
    print(f"\nResults:")
    print(f"  ✓ New brands created: {successful}")
    print(f"  ⊘ Already existed (skipped): {skipped}")
    print(f"  ✗ Failed: {failed}")
    print(f"  ⏱️  Total time: {elapsed_total/60:.1f} minutes")

    if successful + failed > 0:
        success_rate = (successful / (successful + failed)) * 100
        print(f"  📊 Success rate: {success_rate:.1f}%")

    print(f"\nTotal brands in database: {total_in_db}")

    if successful > 0:
        print(f"\n🎉 Added {successful} new brands! Total: {total_in_db}")
    elif skipped > 0:
        print(f"\n✓ All {skipped} brands already exist in database")

    # Production readiness
    print("\n" + "="*60)
    print("PRODUCTION READINESS CHECK")
    print("="*60)
    if total_in_db >= 500:
        print(f"✅ READY FOR PRODUCTION ({total_in_db} brands)")
    else:
        print(f"⚠️  Need {500 - total_in_db} more brands (current: {total_in_db})")
    print("="*60 + "\n")


if __name__ == "__main__":
    main()
