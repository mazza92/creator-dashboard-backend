"""
Admin Email Campaign API Routes
Email marketing system for creator engagement
Using Gmail SMTP for sending
"""

from flask import Blueprint, request, jsonify, session
from functools import wraps
from psycopg2.extras import RealDictCursor, execute_values
from datetime import datetime, timedelta
import sys
import os
import json
import smtplib
import time
import threading
import re
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


MAX_SEND_ATTEMPTS = 3
STUCK_SENDING_TIMEOUT_MINUTES = 5  # Recipients stuck in 'sending' for this long are recovered
CRON_BATCH_SIZE = 25  # How many emails to send per cron invocation (~20s at 0.8s/email)


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


def _upsert_campaign_recipients(cursor, campaign_id, recipients):
    """Insert recipients once per campaign (idempotent)."""
    if not recipients:
        return

    rows = []
    for r in recipients:
        rows.append((
            campaign_id,
            r['user_id'],
            r['creator_id'],
            (r.get('email') or '').strip().lower(),
            r.get('first_name'),
            r.get('username'),
            r.get('niche'),
            r.get('followers_count') or 0,
            r.get('tier') or 'free',
            r.get('pitches_this_week') or 0,
            r.get('pitches_total') or 0,
            r.get('brands_saved') or 0,
        ))

    execute_values(
        cursor,
        """
        INSERT INTO email_campaign_recipients (
            campaign_id, user_id, creator_id, email, first_name, username, niche,
            followers_count, tier, pitches_this_week, pitches_total, brands_saved
        )
        VALUES %s
        ON CONFLICT (campaign_id, email) DO UPDATE
        SET user_id = EXCLUDED.user_id,
            creator_id = EXCLUDED.creator_id,
            first_name = EXCLUDED.first_name,
            username = EXCLUDED.username,
            niche = EXCLUDED.niche,
            followers_count = EXCLUDED.followers_count,
            tier = EXCLUDED.tier,
            pitches_this_week = EXCLUDED.pitches_this_week,
            pitches_total = EXCLUDED.pitches_total,
            brands_saved = EXCLUDED.brands_saved,
            updated_at = NOW()
        """,
        rows
    )


def _recipient_counts(cursor, campaign_id):
    cursor.execute(
        """
        SELECT
            COUNT(*) AS total,
            COUNT(*) FILTER (WHERE status = 'sent') AS sent,
            COUNT(*) FILTER (WHERE status = 'sending') AS sending,
            COUNT(*) FILTER (
                WHERE status IN ('pending', 'failed_temp') AND attempt_count < %s
            ) AS retryable,
            COUNT(*) FILTER (WHERE status IN ('failed_temp', 'failed_perm')) AS failed
        FROM email_campaign_recipients
        WHERE campaign_id = %s
        """,
        (MAX_SEND_ATTEMPTS, campaign_id)
    )
    return cursor.fetchone()


def _refresh_campaign_totals_from_recipients(cursor, campaign_id, final=False):
    """Sync campaign counters from recipient state table."""
    counts = _recipient_counts(cursor, campaign_id)
    total = counts['total'] or 0
    sent = counts['sent'] or 0
    sending = counts['sending'] or 0
    retryable = counts['retryable'] or 0

    # When a send batch finishes, status should represent unresolved recipients.
    if final:
        if sent == total and total > 0:
            new_status = 'sent'
        elif retryable > 0:
            new_status = 'failed'
        else:
            new_status = 'failed'
    else:
        new_status = 'sending' if (sending > 0 or retryable > 0) else ('sent' if sent == total and total > 0 else 'draft')

    cursor.execute(
        """
        UPDATE email_campaigns
        SET total_recipients = %s,
            total_sent = %s,
            status = %s,
            sent_at = CASE WHEN %s = 'sent' THEN NOW() ELSE sent_at END
        WHERE id = %s
        """,
        (total, sent, new_status, new_status, campaign_id)
    )
    return {
        'total': total,
        'sent': sent,
        'sending': sending,
        'retryable': retryable,
        'failed': counts['failed'] or 0,
        'status': new_status
    }


def _pick_recipients_for_send(cursor, campaign_id):
    """
    Atomically claim sendable recipients so parallel send requests don't double-send.
    """
    cursor.execute(
        """
        WITH picked AS (
            SELECT id
            FROM email_campaign_recipients
            WHERE campaign_id = %s
              AND status IN ('pending', 'failed_temp')
              AND attempt_count < %s
            ORDER BY id
            FOR UPDATE SKIP LOCKED
        )
        UPDATE email_campaign_recipients e
        SET status = 'sending',
            attempt_count = e.attempt_count + 1,
            last_attempt_at = NOW(),
            last_error = NULL,
            updated_at = NOW()
        FROM picked
        WHERE e.id = picked.id
        RETURNING
            e.id, e.user_id, e.creator_id, e.email, e.first_name, e.username, e.niche,
            e.followers_count, e.tier, e.pitches_this_week, e.pitches_total, e.brands_saved
        """,
        (campaign_id, MAX_SEND_ATTEMPTS)
    )
    return cursor.fetchall()


