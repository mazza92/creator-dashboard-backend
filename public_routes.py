"""
Public Routes for SEO-Optimized Brand Directory
No authentication required - open to Google crawlers
"""

from flask import Blueprint, request, jsonify, Response
from psycopg2.extras import RealDictCursor
import os
import psycopg2
import requests

public_bp = Blueprint('public', __name__, url_prefix='/api/public')

# IndexNow configuration
INDEXNOW_KEY = '5b821f1380424d116b8da378e4ca2f143a13f7236d7dd3db58d09cb3e0aeb736'
INDEXNOW_API_URL = 'https://api.indexnow.org/indexnow'

def get_db_connection():
    """Get database connection"""
    return psycopg2.connect(
        host=os.getenv('DB_HOST'),
        port=os.getenv('DB_PORT', 5432),
        database=os.getenv('DB_NAME'),
        user=os.getenv('DB_USER'),
        password=os.getenv('DB_PASSWORD')
    )


def submit_to_indexnow(urls):
    """
    Submit URLs to IndexNow API for instant indexing

    Args:
        urls: Single URL string or list of URLs

    Returns:
        bool: True if successful, False otherwise
    """
    try:
        if isinstance(urls, str):
            urls = [urls]

        payload = {
            "host": "newcollab.co",
            "key": INDEXNOW_KEY,
            "keyLocation": f"https://newcollab.co/{INDEXNOW_KEY}.txt",
            "urlList": urls
        }

        response = requests.post(
            INDEXNOW_API_URL,
            headers={'Content-Type': 'application/json; charset=utf-8'},
            json=payload,
            timeout=10
        )

        if response.status_code == 200:
            print(f"✅ IndexNow: Successfully submitted {len(urls)} URLs")
            return True
        else:
            print(f"⚠️ IndexNow: Status {response.status_code} - {response.text}")
            return False

    except Exception as e:
        print(f"❌ IndexNow: Error submitting URLs: {str(e)}")
        return False

@public_bp.route('/brands', methods=['GET'])
def get_public_brands():
    """
    Public endpoint: Get paginated brand directory
    No auth required - optimized for SEO discovery

    Query params:
    - page: int (default 1)
    - limit: int (default 24, max 100)
    - category: filter by category
    - niche: filter by niche
    - min_followers: minimum follower requirement
    - search: search brand names
    - featured_only: bool - show only featured brands
    """
    try:
        page = int(request.args.get('page', 1))
        limit = min(int(request.args.get('limit', 24)), 100)  # Cap at 100
        offset = (page - 1) * limit

        category = request.args.get('category')
        niche = request.args.get('niche')
        min_followers = request.args.get('min_followers', type=int)
        search = request.args.get('search', '').strip()
        featured_only = request.args.get('featured_only', 'false').lower() == 'true'

        conn = get_db_connection()
        cursor = conn.cursor(cursor_factory=RealDictCursor)

        # Build query with filters
        query = """
            SELECT
                id,
                slug,
                brand_name,
                logo_url,
                description,
                category,
                niches,
                min_followers,
                max_followers,
                platforms,
                regions,
                response_rate,
                avg_response_time_days,
                is_featured,
                has_application_form
            FROM pr_brands
            WHERE 1=1
        """
        params = []

        if featured_only:
            query += " AND is_featured = TRUE"

        if category:
            query += " AND category = %s"
            params.append(category)

        if niche:
            query += " AND %s = ANY(niches)"
            params.append(niche)

        if min_followers is not None:
            query += " AND (min_followers <= %s OR min_followers IS NULL)"
            params.append(min_followers)

        if search:
            query += " AND brand_name ILIKE %s"
            params.append(f'%{search}%')

        # Order: Featured first, then by response rate
        query += """
            ORDER BY
                is_featured DESC,
                response_rate DESC NULLS LAST,
                brand_name ASC
            LIMIT %s OFFSET %s
        """
        params.extend([limit, offset])

        cursor.execute(query, params)
        brands = cursor.fetchall()

        # Get total count for pagination
        count_query = """
            SELECT COUNT(*) as total
            FROM pr_brands
            WHERE 1=1
        """
        count_params = []

        if featured_only:
            count_query += " AND is_featured = TRUE"
        if category:
            count_query += " AND category = %s"
            count_params.append(category)
        if niche:
            count_query += " AND %s = ANY(niches)"
            count_params.append(niche)
        if min_followers is not None:
            count_query += " AND (min_followers <= %s OR min_followers IS NULL)"
            count_params.append(min_followers)
        if search:
            count_query += " AND brand_name ILIKE %s"
            count_params.append(f'%{search}%')

        cursor.execute(count_query, count_params)
        total_count = cursor.fetchone()['total']

        cursor.close()
        conn.close()

        # Format response (NO sensitive data like email or application URLs)
        return jsonify({
            'brands': [{
                'id': b['id'],  # Include ID for save/unsave functionality
                'slug': b['slug'],
                'name': b['brand_name'],
                'logo': b['logo_url'],
                'description': b['description'][:200] if b['description'] else None,  # Truncate for preview
                'category': b['category'],
                'niches': b['niches'],
                'minFollowers': b['min_followers'],
                'maxFollowers': b['max_followers'],
                'platforms': b['platforms'],
                'regions': b['regions'],
                'responseRate': b['response_rate'],
                'avgResponseTime': b['avg_response_time_days'],
                'isFeatured': b['is_featured'],
                'hasApplication': b['has_application_form']
            } for b in brands],
            'pagination': {
                'page': page,
                'limit': limit,
                'total': total_count,
                'totalPages': (total_count + limit - 1) // limit
            }
        }), 200

    except Exception as e:
        print(f"Error fetching public brands: {str(e)}")
        return jsonify({'error': 'Failed to fetch brands'}), 500


