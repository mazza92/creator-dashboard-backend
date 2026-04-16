"""
Admin Email Campaign API Routes
Email marketing system for creator engagement
Using Gmail SMTP for sending
"""

from flask import Blueprint, request, jsonify, session
from functools import wraps
from psycopg2.extras import RealDictCursor
from datetime import datetime, timedelta
import sys
import os
import json
import smtplib
import time
import threading
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import psycopg2

# Gmail SMTP Configuration (uses existing env vars)
GMAIL_USER = os.getenv('SMTP_USERNAME', 'team@newcollab.co')
GMAIL_APP_PASSWORD = os.getenv('SMTP_PASSWORD')


def get_db_connection():
    """Get database connection"""
    return psycopg2.connect(os.getenv('DATABASE_URL'), cursor_factory=RealDictCursor)


admin_email_bp = Blueprint('admin_email', __name__, url_prefix='/api/admin/email')


# ============================================================================
# AUTHENTICATION DECORATOR
# ============================================================================

def admin_required(f):
    """Require admin authentication via X-Admin-Token header"""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        admin_token = request.headers.get('X-Admin-Token')
        if admin_token == 'pr-hunter-admin-2026':
            return f(*args, **kwargs)

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
# TEMPLATES ENDPOINTS
# ============================================================================

@admin_email_bp.route('/templates', methods=['GET'])
@admin_required
def get_templates():
    """Get all email templates"""
    try:
        conn = get_db_connection()
        cursor = conn.cursor(cursor_factory=RealDictCursor)

        cursor.execute("""
            SELECT id, name, type, subject, preview_text, html_content, variables, is_active, created_at
            FROM campaign_templates
            WHERE is_active = true
            ORDER BY type, name
        """)
        templates = cursor.fetchall()
        conn.close()

        return jsonify({'templates': templates})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@admin_email_bp.route('/templates/<int:template_id>', methods=['GET'])
@admin_required
def get_template(template_id):
    """Get single template by ID"""
    try:
        conn = get_db_connection()
        cursor = conn.cursor(cursor_factory=RealDictCursor)

        cursor.execute("SELECT * FROM campaign_templates WHERE id = %s", (template_id,))
        template = cursor.fetchone()
        conn.close()

        if not template:
            return jsonify({'error': 'Template not found'}), 404

        return jsonify({'template': template})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@admin_email_bp.route('/templates', methods=['POST'])
@admin_required
def create_template():
    """Create new email template"""
    try:
        data = request.get_json()
        conn = get_db_connection()
        cursor = conn.cursor(cursor_factory=RealDictCursor)

        cursor.execute("""
            INSERT INTO campaign_templates (name, type, subject, preview_text, html_content, variables)
            VALUES (%s, %s, %s, %s, %s, %s)
            RETURNING id
        """, (
            data['name'],
            data['type'],
            data['subject'],
            data.get('preview_text', ''),
            data['html_content'],
            json.dumps(data.get('variables', []))
        ))

        template_id = cursor.fetchone()['id']
        conn.commit()
        conn.close()

        return jsonify({'id': template_id, 'message': 'Template created'})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@admin_email_bp.route('/templates/<int:template_id>', methods=['PUT'])
@admin_required
def update_template(template_id):
    """Update email template"""
    try:
        data = request.get_json()
        conn = get_db_connection()
        cursor = conn.cursor(cursor_factory=RealDictCursor)

        cursor.execute("""
            UPDATE campaign_templates
            SET name = %s, type = %s, subject = %s, preview_text = %s,
                html_content = %s, variables = %s, updated_at = NOW()
            WHERE id = %s
        """, (
            data['name'],
            data['type'],
            data['subject'],
            data.get('preview_text', ''),
            data['html_content'],
            json.dumps(data.get('variables', [])),
            template_id
        ))

        conn.commit()
        conn.close()

        return jsonify({'message': 'Template updated'})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


# ============================================================================
# SEGMENTS ENDPOINTS
# ============================================================================