def _send_emails_background(campaign_id, recipients, subject, html_content):
    """Background thread function to send emails with rate limiting."""
    print(f"[Background] Starting to send {len(recipients)} emails for campaign {campaign_id}")

    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=RealDictCursor)

    BATCH_SIZE = 25
    DELAY_BETWEEN_EMAILS = 0.8
    sent_count = 0
    failed_count = 0

    try:
        for i, recipient in enumerate(recipients):
            recipient_id = recipient['id']
            recipient_email = (recipient.get('email') or '').strip().lower()
            try:
                personalized_subject = personalize_text(subject, recipient)
                personalized_content = personalize_text(html_content, recipient)

                success = send_email_gmail(
                    to_email=recipient_email,
                    subject=personalized_subject,
                    html_content=personalized_content
                )

                if success:
                    cursor.execute(
                        """
                        UPDATE email_campaign_recipients
                        SET status = 'sent',
                            last_error = NULL,
                            updated_at = NOW()
                        WHERE id = %s
                        """,
                        (recipient_id,)
                    )
                    cursor.execute(
                        """
                        INSERT INTO email_logs (campaign_id, user_id, creator_id, email, status, sent_at)
                        VALUES (%s, %s, %s, %s, 'sent', NOW())
                        """,
                        (campaign_id, recipient['user_id'], recipient['creator_id'], recipient_email)
                    )
                    sent_count += 1
                else:
                    cursor.execute(
                        """
                        UPDATE email_campaign_recipients
                        SET status = 'failed_temp',
                            last_error = 'SMTP send failed',
                            updated_at = NOW()
                        WHERE id = %s
                        """,
                        (recipient_id,)
                    )
                    cursor.execute(
                        """
                        INSERT INTO email_logs (campaign_id, user_id, creator_id, email, status, error_message)
                        VALUES (%s, %s, %s, %s, 'failed', %s)
                        """,
                        (campaign_id, recipient['user_id'], recipient['creator_id'], recipient_email, 'SMTP send failed')
                    )
                    failed_count += 1

                # Commit and refresh every batch for live progress
                if (i + 1) % BATCH_SIZE == 0:
                    _refresh_campaign_totals_from_recipients(cursor, campaign_id, final=False)
                    conn.commit()
                    print(f"[Background] Progress: {i + 1}/{len(recipients)} processed")

                if i < len(recipients) - 1:
                    time.sleep(DELAY_BETWEEN_EMAILS)

            except Exception as e:
                error_message = str(e)
                print(f"[Background] Error sending to {recipient_email}: {error_message}")
                cursor.execute(
                    """
                    UPDATE email_campaign_recipients
                    SET status = 'failed_temp',
                        last_error = %s,
                        updated_at = NOW()
                    WHERE id = %s
                    """,
                    (error_message, recipient_id)
                )
                cursor.execute(
                    """
                    INSERT INTO email_logs (campaign_id, user_id, creator_id, email, status, error_message)
                    VALUES (%s, %s, %s, %s, 'failed', %s)
                    """,
                    (campaign_id, recipient['user_id'], recipient['creator_id'], recipient_email, error_message)
                )
                failed_count += 1
                conn.commit()

        summary = _refresh_campaign_totals_from_recipients(cursor, campaign_id, final=True)
        conn.commit()
        print(
            f"[Background] Campaign {campaign_id} complete: "
            f"{summary['sent']} sent, {summary['failed']} failed"
        )

    except Exception as e:
        print(f"[Background] FATAL error for campaign {campaign_id}: {str(e)}")
        try:
            cursor.execute(
                """
                UPDATE email_campaigns
                SET status = 'failed'
                WHERE id = %s
                """,
                (campaign_id,)
            )
            conn.commit()
        except Exception:
            pass
    finally:
        try:
            conn.close()
        except Exception:
            pass


def _recover_stuck_sending(cursor):
    """
    Recover recipients stuck in 'sending' status for too long.
    This happens when the background thread crashes.
    """
    cursor.execute(
        """
        UPDATE email_campaign_recipients
        SET status = 'failed_temp',
            last_error = 'Sending thread interrupted - will retry',
            updated_at = NOW()
        WHERE status = 'sending'
          AND last_attempt_at < NOW() - INTERVAL '%s minutes'
        RETURNING campaign_id, COUNT(*) as recovered
        """,
        (STUCK_SENDING_TIMEOUT_MINUTES,)
    )
    # This won't return grouped results, so let's do it differently
    pass


