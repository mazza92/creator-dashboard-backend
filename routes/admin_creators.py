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
import re
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


APPROVAL_STATUSES = ('pending', 'approved', 'rejected', 'pro_approved')
LOW_FOLLOWER_FLAG = 500


def _public_review_posts(raw, limit=8):
    """Strip OAuth/token fields — only what an admin needs to eyeball quality."""
    posts = _parse_json_maybe(raw, [])
    if not isinstance(posts, list):
        return []
    out = []
    for p in posts:
        if not isinstance(p, dict):
            continue
        likes = p.get('likes')
        if likes is None:
            likes = p.get('like_count')
        views = p.get('views')
        if views is None:
            views = p.get('view_count')
        comments = p.get('comments')
        if comments is None:
            comments = p.get('comment_count')
        out.append({
            'id': p.get('id'),
            'title': p.get('title') or p.get('caption') or '',
            'url': p.get('share_url') or p.get('post_url') or p.get('url') or '',
            'thumbnail_url': (
                p.get('cover_image_url')
                or p.get('thumbnail_url')
                or p.get('thumb')
                or ''
            ),
            'likes': likes,
            'views': views,
            'comments': comments,
        })
        if len(out) >= limit:
            break
    return out


def _primary_social(row):
    handle = (row.get('social_handle') or '').strip().lstrip('@')
    platform = (row.get('social_platform') or '').strip().lower()
    links = _parse_json_maybe(row.get('social_links'), [])
    if not handle and isinstance(links, list):
        for item in links:
            if not isinstance(item, dict):
                continue
            h = str(item.get('handle') or '').strip().lstrip('@')
            if h:
                handle = h
                platform = (item.get('platform') or platform or '').strip().lower()
                break
    if not handle:
        handle = (row.get('username') or '').strip().lstrip('@')
    return platform or None, handle or None


def _review_flags(row):
    """Informational flags only — never auto-reject. Micro creators are welcome."""
    flags = []
    bio = (row.get('bio') or '').strip()
    niche_raw = row.get('niche')
    niche_text = ''
    if isinstance(niche_raw, list):
        niche_text = ','.join(str(n) for n in niche_raw if n)
    elif niche_raw is not None:
        niche_text = str(niche_raw).strip()
    if niche_text.lower() in ('', '[]', 'null', 'none', '""'):
        flags.append('missing_niche')
    if not bio:
        flags.append('missing_bio')

    platform, handle = _primary_social(row)
    if not handle:
        flags.append('missing_handle')
    if not platform:
        flags.append('missing_platform')

    followers = row.get('followers_count') or row.get('social_follower_count') or 0
    try:
        followers = int(followers)
    except (TypeError, ValueError):
        followers = 0
    if followers < LOW_FOLLOWER_FLAG:
        flags.append('low_followers')

    tier = str(row.get('tier') or row.get('subscription_tier') or 'free').lower()
    status = row.get('approval_status')
    if tier in ('pro', 'elite') and status == 'pending':
        flags.append('pro_pending')

    username = (row.get('username') or '').strip()
    if not username or 'missing_niche' in flags:
        flags.append('incomplete_profile')
    return flags


def _ensure_review_columns(cursor):
    """Waitlist review can show post thumbs if the column exists; never fail if it does not."""
    cursor.execute('ALTER TABLE creators ADD COLUMN IF NOT EXISTS social_oauth_videos JSONB')


def _admin_actor_id(cursor):
    uid = session.get('user_id')
    if uid:
        return uid
    cursor.execute("SELECT id FROM users WHERE LOWER(email) = 'team@newcollab.co' LIMIT 1")
    row = cursor.fetchone()
    return row['id'] if row else None


