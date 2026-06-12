"""
Admin API Routes for Brand Management
CRM-style CRUD operations for PR directory brands
"""

from flask import Blueprint, request, jsonify, session
from functools import wraps
from psycopg2.extras import RealDictCursor, Json
from datetime import datetime
import sys
import os
import re
import json

# Add parent directory for imports
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import psycopg2

def get_db_connection():
    """Get database connection"""
    return psycopg2.connect(os.getenv('DATABASE_URL'), cursor_factory=RealDictCursor)


# Create Blueprint
admin_brands_bp = Blueprint('admin_brands', __name__, url_prefix='/api/admin')


# ============================================================================
# AUTHENTICATION DECORATOR
# ============================================================================

def admin_required(f):
    """
    Decorator to require admin authentication
    Accepts X-Admin-Token header with valid token
    """
    @wraps(f)
    def decorated_function(*args, **kwargs):
        admin_token = request.headers.get('X-Admin-Token')
        if admin_token == 'pr-hunter-admin-2026':
            return f(*args, **kwargs)

        # Check session-based auth as fallback
        user_id = session.get('user_id')
        if not user_id:
            return jsonify({'error': 'Authentication required'}), 401

        try:
            conn = get_db_connection()
            cursor = conn.cursor(cursor_factory=RealDictCursor)
            cursor.execute('SELECT email FROM users WHERE id = %s', (user_id,))
            user = cursor.fetchone()
            conn.close()

            if not user or user.get('email', '').lower() != 'team@newcollab.co':
                return jsonify({'error': 'Admin access required'}), 403

        except Exception as e:
            return jsonify({'error': str(e)}), 500

        return f(*args, **kwargs)
    return decorated_function


# ============================================================================
# UTILITY FUNCTIONS
# ============================================================================

def create_slug(brand_name: str) -> str:
    """Create URL-friendly slug from brand name"""
    slug = brand_name.lower()
    slug = re.sub(r'[^a-z0-9]+', '-', slug)
    slug = slug.strip('-')
    return slug


# ============================================================================
# BRAND CRUD ENDPOINTS
# ============================================================================

@admin_brands_bp.route('/brands', methods=['GET'])
@admin_required
def get_brands():
    """
    Get all brands with optional filtering

    Query Params:
        page: Page number (default 1)
        limit: Results per page (default 100)
        status: Filter by status (draft, published)
        search: Search by name
        category: Filter by category

    Returns:
        { "brands": [...], "pagination": {...} }
    """
    try:
        page = int(request.args.get('page', 1))
        limit = int(request.args.get('limit', 10000))  # No limit for admin - fetch all brands
        status_filter = request.args.get('status')
        search = request.args.get('search')
        category = request.args.get('category')

        offset = (page - 1) * limit

        conn = get_db_connection()
        cursor = conn.cursor(cursor_factory=RealDictCursor)

        # Build query - fetch ALL columns from pr_brands
        query = """
            SELECT
                id, slug, brand_name as name, logo_url as logo, website,
                description, instagram_handle as instagram, tiktok_handle as tiktok,
                youtube_handle as youtube,
                category, niches, product_types,
                min_followers, max_followers, platforms, regions,
                application_form_url as application_url, contact_email,
                response_rate, avg_response_time_days,
                total_applications, total_responses, last_verified_at,
                is_featured, is_premium, has_application_form,
                application_method, application_requirements,
                accepting_pr, open_pr_featured, roundup_featured, notes, success_stories, source_url,
                cover_image_url, avg_product_value, collaboration_type, payment_offered,
                seo_title, seo_description,
                hero_product, target_audience, tone, price_point, enriched_at,
                COALESCE(status, 'published') as status,
                created_at, updated_at
            FROM pr_brands
            WHERE (COALESCE(status, 'published') = 'published' OR status = 'draft')
        """
        params = []

        if status_filter:
            query += " AND COALESCE(status, 'published') = %s"
            params.append(status_filter)

        if search:
            query += " AND brand_name ILIKE %s"
            params.append(f'%{search}%')

        if category:
            from brand_categories import normalize_category
            canon = normalize_category(category)
            if canon:
                query += " AND category = %s"
                params.append(canon)

        # Count total
        count_query = query.replace(
            "SELECT\n                id, slug, brand_name as name",
            "SELECT COUNT(*) as total"
        ).split("FROM pr_brands")[0] + "FROM pr_brands" + query.split("FROM pr_brands")[1].split("ORDER BY")[0]

        cursor.execute(f"SELECT COUNT(*) as total FROM pr_brands WHERE 1=1" +
                      (" AND COALESCE(status, 'published') = %s" if status_filter else "") +
                      (" AND brand_name ILIKE %s" if search else "") +
                      (" AND category = %s" if category else ""),
                      params)
        total = cursor.fetchone()['total']

        # Get brands
        query += " ORDER BY created_at DESC NULLS LAST LIMIT %s OFFSET %s"
        params.extend([limit, offset])

        cursor.execute(query, params)
        brands = cursor.fetchall()

        conn.close()

        return jsonify({
            'brands': brands,
            'pagination': {
                'page': page,
                'limit': limit,
                'total': total
            }
        }), 200

    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({'error': str(e)}), 500


