"""
PR CRM Routes for Creator Dashboard
Mobile-first API endpoints for brand discovery and pipeline management
"""

from flask import Blueprint, request, jsonify, session
from flask_jwt_extended import jwt_required, get_jwt_identity
import psycopg2
from psycopg2.extras import RealDictCursor
import os
import json
import re
import requests
from datetime import datetime
from urllib.parse import urlparse, urljoin

# Try to import BeautifulSoup for Tier 2 web scraping
try:
    from bs4 import BeautifulSoup
    HAS_BS4 = True
except ImportError:
    HAS_BS4 = False
    print("⚠️ BeautifulSoup not installed - Tier 2 web scraping will be limited")

pr_crm = Blueprint('pr_crm', __name__, url_prefix='/api/pr-crm')


def get_min_follower_cap(creator_followers):
    """
    Get max brand min_followers requirement to show based on creator size.
    Prevents showing brands with high requirements to micro-creators.
    Uses min_followers (brand's creator requirement) as proxy for brand accessibility.
    """
    if not creator_followers or creator_followers < 5000:
        return 10000  # Show brands that accept creators under 10K
    elif creator_followers < 20000:
        return 50000  # Show brands that accept creators under 50K
    elif creator_followers < 50000:
        return 100000  # Show brands that accept creators under 100K
    else:
        return None  # No cap for 50K+ creators


def normalize_niche(niche_str):
    """
    Normalize compound niches like "tech & gadgets" into individual components.
    Returns a list of individual niche terms.
    Examples:
        "tech & gadgets" -> ["tech", "gadgets", "tech & gadgets"]
        "food & beverage" -> ["food", "beverage", "food & beverage"]
        "fitness" -> ["fitness"]
    """
    if not niche_str:
        return []

    niche_lower = niche_str.lower().strip()
    result = [niche_lower]  # Always include the original

    # Split by common separators
    for sep in [' & ', ' and ', '/', ', ']:
        if sep in niche_lower:
            parts = [p.strip() for p in niche_lower.split(sep) if p.strip()]
            result.extend(parts)
            break

    return list(set(result))  # Dedupe


def generate_kit_token(creator_id, brand_id):
    """
    Generate a deterministic, unique kit tracking token for a creator/brand pair.
    Used to track which brand viewed which creator's kit after receiving a pitch.
    """
    import hashlib
    secret = os.getenv('SECRET_KEY', 'fallback-secret-key-change-me')
    raw = f"{creator_id}-{brand_id}-{secret}"
    return hashlib.sha256(raw.encode()).hexdigest()[:12]


def get_db_connection():
    """Get database connection"""
    return psycopg2.connect(
        host=os.getenv('DB_HOST'),
        port=os.getenv('DB_PORT', 5432),
        database=os.getenv('DB_NAME'),
        user=os.getenv('DB_USER'),
        password=os.getenv('DB_PASSWORD')
    )

def get_creator_id_from_session():
    """Get creator ID from session or JWT"""
    # Try session first
    creator_id = session.get('creator_id')
    if creator_id:
        return creator_id

    # Try JWT if session doesn't have it
    try:
        user_id = get_jwt_identity()
        if user_id:
            # Fetch creator_id from user_id
            conn = get_db_connection()
            cursor = conn.cursor(cursor_factory=RealDictCursor)
            cursor.execute('SELECT id FROM creators WHERE user_id = %s', (user_id,))
            creator = cursor.fetchone()
            cursor.close()
            conn.close()
            if creator:
                return creator['id']
    except:
        pass

    return None

# ============================================
# BRAND DISCOVERY ENDPOINTS
# ============================================

@pr_crm.route('/brands', methods=['GET'])
def get_brands():
    """
    Get paginated brand list with filtering
    Query params:
    - page (int): Page number (default 1)
    - limit (int): Items per page (default 20)
    - category (str): Filter by category
    - niche (str): Filter by niche (comma-separated)
    - region (str): Filter by region
    - min_followers (int): Minimum follower requirement
    - max_followers (int): Maximum follower requirement
    - has_form (bool): Only brands with application forms
    - search (str): Search by brand name
    - sort (str): Sort by (newest, response_rate, name)
    """
    try:
        # Get query parameters
        page = int(request.args.get('page', 1))
        limit = int(request.args.get('limit', 20))
        category = request.args.get('category')
        niches = request.args.get('niche')  # comma-separated
        region = request.args.get('region')
        min_followers = request.args.get('min_followers')
        max_followers = request.args.get('max_followers')
        has_form = request.args.get('has_form')
        search = request.args.get('search')
        sort_by = request.args.get('sort', 'newest')
        exclude_ids = request.args.get('exclude_ids')  # comma-separated brand IDs to exclude

        # Check if user is premium (for gating premium brands)
        creator_id = get_creator_id_from_session()
        is_premium = False

        if creator_id:
            temp_conn = get_db_connection()
            temp_cursor = temp_conn.cursor(cursor_factory=RealDictCursor)
            temp_cursor.execute('''
                SELECT subscription_tier
                FROM creators
                WHERE id = %s
            ''', (creator_id,))
            creator = temp_cursor.fetchone()
            temp_cursor.close()
            temp_conn.close()
            if creator and creator['subscription_tier'] in ['pro', 'elite']:
                is_premium = True

        # Build query
        conn = get_db_connection()
        cursor = conn.cursor(cursor_factory=RealDictCursor)

        # Base WHERE clause
        where_clauses = []
        params = []

        # Filter by category (canonical slug)
        if category:
            from brand_categories import normalize_category
            canon_category = normalize_category(category)
            if canon_category:
                where_clauses.append('category = %s')
                params.append(canon_category)

        # Filter by niche
        if niches:
            niche_list = [n.strip() for n in niches.split(',')]
            where_clauses.append('niches ?| %s')  # PostgreSQL JSONB operator
            params.append(niche_list)

        # Filter by region
        if region:
            where_clauses.append('regions @> %s')
            params.append(json.dumps([region]))

        # Filter by follower requirements
        if min_followers:
            where_clauses.append('(min_followers <= %s OR min_followers IS NULL)')
            params.append(int(min_followers))

        if max_followers:
            where_clauses.append('(max_followers >= %s OR max_followers IS NULL)')
            params.append(int(max_followers))

        # Filter by has application form
        if has_form and has_form.lower() == 'true':
            where_clauses.append('has_application_form = true')

        # Search by brand name
        if search:
            where_clauses.append('brand_name ILIKE %s')
            params.append(f'%{search}%')

        # Exclude already seen brands (for discovery feed)
        if exclude_ids:
            try:
                excluded_list = [int(id.strip()) for id in exclude_ids.split(',') if id.strip()]
                if excluded_list:
                    where_clauses.append('id NOT IN %s')
                    params.append(tuple(excluded_list))
            except ValueError:
                pass  # Ignore invalid exclude_ids

        # Gate premium brands for free users
        if not is_premium:
            where_clauses.append('is_premium = false')

        where_sql = ' AND '.join(where_clauses) if where_clauses else '1=1'

        # Sort
        sort_sql = {
            'newest': 'created_at DESC',
            'response_rate': 'response_rate DESC NULLS LAST',
            'name': 'brand_name ASC',
            'followers': 'min_followers ASC'
        }.get(sort_by, 'created_at DESC')

        # Get total count
        cursor.execute(f'SELECT COUNT(*) as total FROM pr_brands WHERE {where_sql}', params)
        total = cursor.fetchone()['total']

        # Get paginated results
        offset = (page - 1) * limit
        query = f'''
            SELECT
                id, brand_name, website, logo_url, cover_image_url, category, niches,
                product_types, regions, platforms, min_followers, max_followers,
                contact_email, instagram_handle, tiktok_handle, youtube_handle,
                has_application_form, application_form_url,
                response_rate, avg_response_time_days, is_premium, notes,
                avg_product_value, collaboration_type, payment_offered
            FROM pr_brands
            WHERE {where_sql}
            ORDER BY {sort_sql}
            LIMIT %s OFFSET %s
        '''

        cursor.execute(query, params + [limit, offset])
        brands = cursor.fetchall()

        from brand_stats_synthesis import resolve_brand_stats
        from public_routes import _estimate_package_value

        for b in brands:
            rate, days = resolve_brand_stats(
                b.get('slug') or str(b['id']),
                b.get('category'),
                b.get('response_rate'),
                b.get('avg_response_time_days'),
            )
            b['response_rate'] = rate
            b['avg_response_time_days'] = days
            # Add estimated package value for quick wins UI
            b['estimated_value'] = _estimate_package_value(b.get('category'), b.get('brand_name'))

        cursor.close()
        conn.close()

        return jsonify({
            'success': True,
            'brands': brands,
            'pagination': {
                'page': page,
                'limit': limit,
                'total': total,
                'pages': (total + limit - 1) // limit
            },
            'is_premium': is_premium
        })

    except Exception as e:
        import traceback
        print(f"❌ Error in get_brands: {str(e)}")
        print(traceback.format_exc())
        return jsonify({'success': False, 'error': str(e), 'details': traceback.format_exc()}), 500


@pr_crm.route('/brands/<int:brand_id>', methods=['GET'])
def get_brand_details(brand_id):
    """Get full details for a specific brand"""
    try:
        conn = get_db_connection()
        cursor = conn.cursor(cursor_factory=RealDictCursor)

        cursor.execute('SELECT * FROM pr_brands WHERE id = %s', (brand_id,))
        brand = cursor.fetchone()

        if not brand:
            cursor.close()
            conn.close()
            return jsonify({'success': False, 'error': 'Brand not found'}), 404

        # Check if user has saved this brand
        creator_id = get_creator_id_from_session()
        is_saved = False
        pipeline_stage = None

        if creator_id:
            cursor.execute('''
                SELECT stage FROM creator_pipeline
                WHERE creator_id = %s AND brand_id = %s
            ''', (creator_id, brand_id))
            pipeline = cursor.fetchone()
            if pipeline:
                is_saved = True
                pipeline_stage = pipeline['stage']

        from brand_stats_synthesis import resolve_brand_stats

        rate, days = resolve_brand_stats(
            brand.get('slug') or str(brand['id']),
            brand.get('category'),
            brand.get('response_rate'),
            brand.get('avg_response_time_days'),
        )
        brand['response_rate'] = rate
        brand['avg_response_time_days'] = days

        cursor.close()
        conn.close()

        return jsonify({
            'success': True,
            'brand': brand,
            'is_saved': is_saved,
            'pipeline_stage': pipeline_stage
        })

    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@pr_crm.route('/brands/categories', methods=['GET'])
def get_categories():
    """Get all available categories with counts"""
    try:
        conn = get_db_connection()
        cursor = conn.cursor(cursor_factory=RealDictCursor)

        from brand_categories import aggregate_category_counts

        cursor.execute('''
            SELECT category, COUNT(*) as count
            FROM pr_brands
            WHERE category IS NOT NULL AND TRIM(category) != ''
            GROUP BY category
        ''')
        rows = cursor.fetchall()

        cursor.close()
        conn.close()

        return jsonify({
            'success': True,
            'categories': aggregate_category_counts([
                {'category': r['category'], 'brand_count': r['count']} for r in rows
            ])
        })

    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


# ============================================
# UNIVERSAL BRAND DISCOVERY (Phase 1)
# ============================================

def normalize_brand_name(name):
    """Normalize brand name for matching: lowercase, no spaces, no special chars"""
    import re
    if not name:
        return ''
    return re.sub(r'[^a-z0-9]', '', name.lower().strip())


def check_discovery_rate_limit(creator_id, conn, cursor):
    """
    Check if creator has hit their daily discovery limit (5 attempts/day).
    Returns (can_discover: bool, attempts_today: int, limit: int)
    """
    from datetime import date
    today = date.today()

    cursor.execute('''
        SELECT attempts FROM creator_discovery_limits
        WHERE creator_id = %s AND date = %s
    ''', (creator_id, today))
    row = cursor.fetchone()

    attempts_today = row['attempts'] if row else 0
    daily_limit = 5

    return attempts_today < daily_limit, attempts_today, daily_limit


def increment_discovery_attempt(creator_id, conn, cursor):
    """Increment the daily discovery attempt counter"""
    from datetime import date
    today = date.today()

    cursor.execute('''
        INSERT INTO creator_discovery_limits (creator_id, date, attempts)
        VALUES (%s, %s, 1)
        ON CONFLICT (creator_id, date)
        DO UPDATE SET attempts = creator_discovery_limits.attempts + 1, updated_at = NOW()
    ''', (creator_id, today))
    conn.commit()


def log_discovery_attempt(creator_id, search_query, normalized_query, found_brand_id, result_tier, result_status, conn, cursor):
    """Log a discovery attempt for analytics"""
    from flask import request as flask_request
    ip_address = flask_request.headers.get('X-Forwarded-For', flask_request.remote_addr)
    user_agent = flask_request.headers.get('User-Agent', '')[:500]

    cursor.execute('''
        INSERT INTO brand_discovery_logs
        (creator_id, search_query, normalized_query, found_brand_id, result_tier, result_status, ip_address, user_agent)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
    ''', (creator_id, search_query, normalized_query, found_brand_id, result_tier, result_status, ip_address, user_agent))
    conn.commit()


# ============================================
# TIER 2: WEB SCRAPING FOR PR CONTACTS
# ============================================

# Common PR/press page paths to check
PR_PAGE_PATHS = [
    '/press', '/pr', '/contact', '/partnerships', '/influencers',
    '/affiliates', '/collaborate', '/press-room', '/media',
    '/about/press', '/about/contact', '/contact-us', '/work-with-us',
    '/creator-program', '/ambassador', '/brand-ambassadors',
    '/influencer-program', '/collab', '/partner'
]

# Email patterns that indicate PR/press contacts
PR_EMAIL_PREFIXES = ['pr', 'press', 'partnerships', 'marketing', 'collab',
                      'collaborate', 'influencer', 'creator', 'media', 'hello',
                      'contact', 'info', 'brand', 'ambassador', 'affiliate']


def guess_brand_domain(brand_name):
    """
    Guess a brand's domain from its name.
    Tries common patterns like brandname.com, brandname.co, etc.
    Returns list of possible domains to try.
    """
    normalized = normalize_brand_name(brand_name)

    # Common domain patterns
    domains = [
        f"{normalized}.com",
        f"{normalized}.co",
        f"www.{normalized}.com",
        f"shop{normalized}.com",
        f"{normalized}beauty.com",
        f"get{normalized}.com",
        f"{normalized}skin.com",
        f"the{normalized}.com",
        f"{normalized}official.com",
    ]

    # Handle common brand name patterns
    # e.g., "Sol de Janeiro" -> "soldejaneiro.com"
    words = brand_name.lower().split()
    if len(words) > 1:
        joined = ''.join(words)
        domains.insert(0, f"{joined}.com")

    return domains


def validate_domain(domain, timeout=5):
    """
    Check if a domain is valid by making a HEAD request.
    Returns the final URL if valid, None otherwise.
    """
    try:
        # Try HTTPS first
        url = f"https://{domain}" if not domain.startswith('http') else domain
        response = requests.head(url, timeout=timeout, allow_redirects=True)
        if response.status_code < 400:
            return response.url
    except:
        pass

    try:
        # Try HTTP as fallback
        url = f"http://{domain}" if not domain.startswith('http') else domain
        response = requests.head(url, timeout=timeout, allow_redirects=True)
        if response.status_code < 400:
            return response.url
    except:
        pass

    return None


def extract_emails_from_text(text):
    """
    Extract email addresses from text using regex.
    Returns list of unique emails found.
    """
    # Email regex pattern
    email_pattern = r'[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}'
    emails = re.findall(email_pattern, text.lower())

    # Filter out common false positives
    exclude_patterns = ['example.com', 'email.com', 'domain.com', 'yoursite.com',
                        'yourdomain.com', '.png', '.jpg', '.gif', 'wixpress.com',
                        'sentry.io', 'facebook.com', 'twitter.com', 'instagram.com']

    filtered = []
    for email in emails:
        if not any(excl in email for excl in exclude_patterns):
            filtered.append(email)

    return list(set(filtered))


def scrape_page_for_emails(url, timeout=10):
    """
    Scrape a webpage for email addresses.
    Returns list of emails found on the page.
    """
    if not HAS_BS4:
        # Fallback to basic regex if BeautifulSoup not available
        try:
            response = requests.get(url, timeout=timeout, headers={
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
            })
            if response.status_code == 200:
                return extract_emails_from_text(response.text)
        except:
            pass
        return []

    try:
        response = requests.get(url, timeout=timeout, headers={
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
        })
        if response.status_code != 200:
            return []

        soup = BeautifulSoup(response.text, 'html.parser')

        # Get all text content
        text = soup.get_text()
        emails = extract_emails_from_text(text)

        # Also check href attributes for mailto: links
        for a_tag in soup.find_all('a', href=True):
            href = a_tag['href']
            if href.startswith('mailto:'):
                email = href.replace('mailto:', '').split('?')[0].lower()
                if '@' in email and email not in emails:
                    emails.append(email)

        return emails
    except Exception as e:
        print(f"⚠️ Error scraping {url}: {e}")
        return []


def find_pr_email_from_list(emails, domain=None):
    """
    From a list of emails, find the most likely PR/press contact.
    Prioritizes known PR prefixes.
    """
    if not emails:
        return None

    # Score emails based on PR likelihood
    scored_emails = []
    for email in emails:
        prefix = email.split('@')[0].lower()
        email_domain = email.split('@')[1].lower() if '@' in email else ''

        score = 0

        # Boost if email matches brand domain
        if domain and domain.replace('www.', '') in email_domain:
            score += 10

        # Score based on prefix
        if prefix in ['pr', 'press']:
            score += 100
        elif prefix in ['partnerships', 'collab', 'collaborate']:
            score += 80
        elif prefix in ['influencer', 'influencers', 'creator', 'creators']:
            score += 70
        elif prefix in ['marketing', 'brand']:
            score += 60
        elif prefix in ['hello', 'hi', 'info', 'contact']:
            score += 40
        elif prefix in ['support', 'help', 'sales']:
            score += 10

        scored_emails.append((email, score))

    # Sort by score descending
    scored_emails.sort(key=lambda x: x[1], reverse=True)

    # Return highest scoring email if it has any relevance
    if scored_emails and scored_emails[0][1] > 0:
        return scored_emails[0][0]

    return None


def tier2_web_scrape(brand_name, known_domain=None):
    """
    Tier 2 Discovery: Scrape brand website for PR contacts.

    1. Determine brand domain (use known or guess)
    2. Check common PR page paths
    3. Extract emails from pages
    4. Return best PR email found

    Returns: {
        'found': bool,
        'email': str or None,
        'domain': str or None,
        'source_url': str or None,
        'all_emails': list
    }
    """
    result = {
        'found': False,
        'email': None,
        'domain': None,
        'source_url': None,
        'all_emails': []
    }

    # Determine domain to use
    domains_to_try = []
    if known_domain:
        domains_to_try.append(known_domain)
    domains_to_try.extend(guess_brand_domain(brand_name))

    # Find a valid domain
    valid_base_url = None
    valid_domain = None
    for domain in domains_to_try:
        url = validate_domain(domain)
        if url:
            valid_base_url = url
            parsed = urlparse(url)
            valid_domain = parsed.netloc.replace('www.', '')
            result['domain'] = valid_domain
            break

    if not valid_base_url:
        print(f"⚠️ Tier 2: Could not find valid domain for {brand_name}")
        return result

    print(f"✓ Tier 2: Found valid domain {valid_domain} for {brand_name}")

    # Scrape homepage first
    all_emails = scrape_page_for_emails(valid_base_url)

    # Try PR page paths
    for path in PR_PAGE_PATHS:
        try:
            page_url = urljoin(valid_base_url, path)
            emails = scrape_page_for_emails(page_url, timeout=5)
            if emails:
                all_emails.extend(emails)
                result['source_url'] = page_url
                print(f"✓ Tier 2: Found emails on {page_url}: {emails}")
        except:
            pass

    # Dedupe
    all_emails = list(set(all_emails))
    result['all_emails'] = all_emails

    # Find best PR email
    pr_email = find_pr_email_from_list(all_emails, valid_domain)
    if pr_email:
        result['found'] = True
        result['email'] = pr_email
        print(f"✓ Tier 2: Best PR email for {brand_name}: {pr_email}")

    return result


# ============================================
# TIER 3: PATTERN-BASED EMAIL INFERENCE
# ============================================

def tier3_pattern_inference(brand_name, domain=None):
    """
    Tier 3 Discovery: Generate likely email addresses based on common patterns.
    These are UNVERIFIED and should be labeled as such.

    Returns: {
        'found': bool,
        'email': str or None,  # Primary inferred email
        'alternatives': list,  # Other possible emails
        'domain': str or None,
        'verified': False  # Always false for Tier 3
    }
    """
    result = {
        'found': False,
        'email': None,
        'alternatives': [],
        'domain': None,
        'verified': False
    }

    # Determine domain
    if not domain:
        # Try to find a valid domain
        domains_to_try = guess_brand_domain(brand_name)
        for d in domains_to_try[:3]:  # Only try first 3
            url = validate_domain(d, timeout=3)
            if url:
                parsed = urlparse(url)
                domain = parsed.netloc.replace('www.', '')
                break

    if not domain:
        print(f"⚠️ Tier 3: Could not determine domain for {brand_name}")
        return result

    result['domain'] = domain

    # Generate inferred emails in order of likelihood
    inferred_emails = [
        f"pr@{domain}",
        f"press@{domain}",
        f"partnerships@{domain}",
        f"hello@{domain}",
        f"collab@{domain}",
        f"marketing@{domain}",
        f"influencer@{domain}",
        f"creators@{domain}",
        f"contact@{domain}",
        f"info@{domain}",
    ]

    result['found'] = True
    result['email'] = inferred_emails[0]  # pr@ is most common
    result['alternatives'] = inferred_emails[1:5]  # Next 4 alternatives

    print(f"✓ Tier 3: Generated inferred emails for {brand_name}: {inferred_emails[:3]}")

    return result


