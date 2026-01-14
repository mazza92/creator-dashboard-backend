"""
Admin API Routes for PR Hunter
Handles candidate management, approval workflow, and hunt triggers
"""

from flask import Blueprint, request, jsonify, session
from functools import wraps
from psycopg2.extras import RealDictCursor
from datetime import datetime
import sys
import os

# Add parent directory for imports
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from database import get_db_connection
from tasks.pr_hunter_tasks import run_pr_hunt, reverify_email


# Create Blueprint
admin_pr_hunter_bp = Blueprint('admin_pr_hunter', __name__, url_prefix='/api/admin')


# ============================================================================
# AUTHENTICATION DECORATOR
# ============================================================================

def admin_required(f):
    """Decorator to require admin authentication"""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        # Check if user is authenticated and is admin
        user_id = session.get('user_id')
        if not user_id:
            return jsonify({'error': 'Authentication required'}), 401

        try:
            conn = get_db_connection()
            cursor = conn.cursor(cursor_factory=RealDictCursor)
            cursor.execute('SELECT role FROM users WHERE id = %s', (user_id,))
            user = cursor.fetchone()
            conn.close()

            if not user or user['role'] != 'brand':  # Assuming brands are admins
                return jsonify({'error': 'Admin access required'}), 403

        except Exception as e:
            return jsonify({'error': str(e)}), 500

        return f(*args, **kwargs)
    return decorated_function


# ============================================================================
# HUNT TRIGGER ENDPOINT
# ============================================================================

@admin_pr_hunter_bp.route('/pr-hunt/start', methods=['POST'])
@admin_required
def start_pr_hunt():
    """
    Start a new PR hunt for a given keyword

    Request Body:
        {
            "keyword": "K-Beauty",
            "max_results": 50
        }

    Returns:
        {
            "task_id": "abc-123",
            "status": "started",
            "keyword": "K-Beauty"
        }
    """
    try:
        data = request.get_json()
        keyword = data.get('keyword')
        max_results = data.get('max_results', 50)

        if not keyword:
            return jsonify({'error': 'Keyword is required'}), 400

        # Validate max_results
        if not isinstance(max_results, int) or max_results < 1 or max_results > 200:
            return jsonify({'error': 'max_results must be between 1 and 200'}), 400

        # Trigger Celery task
        task = run_pr_hunt.delay(keyword, max_results)

        return jsonify({
            'task_id': task.id,
            'status': 'started',
            'keyword': keyword,
            'max_results': max_results,
            'message': f'PR hunt started for "{keyword}". You will be notified when complete.'
        }), 202

    except Exception as e:
        return jsonify({'error': str(e)}), 500


@admin_pr_hunter_bp.route('/pr-hunt/status/<task_id>', methods=['GET'])
@admin_required
def get_hunt_status(task_id):
    """
    Get status of a running PR hunt task

    Returns:
        {
            "task_id": "abc-123",
            "state": "PROGRESS",
            "meta": {
                "step": "Enriching brands",
                "progress": 45,
                "saved": 12
            }
        }
    """
    try:
        from celery.result import AsyncResult
        task = AsyncResult(task_id, app=run_pr_hunt.app)

        response = {
            'task_id': task_id,
            'state': task.state,
        }

        if task.state == 'PROGRESS':
            response['meta'] = task.info
        elif task.state == 'SUCCESS':
            response['result'] = task.result
        elif task.state == 'FAILURE':
            response['error'] = str(task.info)

        return jsonify(response), 200

    except Exception as e:
        return jsonify({'error': str(e)}), 500


# ============================================================================
# CANDIDATE MANAGEMENT ENDPOINTS
# ============================================================================

@admin_pr_hunter_bp.route('/candidates', methods=['GET'])
@admin_required
def get_candidates():
    """
    Get all pending brand candidates with pagination

    Query Params:
        page: Page number (default 1)
        limit: Results per page (default 50)
        status: Filter by status (PENDING, APPROVED, REJECTED)
        min_score: Minimum verification score (0-100)

    Returns:
        {
            "candidates": [...],
            "pagination": {
                "page": 1,
                "limit": 50,
                "total": 150
            }
        }
    """
    try:
        # Parse query params
        page = int(request.args.get('page', 1))
        limit = int(request.args.get('limit', 50))
        status_filter = request.args.get('status', 'PENDING')
        min_score = int(request.args.get('min_score', 0))

        offset = (page - 1) * limit

        conn = get_db_connection()
        cursor = conn.cursor(cursor_factory=RealDictCursor)

        # Count total
        count_query = '''
            SELECT COUNT(*) as total
            FROM brand_candidates
            WHERE status = %s AND verification_score >= %s
        '''
        cursor.execute(count_query, (status_filter, min_score))
        total = cursor.fetchone()['total']

        # Get candidates
        query = '''
            SELECT
                id, brand_name, website_url, domain,
                instagram_handle, tiktok_handle,
                pr_manager_name, pr_manager_linkedin, pr_manager_title,
                contact_email, email_source, verification_score, verification_status, is_catch_all,
                logo_url, description, status, discovery_source,
                created_at, updated_at
            FROM brand_candidates
            WHERE status = %s AND verification_score >= %s
            ORDER BY verification_score DESC, created_at DESC
            LIMIT %s OFFSET %s
        '''

        cursor.execute(query, (status_filter, min_score, limit, offset))
        candidates = cursor.fetchall()

        conn.close()

        return jsonify({
            'candidates': candidates,
            'pagination': {
                'page': page,
                'limit': limit,
                'total': total
            }
        }), 200

    except Exception as e:
        return jsonify({'error': str(e)}), 500


