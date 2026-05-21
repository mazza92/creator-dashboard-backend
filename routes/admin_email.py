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
import imaplib
import email
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.utils import make_msgid

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import psycopg2
from services.outreach_image_gen import (
    generate_ugc_image,
    get_showcase_creators,
    init as init_outreach_image_gen,
)
# Initialise schema + seed on first import (idempotent)
try:
    init_outreach_image_gen()
except Exception as _e:
    import logging as _logging
    _logging.getLogger(__name__).warning(f"outreach_image_gen init: {_e}")

# Gmail SMTP Configuration (uses existing env vars)
GMAIL_USER = os.getenv('SMTP_USERNAME', 'team@newcollab.co')
GMAIL_APP_PASSWORD = os.getenv('SMTP_PASSWORD')


def get_db_connection():
    """Get database connection"""
    return psycopg2.connect(os.getenv('DATABASE_URL'), cursor_factory=RealDictCursor)


MAX_SEND_ATTEMPTS = 3
STUCK_SENDING_TIMEOUT_MINUTES = 5  # Recipients stuck in 'sending' for this long are recovered
CRON_BATCH_SIZE = 25  # How many emails to send per cron invocation (~20s at 0.8s/email)
ALLOWED_BRAND_TEMPLATE_IDS = {8}
DISALLOWED_BRAND_TEMPLATE_IDS = {6, 7}
BLOCKED_OUTREACH_STATUSES = {
    'replied', 'interested', 'not_interested', 'signed_up',
    'wrong_email', 'bounced', 'do_not_contact', 'unsubscribe',
    'reply',  # inbox-sync simplified status (manual follow-up)
}
DEFAULT_FOLLOWUP_COOLDOWN_HOURS = 96  # 4 days between follow-ups unless overridden


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


def _enforce_template_allowlist(template_id: int):
    """Return (ok, error_response, status_code)."""
    if template_id in DISALLOWED_BRAND_TEMPLATE_IDS:
        return False, {'error': f'template_id {template_id} is disabled'}, 400
    if template_id not in ALLOWED_BRAND_TEMPLATE_IDS:
        return False, {'error': f'template_id {template_id} is not allowed for brand outreach'}, 400
    return True, None, 200


def _is_blocked_outreach_status(status: str) -> bool:
    return (status or '').strip().lower() in BLOCKED_OUTREACH_STATUSES


def _has_recent_contact(last_contacted_at, min_hours: int = DEFAULT_FOLLOWUP_COOLDOWN_HOURS) -> bool:
    if not last_contacted_at:
        return False
    try:
        if isinstance(last_contacted_at, datetime):
            delta = datetime.utcnow() - last_contacted_at.replace(tzinfo=None)
        else:
            return False
        return delta < timedelta(hours=max(1, int(min_hours)))
    except Exception:
        return False