@pr_crm.route('/brands/discover', methods=['POST'])
def discover_brand():
    """
    Universal Brand Discovery endpoint.
    Searches for a brand by name, first in curated directory, then in known contacts (Tier 1).
    If found in known contacts but not in directory, creates a discovered brand entry.

    Request body:
    - query (str): Brand name to search for

    Returns:
    - found (bool): Whether a match was found
    - brand (obj): Brand data if found
    - source (str): 'curated', 'discovered', 'known_contact', 'not_found'
    - discovery_tier (int): 1, 2, 3 or null
    - can_discover (bool): If false, rate limit hit
    """
    creator_id = get_creator_id_from_session()
    if not creator_id:
        return jsonify({'success': False, 'error': 'Not authenticated'}), 401

    try:
        data = request.get_json() or {}
        search_query = (data.get('query') or '').strip()

        if not search_query or len(search_query) < 2:
            return jsonify({
                'success': False,
                'error': 'Search query must be at least 2 characters'
            }), 400

        normalized_query = normalize_brand_name(search_query)

        conn = get_db_connection()
        cursor = conn.cursor(cursor_factory=RealDictCursor)

        # ============================================
        # STEP 1: Check curated directory first
        # ============================================
        cursor.execute('''
            SELECT
                id, brand_name, website, logo_url, cover_image_url, category, niches,
                product_types, regions, platforms, min_followers, max_followers,
                contact_email, instagram_handle, tiktok_handle, youtube_handle,
                has_application_form, application_form_url,
                response_rate, avg_response_time_days, is_premium, notes,
                avg_product_value, collaboration_type, payment_offered,
                source, discovery_tier, verified_contact
            FROM pr_brands
            WHERE LOWER(REPLACE(brand_name, ' ', '')) ILIKE %s
               OR brand_name ILIKE %s
            ORDER BY
                CASE WHEN source = 'curated' THEN 0 ELSE 1 END,
                search_count DESC NULLS LAST
            LIMIT 1
        ''', (f'%{normalized_query}%', f'%{search_query}%'))

        existing_brand = cursor.fetchone()

        if existing_brand:
            # Found in directory - increment search count and return
            source_type = existing_brand.get('source') or 'curated'
            print(f"✓ Step 1: Found '{existing_brand['brand_name']}' in {source_type} directory (email: {existing_brand.get('contact_email')})")
            cursor.execute('''
                UPDATE pr_brands SET search_count = COALESCE(search_count, 0) + 1 WHERE id = %s
            ''', (existing_brand['id'],))
            conn.commit()

            log_discovery_attempt(
                creator_id, search_query, normalized_query,
                existing_brand['id'], existing_brand.get('discovery_tier'),
                f'found_{source_type}', conn, cursor
            )

            # Enrich with stats
            from brand_stats_synthesis import resolve_brand_stats
            from public_routes import _estimate_package_value

            rate, days = resolve_brand_stats(
                str(existing_brand['id']),
                existing_brand.get('category'),
                existing_brand.get('response_rate'),
                existing_brand.get('avg_response_time_days'),
            )
            existing_brand['response_rate'] = rate
            existing_brand['avg_response_time_days'] = days
            existing_brand['estimated_value'] = _estimate_package_value(
                existing_brand.get('category'), existing_brand.get('brand_name')
            )

            cursor.close()
            conn.close()

            return jsonify({
                'success': True,
                'found': True,
                'brand': existing_brand,
                'source': source_type,
                'discovery_tier': existing_brand.get('discovery_tier'),
                'verified_contact': existing_brand.get('verified_contact', True)
            })

        # ============================================
        # STEP 2: Check rate limit before discovery
        # ============================================
        print(f"🔍 Step 1: Not found in directory, checking rate limit...")
        can_discover, attempts_today, daily_limit = check_discovery_rate_limit(creator_id, conn, cursor)
        print(f"   Rate limit: {attempts_today}/{daily_limit} attempts today")

        if not can_discover:
            print(f"⚠️ Step 2: Rate limit reached for {search_query}")
            log_discovery_attempt(
                creator_id, search_query, normalized_query,
                None, None, 'rate_limited', conn, cursor
            )
            cursor.close()
            conn.close()

            return jsonify({
                'success': True,
                'found': False,
                'source': 'not_found',
                'can_discover': False,
                'rate_limit': {
                    'attempts_today': attempts_today,
                    'daily_limit': daily_limit,
                    'message': f'Discovery limit reached ({daily_limit}/day). Try again tomorrow.'
                }
            })

        # ============================================
        # STEP 3: Tier 1 - Check known_brand_contacts
        # ============================================
        print(f"🔍 Step 3 Tier 1: Checking known_brand_contacts for '{search_query}'...")
        cursor.execute('''
            SELECT * FROM known_brand_contacts
            WHERE normalized_name ILIKE %s
               OR brand_name ILIKE %s
            ORDER BY verified DESC, usage_count DESC
            LIMIT 1
        ''', (f'%{normalized_query}%', f'%{search_query}%'))

        known_contact = cursor.fetchone()

        if known_contact:
            # Found in known contacts! Create a discovered brand entry
            print(f"✓ Tier 1: Found known contact for '{known_contact['brand_name']}': {known_contact['contact_email']}")
            increment_discovery_attempt(creator_id, conn, cursor)

            # Infer category from brand name (basic heuristic for Phase 1)
            category = 'beauty'  # Default for most known contacts
            brand_name_lower = known_contact['brand_name'].lower()
            if any(w in brand_name_lower for w in ['gym', 'fit', 'yoga', 'active', 'sport']):
                category = 'fitness'
            elif any(w in brand_name_lower for w in ['fashion', 'style', 'wear', 'cloth']):
                category = 'fashion'

            # Insert into pr_brands as discovered (status=draft for moderation)
            cursor.execute('''
                INSERT INTO pr_brands (
                    brand_name, website, contact_email, category,
                    source, discovery_tier, discovered_at, verified_contact, search_count, status
                )
                VALUES (%s, %s, %s, %s, 'discovered', 1, NOW(), %s, 1, 'draft')
                ON CONFLICT DO NOTHING
                RETURNING id, brand_name, website, contact_email, category, source, discovery_tier, verified_contact
            ''', (
                known_contact['brand_name'],
                f"https://{known_contact['domain']}" if known_contact.get('domain') else None,
                known_contact['contact_email'],
                category,
                known_contact.get('verified', True)
            ))

            new_brand = cursor.fetchone()

            if new_brand:
                # Update known contact usage
                cursor.execute('''
                    UPDATE known_brand_contacts
                    SET usage_count = usage_count + 1, last_used_at = NOW()
                    WHERE id = %s
                ''', (known_contact['id'],))
                conn.commit()

                log_discovery_attempt(
                    creator_id, search_query, normalized_query,
                    new_brand['id'], 1, 'discovered_new', conn, cursor
                )

                # Enrich with estimated values
                from public_routes import _estimate_package_value
                new_brand['estimated_value'] = _estimate_package_value(category, new_brand['brand_name'])
                new_brand['response_rate'] = None
                new_brand['avg_response_time_days'] = None

                cursor.close()
                conn.close()

                return jsonify({
                    'success': True,
                    'found': True,
                    'brand': dict(new_brand),
                    'source': 'discovered',
                    'discovery_tier': 1,
                    'verified_contact': known_contact.get('verified', True),
                    'is_new_discovery': True
                })
            else:
                # Brand might already exist from another discovery
                cursor.execute('''
                    SELECT * FROM pr_brands WHERE LOWER(brand_name) = LOWER(%s) LIMIT 1
                ''', (known_contact['brand_name'],))
                existing = cursor.fetchone()

                if existing:
                    cursor.execute('''
                        UPDATE pr_brands SET search_count = COALESCE(search_count, 0) + 1 WHERE id = %s
                    ''', (existing['id'],))
                    conn.commit()

                    log_discovery_attempt(
                        creator_id, search_query, normalized_query,
                        existing['id'], existing.get('discovery_tier'), 'found_discovered', conn, cursor
                    )

                    cursor.close()
                    conn.close()

                    return jsonify({
                        'success': True,
                        'found': True,
                        'brand': dict(existing),
                        'source': existing.get('source', 'discovered'),
                        'discovery_tier': existing.get('discovery_tier'),
                        'verified_contact': existing.get('verified_contact', True)
                    })

        # ============================================
        # STEP 4: Tier 2 - Web Scrape for PR Contacts
        # ============================================
        print(f"🔍 Tier 2: Starting web scrape for {search_query}")
        tier2_result = tier2_web_scrape(search_query)

        if tier2_result['found'] and tier2_result['email']:
            increment_discovery_attempt(creator_id, conn, cursor)

            # Infer category from brand name
            category = 'beauty'  # Default
            brand_name_lower = search_query.lower()
            if any(w in brand_name_lower for w in ['gym', 'fit', 'yoga', 'active', 'sport']):
                category = 'fitness'
            elif any(w in brand_name_lower for w in ['fashion', 'style', 'wear', 'cloth', 'dress']):
                category = 'fashion'
            elif any(w in brand_name_lower for w in ['tech', 'gadget', 'electronic', 'phone', 'computer']):
                category = 'tech'
            elif any(w in brand_name_lower for w in ['food', 'drink', 'snack', 'beverage']):
                category = 'food'

            # Insert as Tier 2 discovered brand (status=draft for moderation)
            cursor.execute('''
                INSERT INTO pr_brands (
                    brand_name, website, contact_email, category,
                    source, discovery_tier, discovered_at, verified_contact, search_count, status
                )
                VALUES (%s, %s, %s, %s, 'discovered', 2, NOW(), true, 1, 'draft')
                ON CONFLICT DO NOTHING
                RETURNING id, brand_name, website, contact_email, category, source, discovery_tier, verified_contact
            ''', (
                search_query.title(),  # Capitalize brand name
                f"https://{tier2_result['domain']}" if tier2_result.get('domain') else None,
                tier2_result['email'],
                category
            ))

            new_brand = cursor.fetchone()

            if new_brand:
                # Also save to known_brand_contacts for future lookups
                cursor.execute('''
                    INSERT INTO known_brand_contacts (
                        brand_name, normalized_name, domain, contact_email,
                        contact_type, source, verified, usage_count
                    )
                    VALUES (%s, %s, %s, %s, 'pr', 'web_scrape', true, 1)
                    ON CONFLICT (normalized_name, contact_email) DO UPDATE
                    SET usage_count = known_brand_contacts.usage_count + 1,
                        last_used_at = NOW()
                ''', (
                    search_query.title(),
                    normalized_query,
                    tier2_result.get('domain'),
                    tier2_result['email']
                ))
                conn.commit()

                log_discovery_attempt(
                    creator_id, search_query, normalized_query,
                    new_brand['id'], 2, 'discovered_tier2', conn, cursor
                )

                from public_routes import _estimate_package_value
                new_brand['estimated_value'] = _estimate_package_value(category, new_brand['brand_name'])
                new_brand['response_rate'] = None
                new_brand['avg_response_time_days'] = None

                cursor.close()
                conn.close()

                return jsonify({
                    'success': True,
                    'found': True,
                    'brand': dict(new_brand),
                    'source': 'discovered',
                    'discovery_tier': 2,
                    'verified_contact': True,  # Tier 2 emails are scraped from official pages
                    'is_new_discovery': True,
                    'discovery_method': 'web_scrape',
                    'all_emails_found': tier2_result.get('all_emails', [])[:5]  # Show alternatives
                })

        # ============================================
        # STEP 5: Tier 3 - Pattern-Based Email Inference
        # ============================================
        print(f"🔍 Tier 3: Starting pattern inference for {search_query}")
        tier3_result = tier3_pattern_inference(search_query, domain=tier2_result.get('domain'))

        if tier3_result['found'] and tier3_result['email']:
            increment_discovery_attempt(creator_id, conn, cursor)

            # Infer category
            category = 'beauty'
            brand_name_lower = search_query.lower()
            if any(w in brand_name_lower for w in ['gym', 'fit', 'yoga', 'active', 'sport']):
                category = 'fitness'
            elif any(w in brand_name_lower for w in ['fashion', 'style', 'wear', 'cloth', 'dress']):
                category = 'fashion'
            elif any(w in brand_name_lower for w in ['tech', 'gadget', 'electronic', 'phone', 'computer']):
                category = 'tech'
            elif any(w in brand_name_lower for w in ['food', 'drink', 'snack', 'beverage']):
                category = 'food'

            # Insert as Tier 3 discovered brand (UNVERIFIED, status=draft for moderation)
            cursor.execute('''
                INSERT INTO pr_brands (
                    brand_name, website, contact_email, category,
                    source, discovery_tier, discovered_at, verified_contact, search_count, status
                )
                VALUES (%s, %s, %s, %s, 'discovered', 3, NOW(), false, 1, 'draft')
                ON CONFLICT DO NOTHING
                RETURNING id, brand_name, website, contact_email, category, source, discovery_tier, verified_contact
            ''', (
                search_query.title(),
                f"https://{tier3_result['domain']}" if tier3_result.get('domain') else None,
                tier3_result['email'],
                category
            ))

            new_brand = cursor.fetchone()

            if new_brand:
                # Save inferred contact (marked as not verified)
                cursor.execute('''
                    INSERT INTO known_brand_contacts (
                        brand_name, normalized_name, domain, contact_email,
                        contact_type, source, verified, usage_count
                    )
                    VALUES (%s, %s, %s, %s, 'pr', 'pattern_inference', false, 1)
                    ON CONFLICT (normalized_name, contact_email) DO UPDATE
                    SET usage_count = known_brand_contacts.usage_count + 1,
                        last_used_at = NOW()
                ''', (
                    search_query.title(),
                    normalized_query,
                    tier3_result.get('domain'),
                    tier3_result['email']
                ))
                conn.commit()

                log_discovery_attempt(
                    creator_id, search_query, normalized_query,
                    new_brand['id'], 3, 'discovered_tier3', conn, cursor
                )

                from public_routes import _estimate_package_value
                new_brand['estimated_value'] = _estimate_package_value(category, new_brand['brand_name'])
                new_brand['response_rate'] = None
                new_brand['avg_response_time_days'] = None

                cursor.close()
                conn.close()

                return jsonify({
                    'success': True,
                    'found': True,
                    'brand': dict(new_brand),
                    'source': 'discovered',
                    'discovery_tier': 3,
                    'verified_contact': False,  # Tier 3 emails are inferred, NOT verified
                    'is_new_discovery': True,
                    'discovery_method': 'pattern_inference',
                    'alternative_emails': tier3_result.get('alternatives', []),
                    'warning': 'This email was inferred from common patterns and may not be correct. Try alternatives if no response.'
                })

        # ============================================
        # STEP 6: Not found after all tiers
        # ============================================
        increment_discovery_attempt(creator_id, conn, cursor)
        log_discovery_attempt(
            creator_id, search_query, normalized_query,
            None, None, 'not_found', conn, cursor
        )

        cursor.close()
        conn.close()

        return jsonify({
            'success': True,
            'found': False,
            'source': 'not_found',
            'can_discover': True,
            'search_query': search_query,
            'domain_tried': tier2_result.get('domain') or tier3_result.get('domain'),
            'message': f'We couldn\'t find PR contact information for "{search_query}". The brand may not have a public PR program or their contact info isn\'t publicly available.'
        })

    except Exception as e:
        import traceback
        print(f"❌ Error in discover_brand: {str(e)}")
        print(traceback.format_exc())
        return jsonify({'success': False, 'error': str(e)}), 500


@pr_crm.route('/brands/search-suggestions', methods=['GET'])
def get_search_suggestions():
    """
    Get brand name suggestions for search autocomplete.
    Returns top matches from both curated and discovered brands.
    """
    try:
        query = request.args.get('q', '').strip()
        if len(query) < 2:
            return jsonify({'success': True, 'suggestions': []})

        normalized = normalize_brand_name(query)

        conn = get_db_connection()
        cursor = conn.cursor(cursor_factory=RealDictCursor)

        # Search curated brands first, then discovered
        cursor.execute('''
            SELECT id, brand_name, logo_url, category, source
            FROM pr_brands
            WHERE brand_name ILIKE %s
               OR LOWER(REPLACE(brand_name, ' ', '')) ILIKE %s
            ORDER BY
                CASE WHEN source = 'curated' THEN 0 ELSE 1 END,
                search_count DESC NULLS LAST,
                brand_name ASC
            LIMIT 8
        ''', (f'%{query}%', f'%{normalized}%'))

        suggestions = cursor.fetchall()

        cursor.close()
        conn.close()

        return jsonify({
            'success': True,
            'suggestions': [
                {
                    'id': s['id'],
                    'name': s['brand_name'],
                    'logo': s['logo_url'],
                    'category': s['category'],
                    'source': s.get('source', 'curated')
                }
                for s in suggestions
            ]
        })

    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@pr_crm.route('/brands/top-searched', methods=['GET'])
def get_top_searched_brands():
    """
    Get top searched brands for discovery analytics.
    Admin endpoint for reviewing which brands to add to curated list.
    """
    try:
        conn = get_db_connection()
        cursor = conn.cursor(cursor_factory=RealDictCursor)

        # Top searched discovered brands (candidates for curation)
        cursor.execute('''
            SELECT
                normalized_query,
                COUNT(*) as search_count,
                COUNT(DISTINCT creator_id) as unique_searchers,
                MAX(created_at) as last_searched,
                result_status
            FROM brand_discovery_logs
            WHERE created_at >= NOW() - INTERVAL '30 days'
            GROUP BY normalized_query, result_status
            ORDER BY search_count DESC
            LIMIT 50
        ''')

        top_queries = cursor.fetchall()

        cursor.close()
        conn.close()

        return jsonify({
            'success': True,
            'top_queries': top_queries
        })

    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


# ============================================
# PIPELINE MANAGEMENT ENDPOINTS
# ============================================

@pr_crm.route('/pipeline', methods=['GET'])
def get_pipeline():
    """
    Get creator's pipeline
    Query params:
    - stage (str): Filter by stage (saved, pitched, responded, success)
    """
    creator_id = get_creator_id_from_session()
    if not creator_id:
        return jsonify({'success': False, 'error': 'Not authenticated'}), 401

    try:
        stage = request.args.get('stage')

        conn = get_db_connection()
        cursor = conn.cursor(cursor_factory=RealDictCursor)

        # Build query
        if stage:
            query = '''
                SELECT
                    cp.*,
                    pb.brand_name, pb.website, pb.logo_url, pb.cover_image_url, pb.category,
                    pb.instagram_handle, pb.contact_email, pb.application_form_url,
                    pb.has_application_form, pb.description
                FROM creator_pipeline cp
                JOIN pr_brands pb ON cp.brand_id = pb.id
                WHERE cp.creator_id = %s AND cp.stage = %s
                ORDER BY cp.updated_at DESC
            '''
            cursor.execute(query, (creator_id, stage))
        else:
            query = '''
                SELECT
                    cp.*,
                    pb.brand_name, pb.website, pb.logo_url, pb.cover_image_url, pb.category,
                    pb.instagram_handle, pb.contact_email, pb.application_form_url,
                    pb.has_application_form, pb.description
                FROM creator_pipeline cp
                JOIN pr_brands pb ON cp.brand_id = pb.id
                WHERE cp.creator_id = %s
                ORDER BY cp.updated_at DESC
            '''
            cursor.execute(query, (creator_id,))

        pipeline_items = cursor.fetchall()

        # Get counts by stage
        cursor.execute('''
            SELECT stage, COUNT(*) as count
            FROM creator_pipeline
            WHERE creator_id = %s
            GROUP BY stage
        ''', (creator_id,))
        stage_counts = {row['stage']: row['count'] for row in cursor.fetchall()}

        cursor.close()
        conn.close()

        return jsonify({
            'success': True,
            'pipeline': pipeline_items,
            'stage_counts': stage_counts
        })

    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@pr_crm.route('/pipeline/save', methods=['POST'])
def save_brand_to_pipeline():
    """Save a brand to pipeline (stage: saved) - UNLIMITED for all users"""
    creator_id = get_creator_id_from_session()
    if not creator_id:
        return jsonify({'success': False, 'error': 'Not authenticated'}), 401

    conn = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor(cursor_factory=RealDictCursor)

        data = request.json
        brand_id = data.get('brand_id')
        brand_slug = data.get('slug')

        # Resolve brand_id from slug if not provided
        if not brand_id and brand_slug:
            cursor.execute('SELECT id FROM pr_brands WHERE slug = %s', (brand_slug,))
            brand_row = cursor.fetchone()
            if brand_row:
                brand_id = brand_row['id']

        if not brand_id:
            cursor.close()
            conn.close()
            return jsonify({'success': False, 'error': 'brand_id or slug required'}), 400

        # Insert or update - saving is unlimited (no quota check)
        cursor.execute('''
            INSERT INTO creator_pipeline (creator_id, brand_id, stage, created_at, updated_at)
            VALUES (%s, %s, 'saved', NOW(), NOW())
            ON CONFLICT (creator_id, brand_id) DO UPDATE
            SET stage = 'saved', updated_at = NOW()
            RETURNING id
        ''', (creator_id, brand_id))

        pipeline_id = cursor.fetchone()['id']

        # Update creator's brands_saved_count (no daily limit tracking for saves)
        cursor.execute('''
            UPDATE creators
            SET brands_saved_count = COALESCE(brands_saved_count, 0) + 1
            WHERE id = %s
        ''', (creator_id,))

        conn.commit()
        cursor.close()
        conn.close()

        return jsonify({
            'success': True,
            'pipeline_id': pipeline_id,
            'message': 'Brand saved to pipeline'
        })

    except Exception as e:
        if conn:
            conn.rollback()
        return jsonify({'success': False, 'error': str(e)}), 500


@pr_crm.route('/pipeline/<int:pipeline_id>/update-stage', methods=['PATCH'])
def update_pipeline_stage(pipeline_id):
    """Update pipeline stage (move brand to different stage)"""
    creator_id = get_creator_id_from_session()
    if not creator_id:
        return jsonify({'success': False, 'error': 'Not authenticated'}), 401

    try:
        data = request.json
        new_stage = data.get('stage')
        notes = data.get('notes')

        if new_stage not in ['saved', 'pitched', 'responded', 'success', 'rejected', 'archived']:
            return jsonify({'success': False, 'error': 'Invalid stage'}), 400

        conn = get_db_connection()
        cursor = conn.cursor(cursor_factory=RealDictCursor)

        # Update stage
        update_fields = ['stage = %s', 'updated_at = NOW()']
        params = [new_stage]

        # Update specific fields based on stage
        if new_stage == 'pitched':
            update_fields.append('pitched_at = NOW()')
        elif new_stage == 'responded':
            update_fields.append('responded_at = NOW()')
        elif new_stage == 'success':
            update_fields.append('accepted_at = NOW()')

        if notes:
            update_fields.append('notes = %s')
            params.append(notes)

        params.extend([creator_id, pipeline_id])

        query = f'''
            UPDATE creator_pipeline
            SET {', '.join(update_fields)}
            WHERE creator_id = %s AND id = %s
            RETURNING id
        '''

        cursor.execute(query, params)

        if cursor.rowcount == 0:
            cursor.close()
            conn.close()
            return jsonify({'success': False, 'error': 'Pipeline item not found'}), 404

        conn.commit()
        cursor.close()
        conn.close()

        return jsonify({
            'success': True,
            'message': f'Stage updated to {new_stage}'
        })

    except Exception as e:
        if conn:
            conn.rollback()
        return jsonify({'success': False, 'error': str(e)}), 500


@pr_crm.route('/pipeline/<int:pipeline_id>', methods=['DELETE'])
def remove_from_pipeline(pipeline_id):
    """Remove brand from pipeline by pipeline_id"""
    creator_id = get_creator_id_from_session()
    if not creator_id:
        return jsonify({'success': False, 'error': 'Not authenticated'}), 401

    try:
        conn = get_db_connection()
        cursor = conn.cursor()

        cursor.execute('''
            DELETE FROM creator_pipeline
            WHERE creator_id = %s AND id = %s
        ''', (creator_id, pipeline_id))

        if cursor.rowcount == 0:
            cursor.close()
            conn.close()
            return jsonify({'success': False, 'error': 'Pipeline item not found'}), 404

        conn.commit()
        cursor.close()
        conn.close()

        return jsonify({
            'success': True,
            'message': 'Brand removed from pipeline'
        })

    except Exception as e:
        print(f"Error removing from pipeline: {str(e)}")
        return jsonify({'success': False, 'error': str(e)}), 500


@pr_crm.route('/pipeline/brand/<int:brand_id>', methods=['DELETE'])
def remove_brand_from_pipeline(brand_id):
    """Remove brand from pipeline by brand_id (for UI convenience)"""
    creator_id = get_creator_id_from_session()
    if not creator_id:
        return jsonify({'success': False, 'error': 'Not authenticated'}), 401

    try:
        conn = get_db_connection()
        cursor = conn.cursor()

        cursor.execute('''
            DELETE FROM creator_pipeline
            WHERE creator_id = %s AND brand_id = %s
        ''', (creator_id, brand_id))

        if cursor.rowcount == 0:
            cursor.close()
            conn.close()
            return jsonify({'success': False, 'error': 'Brand not found in pipeline'}), 404

        conn.commit()
        cursor.close()
        conn.close()

        return jsonify({
            'success': True,
            'message': 'Brand removed from pipeline'
        })

    except Exception as e:
        if conn:
            conn.rollback()
        return jsonify({'success': False, 'error': str(e)}), 500


