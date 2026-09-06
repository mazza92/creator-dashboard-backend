"""
Creator Approval System Routes
Handles waitlist, approval queue, and admin approval workflow
"""

from flask import Blueprint, request, jsonify
from flask_jwt_extended import jwt_required, get_jwt_identity
from psycopg2.extras import RealDictCursor
import json
from datetime import datetime
import os
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from jinja2 import Environment, FileSystemLoader

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
@jwt_required()
def get_approval_status():
    """
    Get creator's current approval status and queue position.
    Called by Waitlist.js every 30 seconds to check for approval.
    """
    try:
        user_id = get_jwt_identity()
        conn = get_db_connection()
        cursor = conn.cursor(cursor_factory=RealDictCursor)

        # Get creator's approval data
        cursor.execute("""
            SELECT c.approval_status, c.approval_queue_position, c.waitlist_joined_at,
                   c.rejection_reason
            FROM creators c
            WHERE c.user_id = %s
        """, (user_id,))

        creator = cursor.fetchone()
        cursor.close()
        conn.close()

        if not creator:
            return jsonify({"error": "Creator not found"}), 404

        # Calculate estimated wait (20 approvals per day)
        estimated_days = 0
        if creator['approval_queue_position']:
            estimated_days = max(1, creator['approval_queue_position'] // 20)

        return jsonify({
            "status": creator['approval_status'],
            "queue_position": creator['approval_queue_position'],
            "estimated_wait_days": estimated_days,
            "waitlist_joined_at": creator['waitlist_joined_at'].isoformat() if creator['waitlist_joined_at'] else None,
            "can_skip_with_pro": creator['approval_status'] == 'pending',
            "rejection_reason": creator['rejection_reason']
        }), 200

    except Exception as e:
        print(f"Error fetching approval status: {e}")
        return jsonify({"error": "Failed to fetch approval status"}), 500


@creator_approval_bp.route('/api/user/track-waitlist-view', methods=['POST'])
@jwt_required()
def track_waitlist_view():
    """
    Track when a creator first views the waitlist page.
    Sets waitlist_joined_at timestamp for analytics.
    """
    try:
        user_id = get_jwt_identity()
        conn = get_db_connection()
        cursor = conn.cursor()

        # Only set if not already set
        cursor.execute("""
            UPDATE creators
            SET waitlist_joined_at = NOW()
            WHERE user_id = %s
              AND waitlist_joined_at IS NULL
        """, (user_id,))

        conn.commit()
        cursor.close()
        conn.close()

        return jsonify({"success": True}), 200

    except Exception as e:
        print(f"Error tracking waitlist view: {e}")
        return jsonify({"error": "Failed to track waitlist view"}), 500


# =============================================================================
# ADMIN ENDPOINTS - Approval Queue Management
# =============================================================================

@creator_approval_bp.route('/api/admin/approval-queue', methods=['GET'])
@jwt_required()
def get_approval_queue():
    """
    Get pending creators for admin review.
    Requires admin role.

    Query params:
    - limit: Number of results (default 50)
    - offset: Pagination offset (default 0)
    - sort: 'oldest' or 'newest' (default 'oldest')
    - filter_niche: Filter by niche (optional)
    """
    try:
        user_id = get_jwt_identity()
        conn = get_db_connection()
        cursor = conn.cursor(cursor_factory=RealDictCursor)

        # Check if user is admin
        cursor.execute("SELECT user_role FROM users WHERE id = %s", (user_id,))
        user = cursor.fetchone()

        if not user or user['user_role'] != 'admin':
            cursor.close()
            conn.close()
            return jsonify({"error": "Unauthorized - admin access required"}), 403

        # Get query parameters
        limit = int(request.args.get('limit', 50))
        offset = int(request.args.get('offset', 0))
        sort = request.args.get('sort', 'oldest')
        filter_niche = request.args.get('filter_niche')

        order = 'created_at ASC' if sort == 'oldest' else 'created_at DESC'

        query = """
            SELECT c.id as creator_id, c.username, u.email, c.platform, c.follower_count,
                   c.niches, c.bio, c.created_at, c.approval_queue_position
            FROM creators c
            JOIN users u ON c.user_id = u.id
            WHERE c.approval_status = 'pending'
        """

        params = []
        if filter_niche:
            query += " AND %s = ANY(c.niches)"
            params.append(filter_niche)

        query += f" ORDER BY {order} LIMIT %s OFFSET %s"
        params.extend([limit, offset])

        cursor.execute(query, tuple(params))
        creators = cursor.fetchall()

        # Get total count
        count_query = "SELECT COUNT(*) as total FROM creators WHERE approval_status = 'pending'"
        if filter_niche:
            count_query += " AND %s = ANY(niches)"
            cursor.execute(count_query, (filter_niche,))
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
@jwt_required()
def approve_creator(creator_id):
    """
    Approve a creator from the pending queue.

    Body:
    - send_email: bool (default True)
    - note: string (optional admin note)
    """
    try:
        admin_user_id = get_jwt_identity()
        data = request.get_json() or {}
        send_email = data.get('send_email', True)
        note = data.get('note', '')

        conn = get_db_connection()
        cursor = conn.cursor(cursor_factory=RealDictCursor)

        # Check if user is admin
        cursor.execute("SELECT user_role FROM users WHERE id = %s", (admin_user_id,))
        user = cursor.fetchone()

        if not user or user['user_role'] != 'admin':
            cursor.close()
            conn.close()
            return jsonify({"error": "Unauthorized - admin access required"}), 403

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
@jwt_required()
def reject_creator(creator_id):
    """
    Reject a creator from the pending queue.

    Body:
    - reason: string (required)
    - send_email: bool (default True)
    """
    try:
        admin_user_id = get_jwt_identity()
        data = request.get_json() or {}
        reason = data.get('reason', 'Application does not meet our criteria')
        send_email = data.get('send_email', True)

        conn = get_db_connection()
        cursor = conn.cursor(cursor_factory=RealDictCursor)

        # Check if user is admin
        cursor.execute("SELECT user_role FROM users WHERE id = %s", (admin_user_id,))
        user = cursor.fetchone()

        if not user or user['user_role'] != 'admin':
            cursor.close()
            conn.close()
            return jsonify({"error": "Unauthorized - admin access required"}), 403

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

def send_approval_email(email, username):
    """Send approval notification email to creator"""
    try:
        smtp_server = os.getenv('SMTP_SERVER', 'smtp.gmail.com')
        smtp_port = int(os.getenv('SMTP_PORT', 587))
        smtp_username = os.getenv('SMTP_USERNAME')
        smtp_password = os.getenv('SMTP_PASSWORD')

        if not smtp_username or not smtp_password:
            print("⚠️  SMTP credentials not set, skipping approval email")
            return False

        subject = "🎉 You're approved! Welcome to Newcollab"

        html_content = f"""
        <html>
        <body style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; padding: 20px; max-width: 600px;">
            <h2 style="color: #10b981;">🎉 You're in!</h2>
            <p>Hi {username},</p>
            <p>Great news — your creator profile has been approved!</p>
            <p>You now have full access to browse 2,000+ gifting brands, submit PR requests, and land your first free product.</p>
            <p style="margin: 30px 0;">
                <a href="https://app.newcollab.co/creator/dashboard/for-you"
                   style="background: linear-gradient(135deg, #ec4899 0%, #db2777 100%); color: white; padding: 14px 28px; text-decoration: none; border-radius: 8px; display: inline-block; font-weight: 600;">
                    Start browsing brands →
                </a>
            </p>
            <p style="color: #6b7280; font-size: 14px;">
                Need help getting started? Reply to this email or reach out to team@newcollab.co
            </p>
        </body>
        </html>
        """

        msg = MIMEMultipart('alternative')
        msg['Subject'] = subject
        msg['From'] = smtp_username
        msg['To'] = email
        msg.attach(MIMEText(html_content, 'html'))

        with smtplib.SMTP(smtp_server, smtp_port) as server:
            server.starttls()
            server.login(smtp_username, smtp_password)
            server.send_message(msg)

        print(f"✅ Approval email sent to {email}")
        return True

    except Exception as e:
        print(f"❌ Error sending approval email: {e}")
        return False


def send_rejection_email(email, username, reason):
    """Send rejection notification email to creator"""
    try:
        smtp_server = os.getenv('SMTP_SERVER', 'smtp.gmail.com')
        smtp_port = int(os.getenv('SMTP_PORT', 587))
        smtp_username = os.getenv('SMTP_USERNAME')
        smtp_password = os.getenv('SMTP_PASSWORD')

        if not smtp_username or not smtp_password:
            print("⚠️  SMTP credentials not set, skipping rejection email")
            return False

        subject = "Update on your Newcollab application"

        html_content = f"""
        <html>
        <body style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; padding: 20px; max-width: 600px;">
            <h2 style="color: #374151;">Update on your application</h2>
            <p>Hi {username},</p>
            <p>Thank you for your interest in Newcollab. After reviewing your profile, we're unable to approve your creator account at this time.</p>
            <p style="background: #fef2f2; border-left: 3px solid #ef4444; padding: 15px; margin: 20px 0;">
                <strong>Reason:</strong><br>
                {reason}
            </p>
            <p>We maintain high standards to ensure quality matches between brands and creators. We encourage you to continue growing your content and audience.</p>
            <p style="margin: 30px 0;">
                Questions? <a href="mailto:team@newcollab.co" style="color: #ec4899; text-decoration: none; font-weight: 600;">Reach out to our team</a>
            </p>
            <p style="color: #6b7280; font-size: 14px;">
                Best,<br>
                The Newcollab Team
            </p>
        </body>
        </html>
        """

        msg = MIMEMultipart('alternative')
        msg['Subject'] = subject
        msg['From'] = smtp_username
        msg['To'] = email
        msg.attach(MIMEText(html_content, 'html'))

        with smtplib.SMTP(smtp_server, smtp_port) as server:
            server.starttls()
            server.login(smtp_username, smtp_password)
            server.send_message(msg)

        print(f"✅ Rejection email sent to {email}")
        return True

    except Exception as e:
        print(f"❌ Error sending rejection email: {e}")
        return False