@admin_email_bp.route('/segments', methods=['GET'])
@admin_required
def get_segments():
    """Get pre-built segments with user counts"""
    try:
        conn = get_db_connection()
        cursor = conn.cursor(cursor_factory=RealDictCursor)

        segments = []

        # All Active Users
        cursor.execute("""
            SELECT COUNT(DISTINCT c.id) as count
            FROM creators c
            JOIN users u ON c.user_id = u.id
            WHERE u.unsubscribed_at IS NULL
        """)
        segments.append({
            'id': 'all_active',
            'name': 'All Active Users',
            'description': 'All creators with profiles',
            'count': cursor.fetchone()['count'],
            'icon': 'team'
        })

        # New Users (7 days)
        cursor.execute("""
            SELECT COUNT(DISTINCT c.id) as count
            FROM creators c
            JOIN users u ON c.user_id = u.id
            WHERE u.created_at >= NOW() - INTERVAL '7 days'
            AND u.unsubscribed_at IS NULL
        """)
        segments.append({
            'id': 'new_users_7d',
            'name': 'New Users (7 days)',
            'description': 'Signed up in the last 7 days',
            'count': cursor.fetchone()['count'],
            'icon': 'user-add'
        })

        # Exploring - Saved but never pitched
        cursor.execute("""
            SELECT COUNT(DISTINCT c.id) as count
            FROM creators c
            JOIN users u ON c.user_id = u.id
            WHERE c.id IN (
                SELECT DISTINCT creator_id FROM creator_pipeline WHERE pitched_at IS NULL
            )
            AND c.id NOT IN (
                SELECT DISTINCT creator_id FROM creator_pipeline WHERE pitched_at IS NOT NULL
            )
            AND u.unsubscribed_at IS NULL
        """)
        segments.append({
            'id': 'exploring',
            'name': 'Exploring (Saved, No Pitch)',
            'description': 'Saved brands but never sent a pitch - need nudge',
            'count': cursor.fetchone()['count'],
            'icon': 'eye',
            'highlight': True
        })

        # Engaged - Has pitched
        cursor.execute("""
            SELECT COUNT(DISTINCT c.id) as count
            FROM creators c
            JOIN users u ON c.user_id = u.id
            WHERE c.id IN (
                SELECT DISTINCT creator_id FROM creator_pipeline WHERE pitched_at IS NOT NULL
            )
            AND u.unsubscribed_at IS NULL
        """)
        segments.append({
            'id': 'engaged',
            'name': 'Engaged (1+ Pitches)',
            'description': 'Has sent at least one pitch',
            'count': cursor.fetchone()['count'],
            'icon': 'check-circle'
        })

        # Power Users
        cursor.execute("""
            SELECT COUNT(DISTINCT c.id) as count
            FROM creators c
            JOIN users u ON c.user_id = u.id
            WHERE COALESCE(c.pitches_sent_total, 0) >= 5
            AND u.unsubscribed_at IS NULL
        """)
        segments.append({
            'id': 'power_users',
            'name': 'Power Users (5+ Pitches)',
            'description': 'Most active users - great for testimonials',
            'count': cursor.fetchone()['count'],
            'icon': 'trophy'
        })

        # At Quota Limit
        cursor.execute("""
            SELECT COUNT(DISTINCT c.id) as count
            FROM creators c
            JOIN users u ON c.user_id = u.id
            WHERE COALESCE(c.pitches_sent_this_week, 0) >= 3
            AND COALESCE(c.subscription_tier, 'free') = 'free'
            AND u.unsubscribed_at IS NULL
        """)
        segments.append({
            'id': 'at_quota_limit',
            'name': 'At Pitch Limit (3/3)',
            'description': 'Free users who hit weekly limit - upgrade candidates!',
            'count': cursor.fetchone()['count'],
            'icon': 'thunderbolt',
            'highlight': True
        })

        # Dormant
        cursor.execute("""
            SELECT COUNT(DISTINCT c.id) as count
            FROM creators c
            JOIN users u ON c.user_id = u.id
            WHERE c.id NOT IN (
                SELECT DISTINCT creator_id
                FROM creator_pipeline
                WHERE created_at >= NOW() - INTERVAL '14 days'
                   OR pitched_at >= NOW() - INTERVAL '14 days'
            )
            AND u.created_at < NOW() - INTERVAL '14 days'
            AND u.unsubscribed_at IS NULL
        """)
        segments.append({
            'id': 'dormant',
            'name': 'Dormant (14+ days)',
            'description': 'No activity in 2+ weeks - win-back candidates',
            'count': cursor.fetchone()['count'],
            'icon': 'clock-circle'
        })

        # Free Tier
        cursor.execute("""
            SELECT COUNT(DISTINCT c.id) as count
            FROM creators c
            JOIN users u ON c.user_id = u.id
            WHERE COALESCE(c.subscription_tier, 'free') = 'free'
            AND u.unsubscribed_at IS NULL
        """)
        segments.append({
            'id': 'free_tier',
            'name': 'Free Tier Users',
            'description': 'All free users',
            'count': cursor.fetchone()['count'],
            'icon': 'user'
        })

        conn.close()
        return jsonify({'segments': segments})

    except Exception as e:
        return jsonify({'error': str(e)}), 500