@admin_brands_bp.route('/brands/<int:brand_id>', methods=['GET'])
@admin_required
def get_brand(brand_id):
    """Get single brand by ID"""
    try:
        conn = get_db_connection()
        cursor = conn.cursor(cursor_factory=RealDictCursor)

        cursor.execute("""
            SELECT
                id, slug, brand_name as name, logo_url as logo, website,
                description, instagram_handle as instagram, tiktok_handle as tiktok,
                youtube_handle as youtube,
                category, niches, product_types,
                min_followers, max_followers, platforms, regions,
                application_form_url as application_url, contact_email,
                response_rate, avg_response_time_days,
                total_applications, total_responses, last_verified_at,
                is_featured, is_premium, has_application_form,
                application_method, application_requirements,
                accepting_pr, open_pr_featured, roundup_featured, notes, success_stories, source_url,
                cover_image_url, avg_product_value, collaboration_type, payment_offered,
                seo_title, seo_description,
                hero_product, target_audience, tone, price_point, enriched_at,
                COALESCE(status, 'published') as status,
                created_at, updated_at
            FROM pr_brands
            WHERE id = %s
        """, (brand_id,))

        brand = cursor.fetchone()
        conn.close()

        if not brand:
            return jsonify({'error': 'Brand not found'}), 404

        return jsonify(brand), 200

    except Exception as e:
        return jsonify({'error': str(e)}), 500