# ============================================
# EMAIL TEMPLATES ENDPOINTS
# ============================================

@pr_crm.route('/templates', methods=['GET'])
def get_email_templates():
    """Get all email templates"""
    conn = None
    try:
        creator_id = get_creator_id_from_session()
        is_premium = False

        conn = get_db_connection()
        cursor = conn.cursor(cursor_factory=RealDictCursor)

        # Check if creator is premium
        if creator_id:
            cursor.execute('SELECT subscription_tier FROM creators WHERE id = %s', (creator_id,))
            creator = cursor.fetchone()
            if creator and creator['subscription_tier'] in ['pro', 'elite']:
                is_premium = True

        # Get platform templates
        if is_premium:
            cursor.execute('SELECT * FROM email_templates ORDER BY success_rate DESC')
        else:
            cursor.execute('SELECT * FROM email_templates WHERE is_premium = false ORDER BY success_rate DESC')

        templates = cursor.fetchall()

        # Get creator's custom templates if logged in
        custom_templates = []
        if creator_id:
            try:
                cursor.execute('''
                    SELECT * FROM creator_custom_templates
                    WHERE creator_id = %s
                    ORDER BY created_at DESC
                ''', (creator_id,))
                custom_templates = cursor.fetchall()
            except Exception as custom_error:
                # Table might not exist, skip custom templates
                print(f"Could not fetch custom templates: {str(custom_error)}")
                custom_templates = []

        cursor.close()
        conn.close()

        return jsonify({
            'success': True,
            'templates': templates,
            'custom_templates': custom_templates
        })

    except Exception as e:
        if conn:
            try:
                conn.close()
            except:
                pass
        print(f"Error in get_email_templates: {str(e)}")
        import traceback
        traceback.print_exc()
        return jsonify({'success': False, 'error': str(e)}), 500


@pr_crm.route('/templates/<int:template_id>/render', methods=['POST'])
def render_email_template(template_id):
    """Render email template with creator data"""
    creator_id = get_creator_id_from_session()
    if not creator_id:
        return jsonify({'success': False, 'error': 'Not authenticated'}), 401

    try:
        data = request.json
        brand_name = data.get('brand_name', '')

        conn = get_db_connection()
        cursor = conn.cursor(cursor_factory=RealDictCursor)

        # Get template
        cursor.execute('SELECT * FROM email_templates WHERE id = %s', (template_id,))
        template = cursor.fetchone()

        if not template:
            cursor.close()
            conn.close()
            return jsonify({'success': False, 'error': 'Template not found'}), 404

        # Get creator data
        cursor.execute('''
            SELECT c.*, u.first_name, u.last_name, u.email
            FROM creators c
            JOIN users u ON c.user_id = u.id
            WHERE c.id = %s
        ''', (creator_id,))
        creator = cursor.fetchone()

        cursor.close()
        conn.close()

        # Build variable replacement map
        social_links = creator.get('social_links', [])
        if isinstance(social_links, str):
            social_links = json.loads(social_links)

        primary_platform = social_links[0] if social_links else {}

        variables = {
            'creator_name': f"{creator['first_name']} {creator['last_name']}",
            'brand_name': brand_name,
            'follower_count': f"{creator.get('followers_count', 0):,}",
            'engagement_rate': f"{creator.get('engagement_rate', 0):.1f}",
            'niche': ', '.join(json.loads(creator.get('niche', '[]')) if isinstance(creator.get('niche'), str) else creator.get('niche', [])),
            'primary_platform': primary_platform.get('platform', 'Instagram'),
            'instagram_handle': next((link['username'] for link in social_links if link['platform'] == 'instagram'), '@username'),
            'tiktok_handle': next((link['username'] for link in social_links if link['platform'] == 'tiktok'), ''),
            'youtube_handle': next((link['username'] for link in social_links if link['platform'] == 'youtube'), ''),
            'creator_email': creator['email'],
            'media_kit_link': f"https://newcollab.co/c/{creator.get('username', 'creator')}",
            'location': ', '.join(json.loads(creator.get('regions', '[]')) if isinstance(creator.get('regions'), str) else creator.get('regions', [])),
            'age_range': creator.get('primary_age_range', '18-24'),
        }

        # Render template
        subject = template['subject_line']
        body = template['body_template']

        for var, value in variables.items():
            subject = subject.replace(f'{{{var}}}', str(value))
            body = body.replace(f'{{{var}}}', str(value))

        return jsonify({
            'success': True,
            'subject': subject,
            'body': body,
            'variables_used': variables
        })

    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


# ============================================
# ANALYTICS ENDPOINTS
# ============================================

@pr_crm.route('/analytics', methods=['GET'])
def get_analytics():
    """Get creator's PR CRM analytics"""
    creator_id = get_creator_id_from_session()
    if not creator_id:
        return jsonify({'success': False, 'error': 'Not authenticated'}), 401

    try:
        conn = get_db_connection()
        cursor = conn.cursor(cursor_factory=RealDictCursor)

        # Get overall stats
        cursor.execute('''
            SELECT
                COUNT(*) FILTER (WHERE stage = 'saved') as saved_count,
                COUNT(*) FILTER (WHERE stage = 'pitched') as pitched_count,
                COUNT(*) FILTER (WHERE stage = 'responded') as responded_count,
                COUNT(*) FILTER (WHERE stage = 'success') as success_count,
                COUNT(*) FILTER (WHERE email_opened = true) as opened_count,
                COUNT(*) FILTER (WHERE pitched_at IS NOT NULL) as total_pitched
            FROM creator_pipeline
            WHERE creator_id = %s
        ''', (creator_id,))

        stats = cursor.fetchone()

        # Calculate rates
        total_pitched = stats['total_pitched'] or 1  # Avoid division by zero
        open_rate = (stats['opened_count'] / total_pitched * 100) if total_pitched > 0 else 0
        response_rate = (stats['responded_count'] / total_pitched * 100) if total_pitched > 0 else 0
        success_rate = (stats['success_count'] / total_pitched * 100) if total_pitched > 0 else 0

        cursor.close()
        conn.close()

        return jsonify({
            'success': True,
            'stats': {
                **stats,
                'open_rate': round(open_rate, 1),
                'response_rate': round(response_rate, 1),
                'success_rate': round(success_rate, 1)
            }
        })

    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


# ============================================
# REVEAL CONTACT ENDPOINT
# ============================================

# ============================================
# PITCH TRACKING ENDPOINTS
# ============================================

@pr_crm.route('/pitch-limits', methods=['GET'])
def get_pitch_limits():
    """Get creator's pitch limits for the month"""
    creator_id = get_creator_id_from_session()
    if not creator_id:
        return jsonify({'success': False, 'error': 'Not authenticated'}), 401

    try:
        conn = get_db_connection()
        cursor = conn.cursor(cursor_factory=RealDictCursor)

        # Get creator's subscription tier and pitch count
        cursor.execute('''
            SELECT subscription_tier, pitches_sent_this_week, last_pitch_reset
            FROM creators
            WHERE id = %s
        ''', (creator_id,))
        creator = cursor.fetchone()

        cursor.close()
        conn.close()

        if not creator:
            return jsonify({'success': False, 'error': 'Creator not found'}), 404

        tier = creator['subscription_tier'] or 'free'
        pitches_used = creator.get('pitches_sent_this_week') or 0
        last_reset = creator.get('last_pitch_reset')

        # Reset monthly count if needed (resets on 1st of each month)
        from datetime import date
        today = date.today()
        month_start = today.replace(day=1)

        if last_reset is None or last_reset < month_start:
            pitches_used = 0

        # DEPRECATED: Use /api/pr-crm/unlocks/balance instead
        # FREE users get 5 contacts per MONTH (new credit unlock system)
        FREE_MONTHLY_LIMIT = 5
        is_pro = tier in ['pro', 'elite']

        return jsonify({
            'success': True,
            'used': pitches_used,
            'limit': FREE_MONTHLY_LIMIT if not is_pro else 999,
            'canPitch': is_pro or pitches_used < FREE_MONTHLY_LIMIT,
            'tier': tier,
            'period': 'month'
        })

    except Exception as e:
        print(f"Error in get_pitch_limits: {str(e)}")
        return jsonify({'success': False, 'error': str(e)}), 500


@pr_crm.route('/track-pitch', methods=['POST'])
def track_pitch():
    """Track when a creator sends a pitch and update pipeline stage"""
    creator_id = get_creator_id_from_session()
    if not creator_id:
        return jsonify({'success': False, 'error': 'Not authenticated'}), 401

    try:
        data = request.get_json()
        brand_id = data.get('brand_id')
        brand_slug = data.get('slug')
        pipeline_id = data.get('pipeline_id')

        # Resolve brand_id from slug if not provided
        if not brand_id and brand_slug:
            conn_temp = get_db_connection()
            cursor_temp = conn_temp.cursor(cursor_factory=RealDictCursor)
            cursor_temp.execute('SELECT id FROM pr_brands WHERE slug = %s', (brand_slug,))
            brand_row = cursor_temp.fetchone()
            cursor_temp.close()
            conn_temp.close()
            if brand_row:
                brand_id = brand_row['id']

        if not brand_id:
            return jsonify({'success': False, 'error': 'brand_id or slug required'}), 400

        conn = get_db_connection()
        cursor = conn.cursor(cursor_factory=RealDictCursor)

        # Check pitch limits
        cursor.execute('''
            SELECT subscription_tier, pitches_sent_this_week, last_pitch_reset
            FROM creators
            WHERE id = %s
        ''', (creator_id,))
        creator = cursor.fetchone()

        if not creator:
            cursor.close()
            conn.close()
            return jsonify({'success': False, 'error': 'Creator not found'}), 404

        tier = creator['subscription_tier'] or 'free'
        pitches_used = creator.get('pitches_sent_this_week') or 0
        last_reset = creator.get('last_pitch_reset')

        # Reset monthly count if needed (resets on 1st of each month)
        from datetime import date
        today = date.today()
        month_start = today.replace(day=1)

        if last_reset is None or last_reset < month_start:
            pitches_used = 0

        # DEPRECATED: Use new credit unlock system (attempt_unlock) instead
        # Free users get 5 contacts per MONTH
        FREE_MONTHLY_LIMIT = 5
        is_pro = tier in ['pro', 'elite']

        if not is_pro and pitches_used >= FREE_MONTHLY_LIMIT:
            cursor.close()
            conn.close()
            return jsonify({
                'success': False,
                'error': 'Monthly contact limit reached. Upgrade to Pro for unlimited contacts!',
                'upgrade_required': True,
                'used': pitches_used,
                'limit': FREE_MONTHLY_LIMIT
            }), 403

        # Update pitch count
        new_pitch_count = pitches_used + 1
        cursor.execute('''
            UPDATE creators
            SET pitches_sent_this_week = %s,
                last_pitch_reset = %s,
                last_pitch_at = NOW()
            WHERE id = %s
        ''', (new_pitch_count, month_start, creator_id))

        # Per emailflowbrief.md Stage 5: When user hits 3rd pitch, schedule
        # quota email for 7 days later (not immediately)
        if new_pitch_count == 3 and tier == 'free':
            cursor.execute('''
                UPDATE creators
                SET quota_email_send_at = NOW() + INTERVAL '7 days'
                WHERE id = %s
                  AND quota_email_send_at IS NULL
            ''', (creator_id,))

        # Generate kit tracking token for this creator/brand pair
        kit_token = generate_kit_token(creator_id, brand_id)
        print(f"[TRACK_PITCH] Storing kit_token: {kit_token} for creator_id: {creator_id}, brand_id: {brand_id}")

        # Generate email tracking token (for open tracking pixel)
        import uuid
        tracking_token = str(uuid.uuid4()).replace('-', '')[:32]

        # Update pipeline stage to 'pitched' if in pipeline, include kit_token and tracking_token
        cursor.execute('''
            INSERT INTO creator_pipeline (creator_id, brand_id, stage, pitched_at, kit_token, tracking_token, created_at, updated_at)
            VALUES (%s, %s, 'pitched', NOW(), %s, %s, NOW(), NOW())
            ON CONFLICT (creator_id, brand_id) DO UPDATE
            SET stage = 'pitched',
                pitched_at = NOW(),
                kit_token = COALESCE(creator_pipeline.kit_token, %s),
                tracking_token = COALESCE(creator_pipeline.tracking_token, %s),
                updated_at = NOW()
            RETURNING tracking_token
        ''', (creator_id, brand_id, kit_token, tracking_token, kit_token, tracking_token))

        result = cursor.fetchone()
        final_tracking_token = result['tracking_token'] if result else tracking_token

        # Set first_pitch_sent_at if this is their first pitch (for email conversion sequence)
        cursor.execute('''
            UPDATE creators
            SET first_pitch_sent_at = NOW()
            WHERE id = %s AND first_pitch_sent_at IS NULL
        ''', (creator_id,))

        conn.commit()
        cursor.close()
        conn.close()

        # Build tracking pixel URL
        api_base = os.getenv('API_BASE_URL', 'https://api.newcollab.co')
        tracking_pixel_url = f"{api_base}/api/public/t/{final_tracking_token}.png"

        return jsonify({
            'success': True,
            'message': 'Pitch tracked successfully',
            'pitches_used': pitches_used + 1,
            'tier': tier,
            'tracking_token': final_tracking_token,
            'tracking_pixel_url': tracking_pixel_url
        })

    except Exception as e:
        print(f"Error in track_pitch: {str(e)}")
        import traceback
        traceback.print_exc()
        return jsonify({'success': False, 'error': str(e)}), 500


@pr_crm.route('/generate-pitch', methods=['POST'])
def generate_pitch():
    """Generate an AI pitch for a brand (uses Golden Template for now)"""
    creator_id = get_creator_id_from_session()
    if not creator_id:
        return jsonify({'success': False, 'error': 'Not authenticated'}), 401

    try:
        data = request.get_json()
        brand_id = data.get('brand_id')
        brand_slug = data.get('slug')
        is_followup = data.get('is_followup', False)

        if not brand_id and not brand_slug:
            return jsonify({'success': False, 'error': 'brand_id or slug required'}), 400

        conn = get_db_connection()
        cursor = conn.cursor(cursor_factory=RealDictCursor)

        # Get brand details - try by ID first, then by slug
        brand = None
        if brand_id:
            cursor.execute('SELECT * FROM pr_brands WHERE id = %s', (brand_id,))
            brand = cursor.fetchone()

        if not brand and brand_slug:
            cursor.execute('SELECT * FROM pr_brands WHERE slug = %s', (brand_slug,))
            brand = cursor.fetchone()

        if not brand:
            cursor.close()
            conn.close()
            return jsonify({'success': False, 'error': 'Brand not found'}), 404

        # ========== CREDIT UNLOCK CHECK (NO DEDUCTION) ==========
        # Check if user CAN unlock this brand - actual credit deduction happens in confirm-send
        # Skip check for follow-ups (brand already unlocked if they pitched before)
        if not is_followup:
            unlock_check = can_unlock(creator_id, brand['id'], conn)

            if not unlock_check.get('can_unlock'):
                cursor.close()
                conn.close()
                return jsonify({
                    'success': False,
                    'paywall': True,
                    'remaining': 0,
                    'reset_at': unlock_check.get('reset_at'),
                    'message': "You've used all 5 contacts this month."
                }), 402  # Payment Required

            if unlock_check.get('error'):
                cursor.close()
                conn.close()
                return jsonify({'success': False, 'error': unlock_check.get('error', 'Unlock check failed')}), 500

            # Log the check for debugging (no credit deducted yet!)
            print(f"[generate_pitch] Unlock CHECK (no deduction) for creator {creator_id}, brand {brand['id']}: {unlock_check}")
        # =========================================================

        # Get creator profile with collab count for tiered pitch generation
        cursor.execute('''
            SELECT c.*, u.first_name, u.last_name, u.email,
                   (SELECT COUNT(*) FROM creator_pipeline cp
                    WHERE cp.creator_id = c.id
                    AND cp.stage IN ('success', 'responded')) AS collab_count,
                   (SELECT cp2.brand_id FROM creator_pipeline cp2
                    JOIN pr_brands pb ON pb.id = cp2.brand_id
                    WHERE cp2.creator_id = c.id AND cp2.stage = 'success'
                    ORDER BY cp2.updated_at DESC LIMIT 1) AS last_collab_brand_id,
                   (SELECT pb.brand_name FROM creator_pipeline cp2
                    JOIN pr_brands pb ON pb.id = cp2.brand_id
                    WHERE cp2.creator_id = c.id AND cp2.stage = 'success'
                    ORDER BY cp2.updated_at DESC LIMIT 1) AS last_collab_brand_name
            FROM creators c
            JOIN users u ON c.user_id = u.id
            WHERE c.id = %s
        ''', (creator_id,))
        creator = cursor.fetchone()

        cursor.close()
        conn.close()

        if not creator:
            return jsonify({'success': False, 'error': 'Creator not found'}), 404

        # Generate pitch using Golden Template or Follow-up Template
        if is_followup:
            pitch = generate_followup_pitch(brand, creator)
        else:
            pitch = generate_golden_template_pitch(brand, creator)

        # Debug: log what we're returning
        print(f"[generate_pitch] Brand ID: {brand.get('id')}, Name: {brand.get('brand_name')}, is_followup: {is_followup}")
        print(f"[generate_pitch] Email: {brand.get('contact_email')}, App URL: {brand.get('application_form_url')}")

        # Get current unlock balance for response
        unlock_balance = get_creator_unlock_balance(creator_id)

        return jsonify({
            'success': True,
            **pitch,
            'brand_email': brand.get('contact_email'),
            'brand_name': brand.get('brand_name'),
            'brand_logo': brand.get('logo_url'),
            'application_form_url': brand.get('application_form_url'),
            'brand_unlocked': True,  # Indicates this brand is now unlocked
            'unlock_balance': unlock_balance  # Current remaining unlocks
        })

    except Exception as e:
        print(f"Error in generate_pitch: {str(e)}")
        import traceback
        traceback.print_exc()
        return jsonify({'success': False, 'error': str(e)}), 500


# ============================================
# TIERED PITCH GENERATION SYSTEM
# Different strategies based on creator tier
# ============================================

# Tier definitions:
# - Tier 1 (Starter): 0-999 followers, 0 past collabs
# - Tier 2 (Growing): 1k-9.9k followers OR has 1+ past collab
# - Tier 3 (Established): 10k+ followers

def compute_creator_tier(followers, collab_count):
    """
    Compute creator tier based on followers and collab history.

    Returns: (tier_number, tier_label)
    - Tier 1: Starter (0-999 followers, no collabs)
    - Tier 2: Growing (1k-9.9k followers OR has collabs)
    - Tier 3: Established (10k+ followers)
    """
    followers = followers or 0
    collab_count = collab_count or 0

    # Tier 3: 10k+ followers (regardless of collab history)
    if followers >= 10000:
        return (3, 'established')

    # Tier 2: 1k-9.9k with collabs OR under 1k with collabs
    if followers >= 1000 or collab_count >= 1:
        return (2, 'growing')

    # Tier 1: Under 1k, no collabs
    return (1, 'starter')


# Banned phrases that make pitches sound AI-generated
BANNED_PHRASES = [
    'in today\'s world', 'unlock', 'leverage', 'elevate', 'game changer',
    'game-changer', 'dive into', 'seamless', 'unparalleled', 'in the realm of',
    'delve', 'cutting-edge', 'synergy', 'revolutionary', 'disruptive',
    'best-in-class', 'world-class', 'state-of-the-art', 'next-level',
    'take it to the next level', 'resonate', 'impactful', 'holistic',
]

# Phrases that Tier 1 (Starter) creators should NOT use
TIER1_BANNED_PHRASES = [
    'my followers ask', 'my audience', 'my followers', 'my comments ask',
    'followers ask', 'audience loves', 'followers love', 'community loves',
    'followers trust', 'audience trusts', 'followers expect', 'audience expects',
    'followers screenshot', 'followers save', 'followers engage',
    'comments ask', 'DMs asking', 'keep asking me',
]


def validate_pitch_content(body, tier):
    """
    Validate pitch content for banned phrases.
    Returns (is_valid, issues_list)
    """
    issues = []
    body_lower = body.lower()

    # Check for em dashes (AI writing signature)
    if '—' in body:
        issues.append('Contains em dash (—)')

    # Check for general banned phrases
    for phrase in BANNED_PHRASES:
        if phrase.lower() in body_lower:
            issues.append(f'Contains banned phrase: "{phrase}"')

    # Tier 1 specific: no audience/follower claims
    if tier == 1:
        for phrase in TIER1_BANNED_PHRASES:
            if phrase.lower() in body_lower:
                issues.append(f'Tier 1 cannot claim: "{phrase}"')

    return (len(issues) == 0, issues)


# ============================================
# TIER-SPECIFIC TEMPLATES
# ============================================

# TIER 1 (Starter): Focus on content creation skills, NOT audience
# These templates emphasize UGC value and content quality
TIER1_LINE1_TEMPLATES = {
    'skincare': [
        "I create skincare content that brands use for their own ads and social.",
        "I make before-and-after skincare content with real lighting and real results.",
    ],
    'beauty': [
        "I create beauty content that works as UGC for brand social and ads.",
        "I film clean, well-lit beauty tutorials that brands can repurpose.",
    ],
    'wellness': [
        "I create wellness content that brands use for authentic social proof.",
        "I make routine-based wellness videos with real product integration.",
    ],
    'fitness': [
        "I create workout content that shows products in real training scenarios.",
        "I film fitness content that brands can use for UGC ads and social.",
    ],
    'fashion': [
        "I create styled content that shows how pieces actually look and move.",
        "I film outfit content with clean backgrounds and natural movement.",
    ],
    'food': [
        "I create recipe content that shows products in real kitchen use.",
        "I make food content with clear process shots and final plating.",
    ],
    'supplement': [
        "I create routine content showing how supplements fit into a real day.",
        "I film morning-stack videos that brands use for authentic social proof.",
    ],
    'default': [
        "I create content that brands use for their social and UGC ads.",
        "I make product content with clean visuals and authentic integration.",
    ],
}

# TIER 2 (Growing): Reference past collabs and engagement
TIER2_LINE1_TEMPLATES = {
    'skincare': [
        "I've partnered with skincare brands before and my content drives real engagement.",
        "I create skincare content with {engagement_rate}% engagement from followers who actually buy.",
    ],
    'beauty': [
        "My beauty content consistently drives engagement and saves from followers ready to purchase.",
        "I've worked with beauty brands and know how to create content that converts.",
    ],
    'wellness': [
        "I create wellness content that my {followers_str} followers actively engage with.",
        "My wellness audience has {engagement_rate}% engagement and trusts my recommendations.",
    ],
    'fitness': [
        "My fitness content reaches {followers_str} followers who engage at {engagement_rate}%.",
        "I've partnered with fitness brands and my audience responds to gear recommendations.",
    ],
    'fashion': [
        "My fashion content drives {engagement_rate}% engagement from style-focused followers.",
        "I create fashion content that {followers_str} followers actively save and shop from.",
    ],
    'food': [
        "My food content reaches {followers_str} followers who try products I feature.",
        "I create recipe content with {engagement_rate}% engagement from foodies who cook along.",
    ],
    'default': [
        "I create content for {followers_str} followers with {engagement_rate}% engagement.",
        "My content drives real engagement from followers who act on recommendations.",
    ],
}

