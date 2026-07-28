# -*- coding: utf-8 -*-
"""
Lifecycle Email Engine
Handles the 23-email strategy with state-based segmentation,
throttling, and feature flags.
"""
import os
import json
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime, timedelta, date
from typing import Optional, Dict, Any, List, Tuple
from functools import lru_cache

from jinja2 import Environment, FileSystemLoader
import psycopg2
from psycopg2.extras import RealDictCursor
from public_routes import make_unsubscribe_token

# ============================================
# CONFIGURATION
# ============================================

SMTP_SERVER = os.getenv('SMTP_SERVER', 'smtp.gmail.com')
SMTP_PORT = int(os.getenv('SMTP_PORT', 587))
SMTP_USERNAME = os.getenv('SMTP_USERNAME', 'team@newcollab.co')
SMTP_PASSWORD = os.getenv('SMTP_PASSWORD')
EMAIL_SENDER_NAME = os.getenv('EMAIL_SENDER_NAME', 'Your Newcollab Manager')
FRONTEND_URL = os.getenv('FRONTEND_URL', 'https://newcollab.co').rstrip('/')
BACKEND_URL = os.getenv('BACKEND_URL', 'https://api.newcollab.co').rstrip('/')

# Throttling limits
MAX_EMAILS_PER_DAY = 1
MAX_EMAILS_PER_WEEK = 2

# Quiet hours (22:00 - 06:00 user local time, default to EST)
QUIET_HOUR_START = 22
QUIET_HOUR_END = 6

# Template directory
TEMPLATE_DIR = os.path.join(os.path.dirname(__file__), 'templates')

# Initialize Jinja2
jinja_env = Environment(loader=FileSystemLoader(TEMPLATE_DIR))


# ============================================
# DATABASE CONNECTION
# ============================================

def get_db_connection():
    """Get database connection."""
    return psycopg2.connect(os.getenv('DATABASE_URL'))


# ============================================
# FEATURE FLAGS
# ============================================

def is_feature_enabled(flag_name: str) -> bool:
    """Check if a feature flag is enabled."""
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT enabled FROM email_feature_flags WHERE flag_name = %s",
            (flag_name,)
        )
        row = cursor.fetchone()
        return row[0] if row else False
    finally:
        conn.close()


def get_all_feature_flags() -> Dict[str, bool]:
    """Get all feature flags as a dict."""
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT flag_name, enabled FROM email_feature_flags")
        return {row[0]: row[1] for row in cursor.fetchall()}
    finally:
        conn.close()


def set_feature_flag(flag_name: str, enabled: bool) -> bool:
    """Set a feature flag value."""
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        cursor.execute(
            """UPDATE email_feature_flags
               SET enabled = %s, updated_at = NOW()
               WHERE flag_name = %s""",
            (enabled, flag_name)
        )
        conn.commit()
        return cursor.rowcount > 0
    finally:
        conn.close()


# ============================================
# USER STATE DETERMINATION
# ============================================

def determine_lifecycle_state(creator: Dict[str, Any]) -> str:
    """
    Determine the lifecycle state for a creator.
    States: new, explorer, engaged, doubter, maximizer, winner, dormant
    """
    # Extract relevant fields
    # Note: last_login doesn't exist in DB, using created_at as proxy
    days_since_signup = (datetime.now() - creator.get('created_at', datetime.now())).days
    days_since_login = days_since_signup  # Using signup date since last_login not tracked
    # Use total_unlocks (lifetime) not daily_unlocks_used (resets daily!)
    unlocks_used = creator.get('total_unlocks', 0) or creator.get('daily_unlocks_used', 0) or 0
    replies_received = creator.get('total_replies_received', 0) or 0
    has_pr_box = creator.get('first_pr_box_received_at') is not None
    subscription_tier = creator.get('subscription_tier', 'free') or 'free'

    # Priority order for state determination
    if has_pr_box or replies_received > 0:
        return 'winner'
    elif days_since_login >= 14:
        return 'dormant'
    elif unlocks_used >= 3 and subscription_tier == 'free':
        return 'maximizer'
    elif unlocks_used >= 2 and days_since_signup >= 14 and replies_received == 0:
        return 'doubter'
    elif unlocks_used >= 2:
        return 'engaged'
    elif unlocks_used >= 1:
        return 'explorer'
    elif days_since_signup <= 3:
        return 'new'
    else:
        return 'explorer'