@admin_brands_bp.route('/brands', methods=['POST'])
@admin_required
def create_brand():
    """
    Create a new brand

    Request Body:
        {
            "name": "Brand Name",
            "slug": "brand-name",
            "category": "skincare",
            "status": "draft",
            ...
        }

    Returns:
        { "brand": {...}, "message": "Brand created" }
    """
    try:
        data = request.get_json()

        name = data.get('name', 'New Brand')
        slug = data.get('slug') or create_slug(name)

        conn = get_db_connection()
        cursor = conn.cursor(cursor_factory=RealDictCursor)

        # Check if slug exists
        cursor.execute('SELECT id FROM pr_brands WHERE slug = %s', (slug,))
        if cursor.fetchone():
            slug = f"{slug}-{int(datetime.now().timestamp())}"

        # Insert brand with ALL columns
        cursor.execute("""
            INSERT INTO pr_brands (
                brand_name, slug, logo_url, website, description,
                instagram_handle, tiktok_handle, youtube_handle,
                category, niches, product_types,
                min_followers, max_followers, platforms, regions,
                application_form_url, contact_email,
                response_rate, avg_response_time_days,
                is_featured, is_premium, has_application_form,
                application_method, application_requirements,
                accepting_pr, open_pr_featured, roundup_featured, notes, success_stories, source_url,
                cover_image_url, avg_product_value, collaboration_type, payment_offered,
                seo_title, seo_description,
                status, created_at
            ) VALUES (
                %s, %s, %s, %s, %s,
                %s, %s, %s,
                %s, %s, %s,
                %s, %s, %s, %s,
                %s, %s,
                %s, %s,
                %s, %s, %s,
                %s, %s,
                %s, %s, %s, %s, %s, %s,
                %s, %s, %s, %s,
                %s, %s,
                %s, CURRENT_TIMESTAMP
            )
            RETURNING id
        """, (
            name,
            slug,
            data.get('logo'),
            data.get('website'),
            data.get('description'),
            data.get('instagram'),
            data.get('tiktok'),
            data.get('youtube'),
            data.get('category', 'other'),
            Json(data.get('niches', [])),  # JSONB column
            data.get('product_types'),
            data.get('min_followers', 0),
            data.get('max_followers'),
            Json(data.get('platforms', [])),  # JSONB column
            Json(data.get('regions', ['Worldwide'])),  # JSONB column
            data.get('application_url'),
            data.get('contact_email'),
            data.get('response_rate'),
            data.get('avg_response_time_days'),
            data.get('is_featured', False),
            data.get('is_premium', False),
            data.get('has_application_form', False),
            data.get('application_method'),
            data.get('application_requirements'),
            data.get('accepting_pr', True),
            data.get('open_pr_featured', False),
            data.get('roundup_featured', False),
            data.get('notes'),
            data.get('success_stories'),
            data.get('source_url'),
            data.get('cover_image_url'),
            data.get('avg_product_value'),
            data.get('collaboration_type'),
            data.get('payment_offered'),
            data.get('seo_title'),
            data.get('seo_description'),
            data.get('status', 'draft')
        ))

        brand_id = cursor.fetchone()['id']

        # Get the created brand
        cursor.execute("""
            SELECT
                id, slug, brand_name as name, logo_url as logo, website,
                description, instagram_handle as instagram, tiktok_handle as tiktok,
                youtube_handle as youtube,
                category, niches, product_types,
                min_followers, max_followers, platforms, regions,
                application_form_url as application_url, contact_email,
                response_rate, avg_response_time_days,
                total_applications, total_responses, last_verified_at,
                is_featured, is_premium, has_application_form,
                application_method, application_requirements,
                accepting_pr, open_pr_featured, roundup_featured, notes, success_stories, source_url,
                cover_image_url, avg_product_value, collaboration_type, payment_offered,
                seo_title, seo_description,
                hero_product, target_audience, tone, price_point, enriched_at,
                COALESCE(status, 'published') as status,
                created_at, updated_at
            FROM pr_brands
            WHERE id = %s
        """, (brand_id,))

        brand = cursor.fetchone()

        conn.commit()
        conn.close()

        return jsonify({
            'brand': brand,
            'message': 'Brand created successfully'
        }), 201

    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({'error': str(e)}), 500


