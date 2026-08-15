"""
Brand Content Hub — creator submissions + admin review.
v1: manual admin push, no brand notification email.
"""

from flask import Blueprint, request, jsonify, session
from flask_jwt_extended import get_jwt_identity
from functools import wraps
from psycopg2.extras import RealDictCursor
from urllib.parse import urlparse
from datetime import datetime
import os
import psycopg2

content_hub_bp = Blueprint('content_hub', __name__)

CONTENT_TYPES = frozenset({
    'unboxing', 'review', 'grwm', 'haul', 'tutorial', 'lifestyle', 'other',
})
STATUSES = frozenset({
    'pending_review', 'approved', 'rejected', 'flagged',
    'pushed_to_brand', 'brand_responded',
})
BRAND_RESPONSES = frozenset({
    'interested', 'not_interested', 'wants_more_content',
    'wants_paid_collab', 'no_response',
})
REJECTION_REASONS = frozenset({
    'duplicate',
    'invalid_url',
    'brand_not_tagged',
    'quality',
    'off_brand',
    'other',
})
REJECTION_LABELS = {
    'duplicate': 'Duplicate submission',
    'invalid_url': 'Post URL invalid or not accessible',
    'brand_not_tagged': 'Brand not tagged or mentioned in the content',
    'quality': 'Content quality below threshold',
    'off_brand': 'Off-brand or negative content',
    'other': 'Other',
}
ALLOWED_HOSTS = (
    'tiktok.com', 'www.tiktok.com', 'vm.tiktok.com',
    'instagram.com', 'www.instagram.com',
    'youtube.com', 'www.youtube.com', 'm.youtube.com', 'youtu.be',
)
RATE_LIMIT_PER_DAY = 10
MAX_FREETEXT = 100
MAX_DESCRIPTION = 200

_TABLE_READY = False


def get_db_connection():
    url = os.getenv('DATABASE_URL')
    if url:
        return psycopg2.connect(url)
    return psycopg2.connect(
        host=os.getenv('DB_HOST'),
        port=os.getenv('DB_PORT', 5432),
        database=os.getenv('DB_NAME'),
        user=os.getenv('DB_USER'),
        password=os.getenv('DB_PASSWORD'),
    )


def ensure_table():
    global _TABLE_READY
    if _TABLE_READY:
        return
    sql_path = os.path.join(os.path.dirname(__file__), 'migrations', 'add_content_submissions.sql')
    with open(sql_path, encoding='utf-8') as f:
        sql = f.read()
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        cursor.execute(sql)
        conn.commit()
        cursor.close()
        _TABLE_READY = True
    finally:
        conn.close()


def get_creator_id_from_session():
    creator_id = session.get('creator_id')
    if creator_id:
        return creator_id
    user_id = session.get('user_id')
    lookup_ids = [user_id]
    try:
        jwt_user_id = get_jwt_identity()
        if jwt_user_id:
            lookup_ids.append(jwt_user_id)
    except Exception:
        pass
    for uid in lookup_ids:
        if not uid:
            continue
        try:
            conn = get_db_connection()
            cursor = conn.cursor(cursor_factory=RealDictCursor)
            cursor.execute('SELECT id FROM creators WHERE user_id = %s', (uid,))
            creator = cursor.fetchone()
            cursor.close()
            conn.close()
            if creator:
                return creator['id']
        except Exception:
            pass
    return None


def admin_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        admin_token = request.headers.get('X-Admin-Token')
        if admin_token == 'pr-hunter-admin-2026':
            return f(*args, **kwargs)
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


def _host_allowed(hostname):
    host = (hostname or '').lower().rstrip('.')
    if host.startswith('www.'):
        host = host[4:]
    allowed = {h[4:] if h.startswith('www.') else h for h in ALLOWED_HOSTS}
    return host in allowed or any(host.endswith('.' + h) for h in allowed)


def parse_post_url(raw):
    """Return (normalized_url, platform) or (None, error)."""
    url = (raw or '').strip()
    if not url.lower().startswith('https://'):
        return None, 'Post URL must start with https://'
    try:
        parsed = urlparse(url)
    except Exception:
        return None, 'Post URL is not valid'
    if parsed.scheme != 'https' or not parsed.netloc:
        return None, 'Post URL is not valid'
    host = parsed.netloc.lower()
    if not _host_allowed(host):
        return None, 'Post URL must be a TikTok, Instagram, or YouTube link'
    platform = 'other'
    if 'tiktok.com' in host:
        platform = 'tiktok'
    elif 'instagram.com' in host:
        platform = 'instagram'
    elif 'youtube.com' in host or host == 'youtu.be':
        platform = 'youtube'
    normalized = f'https://{parsed.netloc}{parsed.path}'.rstrip('/')
    if parsed.query and platform == 'youtube':
        normalized = f'{normalized}?{parsed.query}'
    return {'url': normalized, 'platform': platform}, None