@admin_email_bp.route('/segments/preview', methods=['POST'])
@admin_required
def preview_segment():
    """Preview users in a segment"""
    try:
        data = request.get_json()
        segment_id = data.get('segment_id', 'all_active')
        limit = data.get('limit', 50)

        conn = get_db_connection()
        cursor = conn.cursor(cursor_factory=RealDictCursor)

        # Base query
        base_query = """
            SELECT
                c.id as creator_id,
                u.id as user_id,
                u.email,
                u.first_name,
                c.username,
                c.niche,
                c.followers_count,
                COALESCE(c.subscription_tier, 'free') as tier,
                COALESCE(c.pitches_sent_this_week, 0) as pitches_this_week,
                COALESCE(c.pitches_sent_total, 0) as pitches_total,
                COALESCE(c.brands_saved_count, 0) as brands_saved,
                u.created_at as signup_date
            FROM creators c
            JOIN users u ON c.user_id = u.id
            WHERE u.unsubscribed_at IS NULL
        """

        # Add segment conditions
        if segment_id == 'new_users_7d':
            base_query += " AND u.created_at >= NOW() - INTERVAL '7 days'"
        elif segment_id == 'exploring':
            base_query += """
                AND c.id IN (SELECT DISTINCT creator_id FROM creator_pipeline WHERE pitched_at IS NULL)
                AND c.id NOT IN (SELECT DISTINCT creator_id FROM creator_pipeline WHERE pitched_at IS NOT NULL)
            """
        elif segment_id == 'engaged':
            base_query += " AND c.id IN (SELECT DISTINCT creator_id FROM creator_pipeline WHERE pitched_at IS NOT NULL)"
        elif segment_id == 'power_users':
            base_query += " AND COALESCE(c.pitches_sent_total, 0) >= 5"
        elif segment_id == 'at_quota_limit':
            base_query += " AND COALESCE(c.pitches_sent_this_week, 0) >= 3 AND COALESCE(c.subscription_tier, 'free') = 'free'"
        elif segment_id == 'dormant':
            base_query += """
                AND c.id NOT IN (
                    SELECT DISTINCT creator_id FROM creator_pipeline
                    WHERE created_at >= NOW() - INTERVAL '14 days' OR pitched_at >= NOW() - INTERVAL '14 days'
                )
                AND u.created_at < NOW() - INTERVAL '14 days'
            """
        elif segment_id == 'free_tier':
            base_query += " AND COALESCE(c.subscription_tier, 'free') = 'free'"

        # Get total count first
        count_query = f"SELECT COUNT(*) as total FROM ({base_query}) sub"
        cursor.execute(count_query)
        total_count = cursor.fetchone()['total']

        # Get sample users
        base_query += f" ORDER BY u.created_at DESC LIMIT {limit}"
        cursor.execute(base_query)
        users = cursor.fetchall()

        conn.close()

        return jsonify({
            'users': users,
            'total_count': total_count,
            'showing': len(users)
        })

    except Exception as e:
        return jsonify({'error': str(e)}), 500


# ============================================================================
# CAMPAIGNS ENDPOINTS
# ============================================================================

@admin_email_bp.route('/campaigns', methods=['GET'])
@admin_required
def get_campaigns():
    """Get all campaigns"""
    try:
        conn = get_db_connection()
        cursor = conn.cursor(cursor_factory=RealDictCursor)

        cursor.execute("""
            SELECT
                ec.*,
                et.name as template_name,
                et.type as template_type
            FROM email_campaigns ec
            LEFT JOIN campaign_templates et ON ec.template_id = et.id
            ORDER BY ec.created_at DESC
            LIMIT 50
        """)
        campaigns = cursor.fetchall()
        conn.close()

        return jsonify({'campaigns': campaigns})

    except Exception as e:
        return jsonify({'error': str(e)}), 500


