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
from datetime import date, datetime
from decimal import Decimal

# Add parent directory for imports
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


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

    where_clauses = ["u.unsubscribed_at IS NULL"]
    params = []

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

    if sort not in ('signup', 'pitches', 'followers'):
        sort = 'signup'
    if order not in ('asc', 'desc'):
        order = 'desc'

    direction = 'ASC' if order == 'asc' else 'DESC'
    nulls = 'NULLS LAST' if order == 'desc' else 'NULLS FIRST'

    if sort == 'pitches':
        return f"pitches_total {direction} {nulls}, u.created_at DESC"
    if sort == 'followers':
        return f"c.followers_count {direction} {nulls}, u.created_at DESC"
    return f"u.created_at {direction} {nulls}"


PITCH_STATS_SQL = """
    (
        SELECT COUNT(*)::int
        FROM creator_pipeline cp
        WHERE cp.creator_id = c.id AND cp.pitched_at IS NOT NULL
    ) AS pitches_total,
    (
        SELECT COUNT(*)::int
        FROM creator_pipeline cp
        WHERE cp.creator_id = c.id
          AND cp.pitched_at >= DATE_TRUNC('week', NOW())
    ) AS pitches_this_week
"""


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
                        SELECT 1 FROM creator_pipeline cp
                        WHERE cp.creator_id = c.id AND cp.pitched_at IS NOT NULL
                    )
                ) AS pitched
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
                {PITCH_STATS_SQL},
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
                {PITCH_STATS_SQL},
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
                    SELECT MAX(cp.pitched_at) FROM creator_pipeline cp
                    WHERE cp.creator_id = c.id AND cp.pitched_at IS NOT NULL
                ) AS last_pitched_at
            FROM creators c
            JOIN users u ON c.user_id = u.id
            WHERE c.id = %s
              AND u.unsubscribed_at IS NULL
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