@admin_pr_hunter_bp.route('/candidates/<int:candidate_id>', methods=['GET'])
@admin_required
def get_candidate(candidate_id):
    """Get single candidate details"""
    try:
        conn = get_db_connection()
        cursor = conn.cursor(cursor_factory=RealDictCursor)

        cursor.execute('SELECT * FROM brand_candidates WHERE id = %s', (candidate_id,))
        candidate = cursor.fetchone()

        conn.close()

        if not candidate:
            return jsonify({'error': 'Candidate not found'}), 404

        return jsonify(candidate), 200

    except Exception as e:
        return jsonify({'error': str(e)}), 500


@admin_pr_hunter_bp.route('/candidates/<int:candidate_id>', methods=['PATCH'])
@admin_required
def update_candidate(candidate_id):
    """
    Update candidate fields (e.g., manually fix name or email)

    Request Body:
        {
            "pr_manager_name": "Jessica Smith",
            "contact_email": "jessica@glowrecipe.com"
        }
    """
    try:
        data = request.get_json()

        # Allowed fields for manual editing
        allowed_fields = [
            'brand_name', 'pr_manager_name', 'pr_manager_title',
            'contact_email', 'description'
        ]

        # Build UPDATE query
        update_fields = []
        params = []

        for field in allowed_fields:
            if field in data:
                update_fields.append(f"{field} = %s")
                params.append(data[field])

        if not update_fields:
            return jsonify({'error': 'No valid fields to update'}), 400

        params.append(candidate_id)

        conn = get_db_connection()
        cursor = conn.cursor(cursor_factory=RealDictCursor)

        query = f'''
            UPDATE brand_candidates
            SET {", ".join(update_fields)}, updated_at = CURRENT_TIMESTAMP
            WHERE id = %s
            RETURNING *
        '''

        cursor.execute(query, params)
        updated_candidate = cursor.fetchone()

        conn.commit()
        conn.close()

        return jsonify(updated_candidate), 200

    except Exception as e:
        return jsonify({'error': str(e)}), 500


@admin_pr_hunter_bp.route('/candidates/approve', methods=['POST'])
@admin_required
def approve_candidates():
    """
    Approve candidates and move them to live pr_brands table

    Request Body:
        {
            "candidate_ids": [1, 2, 3]
        }

    Returns:
        {
            "approved": 3,
            "failed": 0,
            "brand_ids": [101, 102, 103]
        }
    """
    try:
        data = request.get_json()
        candidate_ids = data.get('candidate_ids', [])

        if not candidate_ids or not isinstance(candidate_ids, list):
            return jsonify({'error': 'candidate_ids must be a non-empty array'}), 400

        user_id = session.get('user_id')

        conn = get_db_connection()
        cursor = conn.cursor(cursor_factory=RealDictCursor)

        approved_count = 0
        failed_count = 0
        brand_ids = []

        for candidate_id in candidate_ids:
            try:
                # Get candidate
                cursor.execute('SELECT * FROM brand_candidates WHERE id = %s', (candidate_id,))
                candidate = cursor.fetchone()

                if not candidate:
                    failed_count += 1
                    continue

                # Create slug
                slug = create_slug(candidate['brand_name'])

                # Insert into brands table
                insert_query = '''
                    INSERT INTO brands (
                        name, slug, website, logo, description,
                        pr_contact_email, pr_manager_name,
                        instagram, tiktok,
                        is_visible, accepting_pr,
                        created_at
                    ) VALUES (
                        %s, %s, %s, %s, %s,
                        %s, %s,
                        %s, %s,
                        TRUE, TRUE,
                        CURRENT_TIMESTAMP
                    )
                    RETURNING id
                '''

                cursor.execute(insert_query, (
                    candidate['brand_name'],
                    slug,
                    candidate['website_url'],
                    candidate['logo_url'],
                    candidate['description'],
                    candidate['contact_email'],
                    candidate['pr_manager_name'],
                    candidate['instagram_handle'],
                    candidate['tiktok_handle']
                ))

                brand_id = cursor.fetchone()['id']
                brand_ids.append(brand_id)

                # Update candidate status
                cursor.execute('''
                    UPDATE brand_candidates
                    SET status = 'APPROVED',
                        approved_at = CURRENT_TIMESTAMP,
                        approved_by = %s
                    WHERE id = %s
                ''', (user_id, candidate_id))

                approved_count += 1

            except Exception as e:
                print(f"Failed to approve candidate {candidate_id}: {str(e)}")
                failed_count += 1
                conn.rollback()
                continue

        conn.commit()
        conn.close()

        return jsonify({
            'approved': approved_count,
            'failed': failed_count,
            'brand_ids': brand_ids
        }), 200

    except Exception as e:
        return jsonify({'error': str(e)}), 500


