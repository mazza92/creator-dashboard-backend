"""
Public Routes for SEO-Optimized Brand Directory
No authentication required - open to Google crawlers
"""

from flask import Blueprint, request, jsonify, Response
from psycopg2.extras import RealDictCursor
from datetime import date, timedelta
import hashlib
import hmac
import os
import psycopg2
import requests

from brand_stats_synthesis import resolve_brand_stats, resolve_pitch_social_proof
from brand_categories import normalize_category, aggregate_category_counts, category_label

public_bp = Blueprint('public', __name__, url_prefix='/api/public')


# ── Unsubscribe token helpers ─────────────────────────────────────────────────

def _unsub_secret():
    return os.getenv('JWT_SECRET_KEY', os.getenv('SECRET_KEY', 'fallback-secret'))


def make_unsubscribe_token(user_id):
    """Generate a short HMAC token for one-click unsubscribe links."""
    msg = f"unsub:{user_id}".encode()
    return hmac.new(_unsub_secret().encode(), msg, hashlib.sha256).hexdigest()[:32]


def verify_unsubscribe_token(user_id, token):
    expected = make_unsubscribe_token(user_id)
    return hmac.compare_digest(expected, token)


@public_bp.route('/unsubscribe', methods=['GET'])
def unsubscribe():
    """
    One-click unsubscribe handler.
    Called by the link in every email footer: /api/public/unsubscribe?uid=<id>&token=<tok>
    Sets users.unsubscribed_at = NOW() so no future emails are sent.
    """
    user_id = request.args.get('uid')
    token = request.args.get('token')

    frontend_url = os.getenv('FRONTEND_URL', 'https://newcollab.co')

    if not user_id or not token:
        return jsonify({'error': 'Missing uid or token'}), 400

    if not verify_unsubscribe_token(user_id, token):
        return jsonify({'error': 'Invalid unsubscribe link'}), 403

    try:
        conn = get_db_connection()
        cursor = conn.cursor(cursor_factory=RealDictCursor)

        cursor.execute("""
            UPDATE users
            SET unsubscribed_at = NOW()
            WHERE id = %s AND unsubscribed_at IS NULL
            RETURNING id, email
        """, (user_id,))
        row = cursor.fetchone()
        conn.commit()
        cursor.close()
        conn.close()

        if row:
            print(f"[unsubscribe] {row['email']} (id={user_id}) unsubscribed")
        else:
            print(f"[unsubscribe] user {user_id} already unsubscribed or not found")

        # Redirect to a confirmation page on the frontend
        from flask import redirect
        return redirect(f"{frontend_url}/unsubscribed?status=ok", code=302)

    except Exception as e:
        print(f"[unsubscribe] error for user {user_id}: {e}")
        return jsonify({'error': 'Server error'}), 500


# ── End unsubscribe ───────────────────────────────────────────────────────────


def _estimate_package_value(category):
    """Estimate average PR package value by category."""
    category_values = {
        'beauty': 85,
        'skincare': 120,
        'fashion': 150,
        'wellness': 95,
        'fitness': 80,
        'food': 60,
        'lifestyle': 100,
        'tech': 200,
        'home': 130,
        'haircare': 90,
        'jewelry': 180,
        'pets': 70,
    }
    return category_values.get((category or '').lower(), 100)


def _format_public_brand_list_item(b):
    """Format brand for directory cards with synthetic stats when DB values are empty."""
    response_rate, avg_days = resolve_brand_stats(
        b['slug'], b['category'], b['response_rate'], b['avg_response_time_days']
    )
    pitch_count, response_count = resolve_pitch_social_proof(
        b['slug'], b.get('pitch_count'), b.get('response_count'), response_rate
    )

    # Calculate estimated package value
    estimated_value = _estimate_package_value(b['category'])

    # Get recent replies count (last 30 days) - use response_count as proxy
    recent_replies = response_count if response_count else 0

    return {
        'id': b['id'],
        'slug': b['slug'],
        'name': b['brand_name'],
        'logo': b['logo_url'],
        'website': b.get('website'),
        'description': b['description'][:200] if b['description'] else None,
        'category': b['category'],
        'niches': b['niches'],
        'minFollowers': b['min_followers'],
        'maxFollowers': b['max_followers'],
        'platforms': b['platforms'],
        'regions': b['regions'],
        'responseRate': response_rate,
        'avgResponseTime': avg_days,
        'isFeatured': b['is_featured'],
        'hasApplication': b['has_application_form'],
        'hasDirectLink': b['has_direct_link'],
        'hasEmailContact': b['has_email_contact'],
        'isNew': b['created_at'] and (b['created_at'].date() >= (date.today() - timedelta(days=7))) if b.get('created_at') else False,
        'pitchStats': {
            'totalPitches': pitch_count,
            'totalResponses': response_count,
        },
        # New fields for quick wins
        'estimatedValue': estimated_value,
        'recentReplies': recent_replies,
    }