# TIER 3 (Established): Lead with audience data and past partners
TIER3_LINE1_TEMPLATES = {
    'skincare': [
        "Your {product} {verb_is} what my {followers_str} skincare followers ask about.",
        "The {product} {verb_has} the formula my {followers_str} audience looks for.",
    ],
    'beauty': [
        "Your {product} {verb_is} what my {followers_str} beauty followers request.",
        "The {product} {verb_has} the quality my {followers_str} audience expects.",
    ],
    'wellness': [
        "Your {product} {verb_is} what my {followers_str} wellness audience asks about.",
        "The {product} {verb_has} the clean profile my {followers_str} followers look for.",
    ],
    'fitness': [
        "Your {product} {verb_is} what my {followers_str} fitness followers want to see.",
        "The {product} {verb_has} the performance my {followers_str} audience expects.",
    ],
    'fashion': [
        "Your {product} {verb_is} what my {followers_str} fashion followers save.",
        "The {product} {verb_has} the style my {followers_str} audience shops for.",
    ],
    'food': [
        "Your {product} {verb_is} what my {followers_str} foodie followers ask about.",
        "The {product} {verb_has} the quality my {followers_str} audience looks for.",
    ],
    'default': [
        "Your {product} {verb_is} what my {followers_str} followers ask about.",
        "The {product} {verb_has} the quality my audience of {followers_str} expects.",
    ],
}

# TIER-SPECIFIC LINE 2 (Creator proof)
def get_tier_proof_line(tier, creator_data, past_collab_brand=None, past_collab_views=None):
    """Generate tier-appropriate creator proof line."""
    followers_str = creator_data.get('followers_str', 'growing')
    engagement_rate = creator_data.get('engagement_rate', 5.0)
    platform = creator_data.get('platform', 'Instagram')
    niche = creator_data.get('niche', 'content')

    if tier == 1:
        # Tier 1: Focus on content skills, not audience
        return f"I create {niche.lower()} content on {platform}. Even while building my following, I make UGC-style videos that brands can use directly in their own ads."

    elif tier == 2:
        # Tier 2: Reference engagement and past work if available
        if past_collab_brand and past_collab_views:
            return f"I previously partnered with {past_collab_brand} and that video reached {past_collab_views} views. I create {niche.lower()} content for {followers_str} followers with {engagement_rate}% engagement."
        elif past_collab_brand:
            return f"I've partnered with brands like {past_collab_brand}. I create {niche.lower()} content on {platform} for {followers_str} followers with {engagement_rate}% engagement."
        else:
            return f"I create {niche.lower()} content on {platform} for {followers_str} followers with {engagement_rate}% engagement rate."

    else:
        # Tier 3: Full stats
        return f"I create {niche.lower()} content on {platform} ({followers_str} followers, {engagement_rate}% engagement)."


# TIER-SPECIFIC ASK LINES
TIER1_ASK = "Would you be open to sending product in exchange for content you can use on your own channels?"
TIER2_ASK = "Would you be open to a gifted collaboration?"
TIER3_ASK = "Would you be open to discussing a gifted post or potential partnership?"


# ============================================
# LEGACY TEMPLATES (kept for Tier 3 / fallback)
# ============================================

# LINE 1 TEMPLATES: Brand hook per product category
# Variables: {product}, {verb_is}, {verb_has}
LINE1_TEMPLATES = {
    'spf': [
        "Your {product} {verb_is} the invisible-finish SPF my followers keep asking me to find.",
        "The {product} {verb_is} the no-white-cast formula my comments ask for every time I post a sunny-day routine.",
    ],
    'serum': [
        "The {product} {verb_has} the active percentage my audience actually looks for.",
        "Your {product} {verb_has} the formula my followers ask about when they see my skincare posts.",
    ],
    'patches': [
        "The {product} dissolve by morning. They're the ones showing up in my skincare shelf posts.",
        "Your {product} {verb_is} the overnight fix my followers screenshot from my content.",
    ],
    'supplement': [
        "The {product} {verb_has} the clean ingredient label my fitness audience actually reads.",
        "Your {product} {verb_is} the kind of daily stack my followers ask about in every routine post.",
    ],
    'fragrance': [
        "Your {product} {verb_has} the aesthetic my audience screenshots from my content.",
        "The {product} scent profile {verb_is} what I'd actually have on during a filming day.",
    ],
    'activewear': [
        "The {product} {verb_is} the kind of fit my workout audience asks about in every video.",
        "Your {product} {verb_has} the look and feel my fitness followers actively search for.",
    ],
    'food': [
        "Your {product} {verb_is} what my audience asks about when they see my kitchen content.",
        "The {product} {verb_has} the clean label my food followers actually read before buying.",
    ],
    'skincare': [
        "The {product} {verb_has} the formula my followers trust based on my routine content.",
        "Your {product} {verb_is} what my skincare audience asks for when they see my shelf.",
    ],
    'makeup': [
        "The {product} {verb_has} the finish my audience asks about in every get-ready post.",
        "Your {product} {verb_is} the kind of quality my beauty followers expect to see.",
    ],
    'haircare': [
        "The {product} {verb_has} the salon-quality formula my followers actively search for.",
        "Your {product} {verb_is} what my audience asks about when they see my hair routine.",
    ],
    'fashion': [
        "Your {product} {verb_is} the piece my audience saves every time I post a styled look.",
        "The {product} {verb_has} the fit my followers ask about when they see my outfit content.",
    ],
    'home': [
        "Your {product} {verb_is} the one my followers screenshot every time it appears in my space content.",
        "The {product} {verb_has} the aesthetic my audience looks for in home finds.",
    ],
    'pet': [
        "Your {product} {verb_is} what my audience asks about every time I show my pet on camera.",
        "The {product} {verb_has} the quality my pet community expects from brands I feature.",
    ],
    'fitness': [
        "Your {product} {verb_is} what comes up in my comments every time I film a training session.",
        "The {product} {verb_has} the performance my fitness audience actively looks for.",
    ],
    'jewelry': [
        "Your {product} {verb_is} the piece my followers ask about every time it shows up in my content.",
        "The {product} {verb_has} the detail my audience zooms in on in every post.",
    ],
    'luxury': [
        "Your {product} {verb_is} what my audience saves every time I feature elevated pieces.",
        "The {product} {verb_has} the craftsmanship my followers expect from the brands I work with.",
    ],
    'lifestyle': [
        "Your {product} {verb_is} what my audience asks about when they see my daily content.",
        "The {product} {verb_has} the quality my lifestyle followers look for in my recommendations.",
    ],
    'default': [
        "Your {product} {verb_is} what my audience asks about when they see products like this.",
        "The {product} {verb_has} the quality my followers expect from brands I feature.",
    ],
}

# LINE 3 TEMPLATES: Content idea per product category
# Variables: {short}, {product}, {activity}, {workout}, {meal}, {timeframe}
LINE3_TEMPLATES = {
    'spf': [
        "I'd film my morning run with {short} already on. No reapplication, no white cast, just the result.",
        "I'd show the application, then 4 hours later: no shine, no pilling, under makeup.",
    ],
    'serum': [
        "I'd show {short} going on, skin texture before, skin 20 minutes after.",
        "I'd build a 60-second routine around {short}, showing how it layers between cleanse and SPF.",
    ],
    'patches': [
        "I'd show {short} going on overnight, then the morning reveal. Before and after.",
        "I'd film applying {short} at night, then the peel-off moment with the gunk visible.",
    ],
    'supplement': [
        "I'd film the morning stack: {short} next to the water bottle, before the workout, as part of the routine my audience follows.",
        "I'd build a 60-second 'what I take every morning' video around {short}. Timing, why I started, what I pair it with.",
    ],
    'fragrance': [
        "I'd build a getting-ready moment around {short}. Bottle on the counter, the spritz before leaving, the mood it sets.",
        "I'd show {short} as part of a morning shelf moment. What it sits next to, why it's the one I reach for.",
    ],
    'activewear': [
        "I'd film a full workout with {short}, showing how it holds up through sweat.",
        "I'd show the gear check before a training session, {short} front and center.",
    ],
    'food': [
        "I'd show {short} in a real recipe: the prep, the cook, the final plate.",
        "I'd feature {short} in my morning routine, from fridge to table to taste test.",
    ],
    'skincare': [
        "I'd show {short} going on, skin texture before, skin 20 minutes after.",
        "I'd build a 60-second routine around {short}, showing how it fits my actual skincare steps.",
    ],
    'makeup': [
        "I'd show {short} in a real get-ready, before and after the full look.",
        "I'd feature {short} in a tutorial, showing the application technique that works.",
    ],
    'haircare': [
        "I'd show {short} in my actual wash-day routine, from application to styled result.",
        "I'd film the before and after with {short}, showing texture and shine difference.",
    ],
    'fashion': [
        "I'd style {short} three ways: how I'd wear it day-to-day, dressed up, and the way my audience actually lives in it.",
        "I'd film {short} in a real outfit video: the fit, the fabric close-up, how it moves.",
    ],
    'home': [
        "I'd film {short} styled into a real corner of my space: natural light, what it sits next to, the detail shot.",
        "I'd show {short} in my actual home: the unboxing, the placement, the before and after of the space.",
    ],
    'pet': [
        "I'd film my pet with {short}: the reaction, the use, and the honest result my audience expects from me.",
        "I'd show {short} in a real moment with my pet: how they interact with it, whether it holds up.",
    ],
    'fitness': [
        "I'd take {short} through a full session: how it performs, how it holds up, the result after.",
        "I'd film {short} in my actual workout: the fit check, the sweat test, the honest review.",
    ],
    'jewelry': [
        "I'd show {short} in a getting-ready moment: how it layers, what it goes with, the close-up detail.",
        "I'd film {short} styled three ways: casual, elevated, and the way I'd actually wear it daily.",
    ],
    'luxury': [
        "I'd dedicate a video to {short}: the craftsmanship, the details, how it fits into my content.",
        "I'd show {short} in an elevated moment: the unboxing, the styling, the quality close-ups.",
    ],
    'lifestyle': [
        "I'd show {short} in my actual daily routine: how I use it, when I reach for it, why it works.",
        "I'd film {short} in a real moment: the context, the use, the result my audience can relate to.",
    ],
    'default': [
        "I'd show {short} in a real moment: how I use it, why it fits my content, what my audience would see.",
        "I'd film {short} in an authentic use-case: the context, the detail, the honest result.",
    ],
}

# LINE 4 TEMPLATES: Ask with appropriate unit per category
# Variables: {short}
LINE4_TEMPLATES = {
    'spf': "Would you be open to sending a bottle of {short}?",
    'serum': "Would you be open to sending a bottle of {short}?",
    'patches': "Would you be open to sending a pack of {short}?",
    'supplement': "Would you be open to sending a month's supply?",
    'fragrance': "Would you be open to sending a bottle to try?",
    'activewear': "Would you be open to sending a pair to try?",
    'food': "Would you be open to sending some to try?",
    'skincare': "Would you be open to sending a bottle of {short}?",
    'makeup': "Would you be open to sending {short} to try?",
    'haircare': "Would you be open to sending a bottle of {short}?",
    'fashion': "Would you be open to sending a piece to try?",
    'home': "Would you be open to sending one to feature?",
    'pet': "Would you be open to sending some for my pet to try?",
    'fitness': "Would you be open to sending some gear to test?",
    'jewelry': "Would you be open to sending a piece to feature?",
    'luxury': "Would you be open to loaning a piece for content?",
    'lifestyle': "Would you be open to sending one to try?",
    'default': "Would you be open to sending a sample to try?",
}

# CATEGORY MAP: Maps brand.category strings to template keys
CATEGORY_MAP = {
    # SPF/Sunscreen
    'sunscreen': 'spf', 'spf': 'spf', 'sun care': 'spf', 'sun protection': 'spf',
    # Serum
    'serum': 'serum', 'serums': 'serum', 'face oil': 'serum', 'treatment': 'serum',
    # Patches
    'patches': 'patches', 'pimple patches': 'patches', 'acne patches': 'patches', 'hydrocolloid': 'patches',
    # Supplements
    'supplement': 'supplement', 'supplements': 'supplement', 'vitamins': 'supplement',
    'wellness': 'supplement', 'gummies': 'supplement', 'capsules': 'supplement',
    'protein': 'supplement', 'collagen': 'supplement', 'probiotics': 'supplement',
    # Fragrance
    'fragrance': 'fragrance', 'perfume': 'fragrance', 'cologne': 'fragrance',
    'candle': 'fragrance', 'candles': 'fragrance', 'home fragrance': 'fragrance',
    'scent': 'fragrance', 'mist': 'fragrance',
    # Activewear
    'activewear': 'activewear', 'athleisure': 'activewear', 'sportswear': 'activewear',
    'fitness apparel': 'activewear', 'workout wear': 'activewear', 'leggings': 'activewear',
    # Food
    'food': 'food', 'beverage': 'food', 'snacks': 'food', 'drinks': 'food',
    'cooking': 'food', 'kitchen': 'food', 'pantry': 'food',
    # Skincare
    'skincare': 'skincare', 'skin care': 'skincare', 'moisturizer': 'skincare',
    'cleanser': 'skincare', 'face': 'skincare', 'anti-aging': 'skincare',
    # Makeup
    'makeup': 'makeup', 'cosmetics': 'makeup', 'beauty': 'makeup',
    'lipstick': 'makeup', 'foundation': 'makeup', 'mascara': 'makeup',
    # Haircare
    'haircare': 'haircare', 'hair care': 'haircare', 'hair': 'haircare',
    'shampoo': 'haircare', 'conditioner': 'haircare', 'styling': 'haircare',
    # Fashion
    'fashion': 'fashion', 'clothing': 'fashion', 'apparel': 'fashion',
    'accessories': 'fashion', 'shoes': 'fashion', 'bags': 'fashion',
    # Home
    'home': 'home', 'home decor': 'home', 'decor': 'home', 'furniture': 'home',
    'interiors': 'home', 'home goods': 'home', 'bedding': 'home',
    # Pet
    'pet': 'pet', 'pets': 'pet', 'dog': 'pet', 'cat': 'pet',
    'pet food': 'pet', 'pet care': 'pet', 'pet supplies': 'pet',
    # Fitness
    'fitness': 'fitness', 'gym': 'fitness', 'workout': 'fitness',
    'sports': 'fitness', 'training': 'fitness', 'exercise': 'fitness',
    # Jewelry
    'jewelry': 'jewelry', 'jewellery': 'jewelry', 'accessories': 'jewelry',
    'watches': 'jewelry', 'rings': 'jewelry', 'necklaces': 'jewelry',
    # Luxury
    'luxury': 'luxury', 'premium': 'luxury', 'designer': 'luxury',
    'high-end': 'luxury', 'luxury fashion': 'luxury',
    # Lifestyle
    'lifestyle': 'lifestyle', 'other': 'lifestyle',
    # Tech -> lifestyle (accessories fit lifestyle template)
    'tech': 'lifestyle', 'technology': 'lifestyle', 'gadgets': 'lifestyle',
}


def get_template_key(category, hero_product=None):
    """
    Get the template key for a brand based on category and hero product.
    Checks hero_product first for product-type keywords, then falls back to category.
    """
    # First, check hero_product for specific product types (more accurate)
    if hero_product:
        hp_lower = hero_product.lower()
        # Product-specific detection
        if any(w in hp_lower for w in ['sunscreen', 'spf', 'sun protection']):
            return 'spf'
        if any(w in hp_lower for w in ['serum', 'oil', 'acid']):
            return 'serum'
        if any(w in hp_lower for w in ['patch', 'hydro', 'pimple']):
            return 'patches'
        if any(w in hp_lower for w in ['supplement', 'vitamin', 'gummy', 'capsule', 'protein', 'collagen', 'probiotic', 'magnesium', 'creatine']):
            return 'supplement'
        if any(w in hp_lower for w in ['fragrance', 'perfume', 'cologne', 'candle', 'scent', 'mist']):
            return 'fragrance'
        if any(w in hp_lower for w in ['legging', 'shorts', 'sports bra', 'activewear', 'workout gear']):
            return 'activewear'
        if any(w in hp_lower for w in ['shampoo', 'conditioner', 'hair mask', 'hair oil']):
            return 'haircare'
        if any(w in hp_lower for w in ['foundation', 'lipstick', 'mascara', 'palette', 'eyeshadow', 'blush', 'concealer']):
            return 'makeup'
        if any(w in hp_lower for w in ['cleanser', 'moisturizer', 'toner', 'face cream', 'face lotion']):
            return 'skincare'
        # New categories
        if any(w in hp_lower for w in ['dress', 'jacket', 'coat', 'shirt', 'pants', 'jeans', 'sweater', 'top', 'skirt']):
            return 'fashion'
        if any(w in hp_lower for w in ['pillow', 'blanket', 'lamp', 'vase', 'rug', 'throw', 'decor']):
            return 'home'
        if any(w in hp_lower for w in ['dog food', 'cat food', 'pet toy', 'dog treat', 'cat treat', 'leash', 'collar']):
            return 'pet'
        if any(w in hp_lower for w in ['dumbbell', 'kettlebell', 'resistance band', 'yoga mat', 'fitness']):
            return 'fitness'
        if any(w in hp_lower for w in ['necklace', 'bracelet', 'ring', 'earring', 'watch', 'chain']):
            return 'jewelry'

    # Fall back to category map
    if category:
        cat_lower = category.lower().strip()
        return CATEGORY_MAP.get(cat_lower, 'default')

    return 'default'