def update_creator_state(creator_id: int) -> str:
    """Update and return the lifecycle state for a creator."""
    conn = get_db_connection()
    try:
        cursor = conn.cursor(cursor_factory=RealDictCursor)

        # Get creator data
        cursor.execute("""
            SELECT c.id, c.created_at, c.daily_unlocks_used,
                   c.total_replies_received, c.first_pr_box_received_at,
                   c.subscription_tier, c.lifecycle_state
            FROM creators c
            WHERE c.id = %s
        """, (creator_id,))
        creator = cursor.fetchone()

        # Get TOTAL lifetime unlocks (not daily which resets)
        cursor.execute("""
            SELECT COUNT(*) as total FROM brand_unlocks WHERE creator_id = %s
        """, (creator_id,))
        unlock_row = cursor.fetchone()
        if creator:
            creator['total_unlocks'] = unlock_row['total'] if unlock_row else 0

        if not creator:
            return 'unknown'

        new_state = determine_lifecycle_state(creator)

        # Update if changed
        if new_state != creator.get('lifecycle_state'):
            cursor.execute("""
                UPDATE creators
                SET lifecycle_state = %s, lifecycle_state_updated_at = NOW()
                WHERE id = %s
            """, (new_state, creator_id))
            conn.commit()

        return new_state
    finally:
        conn.close()


# ============================================
# THROTTLING
# ============================================

def check_throttling(creator_id: int, template_slug: str) -> Tuple[bool, str]:
    """
    Check if we can send an email to this creator.
    Returns (can_send, reason).
    """
    conn = get_db_connection()
    try:
        cursor = conn.cursor(cursor_factory=RealDictCursor)

        # Get template info for exemptions
        cursor.execute("""
            SELECT exempt_from_weekly_cap, exempt_from_daily_cap
            FROM lifecycle_email_templates WHERE slug = %s
        """, (template_slug,))
        template = cursor.fetchone()

        exempt_weekly = template.get('exempt_from_weekly_cap', False) if template else False
        exempt_daily = template.get('exempt_from_daily_cap', False) if template else False

        # Get creator throttle counters
        cursor.execute("""
            SELECT lifecycle_emails_sent_today, lifecycle_emails_sent_this_week,
                   lifecycle_last_email_date, lifecycle_week_start_date
            FROM creators WHERE id = %s
        """, (creator_id,))
        creator = cursor.fetchone()

        if not creator:
            return False, "Creator not found"

        today = date.today()

        # Reset daily counter if new day
        last_email_date = creator.get('lifecycle_last_email_date')
        if last_email_date and last_email_date < today:
            cursor.execute("""
                UPDATE creators SET lifecycle_emails_sent_today = 0 WHERE id = %s
            """, (creator_id,))
            conn.commit()
            creator['lifecycle_emails_sent_today'] = 0

        # Reset weekly counter if new week (Monday)
        week_start = creator.get('lifecycle_week_start_date')
        current_week_start = today - timedelta(days=today.weekday())
        if not week_start or week_start < current_week_start:
            cursor.execute("""
                UPDATE creators
                SET lifecycle_emails_sent_this_week = 0,
                    lifecycle_week_start_date = %s
                WHERE id = %s
            """, (current_week_start, creator_id))
            conn.commit()
            creator['lifecycle_emails_sent_this_week'] = 0

        # Check daily limit
        if not exempt_daily and (creator.get('lifecycle_emails_sent_today', 0) or 0) >= MAX_EMAILS_PER_DAY:
            return False, f"Daily limit reached ({MAX_EMAILS_PER_DAY}/day)"

        # Check weekly limit
        if not exempt_weekly and (creator.get('lifecycle_emails_sent_this_week', 0) or 0) >= MAX_EMAILS_PER_WEEK:
            return False, f"Weekly limit reached ({MAX_EMAILS_PER_WEEK}/week)"

        return True, "OK"
    finally:
        conn.close()


def is_quiet_hours(timezone: str = 'America/New_York') -> bool:
    """
    Check if current time is in quiet hours (22:00 - 06:00).
    For simplicity, uses server time. In production, use pytz for timezone handling.
    """
    current_hour = datetime.now().hour
    return current_hour >= QUIET_HOUR_START or current_hour < QUIET_HOUR_END


def increment_throttle_counters(creator_id: int):
    """Increment the throttle counters after sending an email."""
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("""
            UPDATE creators SET
                lifecycle_emails_sent_today = COALESCE(lifecycle_emails_sent_today, 0) + 1,
                lifecycle_emails_sent_this_week = COALESCE(lifecycle_emails_sent_this_week, 0) + 1,
                lifecycle_last_email_date = CURRENT_DATE,
                last_any_email_sent = NOW()
            WHERE id = %s
        """, (creator_id,))
        conn.commit()
    finally:
        conn.close()


# ============================================
# EMAIL SENDING
# ============================================

def render_template(template_file: str, context: Dict[str, Any]) -> str:
    """Render a Jinja2 template with the given context."""
    template = jinja_env.get_template(template_file)
    return template.render(**context)


def render_subject(subject_template: str, context: Dict[str, Any]) -> str:
    """Render a subject line template."""
    from jinja2 import Template
    return Template(subject_template).render(**context)