def _approval_snapshot(cursor):
    cursor.execute("""
        SELECT
            COUNT(*) FILTER (WHERE approval_status = 'pending')::int AS pending,
            COUNT(*) FILTER (
                WHERE approval_status IN ('approved', 'pro_approved')
                  AND approved_at >= CURRENT_DATE
            )::int AS approved_today,
            COUNT(*) FILTER (
                WHERE approval_status = 'rejected'
                  AND rejected_at >= CURRENT_DATE
            )::int AS rejected_today
        FROM creators
    """)
    return _serialize_row(cursor.fetchone()) or {
        'pending': 0, 'approved_today': 0, 'rejected_today': 0,
    }


def _enrich_review_row(row):
    row = dict(row)
    row['regions'] = _parse_json_maybe(row.get('regions'), [])
    row['platforms'] = _parse_json_maybe(row.get('platforms'), [])
    row['social_links'] = _parse_json_maybe(row.get('social_links'), [])
    row['recent_posts'] = _public_review_posts(row.pop('social_oauth_videos', None))
    platform, handle = _primary_social(row)
    row['display_platform'] = platform
    row['display_handle'] = handle
    row['flags'] = _review_flags(row)
    row['profile_ready'] = 'incomplete_profile' not in row['flags']
    followers = row.get('social_follower_count') or row.get('followers_count') or 0
    try:
        row['review_followers'] = int(followers)
    except (TypeError, ValueError):
        row['review_followers'] = 0
    return _serialize_row(row)


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


_HANDLE_URL_RE = re.compile(
    r'(?:https?://)?(?:www\.)?(?:instagram\.com|tiktok\.com|x\.com|twitter\.com|youtube\.com)/@?([^/?#]+)',
    re.I,
)

# Quoted JSON tokens so "US" does not match AUSTRALIA.
_REGION_ALIASES = {
    'us': ['US', 'USA', 'United States'],
    'usa': ['US', 'USA', 'United States'],
    'united states': ['US', 'USA', 'United States'],
    'uk': ['UK', 'GB', 'United Kingdom'],
    'gb': ['UK', 'GB', 'United Kingdom'],
    'united kingdom': ['UK', 'GB', 'United Kingdom'],
    'canada': ['Canada', 'CA'],
    'ca': ['Canada', 'CA'],
    'australia': ['AU', 'Australia'],
    'au': ['AU', 'Australia'],
    'europe': ['Europe', 'EU'],
    'eu': ['Europe', 'EU'],
    'latam': ['LATAM', 'Latin America'],
    'latin america': ['LATAM', 'Latin America'],
    'mena': ['MENA', 'Middle East', 'Middle East & Africa'],
    'asia': ['Asia', 'Asia Pacific', 'ASIA'],
    'asia pacific': ['Asia', 'Asia Pacific', 'ASIA'],
    'global': ['Global', 'Worldwide', 'GLOBAL', 'WW'],
    'worldwide': ['Global', 'Worldwide', 'GLOBAL', 'WW'],
}

_HANDLE_SQL = "LOWER(BTRIM(BOTH '@' FROM COALESCE({col}, '')))"


def _arg_get(src, key, default=''):
    val = src.get(key, default) if src is not None else default
    if val is None:
        return default
    return str(val).strip() if default == '' or isinstance(val, str) else val


def _split_csv(raw):
    if not raw:
        return []
    return [part.strip() for part in str(raw).split(',') if part.strip()]


def _safe_like_fragment(value):
    """Strip LIKE wildcards so admin search is literal."""
    return re.sub(r'[%_\\]+', '', str(value or ''))


def normalize_search_token(raw):
    """Turn @handle, profile URL, or pasted email into a single lookup token."""
    q = (raw or '').strip()
    if not q:
        return ''
    if ' ' not in q and '/' not in q and '@' in q and '.' in q.rsplit('@', 1)[-1]:
        return q
    url_match = _HANDLE_URL_RE.search(q)
    if url_match:
        return url_match.group(1).lstrip('@').strip()
    if q.startswith('@'):
        return q[1:].strip()
    return q


def _int_arg(src, key):
    raw = _arg_get(src, key, '')
    if raw == '':
        return None
    try:
        return max(0, int(raw))
    except (TypeError, ValueError):
        return None


