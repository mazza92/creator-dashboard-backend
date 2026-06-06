"""
Opportunities Routes for Creator Dashboard
Brand casting calls / job board layer for PR opportunities
"""

from flask import Blueprint, request, jsonify, session
from flask_jwt_extended import jwt_required, get_jwt_identity
from functools import wraps
import psycopg2
from psycopg2.extras import RealDictCursor
import os
from datetime import datetime, timedelta, date
import requests

opportunities_bp = Blueprint('opportunities', __name__, url_prefix='/api/opportunities')


def admin_required(f):
    """
    Decorator to require admin authentication.
    Matches the pattern used in admin_brands.py and admin_reports.py.
    """
    @wraps(f)
    def decorated_function(*args, **kwargs):
        # Check X-Admin-Token header first
        admin_token = request.headers.get('X-Admin-Token')
        if admin_token == 'pr-hunter-admin-2026':
            return f(*args, **kwargs)

        # Check session-based auth as fallback
        user_id = session.get('user_id')
        if not user_id:
            return jsonify({'success': False, 'error': 'Authentication required'}), 401

        try:
            conn = get_db_connection()
            cursor = conn.cursor(cursor_factory=RealDictCursor)
            cursor.execute('SELECT email FROM users WHERE id = %s', (user_id,))
            user = cursor.fetchone()
            conn.close()

            if not user or user.get('email', '').lower() != 'team@newcollab.co':
                return jsonify({'success': False, 'error': 'Admin access required'}), 403

        except Exception as e:
            return jsonify({'success': False, 'error': str(e)}), 500

        return f(*args, **kwargs)
    return decorated_function

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
    # Check if creator_id is directly in session
    creator_id = session.get('creator_id')
    if creator_id:
        return creator_id

    # Check if user_id is in session and lookup creator
    user_id = session.get('user_id')
    if user_id:
        try:
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

    # Try JWT as fallback
    try:
        jwt_user_id = get_jwt_identity()
        if jwt_user_id:
            conn = get_db_connection()
            cursor = conn.cursor(cursor_factory=RealDictCursor)
            cursor.execute('SELECT id FROM creators WHERE user_id = %s', (jwt_user_id,))
            creator = cursor.fetchone()
            cursor.close()
            conn.close()
            if creator:
                return creator['id']
    except:
        pass
    return None

def send_email_notification(to_email, subject, body):
    """Send email via SendGrid"""
    try:
        sendgrid_api_key = os.getenv('SENDGRID_API_KEY')
        if not sendgrid_api_key:
            print(f"SendGrid API key not configured, skipping email to {to_email}")
            return False

        response = requests.post(
            'https://api.sendgrid.com/v3/mail/send',
            headers={
                'Authorization': f'Bearer {sendgrid_api_key}',
                'Content-Type': 'application/json'
            },
            json={
                'personalizations': [{'to': [{'email': to_email}]}],
                'from': {'email': 'team@newcollab.co', 'name': 'Newcollab'},
                'subject': subject,
                'content': [{'type': 'text/plain', 'value': body}]
            }
        )
        return response.status_code in [200, 201, 202]
    except Exception as e:
        print(f"Error sending email: {str(e)}")
        return False


# ============================================
# PUBLIC: Brand submits opportunity
# ============================================