@admin_pr_hunter_bp.route('/candidates/reject', methods=['POST'])
@admin_required
def reject_candidates():
    """
    Reject candidates

    Request Body:
        {
            "candidate_ids": [1, 2, 3],
            "reason": "Not a good fit"
        }
    """
    try:
        data = request.get_json()
        candidate_ids = data.get('candidate_ids', [])
        reason = data.get('reason', 'Rejected by admin')

        if not candidate_ids:
            return jsonify({'error': 'candidate_ids is required'}), 400

        conn = get_db_connection()
        cursor = conn.cursor()

        cursor.execute('''
            UPDATE brand_candidates
            SET status = 'REJECTED',
                rejection_reason = %s,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = ANY(%s)
        ''', (reason, candidate_ids))

        rejected_count = cursor.rowcount

        conn.commit()
        conn.close()

        return jsonify({
            'rejected': rejected_count
        }), 200

    except Exception as e:
        return jsonify({'error': str(e)}), 500


@admin_pr_hunter_bp.route('/candidates/<int:candidate_id>/reverify', methods={'POST'])
@admin_required
def reverify_candidate_email(candidate_id):
    """
    Re-verify a candidate's email

    Returns:
        {
            "task_id": "xyz-789",
            "status": "started"
        }
    """
    try:
        # Trigger reverification task
        task = reverify_email.delay(candidate_id)

        return jsonify({
            'task_id': task.id,
            'status': 'started',
            'candidate_id': candidate_id
        }), 202

    except Exception as e:
        return jsonify({'error': str(e)}), 500


# ============================================================================
# STATISTICS ENDPOINT
# ============================================================================

@admin_pr_hunter_bp.route('/pr-hunt/stats', methods=['GET'])
@admin_required
def get_hunt_stats():
    """
    Get overall PR hunt statistics

    Returns:
        {
            "total_candidates": 150,
            "pending": 45,
            "approved": 100,
            "rejected": 5,
            "avg_verification_score": 87.5,
            "recent_hunts": [...]
        }
    """
    try:
        conn = get_db_connection()
        cursor = conn.cursor(cursor_factory=RealDictCursor)

        # Overall stats
        cursor.execute('''
            SELECT
                COUNT(*) as total,
                SUM(CASE WHEN status = 'PENDING' THEN 1 ELSE 0 END) as pending,
                SUM(CASE WHEN status = 'APPROVED' THEN 1 ELSE 0 END) as approved,
                SUM(CASE WHEN status = 'REJECTED' THEN 1 ELSE 0 END) as rejected,
                AVG(verification_score)::NUMERIC(5,2) as avg_score
            FROM brand_candidates
        ''')
        stats = cursor.fetchone()

        # Recent hunts (grouped by discovery_source)
        cursor.execute('''
            SELECT
                discovery_source,
                COUNT(*) as count,
                MAX(created_at) as last_hunt
            FROM brand_candidates
            GROUP BY discovery_source
            ORDER BY last_hunt DESC
            LIMIT 10
        ''')
        recent_hunts = cursor.fetchall()

        conn.close()

        return jsonify({
            'total_candidates': stats['total'],
            'pending': stats['pending'],
            'approved': stats['approved'],
            'rejected': stats['rejected'],
            'avg_verification_score': float(stats['avg_score']) if stats['avg_score'] else 0,
            'recent_hunts': recent_hunts
        }), 200

    except Exception as e:
        return jsonify({'error': str(e)}), 500


# ============================================================================
# UTILITY FUNCTIONS
# ============================================================================

def create_slug(brand_name: str) -> str:
    """Create URL-friendly slug from brand name"""
    import re
    slug = brand_name.lower()
    slug = re.sub(r'[^a-z0-9]+', '-', slug)
    slug = slug.strip('-')
    return slug