def _niche_needles(raw):
    needles = []
    seen = set()
    try:
        from brand_categories import CATEGORY_LABELS, normalize_category, raw_values_for_canonical
    except Exception:
        normalize_category = None
        raw_values_for_canonical = None
        CATEGORY_LABELS = {}

    for item in _split_csv(raw):
        candidates = [item]
        if normalize_category:
            slug = normalize_category(item)
            if slug and slug != 'other':
                candidates.extend(raw_values_for_canonical(slug) or [slug])
                label = CATEGORY_LABELS.get(slug)
                if label:
                    candidates.append(label)
            elif slug == 'other':
                candidates.append(item)
        for cand in candidates:
            key = str(cand).strip()
            if not key:
                continue
            low = key.lower()
            if low in seen:
                continue
            seen.add(low)
            needles.append(key)
    return needles


def _region_needles(raw):
    needles = []
    seen = set()
    for item in _split_csv(raw):
        aliases = _REGION_ALIASES.get(item.strip().lower(), [item.strip()])
        for alias in aliases:
            key = alias.strip()
            if not key:
                continue
            low = key.lower()
            if low in seen:
                continue
            seen.add(low)
            needles.append(key)
    return needles


def _search_match_sql(token):
    """Match email, name, username, social handle, kit slug, and social_links JSON."""
    safe = _safe_like_fragment(token)
    if not safe:
        return None, []
    exact = safe.lower()
    contains = f'%{safe}%'
    username_sql = _HANDLE_SQL.format(col='c.username')
    social_sql = _HANDLE_SQL.format(col='c.social_handle')
    sql = f"""(
        LOWER(u.email) = %s
        OR {username_sql} = %s
        OR {social_sql} = %s
        OR LOWER(COALESCE(c.kit_slug, '')) = %s
        OR u.email ILIKE %s
        OR COALESCE(u.first_name, '') ILIKE %s
        OR {username_sql} ILIKE %s
        OR {social_sql} ILIKE %s
        OR COALESCE(c.kit_slug, '') ILIKE %s
        OR COALESCE(c.social_links::text, '') ILIKE %s
    )"""
    params = [
        exact, exact, exact, exact,
        contains, contains, contains, contains, contains, contains,
    ]
    return sql, params


def _search_rank_sql(token):
    safe = _safe_like_fragment(token)
    if not safe:
        return '', []
    exact = safe.lower()
    prefix = f'{safe.lower()}%'
    username_sql = _HANDLE_SQL.format(col='c.username')
    social_sql = _HANDLE_SQL.format(col='c.social_handle')
    sql = f"""CASE
        WHEN LOWER(u.email) = %s THEN 0
        WHEN {username_sql} = %s THEN 0
        WHEN {social_sql} = %s THEN 0
        WHEN LOWER(COALESCE(c.kit_slug, '')) = %s THEN 1
        WHEN {username_sql} LIKE %s THEN 2
        WHEN {social_sql} LIKE %s THEN 2
        ELSE 3
    END"""
    return sql, [exact, exact, exact, exact, prefix, prefix]