@admin_brands_bp.route('/brands/<int:brand_id>', methods=['PATCH'])
@admin_required
def update_brand(brand_id):
    """
    Update brand fields

    Request Body:
        {
            "name": "Updated Name",
            "category": "beauty",
            ...
        }
    """
    try:
        data = request.get_json()

        # Map frontend field names to database columns
        field_mapping = {
            'name': 'brand_name',
            'logo': 'logo_url',
            'instagram': 'instagram_handle',
            'tiktok': 'tiktok_handle',
            'youtube': 'youtube_handle',
            'application_url': 'application_form_url',
            'accepting_pr': 'accepting_pr',
            'is_featured': 'is_featured',
            'seo': None,  # Handle nested SEO object
        }

        # Direct fields (same name in frontend and backend)
        direct_fields = [
            'slug', 'website', 'description', 'category', 'niches',
            'product_types', 'min_followers', 'max_followers',
            'platforms', 'regions', 'contact_email',
            'response_rate', 'avg_response_time_days',
            'total_applications', 'total_responses', 'last_verified_at',
            'has_application_form', 'application_method', 'application_requirements',
            'is_premium', 'open_pr_featured', 'roundup_featured', 'notes', 'success_stories', 'source_url',
            'cover_image_url', 'avg_product_value', 'collaboration_type', 'payment_offered',
            'status', 'seo_title', 'seo_description',
            'hero_product', 'target_audience', 'tone', 'price_point', 'enriched_at'
        ]

        update_fields = []
        params = []

        for key, value in data.items():
            if key == 'seo' and isinstance(value, dict):
                # Handle nested SEO object
                if 'title' in value:
                    update_fields.append('seo_title = %s')
                    params.append(value['title'])
                if 'description' in value:
                    update_fields.append('seo_description = %s')
                    params.append(value['description'])
            elif key in field_mapping and field_mapping[key]:
                update_fields.append(f"{field_mapping[key]} = %s")
                params.append(value)
            elif key in direct_fields:
                if key == 'category' and value:
                    from brand_categories import normalize_category
                    value = normalize_category(value)
                update_fields.append(f"{key} = %s")
                params.append(value)

        if not update_fields:
            return jsonify({'error': 'No valid fields to update'}), 400

        params.append(brand_id)

        conn = get_db_connection()
        cursor = conn.cursor(cursor_factory=RealDictCursor)

        query = f"""
            UPDATE pr_brands
            SET {", ".join(update_fields)}, updated_at = CURRENT_TIMESTAMP
            WHERE id = %s
            RETURNING id
        """

        cursor.execute(query, params)
        result = cursor.fetchone()

        if not result:
            conn.close()
            return jsonify({'error': 'Brand not found'}), 404

        # Get updated brand
        cursor.execute("""
            SELECT
                id, slug, brand_name as name, logo_url as logo, website,
                description, instagram_handle as instagram, tiktok_handle as tiktok,
                youtube_handle as youtube,
                category, niches, product_types,
                min_followers, max_followers, platforms, regions,
                application_form_url as application_url, contact_email,
                response_rate, avg_response_time_days,
                total_applications, total_responses, last_verified_at,
                is_featured, is_premium, has_application_form,
                application_method, application_requirements,
                accepting_pr, open_pr_featured, roundup_featured, notes, success_stories, source_url,
                cover_image_url, avg_product_value, collaboration_type, payment_offered,
                seo_title, seo_description,
                hero_product, target_audience, tone, price_point, enriched_at,
                COALESCE(status, 'published') as status,
                created_at, updated_at
            FROM pr_brands
            WHERE id = %s
        """, (brand_id,))

        brand = cursor.fetchone()

        conn.commit()
        conn.close()

        return jsonify(brand), 200

    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({'error': str(e)}), 500


@admin_brands_bp.route('/brands/<int:brand_id>', methods=['DELETE'])
@admin_required
def delete_brand(brand_id):
    """Delete a brand"""
    try:
        conn = get_db_connection()
        cursor = conn.cursor()

        cursor.execute('DELETE FROM pr_brands WHERE id = %s RETURNING id', (brand_id,))
        result = cursor.fetchone()

        conn.commit()
        conn.close()

        if not result:
            return jsonify({'error': 'Brand not found'}), 404

        return jsonify({'success': True, 'deleted_id': brand_id}), 200

    except Exception as e:
        return jsonify({'error': str(e)}), 500


# ============================================================================
# BULK OPERATIONS
# ============================================================================