def validate_submission_payload(data):
    errors = []
    parsed, url_err = parse_post_url((data or {}).get('post_url'))
    if url_err:
        errors.append(url_err)

    brand_id = (data or {}).get('brand_id')
    freetext = ((data or {}).get('brand_name_freetext') or '').strip()
    if brand_id in ('', None):
        brand_id = None
    else:
        try:
            brand_id = int(brand_id)
        except (TypeError, ValueError):
            errors.append('brand_id must be a number')
            brand_id = None
    if not brand_id and not freetext:
        errors.append('Pick a brand from the directory or enter a brand name')
    if freetext and len(freetext) > MAX_FREETEXT:
        errors.append(f'Brand name must be {MAX_FREETEXT} characters or fewer')

    content_type = ((data or {}).get('content_type') or '').strip().lower()
    if content_type not in CONTENT_TYPES:
        errors.append('Select a content type')

    description = ((data or {}).get('description') or '').strip()
    if len(description) > MAX_DESCRIPTION:
        errors.append(f'Description must be {MAX_DESCRIPTION} characters or fewer')

    if not (data or {}).get('consent_given'):
        errors.append('Consent is required')

    payload = {
        'post_url': parsed['url'] if parsed else None,
        'post_platform': parsed['platform'] if parsed else None,
        'brand_id': brand_id,
        'brand_name_freetext': freetext if not brand_id else None,
        'content_type': content_type if content_type in CONTENT_TYPES else None,
        'description': description or None,
        'consent_given': True,
    }
    return payload, errors


def _serialize_submission(row, admin=False):
    if not row:
        return None
    data = dict(row)
    for key in ('created_at', 'updated_at', 'reviewed_at', 'pushed_to_brand_at', 'brand_response_at'):
        val = data.get(key)
        if hasattr(val, 'isoformat'):
            data[key] = val.isoformat()
    if not admin:
        data.pop('admin_notes', None)
        data.pop('reviewed_by', None)
        reason = data.get('rejection_reason')
        data['rejection_reason_label'] = (
            REJECTION_LABELS.get(reason, reason) if reason else None
        )
    brand_name = data.get('directory_brand_name') or data.get('brand_name_freetext')
    data['brand_name'] = brand_name
    data['brand_logo'] = data.get('logo_url')
    data['is_freetext_brand'] = not data.get('brand_id') and bool(data.get('brand_name_freetext'))
    return data


# ---------------------------------------------------------------------------
# Creator endpoints
# ---------------------------------------------------------------------------

@content_hub_bp.route('/api/creator/content-submissions', methods=['GET'])
def list_creator_submissions():
    ensure_table()
    creator_id = get_creator_id_from_session()
    if not creator_id:
        return jsonify({'success': False, 'error': 'Not authenticated'}), 401
    conn = get_db_connection()
    try:
        cursor = conn.cursor(cursor_factory=RealDictCursor)
        cursor.execute("""
            SELECT s.*, b.brand_name AS directory_brand_name, b.logo_url
            FROM creator_content_submissions s
            LEFT JOIN pr_brands b ON b.id = s.brand_id
            WHERE s.creator_id = %s
            ORDER BY s.created_at DESC
        """, (creator_id,))
        rows = cursor.fetchall() or []
        return jsonify({
            'success': True,
            'submissions': [_serialize_submission(r) for r in rows],
        })
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500
    finally:
        conn.close()


