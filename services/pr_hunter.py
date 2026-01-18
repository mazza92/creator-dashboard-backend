"""
PR Hunter Automation Engine
Automates discovery and enrichment of brand PR contacts with waterfall verification.

Architecture:
1. Discovery: Find brands via Google Custom Search API
2. Form Scout: Find PR application forms (Typeform, Google Forms, etc.)
3. Enrichment: Find PR contacts via LinkedIn + Email APIs
4. Verification: Validate emails via SMTP handshake
5. Staging: Save to brand_candidates for manual review
"""

import os
import re
import requests
import time
from urllib.parse import urlparse
from typing import Dict, List, Optional, Tuple
from datetime import datetime


class PRHunterService:
    """Main service for automated brand discovery and enrichment"""

    def __init__(self):
        # API Keys (from environment variables)
        self.serpapi_key = os.getenv('SERPAPI_API_KEY')
        self.hunter_api_key = os.getenv('HUNTER_API_KEY')
        self.neverbounce_api_key = os.getenv('NEVERBOUNCE_API_KEY')

        # Rate limiting
        self.request_delay = 1.0  # Seconds between API calls

        # Form Scout: Priority platforms for PR application forms
        self.form_platforms = [
            'typeform.com/to/',
            'docs.google.com/forms',
            'app.grin.co',
            'dovetale.com/community/apply',
            'collabs.shopify.com',
            '/pages/ambassador',
            '/pages/influencer-program',
            '/pages/pr-application',
            'tally.so',
            'airtable.com',
            'jotform.com'
        ]

    # ============================================================================
    # MODULE A: DISCOVERY (Finding the Brands)
    # ============================================================================

    def search_google_for_brands(self, keyword: str, max_results: int = 50) -> List[Dict]:
        """
        Discover brands using Google Custom Search API via SerpApi

        IMPROVED: Focus on finding actual brand websites with PR/influencer programs
        instead of article pages.

        Args:
            keyword: Search keyword (e.g., "Clean Beauty", "K-Beauty")
            max_results: Maximum number of brands to find

        Returns:
            List of discovered brands with basic info
        """
        discovered_brands = []
        results_per_strategy = max(5, max_results // 5)

        # Exclude common article/listicle sites
        exclusions = '-site:amazon.com -site:pinterest.com -site:buzzfeed.com -site:allure.com -site:byrdie.com -site:cosmopolitan.com -site:elle.com -site:vogue.com -site:harpersbazaar.com -site:glamour.com -site:refinery29.com -site:popsugar.com -site:insider.com -site:businessinsider.com -site:forbes.com -site:youtube.com -site:reddit.com -site:quora.com'

        # Strategy 1: Direct brand ambassador/influencer program pages
        ambassador_query = f'{keyword} brand "ambassador program" OR "influencer program" OR "pr application" {exclusions}'
        ambassador_brands = self._execute_search(ambassador_query, max_results=results_per_strategy)
        discovered_brands.extend(ambassador_brands)

        # Strategy 2: Shopify Collabs / GRIN partner brands (indicates active PR)
        platform_query = f'{keyword} brand (site:collabs.shopify.com OR site:app.grin.co OR site:dovetale.com)'
        platform_brands = self._execute_search(platform_query, max_results=results_per_strategy)
        discovered_brands.extend(platform_brands)

        # Strategy 3: Brand websites with press/PR pages
        press_query = f'{keyword} brand site:*.com/pages/press OR site:*.com/pages/pr OR site:*.com/press {exclusions}'
        press_brands = self._execute_search(press_query, max_results=results_per_strategy)
        discovered_brands.extend(press_brands)

        # Strategy 4: Typeform/Google Forms for brand applications
        form_query = f'{keyword} brand (site:typeform.com OR site:docs.google.com/forms) "ambassador" OR "influencer" OR "pr"'
        form_brands = self._execute_search(form_query, max_results=results_per_strategy)
        discovered_brands.extend(form_brands)

        # Strategy 5: Brand websites directly (company domains)
        brand_query = f'{keyword} brand official website "contact" OR "about us" {exclusions}'
        brand_results = self._execute_search(brand_query, max_results=results_per_strategy)
        discovered_brands.extend(brand_results)

        # Filter out article/listicle pages
        filtered_brands = []
        for brand in discovered_brands:
            if not self._is_article_page(brand):
                filtered_brands.append(brand)
            else:
                print(f"⚠️ Filtered out article page: {brand.get('brand_name', 'Unknown')[:50]}")

        # Deduplicate by domain
        unique_brands = {}
        for brand in filtered_brands:
            domain = brand.get('domain')
            if domain and domain not in unique_brands:
                unique_brands[domain] = brand

        return list(unique_brands.values())

    def _is_article_page(self, brand: Dict) -> bool:
        """
        Check if the result is an article/listicle page rather than a brand website

        Args:
            brand: Brand data from search result

        Returns:
            True if this looks like an article page
        """
        title = brand.get('brand_name', '').lower()
        url = brand.get('website_url', '').lower()
        source = brand.get('discovery_source', '').lower()

        # Article title patterns to filter out
        article_patterns = [
            r'\d+\s+best',           # "10 Best", "The 11 Best"
            r'top\s+\d+',            # "Top 10"
            r'best\s+.*\s+of\s+20',  # "Best X of 2025"
            r'ranking',              # "Ranking"
            r'review:',              # "Review:"
            r'vs\.?\s',              # "X vs Y"
            r'comparison',           # "Comparison"
            r'guide\s+to',           # "Guide to"
            r'how\s+to',             # "How to"
            r'what\s+is',            # "What is"
        ]

        for pattern in article_patterns:
            if re.search(pattern, title):
                return True

        # Media/news domain patterns
        media_domains = [
            'allure.com', 'byrdie.com', 'cosmopolitan.com', 'elle.com',
            'vogue.com', 'harpersbazaar.com', 'glamour.com', 'refinery29.com',
            'popsugar.com', 'insider.com', 'businessinsider.com', 'forbes.com',
            'buzzfeed.com', 'huffpost.com', 'nytimes.com', 'washingtonpost.com',
            'theguardian.com', 'bbc.com', 'cnn.com', 'yahoo.com', 'msn.com',
            'medium.com', 'substack.com', 'wordpress.com', 'blogspot.com',
            'tumblr.com', 'wix.com', 'weebly.com'
        ]

        domain = brand.get('domain', '').lower()
        for media in media_domains:
            if media in domain:
                return True

        return False

    def _execute_search(self, query: str, max_results: int = 25) -> List[Dict]:
        """
        Execute a Google search via SerpApi

        Args:
            query: Search query string
            max_results: Maximum results to return

        Returns:
            List of search results with extracted metadata
        """
        if not self.serpapi_key:
            raise ValueError("SERPAPI_API_KEY not configured")

        url = "https://serpapi.com/search"
        params = {
            'q': query,
            'api_key': self.serpapi_key,
            'num': max_results,
            'engine': 'google'
        }

        try:
            response = requests.get(url, params=params, timeout=30)
            response.raise_for_status()
            data = response.json()

            results = []
            for result in data.get('organic_results', [])[:max_results]:
                brand_data = self._extract_brand_from_search_result(result)
                if brand_data:
                    results.append(brand_data)

            time.sleep(self.request_delay)  # Rate limiting
            return results

        except Exception as e:
            print(f"SerpApi search error: {str(e)}")
            return []

    def _extract_brand_from_search_result(self, result: Dict) -> Optional[Dict]:
        """
        Extract brand information from search result

        Args:
            result: Search result from SerpApi

        Returns:
            Extracted brand data or None
        """
        link = result.get('link', '')
        domain = self._clean_domain(link)

        if not domain:
            return None

        # Extract and clean brand name
        raw_title = result.get('title', '')
        brand_name = self._clean_brand_name(raw_title, domain)

        return {
            'brand_name': brand_name[:100],  # Truncate to fit varchar(100) if needed
            'website_url': link[:255],  # Truncate URL
            'domain': domain[:255],
            'discovery_source': f"Google Search: {result.get('snippet', '')[:100]}",
            'tiktok_handle': self._extract_tiktok_handle(link) if 'tiktok.com' in link else None,
            'instagram_handle': self._extract_instagram_handle(link) if 'instagram.com' in link else None
        }

    def _clean_brand_name(self, raw_title: str, domain: str) -> str:
        """
        Clean and extract actual brand name from search result title

        Args:
            raw_title: Raw title from search result
            domain: Domain to use as fallback

        Returns:
            Cleaned brand name
        """
        # Remove common suffixes
        title = raw_title.split('|')[0].strip()
        title = title.split(' - ')[0].strip()
        title = title.split(' – ')[0].strip()
        title = title.split(':')[0].strip()

        # Remove common prefixes
        prefixes_to_remove = [
            'Welcome to ', 'Shop ', 'Buy ', 'Official ', 'The ',
            'About ', 'Home ', 'Contact '
        ]
        for prefix in prefixes_to_remove:
            if title.lower().startswith(prefix.lower()):
                title = title[len(prefix):].strip()

        # Remove common suffixes
        suffixes_to_remove = [
            ' Official Site', ' Official Website', ' Home', ' Shop',
            ' Store', ' Beauty', ' Skincare', ' Cosmetics', '.com',
            ' Ambassador Program', ' Influencer Program', ' PR Application'
        ]
        for suffix in suffixes_to_remove:
            if title.lower().endswith(suffix.lower()):
                title = title[:-len(suffix)].strip()

        # If title is too long or looks like an article, use domain as brand name
        if len(title) > 50 or not title:
            # Extract brand name from domain (e.g., "glowrecipe.com" -> "Glow Recipe")
            brand_from_domain = domain.split('.')[0]
            # Add spaces before capital letters for camelCase domains
            brand_from_domain = re.sub(r'([a-z])([A-Z])', r'\1 \2', brand_from_domain)
            title = brand_from_domain.replace('-', ' ').replace('_', ' ').title()

        return title.strip() or domain.split('.')[0].title()

    def _clean_domain(self, url: str) -> Optional[str]:
        """
        Extract and clean domain from URL

        Args:
            url: Full URL

        Returns:
            Cleaned domain (e.g., 'glowrecipe.com') or None
        """
        try:
            parsed = urlparse(url)
            domain = parsed.netloc.lower()
            # Remove www., mobile., etc.
            domain = re.sub(r'^(www\.|m\.|mobile\.)', '', domain)
            return domain if domain else None
        except:
            return None

    def _extract_tiktok_handle(self, url: str) -> Optional[str]:
        """Extract TikTok handle from URL"""
        match = re.search(r'tiktok\.com/@([a-zA-Z0-9_.]+)', url)
        return match.group(1) if match else None

    def _extract_instagram_handle(self, url: str) -> Optional[str]:
        """Extract Instagram handle from URL"""
        match = re.search(r'instagram\.com/([a-zA-Z0-9_.]+)', url)
        return match.group(1) if match else None

    # ============================================================================
    # MODULE A.5: FORM SCOUT (Finding PR Application Forms)
    # ============================================================================

    def find_pr_application_form(self, brand_name: str, domain: str = None) -> Optional[Dict]:
        """
        Search for PR/Ambassador application forms using smart queries

        Priority: Forms are easier to find than emails and provide immediate value
        to free tier users.

        Args:
            brand_name: Name of the brand
            domain: Optional domain to help narrow search

        Returns:
            Dict with application_url and application_method or None
        """
        if not self.serpapi_key:
            return None

        # Build smart search queries
        queries = [
            f'{brand_name} "ambassador application" typeform',
            f'{brand_name} "pr list" google form',
            f'{brand_name} "influencer application"',
            f'site:typeform.com "{brand_name}" ambassador',
            f'site:docs.google.com/forms "{brand_name}" influencer',
            f'site:app.grin.co "{brand_name}"',
            f'site:dovetale.com "{brand_name}"',
            f'{brand_name} ambassador program apply',
        ]

        # If domain provided, add domain-specific searches
        if domain:
            queries.insert(0, f'site:{domain} /pages/ambassador')
            queries.insert(1, f'site:{domain} /pages/influencer')
            queries.insert(2, f'site:{domain} pr application')

        # Try each query until we find a valid form
        for query in queries:
            try:
                url = "https://serpapi.com/search"
                params = {
                    'q': query,
                    'api_key': self.serpapi_key,
                    'num': 3,
                    'engine': 'google'
                }

                response = requests.get(url, params=params, timeout=30)
                response.raise_for_status()
                data = response.json()

                # Check organic results for form URLs
                for result in data.get('organic_results', [])[:3]:
                    result_url = result.get('link', '')

                    # Check if URL matches known form patterns
                    for platform in self.form_platforms:
                        if platform in result_url.lower():
                            # Validate it's actually an application form
                            title = result.get('title', '').lower()
                            snippet = result.get('snippet', '').lower()

                            application_keywords = [
                                'ambassador', 'influencer', 'pr', 'creator',
                                'application', 'apply', 'join', 'program'
                            ]

                            if any(keyword in title or keyword in snippet for keyword in application_keywords):
                                time.sleep(self.request_delay)
                                return {
                                    'application_url': result_url,
                                    'application_method': self._detect_application_method(result_url),
                                    'form_platform': self._detect_form_platform(result_url),
                                    'found_via': f"Form Scout: {query[:50]}..."
                                }

                time.sleep(self.request_delay)

            except Exception as e:
                print(f"Form Scout search error for '{query}': {str(e)}")
                continue

        return None

    def _detect_application_method(self, url: str) -> str:
        """
        Detect application method from URL

        Returns:
            'DIRECT_FORM', 'EMAIL_FORM', or 'PLATFORM'
        """
        url_lower = url.lower()

        if 'typeform.com' in url_lower or 'tally.so' in url_lower or 'jotform.com' in url_lower:
            return 'DIRECT_FORM'
        elif 'docs.google.com/forms' in url_lower or 'airtable.com' in url_lower:
            return 'DIRECT_FORM'
        elif 'grin.co' in url_lower or 'dovetale.com' in url_lower or 'collabs.shopify.com' in url_lower:
            return 'PLATFORM'
        else:
            return 'DIRECT_FORM'

    def _detect_form_platform(self, url: str) -> str:
        """Detect which platform hosts the form"""
        url_lower = url.lower()

        platforms = {
            'typeform.com': 'Typeform',
            'docs.google.com/forms': 'Google Forms',
            'app.grin.co': 'GRIN',
            'dovetale.com': 'Dovetale',
            'collabs.shopify.com': 'Shopify Collabs',
            'tally.so': 'Tally',
            'airtable.com': 'Airtable',
            'jotform.com': 'JotForm'
        }

        for pattern, platform_name in platforms.items():
            if pattern in url_lower:
                return platform_name

        return 'Direct Website'

    # ============================================================================
    # MODULE B: WATERFALL ENRICHMENT (Finding the Email)
    # ============================================================================

    def enrich_brand_data(self, brand: Dict) -> Dict:
        """
        Enrich brand with PR contact information using waterfall approach

        Waterfall Steps:
        0. Find PR Application Form (Form Scout) - Easy, high success rate
        1. Find PR Manager via LinkedIn - Medium difficulty
        2. Find Email via Hunter.io - Requires name + domain
        3. Verify Email via NeverBounce - Final validation

        Args:
            brand: Basic brand info from discovery

        Returns:
            Enriched brand data with PR contact
        """
        domain = brand.get('domain')
        brand_name = brand.get('brand_name')

        if not domain or not brand_name:
            return brand

        # Step 0: Find PR Application Form (PRIORITY - easiest to find)
        form_data = self.find_pr_application_form(brand_name, domain)
        if form_data:
            brand['application_url'] = form_data['application_url']
            brand['application_method'] = form_data['application_method']
            brand['form_platform'] = form_data.get('form_platform', 'Unknown')
            print(f"✅ Found application form: {form_data['form_platform']}")
        else:
            brand['application_method'] = 'EMAIL_ONLY'
            print(f"⚠️ No application form found for {brand_name}")

        # Step 1: Find PR Manager via LinkedIn
        pr_contact = self._find_pr_manager_linkedin(brand_name)

        if pr_contact:
            brand['pr_manager_name'] = pr_contact['name']
            brand['pr_manager_linkedin'] = pr_contact['linkedin_url']
            brand['pr_manager_title'] = pr_contact['title']

            # Step 2: Find Email via Hunter.io
            email_data = self._find_email_hunter(
                first_name=pr_contact['first_name'],
                last_name=pr_contact['last_name'],
                domain=domain
            )

            if email_data:
                brand['contact_email'] = email_data['email']
                brand['email_source'] = email_data['source']
                brand['verification_score'] = email_data['confidence']

                # Step 3: Verify Email via NeverBounce
                verification = self._verify_email_smtp(email_data['email'])
                brand['verification_status'] = verification['status']
                brand['is_catch_all'] = verification['is_catch_all']
                brand['verification_score'] = verification['score']

        # Fetch logo via logo.dev
        brand['logo_url'] = self._fetch_logo(domain)

        return brand

    def _find_pr_manager_linkedin(self, brand_name: str) -> Optional[Dict]:
        """
        Find PR Manager via LinkedIn search

        Args:
            brand_name: Name of the brand

        Returns:
            PR contact info or None
        """
        if not self.serpapi_key:
            return None

        # Search for PR-related roles
        query = f'site:linkedin.com/in {brand_name} ("PR Manager" OR "Influencer Marketing" OR "Partnership" OR "Founder")'

        url = "https://serpapi.com/search"
        params = {
            'q': query,
            'api_key': self.serpapi_key,
            'num': 3,
            'engine': 'google'
        }

        try:
            response = requests.get(url, params=params, timeout=30)
            response.raise_for_status()
            data = response.json()

            for result in data.get('organic_results', []):
                title = result.get('title', '')
                linkedin_url = result.get('link', '')

                # Filter out low-priority titles
                if any(word in title.lower() for word in ['intern', 'assistant', 'coordinator']):
                    continue

                # Extract name and title
                name_match = re.match(r'([^-|]+)', title)
                if name_match:
                    full_name = name_match.group(1).strip()
                    name_parts = full_name.split()

                    return {
                        'name': full_name,
                        'first_name': name_parts[0] if name_parts else '',
                        'last_name': name_parts[-1] if len(name_parts) > 1 else '',
                        'title': title,
                        'linkedin_url': linkedin_url
                    }

            time.sleep(self.request_delay)
            return None

        except Exception as e:
            print(f"LinkedIn search error: {str(e)}")
            return None

    def _find_email_hunter(self, first_name: str, last_name: str, domain: str) -> Optional[Dict]:
        """
        Find email using Hunter.io API

        Args:
            first_name: Contact's first name
            last_name: Contact's last name
            domain: Company domain

        Returns:
            Email data or None
        """
        if not self.hunter_api_key:
            return None

        url = "https://api.hunter.io/v2/email-finder"
        params = {
            'domain': domain,
            'first_name': first_name,
            'last_name': last_name,
            'api_key': self.hunter_api_key
        }

        try:
            response = requests.get(url, params=params, timeout=30)
            response.raise_for_status()
            data = response.json()

            email_data = data.get('data', {})
            if email_data and email_data.get('email'):
                time.sleep(self.request_delay)
                return {
                    'email': email_data['email'],
                    'source': 'Hunter',
                    'confidence': email_data.get('confidence', 0)
                }

            time.sleep(self.request_delay)
            return None

        except Exception as e:
            print(f"Hunter.io error: {str(e)}")
            return None

    def _verify_email_smtp(self, email: str) -> Dict:
        """
        Verify email deliverability via NeverBounce API

        Args:
            email: Email address to verify

        Returns:
            Verification result with status and score
        """
        if not self.neverbounce_api_key:
            return {
                'status': 'unknown',
                'is_catch_all': False,
                'score': 0
            }

        url = "https://api.neverbounce.com/v4/single/check"
        params = {
            'key': self.neverbounce_api_key,
            'email': email
        }

        try:
            response = requests.get(url, params=params, timeout=30)
            response.raise_for_status()
            data = response.json()

            result = data.get('result', 'unknown')
            flags = data.get('flags', [])

            # Map NeverBounce results to our status
            status_map = {
                'valid': 'valid',
                'invalid': 'invalid',
                'disposable': 'invalid',
                'catchall': 'catch-all',
                'unknown': 'unknown'
            }

            status = status_map.get(result, 'unknown')
            is_catch_all = 'has_dns_mx' in flags and 'free_email_host' not in flags and result == 'catchall'

            # Assign score based on status
            score_map = {
                'valid': 100,
                'catch-all': 50,
                'unknown': 25,
                'invalid': 0
            }

            time.sleep(self.request_delay)

            return {
                'status': status,
                'is_catch_all': is_catch_all,
                'score': score_map.get(status, 0)
            }

        except Exception as e:
            print(f"NeverBounce verification error: {str(e)}")
            return {
                'status': 'unknown',
                'is_catch_all': False,
                'score': 0
            }

    def _fetch_logo(self, domain: str) -> Optional[str]:
        """
        Fetch brand logo via logo.dev API

        Args:
            domain: Company domain

        Returns:
            Logo URL or None
        """
        # logo.dev is free and has good coverage
        return f"https://img.logo.dev/{domain}?token=pk_X-1ZO13CRYuNw\\_F4mZEQ"

    # ============================================================================
    # QUALITY GATE (100% Quality Filter)
    # ============================================================================

    def quality_gate(self, candidate: Dict) -> Tuple[bool, Optional[str]]:
        """
        Strict filtering to ensure only high-quality candidates are shown

        IMPORTANT: With Form Scout, brands with application forms are valuable
        even without emails (users can apply directly via form)

        Args:
            candidate: Brand candidate data

        Returns:
            (passes_quality_check, rejection_reason)
        """
        # NEW: If brand has an application form, it's automatically valuable!
        has_application_form = bool(candidate.get('application_url'))

        if has_application_form:
            # Brands with forms pass quality gate (forms are easier for creators)
            return True, None

        # For EMAIL_ONLY brands, apply strict quality checks:

        # 1. Must have a PR manager name (no generic contacts)
        if not candidate.get('pr_manager_name'):
            return False, "No PR manager identified"

        # 2. Must have an email
        if not candidate.get('contact_email'):
            return False, "No email found"

        # 3. Must not be a generic email
        email = candidate.get('contact_email', '')
        generic_prefixes = ['info', 'contact', 'support', 'help', 'hello', 'admin', 'sales']
        if any(email.lower().startswith(prefix) for prefix in generic_prefixes):
            return False, f"Generic email prefix: {email.split('@')[0]}"

        # 4. Must be DELIVERABLE (validation score check)
        verification_status = candidate.get('verification_status', 'unknown')
        if verification_status == 'invalid':
            return False, "Email marked as invalid"

        # 5. Warn about catch-all (but don't auto-reject - let admin decide)
        if verification_status == 'catch-all':
            # Still pass, but flag it
            return True, None

        # 6. Prefer high verification scores
        score = candidate.get('verification_score', 0)
        if score < 50:
            return False, f"Low verification score: {score}"

        return True, None


# ============================================================================
# UTILITY FUNCTIONS
# ============================================================================

def chunk_list(items: List, chunk_size: int) -> List[List]:
    """Split list into chunks for batch processing"""
    return [items[i:i + chunk_size] for i in range(0, len(items), chunk_size)]