def send_lifecycle_email(
    to_email: str,
    template_slug: str,
    context: Dict[str, Any],
    creator_id: int,
    brand_id: Optional[int] = None,
    dedup_key: Optional[str] = None
) -> Tuple[bool, str]:
    """
    Send a lifecycle email.
    Returns (success, message_or_error).
    """
    conn = get_db_connection()
    try:
        cursor = conn.cursor(cursor_factory=RealDictCursor)

        # Get template info
        cursor.execute("""
            SELECT * FROM lifecycle_email_templates WHERE slug = %s AND active = true
        """, (template_slug,))
        template = cursor.fetchone()

        if not template:
            return False, f"Template {template_slug} not found or inactive"

        # Check feature flag
        feature_flag = template.get('feature_flag')
        if feature_flag and not is_feature_enabled(feature_flag):
            return False, f"Feature flag {feature_flag} is disabled"

        # Check deduplication
        if dedup_key:
            cursor.execute("""
                SELECT id FROM lifecycle_email_sends
                WHERE creator_id = %s AND dedup_key = %s
            """, (creator_id, dedup_key))
            if cursor.fetchone():
                return False, f"Email already sent (dedup_key: {dedup_key})"

        # Check throttling
        can_send, throttle_reason = check_throttling(creator_id, template_slug)
        if not can_send:
            return False, throttle_reason

        # Check quiet hours (except for exempt emails)
        if not template.get('exempt_from_daily_cap') and is_quiet_hours():
            return False, "Quiet hours - email queued"

        # Get user_id for unsubscribe token
        cursor.execute("SELECT user_id FROM creators WHERE id = %s", (creator_id,))
        creator_row = cursor.fetchone()
        user_id = creator_row['user_id'] if creator_row else creator_id

        # Generate signed unsubscribe URL
        unsubscribe_token = make_unsubscribe_token(str(user_id))
        unsubscribe_url = f"{BACKEND_URL}/api/public/unsubscribe?uid={user_id}&token={unsubscribe_token}"

        # Build context with defaults
        full_context = {
            'first_name': context.get('first_name', 'there'),
            'preheader': template.get('preheader_template', ''),
            'cta_url': context.get('cta_url', f"{FRONTEND_URL}/creator/dashboard/pr-ready"),
            'preferences_url': f"{FRONTEND_URL}/creator/dashboard/settings",
            'unsubscribe_url': unsubscribe_url,
            'subject': template.get('subject_template', ''),
            **context
        }

        # Add UTM parameters to CTA URL
        utm = f"utm_source=email&utm_medium=lifecycle&utm_campaign={template_slug}"
        if '?' in full_context['cta_url']:
            full_context['cta_url'] += f"&{utm}"
        else:
            full_context['cta_url'] += f"?{utm}"

        # Render email
        subject = render_subject(template['subject_template'], full_context)
        html_content = render_template(template['body_template_file'], full_context)

        # Send via SMTP
        success, message_id = _send_smtp(to_email, subject, html_content)

        if success:
            # Log the send
            cursor.execute("""
                INSERT INTO lifecycle_email_sends
                (creator_id, template_slug, brand_id, brand_slug, email_address,
                 subject_rendered, provider_message_id, status, dedup_key, utm_campaign)
                VALUES (%s, %s, %s, %s, %s, %s, %s, 'sent', %s, %s)
            """, (
                creator_id, template_slug, brand_id, context.get('brand_slug'),
                to_email, subject, message_id, dedup_key, template_slug
            ))

            # Update throttle counters
            increment_throttle_counters(creator_id)

            conn.commit()
            return True, message_id
        else:
            # Log failure
            cursor.execute("""
                INSERT INTO lifecycle_email_sends
                (creator_id, template_slug, brand_id, email_address,
                 subject_rendered, status, error_message, dedup_key)
                VALUES (%s, %s, %s, %s, %s, 'failed', %s, %s)
            """, (
                creator_id, template_slug, brand_id, to_email,
                subject, message_id, dedup_key
            ))
            conn.commit()
            return False, message_id
    except Exception as e:
        return False, str(e)
    finally:
        conn.close()


def _send_smtp(to_email: str, subject: str, html_content: str) -> Tuple[bool, str]:
    """Send email via SMTP. Returns (success, message_id_or_error)."""
    if not SMTP_PASSWORD:
        return False, "SMTP_PASSWORD not configured"

    try:
        msg = MIMEMultipart('alternative')
        msg['Subject'] = subject
        msg['From'] = f"{EMAIL_SENDER_NAME} <{SMTP_USERNAME}>"
        msg['To'] = to_email
        msg['Reply-To'] = 'team@newcollab.co'

        # Create plain text version (basic conversion)
        from html import unescape
        import re
        plain_text = re.sub(r'<[^>]+>', '', html_content)
        plain_text = unescape(plain_text)
        plain_text = re.sub(r'\n\s*\n', '\n\n', plain_text).strip()

        msg.attach(MIMEText(plain_text, 'plain'))
        msg.attach(MIMEText(html_content, 'html'))

        with smtplib.SMTP(SMTP_SERVER, SMTP_PORT) as server:
            server.starttls()
            server.login(SMTP_USERNAME, SMTP_PASSWORD)
            server.sendmail(SMTP_USERNAME, to_email, msg.as_string())

        # Generate a pseudo message ID
        message_id = f"<{datetime.now().strftime('%Y%m%d%H%M%S')}.{hash(to_email) % 10000}@newcollab.co>"
        return True, message_id
    except Exception as e:
        return False, str(e)


