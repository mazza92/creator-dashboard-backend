"""
Free Brand Scraper - No paid APIs required
Uses free tools: Instaloader, Google search, email pattern generation
"""

import os
import re
import json
import time
import requests
from typing import Dict, List, Optional
from datetime import datetime
import psycopg2
from dotenv import load_dotenv

try:
    import instaloader
except ImportError:
    print("Installing instaloader...")
    os.system("pip install instaloader")
    import instaloader

load_dotenv()


class FreeBrandScraper:
    def __init__(self):
        self.db_conn = self._get_db_connection()
        self.instagram_loader = instaloader.Instaloader()

    def _get_db_connection(self):
        """Connect to PostgreSQL database"""
        return psycopg2.connect(
            host=os.getenv('DB_HOST', 'localhost'),
            database=os.getenv('DB_NAME', 'creator_dashboard'),
            user=os.getenv('DB_USER', 'postgres'),
            password=os.getenv('DB_PASSWORD')
        )

    def scrape_instagram_free(self, username: str) -> Optional[Dict]:
        """
        Scrape Instagram using free instaloader library
        100% free, no API required
        """
        try:
            username = username.replace('@', '')

            # Get profile
            profile = instaloader.Profile.from_username(
                self.instagram_loader.context,
                username
            )

            # Extract data
            category = profile.business_category_name or ''
            # Don't save 'None' as category
            if category in ['None', 'none', 'NONE']:
                category = ''

            brand_data = {
                'brand_name': profile.full_name or username,
                'instagram_handle': f'@{username}',
                'description': profile.biography or '',
                'website': profile.external_url or '',
                'followers_count': profile.followers,
                'is_business': profile.is_business_account,
                'is_verified': profile.is_verified,
                'category': category,
                'profile_pic': profile.profile_pic_url,
                'data_source': 'instagram_free'
            }

            return brand_data

        except instaloader.exceptions.ProfileNotExistsException:
            print(f"Profile {username} does not exist")
            return None
        except Exception as e:
            print(f"Error scraping {username}: {str(e)}")
            return None

    def extract_meta_description(self, url: str) -> Optional[str]:
        """
        Extract meta description from website for better brand description
        """
        try:
            if not url:
                return None

            if not url.startswith('http'):
                url = f'https://{url}'

            headers = {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
            }
            response = requests.get(url, headers=headers, timeout=10)

            if response.status_code != 200:
                return None

            try:
                from bs4 import BeautifulSoup
                soup = BeautifulSoup(response.text, 'html.parser')

                # Try meta description tag
                meta_desc = soup.find('meta', attrs={'name': 'description'})
                if meta_desc and meta_desc.get('content'):
                    return meta_desc.get('content').strip()

                # Try og:description
                og_desc = soup.find('meta', attrs={'property': 'og:description'})
                if og_desc and og_desc.get('content'):
                    return og_desc.get('content').strip()

                # Try twitter:description
                twitter_desc = soup.find('meta', attrs={'name': 'twitter:description'})
                if twitter_desc and twitter_desc.get('content'):
                    return twitter_desc.get('content').strip()

            except ImportError:
                print("   [INFO] BeautifulSoup not installed, skipping meta description")
                return None

            return None

        except Exception as e:
            print(f"   [INFO] Could not extract meta description: {str(e)}")
            return None

    def extract_cover_image(self, url: str, instagram_handle: str = None) -> Optional[str]:
        """
        Extract cover/hero image from brand's website or Instagram
        Priority: og:image > twitter:image > first Instagram post
        """
        # Try to get image from website first
        if url:
            try:
                if not url.startswith('http'):
                    url = f'https://{url}'

                headers = {
                    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
                }
                response = requests.get(url, headers=headers, timeout=10)

                if response.status_code == 200:
                    try:
                        from bs4 import BeautifulSoup
                        soup = BeautifulSoup(response.text, 'html.parser')

                        # Try og:image (best quality usually)
                        og_image = soup.find('meta', attrs={'property': 'og:image'})
                        if og_image and og_image.get('content'):
                            img_url = og_image.get('content').strip()
                            if img_url:
                                # Make absolute URL if relative
                                if img_url.startswith('//'):
                                    img_url = f'https:{img_url}'
                                elif img_url.startswith('/'):
                                    from urllib.parse import urljoin
                                    img_url = urljoin(url, img_url)
                                return img_url

                        # Try twitter:image
                        twitter_image = soup.find('meta', attrs={'name': 'twitter:image'})
                        if twitter_image and twitter_image.get('content'):
                            img_url = twitter_image.get('content').strip()
                            if img_url:
                                if img_url.startswith('//'):
                                    img_url = f'https:{img_url}'
                                elif img_url.startswith('/'):
                                    from urllib.parse import urljoin
                                    img_url = urljoin(url, img_url)
                                return img_url

                    except ImportError:
                        pass  # BeautifulSoup not available
            except Exception as e:
                print(f"   [INFO] Could not extract cover image from website: {str(e)}")

        # Fallback: Try to get from Instagram recent post
        if instagram_handle:
            try:
                username = instagram_handle.replace('@', '')
                profile = instaloader.Profile.from_username(
                    self.instagram_loader.context,
                    username
                )

                # Get first post's thumbnail
                posts = profile.get_posts()
                first_post = next(posts, None)
                if first_post:
                    return first_post.url

            except Exception as e:
                print(f"   [INFO] Could not extract Instagram post image: {str(e)}")

        return None

    def extract_emails_from_website(self, url: str) -> List[str]:
        """
        Extract emails from website HTML
        Free method using regex patterns
        """
        try:
            if not url:
                return []

            # Ensure URL has protocol
            if not url.startswith('http'):
                url = f'https://{url}'

            # Fetch website content
            headers = {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
            }
            response = requests.get(url, headers=headers, timeout=10)

            if response.status_code != 200:
                return []

            # Extract emails using regex
            email_pattern = r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b'
            emails = re.findall(email_pattern, response.text)

            # Filter out generic/unwanted emails
            filtered_emails = []
            unwanted_patterns = [
                'example.com', 'test.com', 'email.com', '@sso', '.png', '.jpg',
                'sentry.io', 'localhost', '127.0.0.1', 'noreply', 'no-reply',
                'donotreply', 'amazonaws.com', 'cloudfront.net', 'googleusercontent.com',
                'fbcdn.net', 'fastly.net', 'akamai', 'tracking', 'analytics',
                '@o4', '@o5', '@o6', '@ingest', 'bugsnag', 'raygun', 'rollbar',
                'loggly', 'splunk', 'datadog', 'newrelic'
            ]
            unwanted_starts = ['admin@', 'info@', 'support@', 'help@', 'sales@', 'webmaster@']
            priority_keywords = ['marketing', 'pr', 'partnership', 'collab', 'brand', 'press']

            for email in set(emails):  # Remove duplicates
                email_lower = email.lower()

                # Skip unwanted patterns
                if any(pattern in email_lower for pattern in unwanted_patterns):
                    continue

                # Skip if starts with unwanted prefix
                if any(email_lower.startswith(prefix) for prefix in unwanted_starts):
                    continue

                # Skip if email contains random hash-like strings (32+ hex chars)
                if re.search(r'[a-f0-9]{32,}', email_lower):
                    continue

                # Skip if domain is just numbers (like o4504566675341312.ingest.sentry.io)
                domain = email_lower.split('@')[1] if '@' in email_lower else ''
                if re.search(r'@o\d{10,}', email_lower) or re.search(r'\d{10,}\.', domain):
                    continue

                # Prioritize marketing/PR emails
                if any(keyword in email_lower for keyword in priority_keywords):
                    filtered_emails.insert(0, email)  # Add to front
                else:
                    filtered_emails.append(email)

            return filtered_emails

        except Exception as e:
            print(f"Error extracting emails from {url}: {str(e)}")
            return []

    def guess_brand_emails(self, domain: str, brand_name: str) -> List[str]:
        """
        Generate probable email addresses based on common patterns
        Free method - no API required
        """
        if not domain:
            return []

        # Clean domain
        domain = domain.replace('http://', '').replace('https://', '').split('/')[0].lower()
        # Remove www. prefix if present
        if domain.startswith('www.'):
            domain = domain[4:]

        # Common email patterns for brands
        patterns = [
            f'marketing@{domain}',
            f'pr@{domain}',
            f'partnerships@{domain}',
            f'collabs@{domain}',
            f'brand@{domain}',
            f'press@{domain}',
            f'influencer@{domain}',
            f'hello@{domain}',
            f'contact@{domain}',
            f'info@{domain}'
        ]

        return patterns

    def simple_email_check(self, email: str) -> bool:
        """
        Basic email validation without paid API
        Checks MX records to verify domain accepts email
        """
        try:
            import dns.resolver

            # Extract domain from email
            domain = email.split('@')[1]

            # Check MX records
            mx_records = dns.resolver.resolve(domain, 'MX')

            # If MX records exist, domain can receive email
            return len(mx_records) > 0

        except:
            # If dns.resolver not available or error, assume valid
            # (Better to have false positives than lose real contacts)
            return True

    def scrape_contact_page(self, website: str) -> Optional[Dict]:
        """
        Find and scrape contact page for email addresses
        Free method using requests + BeautifulSoup
        """
        try:
            from bs4 import BeautifulSoup

            if not website:
                return None

            if not website.startswith('http'):
                website = f'https://{website}'

            # Common contact page URLs
            contact_urls = [
                f'{website}/contact',
                f'{website}/contact-us',
                f'{website}/press',
                f'{website}/pr',
                f'{website}/partnerships',
                f'{website}/about/contact',
                f'{website}/pages/contact'
            ]

            headers = {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
            }

            for url in contact_urls:
                try:
                    response = requests.get(url, headers=headers, timeout=5)
                    if response.status_code == 200:
                        soup = BeautifulSoup(response.text, 'html.parser')

                        # Extract emails from contact page
                        emails = self.extract_emails_from_website(url)
                        if emails:
                            return {
                                'contact_page_url': url,
                                'emails_found': emails,
                                'primary_email': emails[0]
                            }
                except:
                    continue

            return None

        except Exception as e:
            print(f"Error scraping contact page: {str(e)}")
            return None

    def estimate_product_value(self, description: str, category: str) -> int:
        """
        Estimate average product value based on brand category and description
        """
        # Price keywords and their estimated values
        luxury_keywords = ['luxury', 'premium', 'high-end', 'exclusive', 'designer']
        mid_range_keywords = ['quality', 'professional', 'certified']

        description_lower = (description or '').lower()
        category_lower = (category or '').lower()

        # Category-based estimates
        category_values = {
            'beauty': 35,
            'skincare': 45,
            'makeup': 30,
            'fashion': 60,
            'jewelry': 150,
            'tech': 200,
            'fitness': 50,
            'food': 25,
            'supplement': 40
        }

        base_value = 50  # Default

        # Check category
        for cat, value in category_values.items():
            if cat in category_lower:
                base_value = value
                break

        # Adjust for luxury indicators
        if any(keyword in description_lower for keyword in luxury_keywords):
            base_value *= 3
        elif any(keyword in description_lower for keyword in mid_range_keywords):
            base_value *= 1.5

        return int(base_value)

    def scrape_full_brand_free(self, instagram_handle: str) -> Optional[int]:
        """
        Complete free scraping workflow:
        1. Check if brand already exists
        2. Scrape Instagram (free)
        3. Extract emails from website (free)
        4. Generate probable emails (free)
        5. Basic MX record validation (free)
        6. Save to database
        """
        print(f"\n=== Scraping brand: {instagram_handle} (FREE METHOD) ===")

        # Step 0: Check if brand already exists
        instagram_handle_formatted = f'@{instagram_handle.replace("@", "")}'
        cursor = self.db_conn.cursor()
        cursor.execute(
            "SELECT id, brand_name FROM pr_brands WHERE instagram_handle = %s",
            (instagram_handle_formatted,)
        )
        existing = cursor.fetchone()
        cursor.close()

        if existing:
            print(f"   [SKIP] Brand already exists (ID: {existing[0]}, Name: {existing[1]})")
            return 'SKIPPED'  # Return special value to indicate skipped (not failed)

        # Step 1: Instagram scraping
        print("1. Scraping Instagram (free)...")
        brand_data = self.scrape_instagram_free(instagram_handle)

        if not brand_data:
            print("   [X] Failed to scrape Instagram")
            return None

        print(f"   [OK] Found: {brand_data.get('brand_name')}")
        website = brand_data.get('website')

        if not website:
            print("   [X] No website found")
            # Still save the brand, just without email
            return self.save_brand_to_db(brand_data)

        # Step 2: Extract better description from website meta tags
        print("2. Extracting meta description from website...")
        meta_description = self.extract_meta_description(website)
        if meta_description:
            print(f"   [OK] Using website description: {meta_description[:80]}...")
            brand_data['description'] = meta_description
        else:
            print(f"   [X] No meta description found, using Instagram bio")

        # Step 2.5: Extract cover image from website or Instagram
        print("2.5. Extracting cover image...")
        cover_image = self.extract_cover_image(website, brand_data.get('instagram_handle'))
        if cover_image:
            print(f"   [OK] Found cover image: {cover_image[:60]}...")
            brand_data['cover_image_url'] = cover_image
        else:
            print(f"   [X] No cover image found")

        # Step 3: Extract emails from website
        print("3. Extracting emails from website...")
        extracted_emails = self.extract_emails_from_website(website)

        if extracted_emails:
            print(f"   [OK] Found {len(extracted_emails)} emails: {extracted_emails[0]}")
            brand_data['contact_email'] = extracted_emails[0]
        else:
            print("   [X] No emails found on website")

        # Step 4: Try contact page
        if not brand_data.get('contact_email'):
            print("4. Checking contact page...")
            contact_data = self.scrape_contact_page(website)
            if contact_data and contact_data.get('primary_email'):
                brand_data['contact_email'] = contact_data['primary_email']
                print(f"   [OK] Found on contact page: {contact_data['primary_email']}")
            else:
                print("   [X] No contact page email found")

        # Step 5: Generate probable emails if none found
        if not brand_data.get('contact_email'):
            print("5. Generating probable emails...")
            probable_emails = self.guess_brand_emails(website, brand_data.get('brand_name'))
            if probable_emails:
                brand_data['contact_email'] = probable_emails[0]
                brand_data['probable_emails'] = probable_emails[:5]
                print(f"   -> Generated: {probable_emails[0]} (unverified)")

        # Step 6: Basic email validation
        if brand_data.get('contact_email'):
            print("6. Validating email (MX check)...")
            is_valid = self.simple_email_check(brand_data['contact_email'])
            brand_data['email_verified'] = is_valid
            if is_valid:
                print(f"   [OK] Email domain valid")
            else:
                print(f"   [X] Email domain invalid")

        # Step 7: Estimate product value
        avg_value = self.estimate_product_value(
            brand_data.get('description', ''),
            brand_data.get('category', '')
        )
        brand_data['avg_product_value'] = avg_value
        brand_data['collaboration_type'] = 'gifting'  # Default assumption
        brand_data['payment_offered'] = brand_data.get('followers_count', 0) > 100000

        # Step 8: Save to database
        print("7. Saving to database...")
        brand_id = self.save_brand_to_db(brand_data)

        if brand_id:
            print(f"   [OK] Saved as Brand ID: {brand_id}")
        else:
            print(f"   [X] Failed to save")

        # Rate limiting to avoid bans
        time.sleep(3)

        return brand_id

    def save_brand_to_db(self, brand_data: Dict) -> Optional[int]:
        """Save brand to database"""
        try:
            cursor = self.db_conn.cursor()

            # Check if brand already exists
            cursor.execute(
                "SELECT id FROM pr_brands WHERE instagram_handle = %s",
                (brand_data.get('instagram_handle'),)
            )
            existing = cursor.fetchone()

            if existing:
                # Update existing brand
                brand_id = existing[0]
                cursor.execute("""
                    UPDATE pr_brands SET
                        brand_name = COALESCE(%s, brand_name),
                        website = COALESCE(%s, website),
                        contact_email = COALESCE(%s, contact_email),
                        category = COALESCE(%s, category),
                        min_followers = COALESCE(%s, min_followers),
                        notes = COALESCE(%s, notes),
                        logo_url = COALESCE(%s, logo_url),
                        cover_image_url = COALESCE(%s, cover_image_url),
                        avg_product_value = COALESCE(%s, avg_product_value),
                        collaboration_type = COALESCE(%s, collaboration_type),
                        payment_offered = COALESCE(%s, payment_offered)
                    WHERE id = %s
                """, (
                    brand_data.get('brand_name'),
                    brand_data.get('website'),
                    brand_data.get('contact_email'),
                    brand_data.get('category'),
                    brand_data.get('followers_count'),
                    f"Scraped: {brand_data.get('description', '')[:200]}",
                    brand_data.get('profile_pic'),
                    brand_data.get('cover_image_url'),
                    brand_data.get('avg_product_value', 50),
                    brand_data.get('collaboration_type', 'gifting'),
                    brand_data.get('payment_offered', False),
                    brand_id
                ))
                self.db_conn.commit()
            else:
                # Insert new brand
                cursor.execute("""
                    INSERT INTO pr_brands (
                        brand_name, website, contact_email, instagram_handle,
                        category, min_followers, logo_url, cover_image_url, notes,
                        niches, regions, platforms,
                        avg_product_value, collaboration_type, payment_offered
                    ) VALUES (
                        %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
                    ) RETURNING id
                """, (
                    brand_data.get('brand_name'),
                    brand_data.get('website'),
                    brand_data.get('contact_email'),
                    brand_data.get('instagram_handle'),
                    brand_data.get('category', 'Other'),
                    brand_data.get('followers_count', 0),
                    brand_data.get('profile_pic'),
                    brand_data.get('cover_image_url'),
                    f"Scraped: {brand_data.get('description', '')[:200]}",
                    json.dumps([brand_data.get('category', 'Other')]),  # niches as array
                    json.dumps(['Global']),  # default regions
                    json.dumps(['Instagram']),  # platforms
                    brand_data.get('avg_product_value', 50),
                    brand_data.get('collaboration_type', 'gifting'),
                    brand_data.get('payment_offered', False)
                ))
                brand_id = cursor.fetchone()[0]
                self.db_conn.commit()

            cursor.close()
            return brand_id

        except Exception as e:
            print(f"Error saving brand: {str(e)}")
            import traceback
            print(traceback.format_exc())
            self.db_conn.rollback()
            return None