@admin_brands_bp.route('/brands/bulk-import', methods=['POST'])
@admin_required
def bulk_import_brands():
    """
    Bulk import brands from spreadsheet paste

    Request Body:
        {
            "brands": [
                {"name": "Brand 1", "website": "...", "category": "..."},
                {"name": "Brand 2", ...}
            ]
        }

    Returns:
        { "imported": 5, "failed": 0, "brands": [...] }
    """
    try:
        data = request.get_json()
        brands_data = data.get('brands', [])

        if not brands_data:
            return jsonify({'error': 'No brands to import'}), 400

        conn = get_db_connection()
        cursor = conn.cursor(cursor_factory=RealDictCursor)

        imported_count = 0
        failed_count = 0
        imported_brands = []

        for brand_data in brands_data:
            try:
                name = brand_data.get('name', 'Unknown Brand')
                slug = brand_data.get('slug') or create_slug(name)

                # Check if slug exists, make unique if needed
                cursor.execute('SELECT id FROM pr_brands WHERE slug = %s', (slug,))
                if cursor.fetchone():
                    slug = f"{slug}-{int(datetime.now().timestamp())}"

                cursor.execute("""
                    INSERT INTO pr_brands (
                        brand_name, slug, website, category,
                        min_followers, application_form_url,
                        status, created_at
                    ) VALUES (
                        %s, %s, %s, %s, %s, %s, 'draft', CURRENT_TIMESTAMP
                    )
                    RETURNING id, brand_name as name, slug
                """, (
                    name,
                    slug,
                    brand_data.get('website'),
                    brand_data.get('category', 'other'),
                    brand_data.get('min_followers', 0),
                    brand_data.get('application_url')
                ))

                brand = cursor.fetchone()
                imported_brands.append(brand)
                imported_count += 1

            except Exception as e:
                print(f"Failed to import {brand_data.get('name')}: {str(e)}")
                failed_count += 1
                conn.rollback()
                continue

        conn.commit()
        conn.close()

        return jsonify({
            'imported': imported_count,
            'failed': failed_count,
            'brands': imported_brands
        }), 200

    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({'error': str(e)}), 500


@admin_brands_bp.route('/brands/bulk-update', methods=['POST'])
@admin_required
def bulk_update_brands():
    """
    Bulk update multiple brands

    Request Body:
        {
            "ids": [1, 2, 3],
            "updates": { "status": "published" }
        }

    Returns:
        { "updated": 3 }
    """
    try:
        data = request.get_json()
        brand_ids = data.get('ids', [])
        updates = data.get('updates', {})

        if not brand_ids or not updates:
            return jsonify({'error': 'ids and updates are required'}), 400

        # Build update query
        allowed_fields = ['status', 'category', 'is_featured', 'accepting_pr', 'open_pr_featured', 'roundup_featured']
        update_fields = []
        params = []

        for field, value in updates.items():
            if field in allowed_fields:
                update_fields.append(f"{field} = %s")
                params.append(value)

        if not update_fields:
            return jsonify({'error': 'No valid fields to update'}), 400

        params.append(brand_ids)

        conn = get_db_connection()
        cursor = conn.cursor()

        query = f"""
            UPDATE pr_brands
            SET {", ".join(update_fields)}, updated_at = CURRENT_TIMESTAMP
            WHERE id = ANY(%s)
        """

        cursor.execute(query, params)
        updated_count = cursor.rowcount

        conn.commit()
        conn.close()

        return jsonify({'updated': updated_count}), 200

    except Exception as e:
        return jsonify({'error': str(e)}), 500


# ============================================================================
# STATS ENDPOINT
# ============================================================================

@admin_brands_bp.route('/brands/stats', methods=['GET'])
@admin_required
def get_brand_stats():
    """
    Get brand statistics

    Returns:
        {
            "total": 150,
            "published": 120,
            "draft": 30,
            "by_category": {...}
        }
    """
    try:
        conn = get_db_connection()
        cursor = conn.cursor(cursor_factory=RealDictCursor)

        # Overall stats
        cursor.execute("""
            SELECT
                COUNT(*) as total,
                SUM(CASE WHEN COALESCE(status, 'published') = 'published' THEN 1 ELSE 0 END) as published,
                SUM(CASE WHEN status = 'draft' THEN 1 ELSE 0 END) as draft,
                SUM(CASE WHEN is_featured = TRUE THEN 1 ELSE 0 END) as featured
            FROM pr_brands
        """)
        stats = cursor.fetchone()

        # By category
        cursor.execute("""
            SELECT category, COUNT(*) as count
            FROM pr_brands
            GROUP BY category
            ORDER BY count DESC
        """)
        by_category = {row['category']: row['count'] for row in cursor.fetchall()}

        conn.close()

        return jsonify({
            'total': stats['total'],
            'published': stats['published'],
            'draft': stats['draft'],
            'featured': stats['featured'],
            'by_category': by_category
        }), 200

    except Exception as e:
        return jsonify({'error': str(e)}), 500