def generate_golden_template_pitch(brand, creator):
    """
    Generate a proven cold PR pitch email using semi-custom templates.

    Uses category-based templates with dynamic slot filling.
    AI fills proven sentences, doesn't invent content.

    Formula:
    - Line 1: Category-specific brand hook (from LINE1_TEMPLATES)
    - Line 2: Creator proof with stats
    - Line 3: Category-specific content idea (from LINE3_TEMPLATES)
    - Line 4: Category-specific ask (from LINE4_TEMPLATES)
    """
    import random
    import re

    # ===== HELPER FUNCTIONS =====
    def is_valid_first_name(name):
        """Check if name is a real first name, not a username/handle."""
        if not name:
            return False
        name = name.strip()
        if name.islower() and len(name) < 4:
            return False
        if '_' in name or any(c.isdigit() for c in name):
            return False
        if name.lower() in ['admin', 'user', 'creator', 'test', 'social', 'content']:
            return False
        return True

    def clean_hero_product(hp):
        """Strip variant parentheticals from hero_product."""
        if not hp:
            return hp
        return re.sub(r'\s*\([^)]*\)', '', hp).strip()

    def get_short_ref(product_name, template_key):
        """Get short reference for product based on template key."""
        p_lower = product_name.lower()
        words = product_name.split()

        # Category-specific short refs
        short_refs_by_key = {
            'spf': 'the SPF',
            'serum': 'the serum',
            'patches': 'the patches',
            'supplement': 'them',
            'fragrance': 'it',
            'activewear': 'the gear',
            'food': 'it',
            'skincare': 'it',
            'makeup': 'it',
            'haircare': 'the product',
            'fashion': 'the piece',
            'home': 'it',
            'pet': 'the product',
            'fitness': 'the gear',
            'jewelry': 'the piece',
            'luxury': 'the piece',
            'lifestyle': 'it',
        }

        # Product-type specific overrides
        type_refs = {
            'serum': 'the serum', 'lotion': 'the lotion', 'cream': 'the cream',
            'oil': 'the oil', 'balm': 'the balm', 'mask': 'the mask',
            'cleanser': 'the cleanser', 'toner': 'the toner', 'sunscreen': 'the SPF',
            'spf': 'the SPF', 'moisturizer': 'the moisturizer',
            'patches': 'the patches', 'pads': 'the pads',
            'candle': 'it', 'fragrance': 'it', 'perfume': 'it', 'mist': 'it',
            'supplement': 'them', 'vitamins': 'them', 'gummies': 'them',
            'leggings': 'the leggings', 'shorts': 'the shorts',
            'shampoo': 'the shampoo', 'conditioner': 'the conditioner',
            # Fashion
            'dress': 'the dress', 'jacket': 'the jacket', 'coat': 'the coat',
            'bag': 'the bag', 'shoes': 'the shoes',
            # Home
            'pillow': 'the pillow', 'blanket': 'the blanket', 'lamp': 'the lamp',
            # Jewelry
            'necklace': 'the necklace', 'bracelet': 'the bracelet', 'ring': 'the ring',
            'earrings': 'the earrings', 'watch': 'the watch',
        }

        for ptype, short in type_refs.items():
            if ptype in p_lower:
                return short

        # Use category default
        if template_key in short_refs_by_key:
            return short_refs_by_key[template_key]

        # Fallback: first distinctive word
        if words and words[0].lower() not in ['the', 'a', 'an', 'your', 'our']:
            return f"the {words[0]}"
        return 'it'

    # ===== CREATOR DATA =====
    creator_name = ''
    first_name = creator.get('first_name', '').strip()
    if first_name and is_valid_first_name(first_name):
        creator_name = first_name.capitalize()
    else:
        display = creator.get('display_name', '') or ''
        if ' ' in display:
            first_word = display.split()[0].strip()
            if is_valid_first_name(first_word):
                creator_name = first_word.capitalize()

    followers = (
        creator.get('creator_followers') or
        creator.get('media_kit_followers') or
        creator.get('followers_count') or
        0
    )

    if followers >= 1_000_000:
        followers_str = f"{followers / 1_000_000:.1f}M"
    elif followers >= 1_000:
        followers_str = f"{followers / 1_000:.1f}K"
    else:
        followers_str = str(followers) if followers else 'growing'

    engagement_rate_raw = creator.get('engagement_rate') or 5
    engagement_rate = round(float(engagement_rate_raw), 1)

    # Niche - handle various formats (string, JSON string, array)
    creator_niches_raw = creator.get('creator_niches') or creator.get('niche')
    if isinstance(creator_niches_raw, str):
        try:
            parsed = json.loads(creator_niches_raw)
            if isinstance(parsed, list):
                # Clean each niche of any remaining quotes/brackets
                creator_niches = [str(n).strip('"\'[] ') for n in parsed if n]
            else:
                creator_niches = [str(parsed).strip('"\'[] ')]
        except:
            # Plain string - clean and use as single niche
            creator_niches = [creator_niches_raw.strip('"\'[] ')]
    elif isinstance(creator_niches_raw, list):
        # Clean each niche of any remaining quotes/brackets
        creator_niches = [str(n).strip('"\'[] ') for n in creator_niches_raw if n]
    else:
        creator_niches = []

    # Filter out empty values
    creator_niches = [n for n in creator_niches if n and n.lower() not in ['null', 'none', '']]

    brand_category = (brand.get('category') or '').lower()
    niche = None
    related_niches = {
        'fitness': ['athleisure', 'activewear', 'sports'],
        'wellness': ['skincare', 'supplements', 'self-care'],
        'beauty': ['skincare', 'makeup', 'haircare'],
        'skincare': ['beauty', 'wellness'],
        'fashion': ['lifestyle', 'accessories'],
        'tech': ['gaming', 'gadgets', 'electronics'],
        'gadgets': ['tech', 'gaming', 'electronics'],
        'gaming': ['tech', 'entertainment'],
    }

    for n in creator_niches:
        # Normalize compound niches for matching
        normalized = normalize_niche(n)
        for niche_part in normalized:
            if niche_part == brand_category:
                niche = n
                break
        if niche:
            break
    if not niche:
        for n in creator_niches:
            # Normalize compound niches for matching
            normalized = normalize_niche(n)
            for niche_part in normalized:
                if niche_part in related_niches.get(brand_category, []):
                    niche = n
                    break
            if niche:
                break
    if not niche and creator_niches:
        niche = creator_niches[0]
    if not niche:
        niche = brand_category or 'content'

    # Clean niche value - remove JSON artifacts like brackets and quotes
    if niche:
        niche = str(niche).strip()
        # Remove JSON array formatting if present
        if niche.startswith('[') and niche.endswith(']'):
            try:
                parsed = json.loads(niche)
                if isinstance(parsed, list) and len(parsed) > 0:
                    niche = parsed[0]
            except:
                niche = niche.strip('[]"\'').split(',')[0].strip().strip('"\'')
        # Clean any remaining quotes
        niche = niche.strip('"\'[]')

    # Platform
    social_links_raw = creator.get('social_links') or []
    if isinstance(social_links_raw, str):
        try:
            social_links_raw = json.loads(social_links_raw)
        except:
            social_links_raw = []

    platform = 'Instagram'
    for link in social_links_raw:
        if isinstance(link, dict):
            plat = link.get('platform', '').lower()
            if plat == 'tiktok':
                platform = 'TikTok'
                break
            elif plat == 'youtube':
                platform = 'YouTube'

    primary_format = creator.get('primary_format') or ('TikTok video' if platform == 'TikTok' else 'Instagram Reel' if platform == 'Instagram' else 'video')

    # Audience description
    audience_description = creator.get('audience_description')
    if not audience_description:
        niche_audiences = {
            'beauty': 'beauty enthusiasts who trust creator recommendations over ads',
            'skincare': 'skincare followers actively seeking product recommendations',
            'fashion': 'style-conscious followers who shop based on what I wear',
            'fitness': 'fitness followers who buy gear based on creator content',
            'wellness': 'wellness seekers looking for trusted product recommendations',
            'food': 'foodies who try products I feature',
            'lifestyle': f'{niche} followers who engage with product content',
        }
        audience_description = niche_audiences.get(niche.lower() if niche else '', f'{niche} enthusiasts aged 20-35')

    # ===== BRAND DATA =====
    brand_name = brand.get('brand_name', 'the brand')
    hero_product_raw = brand.get('hero_product')
    category = (brand.get('category') or '').lower()

    hero_product = clean_hero_product(hero_product_raw) if hero_product_raw else None
    if not hero_product:
        hero_product = f"{brand_name} products"

    has_specific_hero = bool(brand.get('hero_product'))

    # Grammar: plural detection
    product_lower = hero_product.lower()
    is_plural = (
        product_lower.endswith('patches') or
        product_lower.endswith('pads') or
        product_lower.endswith('gummies') or
        product_lower.endswith('capsules') or
        product_lower.endswith('vitamins') or
        product_lower.endswith('drops') or
        ' and ' in product_lower
    )
    verb_is = "are" if is_plural else "is"
    verb_has = "have" if is_plural else "has"

    # ===== GET TEMPLATE KEY =====
    template_key = get_template_key(category, hero_product if has_specific_hero else None)

    # Get short reference
    short_ref = get_short_ref(hero_product, template_key) if has_specific_hero else 'your products'

    # Media kit with tracking token
    kit_published = creator.get('kit_published', False)
    username = creator.get('username', creator.get('id', 'creator'))
    if kit_published:
        # Get IDs from creator/brand dicts for token generation
        c_id = creator.get('id') or creator.get('creator_id')
        b_id = brand.get('id') or brand.get('brand_id')
        if c_id and b_id:
            kit_token = generate_kit_token(c_id, b_id)
            media_kit_url = f"https://newcollab.co/kit/{username}?ref={kit_token}"
        else:
            media_kit_url = f"https://newcollab.co/kit/{username}"
    else:
        media_kit_url = None

    # ===== COMPUTE CREATOR TIER =====
    collab_count = creator.get('collab_count', 0) or 0
    tier_num, tier_label = compute_creator_tier(followers, collab_count)
    last_collab_brand = creator.get('last_collab_brand_name')

    # Debug logging
    print(f"[Pitch Generator] Creator tier: {tier_num} ({tier_label}), followers: {followers}, collabs: {collab_count}")

    # ===== GENERATE PITCH USING TIERED TEMPLATES =====

    # Prepare creator data dict for template functions
    creator_data = {
        'followers_str': followers_str,
        'engagement_rate': engagement_rate,
        'platform': platform,
        'niche': niche,
        'audience_description': audience_description,
    }

    # LINE 1: Tier-specific brand hook
    if tier_num == 1:
        # Tier 1 (Starter): Focus on content skills, not audience
        tier_templates = TIER1_LINE1_TEMPLATES.get(template_key, TIER1_LINE1_TEMPLATES['default'])
        brand_hook = random.choice(tier_templates)
    elif tier_num == 2:
        # Tier 2 (Growing): Reference engagement and past work
        tier_templates = TIER2_LINE1_TEMPLATES.get(template_key, TIER2_LINE1_TEMPLATES['default'])
        brand_hook = random.choice(tier_templates).format(
            followers_str=followers_str,
            engagement_rate=engagement_rate,
            product=hero_product,
            verb_is=verb_is,
            verb_has=verb_has
        )
    else:
        # Tier 3 (Established): Full audience claims allowed
        tier_templates = TIER3_LINE1_TEMPLATES.get(template_key, TIER3_LINE1_TEMPLATES['default'])
        brand_hook = random.choice(tier_templates).format(
            followers_str=followers_str,
            product=hero_product,
            verb_is=verb_is,
            verb_has=verb_has
        )

    # LINE 2: Tier-specific creator proof
    creator_proof = get_tier_proof_line(
        tier_num,
        creator_data,
        past_collab_brand=last_collab_brand if tier_num >= 2 else None
    )

    # LINE 3: Content idea (same for all tiers - focuses on what they'll create)
    line3_templates = LINE3_TEMPLATES.get(template_key, LINE3_TEMPLATES['default'])
    content_idea = random.choice(line3_templates).format(
        short=short_ref,
        product=hero_product
    )

    # LINE 4: Tier-specific ask
    if tier_num == 1:
        ask = TIER1_ASK
    elif tier_num == 2:
        ask = TIER2_ASK
    else:
        ask = TIER3_ASK

    # Build body
    body = f"""Hi,

{brand_hook}

{creator_proof}

{content_idea}

{ask}"""

    if media_kit_url:
        body += f"\n\nPlease find my portfolio here: {media_kit_url}"

    if creator_name:
        body += f"\n\n{creator_name}"

    # ===== VALIDATE PITCH CONTENT =====
    is_valid, issues = validate_pitch_content(body, tier_num)
    if not is_valid:
        print(f"[Pitch Generator] Validation issues: {issues}")
        # For Tier 1, if validation fails, regenerate with safer template
        if tier_num == 1 and any('Tier 1 cannot claim' in issue for issue in issues):
            # Use the safest possible template
            brand_hook = f"I create {niche.lower()} content that brands use for UGC and social."
            creator_proof = f"I make {platform} content with clean visuals and authentic product integration. I'm building my following and focused on creating content brands can actually use."
            body = f"""Hi,

{brand_hook}

{creator_proof}

{content_idea}

{ask}"""
            if media_kit_url:
                body += f"\n\nPlease find my portfolio here: {media_kit_url}"
            if creator_name:
                body += f"\n\n{creator_name}"

    # ===== SUBJECT LINE (Tier-aware) =====
    if tier_num == 1:
        # Tier 1: Don't lead with follower count
        subject_templates = [
            f"{niche.title()} content creator x {brand_name}",
            f"UGC content idea for {brand_name}",
            f"Content creator for your {niche.lower()} products",
        ]
    elif tier_num == 2:
        # Tier 2: Can mention platform and engagement
        if has_specific_hero:
            subject_templates = [
                f"{hero_product} | {niche.lower()} creator, {followers_str} on {platform}",
                f"Content idea for {hero_product}",
            ]
        else:
            subject_templates = [
                f"{niche.title()} creator ({followers_str}) x {brand_name}",
                f"Collab idea for {brand_name}",
            ]
    else:
        # Tier 3: Full stats in subject
        if has_specific_hero:
            subject_templates = [
                f"{hero_product} for my {followers_str} {platform} {niche.lower()} audience",
                f"{hero_product} | {niche.lower()} creator, {followers_str} {platform}",
                f"Your {hero_product} for my {niche.lower()} content",
            ]
        else:
            subject_templates = [
                f"{niche.title()} creator ({followers_str} {platform}) x {brand_name}",
                f"Content idea for {brand_name} | {followers_str} {niche.lower()} followers",
            ]
    subject = random.choice(subject_templates)

    return {
        'subject': subject,
        'body': body,
        'creator_stats': {
            'followers': followers_str if followers else None,
            'niche': niche,
            'platform': platform
        },
        'kit_published': kit_published,
        'media_kit_url': media_kit_url,
        'tier': tier_num,
        'tier_label': tier_label
    }


def generate_followup_pitch(brand, creator):
    """Generate a concise follow-up email for brands already pitched"""
    import random

    # Extract creator data
    creator_name = creator.get('first_name', '').strip() or 'Creator'

    # Use For You followers if set, else media_kit total, else signup followers
    followers = (
        creator.get('creator_followers') or
        creator.get('media_kit_followers') or
        creator.get('followers_count') or
        0
    )

    # Determine primary platform
    social_links_raw = creator.get('social_links') or []
    if isinstance(social_links_raw, str):
        try:
            social_links_raw = json.loads(social_links_raw)
        except:
            social_links_raw = []

    platform = 'Instagram'
    for link in social_links_raw:
        if isinstance(link, dict):
            plat = link.get('platform', '').lower()
            if plat == 'tiktok':
                platform = 'TikTok'
                break
            elif plat == 'youtube':
                platform = 'YouTube'

    # Format followers
    if followers >= 1000000:
        followers_str = f"{followers / 1000000:.1f}M"
    elif followers >= 1000:
        followers_str = f"{followers / 1000:.1f}K"
    else:
        followers_str = str(followers) if followers else 'growing'

    brand_name = brand.get('brand_name', 'the brand')
    hero_product = brand.get('hero_product') or brand.get('category') or 'products'

    # Concise subject
    subject = f"Quick follow-up re: {brand_name}"

    # Media kit link with tracking token - only include if kit is published
    kit_published = creator.get('kit_published', False)
    username = creator.get('username', creator.get('id', 'creator'))
    if kit_published:
        creator_id = creator.get('id') or creator.get('creator_id')
        brand_id = brand.get('id') or brand.get('brand_id')
        if creator_id and brand_id:
            kit_token = generate_kit_token(creator_id, brand_id)
            media_kit_url = f"https://newcollab.co/kit/{username}?ref={kit_token}"
        else:
            media_kit_url = f"https://newcollab.co/kit/{username}"
    else:
        media_kit_url = None

    # Concise follow-up body (under 50 words)
    body = f"""Hi,

Just following up on my pitch from last week. Still interested in featuring your {hero_product}.

{followers_str} {platform} followers ready to see it."""

    # Add media kit link if published
    if media_kit_url:
        body += f"\n\nPlease find my portfolio here: {media_kit_url}"

    body += f"""

Let me know if you're open to sending product.

{creator_name}"""

    return {
        'subject': subject,
        'body': body,
        'creator_stats': {
            'followers': followers_str if followers else None,
            'platform': platform
        },
        'is_followup': True,
        'kit_published': kit_published,
        'media_kit_url': media_kit_url
    }


# ============================================
# NEW PIPELINE ENDPOINTS (PR Pipeline Feature)
# ============================================

def get_subscription_status(creator_id):
    """Helper to get creator subscription status"""
    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    cursor.execute('SELECT subscription_tier FROM creators WHERE id = %s', (creator_id,))
    result = cursor.fetchone()
    cursor.close()
    conn.close()
    return result.get('subscription_tier', 'free') if result else 'free'


# ============================================
# CREDIT UNLOCK SYSTEM
# ============================================

def attempt_unlock(creator_id, brand_id, conn=None):
    """
    Core unlock gate function. Call this before generating a pitch.

    Returns:
        - {"status": "already_unlocked", "credits_used": 0} - brand was previously unlocked
        - {"status": "unlocked", "credits_used": 0|1, "remaining": N} - brand now unlocked
        - {"status": "paywall", "credits_used": 0, "reset_at": timestamp} - no credits left
    """
    close_conn = False
    if not conn:
        conn = get_db_connection()
        close_conn = True

    cursor = conn.cursor(cursor_factory=RealDictCursor)

    try:
        print(f"[attempt_unlock] Checking creator {creator_id}, brand {brand_id}")

        # Check if already unlocked (free pass - no credit charge)
        cursor.execute('''
            SELECT id FROM brand_unlocks
            WHERE creator_id = %s AND brand_id = %s
        ''', (creator_id, brand_id))

        if cursor.fetchone():
            print(f"[attempt_unlock] Brand {brand_id} already unlocked for creator {creator_id}")
            return {"status": "already_unlocked", "credits_used": 0}

        # Get creator's unlock status
        cursor.execute('''
            SELECT unlocks_tier, unlocks_remaining, unlocks_reset_at, subscription_tier
            FROM creators WHERE id = %s
        ''', (creator_id,))
        creator = cursor.fetchone()

        if not creator:
            return {"status": "error", "error": "Creator not found"}

        # Pro/Elite users get unlimited unlocks
        if creator.get('unlocks_tier') == 'pro' or creator.get('subscription_tier') in ('pro', 'elite'):
            # Create unlock record without charging
            cursor.execute('''
                INSERT INTO brand_unlocks (creator_id, brand_id, unlocked_at)
                VALUES (%s, %s, NOW())
                ON CONFLICT (creator_id, brand_id) DO NOTHING
            ''', (creator_id, brand_id))
            conn.commit()
            return {"status": "unlocked", "credits_used": 0, "remaining": None, "tier": "pro"}

        # Free tier: check if reset needed
        unlocks_remaining = creator.get('unlocks_remaining') or 0
        unlocks_reset_at = creator.get('unlocks_reset_at')

        if unlocks_reset_at and datetime.now() > unlocks_reset_at:
            # Reset the monthly credits
            unlocks_remaining = 5
            cursor.execute('''
                UPDATE creators
                SET unlocks_remaining = 5,
                    unlocks_reset_at = NOW() + INTERVAL '30 days'
                WHERE id = %s
            ''', (creator_id,))
            cursor.execute('SELECT unlocks_reset_at FROM creators WHERE id = %s', (creator_id,))
            unlocks_reset_at = cursor.fetchone().get('unlocks_reset_at')

        # Check if user has credits
        if unlocks_remaining <= 0:
            print(f"[attempt_unlock] PAYWALL triggered for creator {creator_id}: unlocks_remaining={creator.get('unlocks_remaining')}, tier={creator.get('unlocks_tier')}")
            return {
                "status": "paywall",
                "credits_used": 0,
                "remaining": 0,
                "reset_at": unlocks_reset_at.isoformat() if unlocks_reset_at else None,
                "debug_db_value": creator.get('unlocks_remaining')  # For debugging
            }

        # Deduct credit and create unlock
        cursor.execute('''
            UPDATE creators
            SET unlocks_remaining = unlocks_remaining - 1
            WHERE id = %s
            RETURNING unlocks_remaining
        ''', (creator_id,))
        new_remaining = cursor.fetchone().get('unlocks_remaining')

        cursor.execute('''
            INSERT INTO brand_unlocks (creator_id, brand_id, unlocked_at)
            VALUES (%s, %s, NOW())
            ON CONFLICT (creator_id, brand_id) DO NOTHING
        ''', (creator_id, brand_id))

        conn.commit()

        return {
            "status": "unlocked",
            "credits_used": 1,
            "remaining": new_remaining,
            "tier": "free"
        }

    except Exception as e:
        print(f"Error in attempt_unlock: {e}")
        conn.rollback()
        return {"status": "error", "error": str(e)}
    finally:
        cursor.close()
        if close_conn:
            conn.close()


def can_unlock(creator_id, brand_id, conn=None):
    """
    Check if a creator CAN unlock a brand WITHOUT actually deducting credits.
    Use this for pre-checks before showing pitch UI. Call attempt_unlock() on actual send.

    Returns:
        - {"can_unlock": True, "already_unlocked": True} - brand already unlocked, free to use
        - {"can_unlock": True, "already_unlocked": False, "remaining": N} - user has credits
        - {"can_unlock": False, "paywall": True, "reset_at": timestamp} - no credits left
    """
    close_conn = False
    if not conn:
        conn = get_db_connection()
        close_conn = True

    cursor = conn.cursor(cursor_factory=RealDictCursor)

    try:
        # Check if already unlocked
        cursor.execute('''
            SELECT id FROM brand_unlocks
            WHERE creator_id = %s AND brand_id = %s
        ''', (creator_id, brand_id))

        if cursor.fetchone():
            return {"can_unlock": True, "already_unlocked": True}

        # Get creator's unlock status
        cursor.execute('''
            SELECT unlocks_tier, unlocks_remaining, unlocks_reset_at, subscription_tier
            FROM creators WHERE id = %s
        ''', (creator_id,))
        creator = cursor.fetchone()

        if not creator:
            return {"can_unlock": False, "error": "Creator not found"}

        # Pro/Elite users always can unlock
        if creator.get('unlocks_tier') == 'pro' or creator.get('subscription_tier') in ('pro', 'elite'):
            return {"can_unlock": True, "already_unlocked": False, "remaining": None, "tier": "pro"}

        # Free tier: check credits
        unlocks_remaining = creator.get('unlocks_remaining') or 0
        unlocks_reset_at = creator.get('unlocks_reset_at')

        # Check if reset needed (but don't actually reset here)
        if unlocks_reset_at and datetime.now() > unlocks_reset_at:
            unlocks_remaining = 5  # Would be reset on next attempt_unlock

        if unlocks_remaining <= 0:
            return {
                "can_unlock": False,
                "paywall": True,
                "remaining": 0,
                "reset_at": unlocks_reset_at.isoformat() if unlocks_reset_at else None
            }

        return {
            "can_unlock": True,
            "already_unlocked": False,
            "remaining": unlocks_remaining,
            "tier": "free"
        }

    except Exception as e:
        print(f"Error in can_unlock: {e}")
        return {"can_unlock": False, "error": str(e)}
    finally:
        cursor.close()
        if close_conn:
            conn.close()


def check_brand_unlock_status(creator_id, brand_id, conn=None):
    """Check if a brand is already unlocked for a creator"""
    close_conn = False
    if not conn:
        conn = get_db_connection()
        close_conn = True

    cursor = conn.cursor(cursor_factory=RealDictCursor)
    cursor.execute('''
        SELECT id, unlocked_at FROM brand_unlocks
        WHERE creator_id = %s AND brand_id = %s
    ''', (creator_id, brand_id))
    result = cursor.fetchone()
    cursor.close()

    if close_conn:
        conn.close()

    return result is not None


def get_creator_unlock_balance(creator_id, conn=None):
    """Get creator's current unlock balance and status"""
    close_conn = False
    if not conn:
        conn = get_db_connection()
        close_conn = True

    cursor = conn.cursor(cursor_factory=RealDictCursor)
    cursor.execute('''
        SELECT unlocks_tier, unlocks_remaining, unlocks_reset_at, subscription_tier
        FROM creators WHERE id = %s
    ''', (creator_id,))
    creator = cursor.fetchone()
    cursor.close()

    if close_conn:
        conn.close()

    if not creator:
        return None

    # Pro/Elite = unlimited
    if creator.get('unlocks_tier') == 'pro' or creator.get('subscription_tier') in ('pro', 'elite'):
        return {
            "tier": "pro",
            "remaining": None,  # unlimited
            "reset_at": None,
            "is_unlimited": True
        }

    # Check if reset needed
    unlocks_remaining = creator.get('unlocks_remaining') or 0
    unlocks_reset_at = creator.get('unlocks_reset_at')

    if unlocks_reset_at and datetime.now() > unlocks_reset_at:
        unlocks_remaining = 5  # Would reset on next attempt_unlock

    return {
        "tier": "free",
        "remaining": unlocks_remaining,
        "reset_at": unlocks_reset_at.isoformat() if unlocks_reset_at else None,
        "is_unlimited": False
    }


@pr_crm.route('/unlocks/balance', methods=['GET'])
def get_unlock_balance():
    """API endpoint to get creator's current unlock balance"""
    creator_id = get_creator_id_from_session()
    if not creator_id:
        return jsonify({'success': False, 'error': 'Not authenticated'}), 401

    balance = get_creator_unlock_balance(creator_id)
    if not balance:
        return jsonify({'success': False, 'error': 'Creator not found'}), 404

    return jsonify({'success': True, **balance})


@pr_crm.route('/unlocks/debug', methods=['GET'])
def debug_unlock_status():
    """DEBUG: Show raw DB values for current user's unlock state"""
    creator_id = get_creator_id_from_session()
    if not creator_id:
        return jsonify({'success': False, 'error': 'Not authenticated'}), 401

    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=RealDictCursor)

    # Get raw creator data
    cursor.execute('''
        SELECT id, unlocks_tier, unlocks_remaining, unlocks_reset_at, subscription_tier
        FROM creators WHERE id = %s
    ''', (creator_id,))
    creator = cursor.fetchone()

    # Count brand_unlocks
    cursor.execute('SELECT COUNT(*) as count FROM brand_unlocks WHERE creator_id = %s', (creator_id,))
    unlock_count = cursor.fetchone()['count']

    cursor.close()
    conn.close()

    return jsonify({
        'success': True,
        'creator_id': creator_id,
        'raw_db': {
            'unlocks_tier': creator.get('unlocks_tier') if creator else None,
            'unlocks_remaining': creator.get('unlocks_remaining') if creator else None,
            'unlocks_reset_at': creator.get('unlocks_reset_at').isoformat() if creator and creator.get('unlocks_reset_at') else None,
            'subscription_tier': creator.get('subscription_tier') if creator else None,
        },
        'brand_unlocks_count': unlock_count
    })


@pr_crm.route('/unlocks/check/<int:brand_id>', methods=['GET'])
def check_unlock_status(brand_id):
    """API endpoint to check if a specific brand is unlocked"""
    creator_id = get_creator_id_from_session()
    if not creator_id:
        return jsonify({'success': False, 'error': 'Not authenticated'}), 401

    is_unlocked = check_brand_unlock_status(creator_id, brand_id)
    balance = get_creator_unlock_balance(creator_id)

    return jsonify({
        'success': True,
        'is_unlocked': is_unlocked,
        'balance': balance
    })


@pr_crm.route('/unlocks/batch-check', methods=['POST'])
def batch_check_unlocks():
    """Check unlock status for multiple brands at once (for brand cards)"""
    creator_id = get_creator_id_from_session()
    if not creator_id:
        return jsonify({'success': False, 'error': 'Not authenticated'}), 401

    data = request.get_json() or {}
    brand_ids = data.get('brand_ids', [])

    if not brand_ids:
        return jsonify({'success': True, 'unlocked': [], 'balance': get_creator_unlock_balance(creator_id)})

    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=RealDictCursor)

    # Get all unlocked brand IDs for this creator
    cursor.execute('''
        SELECT brand_id FROM brand_unlocks
        WHERE creator_id = %s AND brand_id = ANY(%s)
    ''', (creator_id, brand_ids))

    unlocked_ids = [row['brand_id'] for row in cursor.fetchall()]
    cursor.close()
    conn.close()

    balance = get_creator_unlock_balance(creator_id)

    return jsonify({
        'success': True,
        'unlocked': unlocked_ids,
        'balance': balance
    })