@admin_email_bp.route('/campaigns', methods=['POST'])
@admin_required
def create_campaign():
    """Create a new campaign"""
    try:
        data = request.get_json()
        conn = get_db_connection()
        cursor = conn.cursor(cursor_factory=RealDictCursor)

        cursor.execute("""
            INSERT INTO email_campaigns
            (name, template_id, subject_override, html_content_override, segment_type, segment_filters, status, created_by)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING id
        """, (
            data['name'],
            data.get('template_id'),
            data.get('subject_override'),
            data.get('html_content_override'),
            data.get('segment_type', 'all_active'),
            json.dumps(data.get('segment_filters', {})),
            'draft',
            'admin'
        ))

        campaign_id = cursor.fetchone()['id']
        conn.commit()
        conn.close()

        return jsonify({'id': campaign_id, 'message': 'Campaign created'})

    except Exception as e:
        return jsonify({'error': str(e)}), 500


@admin_email_bp.route('/campaigns/<int:campaign_id>', methods=['GET'])
@admin_required
def get_campaign(campaign_id):
    """Get campaign details"""
    try:
        conn = get_db_connection()
        cursor = conn.cursor(cursor_factory=RealDictCursor)

        cursor.execute("""
            SELECT
                ec.*,
                et.name as template_name,
                et.type as template_type,
                et.subject as template_subject,
                et.html_content as template_content
            FROM email_campaigns ec
            LEFT JOIN campaign_templates et ON ec.template_id = et.id
            WHERE ec.id = %s
        """, (campaign_id,))

        campaign = cursor.fetchone()
        conn.close()

        if not campaign:
            return jsonify({'error': 'Campaign not found'}), 404

        return jsonify({'campaign': campaign})

    except Exception as e:
        return jsonify({'error': str(e)}), 500


def _send_emails_background(campaign_id, recipients, subject, html_content):
    """Background thread function to send emails with rate limiting"""
    print(f"[Background] Starting to send {len(recipients)} emails for campaign {campaign_id}")

    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=RealDictCursor)

    sent_count = 0
    failed_count = 0

    BATCH_SIZE = 25
    DELAY_BETWEEN_EMAILS = 0.8

    try:
        for i, recipient in enumerate(recipients):
            try:
                personalized_subject = personalize_text(subject, recipient)
                personalized_content = personalize_text(html_content, recipient)

                success = send_email_gmail(
                    to_email=recipient['email'],
                    subject=personalized_subject,
                    html_content=personalized_content
                )

                status = 'sent' if success else 'failed'

                cursor.execute("""
                    INSERT INTO email_logs (campaign_id, user_id, creator_id, email, status, sent_at)
                    VALUES (%s, %s, %s, %s, %s, NOW())
                """, (campaign_id, recipient['user_id'], recipient['creator_id'], recipient['email'], status))

                if success:
                    sent_count += 1
                else:
                    failed_count += 1
                    print(f"[Background] Failed to send to {recipient['email']}")

                # Commit progress in batches
                if (i + 1) % BATCH_SIZE == 0:
                    cursor.execute("""
                        UPDATE email_campaigns SET total_sent = %s WHERE id = %s
                    """, (sent_count, campaign_id))
                    conn.commit()
                    print(f"[Background] Progress: {i + 1}/{len(recipients)} sent, {sent_count} successful")

                # Rate limiting delay (skip after last email)
                if i < len(recipients) - 1:
                    time.sleep(DELAY_BETWEEN_EMAILS)

            except Exception as e:
                print(f"[Background] Error sending to {recipient['email']}: {str(e)}")
                try:
                    cursor.execute("""
                        INSERT INTO email_logs (campaign_id, user_id, creator_id, email, status, error_message)
                        VALUES (%s, %s, %s, %s, 'failed', %s)
                    """, (campaign_id, recipient['user_id'], recipient['creator_id'], recipient['email'], str(e)))
                    conn.commit()
                except Exception:
                    pass
                failed_count += 1

        # Mark campaign as fully sent
        cursor.execute("""
            UPDATE email_campaigns
            SET status = 'sent', sent_at = NOW(), total_sent = %s
            WHERE id = %s
        """, (sent_count, campaign_id))
        conn.commit()
        print(f"[Background] Campaign {campaign_id} complete: {sent_count} sent, {failed_count} failed")

    except Exception as e:
        # Unexpected crash — mark campaign as failed so it doesn't stay stuck as 'sending'
        print(f"[Background] FATAL error for campaign {campaign_id}: {str(e)}")
        try:
            cursor.execute("""
                UPDATE email_campaigns
                SET status = 'failed', total_sent = %s
                WHERE id = %s
            """, (sent_count, campaign_id))
            conn.commit()
        except Exception:
            pass

    finally:
        try:
            conn.close()
        except Exception:
            pass


