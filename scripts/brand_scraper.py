"""
Brand Scraper - Multi-source brand data collection system
Scrapes brand data from Instagram, LinkedIn, and other sources
Finds and verifies contact emails
"""

import os
import re
import json
import time
import requests
from typing import Dict, List, Optional
from datetime import datetime
import psycopg2
from psycopg2.extras import RealDictCursor
from dotenv import load_dotenv

# Third-party APIs (you'll need to sign up for these)
# - RapidAPI Instagram scraper
# - Hunter.io for email finding
# - ZeroBounce for email verification
# - Clearbit for company enrichment

load_dotenv()

class BrandScraper:
    def __init__(self):
        self.db_conn = self._get_db_connection()

        # API keys (set these in .env)
        self.hunter_api_key = os.getenv('HUNTER_API_KEY')
        self.zerobounce_api_key = os.getenv('ZEROBOUNCE_API_KEY')
        self.rapidapi_key = os.getenv('RAPIDAPI_KEY')
        self.clearbit_api_key = os.getenv('CLEARBIT_API_KEY')

    def _get_db_connection(self):
        """Connect to PostgreSQL database"""
        return psycopg2.connect(
            host=os.getenv('DB_HOST', 'localhost'),
            database=os.getenv('DB_NAME', 'creator_dashboard'),
            user=os.getenv('DB_USER', 'postgres'),
            password=os.getenv('DB_PASSWORD')
        )

    def scrape_instagram_brand(self, instagram_handle: str) -> Optional[Dict]:
        """
        Scrape brand data from Instagram
        Uses RapidAPI Instagram scraper
        """
        try:
            # Remove @ if present
            handle = instagram_handle.replace('@', '')

            # RapidAPI Instagram Profile endpoint
            url = f"https://instagram-scraper-api2.p.rapidapi.com/v1/info"

            headers = {
                "X-RapidAPI-Key": self.rapidapi_key,
                "X-RapidAPI-Host": "instagram-scraper-api2.p.rapidapi.com"
            }

            params = {"username_or_id_or_url": handle}

            response = requests.get(url, headers=headers, params=params)

            if response.status_code == 200:
                data = response.json()

                # Extract relevant data
                profile = data.get('data', {})

                brand_data = {
                    'brand_name': profile.get('full_name', handle),
                    'instagram_handle': f"@{handle}",
                    'description': profile.get('biography', ''),
                    'website': profile.get('external_url', ''),
                    'followers_count': profile.get('follower_count', 0),
                    'profile_pic': profile.get('profile_pic_url', ''),
                    'is_business': profile.get('is_business_account', False),
                    'is_verified': profile.get('is_verified', False),
                    'category': profile.get('category_name', ''),
                    'data_source': 'instagram'
                }

                return brand_data
            else:
                print(f"Failed to scrape Instagram: {response.status_code}")
                return None

        except Exception as e:
            print(f"Error scraping Instagram {instagram_handle}: {str(e)}")
            return None

    def find_brand_email(self, domain: str, brand_name: str) -> Optional[Dict]:
        """
        Find brand contact email using Hunter.io
        Filters out admin@, info@, etc. to find marketing/PR contacts
        """
        try:
            if not domain or not self.hunter_api_key:
                return None

            # Clean domain
            domain = domain.replace('http://', '').replace('https://', '').split('/')[0]

            # Hunter.io Domain Search API
            url = f"https://api.hunter.io/v2/domain-search"
            params = {
                'domain': domain,
                'api_key': self.hunter_api_key,
                'limit': 10
            }

            response = requests.get(url, params=params)

            if response.status_code == 200:
                data = response.json()
                emails = data.get('data', {}).get('emails', [])

                # Filter emails - prioritize marketing, PR, partnerships
                priority_keywords = ['marketing', 'pr', 'partnership', 'collab', 'brand', 'influencer']
                role_emails = ['admin', 'info', 'support', 'sales', 'hello', 'contact']

                best_email = None
                best_score = 0

                for email_data in emails:
                    email = email_data.get('value', '')
                    position = email_data.get('position', '').lower()
                    first_name = email_data.get('first_name', '').lower()

                    # Skip role-based emails
                    if any(role in email.lower() for role in role_emails):
                        continue

                    # Calculate score based on position/title
                    score = 0
                    for keyword in priority_keywords:
                        if keyword in position or keyword in first_name:
                            score += 10

                    # Prefer emails with higher confidence
                    confidence = email_data.get('confidence', 0)
                    score += confidence

                    if score > best_score:
                        best_score = score
                        best_email = {
                            'email': email,
                            'position': position,
                            'first_name': email_data.get('first_name', ''),
                            'last_name': email_data.get('last_name', ''),
                            'confidence': confidence
                        }

                return best_email

            return None

        except Exception as e:
            print(f"Error finding email for {domain}: {str(e)}")
            return None

    def verify_email(self, email: str) -> Dict:
        """
        Verify email using ZeroBounce API
        Checks if email is valid, deliverable, not disposable
        """
        try:
            if not self.zerobounce_api_key:
                return {'is_valid': False, 'reason': 'No API key'}

            url = f"https://api.zerobounce.net/v2/validate"
            params = {
                'api_key': self.zerobounce_api_key,
                'email': email
            }

            response = requests.get(url, params=params)

            if response.status_code == 200:
                data = response.json()

                status = data.get('status', 'unknown')

                return {
                    'is_valid': status == 'valid',
                    'is_disposable': data.get('sub_status') == 'disposable',
                    'is_role_based': data.get('sub_status') == 'role_based',
                    'mx_records_valid': status in ['valid', 'catch-all'],
                    'status': status,
                    'sub_status': data.get('sub_status', ''),
                    'free_email': data.get('free_email', False)
                }

            return {'is_valid': False, 'reason': 'API error'}

        except Exception as e:
            print(f"Error verifying email {email}: {str(e)}")
            return {'is_valid': False, 'reason': str(e)}

    def enrich_with_clearbit(self, domain: str) -> Optional[Dict]:
        """
        Enrich brand data using Clearbit Company API
        Gets company size, industry, description, etc.
        """
        try:
            if not self.clearbit_api_key:
                return None

            # Clean domain
            domain = domain.replace('http://', '').replace('https://', '').split('/')[0]

            url = f"https://company.clearbit.com/v2/companies/find"
            headers = {
                'Authorization': f'Bearer {self.clearbit_api_key}'
            }
            params = {'domain': domain}

            response = requests.get(url, headers=headers, params=params)

            if response.status_code == 200:
                data = response.json()

                return {
                    'description': data.get('description', ''),
                    'industry': data.get('category', {}).get('industry', ''),
                    'company_size': data.get('metrics', {}).get('employeesRange', ''),
                    'location': data.get('geo', {}).get('city', ''),
                    'logo': data.get('logo', ''),
                    'twitter_handle': data.get('twitter', {}).get('handle', ''),
                    'linkedin_url': data.get('linkedin', {}).get('handle', '')
                }

            return None

        except Exception as e:
            print(f"Error enriching with Clearbit {domain}: {str(e)}")
            return None

    def save_brand_to_db(self, brand_data: Dict) -> int:
        """Save or update brand in database"""
        try:
            cursor = self.db_conn.cursor()

            # Check if brand exists
            cursor.execute(
                "SELECT id FROM brands WHERE instagram_handle = %s OR website = %s",
                (brand_data.get('instagram_handle'), brand_data.get('website'))
            )
            existing = cursor.fetchone()

            if existing:
                # Update existing brand
                brand_id = existing[0]
                cursor.execute("""
                    UPDATE brands SET
                        brand_name = COALESCE(%s, brand_name),
                        description = COALESCE(%s, description),
                        website = COALESCE(%s, website),
                        contact_email = COALESCE(%s, contact_email),
                        instagram_handle = COALESCE(%s, instagram_handle),
                        category = COALESCE(%s, category),
                        min_followers = COALESCE(%s, min_followers),
                        company_size = COALESCE(%s, company_size),
                        industry = COALESCE(%s, industry),
                        avg_product_value = COALESCE(%s, avg_product_value),
                        collaboration_type = COALESCE(%s, collaboration_type),
                        payment_offered = COALESCE(%s, payment_offered),
                        email_verified = COALESCE(%s, email_verified),
                        data_source = %s,
                        last_verified = CURRENT_TIMESTAMP,
                        verification_status = %s,
                        scrape_metadata = %s
                    WHERE id = %s
                """, (
                    brand_data.get('brand_name'),
                    brand_data.get('description'),
                    brand_data.get('website'),
                    brand_data.get('contact_email'),
                    brand_data.get('instagram_handle'),
                    brand_data.get('category'),
                    brand_data.get('followers_count'),
                    brand_data.get('company_size'),
                    brand_data.get('industry'),
                    brand_data.get('avg_product_value'),
                    brand_data.get('collaboration_type'),
                    brand_data.get('payment_offered'),
                    brand_data.get('email_verified'),
                    brand_data.get('data_source', 'scraper'),
                    'verified' if brand_data.get('email_verified') else 'pending',
                    json.dumps(brand_data.get('metadata', {})),
                    brand_id
                ))
            else:
                # Insert new brand
                cursor.execute("""
                    INSERT INTO brands (
                        brand_name, description, website, contact_email, instagram_handle,
                        category, min_followers, company_size, industry, avg_product_value,
                        collaboration_type, payment_offered, email_verified, data_source,
                        verification_status, scrape_metadata, last_verified
                    ) VALUES (
                        %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, CURRENT_TIMESTAMP
                    ) RETURNING id
                """, (
                    brand_data.get('brand_name'),
                    brand_data.get('description'),
                    brand_data.get('website'),
                    brand_data.get('contact_email'),
                    brand_data.get('instagram_handle'),
                    brand_data.get('category'),
                    brand_data.get('followers_count'),
                    brand_data.get('company_size'),
                    brand_data.get('industry'),
                    brand_data.get('avg_product_value'),
                    brand_data.get('collaboration_type'),
                    brand_data.get('payment_offered'),
                    brand_data.get('email_verified'),
                    brand_data.get('data_source', 'scraper'),
                    'verified' if brand_data.get('email_verified') else 'pending',
                    json.dumps(brand_data.get('metadata', {}))
                ))
                brand_id = cursor.fetchone()[0]

            self.db_conn.commit()
            cursor.close()

            return brand_id

        except Exception as e:
            print(f"Error saving brand to DB: {str(e)}")
            self.db_conn.rollback()
            return None

    def scrape_full_brand(self, instagram_handle: str = None, website: str = None) -> Optional[int]:
        """
        Complete brand scraping workflow:
        1. Scrape Instagram data
        2. Find email via Hunter.io
        3. Verify email via ZeroBounce
        4. Enrich with Clearbit
        5. Save to database
        """
        print(f"\n=== Scraping brand: {instagram_handle or website} ===")

        brand_data = {}

        # Step 1: Instagram scraping
        if instagram_handle:
            print("1. Scraping Instagram...")
            ig_data = self.scrape_instagram_brand(instagram_handle)
            if ig_data:
                brand_data.update(ig_data)
                website = brand_data.get('website', website)
                print(f"   ✓ Found: {brand_data.get('brand_name')}")

        if not website:
            print("   ✗ No website found, skipping email discovery")
            return self.save_brand_to_db(brand_data) if brand_data else None

        # Step 2: Email finding
        print("2. Finding contact email...")
        email_data = self.find_brand_email(website, brand_data.get('brand_name', ''))
        if email_data:
            brand_data['contact_email'] = email_data['email']
            brand_data['contact_name'] = f"{email_data.get('first_name', '')} {email_data.get('last_name', '')}".strip()
            brand_data['contact_position'] = email_data.get('position', '')
            print(f"   ✓ Found: {email_data['email']}")

            # Step 3: Email verification
            print("3. Verifying email...")
            verification = self.verify_email(email_data['email'])
            brand_data['email_verified'] = verification.get('is_valid', False)
            brand_data['metadata'] = {'email_verification': verification}

            if verification.get('is_valid'):
                print(f"   ✓ Email verified")
            else:
                print(f"   ✗ Email invalid: {verification.get('reason', 'Unknown')}")
        else:
            print("   ✗ No email found")

        # Step 4: Clearbit enrichment
        print("4. Enriching with company data...")
        clearbit_data = self.enrich_with_clearbit(website)
        if clearbit_data:
            brand_data.update(clearbit_data)
            print(f"   ✓ Enriched: {clearbit_data.get('industry', 'N/A')}")

        # Step 5: Save to database
        print("5. Saving to database...")
        brand_id = self.save_brand_to_db(brand_data)
        if brand_id:
            print(f"   ✓ Saved as Brand ID: {brand_id}")
        else:
            print(f"   ✗ Failed to save")

        # Rate limiting
        time.sleep(2)

        return brand_id


def main():
    """Example usage"""
    scraper = BrandScraper()

    # Example: Scrape a list of beauty/fashion brands from Instagram
    beauty_brands = [
        'glossier',
        'fentybeauty',
        'kyliecosmetics',
        'anastasiabeverlyhills',
        'rarebeauty',
        'milkmakeup',
        'tatcha',
        'drunkelephant'
    ]

    for brand in beauty_brands:
        try:
            scraper.scrape_full_brand(instagram_handle=brand)
        except Exception as e:
            print(f"Error scraping {brand}: {str(e)}")
            continue


if __name__ == "__main__":
    main()