@pr_crm.route('/pipeline/full', methods=['GET'])
def get_pipeline_full():
    """
    Returns all pipeline items for the authenticated user, grouped by stage with summary stats.
    This is the enhanced pipeline endpoint for the new PR Pipeline feature.
    """
    creator_id = get_creator_id_from_session()
    if not creator_id:
        return jsonify({'success': False, 'error': 'Not authenticated'}), 401

    try:
        conn = get_db_connection()
        cursor = conn.cursor(cursor_factory=RealDictCursor)

        # Get all pipeline items with brand info and days since pitched
        cursor.execute("""
            SELECT
                cp.id, cp.stage AS pipeline_stage, cp.send_confirmed,
                cp.pitched_at, cp.followup_count, cp.followup_sent_at,
                cp.replied_at, cp.reply_type,
                cp.package_confirmed_at, cp.package_value,
                cp.expected_delivery, cp.received_at, cp.notes,
                cp.created_at AS saved_at,
                cp.email_opened, cp.email_opened_at, cp.email_open_count,
                pb.id AS brand_id, pb.brand_name, pb.category,
                pb.logo_url, pb.website AS domain, pb.response_rate,
                pb.contact_email AS pr_email, pb.instagram_handle, pb.has_application_form,
                pb.application_form_url, pb.description,
                -- days since pitched (for nudge logic in frontend)
                CASE
                    WHEN cp.pitched_at IS NOT NULL
                    THEN EXTRACT(DAY FROM NOW() - cp.pitched_at)::INT
                    ELSE NULL
                END AS days_since_pitched,
                -- days since follow-up was sent (for follow-up overdue logic)
                CASE
                    WHEN cp.followup_sent_at IS NOT NULL
                    THEN EXTRACT(DAY FROM NOW() - cp.followup_sent_at)::INT
                    ELSE NULL
                END AS days_since_followup
            FROM creator_pipeline cp
            JOIN pr_brands pb ON pb.id = cp.brand_id
            WHERE cp.creator_id = %s
              AND cp.stage != 'archived'
            ORDER BY
                CASE cp.stage
                    WHEN 'replied'   THEN 1
                    WHEN 'followup'  THEN 2
                    WHEN 'waiting'   THEN 3
                    WHEN 'pitched'   THEN 3
                    WHEN 'won'       THEN 4
                    WHEN 'success'   THEN 4
                    WHEN 'saved'     THEN 5
                    WHEN 'received'  THEN 6
                END,
                COALESCE(cp.pitched_at, cp.created_at) DESC
        """, (creator_id,))

        rows = cursor.fetchall()

        # Summary stats
        cursor.execute("""
            SELECT
                COUNT(*) FILTER (WHERE stage IN ('waiting', 'followup', 'pitched')) AS waiting_count,
                COUNT(*) FILTER (WHERE stage IN ('won', 'received', 'success')) AS wins_count,
                COUNT(*) FILTER (WHERE send_confirmed = TRUE OR pitched_at IS NOT NULL) AS total_contacted,
                COALESCE(SUM(package_value) FILTER (WHERE stage IN ('won', 'received', 'success')), 0) AS pr_value_earned
            FROM creator_pipeline
            WHERE creator_id = %s AND stage != 'archived'
        """, (creator_id,))
        stats = cursor.fetchone()

        cursor.close()
        conn.close()

        return jsonify({
            'success': True,
            'items': [dict(r) for r in rows],
            'stats': dict(stats) if stats else {}
        })

    except Exception as e:
        import traceback
        print(f"Error in get_pipeline_full: {str(e)}")
        traceback.print_exc()
        return jsonify({'success': False, 'error': str(e)}), 500


@pr_crm.route('/pipeline/<int:pipeline_id>/update', methods=['PATCH'])
def update_pipeline_item(pipeline_id):
    """
    Single endpoint to advance or update any pipeline field.
    Frontend sends only the fields it wants to change.
    """
    creator_id = get_creator_id_from_session()
    if not creator_id:
        return jsonify({'success': False, 'error': 'Not authenticated'}), 401

    try:
        data = request.get_json()

        conn = get_db_connection()
        cursor = conn.cursor(cursor_factory=RealDictCursor)

        # Security: verify ownership
        cursor.execute(
            "SELECT id FROM creator_pipeline WHERE id = %s AND creator_id = %s",
            (pipeline_id, creator_id)
        )
        row = cursor.fetchone()
        if not row:
            cursor.close()
            conn.close()
            return jsonify({'success': False, 'error': 'Not found'}), 404

        allowed_fields = {
            'stage', 'send_confirmed', 'pitched_at',
            'followup_count', 'followup_sent_at', 'replied_at',
            'reply_type', 'package_confirmed_at', 'package_value',
            'expected_delivery', 'received_at', 'notes'
        }
        updates = {k: v for k, v in data.items() if k in allowed_fields}
        if not updates:
            cursor.close()
            conn.close()
            return jsonify({'success': False, 'error': 'No valid fields'}), 400

        # Build SET clause with proper SQL handling
        set_parts = []
        params = []

        for key, value in updates.items():
            if value == 'NOW()':
                set_parts.append(f"{key} = NOW()")
            else:
                set_parts.append(f"{key} = %s")
                params.append(value)

        set_parts.append("updated_at = NOW()")
        set_clause = ', '.join(set_parts)
        params.extend([creator_id, pipeline_id])

        cursor.execute(
            f"UPDATE creator_pipeline SET {set_clause} WHERE creator_id = %s AND id = %s",
            params
        )

        # If stage -> 'received', update brand response_rate
        if updates.get('stage') == 'received':
            _update_brand_response_rate(cursor, conn, pipeline_id)

        conn.commit()
        cursor.close()
        conn.close()

        return jsonify({'success': True})

    except Exception as e:
        import traceback
        print(f"Error in update_pipeline_item: {str(e)}")
        traceback.print_exc()
        return jsonify({'success': False, 'error': str(e)}), 500


@pr_crm.route('/pipeline/<int:pipeline_id>/confirm-send', methods=['POST'])
def confirm_send(pipeline_id):
    """
    Called when creator confirms they sent the email from their native email app.
    Optionally sends confirmation email to creator.
    """
    creator_id = get_creator_id_from_session()
    if not creator_id:
        return jsonify({'success': False, 'error': 'Not authenticated'}), 401

    data = request.get_json() or {}
    send_confirmation_email = data.get('send_confirmation_email', False)
    contact_method = data.get('contact_method', 'email')

    try:
        conn = get_db_connection()
        cursor = conn.cursor(cursor_factory=RealDictCursor)

        # Get pipeline item with brand name
        cursor.execute("""
            SELECT cp.id, cp.brand_id, cp.pitched_at, cp.send_confirmed, pb.brand_name
            FROM creator_pipeline cp
            LEFT JOIN pr_brands pb ON cp.brand_id = pb.id
            WHERE cp.id = %s AND cp.creator_id = %s
        """, (pipeline_id, creator_id))
        pipeline_item = cursor.fetchone()

        if not pipeline_item:
            cursor.close()
            conn.close()
            return jsonify({'success': False, 'error': 'Not found'}), 404

        cursor.execute("""
            UPDATE creator_pipeline
            SET stage = 'waiting',
                send_confirmed = TRUE,
                pitched_at = NOW(),
                updated_at = NOW()
            WHERE id = %s AND creator_id = %s
        """, (pipeline_id, creator_id))

        # ========== CREDIT UNLOCK DEDUCTION ==========
        # Only deduct credit and track if this is a NEW send (not a re-confirm)
        if not pipeline_item.get('pitched_at') and not pipeline_item.get('send_confirmed'):
            brand_id = pipeline_item.get('brand_id')
            if brand_id:
                # Actually deduct the credit now (on confirmed send)
                unlock_result = attempt_unlock(creator_id, brand_id, conn)
                print(f"[confirm_send] Unlock result for creator {creator_id}, brand {brand_id}: {unlock_result}")

                # If paywall hit (edge case - user ran out while composing), return error
                if unlock_result.get('status') == 'paywall':
                    cursor.close()
                    conn.close()
                    return jsonify({
                        'success': False,
                        'paywall': True,
                        'remaining': 0,
                        'reset_at': unlock_result.get('reset_at'),
                        'message': "You've used all 5 contacts this month."
                    }), 402

            # Legacy: also increment old pitch counter for backwards compatibility
            cursor.execute("""
                UPDATE creators
                SET pitches_sent_this_week = COALESCE(pitches_sent_this_week, 0) + 1,
                    last_pitch_at = NOW()
                WHERE id = %s
                RETURNING pitches_sent_this_week, subscription_tier
            """, (creator_id,))
            result = cursor.fetchone()

            # Schedule quota email when user hits 5th contact (changed from 3)
            if result and result.get('pitches_sent_this_week') == 5 and result.get('subscription_tier', 'free') == 'free':
                cursor.execute("""
                    UPDATE creators
                    SET quota_email_send_at = NOW() + INTERVAL '7 days'
                    WHERE id = %s AND quota_email_send_at IS NULL
                """, (creator_id,))

        # Get creator info for confirmation email
        if send_confirmation_email:
            cursor.execute("""
                SELECT c.id, u.first_name, u.last_name, u.email
                FROM creators c
                JOIN users u ON c.user_id = u.id
                WHERE c.id = %s
            """, (creator_id,))
            creator = cursor.fetchone()

            if creator and creator.get('email'):
                creator_full_name = f"{creator.get('first_name', '')} {creator.get('last_name', '')}".strip() or 'Creator'
                _send_pitch_confirmation_email(
                    creator['email'],
                    creator_full_name,
                    pipeline_item.get('brand_name', 'the brand')
                )

        conn.commit()
        cursor.close()
        conn.close()

        return jsonify({'success': True, 'stage': 'waiting', 'contact_method': contact_method})

    except Exception as e:
        import traceback
        print(f"Error in confirm_send: {str(e)}")
        traceback.print_exc()
        return jsonify({'success': False, 'error': str(e)}), 500


def _send_pitch_confirmation_email(to_email, creator_name, brand_name):
    """Send confirmation email when creator confirms they contacted a brand."""
    try:
        from email_cron_routes import send_template_email

        app_url = os.getenv('FRONTEND_URL', 'https://app.newcollab.co').rstrip('/')
        subject = f'✓ You contacted {brand_name}!'

        context = {
            'subject': subject,
            'preheader': f'Step 1 done! You just reached out to {brand_name}. Here\'s what to do next.',
            'message': f"""
                <p style="margin: 0 0 16px;">Hey {creator_name},</p>
                <p style="margin: 0 0 16px;">You just contacted <strong>{brand_name}</strong>. That's step 1 complete! 🎉</p>
                <p style="margin: 0 0 12px;">Here's what happens next:</p>
                <ul style="text-align: left; margin: 0 0 16px; padding-left: 20px; color: #1d1d1f;">
                    <li style="margin-bottom: 6px;">We'll remind you to follow up in 7 days if you haven't heard back</li>
                    <li style="margin-bottom: 6px;">Most brands respond within 1-2 weeks</li>
                    <li style="margin-bottom: 6px;">Track your pipeline and log any replies in your dashboard</li>
                </ul>
                <p style="margin: 0; color: #059669; font-weight: 600;">Keep the momentum going. The more brands you contact, the more packages you'll land! 📦</p>
            """,
            'action_url': f'{app_url}/creator/dashboard/pr-pipeline',
            'action_text': 'Track My Pipeline',
        }

        success, error = send_template_email(
            to_email=to_email,
            template_name='conversion_email.html',
            subject=subject,
            context=context
        )

        if not success:
            print(f"Failed to send pitch confirmation email: {error}")

    except Exception as e:
        print(f"Error sending pitch confirmation email: {str(e)}")


@pr_crm.route('/pipeline/<int:pipeline_id>/confirm-followup', methods=['POST'])
def confirm_followup(pipeline_id):
    """
    Called when creator confirms they sent a follow-up email.
    Updates followup_count and followup_sent_at, moves stage to 'followup'.
    """
    creator_id = get_creator_id_from_session()
    if not creator_id:
        return jsonify({'success': False, 'error': 'Not authenticated'}), 401

    try:
        conn = get_db_connection()
        cursor = conn.cursor(cursor_factory=RealDictCursor)

        # Get pipeline item
        cursor.execute("""
            SELECT cp.id, cp.followup_count, pb.brand_name
            FROM creator_pipeline cp
            LEFT JOIN pr_brands pb ON cp.brand_id = pb.id
            WHERE cp.id = %s AND cp.creator_id = %s
        """, (pipeline_id, creator_id))
        pipeline_item = cursor.fetchone()

        if not pipeline_item:
            cursor.close()
            conn.close()
            return jsonify({'success': False, 'error': 'Not found'}), 404

        current_count = pipeline_item.get('followup_count', 0) or 0

        cursor.execute("""
            UPDATE creator_pipeline
            SET stage = 'followup',
                followup_count = %s,
                followup_sent_at = NOW(),
                updated_at = NOW()
            WHERE id = %s AND creator_id = %s
        """, (current_count + 1, pipeline_id, creator_id))

        conn.commit()
        cursor.close()
        conn.close()

        return jsonify({
            'success': True,
            'stage': 'followup',
            'followup_count': current_count + 1
        })

    except Exception as e:
        import traceback
        print(f"Error in confirm_followup: {str(e)}")
        traceback.print_exc()
        return jsonify({'success': False, 'error': str(e)}), 500


@pr_crm.route('/pipeline/<int:pipeline_id>/log-reply', methods=['POST'])
def log_reply(pipeline_id):
    """
    Called when creator taps one of the 4 reply-type options.
    """
    creator_id = get_creator_id_from_session()
    if not creator_id:
        return jsonify({'success': False, 'error': 'Not authenticated'}), 401

    try:
        is_pro = get_subscription_status(creator_id) in ['pro', 'elite']
        data = request.get_json()
        reply_type = data.get('reply_type')

        if reply_type not in ('package_coming', 'need_info', 'not_fit', 'unsure'):
            return jsonify({'success': False, 'error': 'Invalid reply_type'}), 400

        new_stage = {
            'package_coming': 'won',
            'need_info': 'replied',
            'not_fit': 'archived',
            'unsure': 'replied',
        }[reply_type]

        conn = get_db_connection()
        cursor = conn.cursor(cursor_factory=RealDictCursor)

        # Verify ownership
        cursor.execute(
            "SELECT id FROM creator_pipeline WHERE id = %s AND creator_id = %s",
            (pipeline_id, creator_id)
        )
        if not cursor.fetchone():
            cursor.close()
            conn.close()
            return jsonify({'success': False, 'error': 'Not found'}), 404

        # Update with stage-specific timestamps
        if new_stage == 'won':
            cursor.execute("""
                UPDATE creator_pipeline
                SET stage = %s,
                    reply_type = %s,
                    replied_at = NOW(),
                    package_confirmed_at = NOW(),
                    updated_at = NOW()
                WHERE id = %s AND creator_id = %s
            """, (new_stage, reply_type, pipeline_id, creator_id))
        else:
            cursor.execute("""
                UPDATE creator_pipeline
                SET stage = %s,
                    reply_type = %s,
                    replied_at = NOW(),
                    updated_at = NOW()
                WHERE id = %s AND creator_id = %s
            """, (new_stage, reply_type, pipeline_id, creator_id))

        conn.commit()
        cursor.close()
        conn.close()

        return jsonify({
            'success': True,
            'stage': new_stage,
            'show_kit_prompt': reply_type == 'need_info',
            'show_value_prompt': reply_type == 'package_coming' and is_pro,
            'show_upgrade': reply_type == 'package_coming' and not is_pro,
        })

    except Exception as e:
        import traceback
        print(f"Error in log_reply: {str(e)}")
        traceback.print_exc()
        return jsonify({'success': False, 'error': str(e)}), 500


@pr_crm.route('/pipeline/bump', methods=['POST'])
def bump_profile():
    """
    Pro feature: Request a manual follow-up from Newcollab team.
    Adds to queue for admin to send personalized follow-up email to brand.
    Limited to 2 bumps per Pro user per month.
    """
    creator_id = get_creator_id_from_session()
    if not creator_id:
        return jsonify({'success': False, 'error': 'Not authenticated'}), 401

    # Verify Pro subscription
    subscription = get_subscription_status(creator_id)
    if subscription not in ['pro', 'elite']:
        return jsonify({'success': False, 'error': 'Pro subscription required'}), 403

    data = request.get_json() or {}
    pipeline_id = data.get('pipeline_id')
    brand_id = data.get('brand_id')

    if not pipeline_id or not brand_id:
        return jsonify({'success': False, 'error': 'Missing pipeline_id or brand_id'}), 400

    try:
        conn = get_db_connection()
        cursor = conn.cursor(cursor_factory=RealDictCursor)

        # Check bump limit (2 per month)
        cursor.execute("""
            SELECT COUNT(*) as bump_count
            FROM profile_bumps
            WHERE creator_id = %s
              AND created_at > DATE_TRUNC('month', CURRENT_DATE)
        """, (creator_id,))
        bump_count = cursor.fetchone()['bump_count']

        if bump_count >= 2:
            cursor.close()
            conn.close()
            return jsonify({'success': False, 'error': 'Monthly bump limit reached (2/month)'}), 400

        # Get creator and brand info for the bump queue
        cursor.execute("""
            SELECT c.id, c.username, c.niche, c.followers_count,
                   u.email as creator_email,
                   c.kit_slug
            FROM creators c
            JOIN users u ON u.id = c.user_id
            WHERE c.id = %s
        """, (creator_id,))
        creator = cursor.fetchone()

        cursor.execute("""
            SELECT brand_name, category, contact_email, application_form_url
            FROM pr_brands WHERE id = %s
        """, (brand_id,))
        brand = cursor.fetchone()

        if not creator or not brand:
            cursor.close()
            conn.close()
            return jsonify({'success': False, 'error': 'Creator or brand not found'}), 404

        # Insert bump request into queue
        cursor.execute("""
            INSERT INTO profile_bumps (
                creator_id, brand_id, pipeline_id,
                creator_username, creator_niche, creator_followers,
                creator_email, kit_slug,
                brand_name, brand_category, brand_email,
                status, created_at
            ) VALUES (
                %s, %s, %s,
                %s, %s, %s,
                %s, %s,
                %s, %s, %s,
                'pending', NOW()
            )
            RETURNING id
        """, (
            creator_id, brand_id, pipeline_id,
            creator.get('username'), creator.get('niche'), creator.get('followers_count'),
            creator.get('creator_email'), creator.get('kit_slug'),
            brand.get('brand_name'), brand.get('category'), brand.get('pr_email')
        ))
        bump_id = cursor.fetchone()['id']

        conn.commit()
        cursor.close()
        conn.close()

        return jsonify({
            'success': True,
            'bump_id': bump_id,
            'bumps_remaining': 2 - (bump_count + 1)
        })

    except Exception as e:
        import traceback
        print(f"Error in bump_profile: {str(e)}")
        traceback.print_exc()
        return jsonify({'success': False, 'error': str(e)}), 500


@pr_crm.route('/pipeline/bumps-remaining', methods=['GET'])
def get_bumps_remaining():
    """Get remaining bump count for current month and list of bumped pipeline IDs."""
    creator_id = get_creator_id_from_session()
    if not creator_id:
        return jsonify({'success': False, 'error': 'Not authenticated'}), 401

    try:
        conn = get_db_connection()
        cursor = conn.cursor(cursor_factory=RealDictCursor)

        # Get bump count for current month
        cursor.execute("""
            SELECT COUNT(*) as bump_count
            FROM profile_bumps
            WHERE creator_id = %s
              AND created_at > DATE_TRUNC('month', CURRENT_DATE)
        """, (creator_id,))
        bump_count = cursor.fetchone()['bump_count']

        # Get all bumped pipeline IDs with their status
        cursor.execute("""
            SELECT pipeline_id, status
            FROM profile_bumps
            WHERE creator_id = %s
        """, (creator_id,))
        bumped_items = {row['pipeline_id']: row['status'] for row in cursor.fetchall()}

        cursor.close()
        conn.close()

        return jsonify({
            'success': True,
            'bumps_used': bump_count,
            'bumps_remaining': max(0, 2 - bump_count),
            'bumped_items': bumped_items
        })

    except Exception as e:
        return jsonify({'success': True, 'bumps_used': 0, 'bumps_remaining': 2, 'bumped_items': {}})