@admin_email_bp.route('/campaigns/<int:campaign_id>/reset', methods=['POST'])
@admin_required
def reset_campaign(campaign_id):
    """Reset a stuck/failed campaign back to draft so it can be re-sent"""
    try:
        conn = get_db_connection()
        cursor = conn.cursor(cursor_factory=RealDictCursor)

        cursor.execute("""
            UPDATE email_campaigns
            SET status = 'draft', sent_at = NULL
            WHERE id = %s
            RETURNING id, status
        """, (campaign_id,))
        row = cursor.fetchone()
        conn.commit()
        conn.close()

        if not row:
            return jsonify({'error': 'Campaign not found'}), 404

        return jsonify({'message': 'Campaign reset to draft', 'campaign_id': campaign_id})

    except Exception as e:
        return jsonify({'error': str(e)}), 500


@admin_email_bp.route('/campaigns/<int:campaign_id>/send', methods=['POST'])
@admin_required
def send_campaign(campaign_id):
    """Send campaign to all users in segment via Gmail SMTP (async)"""
    try:
        conn = get_db_connection()
        cursor = conn.cursor(cursor_factory=RealDictCursor)

        # Get campaign
        cursor.execute("""
            SELECT ec.*, et.subject as template_subject, et.html_content as template_html_content
            FROM email_campaigns ec
            LEFT JOIN campaign_templates et ON ec.template_id = et.id
            WHERE ec.id = %s
        """, (campaign_id,))

        campaign = cursor.fetchone()

        if not campaign:
            conn.close()
            return jsonify({'error': 'Campaign not found'}), 404

        # Only block if fully sent — 'sending' may be a stuck/crashed thread, allow resume
        if campaign['status'] == 'sent':
            conn.close()
            return jsonify({'error': 'Campaign already fully sent. Use the reset endpoint to re-send.'}), 400

        # Get recipients
        segment_id = campaign['segment_type']

        recipient_query = """
            SELECT
                c.id as creator_id, u.id as user_id, u.email, u.first_name,
                c.username, c.niche, c.followers_count,
                COALESCE(c.subscription_tier, 'free') as tier,
                COALESCE(c.pitches_sent_this_week, 0) as pitches_this_week,
                COALESCE(c.pitches_sent_total, 0) as pitches_total,
                COALESCE(c.brands_saved_count, 0) as brands_saved
            FROM creators c
            JOIN users u ON c.user_id = u.id
            WHERE u.unsubscribed_at IS NULL
        """

        if segment_id == 'new_users_7d':
            recipient_query += " AND u.created_at >= NOW() - INTERVAL '7 days'"
        elif segment_id == 'exploring':
            recipient_query += """
                AND c.id IN (SELECT DISTINCT creator_id FROM creator_pipeline WHERE pitched_at IS NULL)
                AND c.id NOT IN (SELECT DISTINCT creator_id FROM creator_pipeline WHERE pitched_at IS NOT NULL)
            """
        elif segment_id == 'at_quota_limit':
            recipient_query += " AND COALESCE(c.pitches_sent_this_week, 0) >= 3 AND COALESCE(c.subscription_tier, 'free') = 'free'"
        elif segment_id == 'dormant':
            recipient_query += """
                AND c.id NOT IN (
                    SELECT DISTINCT creator_id FROM creator_pipeline
                    WHERE created_at >= NOW() - INTERVAL '14 days' OR pitched_at >= NOW() - INTERVAL '14 days'
                )
                AND u.created_at < NOW() - INTERVAL '14 days'
            """
        elif segment_id == 'free_tier':
            recipient_query += " AND COALESCE(c.subscription_tier, 'free') = 'free'"
        elif segment_id == 'engaged':
            recipient_query += " AND c.id IN (SELECT DISTINCT creator_id FROM creator_pipeline WHERE pitched_at IS NOT NULL)"
        elif segment_id == 'power_users':
            recipient_query += " AND COALESCE(c.pitches_sent_total, 0) >= 5"

        cursor.execute(recipient_query)
        all_recipients = cursor.fetchall()

        # Exclude users who already received this campaign
        cursor.execute("""
            SELECT email FROM email_logs
            WHERE campaign_id = %s AND status = 'sent'
        """, (campaign_id,))
        already_sent = {row['email'] for row in cursor.fetchall()}

        recipients = [r for r in all_recipients if r['email'] not in already_sent]

        if not recipients:
            conn.close()
            return jsonify({
                'message': 'No new recipients to send to',
                'sent': 0,
                'failed': 0,
                'already_sent': len(already_sent)
            })

        # Update status to sending
        cursor.execute("""
            UPDATE email_campaigns
            SET status = 'sending', total_recipients = %s
            WHERE id = %s
        """, (len(all_recipients), campaign_id))
        conn.commit()

        subject = campaign['subject_override'] or campaign.get('template_subject') or 'New update from Newcollab'
        html_content = campaign.get('html_content_override') or campaign.get('template_html_content')

        if not html_content:
            conn.close()
            return jsonify({'error': 'Campaign has no email content'}), 400

        conn.close()

        # Start background thread to send emails
        thread = threading.Thread(
            target=_send_emails_background,
            args=(campaign_id, recipients, subject, html_content)
        )
        thread.daemon = True
        thread.start()

        # Return immediately — emails are being sent in the background thread
        return jsonify({
            'message': f'Sending started for {len(recipients)} recipients',
            'sending': len(recipients),
            'already_sent': len(already_sent),
            'status': 'sending'
        })

    except Exception as e:
        return jsonify({'error': str(e)}), 500