@content_hub_bp.route('/api/creator/content-submissions', methods=['POST'])
def create_submission():
    ensure_table()
    creator_id = get_creator_id_from_session()
    if not creator_id:
        return jsonify({'success': False, 'error': 'Not authenticated'}), 401

    payload, errors = validate_submission_payload(request.get_json(silent=True) or {})
    if errors:
        return jsonify({'success': False, 'error': errors[0], 'errors': errors}), 400

    conn = get_db_connection()
    try:
        cursor = conn.cursor(cursor_factory=RealDictCursor)
        cursor.execute("""
            SELECT COUNT(*) AS n FROM creator_content_submissions
            WHERE creator_id = %s AND created_at > NOW() - INTERVAL '24 hours'
        """, (creator_id,))
        if (cursor.fetchone() or {}).get('n', 0) >= RATE_LIMIT_PER_DAY:
            return jsonify({
                'success': False,
                'error': 'You can submit up to 10 posts per day. Try again tomorrow.',
            }), 429

        if payload['brand_id']:
            cursor.execute('SELECT id FROM pr_brands WHERE id = %s', (payload['brand_id'],))
            if not cursor.fetchone():
                return jsonify({'success': False, 'error': 'Brand not found'}), 400

        cursor.execute("""
            INSERT INTO creator_content_submissions (
                creator_id, post_url, post_platform, brand_id, brand_name_freetext,
                content_type, description, consent_given, status
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, TRUE, 'pending_review')
            RETURNING *
        """, (
            creator_id,
            payload['post_url'],
            payload['post_platform'],
            payload['brand_id'],
            payload['brand_name_freetext'],
            payload['content_type'],
            payload['description'],
        ))
        row = cursor.fetchone()
        conn.commit()
        if payload['brand_id']:
            cursor.execute(
                'SELECT brand_name AS directory_brand_name, logo_url FROM pr_brands WHERE id = %s',
                (payload['brand_id'],),
            )
            brand = cursor.fetchone() or {}
            row = {**dict(row), **dict(brand)}
        return jsonify({
            'success': True,
            'submission': _serialize_submission(row),
        }), 201
    except psycopg2.errors.UniqueViolation:
        conn.rollback()
        return jsonify({
            'success': False,
            'error': "You've already submitted this post.",
        }), 409
    except Exception as e:
        conn.rollback()
        return jsonify({'success': False, 'error': str(e)}), 500
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Admin endpoints
# ---------------------------------------------------------------------------

def _admin_reviewer_id():
    return session.get('user_id')


@content_hub_bp.route('/api/admin/content-submissions', methods=['GET'])
@admin_required
def admin_list_submissions():
    ensure_table()
    status = (request.args.get('status') or 'pending_review').strip()
    brand_id = request.args.get('brand_id')
    creator_q = (request.args.get('creator') or '').strip()
    date_from = request.args.get('from')
    date_to = request.args.get('to')

    clauses = []
    params = []
    if status and status != 'all':
        clauses.append('s.status = %s')
        params.append(status)
    if brand_id:
        clauses.append('s.brand_id = %s')
        params.append(int(brand_id))
    if creator_q:
        clauses.append('(c.username ILIKE %s OR c.social_handle ILIKE %s)')
        params.extend([f'%{creator_q}%', f'%{creator_q}%'])
    if date_from:
        clauses.append('s.created_at >= %s')
        params.append(date_from)
    if date_to:
        clauses.append('s.created_at < %s::date + INTERVAL \'1 day\'')
        params.append(date_to)

    where = ('WHERE ' + ' AND '.join(clauses)) if clauses else ''
    conn = get_db_connection()
    try:
        cursor = conn.cursor(cursor_factory=RealDictCursor)
        cursor.execute(f"""
            SELECT s.*,
                   c.username, c.followers_count, c.niche, c.regions, c.image_profile,
                   c.social_handle, c.social_platform, c.platforms,
                   b.brand_name AS directory_brand_name, b.logo_url, b.category AS brand_category,
                   b.website AS brand_website, b.contact_email AS brand_contact_email
            FROM creator_content_submissions s
            JOIN creators c ON c.id = s.creator_id
            LEFT JOIN pr_brands b ON b.id = s.brand_id
            {where}
            ORDER BY s.created_at DESC
            LIMIT 200
        """, params)
        rows = cursor.fetchall() or []

        cursor.execute("""
            SELECT status, COUNT(*) AS n
            FROM creator_content_submissions
            GROUP BY status
        """)
        counts = {r['status']: r['n'] for r in (cursor.fetchall() or [])}
        return jsonify({
            'success': True,
            'submissions': [_serialize_submission(r, admin=True) for r in rows],
            'counts': counts,
        })
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500
    finally:
        conn.close()


@content_hub_bp.route('/api/admin/content-submissions/<int:submission_id>', methods=['GET'])
@admin_required
def admin_get_submission(submission_id):
    ensure_table()
    conn = get_db_connection()
    try:
        cursor = conn.cursor(cursor_factory=RealDictCursor)
        cursor.execute(ADMIN_DETAIL_SQL + ' WHERE s.id = %s', (submission_id,))
        row = cursor.fetchone()
        if not row:
            return jsonify({'success': False, 'error': 'Not found'}), 404
        return jsonify({'success': True, 'submission': _serialize_submission(row, admin=True)})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500
    finally:
        conn.close()