@opportunities_bp.route('/public/submit', methods=['POST'])
def brand_submit():
    """Public endpoint for brands to submit opportunities"""
    try:
        data = request.get_json()

        # Validate required fields
        required = ['brand_name', 'brand_email', 'brand_website', 'product_name', 'campaign_description', 'spots_total']
        for field in required:
            if not data.get(field):
                return jsonify({'success': False, 'error': f'{field} is required'}), 400

        conn = get_db_connection()
        cursor = conn.cursor(cursor_factory=RealDictCursor)

        cursor.execute('''
            INSERT INTO opportunities (
                brand_name, brand_email, brand_website, brand_category,
                product_name, campaign_description, pr_value_usd,
                creator_count_range, shipping_regions, follower_ranges,
                content_types, creator_niches, additional_notes,
                application_deadline, spots_total, status
            ) VALUES (
                %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 'pending'
            ) RETURNING id
        ''', (
            data['brand_name'],
            data['brand_email'],
            data['brand_website'],
            data.get('brand_category'),
            data['product_name'],
            data['campaign_description'],
            data.get('pr_value_usd'),
            data.get('creator_count_range', '5-10'),
            data.get('shipping_regions', []),
            data.get('follower_ranges', []),
            data.get('content_types', []),
            data.get('creator_niches', []),
            data.get('additional_notes') or None,
            data.get('application_deadline') or None,
            int(data['spots_total'])
        ))

        result = cursor.fetchone()
        opp_id = result['id']

        conn.commit()
        cursor.close()
        conn.close()

        # Notify admin
        send_email_notification(
            'mahery@newcollab.co',
            f'New opportunity to review: {data["brand_name"]}',
            f'{data["brand_name"]} submitted an opportunity for {data["product_name"]}.\n\n'
            f'Review at: https://app.newcollab.co/admin/opportunities/{opp_id}'
        )

        # Confirm to brand
        send_email_notification(
            data['brand_email'],
            'Your Newcollab listing is under review',
            f'Hi {data["brand_name"]},\n\n'
            f'We received your opportunity listing for {data["product_name"]}. '
            f'We will review and publish it within 24 hours.\n\n'
            f'Best,\nNewcollab Team'
        )

        return jsonify({'success': True, 'id': opp_id}), 201

    except Exception as e:
        print(f"Error in brand_submit: {str(e)}")
        return jsonify({'success': False, 'error': str(e)}), 500


# ============================================
# CREATOR: List live opportunities
# ============================================

@opportunities_bp.route('/list', methods=['GET'])
def list_opportunities():
    """Get list of live opportunities for creators"""
    creator_id = get_creator_id_from_session()
    if not creator_id:
        return jsonify({'success': False, 'error': 'Not authenticated'}), 401

    try:
        conn = get_db_connection()
        cursor = conn.cursor(cursor_factory=RealDictCursor)

        # Get creator's niche and subscription tier
        cursor.execute('''
            SELECT niche, subscription_tier
            FROM creators
            WHERE id = %s
        ''', (creator_id,))
        creator = cursor.fetchone()

        if not creator:
            cursor.close()
            conn.close()
            return jsonify({'success': False, 'error': 'Creator not found'}), 404

        creator_niche = creator.get('niche') or ''
        is_pro = creator.get('subscription_tier') in ['pro', 'elite']

        # Get all live opportunities
        cursor.execute('''
            SELECT
                id, brand_name, brand_category, brand_logo_url, product_name, campaign_description,
                pr_value_usd, creator_count_range, shipping_regions, follower_ranges,
                content_types, creator_niches, spots_total, spots_filled, closes_at,
                created_at
            FROM opportunities
            WHERE status = 'live'
              AND (closes_at IS NULL OR closes_at > NOW())
              AND spots_filled < spots_total
            ORDER BY created_at DESC
        ''')
        all_opps = cursor.fetchall()

        # Get which ones the creator already applied to
        cursor.execute('''
            SELECT opportunity_id FROM opportunity_applications
            WHERE creator_id = %s
        ''', (creator_id,))
        applied_ids = {row['opportunity_id'] for row in cursor.fetchall()}

        cursor.close()
        conn.close()

        # Split into matched and others
        matched = []
        others = []

        for opp in all_opps:
            opp_niches = opp.get('creator_niches') or []
            # Match if opportunity has no niche restriction, or creator's niche is in the list
            is_match = not opp_niches or (creator_niche and creator_niche.lower() in [n.lower() for n in opp_niches])

            spots_left = opp['spots_total'] - opp['spots_filled']
            days_left = None
            if opp['closes_at']:
                delta = (opp['closes_at'] - datetime.utcnow()).days
                days_left = max(0, delta)

            serialized = {
                'id': opp['id'],
                'brand_name': opp['brand_name'],
                'brand_category': opp['brand_category'],
                'brand_logo_url': opp.get('brand_logo_url'),
                'product_name': opp['product_name'],
                'campaign_description': opp['campaign_description'],
                'pr_value_usd': opp['pr_value_usd'],
                'creator_count_range': opp['creator_count_range'],
                'shipping_regions': opp['shipping_regions'] or [],
                'follower_ranges': opp['follower_ranges'] or [],
                'content_types': opp['content_types'] or [],
                'spots_total': opp['spots_total'],
                'spots_left': spots_left,
                'days_left': days_left,
                'is_matched': is_match,
                'already_applied': opp['id'] in applied_ids
            }

            if is_match:
                matched.append(serialized)
            else:
                others.append(serialized)

        return jsonify({
            'success': True,
            'matched': matched,
            'others': others,
            'is_pro': is_pro
        })

    except Exception as e:
        print(f"Error in list_opportunities: {str(e)}")
        return jsonify({'success': False, 'error': str(e)}), 500