def _recover_stuck_sending_v2(cursor):
    """
    Recover recipients stuck in 'sending' status for too long.
    Returns count of recovered recipients.
    """
    cursor.execute(
        """
        WITH recovered AS (
            UPDATE email_campaign_recipients
            SET status = 'failed_temp',
                last_error = 'Sending thread interrupted - will retry',
                updated_at = NOW()
            WHERE status = 'sending'
              AND last_attempt_at < NOW() - INTERVAL '%s minutes'
            RETURNING campaign_id
        )
        SELECT COUNT(*) as count FROM recovered
        """,
        (STUCK_SENDING_TIMEOUT_MINUTES,)
    )
    result = cursor.fetchone()
    return result['count'] if result else 0


def _process_campaign_batch(cursor, campaign_id, subject, html_content, batch_size):
    """
    Process a batch of recipients synchronously.
    Returns (sent_count, failed_count).
    """
    # Pick recipients to send
    cursor.execute(
        """
        WITH picked AS (
            SELECT id
            FROM email_campaign_recipients
            WHERE campaign_id = %s
              AND status IN ('pending', 'failed_temp')
              AND attempt_count < %s
            ORDER BY id
            LIMIT %s
            FOR UPDATE SKIP LOCKED
        )
        UPDATE email_campaign_recipients e
        SET status = 'sending',
            attempt_count = e.attempt_count + 1,
            last_attempt_at = NOW(),
            last_error = NULL,
            updated_at = NOW()
        FROM picked
        WHERE e.id = picked.id
        RETURNING
            e.id, e.user_id, e.creator_id, e.email, e.first_name, e.username, e.niche,
            e.followers_count, e.tier, e.pitches_this_week, e.pitches_total, e.brands_saved
        """,
        (campaign_id, MAX_SEND_ATTEMPTS, batch_size)
    )
    recipients = cursor.fetchall()

    if not recipients:
        return 0, 0

    sent_count = 0
    failed_count = 0

    for recipient in recipients:
        recipient_id = recipient['id']
        recipient_email = (recipient.get('email') or '').strip().lower()

        try:
            personalized_subject = personalize_text(subject, recipient)
            personalized_content = personalize_text(html_content, recipient)

            success = send_email_gmail(
                to_email=recipient_email,
                subject=personalized_subject,
                html_content=personalized_content
            )

            if success:
                cursor.execute(
                    """
                    UPDATE email_campaign_recipients
                    SET status = 'sent', last_error = NULL, updated_at = NOW()
                    WHERE id = %s
                    """,
                    (recipient_id,)
                )
                cursor.execute(
                    """
                    INSERT INTO email_logs (campaign_id, user_id, creator_id, email, status, sent_at)
                    VALUES (%s, %s, %s, %s, 'sent', NOW())
                    """,
                    (campaign_id, recipient['user_id'], recipient['creator_id'], recipient_email)
                )
                sent_count += 1
            else:
                cursor.execute(
                    """
                    UPDATE email_campaign_recipients
                    SET status = 'failed_temp', last_error = 'SMTP send failed', updated_at = NOW()
                    WHERE id = %s
                    """,
                    (recipient_id,)
                )
                failed_count += 1

            # Small delay to avoid rate limiting
            time.sleep(0.8)

        except Exception as e:
            cursor.execute(
                """
                UPDATE email_campaign_recipients
                SET status = 'failed_temp', last_error = %s, updated_at = NOW()
                WHERE id = %s
                """,
                (str(e)[:500], recipient_id)
            )
            failed_count += 1

    return sent_count, failed_count