@admin_email_bp.route('/campaigns/<int:campaign_id>/send-status', methods=['GET'])
@admin_required
def get_send_status(campaign_id):
    """Poll live sending progress for a campaign"""
    try:
        conn = get_db_connection()
        cursor = conn.cursor(cursor_factory=RealDictCursor)

        cursor.execute("""
            SELECT status, total_recipients, total_sent
            FROM email_campaigns
            WHERE id = %s
        """, (campaign_id,))
        campaign = cursor.fetchone()

        if not campaign:
            conn.close()
            return jsonify({'error': 'Campaign not found'}), 404

        cursor.execute("""
            SELECT
                COUNT(*) FILTER (WHERE status = 'sent')   AS sent,
                COUNT(*) FILTER (WHERE status = 'failed') AS failed
            FROM email_logs
            WHERE campaign_id = %s
        """, (campaign_id,))
        counts = cursor.fetchone()
        conn.close()

        total = campaign['total_recipients'] or 0
        sent = counts['sent'] or 0
        failed = counts['failed'] or 0
        progress = round((sent + failed) / total * 100) if total else 0

        return jsonify({
            'status': campaign['status'],
            'total_recipients': total,
            'sent': sent,
            'failed': failed,
            'progress': progress
        })

    except Exception as e:
        return jsonify({'error': str(e)}), 500


@admin_email_bp.route('/campaigns/<int:campaign_id>/test', methods=['POST'])
@admin_required
def send_test_email(campaign_id):
    """Send test email to specified address"""
    try:
        data = request.get_json()
        test_email = data.get('email', 'team@newcollab.co')

        conn = get_db_connection()
        cursor = conn.cursor(cursor_factory=RealDictCursor)

        cursor.execute("""
            SELECT ec.*, et.subject as template_subject, et.html_content as template_html_content
            FROM email_campaigns ec
            LEFT JOIN campaign_templates et ON ec.template_id = et.id
            WHERE ec.id = %s
        """, (campaign_id,))

        campaign = cursor.fetchone()
        conn.close()

        if not campaign:
            return jsonify({'error': 'Campaign not found'}), 404

        # Sample data for personalization
        sample = {
            'first_name': 'Test',
            'username': 'testcreator',
            'email': test_email,
            'niche': 'Beauty & Lifestyle',
            'followers_count': 10000,
            'tier': 'free',
            'pitches_this_week': 2,
            'pitches_total': 5,
            'brands_saved': 12
        }

        subject = campaign['subject_override'] or campaign.get('template_subject') or 'New update from Newcollab'
        html_content = campaign.get('html_content_override') or campaign.get('template_html_content')

        if not html_content:
            return jsonify({'error': 'Campaign has no email content'}), 400
        personalized_subject = f"[TEST] {personalize_text(subject, sample)}"
        personalized_content = personalize_text(html_content, sample)

        success = send_email_gmail(test_email, personalized_subject, personalized_content)

        if success:
            return jsonify({'message': f'Test email sent to {test_email}'})
        else:
            return jsonify({'error': 'Failed to send test email. Check Gmail credentials.'}), 500

    except Exception as e:
        return jsonify({'error': str(e)}), 500


