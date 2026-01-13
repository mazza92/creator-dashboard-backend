"""
Public Routes for SEO-Optimized Brand Directory
No authentication required - open to Google crawlers
"""

from flask import Blueprint, request, jsonify
from psycopg2.extras import RealDictCursor
import os
import psycopg2

public_bp = Blueprint('public', __name__, url_prefix='/api/public')

def get_db_connection():
    """Get database connection"""
    return psycopg2.connect(
        host=os.getenv('DB_HOST'),
        port=os.getenv('DB_PORT', 5432),
        database=os.getenv('DB_NAME'),
        user=os.getenv('DB_USER'),
        password=os.getenv('DB_PASSWORD')
    )

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