def main():
    """Example usage"""
    import sys

    scraper = FreeBrandScraper()

    # Expanded curated lists by category
    beauty_brands = [
        # Top beauty brands
        'glossier', 'fentybeauty', 'kyliecosmetics', 'rarebeauty', 'milkmakeup',
        'hudabeauty', 'patmcgrathreal', 'charlottetilbury', 'lauramercier', 'narsissist',
        'anastasiabeverlyhills', 'benefitcosmetics', 'toofaced', 'urbandecaycosmetics',
        'nyxcosmetics', 'maybelline', 'loreal', 'covergirl', 'elfcosmetics',
        'physiciansformula', 'wetnwildbeauty', 'blackupispower', 'juviasplace',
        'morphebrushes', 'colourpopcosmetics', 'bhcosmetics', 'makeuprevolution',
        # Skincare
        'theordinary', 'cerave', 'cetaphil', 'larocheposay', 'neutrogena',
        'drunkelephant', 'tatcha', 'glow_recipe', 'summerfriedaysinc', 'youthtothepeople',
        'inisfree', 'laneige', 'korres', 'fresh', 'kiehlsus',
        'clinique', 'esteelauder', 'shiseido', 'origins', 'skinceuticals',
        # Indie/DTC beauty
        'ilia', 'rmsbeauty', 'vapourbeauty', 'kjaerweis', 'ritueldefille',
        'beautycounter', 'merit', 'jonesmroadbeauty', 'saiebeauty', 'victoriabeautybeckham'
    ]

    fashion_brands = [
        'fashionnova', 'prettylittlething', 'boohoo', 'revolve', 'asos',
        'zaful', 'shein', 'misguided', 'nastygal', 'dollskill',
        'gymshark', 'fabletics', 'lululemon', 'alo', 'outdoorvoices',
        'everlane', 'reformation', 'aritzia', 'freepeople', 'urbanoutfitters',
        'zara', 'hm', 'mango', 'uniqlo', 'gap'
    ]

    lifestyle_brands = [
        'amazon', 'target', 'walmart', 'nordstrom', 'sephora',
        'ulta', 'bathandbodyworks', 'lushcosmetics', 'theBodyShop', 'spacenk'
    ]

    # Determine which category to scrape (default to beauty)
    category = sys.argv[1] if len(sys.argv) > 1 else 'beauty'

    if category.lower() == 'fashion':
        brands_to_scrape = fashion_brands
    elif category.lower() == 'lifestyle':
        brands_to_scrape = lifestyle_brands
    else:  # default to beauty
        brands_to_scrape = beauty_brands

    print(f"Category: {category.upper()}")
    print(f"Starting free brand scraping...")
    print(f"Total brands to scrape: {len(brands_to_scrape)}\n")

    successful = 0
    skipped = 0
    failed = 0

    for brand in brands_to_scrape:
        try:
            result = scraper.scrape_full_brand_free(brand)
            if result == 'SKIPPED':
                skipped += 1
            elif result is None:
                failed += 1
            else:
                # Got a brand ID back - successfully created new brand
                successful += 1
        except Exception as e:
            print(f"Error scraping {brand}: {str(e)}")
            failed += 1
            continue

    # Get final count from database to show actual new brands
    cursor = scraper.db_conn.cursor()
    cursor.execute('SELECT COUNT(*) FROM pr_brands')
    total_in_db = cursor.fetchone()[0]
    cursor.close()

    print(f"\n{'='*60}")
    print("SCRAPING COMPLETE!")
    print(f"{'='*60}")
    print(f"\nResults:")
    print(f"  ✓ New brands created: {successful}")
    print(f"  ⊘ Already existed (skipped): {skipped}")
    print(f"  ✗ Failed: {failed}")

    if successful + failed > 0:
        print(f"  📊 Success rate: {(successful/(successful+failed)*100):.1f}%")

    print(f"\nTotal brands in database: {total_in_db}")

    if successful > 0:
        print(f"\n🎉 Added {successful} new brands! Total: {total_in_db}")
    elif skipped > 0:
        print(f"\n✓ All {skipped} brands already exist in database")
    else:
        print(f"\n⚠️  No new brands were added")


if __name__ == "__main__":
    main()
