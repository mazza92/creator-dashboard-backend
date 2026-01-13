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

        # Filter by category
        if category:
            where_clauses.append('category = %s')
            params.append(category)

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

        cursor.execute('''
            SELECT category, COUNT(*) as count
            FROM pr_brands
            GROUP BY category
            ORDER BY count DESC
        ''')
        categories = cursor.fetchall()

        cursor.close()
        conn.close()

        return jsonify({
            'success': True,
            'categories': categories
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
    """Save a brand to pipeline (stage: saved)"""
    creator_id = get_creator_id_from_session()
    if not creator_id:
        return jsonify({'success': False, 'error': 'Not authenticated'}), 401

    try:
        # Check daily unlock limit (FREE tier only - 5 per day)
        conn = get_db_connection()
        cursor = conn.cursor(cursor_factory=RealDictCursor)

        cursor.execute('''
            SELECT subscription_tier, daily_unlocks_used, last_unlock_date
            FROM creators
            WHERE id = %s
        ''', (creator_id,))
        creator = cursor.fetchone()

        if not creator:
            cursor.close()
            conn.close()
            return jsonify({'success': False, 'error': 'Creator not found'}), 404

        tier = creator['subscription_tier'] or 'free'

        # Check daily unlock limit for FREE users
        if tier == 'free':
            from datetime import date
            today = date.today()
            last_unlock = creator.get('last_unlock_date')
            daily_unlocks = creator.get('daily_unlocks_used', 0)

            # Reset if it's a new day
            if last_unlock is None or last_unlock != today:
                daily_unlocks = 0

            # Check if limit reached
            DAILY_LIMIT = 5
            if daily_unlocks >= DAILY_LIMIT:
                cursor.close()
                conn.close()
                return jsonify({
                    'success': False,
                    'error': f"You've used all {DAILY_LIMIT} free applications today. Come back tomorrow or upgrade to Pro for unlimited!",
                    'upgrade_required': True,
                    'current_count': daily_unlocks,
                    'limit': DAILY_LIMIT
                }), 403

        data = request.json
        brand_id = data.get('brand_id')

        if not brand_id:
            cursor.close()
            conn.close()
            return jsonify({'success': False, 'error': 'brand_id required'}), 400

        # Insert or update (conn and cursor already initialized above)
        cursor.execute('''
            INSERT INTO creator_pipeline (creator_id, brand_id, stage, created_at, updated_at)
            VALUES (%s, %s, 'saved', NOW(), NOW())
            ON CONFLICT (creator_id, brand_id) DO UPDATE
            SET stage = 'saved', updated_at = NOW()
            RETURNING id
        ''', (creator_id, brand_id))

        pipeline_id = cursor.fetchone()['id']

        # Update creator's brands_saved_count AND daily_unlocks_used (for quota tracking)
        from datetime import date
        today = date.today()

        cursor.execute('''
            UPDATE creators
            SET brands_saved_count = brands_saved_count + 1,
                daily_unlocks_used = daily_unlocks_used + 1,
                last_unlock_date = %s
            WHERE id = %s
        ''', (today, creator_id,))

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
    try:
        creator_id = get_creator_id_from_session()
        is_premium = False

        if creator_id:
            conn = get_db_connection()
            cursor = conn.cursor(cursor_factory=RealDictCursor)
            cursor.execute('SELECT subscription_tier FROM creators WHERE id = %s', (creator_id,))
            creator = cursor.fetchone()
            if creator and creator['subscription_tier'] in ['pro', 'elite']:
                is_premium = True

        conn = get_db_connection()
        cursor = conn.cursor(cursor_factory=RealDictCursor)

        # Get platform templates
        if is_premium:
            cursor.execute('SELECT * FROM email_templates ORDER BY success_rate DESC')
        else:
            cursor.execute('SELECT * FROM email_templates WHERE is_premium = false ORDER BY success_rate DESC')

        templates = cursor.fetchall()

        # Get creator's custom templates if logged in
        custom_templates = []
        if creator_id:
            cursor.execute('''
                SELECT * FROM creator_custom_templates
                WHERE creator_id = %s
                ORDER BY created_at DESC
            ''', (creator_id,))
            custom_templates = cursor.fetchall()

        cursor.close()
        conn.close()

        return jsonify({
            'success': True,
            'templates': templates,
            'custom_templates': custom_templates
        })

    except Exception as e:
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