# ============================================
# CREATOR: Apply to opportunity
# ============================================

@opportunities_bp.route('/<int:opp_id>/apply', methods=['POST'])
def apply_opportunity(opp_id):
    """Creator applies to an opportunity"""
    creator_id = get_creator_id_from_session()
    if not creator_id:
        return jsonify({'success': False, 'error': 'Not authenticated'}), 401

    try:
        conn = get_db_connection()
        cursor = conn.cursor(cursor_factory=RealDictCursor)

        # Get opportunity
        cursor.execute('''
            SELECT id, brand_name, brand_email, product_name, status, spots_total, spots_filled
            FROM opportunities
            WHERE id = %s
        ''', (opp_id,))
        opp = cursor.fetchone()

        if not opp:
            cursor.close()
            conn.close()
            return jsonify({'success': False, 'error': 'Opportunity not found'}), 404

        if opp['status'] != 'live':
            cursor.close()
            conn.close()
            return jsonify({'success': False, 'error': 'This opportunity is no longer active'}), 400

        if opp['spots_filled'] >= opp['spots_total']:
            cursor.close()
            conn.close()
            return jsonify({'success': False, 'error': 'No spots remaining'}), 400

        # Check if already applied
        cursor.execute('''
            SELECT id FROM opportunity_applications
            WHERE opportunity_id = %s AND creator_id = %s
        ''', (opp_id, creator_id))
        existing = cursor.fetchone()

        if existing:
            cursor.close()
            conn.close()
            return jsonify({'success': False, 'error': 'You have already applied'}), 409

        # Check pitch/application limits (same pool as pitches)
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

        # Reset monthly count if needed
        today = date.today()
        month_start = today.replace(day=1)
        if last_reset is None or (isinstance(last_reset, date) and last_reset < month_start):
            pitches_used = 0

        FREE_MONTHLY_LIMIT = 3
        is_pro = tier in ['pro', 'elite']

        if not is_pro and pitches_used >= FREE_MONTHLY_LIMIT:
            cursor.close()
            conn.close()
            return jsonify({
                'success': False,
                'error': 'limit_reached',
                'message': 'You have used all your free applications this month'
            }), 403

        # Create application
        cursor.execute('''
            INSERT INTO opportunity_applications (opportunity_id, creator_id)
            VALUES (%s, %s)
            RETURNING id
        ''', (opp_id, creator_id))

        # Increment spots filled
        cursor.execute('''
            UPDATE opportunities
            SET spots_filled = spots_filled + 1
            WHERE id = %s
        ''', (opp_id,))

        # Increment pitch count (uses same pool)
        cursor.execute('''
            UPDATE creators
            SET pitches_sent_this_week = COALESCE(pitches_sent_this_week, 0) + 1,
                last_pitch_reset = COALESCE(last_pitch_reset, CURRENT_DATE)
            WHERE id = %s
        ''', (creator_id,))

        conn.commit()

        # Get creator details for email
        cursor.execute('''
            SELECT username, followers_count, niche, kit_slug
            FROM creators
            WHERE id = %s
        ''', (creator_id,))
        creator_info = cursor.fetchone()

        cursor.close()
        conn.close()

        # Notify brand
        if creator_info:
            niche_str = creator_info['niche'] or 'Creator'
            slug = creator_info['kit_slug'] or creator_info['username']
            send_email_notification(
                opp['brand_email'],
                f'New application from {creator_info["username"]} for {opp["product_name"]}',
                f'{creator_info["username"]} ({creator_info["followers_count"]} followers, {niche_str}) '
                f'applied to your opportunity.\n\n'
                f'View their media kit: https://app.newcollab.co/kit/{slug}\n\n'
                f'Reply to this email to approve or decline.'
            )

        return jsonify({'success': True}), 201

    except Exception as e:
        print(f"Error in apply_opportunity: {str(e)}")
        return jsonify({'success': False, 'error': str(e)}), 500