# ============================================
# TRIGGER EVALUATION
# ============================================

def get_eligible_emails(creator_id: int) -> List[Dict[str, Any]]:
    """
    Get list of emails that should be sent to this creator.
    Evaluates all triggers and returns eligible templates in priority order.
    """
    conn = get_db_connection()
    try:
        cursor = conn.cursor(cursor_factory=RealDictCursor)

        # Get creator data with all relevant fields
        # Note: last_login column doesn't exist, using created_at as fallback
        cursor.execute("""
            SELECT c.*,
                   u.email,
                   EXTRACT(DAY FROM NOW() - c.created_at) as days_since_signup,
                   EXTRACT(DAY FROM NOW() - c.created_at) as days_since_login
            FROM creators c
            JOIN users u ON c.user_id = u.id
            WHERE c.id = %s
        """, (creator_id,))
        creator = cursor.fetchone()

        if not creator:
            return []

        # Update state
        state = update_creator_state(creator_id)
        creator['lifecycle_state'] = state

        # Get all active templates
        cursor.execute("""
            SELECT * FROM lifecycle_email_templates
            WHERE active = true
            ORDER BY priority DESC
        """)
        templates = cursor.fetchall()

        eligible = []

        for template in templates:
            # Check feature flag
            if template.get('feature_flag') and not is_feature_enabled(template['feature_flag']):
                continue

            # Check required state
            required_states = template.get('required_user_state')
            if required_states and state not in required_states:
                continue

            # Check excluded tiers
            excluded_tiers = template.get('excluded_tiers')
            if excluded_tiers and creator.get('subscription_tier', 'free') in excluded_tiers:
                continue

            # Check throttling
            can_send, _ = check_throttling(creator_id, template['slug'])
            if not can_send:
                continue

            # Evaluate trigger conditions
            if evaluate_trigger(creator, template, cursor):
                eligible.append(template)

        return eligible
    finally:
        conn.close()


def evaluate_trigger(creator: Dict, template: Dict, cursor) -> bool:
    """Evaluate if a template's trigger conditions are met for a creator."""
    trigger_type = template.get('trigger_type')
    conditions = template.get('trigger_conditions', {})

    if not isinstance(conditions, dict):
        try:
            conditions = json.loads(conditions) if conditions else {}
        except:
            conditions = {}

    slug = template['slug']
    creator_id = creator['id']

    # Check if already sent (for one-time emails)
    if conditions.get('one_time'):
        cursor.execute("""
            SELECT id FROM lifecycle_email_sends
            WHERE creator_id = %s AND template_slug = %s
        """, (creator_id, slug))
        if cursor.fetchone():
            return False

    # Check if previous email in sequence was sent
    if conditions.get('requires_previous'):
        cursor.execute("""
            SELECT id FROM lifecycle_email_sends
            WHERE creator_id = %s AND template_slug = %s
        """, (creator_id, conditions['requires_previous']))
        if not cursor.fetchone():
            return False

    if trigger_type == 'immediate':
        # Immediate triggers are handled separately
        return False

    elif trigger_type == 'day_based':
        days = conditions.get('days_after_signup')
        if days is None:
            return False

        days_since_signup = creator.get('days_since_signup', 0) or 0

        # Check if we're in the right day window (day X to day X+2)
        if not (days <= days_since_signup <= days + 2):
            return False

        # Check additional conditions
        condition = conditions.get('condition')
        if condition == 'no_unlocks':
            # Check TOTAL lifetime unlocks, not just daily (daily resets each day!)
            cursor.execute("""
                SELECT COUNT(*) as total FROM brand_unlocks WHERE creator_id = %s
            """, (creator_id,))
            unlock_row = cursor.fetchone()
            total_unlocks = unlock_row['total'] if unlock_row else 0
            if total_unlocks > 0:
                return False
        elif condition == 'incomplete_profile':
            # Check if profile is incomplete (simplified check)
            if creator.get('bio') and creator.get('instagram_handle'):
                return False
        elif condition == 'has_activity':
            # Check TOTAL lifetime unlocks (not daily which resets)
            cursor.execute("""
                SELECT COUNT(*) as total FROM brand_unlocks WHERE creator_id = %s
            """, (creator_id,))
            unlock_row = cursor.fetchone()
            total_unlocks = unlock_row['total'] if unlock_row else 0
            if total_unlocks == 0:
                # Also check for any plan actions
                cursor.execute("""
                    SELECT id FROM ai_manager_actions
                    WHERE creator_id = %s AND completed_at IS NOT NULL
                    LIMIT 1
                """, (creator_id,))
                if not cursor.fetchone():
                    return False

        return True

    elif trigger_type == 'state_based':
        required_state = conditions.get('state')
        if required_state and creator.get('lifecycle_state') != required_state:
            return False

        # Check days after previous email
        if conditions.get('days_after_previous'):
            prev_slug = conditions.get('requires_previous')
            if prev_slug:
                cursor.execute("""
                    SELECT sent_at FROM lifecycle_email_sends
                    WHERE creator_id = %s AND template_slug = %s
                    ORDER BY sent_at DESC LIMIT 1
                """, (creator_id, prev_slug))
                prev_send = cursor.fetchone()
                if prev_send:
                    days_since = (datetime.now() - prev_send['sent_at']).days
                    if days_since < conditions['days_after_previous']:
                        return False

        # Check dormant days
        if conditions.get('days_dormant'):
            if (creator.get('days_since_login', 0) or 0) < conditions['days_dormant']:
                return False

        return True

    elif trigger_type == 'action_based':
        # These are triggered by specific user actions, not by cron
        # Return False for cron evaluation
        return False

    elif trigger_type == 'weekly':
        # Weekly digest - only on Mondays
        if datetime.now().weekday() != 0:  # 0 = Monday
            return False

        # Check if already sent this week
        cursor.execute("""
            SELECT id FROM lifecycle_email_sends
            WHERE creator_id = %s AND template_slug = %s
            AND sent_at >= date_trunc('week', NOW())
        """, (creator_id, slug))
        if cursor.fetchone():
            return False

        return True

    return False