def _ensure_brand_outreach_log_message_id_column(cursor) -> None:
    """
    Ensure brand_outreach_log has message_id for accurate bounce reconciliation.
    Safe to call repeatedly.
    """
    cursor.execute("""
        ALTER TABLE brand_outreach_log
        ADD COLUMN IF NOT EXISTS message_id TEXT
    """)
    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_brand_outreach_log_message_id
        ON brand_outreach_log(message_id)
    """)


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
        _ensure_brand_outreach_log_message_id_column(cursor)

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


@admin_email_bp.route('/brand-outreach/templates/cleanup', methods=['POST'])
@admin_required
def cleanup_brand_outreach_templates():
    """
    Permanently remove known-bad templates (IDs 6 and 7).
    Keeps only approved outreach template flow (template 8 allowlisted).
    """
    try:
        conn = get_db_connection()
        cursor = conn.cursor(cursor_factory=RealDictCursor)
        cursor.execute(
            "DELETE FROM campaign_templates WHERE id = ANY(%s) RETURNING id",
            (list(DISALLOWED_BRAND_TEMPLATE_IDS),)
        )
        deleted_rows = cursor.fetchall()
        conn.commit()
        conn.close()
        deleted_ids = [r['id'] for r in deleted_rows]
        return jsonify({
            'success': True,
            'deleted_template_ids': deleted_ids,
            'allowed_template_ids': sorted(list(ALLOWED_BRAND_TEMPLATE_IDS)),
        })
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

def _build_unsubscribe_url(user_id):
    """Generate a signed one-click unsubscribe URL for a user."""
    try:
        from public_routes import make_unsubscribe_token
        backend_url = os.getenv('BACKEND_URL', 'https://api.newcollab.co')
        token = make_unsubscribe_token(str(user_id))
        return f"{backend_url}/api/public/unsubscribe?uid={user_id}&token={token}"
    except Exception:
        return ''


def personalize_text(text, recipient):
    """Replace template variables with recipient data."""
    if not text:
        return text

    pitches_remaining = 3 - recipient.get('pitches_this_week', 0)
    if recipient.get('tier', 'free') != 'free':
        pitches_remaining = 'unlimited'

    user_id = recipient.get('user_id') or recipient.get('id') or ''
    unsubscribe_url = _build_unsubscribe_url(user_id) if user_id else ''

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
        '{{unsubscribe_url}}': unsubscribe_url,
    }

    for var, value in replacements.items():
        text = text.replace(var, str(value))

    # Rewrite any hardcoded unsubscribe links that point to /login or /settings
    if unsubscribe_url:
        import re
        text = re.sub(
            r'href="https?://[^"]*(?:app\.newcollab\.co/(?:login|creator/dashboard/settings)|newcollab\.co/unsubscribe)[^"]*"([^>]*>(?:[^<]*</a>)?)',
            lambda m: f'href="{unsubscribe_url}"{m.group(1)}',
            text
        )

    return text


def send_email_gmail_with_meta(to_email, subject, html_content):
    """Send email via Gmail SMTP and return transport metadata."""
    if not GMAIL_APP_PASSWORD:
        print(f"Warning: SMTP_PASSWORD not set. GMAIL_USER={GMAIL_USER}")
        return {"success": False, "error": "SMTP_PASSWORD not set", "message_id": None}

    try:
        msg = MIMEMultipart('alternative')
        msg['Subject'] = subject
        msg['From'] = f"Newcollab <{GMAIL_USER}>"
        msg['To'] = to_email
        msg['Message-ID'] = make_msgid(domain=GMAIL_USER.split("@")[-1] if "@" in GMAIL_USER else None)

        html_part = MIMEText(html_content, 'html')
        msg.attach(html_part)

        # Connect to Gmail SMTP with TLS (port 587)
        with smtplib.SMTP('smtp.gmail.com', 587) as server:
            server.starttls()
            server.login(GMAIL_USER, GMAIL_APP_PASSWORD)
            server.sendmail(GMAIL_USER, to_email, msg.as_string())

        print(f"Email sent successfully to {to_email}")
        return {"success": True, "message_id": msg.get("Message-ID")}

    except Exception as e:
        print(f"Email send error: {e}")
        import traceback
        traceback.print_exc()
        return {"success": False, "error": str(e), "message_id": None}


def send_email_gmail(to_email, subject, html_content):
    """Backward-compatible bool wrapper."""
    return bool(send_email_gmail_with_meta(to_email, subject, html_content).get("success"))


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
            query += " AND (bo.brand_id IS NULL OR COALESCE(bo.last_response_status, '') = '')"
            query += " AND COALESCE(bo.last_response_status, '') NOT IN ('replied','interested','not_interested','signed_up','wrong_email','bounced','do_not_contact','unsubscribe','reply')"

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
        if not_contacted:
            count_query += " AND (bo.brand_id IS NULL OR COALESCE(bo.last_response_status, '') = '')"
            count_query += " AND COALESCE(bo.last_response_status, '') NOT IN ('replied','interested','not_interested','signed_up','wrong_email','bounced','do_not_contact','unsubscribe','reply')"

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
        allow_followup = bool(data.get('allow_followup', False))
        min_followup_hours = int(data.get('min_followup_hours', DEFAULT_FOLLOWUP_COOLDOWN_HOURS))

        if not brand_id:
            return jsonify({'error': 'brand_id is required'}), 400
        if template_id is not None:
            ok, err, code = _enforce_template_allowlist(int(template_id))
            if not ok:
                return jsonify(err), code

        conn = get_db_connection()
        cursor = conn.cursor(cursor_factory=RealDictCursor)

        # Get brand details
        cursor.execute("""
            SELECT
                b.id, b.brand_name, b.contact_email, b.website, b.category, b.description,
                bo.last_response_status, bo.last_contacted_at
            FROM pr_brands b
            LEFT JOIN brand_outreach_tracking bo ON bo.brand_id = b.id
            WHERE b.id = %s
        """, (brand_id,))
        brand = cursor.fetchone()

        if not brand:
            conn.close()
            return jsonify({'error': 'Brand not found'}), 404

        if not brand.get('contact_email'):
            conn.close()
            return jsonify({'error': 'Brand has no contact email'}), 400
        if _is_blocked_outreach_status(brand.get('last_response_status')):
            conn.close()
            return jsonify({
                'error': f"Outreach blocked for status '{brand.get('last_response_status')}'",
                'brand_id': brand_id
            }), 409
        if brand.get('last_contacted_at') and not allow_followup:
            conn.close()
            return jsonify({
                'error': 'Duplicate outreach blocked: brand already contacted',
                'brand_id': brand_id,
                'last_contacted_at': str(brand.get('last_contacted_at')),
                'hint': 'Pass allow_followup=true only for intentional day-4/day-9 sequences'
            }), 409
        if allow_followup and _has_recent_contact(brand.get('last_contacted_at'), min_hours=min_followup_hours):
            conn.close()
            return jsonify({
                'error': f"Follow-up blocked by cooldown ({min_followup_hours}h)",
                'brand_id': brand_id,
                'last_contacted_at': str(brand.get('last_contacted_at'))
            }), 409

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

        # Ensure message_id logging support exists, then send.
        _ensure_brand_outreach_log_message_id_column(cursor)
        send_res = send_email_gmail_with_meta(brand['contact_email'], personalized_subject, personalized_content)
        success = bool(send_res.get("success"))
        sent_message_id = send_res.get("message_id")

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
                INSERT INTO brand_outreach_log (brand_id, email_sent_to, subject, status, sent_at, message_id)
                VALUES (%s, %s, %s, 'sent', NOW(), %s)
            """, (brand_id, brand['contact_email'], personalized_subject, sent_message_id))

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
        allow_followup = bool(data.get('allow_followup', False))
        min_followup_hours = int(data.get('min_followup_hours', DEFAULT_FOLLOWUP_COOLDOWN_HOURS))

        if not brand_ids:
            return jsonify({'error': 'brand_ids list is required'}), 400
        if not subject or not html_content:
            return jsonify({'error': 'subject and html_content are required'}), 400

        conn = get_db_connection()
        cursor = conn.cursor(cursor_factory=RealDictCursor)

        # Get all brands
        cursor.execute("""
            SELECT
                b.id, b.brand_name, b.contact_email, b.website, b.category,
                bo.last_response_status
            FROM pr_brands b
            LEFT JOIN brand_outreach_tracking bo ON bo.brand_id = b.id
            WHERE b.id = ANY(%s)
              AND b.contact_email IS NOT NULL
              AND b.contact_email != ''
        """, (brand_ids,))
        brands = cursor.fetchall()

        results = {
            'sent': [],
            'failed': [],
            'skipped': []
        }

        for brand in brands:
            if _is_blocked_outreach_status(brand.get('last_response_status')):
                results['skipped'].append({
                    'brand_id': brand['id'],
                    'reason': f"blocked_status:{brand.get('last_response_status')}"
                })
                continue
            # do not re-send by default once contacted
            cursor.execute(
                "SELECT last_contacted_at FROM brand_outreach_tracking WHERE brand_id=%s",
                (brand['id'],)
            )
            trk = cursor.fetchone() or {}
            last_contacted_at = trk.get('last_contacted_at')
            if last_contacted_at and not allow_followup:
                results['skipped'].append({
                    'brand_id': brand['id'],
                    'reason': 'already_contacted'
                })
                continue
            if allow_followup and _has_recent_contact(last_contacted_at, min_hours=min_followup_hours):
                results['skipped'].append({
                    'brand_id': brand['id'],
                    'reason': f'followup_cooldown_{min_followup_hours}h'
                })
                continue
            # Personalize
            personalized_content = html_content.replace('{{brand_name}}', brand['brand_name'] or '')
            personalized_content = personalized_content.replace('{{website}}', brand['website'] or '')
            personalized_content = personalized_content.replace('{{category}}', brand['category'] or '')
            personalized_subject = subject.replace('{{brand_name}}', brand['brand_name'] or '')

            send_res = send_email_gmail_with_meta(brand['contact_email'], personalized_subject, personalized_content)
            success = bool(send_res.get("success"))
            sent_message_id = send_res.get("message_id")

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
                    INSERT INTO brand_outreach_log (brand_id, email_sent_to, subject, status, sent_at, message_id)
                    VALUES (%s, %s, %s, 'sent', NOW(), %s)
                """, (brand['id'], brand['contact_email'], personalized_subject, sent_message_id))

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
        fetched_ids = [b['id'] for b in brands]
        for bid in brand_ids:
            if bid not in fetched_ids:
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


def _build_ugc_email_html(brand_name: str, vertical: str, creators: list,
                          hero_image_url: str | None, niches: list = None,
                          description: str = "") -> str:
    """
    Build the full UGC outreach email HTML.
    Reference design: creator-holding-product hero image + 3 creator cards + CTA.
    """
    vertical_label = (vertical or "lifestyle").capitalize()

    # Caption derived from vertical tone
    captions = {
        "beauty": "Using only this for my morning routine 🌸 Full review in stories.",
        "skincare": "My skin has never looked better 💧 Full routine breakdown coming.",
        "haircare": "Finally found my HG hair product ✨ Tutorial dropping this week.",
        "makeup": "GRWM using this only — the results spoke for themselves 💄",
        "fitness": "Pre-workout game changer 🔥 Full review + discount in bio.",
        "activewear": "Wearing this to every single workout now 💪 Link in bio.",
        "fashion": "This is my new everyday favourite. Styling inspo coming soon.",
        "food": "Changed my entire morning ritual ☀️ Recipe and review on stories.",
        "wellness": "Obsessed with my new wellness routine 🌿 Everything linked in bio.",
        "supplements": "30-day results update 💪 Honest stack review in bio.",
        "pet": "Biscuit gives this 5/5 paws 🐾 Full honest review linked in bio.",
        "home": "Before vs after was insane ✨ Walkthrough video coming this week.",
        "tech": "Changed how I work from home forever. Review on my channel.",
        "gaming": "Upgraded my setup and cannot go back 🎮 Full review dropping soon.",
        "travel": "This carried me through 14 countries ✈️ Packing guide in stories.",
        "luxury": "Worth every single penny 💎 Unboxing + review now live.",
        "sustainable": "The sustainable swap I've been looking for 🌱 Review in bio.",
        "baby": "Mum-approved and toddler-tested 👶 Honest review in stories.",
        "jewelry": "Wearing this every single day now 🌟 Full haul coming soon.",
    }
    caption = captions.get((vertical or "").lower(), "This is my new favourite find. Review coming soon ✨")

    hero_section = ""
    if hero_image_url:
        hero_section = f"""
                    <!-- Hero UGC post mockup -->
                    <tr>
                        <td style="padding: 0 40px 28px;">
                            <table width="100%" cellpadding="0" cellspacing="0"
                                   style="border:1px solid #ebebeb; border-radius:12px; overflow:hidden;">
                                <!-- post header -->
                                <tr>
                                    <td style="padding:12px 14px; background:#fff;">
                                        <table cellpadding="0" cellspacing="0">
                                            <tr>
                                                <td>
                                                    <div style="width:32px;height:32px;border-radius:50%;
                                                                background:linear-gradient(135deg,#f09433,#e6683c,#dc2743,#cc2366,#bc1888);
                                                                display:inline-block;"></div>
                                                </td>
                                                <td style="padding-left:8px;">
                                                    <span style="font-family:Arial,sans-serif;font-size:13px;
                                                                 font-weight:700;color:#111;">
                                                        {creators[0]['handle'] if creators else '@newcollab_creator'}
                                                    </span><br>
                                                    <span style="font-family:Arial,sans-serif;font-size:11px;color:#888;">
                                                        {creators[0].get('content_style','Content Creator') if creators else 'Content Creator'}
                                                    </span>
                                                </td>
                                                <td style="padding-left:16px;">
                                                    <span style="font-family:Arial,sans-serif;font-size:11px;
                                                                 color:#fff;background:#0095f6;
                                                                 padding:3px 8px;border-radius:4px;">Sponsored</span>
                                                </td>
                                            </tr>
                                        </table>
                                    </td>
                                </tr>
                                <!-- hero image -->
                                <tr>
                                    <td style="padding:0;">
                                        <img src="{hero_image_url}"
                                             alt="UGC preview for {brand_name}"
                                             width="100%"
                                             style="display:block;width:100%;max-height:380px;object-fit:cover;"/>
                                    </td>
                                </tr>
                                <!-- caption -->
                                <tr>
                                    <td style="padding:12px 14px 14px; background:#fff;">
                                        <p style="font-family:Arial,sans-serif;font-size:13px;
                                                  color:#111;margin:0 0 4px;">
                                            <strong>{creators[0]['handle'] if creators else '@creator'}</strong>&nbsp;
                                            {caption}
                                        </p>
                                        <p style="font-family:Arial,sans-serif;font-size:11px;
                                                  color:#aaa;margin:0;">2 HOURS AGO</p>
                                    </td>
                                </tr>
                            </table>
                        </td>
                    </tr>"""

    # Build 3 creator style cards
    creator_cards_html = ""
    if creators:
        cards = []
        for c in creators[:3]:
            cards.append(f"""
                                <td style="width:33%;padding:0 6px;vertical-align:top;">
                                    <table width="100%" cellpadding="0" cellspacing="0"
                                           style="border:1px solid #ebebeb;border-radius:10px;overflow:hidden;">
                                        <tr>
                                            <td style="padding:10px 10px 6px;background:#f9f9f9;text-align:center;">
                                                <span style="font-family:Arial,sans-serif;font-size:11px;
                                                             color:#555;font-weight:600;">{c['handle']}</span>
                                            </td>
                                        </tr>
                                        <tr>
                                            <td style="padding:0;">
                                                <img src="{hero_image_url or 'https://via.placeholder.com/200x200/f5f5f5/cccccc?text=UGC'}"
                                                     width="100%"
                                                     style="display:block;width:100%;height:140px;object-fit:cover;"/>
                                            </td>
                                        </tr>
                                        <tr>
                                            <td style="padding:8px 10px;background:#fff;text-align:center;">
                                                <span style="font-family:Arial,sans-serif;font-size:10px;
                                                             color:#888;text-transform:uppercase;
                                                             letter-spacing:0.5px;">{c.get('content_style','UGC Content')}</span><br>
                                                <span style="font-family:Arial,sans-serif;font-size:11px;
                                                             color:#333;font-weight:600;">{c.get('follower_range','10K-50K')} followers</span>
                                            </td>
                                        </tr>
                                    </table>
                                </td>""")
        creator_cards_html = f"""
                    <tr>
                        <td style="padding: 8px 40px 28px;">
                            <p style="font-family:Arial,sans-serif;font-size:13px;
                                      color:#888;text-transform:uppercase;letter-spacing:1px;
                                      margin:0 0 12px;font-weight:600;">3 CONTENT STYLES READY FOR YOU</p>
                            <table width="100%" cellpadding="0" cellspacing="0">
                                <tr>{''.join(cards)}</tr>
                            </table>
                        </td>
                    </tr>"""

    niche_list = ""
    if niches:
        niche_list = f" ({', '.join(niches[:3])})"

    return f"""<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Free UGC Content for {brand_name}</title>
</head>
<body style="margin:0;padding:0;font-family:Arial,sans-serif;background-color:#f5f5f5;">
<table width="100%" cellpadding="0" cellspacing="0"
       style="background-color:#f5f5f5;padding:32px 20px;">
    <tr>
        <td align="center">
            <table width="600" cellpadding="0" cellspacing="0"
                   style="background:#fff;border-radius:14px;overflow:hidden;
                          box-shadow:0 4px 20px rgba(0,0,0,0.08);max-width:600px;">

                <!-- Top label -->
                <tr>
                    <td style="padding:20px 40px 0;">
                        <p style="font-family:Arial,sans-serif;font-size:11px;font-weight:700;
                                  color:#0095f6;letter-spacing:1px;text-transform:uppercase;
                                  margin:0;">FREE UGC CONTENT FOR {brand_name.upper()}</p>
                    </td>
                </tr>

                <!-- Headline -->
                <tr>
                    <td style="padding:10px 40px 20px;">
                        <h1 style="font-family:Arial,sans-serif;font-size:24px;font-weight:800;
                                   color:#111;margin:0;line-height:1.3;">
                            3 creators on Newcollab are ready to make content with your products.
                        </h1>
                    </td>
                </tr>

                {hero_section}

                <!-- AD-READY block -->
                <tr>
                    <td style="padding:0 40px 28px;">
                        <table width="100%" cellpadding="0" cellspacing="0"
                               style="background:linear-gradient(135deg,#667eea,#764ba2);
                                      border-radius:10px;padding:20px 24px;">
                            <tr>
                                <td>
                                    <p style="font-family:Arial,sans-serif;font-size:11px;font-weight:700;
                                              color:rgba(255,255,255,0.85);letter-spacing:1px;
                                              text-transform:uppercase;margin:0 0 6px;">AD-READY CONTENT</p>
                                    <p style="font-family:Arial,sans-serif;font-size:14px;color:#fff;
                                              margin:0 0 8px;line-height:1.5;">
                                        Every post is made to convert. Real creators, real audiences, real results.
                                        Drop these straight into your Meta or TikTok ad manager.
                                    </p>
                                    <p style="font-family:Arial,sans-serif;font-size:13px;
                                              color:rgba(255,255,255,0.85);margin:0;">
                                        UGC ads convert 4x better than brand-created content.
                                        Average CPM is 50% lower.
                                    </p>
                                </td>
                            </tr>
                        </table>
                    </td>
                </tr>

                {creator_cards_html}

                <!-- Cost comparison -->
                <tr>
                    <td style="padding:0 40px 28px;">
                        <table width="100%" cellpadding="0" cellspacing="0"
                               style="border:1px solid #ebebeb;border-radius:10px;overflow:hidden;">
                            <tr>
                                <td style="padding:16px 20px;border-bottom:1px solid #f0f0f0;">
                                    <p style="font-family:Arial,sans-serif;font-size:14px;font-weight:700;
                                              color:#111;margin:0;">What this costs elsewhere:</p>
                                </td>
                            </tr>
                            <tr>
                                <td style="padding:12px 20px;border-bottom:1px solid #f0f0f0;">
                                    <table width="100%" cellpadding="0" cellspacing="0">
                                        <tr>
                                            <td style="font-family:Arial,sans-serif;font-size:13px;color:#555;">
                                                UGC agency (3 posts)
                                            </td>
                                            <td style="text-align:right;font-family:Arial,sans-serif;
                                                       font-size:13px;color:#aaa;">
                                                <s>$1,500 – $3,000</s>
                                            </td>
                                        </tr>
                                    </table>
                                </td>
                            </tr>
                            <tr>
                                <td style="padding:12px 20px;border-bottom:1px solid #f0f0f0;">
                                    <table width="100%" cellpadding="0" cellspacing="0">
                                        <tr>
                                            <td style="font-family:Arial,sans-serif;font-size:13px;color:#555;">
                                                Freelance creators (3 posts)
                                            </td>
                                            <td style="text-align:right;font-family:Arial,sans-serif;
                                                       font-size:13px;color:#aaa;">
                                                <s>$600 – $1,200</s>
                                            </td>
                                        </tr>
                                    </table>
                                </td>
                            </tr>
                            <tr>
                                <td style="padding:12px 20px;background:#f9fff9;">
                                    <table width="100%" cellpadding="0" cellspacing="0">
                                        <tr>
                                            <td style="font-family:Arial,sans-serif;font-size:13px;
                                                       font-weight:700;color:#111;">
                                                Newcollab creators
                                            </td>
                                            <td style="text-align:right;font-family:Arial,sans-serif;
                                                       font-size:13px;font-weight:700;color:#22c55e;">
                                                Free (gifting only)
                                            </td>
                                        </tr>
                                    </table>
                                </td>
                            </tr>
                        </table>
                    </td>
                </tr>

                <!-- CTA -->
                <tr>
                    <td style="padding:0 40px 36px;text-align:center;">
                        <p style="font-family:Arial,sans-serif;font-size:15px;color:#333;margin:0 0 20px;">
                            Creating a free Brand Account takes 2 minutes.
                            Your next UGC post could be live within days.
                        </p>
                        <a href="https://app.newcollab.co/register"
                           style="display:inline-block;
                                  background:linear-gradient(135deg,#667eea 0%,#764ba2 100%);
                                  color:#fff;text-decoration:none;padding:16px 44px;
                                  border-radius:10px;font-weight:700;font-size:15px;
                                  font-family:Arial,sans-serif;">
                            Claim Your Free Creators →
                        </a>
                    </td>
                </tr>

                <!-- Footer -->
                <tr>
                    <td style="background:#f8f9fa;padding:18px 40px;
                               border-top:1px solid #eee;text-align:center;">
                        <p style="font-family:Arial,sans-serif;font-size:12px;color:#999;margin:0 0 4px;">
                            Newcollab · Connecting Brands with Creators ·
                            <a href="https://newcollab.co" style="color:#667eea;">newcollab.co</a>
                        </p>
                        <p style="font-family:Arial,sans-serif;font-size:10px;color:#bbb;margin:0;">
                            Illustrative preview — not an actual published post.
                        </p>
                    </td>
                </tr>

            </table>
        </td>
    </tr>
</table>
</body>
</html>"""


@admin_email_bp.route('/brand-outreach/render', methods=['POST'])
@admin_required
def render_brand_outreach():
    """
    Render a fully personalised UGC outreach email for a brand.

    Body:
        brand_id (int, required): the brand to render for
        force_regen (bool, optional): bypass image cache and regenerate
        style_preset (str, optional): override default style preset

    Returns:
        {subject, html_content, hero_image_url, creators, brand_name, vertical, cached}
    """
    try:
        data = request.get_json() or {}
        brand_id = data.get("brand_id")
        force_regen = bool(data.get("force_regen", False))

        if not brand_id:
            return jsonify({"error": "brand_id is required"}), 400

        conn = get_db_connection()
        cursor = conn.cursor(cursor_factory=RealDictCursor)
        cursor.execute("""
            SELECT id, brand_name, category, description, contact_email,
                   website, niches, logo
            FROM pr_brands WHERE id = %s
        """, (brand_id,))
        brand = cursor.fetchone()
        conn.close()

        if not brand:
            return jsonify({"error": "Brand not found"}), 404

        brand_name = brand.get("brand_name") or brand.get("name") or "your brand"
        vertical = (brand.get("category") or "lifestyle").lower().strip()

        niches = brand.get("niches") or []
        if isinstance(niches, str):
            try:
                niches = json.loads(niches)
            except Exception:
                niches = [niches]

        description = brand.get("description") or ""

        # 1. Get creators for this vertical
        creators = get_showcase_creators(vertical, limit=3)

        # 2. Generate (or fetch cached) UGC hero image
        hero_image_url = generate_ugc_image(
            brand_id=brand_id,
            brand_name=brand_name,
            vertical=vertical,
            niches=niches,
            description=description,
            force=force_regen,
        )
        cached = not force_regen and hero_image_url is not None

        # 3. Build email HTML
        html_content = _build_ugc_email_html(
            brand_name=brand_name,
            vertical=vertical,
            creators=creators,
            hero_image_url=hero_image_url,
            niches=niches,
            description=description,
        )

        subject = f"Free UGC content for {brand_name} — 3 creators ready to post"

        return jsonify({
            "success": True,
            "subject": subject,
            "html_content": html_content,
            "hero_image_url": hero_image_url,
            "creators": creators,
            "brand_name": brand_name,
            "vertical": vertical,
            "cached": cached,
        })

    except Exception as e:
        return jsonify({"error": str(e)}), 500


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
            WHERE type = 'brand_outreach'
              AND is_active = true
              AND id = ANY(%s)
            ORDER BY name
        """, (list(ALLOWED_BRAND_TEMPLATE_IDS),))
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

        valid_statuses = [
            'replied', 'interested', 'not_interested', 'signed_up',
            'wrong_email', 'bounced', 'do_not_contact', 'unsubscribe',
            'reply', 'auto_reply',
            'followup_due_day4', 'followup_due_day9', 'auto_replied', 'needs_review'
        ]
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


@admin_email_bp.route('/brand-outreach/inbox-log', methods=['GET'])
@admin_required
def get_brand_outreach_inbox_log():
    """
    Resolve inbound artifacts to brand_id using brand_outreach_log.
    Supports:
      - emails=comma-separated recipient emails
      - message_ids=comma-separated Message-ID tokens (with or without <>)
    """
    try:
        raw_emails = request.args.get('emails', '') or ''
        raw_message_ids = request.args.get('message_ids', '') or ''
        emails = [p.strip().lower() for p in raw_emails.split(',') if p.strip()]
        message_ids = []
        for p in raw_message_ids.split(','):
            token = (p or '').strip().strip('<>').strip()
            if token:
                message_ids.append(token)
        if len(emails) > 200 or len(message_ids) > 200:
            return jsonify({'error': 'Maximum 200 items per lookup key'}), 400
        if not emails and not message_ids:
            return jsonify({'success': True, 'matches': {}, 'message_id_matches': {}})

        conn = get_db_connection()
        cursor = conn.cursor(cursor_factory=RealDictCursor)
        _ensure_brand_outreach_log_message_id_column(cursor)

        rows = []
        if emails:
            cursor.execute("""
                SELECT DISTINCT ON (LOWER(TRIM(email_sent_to)))
                    LOWER(TRIM(email_sent_to)) AS email_norm,
                    brand_id,
                    sent_at
                FROM brand_outreach_log
                WHERE LOWER(TRIM(email_sent_to)) = ANY(%s)
                ORDER BY LOWER(TRIM(email_sent_to)), sent_at DESC
            """, (emails,))
            rows = cursor.fetchall()

        msg_rows = []
        if message_ids:
            cursor.execute("""
                SELECT DISTINCT ON (TRIM(BOTH '<>' FROM COALESCE(message_id, '')))
                    TRIM(BOTH '<>' FROM COALESCE(message_id, '')) AS message_id_norm,
                    brand_id,
                    email_sent_to,
                    sent_at
                FROM brand_outreach_log
                WHERE TRIM(BOTH '<>' FROM COALESCE(message_id, '')) = ANY(%s)
                ORDER BY TRIM(BOTH '<>' FROM COALESCE(message_id, '')), sent_at DESC
            """, (message_ids,))
            msg_rows = cursor.fetchall()
        conn.close()

        matches = {}
        for row in rows:
            sent_at = row.get('sent_at')
            if sent_at is not None and hasattr(sent_at, 'isoformat'):
                sent_at_s = sent_at.isoformat()
            else:
                sent_at_s = str(sent_at) if sent_at is not None else ''
            matches[row['email_norm']] = {
                'brand_id': row['brand_id'],
                'sent_at': sent_at_s,
            }

        message_id_matches = {}
        for row in msg_rows:
            sent_at = row.get('sent_at')
            if sent_at is not None and hasattr(sent_at, 'isoformat'):
                sent_at_s = sent_at.isoformat()
            else:
                sent_at_s = str(sent_at) if sent_at is not None else ''
            message_id_matches[row['message_id_norm']] = {
                'brand_id': row['brand_id'],
                'email_sent_to': row.get('email_sent_to'),
                'sent_at': sent_at_s,
            }
        return jsonify({
            'success': True,
            'matches': matches,  # backward compatibility: email matches
            'message_id_matches': message_id_matches,
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@admin_email_bp.route('/brand-outreach/inbox-sync', methods=['POST'])
@admin_required
def sync_brand_outreach_inbox_events():
    """
    Ingest parsed inbox events (from IMAP pull) and update outreach states.

    Body:
      events: [
        {
          "brand_id": 123,
          "event_type": "bounce|auto_reply|reply",
          "summary": "short text"
        }
      ]

    Maps: bounce -> wrong_email, auto_reply -> auto_reply, reply -> reply.
    """
    try:
        data = request.get_json() or {}
        events = data.get('events', [])
        if not isinstance(events, list):
            return jsonify({'error': 'events must be a list'}), 400
        if not events:
            return jsonify({'success': True, 'processed': 0, 'updated': 0, 'skipped': 0, 'results': []})

        conn = get_db_connection()
        cursor = conn.cursor(cursor_factory=RealDictCursor)
        updated = 0
        skipped = 0
        results = []

        for ev in events:
            brand_id = ev.get('brand_id')
            if not brand_id:
                skipped += 1
                results.append({'ok': False, 'reason': 'missing_brand_id'})
                continue

            event_type = (ev.get('event_type') or '').strip().lower()
            summary = (ev.get('summary') or '')[:500]

            if event_type == 'bounce':
                mapped_status = 'wrong_email'
            elif event_type == 'auto_reply':
                mapped_status = 'auto_reply'
            elif event_type == 'reply':
                mapped_status = 'reply'
            else:
                skipped += 1
                results.append({'brand_id': brand_id, 'ok': False, 'reason': f'unmapped_event:{event_type}'})
                continue

            notes = f"inbox_sync event={event_type}; {summary}"
            cursor.execute("""
                INSERT INTO brand_outreach_tracking (brand_id, outreach_count, last_response_status, response_notes, last_response_at)
                VALUES (%s, 0, %s, %s, NOW())
                ON CONFLICT (brand_id) DO UPDATE SET
                    last_response_status = EXCLUDED.last_response_status,
                    response_notes = EXCLUDED.response_notes,
                    last_response_at = NOW()
            """, (brand_id, mapped_status, notes))

            # Mirror inbox outcome into pr_brands.notes so ops can review
            # in the same Notes column used by brand pipeline records.
            note_label = {
                'wrong_email': 'Wrong email',
                'auto_reply': 'Automatic reply',
                'reply': 'Reply',
            }.get(mapped_status, mapped_status)
            note_line = f"[INBOX_SYNC] {note_label}: {summary}".strip()
            cursor.execute("""
                UPDATE pr_brands
                SET notes = CASE
                    WHEN notes IS NULL OR notes = '' THEN %s
                    ELSE notes || E'\n' || %s
                END
                WHERE id = %s
            """, (note_line, note_line, brand_id))
            updated += 1
            results.append({'brand_id': brand_id, 'ok': True, 'status': mapped_status})

        conn.commit()
        conn.close()
        return jsonify({
            'success': True,
            'processed': len(events),
            'updated': updated,
            'skipped': skipped,
            'results': results[:200]
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@admin_email_bp.route('/brand-outreach/followup-candidates', methods=['GET'])
@admin_required
def get_followup_candidates():
    """
    Return brands due for day-4 / day-9 follow-ups, excluding replied/bounced/opt-out.
    """
    try:
        day = int(request.args.get('day', 4))
        if day not in (4, 9):
            return jsonify({'error': 'day must be 4 or 9'}), 400
        limit = min(int(request.args.get('limit', 200)), 500)

        conn = get_db_connection()
        cursor = conn.cursor(cursor_factory=RealDictCursor)
        cursor.execute("""
            SELECT
                b.id AS brand_id, b.brand_name, b.contact_email, b.website,
                bo.last_contacted_at, bo.outreach_count, bo.last_response_status
            FROM brand_outreach_tracking bo
            JOIN pr_brands b ON b.id = bo.brand_id
            WHERE b.contact_email IS NOT NULL
              AND b.contact_email != ''
              AND bo.last_contacted_at <= NOW() - (%s * INTERVAL '1 day')
              AND COALESCE(bo.last_response_status, '') NOT IN
                  ('replied','interested','not_interested','signed_up','wrong_email','bounced','do_not_contact','unsubscribe','reply','auto_reply')
            ORDER BY bo.last_contacted_at ASC
            LIMIT %s
        """, (day, limit))
        rows = cursor.fetchall()
        conn.close()

        followup_status = 'followup_due_day4' if day == 4 else 'followup_due_day9'
        return jsonify({
            'success': True,
            'day': day,
            'followup_status': followup_status,
            'total': len(rows),
            'brands': rows
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500