# ============================================================================
# PUBLIC ENDPOINT - Open PR Featured Brands
# ============================================================================

@admin_brands_bp.route('/brands/open-pr-featured', methods=['GET'])
def get_open_pr_featured_brands():
    """
    Public endpoint to get brands flagged for Open PR Applications section.
    No authentication required - used by public directory page.

    Returns:
        { "brands": [...] }
    """
    try:
        conn = get_db_connection()
        cursor = conn.cursor(cursor_factory=RealDictCursor)

        cursor.execute("""
            SELECT
                id, slug, brand_name as name, logo_url as logo, website,
                category, application_form_url as application_url,
                has_application_form, application_method
            FROM pr_brands
            WHERE open_pr_featured = TRUE
              AND COALESCE(status, 'published') = 'published'
            ORDER BY brand_name ASC
        """)

        brands = cursor.fetchall()
        conn.close()

        return jsonify({'brands': brands}), 200

    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({'error': str(e)}), 500


# ============================================================================
# ROUNDUP FEATURED ENDPOINT - Weekly Email Roundup
# ============================================================================

@admin_brands_bp.route('/brands/roundup-featured', methods=['GET'])
@admin_required
def get_roundup_featured_brands():
    """
    Admin endpoint to get brands tagged for the next weekly email roundup.
    Returns full brand data needed to populate the WeeklyBrandRoundup email template.

    Returns:
        { "brands": [...], "count": N }
    """
    try:
        conn = get_db_connection()
        cursor = conn.cursor(cursor_factory=RealDictCursor)

        cursor.execute("""
            SELECT
                id, slug, brand_name as name, logo_url as logo, website,
                description, category, niches, product_types,
                min_followers, max_followers, platforms, regions,
                application_form_url as application_url,
                has_application_form, application_method,
                accepting_pr, avg_product_value, collaboration_type,
                cover_image_url, roundup_featured
            FROM pr_brands
            WHERE roundup_featured = TRUE
              AND COALESCE(status, 'published') = 'published'
            ORDER BY updated_at DESC NULLS LAST
        """)

        brands = cursor.fetchall()
        conn.close()

        return jsonify({'brands': brands, 'count': len(brands)}), 200

    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({'error': str(e)}), 500


# ============================================================================
# WEBSITE SCRAPER - Auto-fill from OpenGraph
# ============================================================================