def _build_where_clause(args=None):
    src = args if args is not None else request.args
    q = normalize_search_token(_arg_get(src, 'q'))
    niche = _arg_get(src, 'niche')
    region = _arg_get(src, 'region')
    tier = _arg_get(src, 'tier')
    platform = _arg_get(src, 'platform').lower()
    verified_raw = _arg_get(src, 'verified').lower()
    kit_raw = _arg_get(src, 'kit').lower()

    verified = None
    if verified_raw in ('true', 'false'):
        verified = (verified_raw == 'true')

    kit = None
    if kit_raw in ('true', 'false'):
        kit = (kit_raw == 'true')

    where_clauses = ["1=1"]
    params = []

    unsub_raw = _arg_get(src, 'unsubscribed').lower()
    if unsub_raw == 'true':
        where_clauses.append("u.unsubscribed_at IS NOT NULL")
    elif unsub_raw == 'false':
        where_clauses.append("u.unsubscribed_at IS NULL")

    search_sql, search_params = _search_match_sql(q)
    if search_sql:
        where_clauses.append(search_sql)
        params.extend(search_params)

    niche_needles = _niche_needles(niche)
    if niche_needles:
        niche_parts = []
        for needle in niche_needles:
            niche_parts.append("LOWER(COALESCE(c.niche::text, '')) LIKE %s")
            params.append(f"%{_safe_like_fragment(needle).lower()}%")
        where_clauses.append(f"({' OR '.join(niche_parts)})")

    region_needles = _region_needles(region)
    if region_needles:
        region_parts = []
        for needle in region_needles:
            # Quoted JSON token first; also allow a bare exact region string.
            region_parts.append("LOWER(COALESCE(c.regions::text, '')) LIKE %s")
            params.append(f'%"{_safe_like_fragment(needle).lower()}"%')
            region_parts.append("LOWER(BTRIM(COALESCE(c.regions::text, ''))) = %s")
            params.append(_safe_like_fragment(needle).lower())
        where_clauses.append(f"({' OR '.join(region_parts)})")

    if tier:
        where_clauses.append("COALESCE(c.subscription_tier, 'free') = %s")
        params.append(tier)

    if platform in ('instagram', 'tiktok', 'youtube'):
        like = f'%{platform}%'
        where_clauses.append(
            "("
            "LOWER(COALESCE(c.social_platform, '')) = %s "
            "OR LOWER(COALESCE(c.platforms::text, '')) LIKE %s "
            "OR LOWER(COALESCE(c.social_links::text, '')) LIKE %s"
            ")"
        )
        params.extend([platform, like, like])

    if verified is not None:
        where_clauses.append("COALESCE(u.is_verified, false) = %s")
        params.append(verified)

    if kit is not None:
        where_clauses.append("COALESCE(c.has_media_kit, false) = %s")
        params.append(kit)

    approval = _arg_get(src, 'approval_status').lower()
    if approval in APPROVAL_STATUSES:
        where_clauses.append("COALESCE(c.approval_status, 'approved') = %s")
        params.append(approval)

    min_followers = _int_arg(src, 'min_followers')
    max_followers = _int_arg(src, 'max_followers')
    follower_expr = "GREATEST(COALESCE(c.followers_count, 0), COALESCE(c.social_follower_count, 0))"
    if min_followers is not None:
        where_clauses.append(f"{follower_expr} >= %s")
        params.append(min_followers)
    if max_followers is not None:
        where_clauses.append(f"{follower_expr} <= %s")
        params.append(max_followers)

    return " AND ".join(where_clauses), params, q


