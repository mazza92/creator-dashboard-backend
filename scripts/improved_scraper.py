"""
Improved Multi-Platform Brand Scraper
Focuses on getting COMPLETE brand data with contact emails
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
import os
from dotenv import load_dotenv

load_dotenv()


class ImprovedBrandScraper:
    """Enhanced scraper that prioritizes complete data extraction"""

    def __init__(self):
        self.db_conn = self._get_db_connection()
        self.session = requests.Session()
        self.session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
        })

    def _get_db_connection(self):
        """Connect to PostgreSQL database"""
        return psycopg2.connect(
            host=os.getenv('DB_HOST'),
            port=os.getenv('DB_PORT'),
            database=os.getenv('DB_NAME'),
            user=os.getenv('DB_USER'),
            password=os.getenv('DB_PASSWORD')
        )

    def scrape_brand(self, brand_name: str, website: str) -> Optional[int]:
        """
        Scrape complete brand data with focus on contact info

        Returns:
            Brand ID if successful, 'SKIPPED' if exists, None if failed
        """
        try:
            print(f"\n=== Scraping: {brand_name} ===")

            # Check if exists
            cursor = self.db_conn.cursor()
            domain = urlparse(website if website.startswith('http') else f'https://{website}').netloc
            domain = domain.replace('www.', '')

            cursor.execute("""
                SELECT id, brand_name, contact_email FROM pr_brands
                WHERE website LIKE %s OR LOWER(brand_name) = LOWER(%s)
            """, (f'%{domain}%', brand_name))

            existing = cursor.fetchone()
            cursor.close()

            if existing:
                # If exists but missing email, try to update it
                if not existing[2]:
                    print(f"   [UPDATE] Brand exists (ID: {existing[0]}) but missing email - updating...")
                    brand_data = self.scrape_website_comprehensive(website)
                    if brand_data and brand_data.get('contact_email'):
                        self._update_brand_email(existing[0], brand_data['contact_email'])
                        print(f"   [OK] Updated email: {brand_data['contact_email']}")
                    return 'UPDATED'
                else:
                    print(f"   [SKIP] Complete data exists (ID: {existing[0]})")
                    return 'SKIPPED'

            # Scrape comprehensive data
            brand_data = self.scrape_website_comprehensive(website)

            if not brand_data:
                print(f"   [X] Failed to scrape")
                return None

            # Use provided brand name if none found
            if not brand_data.get('brand_name'):
                brand_data['brand_name'] = brand_name

            brand_data['website'] = website

            # Show what we found
            print(f"   [OK] Name: {brand_data.get('brand_name')}")
            if brand_data.get('description'):
                desc_preview = brand_data['description'][:50] + '...' if len(brand_data['description']) > 50 else brand_data['description']
                print(f"   [OK] Description: {desc_preview}")
            if brand_data.get('contact_email'):
                print(f"   [OK] Email: {brand_data.get('contact_email')}")
            else:
                print(f"   [!] No email found")
            if brand_data.get('instagram_handle'):
                print(f"   [OK] Instagram: {brand_data.get('instagram_handle')}")

            # Save to database
            brand_id = self._save_brand(brand_data)

            if brand_id:
                print(f"   [SUCCESS] Saved (ID: {brand_id})")
                return brand_id
            else:
                print(f"   [X] Failed to save")
                return None

        except Exception as e:
            print(f"   [X] Error: {str(e)}")
            return None

    def scrape_website_comprehensive(self, url: str) -> Optional[Dict]:
        """Scrape with multiple attempts to get complete data"""
        if not url.startswith('http'):
            url = f'https://{url}'

        brand_data = {}

        # 1. Try homepage
        print(f"   Fetching homepage...")
        homepage_soup = self._fetch_page(url)
        if homepage_soup:
            brand_data.update(self._extract_from_page(homepage_soup, url))

        # 2. If no email found, try contact page
        if not brand_data.get('contact_email'):
            contact_urls = [
                urljoin(url, '/contact'),
                urljoin(url, '/contact-us'),
                urljoin(url, '/about'),
                urljoin(url, '/about-us'),
                urljoin(url, '/press'),
            ]

            for contact_url in contact_urls:
                print(f"   Trying: {contact_url.split('/')[-1]}...")
                contact_soup = self._fetch_page(contact_url)
                if contact_soup:
                    email = self._extract_email_aggressive(contact_soup, contact_url)
                    if email:
                        brand_data['contact_email'] = email
                        print(f"   Found email on {contact_url.split('/')[-1]} page")
                        break

        return brand_data if brand_data else None

    def _fetch_page(self, url: str, timeout: int = 10) -> Optional[BeautifulSoup]:
        """Fetch and parse a page"""
        try:
            response = self.session.get(url, timeout=timeout, allow_redirects=True)
            if response.status_code == 200:
                return BeautifulSoup(response.text, 'html.parser')
        except:
            pass
        return None

    def _extract_from_page(self, soup: BeautifulSoup, url: str) -> Dict:
        """Extract all data from a page"""
        data = {}

        # Brand name
        data['brand_name'] = self._extract_brand_name(soup, url)

        # Description
        data['description'] = self._extract_description(soup)

        # Logo
        data['logo_url'] = self._extract_logo(soup, url)

        # Cover image
        data['cover_image_url'] = self._extract_cover_image(soup, url)

        # Social links
        socials = self._extract_social_links(soup)
        data.update(socials)

        # Email (aggressive extraction)
        data['contact_email'] = self._extract_email_aggressive(soup, url)

        # Category
        data['category'] = self._extract_category(soup)

        return data

    def _extract_brand_name(self, soup: BeautifulSoup, url: str) -> str:
        """Extract brand name"""
        # Try meta tags
        og_title = soup.find('meta', property='og:site_name')
        if og_title and og_title.get('content'):
            return og_title['content'].strip()

        og_title = soup.find('meta', property='og:title')
        if og_title and og_title.get('content'):
            return og_title['content'].strip().split('|')[0].strip().split('-')[0].strip()

        # Try title
        title = soup.find('title')
        if title:
            return title.text.strip().split('|')[0].strip().split('-')[0].strip()

        # Fallback to domain
        domain = urlparse(url).netloc.replace('www.', '').split('.')[0]
        return domain.capitalize()

    def _extract_description(self, soup: BeautifulSoup) -> str:
        """Extract description"""
        # Try meta description
        meta_desc = soup.find('meta', attrs={'name': 'description'})
        if meta_desc and meta_desc.get('content'):
            desc = meta_desc['content'].strip()
            if len(desc) > 20:  # Only accept if meaningful
                return desc

        og_desc = soup.find('meta', property='og:description')
        if og_desc and og_desc.get('content'):
            desc = og_desc['content'].strip()
            if len(desc) > 20:
                return desc

        # Try first meaningful paragraph
        paragraphs = soup.find_all('p')
        for p in paragraphs:
            text = p.get_text().strip()
            if len(text) > 50:  # Skip short paragraphs
                return text[:500]

        return ''

    def _extract_logo(self, soup: BeautifulSoup, base_url: str) -> Optional[str]:
        """Extract logo"""
        # Try og:image
        og_image = soup.find('meta', property='og:image')
        if og_image and og_image.get('content'):
            return urljoin(base_url, og_image['content'])

        # Try apple touch icon
        apple_icon = soup.find('link', rel='apple-touch-icon')
        if apple_icon and apple_icon.get('href'):
            return urljoin(base_url, apple_icon['href'])

        # Try favicon
        icon = soup.find('link', rel='icon')
        if icon and icon.get('href'):
            return urljoin(base_url, icon['href'])

        return None

    def _extract_cover_image(self, soup: BeautifulSoup, base_url: str) -> Optional[str]:
        """Extract cover image"""
        og_image = soup.find('meta', property='og:image')
        if og_image and og_image.get('content'):
            img_url = og_image['content']
            if img_url.startswith('//'):
                img_url = f'https:{img_url}'
            elif img_url.startswith('/'):
                img_url = urljoin(base_url, img_url)
            return img_url
        return None

    def _extract_social_links(self, soup: BeautifulSoup) -> Dict:
        """Extract social media links"""
        socials = {}
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

    def _extract_email_aggressive(self, soup: BeautifulSoup, url: str) -> Optional[str]:
        """
        AGGRESSIVE email extraction
        ACCEPTS info@, contact@, hello@, etc.
        """
        text = soup.get_text()
        html = str(soup)

        # Find ALL emails
        email_pattern = r'\b([a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,})\b'
        emails_from_text = re.findall(email_pattern, text)
        emails_from_html = re.findall(email_pattern, html)

        # Clean emails (remove any extra characters)
        all_emails = []
        for email in set(emails_from_text + emails_from_html):
            # Extract just the email part
            email_clean = re.search(r'([a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,})', email)
            if email_clean:
                all_emails.append(email_clean.group(1))

        if not all_emails:
            return None

        # Filter ONLY truly unwanted emails
        unwanted_domains = ['example.com', 'domain.com', 'email.com', 'yoursite.com',
                           'wixpress.com', 'sentry.io', 'google-analytics.com']
        unwanted_prefixes = ['noreply@', 'no-reply@', 'webmaster@', 'admin@', 'support@']

        # Priority keywords (highest to lowest)
        tier1_keywords = ['pr@', 'press@', 'media@', 'partnership@', 'partnerships@',
                         'collab@', 'marketing@', 'brand@']
        tier2_keywords = ['contact@', 'hello@', 'info@', 'hi@']
        tier3_keywords = ['team@', 'general@']

        # Score emails
        scored_emails = []
        for email in all_emails:
            email_lower = email.lower()

            # Skip unwanted
            if any(domain in email_lower for domain in unwanted_domains):
                continue
            if any(email_lower.startswith(prefix) for prefix in unwanted_prefixes):
                continue

            # Score
            score = 0

            # Tier 1 (PR/Press) - highest priority
            if any(email_lower.startswith(kw) for kw in tier1_keywords):
                score = 100
            # Tier 2 (Contact/Hello/Info) - good priority
            elif any(email_lower.startswith(kw) for kw in tier2_keywords):
                score = 50
            # Tier 3 (Team/General) - acceptable
            elif any(email_lower.startswith(kw) for kw in tier3_keywords):
                score = 25
            # Any other valid email from brand domain
            else:
                # Check if email domain matches website domain
                domain = urlparse(url).netloc.replace('www.', '')
                email_domain = email_lower.split('@')[1]
                if domain in email_domain or email_domain in domain:
                    score = 10

            if score > 0:
                scored_emails.append((score, email))

        if scored_emails:
            scored_emails.sort(reverse=True)
            return scored_emails[0][1]

        return None

    def _extract_category(self, soup: BeautifulSoup) -> str:
        """Extract category"""
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

    def _save_brand(self, brand_data: Dict) -> Optional[int]:
        """Save brand to database"""
        try:
            cursor = self.db_conn.cursor()

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
                brand_data.get('instagram_handle', ''),
                brand_data.get('tiktok_handle', ''),
                brand_data.get('youtube_handle', ''),
                brand_data.get('logo_url'),
                brand_data.get('cover_image_url'),
                brand_data.get('website', '')
            ))

            brand_id = cursor.fetchone()[0]
            self.db_conn.commit()
            cursor.close()

            return brand_id

        except Exception as e:
            print(f"   [!] Database error: {str(e)}")
            self.db_conn.rollback()
            return None

    def _update_brand_email(self, brand_id: int, email: str):
        """Update existing brand with email"""
        try:
            cursor = self.db_conn.cursor()
            cursor.execute("""
                UPDATE pr_brands
                SET contact_email = %s, updated_at = NOW()
                WHERE id = %s
            """, (email, brand_id))
            self.db_conn.commit()
            cursor.close()
        except Exception as e:
            print(f"   [!] Update error: {str(e)}")
            self.db_conn.rollback()


def main():
    """Run improved scraper"""
    import sys
    sys.path.insert(0, '..')
    from brand_websites import BRAND_DATA

    category = sys.argv[1] if len(sys.argv) > 1 else 'all'

    if category.lower() == 'all':
        brands_to_scrape = BRAND_DATA
    else:
        brands_to_scrape = {k: v for k, v in BRAND_DATA.items()
                           if v.get('category', '').lower() == category.lower()}

    print("\n" + "="*80)
    print("IMPROVED MULTI-PLATFORM SCRAPER")
    print("="*80)
    print(f"Category: {category.upper()}")
    print(f"Total brands: {len(brands_to_scrape)}")
    print(f"Focus: COMPLETE DATA with contact emails")
    print("="*80 + "\n")

    scraper = ImprovedBrandScraper()
    successful = 0
    skipped = 0
    updated = 0
    failed = 0

    start_time = time.time()

    for idx, (brand_name, brand_info) in enumerate(brands_to_scrape.items(), 1):
        print(f"\n[{idx}/{len(brands_to_scrape)}]", end=' ')

        website = brand_info.get('website')
        if not website:
            print(f"{brand_name}: No website")
            failed += 1
            continue

        try:
            result = scraper.scrape_brand(brand_name, website)

            if result == 'SKIPPED':
                skipped += 1
            elif result == 'UPDATED':
                updated += 1
            elif result is None:
                failed += 1
            else:
                successful += 1

            # Respectful delay
            time.sleep(random.uniform(1, 2))

            # Progress every 20 brands
            if idx % 20 == 0:
                elapsed = time.time() - start_time
                print(f"\n{'='*80}")
                print(f"Progress: {idx}/{len(brands_to_scrape)} ({(idx/len(brands_to_scrape))*100:.1f}%)")
                print(f"New: {successful} | Updated: {updated} | Skipped: {skipped} | Failed: {failed}")
                print(f"Time: {elapsed/60:.1f}m")
                print(f"{'='*80}")

        except KeyboardInterrupt:
            print("\n\nInterrupted")
            break
        except Exception as e:
            print(f"   Error: {str(e)}")
            failed += 1

    # Final summary
    cursor = scraper.db_conn.cursor()
    cursor.execute('SELECT COUNT(*) FROM pr_brands')
    total = cursor.fetchone()[0]
    cursor.execute("SELECT COUNT(*) FROM pr_brands WHERE contact_email IS NOT NULL AND contact_email != ''")
    with_email = cursor.fetchone()[0]
    cursor.close()

    elapsed_total = time.time() - start_time

    print("\n" + "="*80)
    print("COMPLETE!")
    print("="*80)
    print(f"\nResults:")
    print(f"  + New brands: {successful}")
    print(f"  ^ Updated (added emails): {updated}")
    print(f"  - Skipped: {skipped}")
    print(f"  x Failed: {failed}")
    print(f"  Time: {elapsed_total/60:.1f}m")

    print(f"\nDatabase Status:")
    print(f"  Total brands: {total}")
    print(f"  With emails: {with_email} ({(with_email/total)*100:.1f}%)")

    if with_email / total >= 0.7:
        print(f"\n[GOOD] {(with_email/total)*100:.1f}% have contact emails")
    else:
        print(f"\n[NEEDS WORK] Only {(with_email/total)*100:.1f}% have emails")

    if total >= 500:
        print(f"\n[PRODUCTION READY] {total} brands")
    else:
        print(f"\n[NEED MORE] {500-total} more brands")

    print("="*80 + "\n")


if __name__ == "__main__":
    main()