# ============================================
# CRON JOB HANDLERS
# ============================================

def process_daily_lifecycle_emails(
    batch_size: int = 100,
    dry_run: bool = False,
    limit: int = None,
    test_email: str = None
) -> Dict[str, int]:
    """
    Process daily lifecycle emails for all eligible creators.
    Called by cron job.

    Args:
        batch_size: Number of creators to process per batch
        dry_run: If True, only count eligible users without sending emails
        limit: If set, only process this many creators total
        test_email: If set, only process creator with this email address
    """
    stats = {
        'processed': 0,
        'sent': 0,
        'skipped': 0,
        'errors': 0,
        'dry_run': dry_run,
        'test_email': test_email,
        'limit': limit
    }

    conn = get_db_connection()
    try:
        cursor = conn.cursor(cursor_factory=RealDictCursor)

        # Build query based on parameters
        if test_email:
            # Only process the specific test email
            cursor.execute("""
                SELECT c.id, c.user_id, u.email, c.lifecycle_state
                FROM creators c
                JOIN users u ON c.user_id = u.id
                WHERE u.email = %s
                AND u.is_verified = true
            """, (test_email,))
        else:
            # Get creators who might need emails
            # Exclude those who already hit daily limit or unsubscribed
            effective_limit = limit if limit else batch_size
            cursor.execute("""
                SELECT c.id, c.user_id, u.email, c.lifecycle_state
                FROM creators c
                JOIN users u ON c.user_id = u.id
                LEFT JOIN email_preferences ep ON ep.creator_id = c.id
                WHERE u.is_verified = true
                AND (ep.unsubscribed_all IS NULL OR ep.unsubscribed_all = false)
                AND (c.lifecycle_emails_sent_today IS NULL OR c.lifecycle_emails_sent_today < %s)
                ORDER BY c.created_at DESC NULLS LAST
                LIMIT %s
            """, (MAX_EMAILS_PER_DAY, effective_limit))

        creators = cursor.fetchall()
        stats['total_eligible'] = len(creators)

        # Track error details for debugging
        if 'error_details' not in stats:
            stats['error_details'] = []

        for creator in creators:
            stats['processed'] += 1

            try:
                eligible = get_eligible_emails(creator['id'])

                if not eligible:
                    stats['skipped'] += 1
                    continue

                # Send the highest priority email
                template = eligible[0]

                # In dry_run mode, just log what would be sent
                if dry_run:
                    stats['sent'] += 1
                    print(f"[DRY RUN] Would send {template['slug']} to creator {creator['id']} ({creator['email']})")
                    continue

                # Build context
                context = build_email_context(creator['id'], template['slug'])

                success, message = send_lifecycle_email(
                    to_email=creator['email'],
                    template_slug=template['slug'],
                    context=context,
                    creator_id=creator['id'],
                    dedup_key=f"{template['slug']}_{creator['id']}_{date.today().isoformat()}" if not template['trigger_conditions'].get('one_time') else f"{template['slug']}_{creator['id']}"
                )

                if success:
                    stats['sent'] += 1
                    print(f"Sent {template['slug']} to creator {creator['id']}")
                else:
                    stats['skipped'] += 1
                    print(f"Skipped {template['slug']} for creator {creator['id']}: {message}")

            except Exception as e:
                stats['errors'] += 1
                error_msg = f"Creator {creator['id']}: {str(e)}"
                print(f"Error processing {error_msg}")
                if len(stats.get('error_details', [])) < 5:  # Keep first 5 errors
                    stats['error_details'].append(error_msg)

        return stats
    finally:
        conn.close()