@pr_crm.route('/kit-views', methods=['GET'])
def get_kit_views():
    """
    Get kit views with brand attribution for "Who Viewed Your Kit" feature.
    Free users: Get total count only (teaser)
    Pro users: Get full list with brand names, timestamps, view counts
    """
    creator_id = get_creator_id_from_session()
    if not creator_id:
        return jsonify({'success': False, 'error': 'Not authenticated'}), 401

    try:
        is_pro = get_subscription_status(creator_id) in ['pro', 'elite']

        conn = get_db_connection()
        cursor = conn.cursor(cursor_factory=RealDictCursor)

        print(f"[KIT_VIEWS_API] Fetching kit views for creator_id: {creator_id}, is_pro: {is_pro}")

        # Get total views this week (for free users teaser)
        cursor.execute('''
            SELECT COUNT(*) as total_views
            FROM kit_views
            WHERE creator_id = %s
              AND viewed_at > NOW() - INTERVAL '7 days'
              AND brand_id IS NOT NULL
        ''', (creator_id,))
        total_views = cursor.fetchone()['total_views']
        print(f"[KIT_VIEWS_API] Total views this week: {total_views}")

        # Get unique brands that viewed (for count display)
        cursor.execute('''
            SELECT COUNT(DISTINCT brand_id) as unique_brands
            FROM kit_views
            WHERE creator_id = %s
              AND viewed_at > NOW() - INTERVAL '7 days'
              AND brand_id IS NOT NULL
        ''', (creator_id,))
        unique_brands = cursor.fetchone()['unique_brands']

        if not is_pro:
            # Free users only get the count (teaser)
            cursor.close()
            conn.close()
            return jsonify({
                'success': True,
                'is_pro': False,
                'views_this_week': total_views,
                'brands_this_week': unique_brands,
                'views': []  # Empty list for free users
            })

        # Pro users get full details
        cursor.execute('''
            SELECT
                kv.id,
                kv.brand_id,
                kv.pipeline_id,
                kv.viewed_at,
                kv.view_count,
                pb.brand_name,
                pb.logo_url,
                pb.category,
                cp.stage,
                cp.replied_at
            FROM kit_views kv
            JOIN pr_brands pb ON pb.id = kv.brand_id
            LEFT JOIN creator_pipeline cp ON cp.id = kv.pipeline_id
            WHERE kv.creator_id = %s
              AND kv.brand_id IS NOT NULL
            ORDER BY kv.viewed_at DESC
            LIMIT 20
        ''', (creator_id,))
        views = cursor.fetchall()

        cursor.close()
        conn.close()

        return jsonify({
            'success': True,
            'is_pro': True,
            'views_this_week': total_views,
            'brands_this_week': unique_brands,
            'views': [{
                'id': v['id'],
                'brand_id': v['brand_id'],
                'brand_name': v['brand_name'],
                'logo_url': v['logo_url'],
                'category': v['category'],
                'viewed_at': v['viewed_at'].isoformat() if v['viewed_at'] else None,
                'view_count': v['view_count'],
                'has_replied': v['replied_at'] is not None,
                'pipeline_stage': v['stage']
            } for v in views]
        })

    except Exception as e:
        print(f"Error in get_kit_views: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({'success': True, 'is_pro': False, 'views_this_week': 0, 'brands_this_week': 0, 'views': []})


@pr_crm.route('/pipeline/stats', methods=['GET'])
def get_pipeline_stats():
    """
    Lightweight endpoint for the journey header stats card.
    """
    creator_id = get_creator_id_from_session()
    if not creator_id:
        return jsonify({'success': False, 'error': 'Not authenticated'}), 401

    try:
        is_pro = get_subscription_status(creator_id) in ['pro', 'elite']

        conn = get_db_connection()
        cursor = conn.cursor(cursor_factory=RealDictCursor)

        cursor.execute("""
            SELECT
                COUNT(*) FILTER (WHERE send_confirmed = TRUE OR pitched_at IS NOT NULL) AS total_contacted,
                COUNT(*) FILTER (WHERE stage IN ('replied', 'won', 'received', 'success')) AS total_responded,
                COALESCE(SUM(package_value) FILTER (WHERE stage IN ('won', 'received', 'success')), 0) AS pr_value_earned
            FROM creator_pipeline WHERE creator_id = %s
        """, (creator_id,))
        stats = cursor.fetchone()

        cursor.close()
        conn.close()

        return jsonify({
            'success': True,
            **dict(stats),
            'pr_value_visible': is_pro,
        })

    except Exception as e:
        import traceback
        print(f"Error in get_pipeline_stats: {str(e)}")
        traceback.print_exc()
        return jsonify({'success': False, 'error': str(e)}), 500


def _update_brand_response_rate(cursor, conn, pipeline_id):
    """Recalculate brand response rate from real outcomes."""
    try:
        cursor.execute("""
            UPDATE pr_brands b SET
                response_rate = (
                    SELECT ROUND(
                        COUNT(*) FILTER (WHERE stage IN ('replied', 'won', 'received', 'success')) * 100.0
                        / NULLIF(COUNT(*) FILTER (WHERE send_confirmed = TRUE OR pitched_at IS NOT NULL), 0)
                    )
                    FROM creator_pipeline
                    WHERE brand_id = b.id AND (send_confirmed = TRUE OR pitched_at IS NOT NULL)
                ),
                responses_received = (
                    SELECT COUNT(*) FILTER (WHERE stage IN ('replied', 'won', 'received', 'success'))
                    FROM creator_pipeline WHERE brand_id = b.id
                )
            WHERE b.id = (SELECT brand_id FROM creator_pipeline WHERE id = %s)
        """, (pipeline_id,))
        conn.commit()
    except Exception as e:
        print(f"Error updating brand response rate: {str(e)}")


@pr_crm.route('/reveal-contact', methods=['POST'])
def reveal_contact():
    """Record when a creator reveals a brand's contact details"""
    creator_id = get_creator_id_from_session()
    if not creator_id:
        return jsonify({'success': False, 'error': 'Not authenticated'}), 401

    try:
        data = request.get_json()
        brand_id = data.get('brand_id')

        if not brand_id:
            return jsonify({'success': False, 'error': 'brand_id required'}), 400

        conn = get_db_connection()
        cursor = conn.cursor(cursor_factory=RealDictCursor)

        # Get creator's subscription tier and current usage
        cursor.execute('''
            SELECT subscription_tier, pitches_sent_this_month
            FROM creators
            WHERE id = %s
        ''', (creator_id,))

        creator = cursor.fetchone()
        if not creator:
            cursor.close()
            conn.close()
            return jsonify({'success': False, 'error': 'Creator not found'}), 404

        tier = creator['subscription_tier'] or 'free'
        current_count = creator['pitches_sent_this_month'] or 0

        # Check limits based on tier
        FREE_LIMIT = 10
        PRO_LIMIT = 20

        if tier == 'free' and current_count >= FREE_LIMIT:
            cursor.close()
            conn.close()
            return jsonify({
                'success': False,
                'error': 'Free tier limit reached',
                'message': f'You\'ve used all {FREE_LIMIT} free brand contacts. Upgrade to Pro for 20 contacts/month + pitch templates.',
                'current_count': current_count,
                'limit': FREE_LIMIT,
                'tier': tier
            }), 403

        if tier == 'pro' and current_count >= PRO_LIMIT:
            cursor.close()
            conn.close()
            return jsonify({
                'success': False,
                'error': 'Pro tier limit reached',
                'message': f'You\'ve used all {PRO_LIMIT} brand contacts this month. Upgrade to Elite for unlimited contacts.',
                'current_count': current_count,
                'limit': PRO_LIMIT,
                'tier': tier
            }), 403

        # Elite tier has unlimited, so no check needed

        # Increment the counter
        cursor.execute('''
            UPDATE creators
            SET pitches_sent_this_month = pitches_sent_this_month + 1
            WHERE id = %s
            RETURNING pitches_sent_this_month
        ''', (creator_id,))

        updated_count = cursor.fetchone()['pitches_sent_this_month']

        conn.commit()
        cursor.close()
        conn.close()

        return jsonify({
            'success': True,
            'message': 'Contact details revealed',
            'new_count': updated_count,
            'tier': tier
        }), 200

    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


# ============================================
# FOR YOU - PERSONALIZED RECOMMENDATIONS
# ============================================

@pr_crm.route('/for-you', methods=['GET'])
def get_for_you():
    """
    Get personalized brand recommendations for the For You page.
    Returns 3 sections: Hot This Week, Matched for You, Right Season
    """
    creator_id = get_creator_id_from_session()
    if not creator_id:
        return jsonify({'success': False, 'error': 'Not authenticated'}), 401

    try:
        conn = get_db_connection()
        cursor = conn.cursor(cursor_factory=RealDictCursor)

        # Get creator subscription status and profile
        is_pro = get_subscription_status(creator_id) in ['pro', 'elite']

        # Get creator's niche from signup + For You preferences + follower count
        # Join with media_kits to get total_followers if available
        cursor.execute("""
            SELECT c.niche, c.creator_niches, c.creator_followers, c.followers_count,
                   mk.total_followers AS media_kit_followers
            FROM creators c
            LEFT JOIN media_kits mk ON mk.creator_id = c.id
            WHERE c.id = %s
        """, (creator_id,))
        creator = cursor.fetchone()

        # Merge signup niche with For You niches
        signup_niche = creator.get('niche') if creator else None
        foryou_niches = creator.get('creator_niches') or [] if creator else []

        # Parse signup niche (could be string or array)
        parsed_signup_niches = []
        if signup_niche:
            if isinstance(signup_niche, list):
                parsed_signup_niches = [n.lower().strip() for n in signup_niche if n]
            elif isinstance(signup_niche, str):
                # Handle JSON array string or comma-separated
                import json
                try:
                    parsed = json.loads(signup_niche)
                    if isinstance(parsed, list):
                        parsed_signup_niches = [str(n).lower().strip() for n in parsed if n]
                    else:
                        parsed_signup_niches = [str(parsed).lower().strip()]
                except:
                    parsed_signup_niches = [n.strip().lower() for n in signup_niche.split(',') if n.strip()]

        # Combine niches, preferring For You selection, falling back to signup
        niches = foryou_niches if foryou_niches else parsed_signup_niches

        # Use For You followers if set, else media_kit total_followers, else signup followers_count
        followers = 0
        if creator:
            followers = (
                creator.get('creator_followers') or
                creator.get('media_kit_followers') or
                creator.get('followers_count') or
                0
            )

        # Calculate max brand min_followers requirement based on creator size
        # Prevents showing brands with high requirements to micro-creators
        min_follower_cap = get_min_follower_cap(followers)

        # Get IDs the user has already pitched (exclude from recommendations)
        cursor.execute("""
            SELECT brand_id FROM creator_pipeline
            WHERE creator_id = %s AND (send_confirmed = TRUE OR pitched_at IS NOT NULL)
        """, (creator_id,))
        already_pitched = cursor.fetchall()
        exclude_ids = [r['brand_id'] for r in already_pitched] if already_pitched else [0]

        # ── Section 1: Most Contacted Brands ─────────────────────────────
        # Top brands by total creators who pitched (stage = 'pitched') in past 30 days
        # IMPORTANT: Filter by user's niches to show relevant brands, not just any popular brand
        # Also filter by min_followers requirement to avoid showing brands that won't accept small creators

        # Build related niches for Section 1 (same logic as Section 2)
        # STRICT mapping: wellness ≠ fitness, they're distinct niches
        related_niches_map = {
            'beauty': ['skincare', 'makeup', 'haircare'],
            'skincare': ['beauty', 'wellness'],
            'fashion': ['lifestyle', 'accessories', 'activewear'],
            'lifestyle': ['fashion', 'home'],
            'fitness': ['athleisure', 'activewear', 'sports'],  # NOT wellness
            'activewear': ['fitness', 'athleisure', 'sports', 'fashion'],
            'athleisure': ['fitness', 'activewear', 'sports', 'fashion'],
            'sports': ['fitness', 'activewear', 'athleisure'],
            'wellness': ['skincare', 'supplements', 'self-care'],  # NOT fitness
            'supplements': ['wellness', 'health'],
            'food': ['lifestyle', 'kitchen', 'beverages'],
            'tech': ['gaming', 'gadgets', 'electronics'],
            'gadgets': ['tech', 'gaming', 'electronics'],
            'gaming': ['tech', 'entertainment'],
            'home': ['lifestyle', 'decor'],
        }

        # Sensitive categories that should only show to specific niches
        # Prevents tech/food/fitness/parenting creators seeing adult brands
        SENSITIVE_CATEGORIES = ['intimacy', 'adult', 'sexual wellness']
        SENSITIVE_ALLOWED_NICHES = ['beauty', 'wellness', 'lifestyle', 'self-care']

        # Build excluded categories based on creator's niches
        creator_niches_lower = [n.lower() for n in (niches or [])]
        # If creator is not in allowed niches, exclude sensitive categories
        exclude_sensitive = not any(n in SENSITIVE_ALLOWED_NICHES for n in creator_niches_lower)
        excluded_categories = SENSITIVE_CATEGORIES if exclude_sensitive else []

        hot_related = set()
        for n in (niches or []):
            # Normalize compound niches like "tech & gadgets" into ["tech", "gadgets"]
            normalized = normalize_niche(n)
            for niche_part in normalized:
                hot_related.add(niche_part)
                hot_related.update(related_niches_map.get(niche_part, []))
        hot_niches_list = list(hot_related) if hot_related else None

        # Add sensitive category exclusion clause if needed
        sensitive_clause = "AND LOWER(b.category) != ALL(%s)" if excluded_categories else ""
        sensitive_params = [excluded_categories] if excluded_categories else []

        if hot_niches_list and min_follower_cap:
            query = f"""
                SELECT
                    b.id, b.slug, b.brand_name AS name, b.logo_url AS logo,
                    b.description, b.category, b.response_rate, b.price_point, b.price_point,
                    b.min_followers, b.website, b.application_form_url
                FROM pr_brands b
                WHERE b.slug IS NOT NULL
                  AND COALESCE(b.status, 'published') = 'published'
                  AND b.id != ALL(%s)
                  AND (b.min_followers IS NULL OR b.min_followers <= %s)
                  AND LOWER(b.category) = ANY(%s)
                  {sensitive_clause}
                ORDER BY (
                    SELECT COUNT(DISTINCT cp.creator_id) FROM creator_pipeline cp
                    WHERE cp.brand_id = b.id
                    AND cp.stage = 'pitched'
                    AND cp.created_at > NOW() - INTERVAL '30 days'
                ) DESC, b.response_rate DESC NULLS LAST
                LIMIT 6
            """
            cursor.execute(query, (exclude_ids, min_follower_cap, hot_niches_list, *sensitive_params))
        elif hot_niches_list:
            query = f"""
                SELECT
                    b.id, b.slug, b.brand_name AS name, b.logo_url AS logo,
                    b.description, b.category, b.response_rate, b.price_point, b.price_point,
                    b.min_followers, b.website, b.application_form_url
                FROM pr_brands b
                WHERE b.slug IS NOT NULL
                  AND COALESCE(b.status, 'published') = 'published'
                  AND b.id != ALL(%s)
                  AND LOWER(b.category) = ANY(%s)
                  {sensitive_clause}
                ORDER BY (
                    SELECT COUNT(DISTINCT cp.creator_id) FROM creator_pipeline cp
                    WHERE cp.brand_id = b.id
                    AND cp.stage = 'pitched'
                    AND cp.created_at > NOW() - INTERVAL '30 days'
                ) DESC, b.response_rate DESC NULLS LAST
                LIMIT 6
            """
            cursor.execute(query, (exclude_ids, hot_niches_list, *sensitive_params))
        elif min_follower_cap:
            query = f"""
                SELECT
                    b.id, b.slug, b.brand_name AS name, b.logo_url AS logo,
                    b.description, b.category, b.response_rate, b.price_point,
                    b.min_followers, b.website, b.application_form_url
                FROM pr_brands b
                WHERE b.slug IS NOT NULL
                  AND COALESCE(b.status, 'published') = 'published'
                  AND b.id != ALL(%s)
                  AND (b.min_followers IS NULL OR b.min_followers <= %s)
                  {sensitive_clause}
                ORDER BY (
                    SELECT COUNT(DISTINCT cp.creator_id) FROM creator_pipeline cp
                    WHERE cp.brand_id = b.id
                    AND cp.stage = 'pitched'
                    AND cp.created_at > NOW() - INTERVAL '30 days'
                ) DESC, b.response_rate DESC NULLS LAST
                LIMIT 6
            """
            cursor.execute(query, (exclude_ids, min_follower_cap, *sensitive_params))
        else:
            query = f"""
                SELECT
                    b.id, b.slug, b.brand_name AS name, b.logo_url AS logo,
                    b.description, b.category, b.response_rate, b.price_point,
                    b.min_followers, b.website, b.application_form_url
                FROM pr_brands b
                WHERE b.slug IS NOT NULL
                  AND COALESCE(b.status, 'published') = 'published'
                  AND b.id != ALL(%s)
                  {sensitive_clause}
                ORDER BY (
                    SELECT COUNT(DISTINCT cp.creator_id) FROM creator_pipeline cp
                    WHERE cp.brand_id = b.id
                    AND cp.stage = 'pitched'
                    AND cp.created_at > NOW() - INTERVAL '30 days'
                ) DESC, b.response_rate DESC NULLS LAST
                LIMIT 6
            """
            cursor.execute(query, (exclude_ids, *sensitive_params))
        hot = cursor.fetchall()

        # Fallback: if not enough brands with pitches, fill from popular brands (also filtered by niche)
        if len(hot) < 3:
            hot_ids = [r['id'] for r in hot] if hot else [0]
            if hot_niches_list and min_follower_cap:
                query = f"""
                    SELECT
                        b.id, b.slug, b.brand_name AS name, b.logo_url AS logo,
                        b.description, b.category, b.response_rate, b.price_point,
                        b.min_followers, b.website, b.application_form_url
                    FROM pr_brands b
                    WHERE b.slug IS NOT NULL
                      AND COALESCE(b.status, 'published') = 'published'
                      AND b.id != ALL(%s)
                      AND b.id != ALL(%s)
                      AND (b.min_followers IS NULL OR b.min_followers <= %s)
                      AND LOWER(b.category) = ANY(%s)
                      {sensitive_clause}
                    ORDER BY b.response_rate DESC NULLS LAST, RANDOM()
                    LIMIT %s
                """
                cursor.execute(query, (exclude_ids, hot_ids, min_follower_cap, hot_niches_list, *sensitive_params, 6 - len(hot)))
            elif hot_niches_list:
                query = f"""
                    SELECT
                        b.id, b.slug, b.brand_name AS name, b.logo_url AS logo,
                        b.description, b.category, b.response_rate, b.price_point,
                        b.min_followers, b.website, b.application_form_url
                    FROM pr_brands b
                    WHERE b.slug IS NOT NULL
                      AND COALESCE(b.status, 'published') = 'published'
                      AND b.id != ALL(%s)
                      AND b.id != ALL(%s)
                      AND LOWER(b.category) = ANY(%s)
                      {sensitive_clause}
                    ORDER BY b.response_rate DESC NULLS LAST, RANDOM()
                    LIMIT %s
                """
                cursor.execute(query, (exclude_ids, hot_ids, hot_niches_list, *sensitive_params, 6 - len(hot)))
            elif min_follower_cap:
                query = f"""
                    SELECT
                        b.id, b.slug, b.brand_name AS name, b.logo_url AS logo,
                        b.description, b.category, b.response_rate, b.price_point,
                        b.min_followers, b.website, b.application_form_url
                    FROM pr_brands b
                    WHERE b.slug IS NOT NULL
                      AND COALESCE(b.status, 'published') = 'published'
                      AND b.id != ALL(%s)
                      AND b.id != ALL(%s)
                      AND (b.min_followers IS NULL OR b.min_followers <= %s)
                      {sensitive_clause}
                    ORDER BY b.response_rate DESC NULLS LAST, RANDOM()
                    LIMIT %s
                """
                cursor.execute(query, (exclude_ids, hot_ids, min_follower_cap, *sensitive_params, 6 - len(hot)))
            else:
                query = f"""
                    SELECT
                        b.id, b.slug, b.brand_name AS name, b.logo_url AS logo,
                        b.description, b.category, b.response_rate, b.price_point,
                        b.min_followers, b.website, b.application_form_url
                    FROM pr_brands b
                    WHERE b.slug IS NOT NULL
                      AND COALESCE(b.status, 'published') = 'published'
                      AND b.id != ALL(%s)
                      AND b.id != ALL(%s)
                      {sensitive_clause}
                    ORDER BY b.response_rate DESC NULLS LAST, RANDOM()
                    LIMIT %s
                """
                cursor.execute(query, (exclude_ids, hot_ids, *sensitive_params, 6 - len(hot)))
            fallback = cursor.fetchall()
            hot = list(hot) + list(fallback)

        # ── Section 2: Matched for You ───────────────────────────
        # Real matching algorithm with meaningful differentiation
        # Score breakdown: Niche (0-40) + Followers (0-25) + Response (0-20) + Bonus (0-15) = 100 max

        # Build related niches map for scoring
        # Keep relationships tight - only truly related categories
        related_niches = {
            'beauty': ['skincare', 'makeup', 'haircare'],
            'skincare': ['beauty', 'makeup'],
            'makeup': ['beauty', 'skincare'],
            'fashion': ['lifestyle', 'accessories', 'activewear'],
            'lifestyle': ['fashion', 'home', 'food'],
            'fitness': ['athleisure', 'activewear', 'sports', 'wellness'],
            'activewear': ['fitness', 'athleisure', 'sports', 'wellness', 'fashion'],
            'athleisure': ['fitness', 'activewear', 'sports', 'fashion'],
            'sports': ['fitness', 'activewear', 'athleisure'],
            'wellness': ['fitness', 'supplements', 'self-care'],
            'supplements': ['wellness', 'fitness'],
            'food': ['lifestyle', 'kitchen', 'beverages', 'food & beverage'],
            'food & beverage': ['food', 'beverages', 'lifestyle'],
            'tech': ['gaming', 'gadgets', 'electronics'],
            'gadgets': ['tech', 'gaming', 'electronics'],
            'gaming': ['tech', 'entertainment'],
            'home': ['lifestyle', 'decor'],
        }

        # Get related categories for the creator's niches
        # Normalize compound niches like "tech & gadgets" into ["tech", "gadgets"]
        creator_related = set()
        for n in (niches or []):
            # Normalize compound niches first
            normalized = normalize_niche(n)
            for niche_part in normalized:
                creator_related.add(niche_part)
                creator_related.update(related_niches.get(niche_part, []))

        if niches or followers:
            # Build the scoring SQL with real differentiation
            # Filter by brand Instagram followers to avoid showing mega-brands to micro-creators
            brand_filter_sql = ""
            query_params = [
                [n.lower() for n in niches] if niches else [''],  # Exact match niches
                list(creator_related) if creator_related else [''],  # Related niches
                followers, followers, followers, followers, followers,  # For follower checks
                exclude_ids
            ]

            if min_follower_cap:
                brand_filter_sql = "AND (b.min_followers IS NULL OR b.min_followers <= %s)"
                query_params.append(min_follower_cap)

            # Add sensitive category exclusion
            if excluded_categories:
                brand_filter_sql += " AND LOWER(b.category) != ALL(%s)"
                query_params.append(excluded_categories)

            # Add niche relevance filter - only show brands matching creator's niches or related niches
            all_relevant_niches = list(creator_related) if creator_related else []
            if all_relevant_niches:
                brand_filter_sql += " AND LOWER(b.category) = ANY(%s)"
                query_params.append(all_relevant_niches)

            # Multi-niche users: add contact popularity to secondary sort
            has_multiple_niches = len(niches) > 1 if niches else False
            order_clause = """
                ORDER BY match_score DESC,
                    (SELECT COUNT(DISTINCT cp.creator_id) FROM creator_pipeline cp
                     WHERE cp.brand_id = b.id AND cp.stage = 'pitched'
                     AND cp.created_at > NOW() - INTERVAL '30 days') DESC,
                    b.response_rate DESC NULLS LAST
            """ if has_multiple_niches else "ORDER BY match_score DESC, b.response_rate DESC NULLS LAST"

            cursor.execute(f"""
                SELECT
                    b.id, b.slug, b.brand_name AS name, b.logo_url AS logo,
                    b.description, b.category, b.response_rate, b.price_point,
                    b.min_followers, b.max_followers, b.website, b.application_form_url,
                    b.niches AS brand_niches,
                    -- Match score scaled to 58-91%% range with natural variance
                    LEAST(91, GREATEST(58, (
                        58 + (
                            -- NICHE MATCH (0-12 scaled points)
                            CASE
                                WHEN LOWER(b.category) = ANY(%s) THEN 12
                                WHEN LOWER(b.category) = ANY(%s) THEN 8
                                ELSE 3
                            END
                            -- FOLLOWER FIT (0-9 scaled points)
                            + CASE
                                WHEN %s BETWEEN COALESCE(b.min_followers, 0)
                                    AND COALESCE(b.max_followers, 999999999) THEN 9
                                WHEN %s >= COALESCE(b.min_followers, 0)
                                    AND b.max_followers IS NULL THEN 8
                                WHEN %s >= COALESCE(b.min_followers, 0) * 0.7 THEN 6
                                WHEN %s >= COALESCE(b.min_followers, 0) * 0.5 THEN 4
                                WHEN %s > COALESCE(b.max_followers, 999999999) THEN 3
                                ELSE 2
                            END
                            -- RESPONSE RATE (0-7 scaled points)
                            + CASE
                                WHEN COALESCE(b.response_rate, 0) >= 50 THEN 7
                                WHEN COALESCE(b.response_rate, 0) >= 35 THEN 5
                                WHEN COALESCE(b.response_rate, 0) >= 20 THEN 4
                                WHEN COALESCE(b.response_rate, 0) >= 10 THEN 2
                                ELSE 1
                            END
                            -- BRAND QUALITY SIGNALS (0-5 scaled points)
                            + CASE WHEN b.has_application_form = true THEN 2 ELSE 0 END
                            + CASE WHEN b.contact_email IS NOT NULL AND b.contact_email != '' THEN 1 ELSE 0 END
                            + CASE WHEN COALESCE(b.price_point, 0) >= 50 THEN 2 ELSE 0 END
                            -- DETERMINISTIC VARIANCE per brand (0-8 points, prime modulo for spread)
                            + (b.id %% 9)
                        )
                    )))::int AS match_score
                FROM pr_brands b
                WHERE b.slug IS NOT NULL
                  AND COALESCE(b.status, 'published') = 'published'
                  AND b.id != ALL(%s)
                  {brand_filter_sql}
                {order_clause}
                LIMIT 8
            """, tuple(query_params))
            matched = cursor.fetchall()
        else:
            # No profile yet — return variety of top brands with basic scoring
            # Default to showing smaller brands (safe for any creator size)
            default_max_followers = 50000  # Safe default for unknown creators
            query = f"""
                SELECT
                    b.id, b.slug, b.brand_name AS name, b.logo_url AS logo,
                    b.description, b.category, b.response_rate, b.price_point,
                    b.min_followers, b.max_followers, b.website, b.application_form_url,
                    -- Match score scaled to 55-82%% range (lower since no profile match)
                    LEAST(82, GREATEST(55, (
                        55 + (
                            -- RESPONSE RATE (0-10 scaled points)
                            CASE
                                WHEN COALESCE(b.response_rate, 0) >= 50 THEN 10
                                WHEN COALESCE(b.response_rate, 0) >= 30 THEN 7
                                WHEN COALESCE(b.response_rate, 0) >= 15 THEN 5
                                ELSE 2
                            END
                            -- BRAND QUALITY (0-6 scaled points)
                            + CASE WHEN b.has_application_form = true THEN 3 ELSE 0 END
                            + CASE WHEN COALESCE(b.price_point, 0) >= 50 THEN 3 ELSE 0 END
                            -- DETERMINISTIC VARIANCE per brand (0-11 points)
                            + (b.id %% 12)
                        )
                    )))::int AS match_score
                FROM pr_brands b
                WHERE b.slug IS NOT NULL
                  AND COALESCE(b.status, 'published') = 'published'
                  AND b.id != ALL(%s)
                  AND (b.min_followers IS NULL OR b.min_followers <= %s)
                  {sensitive_clause}
                ORDER BY match_score DESC
                LIMIT 8
            """
            cursor.execute(query, (exclude_ids, default_max_followers, *sensitive_params))
            matched = cursor.fetchall()

        # Fallback: if 0 matched brands, return popular brands regardless of niche
        # Never leave user with an empty For You page
        if len(matched) == 0:
            fallback_query = f"""
                SELECT
                    b.id, b.slug, b.brand_name AS name, b.logo_url AS logo,
                    b.description, b.category, b.response_rate, b.price_point,
                    b.min_followers, b.website, b.application_form_url,
                    65 AS match_score
                FROM pr_brands b
                WHERE b.slug IS NOT NULL
                  AND COALESCE(b.status, 'published') = 'published'
                  AND b.id != ALL(%s)
                  {("AND (b.min_followers IS NULL OR b.min_followers <= %s)" if min_follower_cap else "")}
                  {sensitive_clause}
                ORDER BY b.response_rate DESC NULLS LAST, RANDOM()
                LIMIT 8
            """
            if min_follower_cap:
                cursor.execute(fallback_query, (exclude_ids, min_follower_cap, *sensitive_params))
            else:
                cursor.execute(fallback_query, (exclude_ids, *sensitive_params))
            matched = cursor.fetchall()

        # ── Section 3: Right Season ──────────────────────────────
        month = datetime.now().month
        seasonal_map = {
            1:  ['fitness', 'wellness', 'lifestyle'],
            2:  ['beauty', 'jewelry', 'fashion', 'skincare'],
            3:  ['beauty', 'fashion', 'skincare'],
            4:  ['fashion', 'lifestyle', 'beauty'],
            5:  ['fashion', 'lifestyle', 'skincare'],
            6:  ['lifestyle', 'beauty', 'skincare', 'fashion'],
            7:  ['lifestyle', 'beauty', 'fashion'],
            8:  ['beauty', 'lifestyle', 'fashion'],
            9:  ['fashion', 'beauty', 'lifestyle'],
            10: ['fashion', 'beauty', 'lifestyle', 'home'],
            11: ['beauty', 'fashion', 'home', 'jewelry'],
            12: ['beauty', 'fashion', 'home', 'jewelry', 'lifestyle'],
        }
        seasonal_cats = seasonal_map.get(month, ['beauty', 'lifestyle'])

        seasonal_reasons = {
            1:  "New Year reset: wellness brands gifting heavily in January",
            2:  "Valentine's season: beauty and jewelry brands seeking creators",
            3:  "Spring launch season: skincare brands partnering with creators",
            4:  "Spring fashion drops: brands seeking fresh campaign content",
            5:  "Pre-summer prep: lifestyle and skincare brands gifting now",
            6:  "Summer campaigns: SPF and fashion brands need content now",
            7:  "Peak summer: lifestyle brands seeking authentic summer content",
            8:  "Late summer push: fashion and beauty brands preparing for fall",
            9:  "Back to school/fall: fashion brands refreshing their creator roster",
            10: "Pre-holiday: beauty and home brands building gifting lists",
            11: "Holiday gifting season: brands most active for PR partnerships",
            12: "Year-end gifting: brands clearing PR budgets before January",
        }

        seasonal_query = f"""
            SELECT
                b.id, b.slug, b.brand_name AS name, b.logo_url AS logo,
                b.description, b.category, b.response_rate, b.price_point,
                b.min_followers, b.website, b.application_form_url
            FROM pr_brands b
            WHERE b.slug IS NOT NULL
              AND COALESCE(b.status, 'published') = 'published'
              AND LOWER(b.category) = ANY(%s)
              AND b.id != ALL(%s)
              {sensitive_clause}
            ORDER BY b.response_rate DESC NULLS LAST, RANDOM()
            LIMIT 4
        """
        cursor.execute(seasonal_query, ([c.lower() for c in seasonal_cats], exclude_ids, *sensitive_params))
        seasonal = cursor.fetchall()

        # ── Section 4: New Brands on newcollab ──────────────────────────
        # 4 most recently added brands, respecting follower cap, sensitive
        # category exclusions and brands the creator already pitched.
        # min_follower_cap may be None (50K+ creators) → treat as no cap.
        newest_follower_cap = min_follower_cap if min_follower_cap else 999999999
        newest_query = f"""
            SELECT
                b.id, b.slug, b.brand_name AS name, b.logo_url AS logo,
                b.description, b.category, b.response_rate, b.price_point,
                b.min_followers, b.website, b.application_form_url, b.created_at
            FROM pr_brands b
            WHERE b.slug IS NOT NULL
              AND COALESCE(b.status, 'published') = 'published'
              AND b.id != ALL(%s)
              AND (b.min_followers IS NULL OR b.min_followers <= %s)
              {sensitive_clause}
            ORDER BY b.created_at DESC NULLS LAST
            LIMIT 4
        """
        cursor.execute(newest_query, (exclude_ids, newest_follower_cap, *sensitive_params))
        newest = cursor.fetchall()

        cursor.close()
        conn.close()

        return jsonify({
            'success': True,
            'hot': [dict(r) for r in hot],
            'matched': [dict(r) for r in matched],
            'seasonal': [dict(r) for r in seasonal],
            'seasonal_reason': seasonal_reasons.get(month, ''),
            'seasonal_month': datetime.now().strftime('%B'),
            'newest': [dict(r) for r in newest],
            'is_pro': is_pro,
            'has_profile': bool(niches or followers),
            'profile': {
                'niches': niches,
                'followers': followers,
            }
        })

    except Exception as e:
        print(f"Error in get_for_you: {str(e)}")
        import traceback
        traceback.print_exc()
        return jsonify({'success': False, 'error': str(e)}), 500


@pr_crm.route('/matched-brands-count', methods=['GET'])
def get_matched_brands_count():
    """
    Count of the creator's matched brands they still need to contact.
    Powers the "matched brands remaining" notification badge on For You.

    Mirrors the matched-section logic (same niche/follower filtering, same
    8-brand display cap) so the badge equals what the user sees, then
    subtracts how many brands they've already contacted. This makes the
    badge tick down as they pitch (e.g. 8 → 6 after contacting 2), creating
    the incentive to upgrade and pitch the rest of their matches.

    Returns: { success, count, matched_total, contacted }
        count          — matched brands left to contact (drives the badge)
        matched_total  — size of their matched roster (display-capped at 8)
        contacted      — brands they've already pitched
    """
    import json

    creator_id = get_creator_id_from_session()
    if not creator_id:
        return jsonify({'success': False, 'error': 'Not authenticated'}), 401

    MATCHED_DISPLAY_CAP = 8

    try:
        conn = get_db_connection()
        cursor = conn.cursor(cursor_factory=RealDictCursor)

        # Creator niche + follower profile (same resolution as /for-you)
        cursor.execute("""
            SELECT c.niche, c.creator_niches, c.creator_followers, c.followers_count,
                   mk.total_followers AS media_kit_followers
            FROM creators c
            LEFT JOIN media_kits mk ON mk.creator_id = c.id
            WHERE c.id = %s
        """, (creator_id,))
        creator = cursor.fetchone()

        foryou_niches = (creator.get('creator_niches') or []) if creator else []
        signup_niche = creator.get('niche') if creator else None
        parsed_signup_niches = []
        if signup_niche:
            if isinstance(signup_niche, list):
                parsed_signup_niches = [str(n).lower().strip() for n in signup_niche if n]
            elif isinstance(signup_niche, str):
                try:
                    parsed = json.loads(signup_niche)
                    if isinstance(parsed, list):
                        parsed_signup_niches = [str(n).lower().strip() for n in parsed if n]
                    else:
                        parsed_signup_niches = [str(parsed).lower().strip()]
                except Exception:
                    parsed_signup_niches = [n.strip().lower() for n in signup_niche.split(',') if n.strip()]
        niches = foryou_niches if foryou_niches else parsed_signup_niches

        followers = 0
        if creator:
            followers = (
                creator.get('creator_followers') or
                creator.get('media_kit_followers') or
                creator.get('followers_count') or
                0
            )
        min_follower_cap = get_min_follower_cap(followers)

        # Related niches — keep in sync with the matched section in /for-you
        related_niches = {
            'beauty': ['skincare', 'makeup', 'haircare'],
            'skincare': ['beauty', 'makeup'],
            'makeup': ['beauty', 'skincare'],
            'fashion': ['lifestyle', 'accessories', 'activewear'],
            'lifestyle': ['fashion', 'home', 'food'],
            'fitness': ['athleisure', 'activewear', 'sports', 'wellness'],
            'activewear': ['fitness', 'athleisure', 'sports', 'wellness', 'fashion'],
            'athleisure': ['fitness', 'activewear', 'sports', 'fashion'],
            'sports': ['fitness', 'activewear', 'athleisure'],
            'wellness': ['fitness', 'supplements', 'self-care'],
            'supplements': ['wellness', 'fitness'],
            'food': ['lifestyle', 'kitchen', 'beverages', 'food & beverage'],
            'food & beverage': ['food', 'beverages', 'lifestyle'],
            'tech': ['gaming', 'gadgets', 'electronics'],
            'gadgets': ['tech', 'gaming', 'electronics'],
            'gaming': ['tech', 'entertainment'],
            'home': ['lifestyle', 'decor'],
        }
        creator_related = set()
        for n in (niches or []):
            # Normalize compound niches like "tech & gadgets" into ["tech", "gadgets"]
            normalized = normalize_niche(n)
            for niche_part in normalized:
                creator_related.add(niche_part)
                creator_related.update(related_niches.get(niche_part, []))

        # Sensitive-category exclusion (same rule as /for-you)
        SENSITIVE_CATEGORIES = ['intimacy', 'adult', 'sexual wellness']
        SENSITIVE_ALLOWED_NICHES = ['beauty', 'wellness', 'lifestyle', 'self-care']
        creator_niches_lower = [n.lower() for n in (niches or [])]
        exclude_sensitive = not any(n in SENSITIVE_ALLOWED_NICHES for n in creator_niches_lower)
        excluded_categories = SENSITIVE_CATEGORIES if exclude_sensitive else []

        # Size of the matched roster (stable — counts the full qualifying pool,
        # capped at the 8 shown). Mirrors the matched section's filters.
        where = ["b.slug IS NOT NULL", "COALESCE(b.status, 'published') = 'published'"]
        params = []
        if min_follower_cap:
            where.append("(b.min_followers IS NULL OR b.min_followers <= %s)")
            params.append(min_follower_cap)
        if excluded_categories:
            where.append("LOWER(b.category) != ALL(%s)")
            params.append(excluded_categories)
        if creator_related:
            where.append("LOWER(b.category) = ANY(%s)")
            params.append(list(creator_related))

        cursor.execute(
            "SELECT COUNT(*) AS cnt FROM pr_brands b WHERE " + " AND ".join(where),
            tuple(params),
        )
        matched_pool = cursor.fetchone()['cnt']
        matched_total = min(matched_pool, MATCHED_DISPLAY_CAP)

        # How many brands the creator has already contacted
        cursor.execute("""
            SELECT COUNT(DISTINCT brand_id) AS cnt FROM creator_pipeline
            WHERE creator_id = %s AND (send_confirmed = TRUE OR pitched_at IS NOT NULL)
        """, (creator_id,))
        contacted = cursor.fetchone()['cnt']

        remaining = max(0, matched_total - contacted)

        cursor.close()
        conn.close()

        return jsonify({
            'success': True,
            'count': remaining,
            'matched_total': matched_total,
            'contacted': contacted,
        })

    except Exception as e:
        print(f"Error in get_matched_brands_count: {str(e)}")
        return jsonify({'success': False, 'error': str(e), 'count': 0}), 500


@pr_crm.route('/creator-profile', methods=['PATCH'])
def update_creator_profile():
    """
    Update creator's niche and follower count for personalized recommendations.
    Body: { creator_niches: string[], creator_followers: int }

    Keeps both niche (JSON string) and creator_niches (array) in sync for consistency.
    """
    creator_id = get_creator_id_from_session()
    if not creator_id:
        return jsonify({'success': False, 'error': 'Not authenticated'}), 401

    try:
        data = request.get_json()

        allowed = {'creator_niches', 'creator_followers'}
        updates = {k: v for k, v in data.items() if k in allowed}

        if not updates:
            return jsonify({'success': False, 'error': 'Nothing to update'}), 400

        conn = get_db_connection()
        cursor = conn.cursor(cursor_factory=RealDictCursor)

        # Build dynamic update query
        set_parts = []
        values = []
        for key, value in updates.items():
            # PostgreSQL TEXT[] arrays - psycopg2 handles list adaptation natively
            if key == 'creator_niches' and isinstance(value, list):
                set_parts.append(f"{key} = %s")
                values.append(value)
                # Keep niche column in sync for consistency across the app
                set_parts.append("niche = %s")
                values.append(json.dumps(value))
            else:
                set_parts.append(f"{key} = %s")
                values.append(value)

        values.append(creator_id)

        cursor.execute(f"""
            UPDATE creators
            SET {', '.join(set_parts)}
            WHERE id = %s
            RETURNING creator_niches, creator_followers
        """, values)

        updated = cursor.fetchone()

        if not updated:
            cursor.close()
            conn.close()
            return jsonify({'success': False, 'error': 'Creator profile not found'}), 404

        conn.commit()
        cursor.close()
        conn.close()

        return jsonify({
            'success': True,
            'profile': {
                'niches': updated.get('creator_niches') or [],
                'followers': updated.get('creator_followers') or 0
            }
        })

    except Exception as e:
        print(f"Error in update_creator_profile: {str(e)}")
        return jsonify({'success': False, 'error': str(e)}), 500


@pr_crm.route('/recent-replies', methods=['GET'])
def get_recent_replies():
    """
    Get recent successful replies for social proof strip.
    Shows personalized data - creators in SAME niche getting replies from brands.
    """
    try:
        user_id = session.get('user_id')
        if not user_id:
            # Return empty for non-logged-in users
            return jsonify({'success': True, 'replies': []})

        conn = get_db_connection()
        cursor = conn.cursor(cursor_factory=RealDictCursor)

        # Get current user's niches for personalized social proof
        # IMPORTANT: Use user_id column, not id. And prefer creator_niches over niche.
        cursor.execute("SELECT creator_niches, niche FROM creators WHERE user_id = %s", (user_id,))
        user_row = cursor.fetchone()
        user_niches = []
        if user_row:
            # Prefer For You edited niches, fall back to signup niche
            niche_raw = user_row.get('creator_niches') or user_row.get('niche')
            if isinstance(niche_raw, str):
                try:
                    user_niches = json.loads(niche_raw)
                except:
                    user_niches = [niche_raw]
            elif isinstance(niche_raw, list):
                user_niches = niche_raw
        user_niches_lower = [n.lower() for n in user_niches if n]

        # Related niches for broader matching
        # NOTE: fitness ≠ wellness - they are distinct categories
        related_niches = {
            'fitness': ['athleisure', 'activewear', 'sports'],
            'wellness': ['skincare', 'supplements', 'self-care'],
            'supplements': ['wellness', 'health'],
            'beauty': ['skincare', 'makeup', 'haircare'],
            'skincare': ['beauty', 'wellness', 'makeup'],
            'fashion': ['lifestyle', 'accessories', 'jewelry'],
            'lifestyle': ['fashion', 'home'],
            'pet': ['animals', 'pets'],
            'tech': ['gaming', 'gadgets', 'electronics'],
            'gadgets': ['tech', 'gaming', 'electronics'],
            'food': ['cooking', 'recipes', 'kitchen'],
        }
        expanded_niches = set(user_niches_lower)
        for n in user_niches_lower:
            # Normalize compound niches like "tech & gadgets" into ["tech", "gadgets"]
            normalized = normalize_niche(n)
            for niche_part in normalized:
                expanded_niches.add(niche_part)
                expanded_niches.update(related_niches.get(niche_part, []))
        expanded_niches_list = list(expanded_niches) if expanded_niches else ['']

        # Get recent replies from creators in similar niches, matching brand categories
        cursor.execute("""
            SELECT
                pb.brand_name,
                pb.category AS brand_category,
                c.niche AS creator_niche,
                -- Format follower count as "6.4K" style
                CASE
                    WHEN COALESCE(c.followers_count, 0) >= 1000000 THEN ROUND(c.followers_count / 1000000.0, 1)::text || 'M'
                    WHEN COALESCE(c.followers_count, 0) >= 1000 THEN ROUND(c.followers_count / 1000.0, 1)::text || 'K'
                    ELSE COALESCE(c.followers_count, 0)::text
                END AS follower_range,
                -- Time ago for freshness signal
                CASE
                    WHEN cp.replied_at > NOW() - INTERVAL '1 hour' THEN EXTRACT(MINUTE FROM NOW() - cp.replied_at)::int || 'm ago'
                    WHEN cp.replied_at > NOW() - INTERVAL '24 hours' THEN EXTRACT(HOUR FROM NOW() - cp.replied_at)::int || 'h ago'
                    ELSE EXTRACT(DAY FROM NOW() - cp.replied_at)::int || 'd ago'
                END AS time_ago,
                'got a reply from' AS event
            FROM creator_pipeline cp
            JOIN pr_brands pb ON pb.id = cp.brand_id
            JOIN creators c ON c.id = cp.creator_id
            WHERE cp.stage = 'replied'
              AND cp.replied_at > NOW() - INTERVAL '14 days'
              AND (
                  LOWER(pb.category) = ANY(%s)
                  OR EXISTS (
                      SELECT 1 FROM jsonb_array_elements_text(
                          CASE WHEN c.niche::text LIKE '[%%' THEN c.niche::jsonb ELSE to_jsonb(ARRAY[c.niche]) END
                      ) AS elem WHERE LOWER(elem) = ANY(%s)
                  )
              )
            ORDER BY cp.replied_at DESC
            LIMIT 5
        """, (expanded_niches_list, expanded_niches_list))
        replies = cursor.fetchall()

        # If no niche-specific replies, get general recent ones
        if not replies:
            cursor.execute("""
                SELECT
                    pb.brand_name,
                    pb.category AS brand_category,
                    c.niche AS creator_niche,
                    CASE
                        WHEN COALESCE(c.followers_count, 0) >= 1000000 THEN ROUND(c.followers_count / 1000000.0, 1)::text || 'M'
                        WHEN COALESCE(c.followers_count, 0) >= 1000 THEN ROUND(c.followers_count / 1000.0, 1)::text || 'K'
                        ELSE COALESCE(c.followers_count, 0)::text
                    END AS follower_range,
                    CASE
                        WHEN cp.replied_at > NOW() - INTERVAL '1 hour' THEN EXTRACT(MINUTE FROM NOW() - cp.replied_at)::int || 'm ago'
                        WHEN cp.replied_at > NOW() - INTERVAL '24 hours' THEN EXTRACT(HOUR FROM NOW() - cp.replied_at)::int || 'h ago'
                        ELSE EXTRACT(DAY FROM NOW() - cp.replied_at)::int || 'd ago'
                    END AS time_ago,
                    'got a reply from' AS event
                FROM creator_pipeline cp
                JOIN pr_brands pb ON pb.id = cp.brand_id
                JOIN creators c ON c.id = cp.creator_id
                WHERE cp.stage = 'replied'
                  AND cp.replied_at > NOW() - INTERVAL '14 days'
                ORDER BY cp.replied_at DESC
                LIMIT 5
            """)
            replies = cursor.fetchall()

        cursor.close()
        conn.close()

        # Format the replies - ALWAYS show user's niche for relevance and personalization
        # This makes the social proof feel personalized: "A pet creator got a reply..."
        formatted_replies = []
        primary_user_niche = user_niches[0] if user_niches else None

        for r in replies:
            reply_dict = dict(r)

            # Always use user's primary niche for social proof display
            # This ensures "A pet creator got a reply" for pet users, not "A beauty creator"
            if primary_user_niche:
                reply_dict['creator_niche'] = primary_user_niche
            else:
                # Fallback: parse and use actual creator niche
                creator_niche = reply_dict.get('creator_niche', '')
                if isinstance(creator_niche, str) and creator_niche.startswith('['):
                    try:
                        creator_niche = json.loads(creator_niche)
                    except:
                        pass
                if isinstance(creator_niche, list):
                    creator_niche = creator_niche[0] if creator_niche else ''
                reply_dict['creator_niche'] = creator_niche or 'creator'

            formatted_replies.append(reply_dict)

        return jsonify({
            'success': True,
            'replies': formatted_replies
        })

    except Exception as e:
        print(f"Error in get_recent_replies: {str(e)}")
        return jsonify({'success': False, 'error': str(e), 'replies': []}), 500


@pr_crm.route('/social-proof-brands', methods=['GET'])
def get_social_proof_brands():
    """
    Get popular brand names for social proof feed.
    Returns a mix of brands that have been pitched and replied to.
    All brands are real from the database - no fakes.
    """
    try:
        conn = get_db_connection()
        cursor = conn.cursor(cursor_factory=RealDictCursor)

        # Get brands with recent pitch activity (contacted events)
        cursor.execute("""
            SELECT DISTINCT
                pb.brand_name,
                'contacted' AS event
            FROM creator_pipeline cp
            JOIN pr_brands pb ON pb.id = cp.brand_id
            WHERE cp.pitched_at > NOW() - INTERVAL '30 days'
              AND cp.pitched_at IS NOT NULL
            ORDER BY pb.brand_name
            LIMIT 15
        """)
        pitched_brands = cursor.fetchall()

        # Get brands with recent replies (reply events)
        cursor.execute("""
            SELECT DISTINCT
                pb.brand_name,
                'reply' AS event
            FROM creator_pipeline cp
            JOIN pr_brands pb ON pb.id = cp.brand_id
            WHERE cp.stage = 'replied'
              AND cp.replied_at > NOW() - INTERVAL '30 days'
            ORDER BY pb.brand_name
            LIMIT 10
        """)
        replied_brands = cursor.fetchall()

        # Get brands with packages sent (package events)
        cursor.execute("""
            SELECT DISTINCT
                pb.brand_name,
                'package' AS event
            FROM creator_pipeline cp
            JOIN pr_brands pb ON pb.id = cp.brand_id
            WHERE cp.stage = 'success'
              AND cp.updated_at > NOW() - INTERVAL '60 days'
            ORDER BY pb.brand_name
            LIMIT 5
        """)
        package_brands = cursor.fetchall()

        cursor.close()
        conn.close()

        # Combine and return
        brands = []
        seen = set()

        # Add pitched brands (contacted events) - most common
        for b in pitched_brands:
            if b['brand_name'] not in seen:
                brands.append({'name': b['brand_name'], 'event': 'contacted'})
                seen.add(b['brand_name'])

        # Add replied brands
        for b in replied_brands:
            if b['brand_name'] not in seen:
                brands.append({'name': b['brand_name'], 'event': 'reply'})
                seen.add(b['brand_name'])

        # Add package brands
        for b in package_brands:
            if b['brand_name'] not in seen:
                brands.append({'name': b['brand_name'], 'event': 'package'})
                seen.add(b['brand_name'])

        return jsonify({
            'success': True,
            'brands': brands
        })

    except Exception as e:
        print(f"Error in get_social_proof_brands: {str(e)}")
        return jsonify({'success': False, 'error': str(e), 'brands': []}), 500
