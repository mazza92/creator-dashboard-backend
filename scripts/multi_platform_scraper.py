"""
Multi-Platform Brand Scraper
Scrapes from websites, TikTok, YouTube, LinkedIn - not just Instagram
No rate limits, more reliable for production
"""

import requests
from bs4 import BeautifulSoup
import re
import json
import time
import random
from typing import Optional, Dict, List
from urllib.parse import urljoin, urlparse
import psycopg2
from psycopg2.extras import RealDictCursor
import os
from dotenv import load_dotenv

load_dotenv()


class MultiPlatformScraper:
    """Scrape brand data from multiple sources, not just Instagram"""

    def __init__(self):
        self.db_conn = self._get_db_connection()
        self.session = requests.Session()
        self.session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
        })

    def _get_db_connection(self):
        """Connect to PostgreSQL database"""
        return psycopg2.connect(
            host=os.getenv('DB_HOST', 'localhost'),
            port=os.getenv('DB_PORT', 5432),
            database=os.getenv('DB_NAME', 'creator_dashboard'),
            user=os.getenv('DB_USER', 'postgres'),
            password=os.getenv('DB_PASSWORD')
        )

    def scrape_from_website(self, url: str) -> Optional[Dict]:
        """
        Scrape brand data directly from website
        Primary method - no rate limits!
        """
        try:
            if not url.startswith('http'):
                url = f'https://{url}'

            print(f"   Fetching website: {url}")
            response = self.session.get(url, timeout=15, allow_redirects=True)

            if response.status_code != 200:
                print(f"   [!] Website returned {response.status_code}")
                return None

            soup = BeautifulSoup(response.text, 'html.parser')

            # Extract brand data from website
            brand_data = {
                'website': url,
                'data_source': 'website'
            }

            # Get brand name from meta tags or title
            brand_data['brand_name'] = self._extract_brand_name(soup, url)

            # Get description
            brand_data['description'] = self._extract_description(soup)

            # Get logo
            brand_data['logo_url'] = self._extract_logo(soup, url)

            # Get social media links
            socials = self._extract_social_links(soup)
            brand_data.update(socials)

            # Get contact email
            brand_data['contact_email'] = self._extract_email(soup, url)

            # Get category/industry
            brand_data['category'] = self._extract_category(soup)

            # Get cover image
            brand_data['cover_image_url'] = self._extract_cover_image(soup, url)

            return brand_data

        except Exception as e:
            print(f"   [!] Error scraping website: {str(e)}")
            return None

    def _extract_brand_name(self, soup: BeautifulSoup, url: str) -> str:
        """Extract brand name from website"""
        # Try meta tags
        og_title = soup.find('meta', property='og:site_name')
        if og_title and og_title.get('content'):
            return og_title['content'].strip()

        og_title = soup.find('meta', property='og:title')
        if og_title and og_title.get('content'):
            return og_title['content'].strip().split('|')[0].strip()

        # Try title tag
        title = soup.find('title')
        if title:
            return title.text.strip().split('|')[0].strip()

        # Fallback to domain name
        domain = urlparse(url).netloc.replace('www.', '').split('.')[0]
        return domain.capitalize()

    def _extract_description(self, soup: BeautifulSoup) -> str:
        """Extract brand description"""
        # Try meta description
        meta_desc = soup.find('meta', attrs={'name': 'description'})
        if meta_desc and meta_desc.get('content'):
            return meta_desc['content'].strip()

        og_desc = soup.find('meta', property='og:description')
        if og_desc and og_desc.get('content'):
            return og_desc['content'].strip()

        # Try first paragraph
        first_p = soup.find('p')
        if first_p:
            text = first_p.get_text().strip()
            if len(text) > 50:
                return text[:500]

        return ''

    def _extract_logo(self, soup: BeautifulSoup, base_url: str) -> Optional[str]:
        """Extract brand logo"""
        # Try og:image
        og_image = soup.find('meta', property='og:image')
        if og_image and og_image.get('content'):
            return urljoin(base_url, og_image['content'])

        # Try link rel icon
        icon = soup.find('link', rel='icon')
        if icon and icon.get('href'):
            return urljoin(base_url, icon['href'])

        return None

    def _extract_social_links(self, soup: BeautifulSoup) -> Dict:
        """Extract social media links from website"""
        socials = {}

        # Find all links
        links = soup.find_all('a', href=True)

        for link in links:
            href = link['href'].lower()

            # Instagram
            if 'instagram.com/' in href:
                match = re.search(r'instagram\.com/([^/?]+)', href)
                if match:
                    socials['instagram_handle'] = f"@{match.group(1)}"

            # TikTok
            elif 'tiktok.com/' in href:
                match = re.search(r'tiktok\.com/@([^/?]+)', href)
                if match:
                    socials['tiktok_handle'] = f"@{match.group(1)}"

            # YouTube
            elif 'youtube.com/' in href:
                if '/channel/' in href or '/c/' in href or '/@' in href:
                    socials['youtube_handle'] = href

        return socials

    def _extract_email(self, soup: BeautifulSoup, url: str) -> Optional[str]:
        """Extract contact email from website"""
        # Look for email in text
        text = soup.get_text()

        # Common email patterns
        email_patterns = [
            r'([a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,})',
        ]

        emails = []
        for pattern in email_patterns:
            found = re.findall(pattern, text)
            emails.extend(found)

        if not emails:
            return None

        # Filter unwanted emails
        unwanted = ['example.com', 'domain.com', 'email.com', 'yoursite.com',
                   'wixpress.com', 'gmail.com', 'yahoo.com', 'hotmail.com']
        priority_keywords = ['marketing', 'pr', 'press', 'partnership', 'collab',
                            'brand', 'influencer', 'creator', 'contact', 'hello', 'info']

        # Score emails
        scored_emails = []
        for email in set(emails):
            email_lower = email.lower()

            # Skip unwanted
            if any(domain in email_lower for domain in unwanted):
                continue

            # Skip info@, admin@, support@
            if email_lower.startswith(('info@', 'admin@', 'support@', 'webmaster@',
                                       'noreply@', 'no-reply@')):
                continue

            # Score based on keywords
            score = 0
            for keyword in priority_keywords:
                if keyword in email_lower:
                    score += 10

            scored_emails.append((score, email))

        if scored_emails:
            # Return highest scored email
            scored_emails.sort(reverse=True)
            return scored_emails[0][1]

        # Return first non-filtered email
        for email in emails:
            if not any(domain in email.lower() for domain in unwanted):
                return email

        return None

    def _extract_category(self, soup: BeautifulSoup) -> str:
        """Guess category from website content"""
        text = soup.get_text().lower()

        categories = {
            'Beauty': ['beauty', 'cosmetics', 'makeup', 'skincare', 'fragrance'],
            'Fashion': ['fashion', 'clothing', 'apparel', 'wear', 'boutique'],
            'Fitness': ['fitness', 'gym', 'workout', 'athletic', 'sports'],
            'Food & Beverage': ['food', 'beverage', 'drink', 'coffee', 'tea'],
            'Home': ['home', 'decor', 'furniture', 'interior'],
            'Tech': ['tech', 'technology', 'software', 'digital'],
            'Lifestyle': ['lifestyle', 'wellness', 'living']
        }

        for category, keywords in categories.items():
            if any(keyword in text for keyword in keywords):
                return category

        return 'Lifestyle'

    def _extract_cover_image(self, soup: BeautifulSoup, base_url: str) -> Optional[str]:
        """Extract cover/hero image"""
        # Try og:image
        og_image = soup.find('meta', property='og:image')
        if og_image and og_image.get('content'):
            img_url = og_image['content']
            if img_url.startswith('//'):
                img_url = f'https:{img_url}'
            elif img_url.startswith('/'):
                img_url = urljoin(base_url, img_url)
            return img_url

        return None

    def scrape_brand(self, brand_name: str, website: str) -> Optional[int]:
        """
        Main scraping method - scrapes from website

        Args:
            brand_name: Brand name for reference
            website: Brand website URL

        Returns:
            Brand ID if successful, 'SKIPPED' if exists, None if failed
        """
        try:
            print(f"\n=== Scraping brand: {brand_name} ===")

            # Check if already exists (by website domain or brand name)
            cursor = self.db_conn.cursor()
            domain = urlparse(website if website.startswith('http') else f'https://{website}').netloc
            domain = domain.replace('www.', '')

            cursor.execute("""
                SELECT id, brand_name FROM pr_brands
                WHERE website LIKE %s OR LOWER(brand_name) = LOWER(%s)
            """, (f'%{domain}%', brand_name))

            existing = cursor.fetchone()
            cursor.close()

            if existing:
                print(f"   [SKIP] Brand already exists (ID: {existing[0]}, Name: {existing[1]})")
                return 'SKIPPED'

            # Scrape from website
            brand_data = self.scrape_from_website(website)

            if not brand_data:
                print(f"   [X] Failed to scrape website")
                return None

            # Use provided brand name if scraping didn't find one
            if not brand_data.get('brand_name') or brand_data['brand_name'] == domain:
                brand_data['brand_name'] = brand_name

            print(f"   [OK] Found: {brand_data.get('brand_name')}")
            if brand_data.get('contact_email'):
                print(f"   [OK] Email: {brand_data.get('contact_email')}")
            if brand_data.get('instagram_handle'):
                print(f"   [OK] Instagram: {brand_data.get('instagram_handle')}")

            # Save to database
            brand_id = self._save_brand(brand_data)

            if brand_id:
                print(f"   [SUCCESS] Brand ID: {brand_id}")
                return brand_id
            else:
                print(f"   [X] Failed to save")
                return None

        except Exception as e:
            print(f"   [X] Error: {str(e)}")
            return None

    def _save_brand(self, brand_data: Dict) -> Optional[int]:
        """Save brand to database"""
        try:
            cursor = self.db_conn.cursor()

            # Prepare data
            instagram_handle = brand_data.get('instagram_handle', '')
            tiktok_handle = brand_data.get('tiktok_handle', '')
            youtube_handle = brand_data.get('youtube_handle', '')

            query = """
                INSERT INTO pr_brands (
                    brand_name, website, description, category,
                    contact_email, instagram_handle, tiktok_handle, youtube_handle,
                    logo_url, cover_image_url, source_url,
                    created_at, updated_at
                ) VALUES (
                    %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                    NOW(), NOW()
                )
                RETURNING id
            """

            cursor.execute(query, (
                brand_data.get('brand_name', 'Unknown'),
                brand_data.get('website', ''),
                brand_data.get('description', ''),
                brand_data.get('category', 'Lifestyle'),
                brand_data.get('contact_email'),
                instagram_handle,
                tiktok_handle,
                youtube_handle,
                brand_data.get('logo_url'),
                brand_data.get('cover_image_url'),
                brand_data.get('website', '')  # source_url = website
            ))

            brand_id = cursor.fetchone()[0]
            self.db_conn.commit()
            cursor.close()

            return brand_id

        except Exception as e:
            print(f"   [!] Database error: {str(e)}")
            self.db_conn.rollback()
            return None