def process_weekly_digest(
    batch_size: int = 100,
    dry_run: bool = False,
    limit: int = None,
    test_email: str = None,
    skip_day_check: bool = False
) -> Dict[str, int]:
    """
    Send weekly digest emails (Monday only).

    Args:
        batch_size: Number of creators to process per batch
        dry_run: If True, only count eligible users without sending emails
        limit: If set, only process this many creators total
        test_email: If set, only process creator with this email address
        skip_day_check: If True, skip the Monday-only check (for testing)
    """
    # Allow skipping day check for testing
    if not skip_day_check and datetime.now().weekday() != 0:  # 0 = Monday
        return {'skipped': 0, 'reason': 'Not Monday'}

    stats = {
        'processed': 0,
        'sent': 0,
        'skipped': 0,
        'errors': 0,
        'version': 'v2',
        'dry_run': dry_run,
        'test_email': test_email,
        'limit': limit
    }

    if not is_feature_enabled('email_weekly_digest_v2'):
        return {'skipped': 0, 'reason': 'Feature disabled'}

    conn = get_db_connection()
    try:
        cursor = conn.cursor(cursor_factory=RealDictCursor)

        # Build query based on parameters
        if test_email:
            # Only process the specific test email
            cursor.execute("""
                SELECT c.id, c.user_id, u.email
                FROM creators c
                JOIN users u ON c.user_id = u.id
                WHERE u.email = %s
                AND u.is_verified = true
            """, (test_email,))
        else:
            # Get verified creators - use dedup to avoid sending twice per week
            effective_limit = limit if limit else batch_size
            cursor.execute("""
                SELECT c.id, c.user_id, u.email
                FROM creators c
                JOIN users u ON c.user_id = u.id
                WHERE u.is_verified = true
                ORDER BY c.id
                LIMIT %s
            """, (effective_limit,))

        creators = cursor.fetchall()
        stats['total_eligible'] = len(creators)

        for creator in creators:
            stats['processed'] += 1

            try:
                # Use dedup_key to prevent duplicate sends this week
                week_key = datetime.now().strftime('%Y-W%W')
                dedup_key = f"weekly_digest_{creator['id']}_{week_key}"

                context = build_weekly_digest_context(creator['id'])

                # In dry_run mode, just log what would be sent
                if dry_run:
                    stats['sent'] += 1
                    print(f"[DRY RUN] Would send weekly_digest to creator {creator['id']} ({creator['email']})")
                    continue

                success, message = send_lifecycle_email(
                    to_email=creator['email'],
                    template_slug='weekly_digest',
                    context=context,
                    creator_id=creator['id'],
                    dedup_key=dedup_key
                )

                if success:
                    stats['sent'] += 1
                else:
                    stats['skipped'] += 1
                    if 'skip_reasons' not in stats:
                        stats['skip_reasons'] = []
                    stats['skip_reasons'].append(f"Creator {creator['id']}: {message}")

            except Exception as e:
                stats['errors'] += 1
                if 'error_details' not in stats:
                    stats['error_details'] = []
                stats['error_details'].append(f"Creator {creator['id']}: {str(e)}")

        return stats
    finally:
        conn.close()


# ============================================
# CONTEXT BUILDERS
# ============================================