@admin_brands_bp.route('/brands/scrape-og', methods=['POST'])
@admin_required
def scrape_opengraph():
    """
    Scrape OpenGraph metadata from a website URL.
    Used to auto-fill brand details (name, description, cover image).

    Request Body:
        { "url": "https://example.com" }

    Returns:
        {
            "success": true,
            "title": "...",
            "description": "...",
            "image": "...",
            "instagram": "..." (if found)
        }
    """
    import requests
    from bs4 import BeautifulSoup
    import re

    try:
        data = request.get_json()
        url = data.get('url')

        if not url:
            return jsonify({'error': 'URL is required'}), 400

        # Ensure URL has protocol
        if not url.startswith('http'):
            url = f'https://{url}'

        # Fetch the page
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
        }
        response = requests.get(url, headers=headers, timeout=10, allow_redirects=True)
        response.raise_for_status()

        soup = BeautifulSoup(response.text, 'html.parser')

        result = {'success': True}

        # Extract OpenGraph title
        og_title = soup.find('meta', property='og:title')
        if og_title and og_title.get('content'):
            result['title'] = og_title['content'].strip()
        else:
            title_tag = soup.find('title')
            if title_tag:
                result['title'] = title_tag.get_text().strip()

        # Extract OpenGraph description
        og_desc = soup.find('meta', property='og:description')
        if og_desc and og_desc.get('content'):
            result['description'] = og_desc['content'].strip()
        else:
            meta_desc = soup.find('meta', attrs={'name': 'description'})
            if meta_desc and meta_desc.get('content'):
                result['description'] = meta_desc['content'].strip()

        # Extract OpenGraph image
        og_image = soup.find('meta', property='og:image')
        if og_image and og_image.get('content'):
            img_url = og_image['content']
            # Handle relative URLs
            if img_url.startswith('/'):
                from urllib.parse import urlparse
                parsed = urlparse(url)
                img_url = f"{parsed.scheme}://{parsed.netloc}{img_url}"
            result['image'] = img_url

        # Try to find Instagram handle
        instagram_patterns = [
            r'instagram\.com/([a-zA-Z0-9_.]+)',
            r'@([a-zA-Z0-9_.]+)\s*(?:on\s+)?instagram',
        ]
        page_text = response.text.lower()
        for pattern in instagram_patterns:
            match = re.search(pattern, page_text, re.IGNORECASE)
            if match:
                handle = match.group(1)
                # Filter out common false positives
                if handle not in ['p', 'reel', 'stories', 'explore', 'accounts']:
                    result['instagram'] = handle
                    break

        return jsonify(result), 200

    except requests.exceptions.Timeout:
        return jsonify({'success': False, 'error': 'Request timed out'}), 200
    except requests.exceptions.RequestException as e:
        return jsonify({'success': False, 'error': str(e)}), 200
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({'success': False, 'error': str(e)}), 200


# ============================================================================
# AI ENRICHMENT ENDPOINT
# ============================================================================