# ============================================================================
# STATS ENDPOINT
# ============================================================================

@admin_email_bp.route('/stats', methods=['GET'])
@admin_required
def get_email_stats():
    """Get overall email stats"""
    try:
        conn = get_db_connection()
        cursor = conn.cursor(cursor_factory=RealDictCursor)

        cursor.execute("SELECT COUNT(*) as count FROM email_campaigns WHERE status = 'sent'")
        total_campaigns = cursor.fetchone()['count']

        cursor.execute("SELECT COUNT(*) as count FROM email_logs WHERE status = 'sent'")
        total_sent = cursor.fetchone()['count']

        cursor.execute("SELECT COUNT(*) as count FROM email_logs WHERE status = 'opened'")
        total_opened = cursor.fetchone()['count']

        cursor.execute("SELECT COUNT(*) as count FROM users WHERE unsubscribed_at IS NOT NULL")
        unsubscribes = cursor.fetchone()['count']

        open_rate = round((total_opened / total_sent * 100), 1) if total_sent > 0 else 0

        cursor.execute("""
            SELECT ec.id, ec.name, ec.status, ec.sent_at, ec.total_recipients, ec.total_sent,
                   et.type as template_type
            FROM email_campaigns ec
            LEFT JOIN campaign_templates et ON ec.template_id = et.id
            ORDER BY ec.created_at DESC
            LIMIT 10
        """)
        recent = cursor.fetchall()

        conn.close()

        return jsonify({
            'total_campaigns': total_campaigns,
            'total_sent': total_sent,
            'total_opened': total_opened,
            'open_rate': open_rate,
            'unsubscribes': unsubscribes,
            'recent_campaigns': recent
        })

    except Exception as e:
        return jsonify({'error': str(e)}), 500


# ============================================================================
# HELPER FUNCTIONS
# ============================================================================

def personalize_text(text, recipient):
    """Replace template variables with recipient data"""
    if not text:
        return text

    pitches_remaining = 3 - recipient.get('pitches_this_week', 0)
    if recipient.get('tier', 'free') != 'free':
        pitches_remaining = 'unlimited'

    replacements = {
        '{{first_name}}': recipient.get('first_name') or recipient.get('username') or 'there',
        '{{username}}': recipient.get('username') or 'Creator',
        '{{email}}': recipient.get('email', ''),
        '{{niche}}': recipient.get('niche') or 'your niche',
        '{{followers_count}}': f"{recipient.get('followers_count', 0):,}",
        '{{tier}}': recipient.get('tier', 'free'),
        '{{pitches_this_week}}': str(recipient.get('pitches_this_week', 0)),
        '{{pitches_remaining}}': str(pitches_remaining),
        '{{pitches_sent_total}}': str(recipient.get('pitches_total', 0)),
        '{{brands_saved_count}}': str(recipient.get('brands_saved', 0)),
    }

    for var, value in replacements.items():
        text = text.replace(var, value)

    return text


def send_email_gmail(to_email, subject, html_content):
    """Send email via Gmail SMTP"""
    if not GMAIL_APP_PASSWORD:
        print(f"Warning: SMTP_PASSWORD not set. GMAIL_USER={GMAIL_USER}")
        return False

    try:
        msg = MIMEMultipart('alternative')
        msg['Subject'] = subject
        msg['From'] = f"Newcollab <{GMAIL_USER}>"
        msg['To'] = to_email

        html_part = MIMEText(html_content, 'html')
        msg.attach(html_part)

        # Connect to Gmail SMTP with TLS (port 587)
        with smtplib.SMTP('smtp.gmail.com', 587) as server:
            server.starttls()
            server.login(GMAIL_USER, GMAIL_APP_PASSWORD)
            server.sendmail(GMAIL_USER, to_email, msg.as_string())

        print(f"Email sent successfully to {to_email}")
        return True

    except Exception as e:
        print(f"Email send error: {e}")
        import traceback
        traceback.print_exc()
        return False
