"""
Admin API Routes for Creator Management (scan/search)
"""

from flask import Blueprint, request, jsonify, session
from functools import wraps
from psycopg2.extras import RealDictCursor
import psycopg2
import os
import sys
import json
import html as html_lib
import time
from datetime import date, datetime
from decimal import Decimal

# Add parent directory for imports
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from services.unlock_quota import CREDIT_USAGE_SQL


# Create Blueprint
admin_creators_bp = Blueprint('admin_creators', __name__, url_prefix='/api/admin')


# ============================================================================
# AUTHENTICATION DECORATOR
# ============================================================================
def admin_required(f):
    """
    Decorator to require admin authentication.
    Accepts X-Admin-Token header with valid token (preferred),
    or falls back to session-based auth.
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


def get_db_connection():
    """Get database connection"""
    return psycopg2.connect(os.getenv('DATABASE_URL'), cursor_factory=RealDictCursor)


def _parse_json_maybe(value, default):
    if value is None:
        return default
    if isinstance(value, (list, dict)):
        return value
    if isinstance(value, str):
        try:
            return json.loads(value)
        except Exception:
            return default
    return default


def _serialize_value(value):
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, Decimal):
        return float(value)
    return value


def _serialize_row(row):
    if not row:
        return row
    return {key: _serialize_value(val) for key, val in row.items()}


DEFAULT_RESUME_SINCE = date(2026, 8, 12)
DEFAULT_RESUME_UNTIL = date(2026, 8, 13)
MAX_RESUME_ONBOARDING_SEND = 200
RESUME_ONBOARDING_SUBJECT = "Your Newcollab account is ready. Log in to finish"


def _parse_iso_date(value, default):
    if not value:
        return default
    try:
        return datetime.strptime(str(value)[:10], '%Y-%m-%d').date()
    except (TypeError, ValueError):
        return default


RESUME_ONBOARDING_APP_URL = 'https://app.newcollab.co'


def _resume_onboarding_login_urls():
    # Outbound email to real users. Never use FRONTEND_URL from local .env (localhost).
    return (
        f'{RESUME_ONBOARDING_APP_URL}/login',
        f'{RESUME_ONBOARDING_APP_URL}/forgot-password',
    )


def _is_incomplete_onboarding_sql():
    """Match login: onboarding is incomplete without username or niche."""
    return """
        (
            NULLIF(BTRIM(COALESCE(c.username::text, '')), '') IS NULL
            OR c.niche IS NULL
            OR BTRIM(c.niche::text) IN ('', '[]', 'null', 'None', '""')
        )
    """


def _fetch_resume_onboarding_cohort(cursor, since_date, until_date, include_sent=False):
    params = [since_date, until_date]
    already_sent_sql = ""
    if not include_sent:
        already_sent_sql = """
              AND (
                c.last_reminder_sent IS NULL
                OR c.last_reminder_sent < NOW() - INTERVAL '7 days'
              )
        """

    cursor.execute(f"""
        SELECT
            c.id AS creator_id,
            u.id AS user_id,
            u.email,
            u.first_name,
            c.username,
            u.created_at AS signup_date,
            c.last_reminder_sent,
            (NULLIF(BTRIM(COALESCE(c.image_profile, '')), '') IS NOT NULL) AS has_image
        FROM creators c
        JOIN users u ON c.user_id = u.id
        WHERE u.email IS NOT NULL
          AND BTRIM(u.email) <> ''
          AND u.unsubscribed_at IS NULL
          AND u.created_at >= %s::date
          AND u.created_at < (%s::date + INTERVAL '1 day')
          AND {_is_incomplete_onboarding_sql()}
          {already_sent_sql}
        ORDER BY u.created_at DESC
        LIMIT {MAX_RESUME_ONBOARDING_SEND}
    """, tuple(params))
    return [_serialize_row(row) for row in cursor.fetchall()]


def _resume_onboarding_email_context(recipient):
    login_url, forgot_url = _resume_onboarding_login_urls()
    first_name = (recipient.get('first_name') or '').strip()
    greeting = f'Hi {html_lib.escape(first_name)}' if first_name else 'Hi'
    return {
        'user_id': recipient.get('user_id'),
        'preheader': 'Log in to resume where you were.',
        'subject': RESUME_ONBOARDING_SUBJECT,
        'action_url': login_url,
        'action_text': 'Log in to finish setup',
        'secondary_action_url': forgot_url,
        'secondary_action_text': 'Forgot your password?',
        'message': (
            f'<p>{greeting},</p>'
            f'<p>You started setting up your creator account on Newcollab. Log in to resume where you were.</p>'
        ),
    }


def _build_where_clause():
    q = request.args.get('q', '').strip()
    niche = request.args.get('niche', '').strip() or None
    region = request.args.get('region', '').strip() or None
    tier = request.args.get('tier', '').strip() or None
    verified_raw = request.args.get('verified', '').strip().lower()
    kit_raw = request.args.get('kit', '').strip().lower()

    verified = None
    if verified_raw in ('true', 'false'):
        verified = (verified_raw == 'true')

    kit = None
    if kit_raw in ('true', 'false'):
        kit = (kit_raw == 'true')

    where_clauses = ["1=1"]
    params = []

    # Optional filter: unsubscribed=true shows only unsubscribed, false hides them, default shows all
    unsub_raw = request.args.get('unsubscribed', '').strip().lower()
    if unsub_raw == 'true':
        where_clauses.append("u.unsubscribed_at IS NOT NULL")
    elif unsub_raw == 'false':
        where_clauses.append("u.unsubscribed_at IS NULL")
    # default (empty): show everyone

    if q:
        where_clauses.append("(u.email ILIKE %s OR u.first_name ILIKE %s OR c.username ILIKE %s)")
        like = f'%{q}%'
        params.extend([like, like, like])

    if niche:
        where_clauses.append("COALESCE(c.niche, '') ILIKE %s")
        params.append(f'%{niche}%')

    if region:
        where_clauses.append("COALESCE(c.regions::text, '') ILIKE %s")
        params.append(f'%{region}%')

    if tier:
        where_clauses.append("COALESCE(c.subscription_tier, 'free') = %s")
        params.append(tier)

    if verified is not None:
        where_clauses.append("COALESCE(u.is_verified, false) = %s")
        params.append(verified)

    if kit is not None:
        where_clauses.append("COALESCE(c.has_media_kit, false) = %s")
        params.append(kit)

    return " AND ".join(where_clauses), params


def _resolve_sort():
    sort = request.args.get('sort', 'signup').strip().lower()
    order = request.args.get('order', 'desc').strip().lower()

    # Support pitches / unlocks / credits — same metric after the apply pivot
    if sort not in ('signup', 'pitches', 'unlocks', 'credits', 'followers'):
        sort = 'signup'
    if order not in ('asc', 'desc'):
        order = 'desc'

    direction = 'ASC' if order == 'asc' else 'DESC'
    nulls = 'NULLS LAST' if order == 'desc' else 'NULLS FIRST'

    if sort in ('pitches', 'unlocks', 'credits'):
        return f"unlocks_count {direction} {nulls}, u.created_at DESC"
    if sort == 'followers':
        return f"c.followers_count {direction} {nulls}, u.created_at DESC"
    return f"u.created_at {direction} {nulls}"


# Credits used = unlocks (same 3-free quota). Include Brand PR applies.
UNLOCK_STATS_SQL = f"""
    (
        SELECT COUNT(*)::int
        FROM ({CREDIT_USAGE_SQL}) credits
        WHERE credits.creator_id = c.id
    ) AS unlocks_count,
    (
        SELECT COUNT(*)::int
        FROM ({CREDIT_USAGE_SQL}) credits
        WHERE credits.creator_id = c.id
          AND credits.used_at >= DATE_TRUNC('week', NOW())
    ) AS unlocks_this_week