# ============================================
# ADMIN: List opportunities
# ============================================

@opportunities_bp.route('/admin/list', methods=['GET'])
@admin_required
def admin_list():
    """Admin endpoint to list opportunities by status"""
    try:
        conn = get_db_connection()
        cursor = conn.cursor(cursor_factory=RealDictCursor)
        status = request.args.get('status', 'pending')

        cursor.execute('''
            SELECT
                id, brand_name, brand_email, brand_website, brand_category,
                brand_logo_url, product_name, campaign_description, pr_value_usd,
                creator_count_range, shipping_regions, follower_ranges,
                content_types, creator_niches, additional_notes, application_deadline,
                spots_total, spots_filled, status, created_at, published_at, closes_at
            FROM opportunities
            WHERE status = %s
            ORDER BY created_at DESC
        ''', (status,))
        opps = cursor.fetchall()

        cursor.close()
        conn.close()

        # Convert datetime objects to strings
        opportunities = []
        for opp in opps:
            opportunities.append({
                'id': opp['id'],
                'brand_name': opp['brand_name'],
                'brand_email': opp['brand_email'],
                'brand_website': opp['brand_website'],
                'brand_category': opp['brand_category'],
                'brand_logo_url': opp['brand_logo_url'],
                'product_name': opp['product_name'],
                'campaign_description': opp['campaign_description'],
                'pr_value_usd': opp['pr_value_usd'],
                'creator_count_range': opp['creator_count_range'],
                'shipping_regions': opp['shipping_regions'] or [],
                'follower_ranges': opp['follower_ranges'] or [],
                'content_types': opp['content_types'] or [],
                'creator_niches': opp['creator_niches'] or [],
                'additional_notes': opp['additional_notes'],
                'application_deadline': opp['application_deadline'].isoformat() if opp['application_deadline'] else None,
                'spots_total': opp['spots_total'],
                'spots_filled': opp['spots_filled'],
                'status': opp['status'],
                'created_at': opp['created_at'].isoformat() if opp['created_at'] else None,
                'published_at': opp['published_at'].isoformat() if opp['published_at'] else None,
                'closes_at': opp['closes_at'].isoformat() if opp['closes_at'] else None
            })

        return jsonify({
            'success': True,
            'opportunities': opportunities
        })

    except Exception as e:
        print(f"Error in admin_list: {str(e)}")
        return jsonify({'success': False, 'error': str(e)}), 500


# ============================================
# ADMIN: Update opportunity (logo, etc)
# ============================================

@opportunities_bp.route('/admin/<int:opp_id>/update', methods=['PATCH'])
@admin_required
def admin_update(opp_id):
    """Admin updates opportunity details (logo URL, etc)"""
    try:
        conn = get_db_connection()
        cursor = conn.cursor(cursor_factory=RealDictCursor)
        data = request.get_json() or {}

        # Build update query dynamically
        updates = []
        values = []

        if 'brand_logo_url' in data:
            updates.append('brand_logo_url = %s')
            values.append(data['brand_logo_url'])

        if not updates:
            return jsonify({'success': False, 'error': 'No updates provided'}), 400

        values.append(opp_id)
        cursor.execute(f'''
            UPDATE opportunities
            SET {', '.join(updates)}
            WHERE id = %s
        ''', values)

        conn.commit()
        cursor.close()
        conn.close()

        return jsonify({'success': True})

    except Exception as e:
        print(f"Error in admin_update: {str(e)}")
        return jsonify({'success': False, 'error': str(e)}), 500


# ============================================
# ADMIN: Publish opportunity
# ============================================