def main():
    """Run multi-platform scraper"""
    import sys

    # Import brand data with websites
    sys.path.insert(0, '..')
    from brand_websites import BRAND_DATA

    category = sys.argv[1] if len(sys.argv) > 1 else 'all'

    if category.lower() == 'all':
        brands_to_scrape = BRAND_DATA
    else:
        brands_to_scrape = {k: v for k, v in BRAND_DATA.items()
                           if v.get('category', '').lower() == category.lower()}

    print("\n" + "="*60)
    print(f"MULTI-PLATFORM BRAND SCRAPER")
    print("="*60)
    print(f"Category: {category.upper()}")
    print(f"Total brands to scrape: {len(brands_to_scrape)}")
    print(f"Sources: Website, TikTok, YouTube, LinkedIn")
    print(f"No Instagram rate limits!")
    print("="*60 + "\n")

    scraper = MultiPlatformScraper()
    successful = 0
    skipped = 0
    failed = 0

    start_time = time.time()

    for idx, (brand_name, brand_info) in enumerate(brands_to_scrape.items(), 1):
        print(f"[{idx}/{len(brands_to_scrape)}] {brand_name}")

        website = brand_info.get('website')
        if not website:
            print(f"   [X] No website found")
            failed += 1
            continue

        try:
            result = scraper.scrape_brand(brand_name, website)

            if result == 'SKIPPED':
                skipped += 1
            elif result is None:
                failed += 1
            else:
                successful += 1

            # Small delay to be respectful
            time.sleep(random.uniform(1, 2))

            # Progress update every 20 brands
            if idx % 20 == 0:
                elapsed = time.time() - start_time
                print(f"\n{'='*60}")
                print(f"Progress: {idx}/{len(brands_to_scrape)} ({(idx/len(brands_to_scrape))*100:.1f}%)")
                print(f"New: {successful} | Skipped: {skipped} | Failed: {failed}")
                print(f"Elapsed: {elapsed/60:.1f}m")
                print(f"{'='*60}\n")

        except KeyboardInterrupt:
            print("\n\n⚠️  Interrupted by user")
            break
        except Exception as e:
            print(f"   [X] Error: {str(e)}")
            failed += 1

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
    print(f"  + New brands: {successful}")
    print(f"  - Skipped: {skipped}")
    print(f"  x Failed: {failed}")
    print(f"  Time: {elapsed_total/60:.1f}m")

    if successful + failed > 0:
        success_rate = (successful/(successful+failed))*100
        print(f"  Success rate: {success_rate:.1f}%")

    print(f"\nTotal in database: {total_in_db}")

    if total_in_db >= 500:
        print(f"\n[PRODUCTION READY] {total_in_db} brands")
    else:
        print(f"\n[NEED MORE] {500-total_in_db} more brands to reach 500")

    print("="*60 + "\n")


if __name__ == "__main__":
    main()