def mask_email(email):
    """Mask email for teaser display: j***@nike.com"""
    if not email:
        return None
    try:
        local, domain = email.split('@')
        if len(local) <= 1:
            masked_local = local[0] + '***'
        else:
            masked_local = local[0] + '***'
        return f"{masked_local}@{domain}"
    except:
        return None


# IndexNow configuration
INDEXNOW_KEY = '5b821f1380424d116b8da378e4ca2f143a13f7236d7dd3db58d09cb3e0aeb736'
INDEXNOW_API_URL = 'https://api.indexnow.org/indexnow'

# CORS Configuration for Public Routes
ALLOWED_ORIGINS = [
    'http://localhost:3000',
    'https://newcollab.co',
    'https://www.newcollab.co',
    'https://api.newcollab.co'
]

@public_bp.after_request
def add_cors_headers(response):
    """Add CORS headers to all public route responses"""
    origin = request.headers.get('Origin')
    if origin in ALLOWED_ORIGINS:
        response.headers['Access-Control-Allow-Origin'] = origin
        response.headers['Access-Control-Allow-Methods'] = 'GET, POST, OPTIONS'
        response.headers['Access-Control-Allow-Headers'] = 'Content-Type, Authorization'
        response.headers['Access-Control-Allow-Credentials'] = 'true'
        response.headers['Access-Control-Max-Age'] = '3600'
    return response

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
    - search: search brand names
    - activity: filter by brand activity ('new', 'active', 'responsive')
    - contact_type: filter by contact availability ('application', 'email')
    - region: filter by region/country (e.g., 'Australia', 'US', 'UK', 'Canada')
    """
    try:
        page = int(request.args.get('page', 1))
        limit = min(int(request.args.get('limit', 24)), 100)  # Cap at 100
        offset = (page - 1) * limit

        category = request.args.get('category')
        niche = request.args.get('niche')
        search = request.args.get('search', '').strip()
        activity = request.args.get('activity')  # 'new', 'active', 'responsive'
        contact_type = request.args.get('contact_type')  # 'application', 'email'
        region = request.args.get('region')  # 'Australia', 'US', 'UK', 'Canada', etc.

        conn = get_db_connection()
        cursor = conn.cursor(cursor_factory=RealDictCursor)

        # Build query with filters (includes pitch stats from creator_pipeline)
        query = """
            SELECT
                b.id,
                b.slug,
                b.brand_name,
                b.logo_url,
                b.website,
                b.description,
                b.category,
                b.niches,
                b.min_followers,
                b.max_followers,
                b.platforms,
                b.regions,
                b.response_rate,
                b.avg_response_time_days,
                b.is_featured,
                b.has_application_form,
                b.created_at,
                CASE WHEN b.application_form_url IS NOT NULL THEN TRUE ELSE FALSE END as has_direct_link,
                CASE WHEN b.contact_email IS NOT NULL THEN TRUE ELSE FALSE END as has_email_contact,
                COALESCE(ps.pitch_count, 0) as pitch_count,
                COALESCE(ps.response_count, 0) as response_count
            FROM pr_brands b
            LEFT JOIN (
                SELECT
                    brand_id,
                    COUNT(*) FILTER (WHERE pitched_at IS NOT NULL) as pitch_count,
                    COUNT(*) FILTER (WHERE stage IN ('responded', 'accepted', 'received', 'shipped')) as response_count
                FROM creator_pipeline
                GROUP BY brand_id
            ) ps ON b.id = ps.brand_id
            WHERE (COALESCE(b.status, 'published') = 'published')
        """
        params = []

        if category:
            canon_category = normalize_category(category)
            if canon_category:
                query += " AND b.category = %s"
                params.append(canon_category)

        if niche:
            query += " AND %s = ANY(b.niches)"
            params.append(niche)

        if search:
            query += " AND b.brand_name ILIKE %s"
            params.append(f'%{search}%')

        # Activity filters
        if activity == 'new':
            # "Added recently" - no date filter, just sort by newest (handled in ORDER BY)
            pass  # Sorting handled below
        elif activity == 'active':
            # Brands actively accepting PR
            query += " AND b.accepting_pr = TRUE"
        elif activity == 'responsive':
            # High response rate (50% or higher)
            query += " AND b.response_rate >= 50"

        # Contact type filters
        if contact_type == 'application':
            # Has an application form URL
            query += " AND b.application_form_url IS NOT NULL"
        elif contact_type == 'email':
            # Has a contact email
            query += " AND b.contact_email IS NOT NULL"

        # Region filter (JSONB array contains)
        if region:
            query += " AND b.regions @> %s::jsonb"
            params.append(f'["{region}"]')

        # Order: depends on activity filter
        if activity == 'new':
            # "Added recently" - sort by newest first, ignore featured
            query += """
                ORDER BY
                    b.created_at DESC NULLS LAST,
                    b.brand_name ASC
                LIMIT %s OFFSET %s
            """
        else:
            # Default: Featured first, then most recently added
            query += """
                ORDER BY
                    b.is_featured DESC,
                    b.created_at DESC NULLS LAST,
                    b.brand_name ASC
                LIMIT %s OFFSET %s
            """
        params.extend([limit, offset])

        cursor.execute(query, params)
        brands = cursor.fetchall()

        # Get total count for pagination
        count_query = """
            SELECT COUNT(*) as total
            FROM pr_brands
            WHERE (COALESCE(status, 'published') = 'published')
        """
        count_params = []

        if category:
            canon_category = normalize_category(category)
            if canon_category:
                count_query += " AND category = %s"
                count_params.append(canon_category)
        if niche:
            count_query += " AND %s = ANY(niches)"
            count_params.append(niche)
        if search:
            count_query += " AND brand_name ILIKE %s"
            count_params.append(f'%{search}%')

        # Activity filters for count
        if activity == 'new':
            pass  # "Added recently" just sorts, doesn't filter
        elif activity == 'active':
            count_query += " AND accepting_pr = TRUE"
        elif activity == 'responsive':
            count_query += " AND response_rate >= 50"

        # Contact type filters for count
        if contact_type == 'application':
            count_query += " AND application_form_url IS NOT NULL"
        elif contact_type == 'email':
            count_query += " AND contact_email IS NOT NULL"

        # Region filter for count
        if region:
            count_query += " AND regions @> %s::jsonb"
            count_params.append(f'["{region}"]')

        cursor.execute(count_query, count_params)
        total_count = cursor.fetchone()['total']

        cursor.close()
        conn.close()

        # Format response (NO sensitive data like email or application URLs)
        return jsonify({
            'brands': [_format_public_brand_list_item(b) for b in brands],
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
                b.id,
                b.slug,
                b.brand_name,
                b.logo_url,
                b.website,
                b.description,
                b.instagram_handle,
                b.tiktok_handle,
                b.category,
                b.niches,
                b.product_types,
                b.min_followers,
                b.max_followers,
                b.platforms,
                b.regions,
                b.has_application_form,
                b.response_rate,
                b.avg_response_time_days,
                b.is_featured,
                b.application_method,
                b.seo_title,
                b.seo_description,
                b.contact_email,
                CASE
                    WHEN b.application_form_url IS NOT NULL THEN TRUE
                    ELSE FALSE
                END as has_direct_link,
                CASE
                    WHEN b.contact_email IS NOT NULL THEN TRUE
                    ELSE FALSE
                END as has_email_contact,
                COALESCE(ps.pitch_count, 0) as pitch_count,
                COALESCE(ps.response_count, 0) as response_count
            FROM pr_brands b
            LEFT JOIN (
                SELECT
                    brand_id,
                    COUNT(*) FILTER (WHERE pitched_at IS NOT NULL) as pitch_count,
                    COUNT(*) FILTER (WHERE stage IN ('responded', 'accepted', 'received', 'shipped')) as response_count
                FROM creator_pipeline
                GROUP BY brand_id
            ) ps ON b.id = ps.brand_id
            WHERE b.slug = %s AND (COALESCE(b.status, 'published') = 'published')
        """, (slug,))

        brand = cursor.fetchone()
        cursor.close()
        conn.close()

        if not brand:
            return jsonify({'error': 'Brand not found'}), 404

        response_rate, avg_days = resolve_brand_stats(
            brand['slug'], brand['category'], brand['response_rate'], brand['avg_response_time_days']
        )
        pitch_count, response_count = resolve_pitch_social_proof(
            brand['slug'], brand['pitch_count'], brand['response_count'], response_rate
        )

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
                'responseRate': response_rate,
                'avgResponseTime': avg_days,
                'totalPitches': pitch_count,
                'totalResponses': response_count,
            },
            'responseRate': response_rate,
            'avgResponseTime': avg_days,
            'isFeatured': brand['is_featured'],
            'applicationMethod': brand['application_method'],
            # Gated fields - tell frontend what's locked (show masked email to create desire)
            'gated': {
                'hasDirectLink': brand['has_direct_link'],
                'hasEmailContact': brand['has_email_contact'],
                'maskedEmail': mask_email(brand['contact_email']),  # Shows "j***@nike.com"
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

    Request body:
    - unlock_type: 'application' (FREE), 'contact' (PRO only), or 'all' (PRO only)

    Returns: Requested data based on unlock_type and user tier
    """
    from flask import session, current_app as app
    from datetime import date

    print(f"\n{'='*60}")
    print(f"🔓 UNLOCK ENDPOINT CALLED - Brand: {slug}")
    print(f"{'='*60}\n")

    try:
        # Get unlock type from request body
        data = request.get_json() or {}
        unlock_type = data.get('unlock_type', 'application')  # Default to application for backwards compatibility

        print(f"📋 Unlock type requested: {unlock_type}")

        # Check authentication
        user_id = session.get('user_id')
        creator_id = session.get('creator_id')

        if not user_id or not creator_id:
            return jsonify({'error': 'Authentication required'}), 401

        # Get user's subscription tier AND quota tracking
        conn = get_db_connection()
        cursor = conn.cursor(cursor_factory=RealDictCursor)

        cursor.execute("""
            SELECT subscription_tier, daily_unlocks_used, last_unlock_date
            FROM creators
            WHERE id = %s
        """, (creator_id,))

        creator = cursor.fetchone()
        if not creator:
            return jsonify({'error': 'Creator not found'}), 404

        tier = creator['subscription_tier'] or 'free'
        is_pro = tier in ['pro', 'elite']

        app.logger.info(f"🔍 Unlock request - Creator ID: {creator_id}, Tier: {tier}, Type: {unlock_type}")
        print(f"🔍 Unlock request - Creator ID: {creator_id}, Tier: {tier}, Type: {unlock_type}")

        # Check if user is trying to unlock contact email without PRO
        if unlock_type in ['contact', 'all'] and not is_pro:
            conn.close()
            return jsonify({
                'error': 'Pro subscription required to unlock contact emails',
                'upgrade_required': True
            }), 403

        # Check MONTHLY unlock limit for FREE users (only for application unlocks)
        if tier == 'free' and unlock_type == 'application':
            today = date.today()
            month_start = today.replace(day=1)
            last_unlock = creator.get('last_unlock_date')
            monthly_unlocks = creator.get('daily_unlocks_used', 0)  # Reusing column for monthly tracking

            app.logger.info(f"🔍 Free user quota check - Current: {monthly_unlocks}/5, Last unlock: {last_unlock}, Month start: {month_start}")
            print(f"🔍 Free user quota check - Current: {monthly_unlocks}/5, Last unlock: {last_unlock}, Month start: {month_start}")

            # Reset if it's a new month
            if last_unlock is None or last_unlock < month_start:
                monthly_unlocks = 0
                app.logger.info(f"🔄 Resetting monthly counter (new month or first unlock)")
                print(f"🔄 Resetting monthly counter (new month or first unlock)")

            # Check if limit reached - 5 unlocks per MONTH
            MONTHLY_LIMIT = 5
            if monthly_unlocks >= MONTHLY_LIMIT:
                app.logger.warning(f"🚫 Monthly quota limit reached for creator {creator_id}")
                print(f"🚫 Monthly quota limit reached for creator {creator_id}")
                conn.close()
                return jsonify({
                    'error': f"You've used all {MONTHLY_LIMIT} free brand unlocks this month. Upgrade to Pro for unlimited access!",
                    'upgrade_required': True,
                    'current_count': monthly_unlocks,
                    'limit': MONTHLY_LIMIT
                }), 403

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

        # Build response based on unlock_type
        response = {
            'brandName': brand['brand_name'],
            'applicationMethod': brand['application_method']
        }

        # Return data based on unlock_type
        if unlock_type == 'application':
            # Application URL only (FREE tier can access)
            if brand['application_form_url']:
                response['applicationUrl'] = brand['application_form_url']
            else:
                response['message'] = 'No public application form available for this brand.'

        elif unlock_type == 'contact':
            # Contact email only (PRO required - already checked above)
            if brand['contact_email']:
                response['contactEmail'] = brand['contact_email']
            else:
                response['message'] = 'No contact email available for this brand.'
            response['isPro'] = True

        elif unlock_type == 'all':
            # Both application URL and contact email (PRO required - already checked above)
            response['applicationUrl'] = brand['application_form_url']
            response['contactEmail'] = brand['contact_email']
            response['isPro'] = True

        # Auto-save to creator's pipeline
        # Check if brand was already saved before
        cursor.execute("""
            SELECT id FROM creator_pipeline
            WHERE creator_id = %s AND brand_id = (SELECT id FROM pr_brands WHERE slug = %s)
        """, (creator_id, slug))
        already_saved = cursor.fetchone() is not None

        cursor.execute("""
            INSERT INTO creator_pipeline (creator_id, brand_id, stage, created_at)
            SELECT %s, id, 'saved', NOW()
            FROM pr_brands
            WHERE slug = %s
            ON CONFLICT (creator_id, brand_id) DO UPDATE SET updated_at = NOW()
        """, (creator_id, slug))

        # Update counters based on tier and unlock type
        if tier == 'free' and unlock_type == 'application':
            # FREE users unlocking application: increment MONTHLY quota
            today = date.today()
            if not already_saved:
                cursor.execute('''
                    UPDATE creators
                    SET brands_saved_count = brands_saved_count + 1,
                        daily_unlocks_used = daily_unlocks_used + 1,
                        last_unlock_date = %s
                    WHERE id = %s
                ''', (today, creator_id))
                app.logger.info(f"✅ New brand saved - incremented both counters for creator {creator_id}")
                print(f"✅ New brand saved - incremented both counters for creator {creator_id}")
            else:
                cursor.execute('''
                    UPDATE creators
                    SET daily_unlocks_used = daily_unlocks_used + 1,
                        last_unlock_date = %s
                    WHERE id = %s
                ''', (today, creator_id))
                app.logger.info(f"✅ Re-unlocked existing brand - incremented monthly quota for creator {creator_id}")
                print(f"✅ Re-unlocked existing brand - incremented monthly quota for creator {creator_id}")
        elif is_pro:
            # PRO/ELITE users: only increment brands_saved_count if it's a new brand (no daily quota)
            if not already_saved:
                cursor.execute('''
                    UPDATE creators
                    SET brands_saved_count = brands_saved_count + 1
                    WHERE id = %s
                ''', (creator_id,))
                app.logger.info(f"✅ New brand saved - incremented brands_saved_count for PRO creator {creator_id}")
                print(f"✅ New brand saved - incremented brands_saved_count for PRO creator {creator_id}")

        conn.commit()

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
            WHERE (COALESCE(status, 'published') = 'published')
              AND category IS NOT NULL
              AND TRIM(category) != ''
            GROUP BY category
        """)

        rows = cursor.fetchall()
        cursor.close()
        conn.close()

        return jsonify({
            'categories': aggregate_category_counts(rows)
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