def build_email_context(creator_id: int, template_slug: str) -> Dict[str, Any]:
    """Build the context dict for a lifecycle email."""
    conn = get_db_connection()
    try:
        cursor = conn.cursor(cursor_factory=RealDictCursor)

        # Get creator info
        cursor.execute("""
            SELECT c.*, u.email, u.first_name
            FROM creators c
            JOIN users u ON c.user_id = u.id
            WHERE c.id = %s
        """, (creator_id,))
        creator = cursor.fetchone()

        if not creator:
            return {}

        unlocks_used = creator.get('daily_unlocks_used', 0) or 0
        unlocks_quota = 3
        subscription_tier = creator.get('subscription_tier', 'free')

        context = {
            'first_name': creator.get('first_name') or creator.get('username') or 'there',
            'current_score': 0,  # creator_score column doesn't exist
            'unlocks_used': unlocks_used,
            'unlocks_quota': unlocks_quota,
            'unlocks_available': max(0, unlocks_quota - unlocks_used),
            'pitches_sent': creator.get('total_pitches_sent', 0) or 0,
            'replies_count': creator.get('total_replies_received', 0) or 0,
            'subscription_tier': subscription_tier,
            'is_pro': subscription_tier in ('pro', 'elite'),
        }

        # Calculate reset date
        from calendar import monthrange
        today = date.today()
        last_day = monthrange(today.year, today.month)[1]
        reset_date = date(today.year, today.month, last_day) + timedelta(days=1)
        context['reset_date'] = reset_date.strftime('%B %d')
        context['days_until_reset'] = (reset_date - today).days
        context['month'] = today.strftime('%B')

        # Get pending follow-ups count (pitched > 7 days ago, no response)
        try:
            cursor.execute("""
                SELECT COUNT(*) as cnt FROM creator_pipeline
                WHERE creator_id = %s
                AND stage = 'pitched'
                AND pitched_at < NOW() - INTERVAL '7 days'
            """, (creator_id,))
            row = cursor.fetchone()
            context['pending_count'] = row['cnt'] if row else 0
        except Exception:
            context['pending_count'] = 0

        # Get new brands count (added in last 30 days)
        try:
            cursor.execute("""
                SELECT COUNT(*) as cnt FROM pr_brands
                WHERE created_at >= NOW() - INTERVAL '30 days'
            """)
            row = cursor.fetchone()
            context['new_brands_count'] = row['cnt'] if row else 0
        except Exception:
            context['new_brands_count'] = 0

        # Template-specific context
        if template_slug.startswith('max_'):
            context['cta_url'] = f"{FRONTEND_URL}/creator/dashboard/pr-ready"
        elif template_slug.startswith('edu_'):
            context['cta_url'] = f"{FRONTEND_URL}/creator/dashboard/pr-ready"
        elif template_slug == 'weekly_digest':
            context = {**context, **build_weekly_digest_context(creator_id)}

        return context
    finally:
        conn.close()


def build_weekly_digest_context(creator_id: int) -> Dict[str, Any]:
    """Build context specifically for the weekly digest email."""
    conn = get_db_connection()
    try:
        cursor = conn.cursor(cursor_factory=RealDictCursor)

        # Get creator - only select columns that definitely exist
        cursor.execute("""
            SELECT c.id, c.username, u.email, u.first_name
            FROM creators c
            JOIN users u ON c.user_id = u.id
            WHERE c.id = %s
        """, (creator_id,))
        creator = cursor.fetchone()

        if not creator:
            return {}

        # Get new brands this week
        try:
            cursor.execute("""
                SELECT brand_name AS name, category FROM pr_brands
                WHERE created_at >= NOW() - INTERVAL '7 days'
                ORDER BY created_at DESC
                LIMIT 3
            """)
            new_brands = cursor.fetchall()
        except Exception as e:
            print(f"[WEEKLY DIGEST] Error fetching new brands: {e}")
            new_brands = []

        context = {
            'first_name': creator.get('first_name') or creator.get('username') or 'there',
            'current_score': 0,
            'score_delta': 0,
            'unlocks_used': 0,
            'unlocks_quota': 3,
            'replies_count': 0,
            'weekly_theme_title': 'Bio polish',
            'weekly_theme_body': 'Brands scan bios in 3 seconds. This week, audit yours.',
            'new_brands': [
                {
                    'name': b['name'],
                    'category': b.get('category') or 'Lifestyle',
                    'reason': 'New this week'
                } for b in new_brands
            ] if new_brands else [
                {'name': 'Check your matches', 'category': '', 'reason': 'See what fits your profile'}
            ],
            'win_story': None,
            'cta_url': f"{FRONTEND_URL}/creator/dashboard/pr-ready",
        }

        return context
    finally:
        conn.close()


def build_brand_email_context(creator_id: int, brand_id: int) -> Dict[str, Any]:
    """Build context for brand-specific emails (pitching, follow-up, etc.)."""
    import re
    conn = get_db_connection()
    try:
        cursor = conn.cursor(cursor_factory=RealDictCursor)

        # Get brand info - use brand_name (not name) as per pr_brands schema
        cursor.execute("""
            SELECT id, brand_name, category, response_rate
            FROM pr_brands WHERE id = %s
        """, (brand_id,))
        brand = cursor.fetchone()

        if not brand:
            return {}

        # Generate slug from brand_name (pr_brands has no slug column)
        brand_slug = re.sub(r'[^a-z0-9]+', '-', brand['brand_name'].lower()).strip('-')

        # Get base context
        context = build_email_context(creator_id, 'pitch_brand')

        # Add brand-specific fields
        context.update({
            'brand_name': brand['brand_name'],
            'brand_slug': brand_slug,
            'brand_category': brand.get('category') or 'Lifestyle',
            'brand_response_rate': brand.get('response_rate'),
            'cta_url': f"{FRONTEND_URL}/creator/dashboard/pr-pipeline?brand_id={brand_id}",
            'remove_url': f"{FRONTEND_URL}/creator/dashboard/pr-pipeline?remove={brand_id}",
        })

        return context
    finally:
        conn.close()


