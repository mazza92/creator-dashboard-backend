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

        for b in brands:
            rate, days = resolve_brand_stats(
                b.get('slug') or str(b['id']),
                b.get('category'),
                b.get('response_rate'),
                b.get('avg_response_time_days'),
            )
            b['response_rate'] = rate
            b['avg_response_time_days'] = days

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
                    pb.has_application_form
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
                    pb.has_application_form
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
    """Generate a personalized pitch using the Golden Template structure"""
    import random

    # Extract creator data
    creator_name = f"{creator.get('first_name', '')} {creator.get('last_name', '')}".strip() or 'Creator'
    followers = creator.get('followers_count')
    social_links = creator.get('social_links') or []
    if isinstance(social_links, str):
        social_links = json.loads(social_links)

    niche = creator.get('niche')
    if isinstance(niche, str):
        try:
            niche = json.loads(niche)
        except:
            niche = [niche]
    if isinstance(niche, list) and len(niche) > 0:
        niche = niche[0]
    else:
        niche = brand.get('category', 'content')

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
            # Try to get handle from various possible fields
            handle = link.get('handle') or link.get('username') or link.get('url') or ''
            if handle and plat:
                platform_handles[plat] = handle

    # Determine primary platform and social URL
    social_url = None
    platform = 'Instagram'  # Default

    # Priority: TikTok > Instagram > YouTube
    if 'tiktok' in platform_handles:
        platform = 'TikTok'
        handle = platform_handles['tiktok'].replace('@', '').replace('https://tiktok.com/', '').replace('https://www.tiktok.com/', '').strip('/')
        if handle and not handle.startswith('http'):
            social_url = f"https://tiktok.com/@{handle}"
        elif handle.startswith('http'):
            social_url = handle
    elif 'instagram' in platform_handles:
        platform = 'Instagram'
        handle = platform_handles['instagram'].replace('@', '').replace('https://instagram.com/', '').replace('https://www.instagram.com/', '').strip('/')
        if handle and not handle.startswith('http'):
            social_url = f"https://instagram.com/{handle}"
        elif handle.startswith('http'):
            social_url = handle
    elif 'youtube' in platform_handles:
        platform = 'YouTube'
        handle = platform_handles['youtube'].replace('@', '')
        if handle.startswith('http'):
            social_url = handle
        else:
            social_url = f"https://youtube.com/@{handle}"

    # Also check username field as fallback for Instagram
    if not social_url and creator.get('username'):
        platform = 'Instagram'
        social_url = f"https://instagram.com/{creator.get('username')}"

    # Format followers
    if followers:
        if followers >= 1000000:
            followers_str = f"{followers / 1000000:.1f}M"
        elif followers >= 1000:
            followers_str = f"{followers / 1000:.1f}K"
        else:
            followers_str = str(followers)
    else:
        followers_str = 'a growing audience'

    # Get month for content series
    from datetime import datetime, timedelta
    next_month = (datetime.now() + timedelta(days=30)).strftime('%B')

    # Generate series name based on niche
    series_names = {
        'Beauty': 'product testing',
        'Skincare': 'skincare routine',
        'Fashion': 'outfit styling',
        'Fitness': 'workout gear review',
        'Food': 'kitchen favorites',
        'Lifestyle': 'daily essentials',
        'Tech': 'tech review',
        'Home': 'home finds',
        'Pets': 'pet product testing',
    }
    series_name = series_names.get(niche, series_names.get(brand.get('category'), 'product review'))

    # Audience interest
    interests = {
        'Beauty': 'what products actually work',
        'Skincare': 'skincare routines and product recs',
        'Fashion': 'where to find good pieces',
        'Fitness': 'gear that holds up',
        'Food': 'kitchen stuff worth buying',
        'Lifestyle': 'everyday essentials',
        'Tech': 'tech that makes life easier',
        'Home': 'home finds',
        'Pets': 'pet products',
    }
    audience_interest = interests.get(niche, interests.get(brand.get('category'), 'product recommendations'))

    brand_name = brand.get('brand_name', 'the brand')
    category = brand.get('category', '')

    # Human openers
    openers = [
        f"I've been using {brand_name} products for a bit now and wanted to reach out about a collab idea.",
        f"Found {brand_name} a few months back and it's become a staple in my routine - figured I'd shoot my shot.",
        f"Quick intro - I'm a {category.lower() if category else 'content'} creator and I've had my eye on {brand_name} for a while.",
        f"Hope this finds the right person! I create {category.lower() if category else ''} content and {brand_name} keeps coming up in my comments.",
        f"I've been wanting to reach out for a while - {brand_name} fits really well with the content I make."
    ]
    opener = random.choice(openers)

    # Build subject
    subject = f"PR collab idea for {brand_name}"

    # Build social links section
    social_line = f"My {platform}: {social_url}" if social_url else ""
    profile_line = f"My profile & past work: https://newcollab.co/c/{creator.get('username', creator.get('id', 'creator'))}"

    # Combine links
    if social_line:
        links_section = f"{social_line}\n{profile_line}"
    else:
        links_section = profile_line

    # Build body
    body = f"""Hi there,

{opener}

I'm putting together a {series_name} series for {next_month} and thought {brand_name} would be a good fit. I have {followers_str} on {platform} who are always asking about {audience_interest}.

Here's what I had in mind:
- A {'TikTok' if platform == 'TikTok' else 'Reel'} showing how I actually use the product (not a basic unboxing)
- I can also send over the raw clips if your team wants to use them

{links_section}

If you're open to it, I'd love to try some products and see if we can make something work. No pressure either way!

Thanks,
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
    """Generate a follow-up email for brands already pitched"""
    import random

    # Extract creator data
    creator_name = f"{creator.get('first_name', '')} {creator.get('last_name', '')}".strip() or 'Creator'
    followers = creator.get('followers_count')

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

    # Determine primary platform and social URL
    social_url = None
    platform = 'Instagram'  # Default

    if 'tiktok' in platform_handles:
        platform = 'TikTok'
        handle = platform_handles['tiktok'].replace('@', '').replace('https://tiktok.com/', '').replace('https://www.tiktok.com/', '').strip('/')
        if handle and not handle.startswith('http'):
            social_url = f"https://tiktok.com/@{handle}"
        elif handle.startswith('http'):
            social_url = handle
    elif 'instagram' in platform_handles:
        platform = 'Instagram'
        handle = platform_handles['instagram'].replace('@', '').replace('https://instagram.com/', '').replace('https://www.instagram.com/', '').strip('/')
        if handle and not handle.startswith('http'):
            social_url = f"https://instagram.com/{handle}"
        elif handle.startswith('http'):
            social_url = handle
    elif 'youtube' in platform_handles:
        platform = 'YouTube'
        handle = platform_handles['youtube'].replace('@', '')
        if handle.startswith('http'):
            social_url = handle
        else:
            social_url = f"https://youtube.com/@{handle}"

    # Fallback for Instagram
    if not social_url and creator.get('username'):
        platform = 'Instagram'
        social_url = f"https://instagram.com/{creator.get('username')}"

    # Format followers
    if followers:
        if followers >= 1000000:
            followers_str = f"{followers / 1000000:.1f}M"
        elif followers >= 1000:
            followers_str = f"{followers / 1000:.1f}K"
        else:
            followers_str = str(followers)
    else:
        followers_str = 'a growing audience'

    brand_name = brand.get('brand_name', 'the brand')
    category = brand.get('category', '')

    # Follow-up openers (different from first outreach)
    openers = [
        "Just wanted to bump this to the top of your inbox - I reached out recently about a potential collab.",
        "Following up on my email from last week about working together.",
        "Hi! Wanted to check in on my collab inquiry from a few days ago.",
        "Quick follow-up on my previous message - wanted to make sure it didn't get lost in the inbox.",
        "Circling back on my earlier pitch - still very interested in collaborating!"
    ]
    opener = random.choice(openers)

    # Build subject for follow-up
    subject = f"Following up - PR collab with {brand_name}"

    # Build social links section
    social_line = f"My {platform}: {social_url}" if social_url else ""
    profile_line = f"My profile: https://newcollab.co/c/{creator.get('username', creator.get('id', 'creator'))}"

    if social_line:
        links_section = f"{social_line}\n{profile_line}"
    else:
        links_section = profile_line

    # Build follow-up body
    body = f"""Hi there,