ADMIN_DETAIL_SQL = """
            SELECT s.*,
                   c.username, c.followers_count, c.niche, c.regions, c.image_profile,
                   c.social_handle, c.social_platform, c.platforms, c.bio,
                   b.brand_name AS directory_brand_name, b.logo_url, b.category AS brand_category,
                   b.website AS brand_website, b.contact_email AS brand_contact_email
            FROM creator_content_submissions s
            JOIN creators c ON c.id = s.creator_id
            LEFT JOIN pr_brands b ON b.id = s.brand_id
"""


def _update_status(submission_id, extra_set, extra_params):
    ensure_table()
    conn = get_db_connection()
    try:
        cursor = conn.cursor(cursor_factory=RealDictCursor)
        cursor.execute('SELECT id FROM creator_content_submissions WHERE id = %s', (submission_id,))
        if not cursor.fetchone():
            return None, ('Not found', 404)
        sets = extra_set + ['updated_at = NOW()']
        cursor.execute(
            f"UPDATE creator_content_submissions SET {', '.join(sets)} WHERE id = %s",
            extra_params + [submission_id],
        )
        cursor.execute(ADMIN_DETAIL_SQL + ' WHERE s.id = %s', (submission_id,))
        row = cursor.fetchone()
        conn.commit()
        return dict(row), None
    except Exception as e:
        conn.rollback()
        return None, (str(e), 500)
    finally:
        conn.close()


@content_hub_bp.route('/api/admin/content-submissions/<int:submission_id>/approve', methods=['PATCH'])
@admin_required
def admin_approve(submission_id):
    notes = (request.get_json(silent=True) or {}).get('admin_notes')
    sets = ['status = %s', 'reviewed_at = NOW()', 'reviewed_by = %s']
    params = ['approved', _admin_reviewer_id()]
    if notes is not None:
        sets.append('admin_notes = %s')
        params.append(notes)
    row, err = _update_status(submission_id, sets, params)
    if err:
        return jsonify({'success': False, 'error': err[0]}), err[1]
    return jsonify({'success': True, 'submission': _serialize_submission(row, admin=True)})


@content_hub_bp.route('/api/admin/content-submissions/<int:submission_id>/reject', methods=['PATCH'])
@admin_required
def admin_reject(submission_id):
    data = request.get_json(silent=True) or {}
    reason = (data.get('reason') or data.get('rejection_reason') or '').strip()
    if reason not in REJECTION_REASONS:
        return jsonify({'success': False, 'error': 'Pick a rejection reason'}), 400
    detail = (data.get('reason_detail') or '').strip()
    stored = reason if reason != 'other' else (detail or 'other')
    notes = data.get('admin_notes')
    sets = [
        'status = %s', 'rejection_reason = %s',
        'reviewed_at = NOW()', 'reviewed_by = %s',
    ]
    params = ['rejected', stored, _admin_reviewer_id()]
    if notes is not None:
        sets.append('admin_notes = %s')
        params.append(notes)
    row, err = _update_status(submission_id, sets, params)
    if err:
        return jsonify({'success': False, 'error': err[0]}), err[1]
    return jsonify({'success': True, 'submission': _serialize_submission(row, admin=True)})


@content_hub_bp.route('/api/admin/content-submissions/<int:submission_id>/flag', methods=['PATCH'])
@admin_required
def admin_flag(submission_id):
    notes = (request.get_json(silent=True) or {}).get('admin_notes')
    sets = ['status = %s', 'reviewed_at = NOW()', 'reviewed_by = %s']
    params = ['flagged', _admin_reviewer_id()]
    if notes is not None:
        sets.append('admin_notes = %s')
        params.append(notes)
    row, err = _update_status(submission_id, sets, params)
    if err:
        return jsonify({'success': False, 'error': err[0]}), err[1]
    return jsonify({'success': True, 'submission': _serialize_submission(row, admin=True)})


@content_hub_bp.route('/api/admin/content-submissions/<int:submission_id>/push', methods=['PATCH'])
@admin_required
def admin_push(submission_id):
    notes = (request.get_json(silent=True) or {}).get('admin_notes')
    sets = [
        'status = %s', 'pushed_to_brand_at = NOW()',
        'reviewed_at = COALESCE(reviewed_at, NOW())', 'reviewed_by = COALESCE(reviewed_by, %s)',
    ]
    params = ['pushed_to_brand', _admin_reviewer_id()]
    if notes is not None:
        sets.append('admin_notes = %s')
        params.append(notes)
    row, err = _update_status(submission_id, sets, params)
    if err:
        return jsonify({'success': False, 'error': err[0]}), err[1]
    return jsonify({'success': True, 'submission': _serialize_submission(row, admin=True)})