@admin_brands_bp.route('/brands/<int:brand_id>/enrich', methods=['POST'])
@admin_required
def enrich_brand(brand_id):
    """
    AI-enrich a single brand using Jina AI + Claude Haiku
    Extracts hero_product, target_audience, tone, price_point from website

    Returns:
        { "success": true, "data": {...} } on success
        { "success": false, "error": "..." } on failure
    """
    import requests as req
    import json as jsonlib

    ANTHROPIC_API_KEY = os.getenv('ANTHROPIC_API_KEY')
    if not ANTHROPIC_API_KEY:
        return jsonify({'success': False, 'error': 'ANTHROPIC_API_KEY not configured'}), 500

    try:
        conn = get_db_connection()
        cursor = conn.cursor(cursor_factory=RealDictCursor)

        # Get brand details
        cursor.execute('SELECT id, brand_name, website, category FROM pr_brands WHERE id = %s', (brand_id,))
        brand = cursor.fetchone()

        if not brand:
            conn.close()
            return jsonify({'success': False, 'error': 'Brand not found'}), 404

        if not brand.get('website'):
            conn.close()
            return jsonify({'success': False, 'error': 'Brand has no website URL'}), 400

        website = brand['website']
        if not website.startswith('http'):
            website = f'https://{website}'

        # Step 1: Fetch page content via Jina AI
        try:
            jina_res = req.get(f"https://r.jina.ai/{website}", timeout=15, headers={
                'User-Agent': 'Mozilla/5.0 (compatible; BrandEnricher/1.0)'
            })
            if jina_res.status_code != 200:
                conn.close()
                return jsonify({'success': False, 'error': f'Could not fetch website (status {jina_res.status_code})'}), 200

            page_content = jina_res.text[:6000]  # Cap for token limits
        except req.Timeout:
            conn.close()
            return jsonify({'success': False, 'error': 'Website fetch timed out'}), 200
        except Exception as e:
            conn.close()
            return jsonify({'success': False, 'error': f'Website fetch failed: {str(e)}'}), 200

        # Step 2: Extract data with Claude Haiku
        category_hint = f"\nBrand category hint: {brand.get('category')}" if brand.get('category') else ""

        prompt = f"""You are extracting structured data from a brand's website content.

Brand: {brand['brand_name']}{category_hint}

Website content:
{page_content}

Extract the following as JSON:
{{
  "hero_product": "their most well-known or bestselling product with SPECIFIC name (e.g. 'Peptide Glazing Fluid', 'Power Leggings', 'Grass-Fed Whey Protein'). If unclear, use their primary product category with a descriptor.",
  "target_audience": "who they sell to in under 12 words (e.g. 'women 25-40 into clean beauty and skincare')",
  "tone": "one of: premium / casual / wellness / functional / luxury / playful / minimalist / bold",
  "price_point": "estimated average single product price in USD as integer (e.g. 32, 85). Use 0 if unclear.",
  "description": "one sentence max 25 words: [Brand] makes [specific product type] for [specific customer]."
}}

Return ONLY valid JSON, no explanation.

JSON:"""

        try:
            claude_res = req.post(
                "https://api.anthropic.com/v1/messages",
                headers={
                    "x-api-key": ANTHROPIC_API_KEY,
                    "anthropic-version": "2023-06-01",
                    "content-type": "application/json"
                },
                json={
                    "model": "claude-haiku-4-5-20251001",
                    "max_tokens": 400,
                    "messages": [{"role": "user", "content": prompt}]
                },
                timeout=30
            )

            if claude_res.status_code != 200:
                conn.close()
                return jsonify({'success': False, 'error': f'AI extraction failed (status {claude_res.status_code})'}), 200

            response_data = claude_res.json()
            text = response_data.get("content", [{}])[0].get("text", "")

            # Clean up response
            text = text.strip()
            if text.startswith("```"):
                text = text.split("```")[1]
                if text.startswith("json"):
                    text = text[4:]
            text = text.strip()

            data = jsonlib.loads(text)

        except jsonlib.JSONDecodeError:
            conn.close()
            return jsonify({'success': False, 'error': 'AI returned invalid JSON'}), 200
        except req.Timeout:
            conn.close()
            return jsonify({'success': False, 'error': 'AI extraction timed out'}), 200
        except Exception as e:
            conn.close()
            return jsonify({'success': False, 'error': f'AI extraction failed: {str(e)}'}), 200

        # Step 3: Update database
        hero_product = data.get('hero_product')
        target_audience = data.get('target_audience')
        tone_raw = data.get('tone')
        price_point_raw = data.get('price_point')
        description = data.get('description')

        # Validate/normalize tone (must be one of valid options, max 50 chars)
        valid_tones = ['premium', 'casual', 'wellness', 'functional', 'luxury', 'playful', 'minimalist', 'bold']
        tone = None
        if tone_raw:
            tone_lower = tone_raw.lower().strip()
            for valid in valid_tones:
                if valid in tone_lower:
                    tone = valid
                    break
            if not tone:
                tone = tone_raw[:50] if len(tone_raw) > 50 else tone_raw

        # Truncate fields to avoid DB errors
        if hero_product and len(hero_product) > 255:
            hero_product = hero_product[:255]
        if target_audience and len(target_audience) > 255:
            target_audience = target_audience[:255]

        # Convert price_point to int (AI may return string or int)
        price_point = None
        if price_point_raw:
            try:
                price_point = int(price_point_raw)
                if price_point <= 0:
                    price_point = None
            except (ValueError, TypeError):
                price_point = None

        cursor.execute('''
            UPDATE pr_brands
            SET hero_product = %s,
                target_audience = %s,
                tone = %s,
                price_point = %s,
                description = COALESCE(description, %s),
                enriched_at = CURRENT_TIMESTAMP
            WHERE id = %s
        ''', (
            hero_product,
            target_audience,
            tone,
            price_point,
            description,
            brand_id
        ))

        conn.commit()
        conn.close()

        return jsonify({
            'success': True,
            'data': {
                'hero_product': hero_product,
                'target_audience': target_audience,
                'tone': tone,
                'price_point': price_point,
                'description': description
            }
        }), 200

    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({'success': False, 'error': str(e)}), 500