def _resolve_sort(args=None, search_token=''):
    src = args if args is not None else request.args
    sort = _arg_get(src, 'sort', 'signup').lower()
    order = _arg_get(src, 'order', 'desc').lower()

    if sort not in ('signup', 'pitches', 'unlocks', 'credits', 'followers'):
        sort = 'signup'
    if order not in ('asc', 'desc'):
        order = 'desc'

    direction = 'ASC' if order == 'asc' else 'DESC'
    nulls = 'NULLS LAST' if order == 'desc' else 'NULLS FIRST'

    if sort in ('pitches', 'unlocks', 'credits'):
        base = f"unlocks_count {direction} {nulls}, u.created_at DESC"
    elif sort == 'followers':
        base = f"c.followers_count {direction} {nulls}, u.created_at DESC"
    else:
        base = f"u.created_at {direction} {nulls}"

    rank_sql, rank_params = _search_rank_sql(search_token)
    if rank_sql:
        return f"{rank_sql} ASC, {base}", rank_params
    return base, []


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
        where_sql, params, search_token = _build_where_clause()
        order_sql, rank_params = _resolve_sort(search_token=search_token)

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
                c.bio,
                c.social_handle,
                c.social_platform,
                c.social_follower_count,
                c.social_verified,
                COALESCE(c.approval_status, 'approved') AS approval_status,
                c.waitlist_joined_at,
                c.approved_at,
                c.rejected_at,
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
        _ensure_review_columns(cursor)

        cursor.execute(count_sql, tuple(params))
        total = cursor.fetchone()['total']

        cursor.execute(stats_sql, tuple(params))
        stats_row = cursor.fetchone()

        cursor.execute(select_sql, tuple(params + rank_params + [limit, offset]))
        creators = cursor.fetchall()

        for c in creators:
            c['regions'] = _parse_json_maybe(c.get('regions'), [])
            c['platforms'] = _parse_json_maybe(c.get('platforms'), [])
            c['social_links'] = _parse_json_maybe(c.get('social_links'), {})

        snapshot = _approval_snapshot(cursor)
        conn.close()

        return jsonify({
            'creators': [_serialize_row(c) for c in creators],
            'pagination': {
                'total': total,
                'limit': limit,
                'offset': offset,
            },
            'stats': _serialize_row(stats_row),
            'approval': snapshot,
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
        _ensure_review_columns(cursor)
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
                c.social_handle,
                c.social_platform,
                c.social_follower_count,
                c.social_verified,
                c.total_likes,
                c.social_oauth_videos,
                COALESCE(c.approval_status, 'approved') AS approval_status,
                c.waitlist_joined_at,
                c.approved_at,
                c.approved_by,
                c.rejection_reason,
                c.rejected_at,
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

        creator = _enrich_review_row(creator)
        creator['top_locations'] = _parse_json_maybe(creator.get('top_locations'), [])

        return jsonify({'creator': creator})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


REVIEW_QUEUE_SELECT = """
            SELECT
                c.id AS creator_id,
                u.id AS user_id,
                u.email,
                u.first_name,
                c.username,
                c.image_profile,
                c.bio,
                c.followers_count,
                c.social_follower_count,
                c.social_handle,
                c.social_platform,
                c.social_verified,
                c.social_links,
                c.social_oauth_videos,
                c.platforms,
                c.niche,
                c.regions,
                c.primary_age_range,
                c.total_likes,
                c.total_posts,
                c.engagement_rate,
                c.avg_engagement_rate,
                COALESCE(c.subscription_tier, 'free') AS tier,
                COALESCE(c.approval_status, 'pending') AS approval_status,
                c.waitlist_joined_at,
                u.created_at AS signup_date,
                c.created_at AS creator_created_at
            FROM creators c
            JOIN users u ON c.user_id = u.id
"""


@admin_creators_bp.route('/creators/approval-queue', methods=['GET'])
@admin_required
def get_approval_queue():
    """
    FIFO review queue for pending creators.
    Complete profiles surface first; incomplete signups stay at the bottom.
    """
    try:
        limit = max(1, min(int(request.args.get('limit', 50)), 100))
        offset = max(0, int(request.args.get('offset', 0)))
        ready_only = request.args.get('ready_only', '').strip().lower() in ('1', 'true', 'yes')
        niche = (request.args.get('niche') or request.args.get('filter_niche') or '').strip()
        q = request.args.get('q', '').strip()

        where = ["c.approval_status = 'pending'"]
        params = []
        if ready_only:
            where.append("""
                NULLIF(BTRIM(COALESCE(c.username::text, '')), '') IS NOT NULL
                AND c.niche IS NOT NULL
                AND BTRIM(c.niche::text) NOT IN ('', '[]', 'null', 'None', '""')
            """)
        if niche:
            where.append("COALESCE(c.niche, '') ILIKE %s")
            params.append(f'%{niche}%')
        if q:
            where.append("(u.email ILIKE %s OR u.first_name ILIKE %s OR c.username ILIKE %s OR c.social_handle ILIKE %s)")
            like = f'%{q}%'
            params.extend([like, like, like, like])

        where_sql = " AND ".join(where)
        order_sql = """
            CASE
                WHEN NULLIF(BTRIM(COALESCE(c.username::text, '')), '') IS NOT NULL
                 AND c.niche IS NOT NULL
                 AND BTRIM(c.niche::text) NOT IN ('', '[]', 'null', 'None', '""')
                THEN 0 ELSE 1
            END,
            COALESCE(c.waitlist_joined_at, c.created_at, u.created_at) ASC
        """

        conn = get_db_connection()
        cursor = conn.cursor(cursor_factory=RealDictCursor)
        _ensure_review_columns(cursor)
        cursor.execute(f"""
            SELECT COUNT(*)::int AS total
            FROM creators c
            JOIN users u ON c.user_id = u.id
            WHERE {where_sql}
        """, tuple(params))
        total = cursor.fetchone()['total']

        cursor.execute(f"""
            {REVIEW_QUEUE_SELECT}
            WHERE {where_sql}
            ORDER BY {order_sql}
            LIMIT %s OFFSET %s
        """, tuple(params + [limit, offset]))
        rows = [_enrich_review_row(r) for r in cursor.fetchall()]
        snapshot = _approval_snapshot(cursor)
        conn.close()

        return jsonify({
            'creators': rows,
            'total': total,
            'limit': limit,
            'offset': offset,
            'approval': snapshot,
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500


def _load_pending_creator(cursor, creator_id):
    cursor.execute(f"""
        {REVIEW_QUEUE_SELECT}
        WHERE c.id = %s
    """, (creator_id,))
    row = cursor.fetchone()
    return _enrich_review_row(row) if row else None


@admin_creators_bp.route('/creators/<int:creator_id>/approve', methods=['POST'])
@admin_required
def approve_creator(creator_id):
    """Approve a pending creator. Pro subscribers are marked pro_approved."""
    try:
        data = request.get_json(silent=True) or {}
        send_email = data.get('send_email', True)
        note = (data.get('note') or '').strip() or None
        force_pro = bool(data.get('as_pro'))

        conn = get_db_connection()
        cursor = conn.cursor(cursor_factory=RealDictCursor)
        creator = _load_pending_creator(cursor, creator_id)
        if not creator:
            conn.close()
            return jsonify({'error': 'Creator not found'}), 404
        if creator.get('approval_status') != 'pending':
            conn.close()
            return jsonify({'error': 'Creator is not pending review'}), 409

        admin_id = _admin_actor_id(cursor)
        tier = str(creator.get('tier') or 'free').lower()
        new_status = 'pro_approved' if force_pro or tier in ('pro', 'elite') else 'approved'

        cursor.execute("""
            UPDATE creators
            SET approval_status = %s,
                approved_at = NOW(),
                approved_by = %s,
                rejection_reason = NULL,
                rejected_at = NULL
            WHERE id = %s AND approval_status = 'pending'
            RETURNING id
        """, (new_status, admin_id, creator_id))
        if not cursor.fetchone():
            conn.close()
            return jsonify({'error': 'Creator not found or already processed'}), 409

        cursor.execute("""
            INSERT INTO creator_approval_audit
            (creator_id, admin_user_id, previous_status, new_status, reason, metadata)
            VALUES (%s, %s, 'pending', %s, %s, %s)
        """, (
            creator_id, admin_id, new_status, note,
            json.dumps({'manual_approval': True, 'as_pro': new_status == 'pro_approved'}),
        ))
        conn.commit()

        emailed = False
        if send_email:
            from creator_approval_routes import send_approval_email
            emailed = bool(send_approval_email(
                creator.get('email'),
                creator.get('first_name') or creator.get('display_handle') or creator.get('username') or 'there',
                user_id=creator.get('user_id'),
                as_pro=new_status == 'pro_approved',
            ))
            if emailed:
                cursor.execute("""
                    UPDATE creators SET approval_email_sent_at = NOW() WHERE id = %s
                """, (creator_id,))
                conn.commit()

        snapshot = _approval_snapshot(cursor)
        conn.close()
        return jsonify({
            'success': True,
            'creator_id': creator_id,
            'status': new_status,
            'emailed': emailed,
            'approval': snapshot,
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@admin_creators_bp.route('/creators/<int:creator_id>/reject', methods=['POST'])
@admin_required
def reject_creator(creator_id):
    """Reject a pending creator. Reason is required and shown on the waitlist."""
    try:
        data = request.get_json(silent=True) or {}
        reason = (data.get('reason') or '').strip()
        send_email = data.get('send_email', True)
        if len(reason) < 8:
            return jsonify({'error': 'Pick a rejection reason'}), 400

        conn = get_db_connection()
        cursor = conn.cursor(cursor_factory=RealDictCursor)
        creator = _load_pending_creator(cursor, creator_id)
        if not creator:
            conn.close()
            return jsonify({'error': 'Creator not found'}), 404
        if creator.get('approval_status') != 'pending':
            conn.close()
            return jsonify({'error': 'Creator is not pending review'}), 409

        admin_id = _admin_actor_id(cursor)
        cursor.execute("""
            UPDATE creators
            SET approval_status = 'rejected',
                rejection_reason = %s,
                rejected_at = NOW()
            WHERE id = %s AND approval_status = 'pending'
            RETURNING id
        """, (reason, creator_id))
        if not cursor.fetchone():
            conn.close()
            return jsonify({'error': 'Creator not found or already processed'}), 409

        cursor.execute("""
            INSERT INTO creator_approval_audit
            (creator_id, admin_user_id, previous_status, new_status, reason, metadata)
            VALUES (%s, %s, 'pending', 'rejected', %s, %s)
        """, (creator_id, admin_id, reason, json.dumps({'manual_rejection': True})))
        conn.commit()

        emailed = False
        if send_email:
            from creator_approval_routes import send_rejection_email
            emailed = bool(send_rejection_email(
                creator.get('email'),
                creator.get('first_name') or creator.get('display_handle') or creator.get('username') or 'there',
                reason,
                user_id=creator.get('user_id'),
            ))

        snapshot = _approval_snapshot(cursor)
        conn.close()
        return jsonify({
            'success': True,
            'creator_id': creator_id,
            'status': 'rejected',
            'reason': reason,
            'emailed': emailed,
            'approval': snapshot,
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@admin_creators_bp.route('/creators/<int:creator_id>/undo-decision', methods=['POST'])
@admin_required
def undo_creator_decision(creator_id):
    """Put an approved/rejected creator back in the queue. Does not unsend email."""
    try:
        conn = get_db_connection()
        cursor = conn.cursor(cursor_factory=RealDictCursor)
        cursor.execute("""
            SELECT id, approval_status FROM creators WHERE id = %s
        """, (creator_id,))
        row = cursor.fetchone()
        if not row:
            conn.close()
            return jsonify({'error': 'Creator not found'}), 404
        previous = row['approval_status']
        if previous == 'pending':
            conn.close()
            return jsonify({'error': 'Creator is already pending'}), 409

        admin_id = _admin_actor_id(cursor)
        cursor.execute("""
            UPDATE creators
            SET approval_status = 'pending',
                approved_at = NULL,
                approved_by = NULL,
                rejection_reason = NULL,
                rejected_at = NULL
            WHERE id = %s
        """, (creator_id,))
        cursor.execute("""
            INSERT INTO creator_approval_audit
            (creator_id, admin_user_id, previous_status, new_status, reason, metadata)
            VALUES (%s, %s, %s, 'pending', %s, %s)
        """, (
            creator_id, admin_id, previous, 'Undo last decision',
            json.dumps({'undo': True}),
        ))
        conn.commit()
        snapshot = _approval_snapshot(cursor)
        conn.close()
        return jsonify({
            'success': True,
            'creator_id': creator_id,
            'status': 'pending',
            'previous_status': previous,
            'approval': snapshot,
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500