# ============================================
# ACTION-TRIGGERED EMAILS
# ============================================

def trigger_welcome_email(creator_id: int, email: str, first_name: str = None):
    """Trigger welcome email immediately after verification."""
    if not is_feature_enabled('email_onboarding_v2'):
        return False, "Feature disabled"

    context = {
        'first_name': first_name or 'there',
        'cta_url': f"{FRONTEND_URL}/creator/dashboard/pr-ready",
    }

    return send_lifecycle_email(
        to_email=email,
        template_slug='welcome_manager',
        context=context,
        creator_id=creator_id,
        dedup_key=f"welcome_manager_{creator_id}"
    )


def trigger_reply_celebration(creator_id: int, brand_id: int):
    """Trigger celebration email when creator marks a reply."""
    if not is_feature_enabled('email_celebration_v2'):
        return False, "Feature disabled"

    conn = get_db_connection()
    try:
        cursor = conn.cursor(cursor_factory=RealDictCursor)

        # Get creator email
        cursor.execute("""
            SELECT u.email FROM creators c
            JOIN users u ON c.user_id = u.id
            WHERE c.id = %s
        """, (creator_id,))
        result = cursor.fetchone()
        if not result:
            return False, "Creator not found"

        context = build_brand_email_context(creator_id, brand_id)
        context['cta_url'] = f"{FRONTEND_URL}/creator/dashboard/pr-pipeline"

        return send_lifecycle_email(
            to_email=result['email'],
            template_slug='celebration_reply',
            context=context,
            creator_id=creator_id,
            brand_id=brand_id,
            dedup_key=f"celebration_reply_{creator_id}_{brand_id}"
        )
    finally:
        conn.close()


def trigger_first_pr_box(creator_id: int, brand_id: int):
    """Trigger celebration email for first PR box."""
    if not is_feature_enabled('email_celebration_v2'):
        return False, "Feature disabled"

    conn = get_db_connection()
    try:
        cursor = conn.cursor(cursor_factory=RealDictCursor)

        # Check if this is actually the first PR box
        cursor.execute("""
            SELECT first_pr_box_received_at FROM creators WHERE id = %s
        """, (creator_id,))
        creator = cursor.fetchone()

        if creator and creator.get('first_pr_box_received_at'):
            return False, "Not first PR box"

        # Mark first PR box
        cursor.execute("""
            UPDATE creators SET first_pr_box_received_at = NOW() WHERE id = %s
        """, (creator_id,))

        # Get creator email
        cursor.execute("""
            SELECT u.email FROM creators c
            JOIN users u ON c.user_id = u.id
            WHERE c.id = %s
        """, (creator_id,))
        result = cursor.fetchone()
        if not result:
            return False, "Creator not found"

        context = build_brand_email_context(creator_id, brand_id)
        context['cta_url'] = f"{FRONTEND_URL}/creator/dashboard/for-you"

        conn.commit()

        return send_lifecycle_email(
            to_email=result['email'],
            template_slug='celebration_first_box',
            context=context,
            creator_id=creator_id,
            brand_id=brand_id,
            dedup_key=f"first_pr_box_{creator_id}"
        )
    finally:
        conn.close()


def trigger_quota_hit(creator_id: int):
    """Trigger email when user hits 3/3 unlocks."""
    if not is_feature_enabled('email_maximizer_pro_v2'):
        return False, "Feature disabled"

    conn = get_db_connection()
    try:
        cursor = conn.cursor(cursor_factory=RealDictCursor)

        # Check quota
        cursor.execute("""
            SELECT c.daily_unlocks_used, c.subscription_tier, u.email
            FROM creators c
            JOIN users u ON c.user_id = u.id
            WHERE c.id = %s
        """, (creator_id,))
        result = cursor.fetchone()

        if not result:
            return False, "Creator not found"

        if result.get('subscription_tier') in ('pro', 'elite'):
            return False, "Pro user - no quota"

        if (result.get('daily_unlocks_used') or 0) < 3:
            return False, "Quota not hit"

        context = build_email_context(creator_id, 'max_quota_hit')

        # Dedup key includes month to allow one per month
        from datetime import date
        month_key = date.today().strftime('%Y-%m')

        return send_lifecycle_email(
            to_email=result['email'],
            template_slug='max_quota_hit',
            context=context,
            creator_id=creator_id,
            dedup_key=f"max_quota_hit_{creator_id}_{month_key}"
        )
    finally:
        conn.close()
