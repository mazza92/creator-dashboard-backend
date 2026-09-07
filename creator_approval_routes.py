"""
Creator Approval System Routes
Handles waitlist, approval queue, and admin approval workflow
"""

from flask import Blueprint, request, jsonify, session
from psycopg2.extras import RealDictCursor
import json
import os

creator_approval_bp = Blueprint('creator_approval', __name__)

# Import database connection from app.py (will be passed as dependency)
get_db_connection = None

def init_approval_routes(db_connection_func):
    """Initialize the routes with database connection function"""
    global get_db_connection
    get_db_connection = db_connection_func


# =============================================================================
# USER ENDPOINTS - Creator Approval Status
# =============================================================================

@creator_approval_bp.route('/api/user/approval-status', methods=['GET'])
def get_approval_status():
    """
    Get creator's current approval status and queue position.
    Called by Waitlist.js every 30 seconds to check for approval.
    """
    try:
        user_id = session.get('user_id')
        if not user_id:
            return jsonify({"error": "Not authenticated"}), 401

        conn = get_db_connection()
        cursor = conn.cursor(cursor_factory=RealDictCursor)

        # Get creator's approval data
        cursor.execute("""
            SELECT c.id, c.approval_status, c.approval_queue_position, c.waitlist_joined_at,
                   c.rejection_reason, c.created_at
            FROM creators c
            WHERE c.user_id = %s
        """, (user_id,))

        creator = cursor.fetchone()

        if not creator:
            cursor.close()
            conn.close()
            return jsonify({"error": "Creator not found"}), 404

        # Calculate queue position dynamically if pending
        queue_position = creator['approval_queue_position']
        if creator['approval_status'] == 'pending':
            cursor.execute("""
                SELECT COUNT(*) + 1 as position
                FROM creators
                WHERE approval_status = 'pending'
                  AND created_at < %s
            """, (creator['created_at'],))
            result = cursor.fetchone()
            queue_position = result['position'] if result else 1

        cursor.close()
        conn.close()

        # Calculate estimated wait (20 approvals per day)
        estimated_days = 0
        if queue_position:
            estimated_days = max(1, queue_position // 20)

        return jsonify({
            "status": creator['approval_status'],
            "queue_position": queue_position,
            "estimated_wait_days": estimated_days,
            "waitlist_joined_at": creator['waitlist_joined_at'].isoformat() if creator['waitlist_joined_at'] else None,
            "can_skip_with_pro": creator['approval_status'] == 'pending',
            "rejection_reason": creator['rejection_reason']
        }), 200

    except Exception as e:
        print(f"Error fetching approval status: {e}")
        return jsonify({"error": "Failed to fetch approval status"}), 500


@creator_approval_bp.route('/api/user/track-waitlist-view', methods=['POST'])
def track_waitlist_view():
    """
    Track when a creator first views the waitlist page.
    Sets waitlist_joined_at timestamp for analytics.
    """
    try:
        user_id = session.get('user_id')
        if not user_id:
            return jsonify({"error": "Not authenticated"}), 401

        conn = get_db_connection()
        cursor = conn.cursor(cursor_factory=RealDictCursor)

        cursor.execute("""
            UPDATE creators
            SET waitlist_joined_at = NOW()
            WHERE user_id = %s
              AND waitlist_joined_at IS NULL
              AND COALESCE(approval_status, 'pending') = 'pending'
            RETURNING id, username, user_id
        """, (user_id,))
        joined = cursor.fetchone()
        emailed = False
        if joined:
            cursor.execute("SELECT email, first_name FROM users WHERE id = %s", (user_id,))
            user = cursor.fetchone() or {}
            conn.commit()
            from waitlist_emails import send_waitlist_email
            emailed = send_waitlist_email(
                'joined',
                email=user.get('email'),
                name=user.get('first_name') or joined.get('username'),
                user_id=user_id,
            )
            if emailed:
                cursor.execute("""
                    UPDATE creators SET last_any_email_sent = NOW() WHERE id = %s
                """, (joined['id'],))
                conn.commit()
        else:
            conn.commit()

        cursor.close()
        conn.close()

        return jsonify({"success": True, "emailed": emailed}), 200

    except Exception as e:
        print(f"Error tracking waitlist view: {e}")
        return jsonify({"error": "Failed to track waitlist view"}), 500


# =============================================================================
# ADMIN ENDPOINTS - Approval Queue Management
# =============================================================================

ADMIN_TOKEN = 'pr-hunter-admin-2026'


def _admin_authorized():
    """Same gate as /api/admin/creators: X-Admin-Token or team@ session."""
    if request.headers.get('X-Admin-Token') == ADMIN_TOKEN:
        return True
    user_id = session.get('user_id')
    if not user_id or not get_db_connection:
        return False
    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    cursor.execute("SELECT email, user_role FROM users WHERE id = %s", (user_id,))
    user = cursor.fetchone()
    cursor.close()
    conn.close()
    if not user:
        return False
    email = (user.get('email') or '').lower()
    return email == 'team@newcollab.co' or user.get('user_role') == 'admin'


@creator_approval_bp.route('/api/admin/approval-queue', methods=['GET'])
def get_approval_queue():
    """
    Get pending creators for admin review.
    Requires admin token or admin session.

    Query params:
    - limit: Number of results (default 50)
    - offset: Pagination offset (default 0)
    - sort: 'oldest' or 'newest' (default 'oldest')
    - filter_niche: Filter by niche (optional)
    """
    try:
        if not _admin_authorized():
            return jsonify({"error": "Unauthorized - admin access required"}), 403

        conn = get_db_connection()
        cursor = conn.cursor(cursor_factory=RealDictCursor)

        # Get query parameters
        limit = int(request.args.get('limit', 50))
        offset = int(request.args.get('offset', 0))
        sort = request.args.get('sort', 'oldest')
        filter_niche = request.args.get('filter_niche')

        order = 'c.created_at ASC' if sort == 'oldest' else 'c.created_at DESC'

        query = """
            SELECT c.id as creator_id, c.username, u.email, u.first_name,
                   c.social_platform, c.social_handle, c.followers_count,
                   c.social_follower_count, c.niche, c.bio, c.image_profile,
                   c.created_at, c.waitlist_joined_at, c.approval_queue_position
            FROM creators c
            JOIN users u ON c.user_id = u.id
            WHERE c.approval_status = 'pending'
        """

        params = []
        if filter_niche:
            query += " AND COALESCE(c.niche, '') ILIKE %s"
            params.append(f'%{filter_niche}%')

        query += f" ORDER BY {order} LIMIT %s OFFSET %s"
        params.extend([limit, offset])

        cursor.execute(query, tuple(params))
        creators = cursor.fetchall()

        # Get total count
        count_query = "SELECT COUNT(*) as total FROM creators WHERE approval_status = 'pending'"
        if filter_niche:
            count_query += " AND COALESCE(niche, '') ILIKE %s"
            cursor.execute(count_query, (f'%{filter_niche}%',))
        else:
            cursor.execute(count_query)

        total = cursor.fetchone()['total']

        cursor.close()
        conn.close()

        return jsonify({
            "creators": [dict(c) for c in creators],
            "total": total,
            "limit": limit,
            "offset": offset
        }), 200

    except Exception as e:
        print(f"Error fetching approval queue: {e}")
        return jsonify({"error": "Failed to fetch approval queue"}), 500


@creator_approval_bp.route('/api/admin/approve-creator/<int:creator_id>', methods=['POST'])
def approve_creator(creator_id):
    """
    Approve a creator from the pending queue.

    Body:
    - send_email: bool (default True)
    - note: string (optional admin note)
    """
    try:
        data = request.get_json() or {}
        send_email = data.get('send_email', True)
        note = data.get('note', '')

        if not _admin_authorized():
            return jsonify({"error": "Unauthorized - admin access required"}), 403

        conn = get_db_connection()
        cursor = conn.cursor(cursor_factory=RealDictCursor)
        admin_user_id = session.get('user_id')
        if not admin_user_id:
            cursor.execute("SELECT id FROM users WHERE LOWER(email) = 'team@newcollab.co' LIMIT 1")
            row = cursor.fetchone()
            admin_user_id = row['id'] if row else None

        # Update creator approval status
        cursor.execute("""
            UPDATE creators
            SET approval_status = 'approved',
                approved_at = NOW(),
                approved_by = %s
            WHERE id = %s AND approval_status = 'pending'
            RETURNING id
        """, (admin_user_id, creator_id))

        updated = cursor.fetchone()
        if not updated:
            cursor.close()
            conn.close()
            return jsonify({"error": "Creator not found or already processed"}), 404

        # Log to audit table
        cursor.execute("""
            INSERT INTO creator_approval_audit
            (creator_id, admin_user_id, previous_status, new_status, reason, metadata)
            VALUES (%s, %s, 'pending', 'approved', %s, %s)
        """, (creator_id, admin_user_id, note, json.dumps({"manual_approval": True})))

        conn.commit()

        # Send approval email
        if send_email:
            cursor.execute("""
                SELECT u.email, c.username
                FROM creators c
                JOIN users u ON c.user_id = u.id
                WHERE c.id = %s
            """, (creator_id,))
            creator = cursor.fetchone()

            if creator:
                send_approval_email(creator['email'], creator['username'])

        cursor.close()
        conn.close()

        print(f"✅ Creator {creator_id} approved by admin {admin_user_id}")
        return jsonify({"success": True, "message": "Creator approved"}), 200

    except Exception as e:
        print(f"Error approving creator: {e}")
        return jsonify({"error": "Failed to approve creator"}), 500


@creator_approval_bp.route('/api/admin/reject-creator/<int:creator_id>', methods=['POST'])
def reject_creator(creator_id):
    """
    Reject a creator from the pending queue.

    Body:
    - reason: string (required)
    - send_email: bool (default True)
    """
    try:
        data = request.get_json() or {}
        reason = data.get('reason', 'Application does not meet our criteria')
        send_email = data.get('send_email', True)

        if not _admin_authorized():
            return jsonify({"error": "Unauthorized - admin access required"}), 403

        conn = get_db_connection()
        cursor = conn.cursor(cursor_factory=RealDictCursor)
        admin_user_id = session.get('user_id')
        if not admin_user_id:
            cursor.execute("SELECT id FROM users WHERE LOWER(email) = 'team@newcollab.co' LIMIT 1")
            row = cursor.fetchone()
            admin_user_id = row['id'] if row else None

        # Update creator approval status
        cursor.execute("""
            UPDATE creators
            SET approval_status = 'rejected',
                rejection_reason = %s,
                rejected_at = NOW()
            WHERE id = %s AND approval_status = 'pending'
            RETURNING id
        """, (reason, creator_id))

        updated = cursor.fetchone()
        if not updated:
            cursor.close()
            conn.close()
            return jsonify({"error": "Creator not found or already processed"}), 404

        # Log to audit table
        cursor.execute("""
            INSERT INTO creator_approval_audit
            (creator_id, admin_user_id, previous_status, new_status, reason, metadata)
            VALUES (%s, %s, 'pending', 'rejected', %s, %s)
        """, (creator_id, admin_user_id, reason, json.dumps({"manual_rejection": True})))

        conn.commit()

        # Send rejection email
        if send_email:
            cursor.execute("""
                SELECT u.email, c.username
                FROM creators c
                JOIN users u ON c.user_id = u.id
                WHERE c.id = %s
            """, (creator_id,))
            creator = cursor.fetchone()

            if creator:
                send_rejection_email(creator['email'], creator['username'], reason)

        cursor.close()
        conn.close()

        print(f"🚫 Creator {creator_id} rejected by admin {admin_user_id}: {reason}")
        return jsonify({"success": True, "message": "Creator rejected"}), 200

    except Exception as e:
        print(f"Error rejecting creator: {e}")
        return jsonify({"error": "Failed to reject creator"}), 500


# =============================================================================
# EMAIL FUNCTIONS
# =============================================================================

def send_approval_email(email, username, user_id=None, as_pro=False):
    """Send approval notification using the shared waitlist template."""
    from waitlist_emails import send_waitlist_email
    return send_waitlist_email(
        'approved', email, username, user_id=user_id, as_pro=as_pro,
    )


def send_rejection_email(email, username, reason, user_id=None):
    """Send rejection notification using the shared waitlist template."""
    from waitlist_emails import send_waitlist_email
    return send_waitlist_email(
        'rejected', email, username, user_id=user_id, reason=reason,
    )
