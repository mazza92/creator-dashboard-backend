"""Creator activity feed (the bell in the header).

GET  /api/activity        {items: [...], unread, seen_at, pulse: {day, niche, count} | null}
POST /api/activity/seen   opening the bell: everything up to now is read
"""
from flask import Blueprint, jsonify

activity_bp = Blueprint("activity", __name__, url_prefix="/api/activity")


def _creator():
    from routes.polly import _creator_auth
    return _creator_auth()


@activity_bp.route("", methods=["GET"])
def get_activity():
    from services.creator_activity import feed

    creator_id, conn, _c = _creator()
    if not creator_id:
        return jsonify({"success": False, "error": "Authentication required"}), 401
    try:
        return jsonify({"success": True, **feed(conn, creator_id)})
    finally:
        conn.close()


@activity_bp.route("/seen", methods=["POST"])
def post_seen():
    from services.creator_activity import mark_seen

    creator_id, conn, _c = _creator()
    if not creator_id:
        return jsonify({"success": False, "error": "Authentication required"}), 401
    try:
        mark_seen(conn, creator_id)
        return jsonify({"success": True})
    finally:
        conn.close()