{opener}

I'm still really interested in creating content around {brand_name} products. I have {followers_str} on {platform} and my audience loves discovering new brands in the {category.lower() if category else 'lifestyle'} space.

Happy to send over some content ideas if that helps - or if you want to check out my recent work first:

{links_section}

Would love to hear back either way. Thanks for considering!

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
                pb.application_form_url,
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

        # Get IDs the user has already pitched (exclude from recommendations)
        cursor.execute("""
            SELECT brand_id FROM creator_pipeline
            WHERE creator_id = %s AND (send_confirmed = TRUE OR pitched_at IS NOT NULL)
        """, (creator_id,))
        already_pitched = cursor.fetchall()
        exclude_ids = [r['brand_id'] for r in already_pitched] if already_pitched else [0]

        # ── Section 1: Hot This Week ─────────────────────────────
        # Top brands by response rate + niche match + recent additions + variety
        cursor.execute("""
            SELECT
                b.id, b.slug, b.brand_name AS name, b.logo_url AS logo,
                b.description, b.category, b.response_rate,
                b.min_followers, b.website, b.application_form_url,
                COALESCE(
                    (SELECT COUNT(*) FROM creator_pipeline cp
                     WHERE cp.brand_id = b.id
                     AND cp.stage IN ('won','received','success')
                     AND cp.package_confirmed_at > NOW() - INTERVAL '30 days'), 0
                ) AS wins_this_week,
                COALESCE(
                    (SELECT COUNT(*) FROM creator_pipeline cp
                     WHERE cp.brand_id = b.id
                     AND cp.send_confirmed = TRUE
                     AND cp.pitched_at > NOW() - INTERVAL '30 days'), 0
                ) AS pitched_this_week,
                (
                    COALESCE(b.response_rate, 0) * 2
                    + CASE WHEN LOWER(b.category) = ANY(%s) THEN 25 ELSE 0 END
                    + CASE WHEN b.created_at > NOW() - INTERVAL '30 days' THEN 15 ELSE 0 END
                    + RANDOM() * 10
                )::int AS hot_score
            FROM pr_brands b
            WHERE b.slug IS NOT NULL
              AND COALESCE(b.status, 'published') = 'published'
              AND b.id != ALL(%s)
              AND b.response_rate > 0
            ORDER BY hot_score DESC
            LIMIT 3
        """, (niches if niches else [''], exclude_ids))
        hot = cursor.fetchall()

        # Fallback: if not enough brands with response_rate, fill from all brands (prefer niche match)
        if len(hot) < 3:
            hot_ids = [r['id'] for r in hot] if hot else [0]
            cursor.execute("""
                SELECT
                    b.id, b.slug, b.brand_name AS name, b.logo_url AS logo,
                    b.description, b.category, b.response_rate,
                    b.min_followers, b.website, b.application_form_url,
                    0 AS wins_this_week, 0 AS pitched_this_week
                FROM pr_brands b
                WHERE b.slug IS NOT NULL
                  AND COALESCE(b.status, 'published') = 'published'
                  AND b.id != ALL(%s)
                  AND b.id != ALL(%s)
                ORDER BY
                    CASE WHEN LOWER(b.category) = ANY(%s) THEN 0 ELSE 1 END,
                    RANDOM()
                LIMIT %s
            """, (exclude_ids, hot_ids, niches if niches else [''], 3 - len(hot)))
            fallback = cursor.fetchall()
            hot = list(hot) + list(fallback)

        # ── Section 2: Matched for You ───────────────────────────
        # Personalized by niche + follower count
        if niches or followers:
            cursor.execute("""
                SELECT
                    b.id, b.slug, b.brand_name AS name, b.logo_url AS logo,
                    b.description, b.category, b.response_rate,
                    b.min_followers, b.website, b.application_form_url,
                    (
                        CASE WHEN LOWER(b.category) = ANY(%s) THEN 50 ELSE 15 END
                        + CASE WHEN %s >= COALESCE(b.min_followers, 0) THEN 30 ELSE 10 END
                        + LEAST(COALESCE(b.response_rate, 0) / 2, 20)
                        + RANDOM() * 5
                    )::int AS match_score
                FROM pr_brands b
                WHERE b.slug IS NOT NULL
                  AND COALESCE(b.status, 'published') = 'published'
                  AND b.id != ALL(%s)
                ORDER BY match_score DESC, b.response_rate DESC NULLS LAST
                LIMIT 8
            """, ([n.lower() for n in niches] if niches else [''], followers, exclude_ids))
            matched = cursor.fetchall()
        else:
            # No profile yet — return variety of top brands
            cursor.execute("""
                SELECT
                    b.id, b.slug, b.brand_name AS name, b.logo_url AS logo,
                    b.description, b.category, b.response_rate,
                    b.min_followers, b.website, b.application_form_url,
                    (COALESCE(b.response_rate, 0) + RANDOM() * 20)::int AS match_score
                FROM pr_brands b
                WHERE b.slug IS NOT NULL
                  AND COALESCE(b.status, 'published') = 'published'
                  AND b.id != ALL(%s)
                ORDER BY match_score DESC
                LIMIT 8
            """, (exclude_ids,))
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