@content_hub_bp.route('/api/admin/content-submissions/<int:submission_id>/respond', methods=['PATCH'])
@admin_required
def admin_respond(submission_id):
    data = request.get_json(silent=True) or {}
    response_status = (data.get('brand_response_status') or '').strip()
    if response_status not in BRAND_RESPONSES:
        return jsonify({'success': False, 'error': 'Pick a brand response'}), 400
    sets = [
        'status = %s', 'brand_response_status = %s', 'brand_response_at = NOW()',
    ]
    params = ['brand_responded', response_status]
    notes = data.get('admin_notes')
    if notes is not None:
        sets.append('admin_notes = %s')
        params.append(notes)
    row, err = _update_status(submission_id, sets, params)
    if err:
        return jsonify({'success': False, 'error': err[0]}), err[1]
    return jsonify({'success': True, 'submission': _serialize_submission(row, admin=True)})


@content_hub_bp.route('/api/admin/content-submissions/<int:submission_id>/notes', methods=['PATCH'])
@admin_required
def admin_notes(submission_id):
    notes = (request.get_json(silent=True) or {}).get('admin_notes', '')
    row, err = _update_status(submission_id, ['admin_notes = %s'], [notes])
    if err:
        return jsonify({'success': False, 'error': err[0]}), err[1]
    return jsonify({'success': True, 'submission': _serialize_submission(row, admin=True)})


@content_hub_bp.route('/api/admin/content-submissions/<int:submission_id>/add-brand', methods=['POST'])
@admin_required
def admin_add_brand_from_submission(submission_id):
    """Create a pr_brands draft from a free-text submission and backfill brand_id."""
    ensure_table()
    data = request.get_json(silent=True) or {}
    conn = get_db_connection()
    try:
        cursor = conn.cursor(cursor_factory=RealDictCursor)
        cursor.execute(
            'SELECT * FROM creator_content_submissions WHERE id = %s',
            (submission_id,),
        )
        sub = cursor.fetchone()
        if not sub:
            return jsonify({'success': False, 'error': 'Not found'}), 404

        name = (data.get('brand_name') or sub.get('brand_name_freetext') or '').strip()
        if not name:
            return jsonify({'success': False, 'error': 'Brand name is required'}), 400

        cursor.execute(
            'SELECT id, brand_name FROM pr_brands WHERE LOWER(brand_name) = LOWER(%s) LIMIT 1',
            (name,),
        )
        existing = cursor.fetchone()
        if existing:
            brand_id = existing['id']
        else:
            from routes.admin_brands import create_slug
            slug = create_slug(name)
            try:
                cursor.execute('SELECT id FROM pr_brands WHERE slug = %s', (slug,))
                if cursor.fetchone():
                    slug = f"{slug}-{int(datetime.now().timestamp())}"
            except Exception:
                conn.rollback()
                slug = create_slug(name)
            try:
                cursor.execute("""
                    INSERT INTO pr_brands (
                        brand_name, slug, website, category, contact_email,
                        status, notes, created_at
                    ) VALUES (%s, %s, %s, %s, %s, 'draft', %s, NOW())
                    RETURNING id
                """, (
                    name,
                    slug,
                    (data.get('website') or '').strip() or None,
                    (data.get('category') or 'other').strip() or 'other',
                    (data.get('contact_email') or '').strip() or None,
                    f'Added from content submission #{submission_id}',
                ))
            except Exception:
                conn.rollback()
                cursor.execute("""
                    INSERT INTO pr_brands (brand_name, website, category, contact_email, notes)
                    VALUES (%s, %s, %s, %s, %s)
                    RETURNING id
                """, (
                    name,
                    (data.get('website') or '').strip() or None,
                    (data.get('category') or 'other').strip() or 'other',
                    (data.get('contact_email') or '').strip() or None,
                    f'Added from content submission #{submission_id}',
                ))
            brand_id = cursor.fetchone()['id']

        cursor.execute("""
            UPDATE creator_content_submissions
            SET brand_id = %s, updated_at = NOW()
            WHERE id = %s
            RETURNING *
        """, (brand_id, submission_id))
        row = cursor.fetchone()
        conn.commit()
        return jsonify({
            'success': True,
            'brand_id': brand_id,
            'submission': _serialize_submission(row, admin=True),
        })
    except Exception as e:
        conn.rollback()
        return jsonify({'success': False, 'error': str(e)}), 500
    finally:
        conn.close()