"""


@admin_creators_bp.route('/creators/resume-onboarding/preview', methods=['GET'])
@admin_required
def preview_resume_onboarding():
    """Preview incomplete onboarding creators for a one-shot resume invite."""
    since_date = _parse_iso_date(request.args.get('since_date'), DEFAULT_RESUME_SINCE)
    until_date = _parse_iso_date(request.args.get('until_date'), DEFAULT_RESUME_UNTIL)
    include_sent = request.args.get('include_sent', '').strip().lower() == 'true'
    if until_date < since_date:
        since_date, until_date = until_date, since_date

    try:
        conn = get_db_connection()
        cursor = conn.cursor(cursor_factory=RealDictCursor)
        recipients = _fetch_resume_onboarding_cohort(
            cursor, since_date, until_date, include_sent=include_sent
        )
        conn.close()
        return jsonify({
            'count': len(recipients),
            'since_date': since_date.isoformat(),
            'until_date': until_date.isoformat(),
            'login_url': _resume_onboarding_login_urls()[0],
            'recipients': recipients,
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@admin_creators_bp.route('/creators/resume-onboarding/send', methods=['POST'])
@admin_required
def send_resume_onboarding():
    """
    One-shot resume-onboarding email for incomplete creator accounts.
    Defaults to the Aug 12–13 2026 signup window. Pass dry_run=true to preview.
    """
    data = request.get_json(silent=True) or {}
    since_date = _parse_iso_date(data.get('since_date'), DEFAULT_RESUME_SINCE)
    until_date = _parse_iso_date(data.get('until_date'), DEFAULT_RESUME_UNTIL)
    include_sent = bool(data.get('force') or data.get('include_sent'))
    dry_run = bool(data.get('dry_run', False))
    test_email = (data.get('test_email') or '').strip() or None
    if until_date < since_date:
        since_date, until_date = until_date, since_date

    try:
        from email_cron_routes import send_template_email

        if test_email:
            sample = {
                'user_id': None,
                'first_name': 'Nyakallo',
            }
            success, error = send_template_email(
                to_email=test_email,
                template_name='resume_onboarding.html',
                subject=RESUME_ONBOARDING_SUBJECT,
                context=_resume_onboarding_email_context(sample),
            )
            if not success:
                return jsonify({'error': error or 'Failed to send test email'}), 500
            return jsonify({
                'success': True,
                'test': True,
                'sent_to': test_email,
            })

        conn = get_db_connection()
        cursor = conn.cursor(cursor_factory=RealDictCursor)
        recipients = _fetch_resume_onboarding_cohort(
            cursor, since_date, until_date, include_sent=include_sent
        )

        if dry_run:
            conn.close()
            return jsonify({
                'success': True,
                'dry_run': True,
                'count': len(recipients),
                'since_date': since_date.isoformat(),
                'until_date': until_date.isoformat(),
                'recipients': recipients,
            })

        sent = 0
        failed = []
        for recipient in recipients:
            email = (recipient.get('email') or '').strip()
            if not email:
                failed.append({'email': None, 'error': 'Missing email'})
                continue
            success, error = send_template_email(
                to_email=email,
                template_name='resume_onboarding.html',
                subject=RESUME_ONBOARDING_SUBJECT,
                context=_resume_onboarding_email_context(recipient),
            )
            if success:
                cursor.execute("""
                    UPDATE creators
                    SET last_reminder_sent = NOW(),
                        last_any_email_sent = NOW()
                    WHERE id = %s
                """, (recipient['creator_id'],))
                conn.commit()
                sent += 1
            else:
                failed.append({'email': email, 'error': error})
            time.sleep(0.4)

        conn.close()
        return jsonify({
            'success': True,
            'dry_run': False,
            'count': len(recipients),
            'sent': sent,
            'failed': len(failed),
            'errors': failed,
            'since_date': since_date.isoformat(),
            'until_date': until_date.isoformat(),
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@admin_creators_bp.route('/creators', methods=['GET'])
@admin_required
def list_creators():
    """
    Scan/search all creators for admin workflows.
    """
    try:
        where_sql, params = _build_where_clause()
        order_sql = _resolve_sort()

        limit = int(request.args.get('limit', 25))
        offset = int(request.args.get('offset', 0))
        limit = max(1, min(limit, 200))
        offset = max(0, offset)

        count_sql = f"""
            SELECT COUNT(DISTINCT c.id) AS total
            FROM creators c
            JOIN users u ON c.user_id = u.id
            WHERE {where_sql}
        """

        stats_sql = f"""
            SELECT
                COUNT(DISTINCT c.id) AS total,
                COUNT(DISTINCT c.id) FILTER (WHERE COALESCE(u.is_verified, false)) AS verified,
                COUNT(DISTINCT c.id) FILTER (WHERE COALESCE(c.has_media_kit, false)) AS with_kit,
                COUNT(DISTINCT c.id) FILTER (
                    WHERE EXISTS (
                        SELECT 1 FROM ({CREDIT_USAGE_SQL}) credits
                        WHERE credits.creator_id = c.id
                    )
                ) AS unlocked
            FROM creators c
            JOIN users u ON c.user_id = u.id
            WHERE {where_sql}
        """

        select_sql = f"""
            SELECT
                c.id AS creator_id,
                u.id AS user_id,
                u.email,
                u.first_name,
                u.is_verified,
                c.username,
                c.image_profile,
                c.followers_count,
                c.platforms,
                c.social_links,
                c.niche,
                c.regions,
                COALESCE(c.subscription_tier, 'free') AS tier,
                {UNLOCK_STATS_SQL},
                COALESCE(c.brands_saved_count, 0) AS brands_saved,
                COALESCE(c.has_media_kit, false) AS has_media_kit,
                COALESCE(c.kit_published, false) AS kit_published,
                c.kit_published_at,
                c.kit_slug,
                c.media_kit_url,
                u.created_at AS signup_date
            FROM creators c
            JOIN users u ON c.user_id = u.id
            WHERE {where_sql}
            ORDER BY {order_sql}
            LIMIT %s
            OFFSET %s
        """

        conn = get_db_connection()
        cursor = conn.cursor(cursor_factory=RealDictCursor)

        cursor.execute(count_sql, tuple(params))
        total = cursor.fetchone()['total']

        cursor.execute(stats_sql, tuple(params))
        stats_row = cursor.fetchone()

        cursor.execute(select_sql, tuple(params + [limit, offset]))
        creators = cursor.fetchall()

        for c in creators:
            c['regions'] = _parse_json_maybe(c.get('regions'), [])
            c['platforms'] = _parse_json_maybe(c.get('platforms'), [])
            c['social_links'] = _parse_json_maybe(c.get('social_links'), {})

        conn.close()

        return jsonify({
            'creators': [_serialize_row(c) for c in creators],
            'pagination': {
                'total': total,
                'limit': limit,
                'offset': offset,
            },
            'stats': _serialize_row(stats_row),
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@admin_creators_bp.route('/creators/<int:creator_id>', methods=['GET'])
@admin_required
def get_creator_details(creator_id):
    """
    Detailed creator view for the admin drawer.
    """
    try:
        conn = get_db_connection()
        cursor = conn.cursor(cursor_factory=RealDictCursor)
        cursor.execute(f"""
            SELECT
                c.id AS creator_id,
                u.id AS user_id,
                u.email,
                u.first_name,
                COALESCE(u.is_verified, false) AS is_verified,
                c.username,
                c.image_profile,
                c.bio,
                c.followers_count,
                c.engagement_rate,
                c.avg_engagement_rate,
                c.total_posts,
                c.total_views,
                c.platforms,
                c.social_links,
                c.niche,
                c.regions,
                c.primary_age_range,
                c.top_locations,
                COALESCE(c.subscription_tier, 'free') AS tier,
                {UNLOCK_STATS_SQL},
                COALESCE(c.brands_saved_count, 0) AS brands_saved,
                COALESCE(c.has_media_kit, false) AS has_media_kit,
                COALESCE(c.kit_published, false) AS kit_published,
                c.kit_published_at,
                c.kit_slug,
                c.media_kit_url,
                c.last_pitch_at,
                c.daily_unlocks_used,
                c.last_unlock_date,
                u.created_at AS signup_date,
                (
                    SELECT COUNT(*)::int FROM portfolio_posts pp
                    WHERE pp.creator_id = c.id
                ) AS portfolio_post_count,
                (
                    SELECT COUNT(*)::int FROM creator_pipeline cp
                    WHERE cp.creator_id = c.id
                ) AS pipeline_saves,
                (
                    SELECT MAX(credits.used_at) FROM ({CREDIT_USAGE_SQL}) credits
                    WHERE credits.creator_id = c.id
                ) AS last_unlocked_at
            FROM creators c
            JOIN users u ON c.user_id = u.id
            WHERE c.id = %s
        """, (creator_id,))

        creator = cursor.fetchone()
        conn.close()

        if not creator:
            return jsonify({'error': 'Creator not found'}), 404

        creator['platforms'] = _parse_json_maybe(creator.get('platforms'), [])
        creator['social_links'] = _parse_json_maybe(creator.get('social_links'), {})
        creator['regions'] = _parse_json_maybe(creator.get('regions'), [])
        creator['top_locations'] = _parse_json_maybe(creator.get('top_locations'), [])

        return jsonify({'creator': _serialize_row(creator)})
    except Exception as e:
        return jsonify({'error': str(e)}), 500