@admin_email_bp.route('/cron/process', methods=['POST'])
def cron_process_campaigns():
    """
    Cron endpoint to process pending email campaigns.
    Call this every 1-2 minutes from an external scheduler (cron-job.org, Vercel cron, etc.)

    This endpoint:
    1. Recovers stuck 'sending' recipients
    2. Processes a batch of pending recipients from active campaigns
    3. Updates campaign status

    Auth: Uses a simple secret token to prevent abuse
    """
    # Simple auth for cron - check header or query param
    cron_secret = request.headers.get('X-Cron-Secret') or request.args.get('secret')
    if cron_secret != 'newcollab-cron-2026':
        return jsonify({'error': 'Unauthorized'}), 401

    try:
        conn = get_db_connection()
        cursor = conn.cursor(cursor_factory=RealDictCursor)

        # Step 1: Recover stuck recipients
        recovered = _recover_stuck_sending_v2(cursor)
        if recovered > 0:
            print(f"[Cron] Recovered {recovered} stuck recipients")
            conn.commit()

        # Step 2: Find campaigns that need processing
        cursor.execute(
            """
            SELECT DISTINCT ec.id, ec.subject_override, ec.html_content_override,
                   et.subject as template_subject, et.html_content as template_html_content
            FROM email_campaigns ec
            LEFT JOIN campaign_templates et ON ec.template_id = et.id
            WHERE ec.status = 'sending'
              AND EXISTS (
                  SELECT 1 FROM email_campaign_recipients ecr
                  WHERE ecr.campaign_id = ec.id
                    AND ecr.status IN ('pending', 'failed_temp')
                    AND ecr.attempt_count < %s
              )
            ORDER BY ec.id
            LIMIT 3
            """,
            (MAX_SEND_ATTEMPTS,)
        )
        campaigns = cursor.fetchall()

        results = []
        total_sent = 0
        total_failed = 0

        for campaign in campaigns:
            subject = campaign['subject_override'] or campaign.get('template_subject') or 'New update from Newcollab'
            html_content = campaign.get('html_content_override') or campaign.get('template_html_content')

            if not html_content:
                continue

            sent, failed = _process_campaign_batch(
                cursor, campaign['id'], subject, html_content, CRON_BATCH_SIZE
            )
            total_sent += sent
            total_failed += failed

            # Update campaign totals
            _refresh_campaign_totals_from_recipients(cursor, campaign['id'], final=False)
            conn.commit()

            results.append({
                'campaign_id': campaign['id'],
                'sent': sent,
                'failed': failed
            })

        # Step 3: Check for completed campaigns and update status
        cursor.execute(
            """
            SELECT id FROM email_campaigns WHERE status = 'sending'
            """
        )
        sending_campaigns = cursor.fetchall()

        for camp in sending_campaigns:
            counts = _recipient_counts(cursor, camp['id'])
            pending = (counts['retryable'] or 0)
            sending = (counts['sending'] or 0)

            if pending == 0 and sending == 0:
                # All done - mark as sent or failed
                _refresh_campaign_totals_from_recipients(cursor, camp['id'], final=True)
                conn.commit()

        conn.close()

        return jsonify({
            'message': 'Cron processing complete',
            'recovered_stuck': recovered,
            'campaigns_processed': len(results),
            'total_sent': total_sent,
            'total_failed': total_failed,
            'details': results
        })

    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({'error': str(e)}), 500


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
        if row:
            cursor.execute(
                """
                UPDATE email_campaign_recipients
                SET status = CASE WHEN status = 'sent' THEN 'sent' ELSE 'pending' END,
                    attempt_count = CASE WHEN status = 'sent' THEN attempt_count ELSE 0 END,
                    last_error = NULL,
                    updated_at = NOW()
                WHERE campaign_id = %s
                """,
                (campaign_id,)
            )
        conn.commit()
        conn.close()

        if not row:
            return jsonify({'error': 'Campaign not found'}), 404

        return jsonify({'message': 'Campaign reset to draft', 'campaign_id': campaign_id})

    except Exception as e:
        return jsonify({'error': str(e)}), 500