@public_bp.route('/brands/<slug>', methods=['GET'])
def get_public_brand(slug):
    """
    Public endpoint: Get single brand details by slug

    SECURITY: This endpoint does NOT expose:
    - application_form_url (gated behind signup)
    - contact_email (gated behind Pro tier)

    Instead, returns:
    - locked: true/false to indicate if user needs to upgrade
    - applicationMethod: tells frontend what type of gate to show
    """
    try:
        conn = get_db_connection()
        cursor = conn.cursor(cursor_factory=RealDictCursor)

        cursor.execute("""
            SELECT
                slug,
                brand_name,
                logo_url,
                website,
                description,
                instagram_handle,
                tiktok_handle,
                category,
                niches,
                product_types,
                min_followers,
                max_followers,
                platforms,
                regions,
                has_application_form,
                response_rate,
                avg_response_time_days,
                is_featured,
                application_method,
                seo_title,
                seo_description,
                -- NOTE: We do NOT select application_form_url or contact_email
                CASE
                    WHEN application_form_url IS NOT NULL THEN TRUE
                    ELSE FALSE
                END as has_direct_link,
                CASE
                    WHEN contact_email IS NOT NULL THEN TRUE
                    ELSE FALSE
                END as has_email_contact
            FROM pr_brands
            WHERE slug = %s
        """, (slug,))

        brand = cursor.fetchone()
        cursor.close()
        conn.close()

        if not brand:
            return jsonify({'error': 'Brand not found'}), 404

        # Format public response
        response = {
            'slug': brand['slug'],
            'name': brand['brand_name'],
            'logo': brand['logo_url'],
            'website': brand['website'],
            'description': brand['description'],
            'instagram': brand['instagram_handle'],
            'tiktok': brand['tiktok_handle'],
            'category': brand['category'],
            'niches': brand['niches'],
            'productTypes': brand['product_types'],
            'requirements': {
                'minFollowers': brand['min_followers'],
                'maxFollowers': brand['max_followers'],
                'platforms': brand['platforms'],
                'regions': brand['regions']
            },
            'stats': {
                'responseRate': brand['response_rate'],
                'avgResponseTime': brand['avg_response_time_days']
            },
            'isFeatured': brand['is_featured'],
            'applicationMethod': brand['application_method'],
            # Gated fields - tell frontend what's locked
            'gated': {
                'hasDirectLink': brand['has_direct_link'],
                'hasEmailContact': brand['has_email_contact'],
                'directLink': {'locked': True, 'requiresAuth': True},
                'emailContact': {'locked': True, 'requiresPro': True}
            },
            # SEO metadata
            'seo': {
                'title': brand['seo_title'],
                'description': brand['seo_description']
            }
        }

        return jsonify(response), 200

    except Exception as e:
        print(f"Error fetching brand {slug}: {str(e)}")
        return jsonify({'error': 'Failed to fetch brand details'}), 500