@opportunities_bp.route('/admin/<int:opp_id>/publish', methods=['PATCH'])
@admin_required
def admin_publish(opp_id):
    """Admin publishes an opportunity"""
    try:
        conn = get_db_connection()
        cursor = conn.cursor(cursor_factory=RealDictCursor)
        data = request.get_json() or {}
        days_open = data.get('days_open', 14)

        # Get opportunity for email and application_deadline
        cursor.execute('SELECT brand_email, brand_name, product_name, application_deadline FROM opportunities WHERE id = %s', (opp_id,))
        opp = cursor.fetchone()

        if not opp:
            cursor.close()
            conn.close()
            return jsonify({'success': False, 'error': 'Opportunity not found'}), 404

        # Use brand's application_deadline if set, otherwise calculate from now
        if opp.get('application_deadline'):
            cursor.execute('''
                UPDATE opportunities
                SET status = 'live',
                    published_at = NOW(),
                    closes_at = %s
                WHERE id = %s
            ''', (opp['application_deadline'], opp_id))
        else:
            cursor.execute('''
                UPDATE opportunities
                SET status = 'live',
                    published_at = NOW(),
                    closes_at = NOW() + INTERVAL '%s days'
                WHERE id = %s
            ''', (days_open, opp_id))

        conn.commit()
        cursor.close()
        conn.close()

        # Notify brand
        send_email_notification(
            opp['brand_email'],
            f'Your Newcollab listing is live',
            f'Your opportunity for {opp["product_name"]} is now live on Newcollab.\n\n'
            f'Creators will start applying within the next 24 hours.\n\n'
            f'Best,\nNewcollab Team'
        )

        return jsonify({'success': True})

    except Exception as e:
        print(f"Error in admin_publish: {str(e)}")
        return jsonify({'success': False, 'error': str(e)}), 500


# ============================================
# ADMIN: Reject opportunity
# ============================================

@opportunities_bp.route('/admin/<int:opp_id>/reject', methods=['PATCH'])
@admin_required
def admin_reject(opp_id):
    """Admin rejects an opportunity"""
    try:
        conn = get_db_connection()
        cursor = conn.cursor(cursor_factory=RealDictCursor)
        data = request.get_json() or {}
        reason = data.get('reason', '')

        # Get opportunity for email
        cursor.execute('SELECT brand_email, brand_name, product_name FROM opportunities WHERE id = %s', (opp_id,))
        opp = cursor.fetchone()

        if not opp:
            cursor.close()
            conn.close()
            return jsonify({'success': False, 'error': 'Opportunity not found'}), 404

        cursor.execute('''
            UPDATE opportunities
            SET status = 'rejected'
            WHERE id = %s
        ''', (opp_id,))

        conn.commit()
        cursor.close()
        conn.close()

        # Notify brand
        reason_text = f'\n\nReason: {reason}' if reason else ''
        send_email_notification(
            opp['brand_email'],
            'Your Newcollab listing needs some changes',
            f'Thanks for submitting to Newcollab. We were not able to publish '
            f'your listing for {opp["product_name"]} at this time.'
            f'{reason_text}\n\n'
            f'Feel free to resubmit or email us at brands@newcollab.co.\n\n'
            f'Best,\nNewcollab Team'
        )

        return jsonify({'success': True})

    except Exception as e:
        print(f"Error in admin_reject: {str(e)}")
        return jsonify({'success': False, 'error': str(e)}), 500


# ============================================
# ADMIN: Get opportunity applications
# ============================================

@opportunities_bp.route('/admin/<int:opp_id>/applications', methods=['GET'])
@admin_required
def admin_get_applications(opp_id):
    """Admin gets applications for an opportunity"""
    try:
        conn = get_db_connection()
        cursor = conn.cursor(cursor_factory=RealDictCursor)
        cursor.execute('''
            SELECT
                oa.id, oa.applied_at, oa.status,
                c.id as creator_id, c.username, c.followers_count,
                c.niche, c.kit_slug, c.image_profile
            FROM opportunity_applications oa
            JOIN creators c ON oa.creator_id = c.id
            WHERE oa.opportunity_id = %s
            ORDER BY oa.applied_at DESC
        ''', (opp_id,))
        apps = cursor.fetchall()

        cursor.close()
        conn.close()

        applications = []
        for app in apps:
            applications.append({
                'id': app['id'],
                'applied_at': app['applied_at'].isoformat() if app['applied_at'] else None,
                'status': app['status'],
                'creator': {
                    'id': app['creator_id'],
                    'display_name': app['username'],
                    'follower_count': app['followers_count'],
                    'niches': [app['niche']] if app['niche'] else [],
                    'slug': app['kit_slug'] or app['username'],
                    'avatar_url': app['image_profile']
                }
            })

        return jsonify({
            'success': True,
            'applications': applications
        })

    except Exception as e:
        print(f"Error in admin_get_applications: {str(e)}")
        return jsonify({'success': False, 'error': str(e)}), 500