@admin_email_bp.route('/campaigns/<int:campaign_id>/continue', methods=['POST'])
@admin_required
def continue_campaign(campaign_id):
    """
    Manually continue a sending campaign by processing a batch.
    Can be called repeatedly from the UI to keep sending.
    """
    try:
        conn = get_db_connection()
        cursor = conn.cursor(cursor_factory=RealDictCursor)

        # First recover any stuck recipients for this campaign
        cursor.execute(
            """
            UPDATE email_campaign_recipients
            SET status = 'failed_temp',
                last_error = 'Recovered from stuck sending state',
                updated_at = NOW()
            WHERE campaign_id = %s
              AND status = 'sending'
              AND last_attempt_at < NOW() - INTERVAL '%s minutes'
            """,
            (campaign_id, STUCK_SENDING_TIMEOUT_MINUTES)
        )
        recovered = cursor.rowcount
        conn.commit()

        # Get campaign details
        cursor.execute(
            """
            SELECT ec.*, et.subject as template_subject, et.html_content as template_html_content
            FROM email_campaigns ec
            LEFT JOIN campaign_templates et ON ec.template_id = et.id
            WHERE ec.id = %s
            """,
            (campaign_id,)
        )
        campaign = cursor.fetchone()

        if not campaign:
            conn.close()
            return jsonify({'error': 'Campaign not found'}), 404

        # Ensure campaign is in sending status
        if campaign['status'] not in ('sending', 'failed'):
            cursor.execute(
                "UPDATE email_campaigns SET status = 'sending' WHERE id = %s",
                (campaign_id,)
            )
            conn.commit()

        subject = campaign['subject_override'] or campaign.get('template_subject') or 'New update from Newcollab'
        html_content = campaign.get('html_content_override') or campaign.get('template_html_content')

        if not html_content:
            conn.close()
            return jsonify({'error': 'Campaign has no email content'}), 400

        # Process a batch
        sent, failed = _process_campaign_batch(
            cursor, campaign_id, subject, html_content, CRON_BATCH_SIZE
        )

        # Update totals and check if complete
        counts = _recipient_counts(cursor, campaign_id)
        pending = (counts['retryable'] or 0)
        sending_count = (counts['sending'] or 0)

        if pending == 0 and sending_count == 0:
            summary = _refresh_campaign_totals_from_recipients(cursor, campaign_id, final=True)
            is_complete = True
        else:
            summary = _refresh_campaign_totals_from_recipients(cursor, campaign_id, final=False)
            is_complete = False

        conn.commit()
        conn.close()

        return jsonify({
            'message': f'Processed batch: {sent} sent, {failed} failed',
            'sent_this_batch': sent,
            'failed_this_batch': failed,
            'recovered_stuck': recovered,
            'total_sent': summary['sent'],
            'total_recipients': summary['total'],
            'remaining': pending,
            'is_complete': is_complete,
            'status': summary['status']
        })

    except Exception as e:
        import traceback
        traceback.print_exc()
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

        subject = campaign['subject_override'] or campaign.get('template_subject') or 'New update from Newcollab'
        html_content = campaign.get('html_content_override') or campaign.get('template_html_content')
        if not html_content:
            conn.close()
            return jsonify({'error': 'Campaign has no email content'}), 400

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

        if not all_recipients:
            conn.close()
            return jsonify({'error': 'No recipients found for this segment'}), 400

        _upsert_campaign_recipients(cursor, campaign_id, all_recipients)

        counts_before = _recipient_counts(cursor, campaign_id)
        already_sent = counts_before['sent'] or 0

        recipients = _pick_recipients_for_send(cursor, campaign_id)
        if recipients:
            cursor.execute(
                """
                UPDATE email_campaigns
                SET status = 'sending'
                WHERE id = %s
                """,
                (campaign_id,)
            )
            final_summary = None
        else:
            final_summary = _refresh_campaign_totals_from_recipients(cursor, campaign_id, final=True)
        conn.commit()

        conn.close()

        if not recipients:
            return jsonify({
                'message': 'No new recipients to send to',
                'sending': 0,
                'already_sent': already_sent,
                'status': final_summary['status'] if final_summary else 'draft'
            })

        # Start background thread to send claimed recipients
        thread = threading.Thread(
            target=_send_emails_background,
            args=(campaign_id, recipients, subject, html_content)
        )
        thread.daemon = True
        thread.start()

        return jsonify({
            'message': f'Sending started for {len(recipients)} recipients',
            'sending': len(recipients),
            'already_sent': already_sent,
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

        counts = _recipient_counts(cursor, campaign_id)
        conn.close()

        total = counts['total'] or campaign['total_recipients'] or 0
        sent = counts['sent'] or 0
        failed = counts['failed'] or 0
        retryable = counts['retryable'] or 0
        progress = round((sent + failed) / total * 100) if total else 0

        return jsonify({
            'status': campaign['status'],
            'total_recipients': total,
            'sent': sent,
            'failed': failed,
            'remaining': retryable,
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

        cursor.execute("SELECT COUNT(*) as count FROM email_campaign_recipients WHERE status = 'sent'")
        total_sent = cursor.fetchone()['count']

        cursor.execute("SELECT COUNT(*) as count FROM email_logs WHERE status = 'opened'")
        total_opened = cursor.fetchone()['count']

        cursor.execute("SELECT COUNT(*) as count FROM users WHERE unsubscribed_at IS NOT NULL")
        unsubscribes = cursor.fetchone()['count']

        open_rate = round((total_opened / total_sent * 100), 1) if total_sent > 0 else 0

        cursor.execute("""
            SELECT ec.id, ec.name, ec.status, ec.sent_at,
                   COALESCE(rc.total_recipients, ec.total_recipients, 0) as total_recipients,
                   COALESCE(rc.total_sent, ec.total_sent, 0) as total_sent,
                   et.type as template_type
            FROM email_campaigns ec
            LEFT JOIN campaign_templates et ON ec.template_id = et.id
            LEFT JOIN (
                SELECT
                    campaign_id,
                    COUNT(*) as total_recipients,
                    COUNT(*) FILTER (WHERE status = 'sent') as total_sent
                FROM email_campaign_recipients
                GROUP BY campaign_id
            ) rc ON rc.campaign_id = ec.id
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


# Conservative validator to avoid obvious malformed addresses in bulk sends.
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


@admin_email_bp.route('/send', methods=['POST'])
@admin_required
def send_admin_email():
    """
    Send a direct admin email to a custom recipient list.

    Body:
        to_emails: [required] list of recipient emails (max 500)
        subject: [required] subject line
        html_content: [required] HTML body
        from_name: [optional] kept for forward compatibility
    """
    try:
        data = request.get_json() or {}
        to_emails = data.get('to_emails') or data.get('emails') or []
        subject = (data.get('subject') or '').strip()
        html_content = data.get('html_content') or data.get('html') or ''

        if not isinstance(to_emails, list) or not to_emails:
            return jsonify({'error': 'to_emails list is required'}), 400
        if len(to_emails) > 500:
            return jsonify({'error': 'Maximum 500 recipients per request'}), 400
        if not subject:
            return jsonify({'error': 'subject is required'}), 400
        if not html_content:
            return jsonify({'error': 'html_content is required'}), 400

        # Normalize, deduplicate, and validate.
        normalized = []
        seen = set()
        invalid = []
        for email in to_emails:
            e = str(email or '').strip().lower()
            if not e:
                continue
            if e in seen:
                continue
            seen.add(e)
            if not _EMAIL_RE.match(e):
                invalid.append(e)
                continue
            normalized.append(e)

        if invalid:
            return jsonify({
                'error': 'Invalid email(s) in recipient list',
                'invalid': invalid[:25]
            }), 400

        if not normalized:
            return jsonify({'error': 'No valid recipients provided'}), 400

        sent = 0
        failed = 0
        failures = []

        for email in normalized:
            ok = send_email_gmail(email, subject, html_content)
            if ok:
                sent += 1
            else:
                failed += 1
                failures.append(email)
            # Keep behavior consistent with campaign throttling to avoid SMTP spikes.
            time.sleep(0.8)

        status_code = 200 if failed == 0 else 207
        return jsonify({
            'success': failed == 0,
            'message': f'Sent {sent} emails, {failed} failed',
            'sent': sent,
            'failed': failed,
            'total_requested': len(to_emails),
            'total_valid': len(normalized),
            'failed_emails': failures[:50]
        }), status_code

    except Exception as e:
        return jsonify({'error': str(e)}), 500


# ============================================================================
# BRAND OUTREACH ENDPOINTS (for brand-acquisition agent)
# ============================================================================

@admin_email_bp.route('/brands-for-outreach', methods=['GET'])
@admin_required
def get_brands_for_outreach():
    """
    Get brands that can be contacted for outreach

    Query Params:
        has_email: Filter to only brands with contact_email (default true)
        category: Filter by category
        not_contacted: Only brands we haven't emailed yet (default false)
        limit: Max results (default 100)
        offset: Pagination offset (default 0)
    """
    try:
        has_email = request.args.get('has_email', 'true').lower() == 'true'
        category = request.args.get('category')
        not_contacted = request.args.get('not_contacted', 'false').lower() == 'true'
        limit = min(int(request.args.get('limit', 100)), 500)
        offset = int(request.args.get('offset', 0))

        conn = get_db_connection()
        cursor = conn.cursor(cursor_factory=RealDictCursor)

        query = """
            SELECT
                b.id, b.brand_name, b.slug, b.website, b.logo_url,
                b.contact_email, b.description, b.category, b.niches,
                b.instagram_handle, b.tiktok_handle,
                b.application_form_url, b.has_application_form,
                b.created_at,
                COALESCE(bo.outreach_count, 0) as times_contacted,
                bo.last_contacted_at,
                bo.last_response_status
            FROM pr_brands b
            LEFT JOIN brand_outreach_tracking bo ON b.id = bo.brand_id
            WHERE COALESCE(b.status, 'published') = 'published'
        """
        params = []

        if has_email:
            query += " AND b.contact_email IS NOT NULL AND b.contact_email != ''"

        if category:
            query += " AND b.category = %s"
            params.append(category)

        if not_contacted:
            query += " AND bo.brand_id IS NULL"

        query += " ORDER BY b.created_at DESC LIMIT %s OFFSET %s"
        params.extend([limit, offset])

        cursor.execute(query, params)
        brands = cursor.fetchall()

        # Get total count
        count_query = """
            SELECT COUNT(*) as total FROM pr_brands b
            LEFT JOIN brand_outreach_tracking bo ON b.id = bo.brand_id
            WHERE COALESCE(b.status, 'published') = 'published'
        """
        if has_email:
            count_query += " AND b.contact_email IS NOT NULL AND b.contact_email != ''"

        cursor.execute(count_query)
        total = cursor.fetchone()['total']

        conn.close()

        return jsonify({
            'brands': brands,
            'total': total,
            'limit': limit,
            'offset': offset
        })

    except Exception as e:
        return jsonify({'error': str(e)}), 500


@admin_email_bp.route('/brand-outreach/send', methods=['POST'])
@admin_required
def send_brand_outreach():
    """
    Send a single outreach email to a brand

    Body:
        brand_id: ID of the brand to contact
        subject: Email subject line
        html_content: HTML email body
        template_id: (optional) Use a saved template instead
    """
    try:
        data = request.get_json()
        brand_id = data.get('brand_id')
        subject = data.get('subject')
        html_content = data.get('html_content')
        template_id = data.get('template_id')

        if not brand_id:
            return jsonify({'error': 'brand_id is required'}), 400

        conn = get_db_connection()
        cursor = conn.cursor(cursor_factory=RealDictCursor)

        # Get brand details
        cursor.execute("""
            SELECT id, brand_name, contact_email, website, category, description
            FROM pr_brands WHERE id = %s
        """, (brand_id,))
        brand = cursor.fetchone()

        if not brand:
            conn.close()
            return jsonify({'error': 'Brand not found'}), 404

        if not brand.get('contact_email'):
            conn.close()
            return jsonify({'error': 'Brand has no contact email'}), 400

        # Get template if specified
        if template_id and not html_content:
            cursor.execute("""
                SELECT subject, html_content FROM campaign_templates WHERE id = %s
            """, (template_id,))
            template = cursor.fetchone()
            if template:
                subject = subject or template['subject']
                html_content = template['html_content']

        if not subject or not html_content:
            conn.close()
            return jsonify({'error': 'subject and html_content are required'}), 400

        # Personalize content with brand data
        personalized_content = html_content.replace('{{brand_name}}', brand['brand_name'] or '')
        personalized_content = personalized_content.replace('{{website}}', brand['website'] or '')
        personalized_content = personalized_content.replace('{{category}}', brand['category'] or '')

        personalized_subject = subject.replace('{{brand_name}}', brand['brand_name'] or '')

        # Send the email
        success = send_email_gmail(brand['contact_email'], personalized_subject, personalized_content)

        if success:
            # Track the outreach
            cursor.execute("""
                INSERT INTO brand_outreach_tracking (brand_id, outreach_count, last_contacted_at, last_subject)
                VALUES (%s, 1, NOW(), %s)
                ON CONFLICT (brand_id) DO UPDATE SET
                    outreach_count = brand_outreach_tracking.outreach_count + 1,
                    last_contacted_at = NOW(),
                    last_subject = EXCLUDED.last_subject
            """, (brand_id, personalized_subject))

            # Log the email
            cursor.execute("""
                INSERT INTO brand_outreach_log (brand_id, email_sent_to, subject, status, sent_at)
                VALUES (%s, %s, %s, 'sent', NOW())
            """, (brand_id, brand['contact_email'], personalized_subject))

            conn.commit()
            conn.close()

            return jsonify({
                'success': True,
                'message': f"Email sent to {brand['brand_name']} at {brand['contact_email']}",
                'brand_id': brand_id,
                'email': brand['contact_email']
            })
        else:
            conn.close()
            return jsonify({'error': 'Failed to send email'}), 500

    except Exception as e:
        return jsonify({'error': str(e)}), 500


@admin_email_bp.route('/brand-outreach/bulk', methods=['POST'])
@admin_required
def send_bulk_brand_outreach():
    """
    Send outreach emails to multiple brands

    Body:
        brand_ids: List of brand IDs to contact
        subject: Email subject line (supports {{brand_name}})
        html_content: HTML email body (supports {{brand_name}}, {{website}}, {{category}})
        delay_seconds: Delay between emails (default 1)
    """
    try:
        data = request.get_json()
        brand_ids = data.get('brand_ids', [])
        subject = data.get('subject')
        html_content = data.get('html_content')
        delay_seconds = float(data.get('delay_seconds', 1))

        if not brand_ids:
            return jsonify({'error': 'brand_ids list is required'}), 400
        if not subject or not html_content:
            return jsonify({'error': 'subject and html_content are required'}), 400

        conn = get_db_connection()
        cursor = conn.cursor(cursor_factory=RealDictCursor)

        # Get all brands
        cursor.execute("""
            SELECT id, brand_name, contact_email, website, category
            FROM pr_brands
            WHERE id = ANY(%s) AND contact_email IS NOT NULL AND contact_email != ''
        """, (brand_ids,))
        brands = cursor.fetchall()

        results = {
            'sent': [],
            'failed': [],
            'skipped': []
        }

        for brand in brands:
            # Personalize
            personalized_content = html_content.replace('{{brand_name}}', brand['brand_name'] or '')
            personalized_content = personalized_content.replace('{{website}}', brand['website'] or '')
            personalized_content = personalized_content.replace('{{category}}', brand['category'] or '')
            personalized_subject = subject.replace('{{brand_name}}', brand['brand_name'] or '')

            success = send_email_gmail(brand['contact_email'], personalized_subject, personalized_content)

            if success:
                cursor.execute("""
                    INSERT INTO brand_outreach_tracking (brand_id, outreach_count, last_contacted_at, last_subject)
                    VALUES (%s, 1, NOW(), %s)
                    ON CONFLICT (brand_id) DO UPDATE SET
                        outreach_count = brand_outreach_tracking.outreach_count + 1,
                        last_contacted_at = NOW(),
                        last_subject = EXCLUDED.last_subject
                """, (brand['id'], personalized_subject))

                cursor.execute("""
                    INSERT INTO brand_outreach_log (brand_id, email_sent_to, subject, status, sent_at)
                    VALUES (%s, %s, %s, 'sent', NOW())
                """, (brand['id'], brand['contact_email'], personalized_subject))

                results['sent'].append({
                    'brand_id': brand['id'],
                    'brand_name': brand['brand_name'],
                    'email': brand['contact_email']
                })
            else:
                results['failed'].append({
                    'brand_id': brand['id'],
                    'brand_name': brand['brand_name'],
                    'email': brand['contact_email']
                })

            time.sleep(delay_seconds)

        # Mark skipped (no email)
        sent_ids = [b['id'] for b in brands]
        for bid in brand_ids:
            if bid not in sent_ids:
                results['skipped'].append({'brand_id': bid, 'reason': 'No contact email'})

        conn.commit()
        conn.close()

        return jsonify({
            'success': True,
            'total_requested': len(brand_ids),
            'total_sent': len(results['sent']),
            'total_failed': len(results['failed']),
            'total_skipped': len(results['skipped']),
            'results': results
        })

    except Exception as e:
        return jsonify({'error': str(e)}), 500


@admin_email_bp.route('/brand-outreach/stats', methods=['GET'])
@admin_required
def get_brand_outreach_stats():
    """Get statistics on brand outreach efforts"""
    try:
        conn = get_db_connection()
        cursor = conn.cursor(cursor_factory=RealDictCursor)

        # Overall stats
        cursor.execute("""
            SELECT
                COUNT(DISTINCT brand_id) as brands_contacted,
                SUM(outreach_count) as total_emails_sent,
                COUNT(*) FILTER (WHERE last_response_status = 'replied') as total_replies,
                COUNT(*) FILTER (WHERE last_response_status = 'signed_up') as total_signups
            FROM brand_outreach_tracking
        """)
        stats = cursor.fetchone()

        # Recent outreach
        cursor.execute("""
            SELECT
                b.brand_name, b.category, bo.last_contacted_at, bo.outreach_count, bo.last_response_status
            FROM brand_outreach_tracking bo
            JOIN pr_brands b ON bo.brand_id = b.id
            ORDER BY bo.last_contacted_at DESC
            LIMIT 20
        """)
        recent = cursor.fetchall()

        # Brands with contact email but not contacted
        cursor.execute("""
            SELECT COUNT(*) as count FROM pr_brands b
            LEFT JOIN brand_outreach_tracking bo ON b.id = bo.brand_id
            WHERE b.contact_email IS NOT NULL AND b.contact_email != ''
            AND bo.brand_id IS NULL
            AND COALESCE(b.status, 'published') = 'published'
        """)
        not_contacted = cursor.fetchone()['count']

        conn.close()

        return jsonify({
            'brands_contacted': stats['brands_contacted'] or 0,
            'total_emails_sent': stats['total_emails_sent'] or 0,
            'total_replies': stats['total_replies'] or 0,
            'total_signups': stats['total_signups'] or 0,
            'brands_not_contacted': not_contacted,
            'recent_outreach': recent
        })

    except Exception as e:
        return jsonify({'error': str(e)}), 500


@admin_email_bp.route('/brand-outreach/templates', methods=['GET'])
@admin_required
def get_brand_outreach_templates():
    """Get email templates for brand outreach"""
    try:
        conn = get_db_connection()
        cursor = conn.cursor(cursor_factory=RealDictCursor)

        cursor.execute("""
            SELECT id, name, type, subject, html_content, variables, created_at
            FROM campaign_templates
            WHERE type = 'brand_outreach' AND is_active = true
            ORDER BY name
        """)
        templates = cursor.fetchall()
        conn.close()

        return jsonify({'templates': templates})

    except Exception as e:
        return jsonify({'error': str(e)}), 500


@admin_email_bp.route('/brand-outreach/log-response', methods=['POST'])
@admin_required
def log_brand_response():
    """
    Log a response from a brand

    Body:
        brand_id: ID of the brand
        response_status: 'replied', 'interested', 'not_interested', 'signed_up'
        notes: Optional notes about the response
    """
    try:
        data = request.get_json()
        brand_id = data.get('brand_id')
        response_status = data.get('response_status')
        notes = data.get('notes', '')

        if not brand_id or not response_status:
            return jsonify({'error': 'brand_id and response_status are required'}), 400

        valid_statuses = ['replied', 'interested', 'not_interested', 'signed_up']
        if response_status not in valid_statuses:
            return jsonify({'error': f'response_status must be one of: {valid_statuses}'}), 400

        conn = get_db_connection()
        cursor = conn.cursor(cursor_factory=RealDictCursor)

        cursor.execute("""
            UPDATE brand_outreach_tracking
            SET last_response_status = %s, response_notes = %s, last_response_at = NOW()
            WHERE brand_id = %s
        """, (response_status, notes, brand_id))

        if cursor.rowcount == 0:
            conn.close()
            return jsonify({'error': 'No outreach record found for this brand'}), 404

        conn.commit()
        conn.close()

        return jsonify({
            'success': True,
            'message': f'Response logged: {response_status}'
        })

    except Exception as e:
        return jsonify({'error': str(e)}), 500
