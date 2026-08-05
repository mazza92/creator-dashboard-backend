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
    days_since_signup = (datetime.now() - creator.get('created_at', datetime.now())).days
    # Use days_since_login from query (uses last_login), fallback to days_since_signup
    days_since_login = creator.get('days_since_login') or days_since_signup
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

        # Get creator data including last_login for dormancy detection
        cursor.execute("""
            SELECT c.id, c.created_at, c.daily_unlocks_used,
                   c.total_replies_received, c.first_pr_box_received_at,
                   c.subscription_tier, c.lifecycle_state,
                   EXTRACT(DAY FROM NOW() - COALESCE(u.last_login, c.created_at)) as days_since_login
            FROM creators c
            JOIN users u ON c.user_id = u.id
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
        # Use last_login from users table for dormancy detection
        cursor.execute("""
            SELECT c.*,
                   u.email,
                   EXTRACT(DAY FROM NOW() - c.created_at) as days_since_signup,
                   EXTRACT(DAY FROM NOW() - COALESCE(u.last_login, c.created_at)) as days_since_login
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
        logging.info(f"[ELIGIBLE] Creator {creator_id}: Found {len(templates)} active templates, state={state}")

        eligible = []

        for template in templates:
            slug = template['slug']

            # Check feature flag
            if template.get('feature_flag') and not is_feature_enabled(template['feature_flag']):
                logging.debug(f"[ELIGIBLE] Creator {creator_id}: {slug} skipped - feature flag disabled")
                continue

            # Check required state
            required_states = template.get('required_user_state')
            if required_states and state not in required_states:
                logging.debug(f"[ELIGIBLE] Creator {creator_id}: {slug} skipped - state {state} not in {required_states}")
                continue

            # Check excluded tiers
            excluded_tiers = template.get('excluded_tiers')
            if excluded_tiers and creator.get('subscription_tier', 'free') in excluded_tiers:
                logging.debug(f"[ELIGIBLE] Creator {creator_id}: {slug} skipped - tier excluded")
                continue

            # Check throttling
            can_send, reason = check_throttling(creator_id, template['slug'])
            if not can_send:
                logging.debug(f"[ELIGIBLE] Creator {creator_id}: {slug} skipped - throttled: {reason}")
                continue

            # Evaluate trigger conditions
            if evaluate_trigger(creator, template, cursor):
                logging.info(f"[ELIGIBLE] Creator {creator_id}: {slug} ELIGIBLE")
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
                # Also check for any pipeline activity (saved brands)
                cursor.execute("""
                    SELECT 1 FROM creator_pipeline
                    WHERE creator_id = %s
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

    import logging
    logging.info(f"[LIFECYCLE CRON] Starting daily emails: batch_size={batch_size}, limit={limit}, dry_run={dry_run}, test_email={test_email}")

    conn = get_db_connection()
    try:
        cursor = conn.cursor(cursor_factory=RealDictCursor)

        # Build query based on parameters
        if test_email:
            # Only process the specific test email (still respect unsubscribe)
            cursor.execute("""
                SELECT c.id, c.user_id, u.email, c.lifecycle_state
                FROM creators c
                JOIN users u ON c.user_id = u.id
                LEFT JOIN email_preferences ep ON ep.creator_id = c.id
                WHERE u.email = %s
                AND u.is_verified = true
                AND ep.unsubscribed_at IS NULL
            """, (test_email,))
        else:
            # Get creators who might need emails
            # Exclude those who already hit daily limit or unsubscribed
            # Include creators whose counter is from a previous day (needs reset)
            effective_limit = limit if limit else batch_size
            cursor.execute("""
                SELECT c.id, c.user_id, u.email, c.lifecycle_state
                FROM creators c
                JOIN users u ON c.user_id = u.id
                LEFT JOIN email_preferences ep ON ep.creator_id = c.id
                WHERE u.is_verified = true
                AND ep.unsubscribed_at IS NULL
                AND (
                    c.lifecycle_emails_sent_today IS NULL
                    OR c.lifecycle_emails_sent_today < %s
                    OR c.lifecycle_last_email_date IS NULL
                    OR c.lifecycle_last_email_date < CURRENT_DATE
                )
                ORDER BY COALESCE(u.last_login, NOW()) ASC, c.created_at ASC
                LIMIT %s
            """, (MAX_EMAILS_PER_DAY, effective_limit))

        creators = cursor.fetchall()
        stats['total_eligible'] = len(creators)
        logging.info(f"[LIFECYCLE CRON] Found {len(creators)} eligible creators to process")

        # Track error details for debugging
        if 'error_details' not in stats:
            stats['error_details'] = []

        # Track which creators are processed for debugging
        if 'processed_creators' not in stats:
            stats['processed_creators'] = []

        for creator in creators:
            stats['processed'] += 1
            logging.info(f"[LIFECYCLE CRON] Processing creator {creator['id']} (state: {creator.get('lifecycle_state')})")

            try:
                eligible = get_eligible_emails(creator['id'])
                logging.info(f"[LIFECYCLE CRON] Creator {creator['id']} has {len(eligible)} eligible emails")

                if not eligible:
                    stats['skipped'] += 1
                    if len(stats['processed_creators']) < 10:
                        stats['processed_creators'].append({
                            'id': creator['id'],
                            'state': creator.get('lifecycle_state'),
                            'eligible_count': 0,
                            'result': 'skipped_no_eligible'
                        })
                    continue

                # Send the highest priority email
                template = eligible[0]

                # In dry_run mode, just log what would be sent
                if dry_run:
                    stats['sent'] += 1
                    if len(stats['processed_creators']) < 10:
                        stats['processed_creators'].append({
                            'id': creator['id'],
                            'state': creator.get('lifecycle_state'),
                            'eligible_count': len(eligible),
                            'would_send': template['slug'],
                            'result': 'dry_run_sent'
                        })
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

        logging.info(f"[LIFECYCLE CRON] Completed: processed={stats['processed']}, sent={stats['sent']}, skipped={stats['skipped']}, errors={stats['errors']}")
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
            # Only process the specific test email (still respect unsubscribe)
            cursor.execute("""
                SELECT c.id, c.user_id, u.email
                FROM creators c
                JOIN users u ON c.user_id = u.id
                LEFT JOIN email_preferences ep ON ep.creator_id = c.id
                WHERE u.email = %s
                AND u.is_verified = true
                AND ep.unsubscribed_at IS NULL
            """, (test_email,))
        else:
            # Get verified creators - exclude unsubscribed
            effective_limit = limit if limit else batch_size
            cursor.execute("""
                SELECT c.id, c.user_id, u.email
                FROM creators c
                JOIN users u ON c.user_id = u.id
                LEFT JOIN email_preferences ep ON ep.creator_id = c.id
                WHERE u.is_verified = true
                AND ep.unsubscribed_at IS NULL
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

def _calculate_creator_progress_score(creator: Dict) -> int:
    """
    Calculate creator's PR readiness progress score (0-100).
    Based on profile completeness and activity.
    """
    score = 0

    # Profile completeness (40 points max)
    if creator.get('bio') and len(str(creator.get('bio', '')).strip()) >= 20:
        score += 10  # Has meaningful bio
    if creator.get('image_profile'):
        score += 8   # Has profile image
    if creator.get('niche') or creator.get('creator_niches'):
        score += 7   # Has set niches
    if creator.get('kit_published'):
        score += 15  # Has published media kit

    # Activity signals (35 points max)
    pitches_sent = creator.get('total_pitches_sent') or 0
    if pitches_sent >= 1:
        score += 10
    if pitches_sent >= 5:
        score += 5
    if pitches_sent >= 10:
        score += 5

    replies_received = creator.get('total_replies_received') or 0
    if replies_received >= 1:
        score += 10
    if replies_received >= 3:
        score += 5

    # Engagement quality (25 points max)
    followers = creator.get('followers_count') or creator.get('creator_followers') or 0
    if followers >= 500:
        score += 5
    if followers >= 1000:
        score += 5
    if followers >= 5000:
        score += 5

    engagement = creator.get('engagement_rate') or creator.get('avg_engagement_rate') or 0
    if engagement and float(engagement) >= 1:
        score += 5
    if engagement and float(engagement) >= 3:
        score += 5

    return min(100, score)


def _get_weekly_themes() -> List[Dict[str, str]]:
    """Rotating weekly themes for the Monday brief."""
    return [
        {'title': 'Bio polish', 'body': 'Brands scan bios in 3 seconds. This week, audit yours for clarity and a clear CTA.'},
        {'title': 'Portfolio refresh', 'body': 'Update your media kit with your best recent content. First impressions matter.'},
        {'title': 'Pitch practice', 'body': 'Draft a pitch to your dream brand. Even if you don\'t send it, practice builds confidence.'},
        {'title': 'Engagement boost', 'body': 'Reply to every comment this week. Active creators get noticed by brand scouts.'},
        {'title': 'Niche down', 'body': 'Brands love specialists. Double down on your core content theme this week.'},
        {'title': 'Follow-up week', 'body': 'No response in 7 days? Send a friendly follow-up. Persistence pays.'},
        {'title': 'Content batch', 'body': 'Create 3+ pieces of content in one session. Consistency attracts brand attention.'},
        {'title': 'Network expansion', 'body': 'Connect with 3 creators in your niche. Collaborations lead to brand introductions.'},
    ]


def _get_niche_to_category_map() -> Dict[str, List[str]]:
    """Map creator niches to brand categories for matching."""
    return {
        'beauty': ['Beauty', 'Skincare', 'Makeup', 'Haircare'],
        'skincare': ['Skincare', 'Beauty'],
        'fashion': ['Fashion', 'Clothing', 'Accessories', 'Jewelry'],
        'lifestyle': ['Lifestyle', 'Home', 'Food & Beverage'],
        'fitness': ['Fitness', 'Activewear', 'Health'],
        'food': ['Food & Beverage', 'Kitchen', 'Home'],
        'parenting': ['Baby', 'Kids', 'Family', 'Home'],
        'mom': ['Baby', 'Kids', 'Family', 'Lifestyle'],
        'travel': ['Travel', 'Lifestyle', 'Fashion'],
        'tech': ['Tech', 'Gaming', 'Gadgets'],
        'pet': ['Pet', 'Animals'],
        'wellness': ['Wellness', 'Health', 'Skincare'],
        'home': ['Home', 'Kitchen', 'Lifestyle'],
        'haircare': ['Haircare', 'Beauty'],
        'makeup': ['Makeup', 'Beauty', 'Skincare'],
    }


def get_for_you_brands_for_email(creator_id: int, cursor, limit: int = 3) -> List[Dict]:
    """
    Get For You brand recommendations for email.
    Returns brands matching creator's niches, excluding already unlocked brands.
    Simple format: brand name + short description only.

    Uses creator_id + week for rotation so each creator sees different brands.
    Matches on creator's actual niches directly (no expansion to avoid over-broad matching).
    """
    from datetime import datetime
    import logging

    # Get creator's niches
    cursor.execute("""
        SELECT c.niche, c.creator_niches
        FROM creators c WHERE c.id = %s
    """, (creator_id,))
    creator = cursor.fetchone()

    if not creator:
        return []

    # Parse niches - use creator_niches as primary source (cleaner array)
    creator_niches = creator.get('creator_niches') or []

    if isinstance(creator_niches, str):
        try:
            creator_niches = json.loads(creator_niches)
        except:
            creator_niches = []

    # Deduplicate and normalize
    unique_niches = list(set(n.strip().lower() for n in creator_niches if n and isinstance(n, str)))

    logging.info(f"[FOR YOU BRANDS] Creator {creator_id} niches: {unique_niches}")

    # Get already unlocked brands to exclude
    cursor.execute("SELECT brand_id FROM brand_unlocks WHERE creator_id = %s", (creator_id,))
    unlocked_ids = [r['brand_id'] for r in cursor.fetchall()]

    # Use creator_id + week for unique rotation per creator
    week_number = datetime.now().isocalendar()[1]
    rotation_offset = ((creator_id * 17) + (week_number * 7)) % 1000

    logging.info(f"[FOR YOU BRANDS] Creator {creator_id} rotation offset: {rotation_offset}")

    # Build query - match on creator's actual niches directly
    exclude_clause = "AND b.id != ALL(%s)" if unlocked_ids else ""

    if unique_niches:
        # Match category directly against creator's niches (case-insensitive)
        query_params = [unique_niches]
        if unlocked_ids:
            query_params.append(unlocked_ids)
        query_params.append(rotation_offset)

        cursor.execute(f"""
            SELECT b.brand_name AS name, b.description
            FROM pr_brands b
            WHERE LOWER(b.category) = ANY(%s)
              AND b.status = 'published'
              AND b.accepting_pr = true
              {exclude_clause}
            ORDER BY (b.id + %s) %% 1000, b.created_at DESC
            LIMIT %s
        """, tuple(query_params) + (limit,))

        brands = cursor.fetchall()
        logging.info(f"[FOR YOU BRANDS] Creator {creator_id} matched {len(brands)} brands on exact niches")

        # If no exact matches, try broader search without category filter
        if not brands:
            logging.info(f"[FOR YOU BRANDS] Creator {creator_id} no exact matches, using popular brands")
            query_params = []
            if unlocked_ids:
                query_params.append(unlocked_ids)
            query_params.append(rotation_offset)

            cursor.execute(f"""
                SELECT b.brand_name AS name, b.description
                FROM pr_brands b
                WHERE b.status = 'published'
                  AND b.accepting_pr = true
                  {exclude_clause}
                ORDER BY (b.id + %s) %% 1000, b.created_at DESC
                LIMIT %s
            """, tuple(query_params) + (limit,))
            brands = cursor.fetchall()
    else:
        # No niche - show popular accepting brands
        query_params = []
        if unlocked_ids:
            query_params.append(unlocked_ids)
        query_params.append(rotation_offset)

        cursor.execute(f"""
            SELECT b.brand_name AS name, b.description
            FROM pr_brands b
            WHERE b.status = 'published'
              AND b.accepting_pr = true
              {exclude_clause}
            ORDER BY (b.id + %s) %% 1000, b.created_at DESC
            LIMIT %s
        """, tuple(query_params) + (limit,))
        brands = cursor.fetchall()

    # Simple format: name + truncated description
    result = []
    for b in brands:
        desc = b.get('description') or ''
        # Truncate description to ~60 chars
        if len(desc) > 60:
            desc = desc[:57].rsplit(' ', 1)[0] + '...'

        result.append({
            'name': b['name'],
            'description': desc
        })

    logging.info(f"[FOR YOU BRANDS] Creator {creator_id} returning: {[b['name'] for b in result]}")

    return result


def _get_matching_categories(creator: Dict) -> set:
    """Get brand categories that match a creator's niches (lowercase for DB matching)."""
    creator_niche = creator.get('niche') or ''
    creator_niches = creator.get('creator_niches') or []

    # Parse niche if it's a JSON string
    if isinstance(creator_niche, str) and creator_niche.startswith('['):
        try:
            creator_niche = json.loads(creator_niche)
        except:
            pass

    if isinstance(creator_niches, str):
        try:
            creator_niches = json.loads(creator_niches)
        except:
            creator_niches = []

    # Normalize to list
    if isinstance(creator_niche, list):
        niche_list = creator_niche
    elif creator_niche:
        niche_list = [creator_niche]
    else:
        niche_list = []

    all_niches = niche_list + (creator_niches if isinstance(creator_niches, list) else [])
    all_niches = [n.strip().lower() for n in all_niches if n and isinstance(n, str)]

    niche_to_category = _get_niche_to_category_map()
    matching_categories = set()

    for niche in all_niches:
        for key, categories in niche_to_category.items():
            if key in niche:
                # Lowercase categories to match database
                matching_categories.update(c.lower() for c in categories)

    # Fallback to broad categories if no matches (lowercase)
    if not matching_categories:
        matching_categories = {'lifestyle', 'beauty', 'fashion'}

    return matching_categories


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

        subscription_tier = creator.get('subscription_tier', 'free')
        is_pro = subscription_tier in ('pro', 'elite')

        # Calculate unlocks correctly (same as weekly_digest)
        unlocks_remaining = creator.get('unlocks_remaining')
        if is_pro:
            unlocks_used = 0
            unlocks_quota = '∞'
            unlocks_available = '∞'
        elif unlocks_remaining is not None:
            unlocks_used = max(0, 3 - unlocks_remaining)
            unlocks_quota = 3
            unlocks_available = unlocks_remaining
        else:
            unlocks_used = creator.get('daily_unlocks_used') or 0
            unlocks_quota = 3
            unlocks_available = max(0, 3 - unlocks_used)

        # Calculate real progress score
        current_score = _calculate_creator_progress_score(creator)

        context = {
            'first_name': creator.get('first_name') or creator.get('username') or 'there',
            'current_score': current_score,
            'unlocks_used': unlocks_used,
            'unlocks_quota': unlocks_quota,
            'unlocks_available': unlocks_available,
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
        elif template_slug == 'time_followup':
            # Get actual pending brands for follow-up email
            try:
                cursor.execute("""
                    SELECT p.brand_name AS name,
                           EXTRACT(DAY FROM NOW() - p.pitched_at)::int AS days_ago,
                           COALESCE(b.response_rate, 10) AS response_rate
                    FROM creator_pipeline p
                    LEFT JOIN pr_brands b ON p.brand_name = b.brand_name
                    WHERE p.creator_id = %s
                    AND p.stage = 'pitched'
                    AND p.pitched_at < NOW() - INTERVAL '7 days'
                    ORDER BY p.pitched_at ASC
                    LIMIT 5
                """, (creator_id,))
                pending = cursor.fetchall()
                context['pending_brands'] = [
                    {'name': p['name'], 'days_ago': p['days_ago'] or 7, 'response_rate': p['response_rate'] or 10}
                    for p in pending
                ] if pending else []
            except Exception as e:
                print(f"[FOLLOWUP] Error fetching pending brands: {e}")
                context['pending_brands'] = []
            context['cta_url'] = f"{FRONTEND_URL}/creator/dashboard/pr-pipeline"
        elif template_slug in ('pitch_brand', 'brand_waiting', 'did_reply', 'celebration_reply', 'celebration_first_box'):
            # Brand-specific emails - get the most recent brand from pipeline
            try:
                if template_slug == 'pitch_brand':
                    # Brand saved but not pitched (2+ days ago)
                    cursor.execute("""
                        SELECT p.brand_name, p.created_at,
                               EXTRACT(DAY FROM NOW() - p.created_at)::int AS days_ago,
                               COALESCE(b.response_rate, 15) AS response_rate
                        FROM creator_pipeline p
                        LEFT JOIN pr_brands b ON p.brand_name = b.brand_name
                        WHERE p.creator_id = %s AND p.stage = 'saved'
                        ORDER BY p.created_at DESC LIMIT 1
                    """, (creator_id,))
                elif template_slug == 'brand_waiting':
                    # Brand saved 6+ days ago, not pitched
                    cursor.execute("""
                        SELECT p.brand_name, p.created_at,
                               EXTRACT(DAY FROM NOW() - p.created_at)::int AS days_ago,
                               COALESCE(b.response_rate, 15) AS response_rate
                        FROM creator_pipeline p
                        LEFT JOIN pr_brands b ON p.brand_name = b.brand_name
                        WHERE p.creator_id = %s AND p.stage = 'saved'
                        AND p.created_at < NOW() - INTERVAL '5 days'
                        ORDER BY p.created_at ASC LIMIT 1
                    """, (creator_id,))
                elif template_slug == 'did_reply':
                    # Brand pitched 2+ weeks ago
                    cursor.execute("""
                        SELECT p.brand_name, p.pitched_at,
                               EXTRACT(DAY FROM NOW() - p.pitched_at)::int AS days_ago,
                               COALESCE(b.response_rate, 15) AS response_rate
                        FROM creator_pipeline p
                        LEFT JOIN pr_brands b ON p.brand_name = b.brand_name
                        WHERE p.creator_id = %s AND p.stage = 'pitched'
                        AND p.pitched_at < NOW() - INTERVAL '13 days'
                        ORDER BY p.pitched_at ASC LIMIT 1
                    """, (creator_id,))
                else:
                    # Celebration emails - get most recent replied/won brand
                    cursor.execute("""
                        SELECT p.brand_name, p.updated_at,
                               0 AS days_ago,
                               COALESCE(b.response_rate, 15) AS response_rate
                        FROM creator_pipeline p
                        LEFT JOIN pr_brands b ON p.brand_name = b.brand_name
                        WHERE p.creator_id = %s AND p.stage IN ('replied', 'won')
                        ORDER BY p.updated_at DESC LIMIT 1
                    """, (creator_id,))

                brand_row = cursor.fetchone()
                if brand_row:
                    context['brand_name'] = brand_row['name'] if 'name' in brand_row else brand_row['brand_name']
                    context['brand_response_rate'] = brand_row['response_rate'] or 15
                    context['days_since_action'] = brand_row['days_ago'] or 2
                else:
                    context['brand_name'] = 'your saved brand'
                    context['brand_response_rate'] = 15
                    context['days_since_action'] = 2
            except Exception as e:
                print(f"[BRAND EMAIL] Error fetching brand context: {e}")
                context['brand_name'] = 'your saved brand'
                context['brand_response_rate'] = 15
                context['days_since_action'] = 2

            context['reply_url'] = f"{FRONTEND_URL}/creator/dashboard/pr-pipeline?action=replied"
            context['followup_url'] = f"{FRONTEND_URL}/creator/dashboard/pr-pipeline?action=followup"
            context['remove_url'] = f"{FRONTEND_URL}/creator/dashboard/pr-pipeline?action=remove"
            context['cta_url'] = f"{FRONTEND_URL}/creator/dashboard/pr-pipeline"
        elif template_slug == 'weekly_digest':
            context = {**context, **build_weekly_digest_context(creator_id)}
        elif template_slug.startswith('reengagement_'):
            # Calculate actual days/weeks inactive
            try:
                cursor.execute("""
                    SELECT u.last_login FROM creators c
                    JOIN users u ON c.user_id = u.id
                    WHERE c.id = %s
                """, (creator_id,))
                row = cursor.fetchone()
                if row and row['last_login']:
                    days_inactive = (date.today() - row['last_login'].date()).days
                else:
                    days_inactive = 14  # Default fallback
                weeks_inactive = max(1, days_inactive // 7)
                context['days_inactive'] = days_inactive
                context['weeks_inactive'] = weeks_inactive
            except Exception:
                context['days_inactive'] = 14
                context['weeks_inactive'] = 2

            # Get top 3 new brands matching creator's niche for reengagement email
            try:
                matching_categories = _get_matching_categories(creator)

                if matching_categories:
                    placeholders = ','.join(['%s'] * len(matching_categories))
                    cursor.execute(f"""
                        SELECT brand_name AS name, category
                        FROM pr_brands
                        WHERE LOWER(category) IN ({placeholders})
                          AND status = 'published'
                          AND accepting_pr = true
                        ORDER BY created_at DESC
                        LIMIT 3
                    """, tuple(matching_categories))
                    brands = cursor.fetchall()

                    # Fallback if no niche matches
                    if not brands:
                        cursor.execute("""
                            SELECT brand_name AS name, category
                            FROM pr_brands
                            WHERE status = 'published'
                              AND accepting_pr = true
                            ORDER BY created_at DESC
                            LIMIT 3
                        """)
                        brands = cursor.fetchall()
                else:
                    cursor.execute("""
                        SELECT brand_name AS name, category
                        FROM pr_brands
                        WHERE status = 'published'
                          AND accepting_pr = true
                        ORDER BY created_at DESC
                        LIMIT 3
                    """)
                    brands = cursor.fetchall()

                # Calculate personalized fit indication based on niche match
                def get_fit_label(brand_category: str) -> str:
                    if (brand_category or '').lower() in matching_categories:
                        return "Great fit"
                    return "New opportunity"

                context['top_new_brands'] = [
                    {
                        'name': b['name'],
                        'category': b.get('category') or 'Lifestyle',
                        'fit_label': get_fit_label(b.get('category') or '')
                    } for b in brands
                ] if brands else [
                    {'name': 'Explore new brands', 'category': 'Various', 'fit_label': 'Browse the directory'}
                ]
            except Exception as e:
                print(f"[REENGAGEMENT] Error fetching top brands: {e}")
                context['top_new_brands'] = []
            context['cta_url'] = f"{FRONTEND_URL}/creator/dashboard/pr-ready"

        return context
    finally:
        conn.close()


def build_weekly_digest_context(creator_id: int) -> Dict[str, Any]:
    """
    Build context for the weekly digest email using AI Manager data.
    Uses Reply Chance score and pending manager plans.
    """
    # Import pr_ready for score calculation
    try:
        from services.pr_ready import compute_pr_ready_score
    except ImportError:
        compute_pr_ready_score = None

    conn = get_db_connection()
    try:
        cursor = conn.cursor(cursor_factory=RealDictCursor)

        # Get full creator data including user_id for scrape lookup
        cursor.execute("""
            SELECT c.id, c.username, c.user_id, u.email, u.first_name,
                   c.daily_unlocks_used, c.unlocks_remaining,
                   c.pitches_sent_this_week, c.subscription_tier,
                   c.bio, c.image_profile, c.niche, c.creator_niches,
                   c.kit_published, c.total_pitches_sent, c.total_replies_received,
                   c.followers_count, c.creator_followers, c.engagement_rate,
                   c.avg_engagement_rate, c.weekly_digest_week_number
            FROM creators c
            JOIN users u ON c.user_id = u.id
            WHERE c.id = %s
        """, (creator_id,))
        creator = cursor.fetchone()

        if not creator:
            return {}

        user_id = creator.get('user_id')
        subscription_tier = creator.get('subscription_tier') or 'free'
        is_pro = subscription_tier in ('pro', 'elite')

        # === UNLOCKS: Calculate correctly ===
        unlocks_remaining = creator.get('unlocks_remaining')
        if is_pro:
            unlocks_used = 0
            unlocks_quota = '∞'
        elif unlocks_remaining is not None:
            unlocks_used = max(0, 3 - unlocks_remaining)
            unlocks_quota = 3
        else:
            # Default for users without unlocks_remaining set
            unlocks_used = creator.get('daily_unlocks_used') or 0
            unlocks_quota = 3

        # === REPLY CHANCE SCORE: Use AI Manager score ===
        reply_chance = 0
        pending_plans = []
        score_delta = 0

        if compute_pr_ready_score and user_id:
            try:
                # Get creator_profile_data (scrape) - same as dashboard
                cursor.execute("""
                    SELECT * FROM creator_profile_data WHERE user_id = %s
                """, (user_id,))
                scrape = cursor.fetchone()

                # Debug: log scrape data found
                if scrape:
                    print(f"[WEEKLY DIGEST] Scrape found: handle={scrape.get('handle')}, "
                          f"has_bio={bool(scrape.get('raw_bio'))}, "
                          f"posts={len(scrape.get('recent_posts') or [])}")
                else:
                    print(f"[WEEKLY DIGEST] No scrape data for user_id={user_id}")

                # Get kit status - same query pattern as check_media_kit_complete
                cursor.execute("""
                    SELECT kit_published as is_published,
                           (SELECT COUNT(*) FROM portfolio_posts WHERE creator_id = %s) as post_count
                    FROM creators WHERE id = %s
                """, (creator_id, creator_id))
                kit_row = cursor.fetchone()
                kit_status = {
                    'is_published': kit_row.get('is_published') if kit_row else False,
                    'post_count': kit_row.get('post_count') or 0 if kit_row else 0
                }
                print(f"[WEEKLY DIGEST] Kit status: published={kit_status['is_published']}, posts={kit_status['post_count']}")

                # Compute PR-Ready score
                report = compute_pr_ready_score(
                    scrape=dict(scrape) if scrape else {},
                    kit_status=kit_status,
                    is_pro=is_pro,
                    creator_bio=creator.get('bio'),
                    creator_profile=dict(creator)
                )

                reply_chance = report.get('score') or 0
                score_delta = report.get('projected_gain') or 0
                print(f"[WEEKLY DIGEST] PR-Ready score: {reply_chance}, projected_gain: {score_delta}, status: {report.get('status')}")

                # Get pending plans - ONLY items where done=False
                fixes = report.get('fixes') or []
                not_done_fixes = [f for f in fixes if not f.get('done', False)]
                pending_plans = [
                    {'number': i + 1, 'title': f.get('title', '')}
                    for i, f in enumerate(not_done_fixes[:3])  # Top 3 pending items
                ]
                # Debug: show which fixes are done vs not done
                for f in fixes:
                    print(f"[WEEKLY DIGEST] Fix: {f.get('id')} - {f.get('title')[:30]}... done={f.get('done')}")
                print(f"[WEEKLY DIGEST] Pending plans to show: {pending_plans}")
            except Exception as e:
                print(f"[WEEKLY DIGEST] Error computing PR-Ready score: {e}")
                reply_chance = _calculate_creator_progress_score(creator)

        # Fallback if pr_ready not available
        if not reply_chance:
            reply_chance = _calculate_creator_progress_score(creator)

        # === NEW BRANDS: Match to creator's niche ===
        # Parse niche - can be a string or JSON array
        raw_niche = creator.get('niche') or ''
        creator_niches = creator.get('creator_niches') or []

        # Parse raw_niche if it's a JSON array string
        if isinstance(raw_niche, str) and raw_niche.startswith('['):
            try:
                raw_niche = json.loads(raw_niche)
            except:
                pass

        # Normalize to list
        if isinstance(raw_niche, list):
            niche_list = raw_niche
        elif raw_niche:
            niche_list = [raw_niche]
        else:
            niche_list = []

        # Parse creator_niches if it's a JSON string
        if isinstance(creator_niches, str):
            try:
                creator_niches = json.loads(creator_niches)
            except:
                creator_niches = []

        # Combine all niches
        all_niches_raw = niche_list + (creator_niches if isinstance(creator_niches, list) else [])
        all_niches = [n.strip().lower() for n in all_niches_raw if n and isinstance(n, str)]

        # Get clean primary niche for display (first niche, capitalized)
        primary_niche_display = all_niches[0].title() if all_niches else 'content'

        # === NEW BRANDS: Use the For You matching logic ===
        # This ensures email recommendations match what creators see in the app
        try:
            new_brands = get_for_you_brands_for_email(creator_id, cursor, limit=3)
            print(f"[WEEKLY DIGEST] For You brands for email: {[b['name'] for b in new_brands]}")
        except Exception as e:
            print(f"[WEEKLY DIGEST] Error getting For You brands: {e}")
            new_brands = []

        # Build context
        context = {
            'first_name': creator.get('first_name') or creator.get('username') or 'there',
            'current_score': reply_chance,
            'score_label': 'Reply Chance',
            'score_delta': score_delta,
            'unlocks_used': unlocks_used,
            'unlocks_quota': unlocks_quota,
            'replies_count': creator.get('total_replies_received') or 0,
            # Use pending plans from AI Manager, or fallback to static theme
            'weekly_theme_title': pending_plans[0]['title'] if pending_plans else 'Optimize your profile',
            'weekly_theme_body': f"Your manager found {len(pending_plans)} improvements. Start with #{pending_plans[0]['number']}." if pending_plans else 'Visit your AI Manager for personalized tips.',
            'pending_plans': pending_plans,
            # Use For You brands - already formatted with accurate match reasons
            'new_brands': new_brands if new_brands else [
                {'name': 'Explore brands', 'category': 'Various', 'reason': 'Browse the directory'}
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
    """Trigger email when user hits 3/3 unlocks (unlocks_remaining becomes 0)."""
    if not is_feature_enabled('email_maximizer_pro_v2'):
        return False, "Feature disabled"

    conn = get_db_connection()
    try:
        cursor = conn.cursor(cursor_factory=RealDictCursor)

        # Check quota - supports both unlocks_remaining (new) and daily_unlocks_used (legacy)
        cursor.execute("""
            SELECT c.daily_unlocks_used, c.unlocks_remaining, c.subscription_tier, u.email
            FROM creators c
            JOIN users u ON c.user_id = u.id
            WHERE c.id = %s
        """, (creator_id,))
        result = cursor.fetchone()

        if not result:
            return False, "Creator not found"

        if result.get('subscription_tier') in ('pro', 'elite'):
            return False, "Pro user - no quota"

        # Check quota hit: unlocks_remaining == 0 (new system) OR daily_unlocks_used >= 3 (legacy)
        unlocks_remaining = result.get('unlocks_remaining')
        daily_unlocks_used = result.get('daily_unlocks_used') or 0

        quota_hit = (unlocks_remaining is not None and unlocks_remaining == 0) or daily_unlocks_used >= 3
        if not quota_hit:
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