@public_bp.route('/brands/<slug>/unlock', methods=['POST'])
def unlock_brand_access(slug):
    """
    Authenticated endpoint: Get gated brand data
    Requires: User must be logged in
    Returns: Application link for Free users, Email for Pro users
    """
    from flask import session

    try:
        # Check authentication
        user_id = session.get('user_id')
        creator_id = session.get('creator_id')

        if not user_id or not creator_id:
            return jsonify({'error': 'Authentication required'}), 401

        # Get user's subscription tier
        conn = get_db_connection()
        cursor = conn.cursor(cursor_factory=RealDictCursor)

        cursor.execute("""
            SELECT subscription_tier
            FROM creators
            WHERE id = %s
        """, (creator_id,))

        creator = cursor.fetchone()
        if not creator:
            return jsonify({'error': 'Creator not found'}), 404

        tier = creator['subscription_tier'] or 'free'

        # Get brand data
        cursor.execute("""
            SELECT
                brand_name,
                application_form_url,
                contact_email,
                application_method
            FROM pr_brands
            WHERE slug = %s
        """, (slug,))

        brand = cursor.fetchone()
        if not brand:
            return jsonify({'error': 'Brand not found'}), 404

        # Determine what data to return based on tier
        response = {
            'brandName': brand['brand_name'],
            'applicationMethod': brand['application_method']
        }

        # Free tier: Gets application link only
        if tier == 'free':
            if brand['application_form_url']:
                response['applicationUrl'] = brand['application_form_url']
            else:
                response['message'] = 'No public application form available. Upgrade to Pro for email contact.'

        # Pro/Elite tier: Gets email contact
        elif tier in ['pro', 'elite']:
            response['applicationUrl'] = brand['application_form_url']
            response['contactEmail'] = brand['contact_email']
            response['isPro'] = True

        # Auto-save to creator's pipeline
        try:
            cursor.execute("""
                INSERT INTO creator_pipeline (creator_id, brand_id, stage, created_at)
                SELECT %s, id, 'saved', NOW()
                FROM pr_brands
                WHERE slug = %s
                ON CONFLICT DO NOTHING
            """, (creator_id, slug))
            conn.commit()
        except:
            pass  # Silent fail if already exists

        cursor.close()
        conn.close()

        return jsonify(response), 200

    except Exception as e:
        print(f"Error unlocking brand {slug}: {str(e)}")
        return jsonify({'error': 'Failed to unlock brand data'}), 500


@public_bp.route('/categories', methods=['GET'])
def get_categories():
    """
    Public endpoint: Get all unique categories with brand counts
    Used for directory filters
    """
    try:
        conn = get_db_connection()
        cursor = conn.cursor(cursor_factory=RealDictCursor)

        cursor.execute("""
            SELECT
                category,
                COUNT(*) as brand_count
            FROM pr_brands
            WHERE category IS NOT NULL
            GROUP BY category
            ORDER BY brand_count DESC
        """)

        categories = cursor.fetchall()
        cursor.close()
        conn.close()

        return jsonify({
            'categories': [{
                'value': c['category'],
                'label': c['category'].replace('_', ' ').title(),
                'count': c['brand_count']
            } for c in categories]
        }), 200

    except Exception as e:
        print(f"Error fetching categories: {str(e)}")
        return jsonify({'error': 'Failed to fetch categories'}), 500


