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
from datetime import datetime

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

        # Determine limits based on tier - FREE users get 3 pitches per MONTH
        FREE_MONTHLY_LIMIT = 3
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

        # Check if limit reached for free users - 3 pitches per MONTH
        FREE_MONTHLY_LIMIT = 3
        is_pro = tier in ['pro', 'elite']

        if not is_pro and pitches_used >= FREE_MONTHLY_LIMIT:
            cursor.close()
            conn.close()
            return jsonify({
                'success': False,
                'error': 'Monthly pitch limit reached. Upgrade to Pro for unlimited pitches!',
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

        # Update pipeline stage to 'pitched' if in pipeline
        cursor.execute('''
            INSERT INTO creator_pipeline (creator_id, brand_id, stage, pitched_at, created_at, updated_at)
            VALUES (%s, %s, 'pitched', NOW(), NOW(), NOW())
            ON CONFLICT (creator_id, brand_id) DO UPDATE
            SET stage = 'pitched', pitched_at = NOW(), updated_at = NOW()
        ''', (creator_id, brand_id))

        # Set first_pitch_sent_at if this is their first pitch (for email conversion sequence)
        cursor.execute('''
            UPDATE creators
            SET first_pitch_sent_at = NOW()
            WHERE id = %s AND first_pitch_sent_at IS NULL
        ''', (creator_id,))

        conn.commit()
        cursor.close()
        conn.close()

        return jsonify({
            'success': True,
            'message': 'Pitch tracked successfully',
            'pitches_used': pitches_used + 1,
            'tier': tier
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

        # Get creator profile
        cursor.execute('''
            SELECT c.*, u.first_name, u.last_name, u.email
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

        return jsonify({
            'success': True,
            **pitch,
            'brand_email': brand.get('contact_email'),
            'brand_name': brand.get('brand_name'),
            'brand_logo': brand.get('logo_url'),
            'application_form_url': brand.get('application_form_url')
        })

    except Exception as e:
        print(f"Error in generate_pitch: {str(e)}")
        import traceback
        traceback.print_exc()
        return jsonify({'success': False, 'error': str(e)}), 500


def generate_golden_template_pitch(brand, creator):
    """Generate a personalized pitch using optimized structure for higher reply rates"""
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
    engagement_rate = creator.get('engagement_rate') or 5  # Default 5%

    # IMPORTANT: Prefer For You edited niches over signup niches
    # This ensures pitch matches what user sees in For You page
    creator_niches_raw = creator.get('creator_niches')  # For You edited niches first
    if not creator_niches_raw:
        creator_niches_raw = creator.get('niche')  # Fall back to signup niche

    # Parse niches into a list
    creator_niches = []
    if isinstance(creator_niches_raw, str):
        try:
            creator_niches = json.loads(creator_niches_raw)
        except:
            creator_niches = [creator_niches_raw]
    elif isinstance(creator_niches_raw, list):
        creator_niches = creator_niches_raw

    # Smart niche selection: pick niche that best matches the brand category
    # This prevents pitching a tech brand with "fitness" when user has both
    brand_category = (brand.get('category') or '').lower()
    niche = None

    # Related niches mapping for smart matching
    # NOTE: fitness ≠ wellness - they are distinct categories
    related_niches = {
        'fitness': ['athleisure', 'activewear', 'sports'],
        'wellness': ['skincare', 'supplements', 'self-care'],
        'supplements': ['wellness', 'health'],
        'beauty': ['skincare', 'makeup', 'haircare'],
        'skincare': ['beauty', 'wellness', 'makeup'],
        'fashion': ['lifestyle', 'accessories', 'jewelry'],
        'tech': ['gaming', 'gadgets'],
        'gaming': ['tech', 'entertainment'],
    }

    if creator_niches:
        # First: exact match with brand category
        for n in creator_niches:
            if n and n.lower() == brand_category:
                niche = n
                break

        # Second: related niche match
        if not niche:
            brand_related = related_niches.get(brand_category, [])
            for n in creator_niches:
                if n and n.lower() in brand_related:
                    niche = n
                    break

        # Third: use first niche as fallback
        if not niche and len(creator_niches) > 0:
            niche = creator_niches[0]

    # Final fallback to brand category
    if not niche:
        niche = brand_category or 'content'

    # Parse social_links JSON to find platform handles
    social_links_raw = creator.get('social_links') or []
    if isinstance(social_links_raw, str):
        try:
            social_links_raw = json.loads(social_links_raw)
        except:
            social_links_raw = []

    # Build a dict of platform -> handle/url
    platform_handles = {}
    for link in social_links_raw:
        if isinstance(link, dict):
            plat = link.get('platform', '').lower()
            handle = link.get('handle') or link.get('username') or link.get('url') or ''
            if handle and plat:
                platform_handles[plat] = handle

    # Determine primary platform
    platform = 'Instagram'  # Default
    if 'tiktok' in platform_handles:
        platform = 'TikTok'
    elif 'youtube' in platform_handles:
        platform = 'YouTube'

    # Format followers
    if followers >= 1000000:
        followers_str = f"{followers / 1000000:.1f}M"
    elif followers >= 1000:
        followers_str = f"{followers / 1000:.1f}K"
    else:
        followers_str = str(followers) if followers else 'growing'

    brand_name = brand.get('brand_name', 'the brand')
    category = (brand.get('category') or '').lower()

    # Hero product fallback - create natural product references when hero_product isn't set
    category_product_fallbacks = {
        'fitness': 'activewear',
        'beauty': 'products',
        'skincare': 'skincare line',
        'fashion': 'pieces',
        'food': 'products',
        'wellness': 'wellness products',
        'supplements': 'supplements',
        'lifestyle': 'products',
        'tech': 'products',
        'pet': 'pet products',
        'haircare': 'haircare',
        'makeup': 'makeup',
        'athleisure': 'activewear',
        'home': 'products',
        'accessories': 'accessories',
        'jewelry': 'jewelry',
    }
    hero_product = brand.get('hero_product') or category_product_fallbacks.get(category, 'products')

    # Audience demographics based on niche
    audience_demos = {
        'beauty': 'women 18-35 interested in beauty',
        'skincare': 'skincare enthusiasts 20-40',
        'fashion': 'style-conscious women 18-35',
        'fitness': 'active lifestyle audience 22-40',
        'food': 'home cooks and foodies',
        'wellness': 'health-conscious consumers 25-45',
        'supplements': 'fitness and wellness focused audience',
        'lifestyle': f'{niche} enthusiasts',
        'tech': 'tech-savvy consumers 18-40',
        'pet': 'pet owners and animal lovers',
    }
    audience_desc = audience_demos.get(niche.lower() if niche else '', audience_demos.get(category, f'{niche} enthusiasts'))

    # Content angles by niche for subject line
    content_angles = {
        'beauty': ['honest product review', 'routine video', 'makeup tutorial'],
        'skincare': ['morning routine video', 'skincare review', '30-day test'],
        'fashion': ['styling video', 'outfit of the day', 'try-on haul'],
        'fitness': ['workout gear review', '30-day challenge', 'training content'],
        'food': ['recipe video', 'taste test', 'cooking tutorial'],
        'wellness': ['wellness routine', 'honest review', 'daily essentials'],
        'supplements': ['supplement review', '30-day results', 'fitness content'],
        'lifestyle': ['product review', 'honest take', 'daily vlog'],
        'tech': ['tech review', 'unboxing', 'honest take'],
        'pet': ['pet product test', 'honest review', 'pet content'],
    }
    content_angle = random.choice(content_angles.get(niche.lower() if niche else '', content_angles.get(category, ['product review', 'honest take'])))

    # Brand-specific hooks (what makes the pitch feel researched)
    brand_hooks = {
        'beauty': f"Your {hero_product} keeps coming up in my comments as a recommendation request.",
        'skincare': f"My audience has been asking about {hero_product} after seeing it on other creators.",
        'fashion': f"Your pieces fit the aesthetic my audience loves.",
        'fitness': f"Your {hero_product} is exactly what my fitness audience looks for.",
        'food': f"My followers keep asking about products like {hero_product}.",
        'wellness': f"Your {hero_product} aligns with what my wellness audience wants.",
        'supplements': f"My fitness audience is always asking about {hero_product}.",
        'lifestyle': f"Your brand fits the content my audience engages with most.",
        'tech': f"Your {hero_product} is exactly what my tech audience follows.",
        'pet': f"My pet-owner audience would love to see {hero_product}.",
    }
    brand_hook = brand_hooks.get(niche.lower() if niche else '', brand_hooks.get(category, f"Your {hero_product} fits perfectly with my content."))

    # Content format
    content_format = 'short-form video' if platform in ['TikTok', 'Instagram'] else 'video'

    # Build subject line (specific, under 10 words, no "PR collab idea")
    subject_templates = [
        f"{content_angle.title()} for {followers_str} {niche.lower() if niche else category} followers",
        f"My {niche.lower() if niche else category} audience and your {hero_product}",
        f"{followers_str} {platform} followers asking about {hero_product}",
        f"{content_angle.title()} idea for {brand_name}",
    ]
    subject = random.choice(subject_templates)

    # Profile link
    profile_url = f"https://newcollab.co/c/{creator.get('username', creator.get('id', 'creator'))}"

    # Build body (under 80 words, specific, clear ask)
    body = f"""Hi,

{brand_hook}

I create {niche.lower() if niche else category} content on {platform} ({followers_str} followers, {engagement_rate}% engagement, {audience_desc}).

I'd love to feature your {hero_product} in a {content_format} this month. Authentic, on-brand, specific to what works for your audience.

Would you be open to sending product?

{profile_url}

{creator_name}"""

    return {
        'subject': subject,
        'body': body,
        'creator_stats': {
            'followers': followers_str if followers else None,
            'niche': niche,
            'platform': platform
        }
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

    # Profile link
    profile_url = f"https://newcollab.co/c/{creator.get('username', creator.get('id', 'creator'))}"

    # Concise follow-up body (under 50 words)
    body = f"""Hi,

Just following up on my pitch from last week. Still interested in featuring your {hero_product}.

{followers_str} {platform} followers ready to see it.

{profile_url}

Let me know if you're open to sending product.

{creator_name}"""

    return {
        'subject': subject,
        'body': body,
        'creator_stats': {
            'followers': followers_str if followers else None,
            'platform': platform
        },
        'is_followup': True
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

        # Increment the monthly contact counter only if this contact was not
        # already tracked when the email/form was opened from Discover.
        if not pipeline_item.get('pitched_at') and not pipeline_item.get('send_confirmed'):
            cursor.execute("""
                UPDATE creators
                SET pitches_sent_this_week = COALESCE(pitches_sent_this_week, 0) + 1,
                    last_pitch_at = NOW()
                WHERE id = %s
                RETURNING pitches_sent_this_week, subscription_tier
            """, (creator_id,))
            result = cursor.fetchone()

            # Per emailflowbrief.md Stage 5: When user hits 3rd pitch, schedule
            # quota email for 7 days later (not immediately)
            if result and result.get('pitches_sent_this_week') == 3 and result.get('subscription_tier', 'free') == 'free':
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
            'preheader': f'Step 1 done — you just reached out to {brand_name}. Here\'s what to do next.',
            'message': f"""
                <p style="margin: 0 0 16px;">Hey {creator_name},</p>
                <p style="margin: 0 0 16px;">You just contacted <strong>{brand_name}</strong> — that's step 1 complete! 🎉</p>
                <p style="margin: 0 0 12px;">Here's what happens next:</p>
                <ul style="text-align: left; margin: 0 0 16px; padding-left: 20px; color: #1d1d1f;">
                    <li style="margin-bottom: 6px;">We'll remind you to follow up in 7 days if you haven't heard back</li>
                    <li style="margin-bottom: 6px;">Most brands respond within 1–2 weeks</li>
                    <li style="margin-bottom: 6px;">Track your pipeline and log any replies in your dashboard</li>
                </ul>
                <p style="margin: 0; color: #059669; font-weight: 600;">Keep the momentum going — the more brands you contact, the more packages you'll land! 📦</p>
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
            'fashion': ['lifestyle', 'accessories'],
            'lifestyle': ['fashion', 'home'],
            'fitness': ['athleisure', 'activewear', 'sports'],  # NOT wellness
            'wellness': ['skincare', 'supplements', 'self-care'],  # NOT fitness
            'supplements': ['wellness', 'health'],
            'food': ['lifestyle', 'kitchen', 'beverages'],
            'tech': ['gaming', 'gadgets'],
            'gaming': ['tech', 'entertainment'],
            'home': ['lifestyle', 'decor'],
        }
        hot_related = set()
        for n in (niches or []):
            n_lower = n.lower()
            hot_related.add(n_lower)
            hot_related.update(related_niches_map.get(n_lower, []))
        hot_niches_list = list(hot_related) if hot_related else None

        if hot_niches_list and min_follower_cap:
            cursor.execute("""
                SELECT
                    b.id, b.slug, b.brand_name AS name, b.logo_url AS logo,
                    b.description, b.category, b.response_rate,
                    b.min_followers, b.website, b.application_form_url
                FROM pr_brands b
                WHERE b.slug IS NOT NULL
                  AND COALESCE(b.status, 'published') = 'published'
                  AND b.id != ALL(%s)
                  AND (b.min_followers IS NULL OR b.min_followers <= %s)
                  AND LOWER(b.category) = ANY(%s)
                ORDER BY (
                    SELECT COUNT(DISTINCT cp.creator_id) FROM creator_pipeline cp
                    WHERE cp.brand_id = b.id
                    AND cp.stage = 'pitched'
                    AND cp.created_at > NOW() - INTERVAL '30 days'
                ) DESC, b.response_rate DESC NULLS LAST
                LIMIT 6
            """, (exclude_ids, min_follower_cap, hot_niches_list))
        elif hot_niches_list:
            cursor.execute("""
                SELECT
                    b.id, b.slug, b.brand_name AS name, b.logo_url AS logo,
                    b.description, b.category, b.response_rate,
                    b.min_followers, b.website, b.application_form_url
                FROM pr_brands b
                WHERE b.slug IS NOT NULL
                  AND COALESCE(b.status, 'published') = 'published'
                  AND b.id != ALL(%s)
                  AND LOWER(b.category) = ANY(%s)
                ORDER BY (
                    SELECT COUNT(DISTINCT cp.creator_id) FROM creator_pipeline cp
                    WHERE cp.brand_id = b.id
                    AND cp.stage = 'pitched'
                    AND cp.created_at > NOW() - INTERVAL '30 days'
                ) DESC, b.response_rate DESC NULLS LAST
                LIMIT 6
            """, (exclude_ids, hot_niches_list))
        elif min_follower_cap:
            cursor.execute("""
                SELECT
                    b.id, b.slug, b.brand_name AS name, b.logo_url AS logo,
                    b.description, b.category, b.response_rate,
                    b.min_followers, b.website, b.application_form_url
                FROM pr_brands b
                WHERE b.slug IS NOT NULL
                  AND COALESCE(b.status, 'published') = 'published'
                  AND b.id != ALL(%s)
                  AND (b.min_followers IS NULL OR b.min_followers <= %s)
                ORDER BY (
                    SELECT COUNT(DISTINCT cp.creator_id) FROM creator_pipeline cp
                    WHERE cp.brand_id = b.id
                    AND cp.stage = 'pitched'
                    AND cp.created_at > NOW() - INTERVAL '30 days'
                ) DESC, b.response_rate DESC NULLS LAST
                LIMIT 6
            """, (exclude_ids, min_follower_cap))
        else:
            cursor.execute("""
                SELECT
                    b.id, b.slug, b.brand_name AS name, b.logo_url AS logo,
                    b.description, b.category, b.response_rate,
                    b.min_followers, b.website, b.application_form_url
                FROM pr_brands b
                WHERE b.slug IS NOT NULL
                  AND COALESCE(b.status, 'published') = 'published'
                  AND b.id != ALL(%s)
                ORDER BY (
                    SELECT COUNT(DISTINCT cp.creator_id) FROM creator_pipeline cp
                    WHERE cp.brand_id = b.id
                    AND cp.stage = 'pitched'
                    AND cp.created_at > NOW() - INTERVAL '30 days'
                ) DESC, b.response_rate DESC NULLS LAST
                LIMIT 6
            """, (exclude_ids,))
        hot = cursor.fetchall()

        # Fallback: if not enough brands with pitches, fill from popular brands (also filtered by niche)
        if len(hot) < 3:
            hot_ids = [r['id'] for r in hot] if hot else [0]
            if hot_niches_list and min_follower_cap:
                cursor.execute("""
                    SELECT
                        b.id, b.slug, b.brand_name AS name, b.logo_url AS logo,
                        b.description, b.category, b.response_rate,
                        b.min_followers, b.website, b.application_form_url
                    FROM pr_brands b
                    WHERE b.slug IS NOT NULL
                      AND COALESCE(b.status, 'published') = 'published'
                      AND b.id != ALL(%s)
                      AND b.id != ALL(%s)
                      AND (b.min_followers IS NULL OR b.min_followers <= %s)
                      AND LOWER(b.category) = ANY(%s)
                    ORDER BY b.response_rate DESC NULLS LAST, RANDOM()
                    LIMIT %s
                """, (exclude_ids, hot_ids, min_follower_cap, hot_niches_list, 6 - len(hot)))
            elif hot_niches_list:
                cursor.execute("""
                    SELECT
                        b.id, b.slug, b.brand_name AS name, b.logo_url AS logo,
                        b.description, b.category, b.response_rate,
                        b.min_followers, b.website, b.application_form_url
                    FROM pr_brands b
                    WHERE b.slug IS NOT NULL
                      AND COALESCE(b.status, 'published') = 'published'
                      AND b.id != ALL(%s)
                      AND b.id != ALL(%s)
                      AND LOWER(b.category) = ANY(%s)
                    ORDER BY b.response_rate DESC NULLS LAST, RANDOM()
                    LIMIT %s
                """, (exclude_ids, hot_ids, hot_niches_list, 6 - len(hot)))
            elif min_follower_cap:
                cursor.execute("""
                    SELECT
                        b.id, b.slug, b.brand_name AS name, b.logo_url AS logo,
                        b.description, b.category, b.response_rate,
                        b.min_followers, b.website, b.application_form_url
                    FROM pr_brands b
                    WHERE b.slug IS NOT NULL
                      AND COALESCE(b.status, 'published') = 'published'
                      AND b.id != ALL(%s)
                      AND b.id != ALL(%s)
                      AND (b.min_followers IS NULL OR b.min_followers <= %s)
                    ORDER BY b.response_rate DESC NULLS LAST, RANDOM()
                    LIMIT %s
                """, (exclude_ids, hot_ids, min_follower_cap, 6 - len(hot)))
            else:
                cursor.execute("""
                    SELECT
                        b.id, b.slug, b.brand_name AS name, b.logo_url AS logo,
                        b.description, b.category, b.response_rate,
                        b.min_followers, b.website, b.application_form_url
                    FROM pr_brands b
                    WHERE b.slug IS NOT NULL
                      AND COALESCE(b.status, 'published') = 'published'
                      AND b.id != ALL(%s)
                      AND b.id != ALL(%s)
                    ORDER BY b.response_rate DESC NULLS LAST, RANDOM()
                    LIMIT %s
                """, (exclude_ids, hot_ids, 6 - len(hot)))
            fallback = cursor.fetchall()
            hot = list(hot) + list(fallback)

        # ── Section 2: Matched for You ───────────────────────────
        # Real matching algorithm with meaningful differentiation
        # Score breakdown: Niche (0-40) + Followers (0-25) + Response (0-20) + Bonus (0-15) = 100 max

        # Build related niches map for scoring
        # NOTE: fitness ≠ wellness - they are distinct categories
        related_niches = {
            'beauty': ['skincare', 'makeup', 'haircare'],
            'skincare': ['beauty', 'wellness'],
            'fashion': ['lifestyle', 'accessories'],
            'lifestyle': ['fashion', 'home'],
            'fitness': ['athleisure', 'activewear', 'sports'],
            'wellness': ['skincare', 'supplements', 'self-care'],
            'food': ['lifestyle', 'kitchen', 'beverages'],
            'tech': ['gaming', 'gadgets'],
            'gaming': ['tech', 'entertainment'],
            'home': ['lifestyle', 'decor'],
        }

        # Get related categories for the creator's niches
        creator_related = set()
        for n in (niches or []):
            n_lower = n.lower()
            creator_related.add(n_lower)
            creator_related.update(related_niches.get(n_lower, []))

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

            cursor.execute(f"""
                SELECT
                    b.id, b.slug, b.brand_name AS name, b.logo_url AS logo,
                    b.description, b.category, b.response_rate,
                    b.min_followers, b.max_followers, b.website, b.application_form_url,
                    b.niches AS brand_niches,
                    (
                        -- NICHE MATCH (0-40 points)
                        CASE
                            WHEN LOWER(b.category) = ANY(%s) THEN 40  -- Exact niche match
                            WHEN LOWER(b.category) = ANY(%s) THEN 28  -- Related niche
                            ELSE 10  -- No match baseline
                        END
                        -- FOLLOWER FIT (0-25 points)
                        + CASE
                            WHEN %s BETWEEN COALESCE(b.min_followers, 0)
                                AND COALESCE(b.max_followers, 999999999) THEN 25  -- Ideal range
                            WHEN %s >= COALESCE(b.min_followers, 0)
                                AND b.max_followers IS NULL THEN 22  -- Above min, no max
                            WHEN %s >= COALESCE(b.min_followers, 0) * 0.7 THEN 16  -- Within 30%% of min
                            WHEN %s >= COALESCE(b.min_followers, 0) * 0.5 THEN 10  -- Within 50%% of min
                            WHEN %s > COALESCE(b.max_followers, 999999999) THEN 8  -- Too big
                            ELSE 5  -- Far below requirements
                        END
                        -- RESPONSE RATE QUALITY (0-20 points)
                        + CASE
                            WHEN COALESCE(b.response_rate, 0) >= 50 THEN 20
                            WHEN COALESCE(b.response_rate, 0) >= 35 THEN 16
                            WHEN COALESCE(b.response_rate, 0) >= 20 THEN 12
                            WHEN COALESCE(b.response_rate, 0) >= 10 THEN 8
                            ELSE 4
                        END
                        -- BONUS POINTS (0-15 points)
                        + CASE WHEN b.has_application_form = true THEN 5 ELSE 0 END
                        + CASE WHEN b.contact_email IS NOT NULL AND b.contact_email != '' THEN 5 ELSE 0 END
                        + (RANDOM() * 5)::int  -- Small randomness
                    )::int AS match_score
                FROM pr_brands b
                WHERE b.slug IS NOT NULL
                  AND COALESCE(b.status, 'published') = 'published'
                  AND b.id != ALL(%s)
                  {brand_filter_sql}
                ORDER BY match_score DESC, b.response_rate DESC NULLS LAST
                LIMIT 8
            """, tuple(query_params))
            matched = cursor.fetchall()
        else:
            # No profile yet — return variety of top brands with basic scoring
            # Default to showing smaller brands (safe for any creator size)
            default_max_followers = 50000  # Safe default for unknown creators
            cursor.execute("""
                SELECT
                    b.id, b.slug, b.brand_name AS name, b.logo_url AS logo,
                    b.description, b.category, b.response_rate,
                    b.min_followers, b.max_followers, b.website, b.application_form_url,
                    (
                        30  -- Base score
                        + CASE
                            WHEN COALESCE(b.response_rate, 0) >= 50 THEN 25
                            WHEN COALESCE(b.response_rate, 0) >= 30 THEN 18
                            WHEN COALESCE(b.response_rate, 0) >= 15 THEN 12
                            ELSE 5
                        END
                        + CASE WHEN b.has_application_form = true THEN 8 ELSE 0 END
                        + (RANDOM() * 15)::int
                    )::int AS match_score
                FROM pr_brands b
                WHERE b.slug IS NOT NULL
                  AND COALESCE(b.status, 'published') = 'published'
                  AND b.id != ALL(%s)
                  AND (b.min_followers IS NULL OR b.min_followers <= %s)
                ORDER BY match_score DESC
                LIMIT 8
            """, (exclude_ids, default_max_followers))
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
            1:  "New Year reset — wellness brands gifting heavily in January",
            2:  "Valentine's season — beauty and jewelry brands seeking creators",
            3:  "Spring launch season — skincare brands partnering with creators",
            4:  "Spring fashion drops — brands seeking fresh campaign content",
            5:  "Pre-summer prep — lifestyle and skincare brands gifting now",
            6:  "Summer campaigns — SPF and fashion brands need content now",
            7:  "Peak summer — lifestyle brands seeking authentic summer content",
            8:  "Late summer push — fashion and beauty brands preparing for fall",
            9:  "Back to school/fall — fashion brands refreshing their creator roster",
            10: "Pre-holiday — beauty and home brands building gifting lists",
            11: "Holiday gifting season — brands most active for PR partnerships",
            12: "Year-end gifting — brands clearing PR budgets before January",
        }

        cursor.execute("""
            SELECT
                b.id, b.slug, b.brand_name AS name, b.logo_url AS logo,
                b.description, b.category, b.response_rate,
                b.min_followers, b.website, b.application_form_url
            FROM pr_brands b
            WHERE b.slug IS NOT NULL
              AND COALESCE(b.status, 'published') = 'published'
              AND LOWER(b.category) = ANY(%s)
              AND b.id != ALL(%s)
            ORDER BY b.response_rate DESC NULLS LAST, RANDOM()
            LIMIT 4
        """, ([c.lower() for c in seasonal_cats], exclude_ids))
        seasonal = cursor.fetchall()

        cursor.close()
        conn.close()

        return jsonify({
            'success': True,
            'hot': [dict(r) for r in hot],
            'matched': [dict(r) for r in matched],
            'seasonal': [dict(r) for r in seasonal],
            'seasonal_reason': seasonal_reasons.get(month, ''),
            'seasonal_month': datetime.now().strftime('%B'),
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
            set_parts.append(f"{key} = %s")
            values.append(value)

            # Keep niche column in sync when creator_niches is updated
            # This ensures consistency across the app (pitch generation, wishlist, etc.)
            if key == 'creator_niches' and isinstance(value, list):
                set_parts.append("niche = %s")
                values.append(json.dumps(value))

        values.append(creator_id)

        cursor.execute(f"""
            UPDATE creators
            SET {', '.join(set_parts)}
            WHERE id = %s
            RETURNING creator_niches, creator_followers
        """, values)

        updated = cursor.fetchone()
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
            'tech': ['gaming', 'gadgets'],
            'food': ['cooking', 'recipes', 'kitchen'],
        }
        expanded_niches = set(user_niches_lower)
        for n in user_niches_lower:
            expanded_niches.update(related_niches.get(n, []))
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