# ============================================
# ADMIN: Close/Pause opportunity
# ============================================

@opportunities_bp.route('/admin/<int:opp_id>/close', methods=['PATCH'])
@admin_required
def admin_close(opp_id):
    """Admin closes or pauses an opportunity"""
    try:
        conn = get_db_connection()
        cursor = conn.cursor(cursor_factory=RealDictCursor)
        data = request.get_json() or {}
        new_status = data.get('status', 'closed')  # 'closed' or 'paused'

        if new_status not in ['closed', 'paused']:
            return jsonify({'success': False, 'error': 'Invalid status'}), 400

        cursor.execute('''
            UPDATE opportunities
            SET status = %s
            WHERE id = %s
        ''', (new_status, opp_id))

        conn.commit()
        cursor.close()
        conn.close()

        return jsonify({'success': True})

    except Exception as e:
        print(f"Error in admin_close: {str(e)}")
        return jsonify({'success': False, 'error': str(e)}), 500


# ============================================
# ADMIN: Reopen opportunity
# ============================================

@opportunities_bp.route('/admin/<int:opp_id>/reopen', methods=['PATCH'])
@admin_required
def admin_reopen(opp_id):
    """Admin reopens a closed/paused opportunity"""
    try:
        conn = get_db_connection()
        cursor = conn.cursor(cursor_factory=RealDictCursor)
        data = request.get_json() or {}
        days_open = data.get('days_open', 14)

        cursor.execute('''
            UPDATE opportunities
            SET status = 'live',
                closes_at = NOW() + INTERVAL '%s days'
            WHERE id = %s
        ''', (days_open, opp_id))

        conn.commit()
        cursor.close()
        conn.close()

        return jsonify({'success': True})

    except Exception as e:
        print(f"Error in admin_reopen: {str(e)}")
        return jsonify({'success': False, 'error': str(e)}), 500


# ============================================
# ADMIN: Edit opportunity (full update)
# ============================================

@opportunities_bp.route('/admin/<int:opp_id>/edit', methods=['PATCH'])
@admin_required
def admin_edit(opp_id):
    """Admin edits opportunity details"""
    try:
        conn = get_db_connection()
        cursor = conn.cursor(cursor_factory=RealDictCursor)
        data = request.get_json() or {}

        # Build update query dynamically for allowed fields
        allowed_fields = [
            'brand_name', 'brand_website', 'brand_email', 'brand_category',
            'brand_logo_url', 'product_name', 'campaign_description',
            'pr_value_usd', 'creator_count_range', 'shipping_regions',
            'follower_ranges', 'content_types', 'creator_niches',
            'additional_notes', 'spots_total'
        ]

        updates = []
        values = []

        for field in allowed_fields:
            if field in data:
                updates.append(f'{field} = %s')
                values.append(data[field])

        if not updates:
            return jsonify({'success': False, 'error': 'No updates provided'}), 400

        values.append(opp_id)
        cursor.execute(f'''
            UPDATE opportunities
            SET {', '.join(updates)}
            WHERE id = %s
        ''', values)

        conn.commit()
        cursor.close()
        conn.close()

        return jsonify({'success': True})

    except Exception as e:
        print(f"Error in admin_edit: {str(e)}")
        return jsonify({'success': False, 'error': str(e)}), 500


# ============================================
# ADMIN: Delete opportunity
# ============================================

@opportunities_bp.route('/admin/<int:opp_id>/delete', methods=['DELETE'])
@admin_required
def admin_delete(opp_id):
    """Admin deletes an opportunity"""
    try:
        conn = get_db_connection()
        cursor = conn.cursor(cursor_factory=RealDictCursor)

        # Delete applications first (should cascade, but be explicit)
        cursor.execute('DELETE FROM opportunity_applications WHERE opportunity_id = %s', (opp_id,))

        # Delete opportunity
        cursor.execute('DELETE FROM opportunities WHERE id = %s', (opp_id,))

        conn.commit()
        cursor.close()
        conn.close()

        return jsonify({'success': True})

    except Exception as e:
        print(f"Error in admin_delete: {str(e)}")
        return jsonify({'success': False, 'error': str(e)}), 500