@public_bp.route('/sitemap.xml', methods=['GET'])
def get_sitemap():
    """
    Generate and serve sitemap.xml for all brand pages
    Public endpoint - no auth required
    """
    try:
        from datetime import datetime

        conn = get_db_connection()
        cursor = conn.cursor(cursor_factory=RealDictCursor)

        # Fetch all public brands with slugs
        cursor.execute('''
            SELECT slug, updated_at
            FROM brands
            WHERE slug IS NOT NULL
            AND is_public = true
            ORDER BY updated_at DESC
        ''')
        brands = cursor.fetchall()

        cursor.close()
        conn.close()

        # Build sitemap XML
        sitemap_xml = '''<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
'''

        # Add homepage
        sitemap_xml += f'''  <url>
    <loc>https://newcollab.co/</loc>
    <lastmod>{datetime.now().strftime('%Y-%m-%d')}</lastmod>
    <changefreq>daily</changefreq>
    <priority>1.0</priority>
  </url>
'''

        # Add directory page
        sitemap_xml += f'''  <url>
    <loc>https://newcollab.co/directory</loc>
    <lastmod>{datetime.now().strftime('%Y-%m-%d')}</lastmod>
    <changefreq>daily</changefreq>
    <priority>0.9</priority>
  </url>
'''

        # Add all brand pages
        for brand in brands:
            last_mod = brand['updated_at'].strftime('%Y-%m-%d') if brand.get('updated_at') else datetime.now().strftime('%Y-%m-%d')
            sitemap_xml += f'''  <url>
    <loc>https://newcollab.co/brand/{brand['slug']}</loc>
    <lastmod>{last_mod}</lastmod>
    <changefreq>weekly</changefreq>
    <priority>0.8</priority>
  </url>
'''

        sitemap_xml += '</urlset>'

        # Return XML with proper content type
        return Response(sitemap_xml, mimetype='application/xml')

    except Exception as e:
        print(f"Error generating sitemap: {str(e)}")
        return jsonify({'error': 'Failed to generate sitemap'}), 500


@public_bp.route('/submit-brands-to-indexnow', methods=['POST'])
def submit_all_brands_to_indexnow():
    """
    Submit all brand pages to IndexNow for indexing
    Can be called manually to update search engines

    Optional: Add CRON_SECRET header for authentication
    """
    # Optional authentication
    cron_secret = os.getenv('CRON_SECRET')
    if cron_secret:
        provided_secret = request.headers.get('X-Cron-Secret')
        if provided_secret != cron_secret:
            return jsonify({'error': 'Unauthorized'}), 401

    try:
        conn = get_db_connection()
        cursor = conn.cursor(cursor_factory=RealDictCursor)

        # Get all public brand slugs
        cursor.execute('''
            SELECT slug
            FROM brands
            WHERE slug IS NOT NULL
            AND is_public = true
        ''')
        brands = cursor.fetchall()

        cursor.close()
        conn.close()

        # Build URLs
        brand_urls = [f"https://newcollab.co/brand/{b['slug']}" for b in brands]

        # Add key pages
        key_pages = [
            'https://newcollab.co/',
            'https://newcollab.co/directory'
        ]

        all_urls = key_pages + brand_urls

        # Submit to IndexNow in batches (max 10,000 URLs per request)
        batch_size = 1000
        success_count = 0
        total_batches = (len(all_urls) + batch_size - 1) // batch_size

        for i in range(0, len(all_urls), batch_size):
            batch = all_urls[i:i + batch_size]
            if submit_to_indexnow(batch):
                success_count += len(batch)

        return jsonify({
            'success': True,
            'message': f'Submitted {success_count}/{len(all_urls)} URLs to IndexNow',
            'total_urls': len(all_urls),
            'brand_pages': len(brand_urls),
            'key_pages': len(key_pages),
            'batches': total_batches
        }), 200

    except Exception as e:
        print(f"Error submitting brands to IndexNow: {str(e)}")
        return jsonify({'error': 'Failed to submit to IndexNow'}), 500
