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


@admin_creators_bp.route('/creators', methods=['GET'])
@admin_required
def list_creators():
    """
    Scan/search all creators for admin workflows.
    """
    try:
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

        limit = int(request.args.get('limit', 25))
        offset = int(request.args.get('offset', 0))
        limit = max(1, min(limit, 200))
        offset = max(0, offset)

        where_clauses = ["u.unsubscribed_at IS NULL"]
        params = []

        if q:
            # Search email, first name, and creator username
            where_clauses.append("(u.email ILIKE %s OR u.first_name ILIKE %s OR c.username ILIKE %s)")
            like = f'%{q}%'
            params.extend([like, like, like])

        if niche:
            where_clauses.append("c.niche = %s")
            params.append(niche)

        if region:
            # regions is stored as JSONB in the DB (in most deployments).
            # Casting to text keeps this endpoint resilient even if it's JSONB or text.
            where_clauses.append("COALESCE(c.regions, '[]'::jsonb)::text ILIKE %s")
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

        where_sql = " AND ".join(where_clauses)

        count_sql = f"""
            SELECT COUNT(DISTINCT c.id) AS total
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
                c.followers_count,
                c.niche,
                c.regions,
                COALESCE(c.subscription_tier, 'free') AS tier,
                c.pitches_sent_this_week,
                c.pitches_sent_total,
                COALESCE(c.brands_saved_count, 0) AS brands_saved,
                COALESCE(c.has_media_kit, false) AS has_media_kit,
                c.kit_published_at,
                c.media_kit_url,
                u.created_at AS signup_date
            FROM creators c
            JOIN users u ON c.user_id = u.id
            WHERE {where_sql}
            ORDER BY u.created_at DESC
            LIMIT %s
            OFFSET %s
        """

        conn = get_db_connection()
        cursor = conn.cursor(cursor_factory=RealDictCursor)

        cursor.execute(count_sql, tuple(params))
        total = cursor.fetchone()['total']

        cursor.execute(select_sql, tuple(params + [limit, offset]))
        creators = cursor.fetchall()

        # Parse JSON-like fields for frontend rendering
        for c in creators:
            c['regions'] = _parse_json_maybe(c.get('regions'), [])

        conn.close()

        return jsonify({
            'creators': creators,
            'pagination': {
                'total': total,
                'limit': limit,
                'offset': offset,
            }
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
        cursor.execute("""
            SELECT
                c.id AS creator_id,
                u.id AS user_id,
                u.email,
                u.first_name,
                COALESCE(u.is_verified, false) AS is_verified,
                c.username,
                c.bio,
                c.followers_count,
                c.platforms,
                c.social_links,
                c.niche,
                c.regions,
                c.primary_age_range,
                c.top_locations,
                COALESCE(c.subscription_tier, 'free') AS tier,
                c.pitches_sent_this_week,
                c.pitches_sent_total,
                COALESCE(c.brands_saved_count, 0) AS brands_saved,
                COALESCE(c.has_media_kit, false) AS has_media_kit,
                c.kit_published_at,
                c.media_kit_url,
                u.created_at AS signup_date
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
        creator['social_links'] = _parse_json_maybe(creator.get('social_links'), [])
        creator['regions'] = _parse_json_maybe(creator.get('regions'), [])

        return jsonify({'creator': creator})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

