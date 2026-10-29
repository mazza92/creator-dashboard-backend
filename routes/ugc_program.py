"""Promote Newcollab: creator apply / dashboard / submit, and the admin review queue (services/ugc_program)."""
from __future__ import annotations

import traceback

from flask import Blueprint, jsonify, request

from pr_crm_routes import get_creator_id_from_session, get_db_connection
from routes.admin_creators import admin_required
from services import ugc_program as up

ugc_program_bp = Blueprint("ugc_program", __name__)


def _with_conn(fn):
    conn = get_db_connection()
    try:
        up.ensure_tables(conn)
        return fn(conn)
    except Exception as err:
        conn.rollback()
        traceback.print_exc()
        return jsonify({"success": False, "error": f"Something went wrong: {type(err).__name__}"}), 500
    finally:
        conn.close()


@ugc_program_bp.route("/api/promote", methods=["GET"])
def promote_dashboard():
    creator_id = get_creator_id_from_session()
    if not creator_id:
        return jsonify({"success": False, "error": "Not authenticated"}), 401
    return _with_conn(lambda conn: jsonify({"success": True, **up.dashboard(conn, creator_id)}))


@ugc_program_bp.route("/api/promote/apply", methods=["POST"])
def promote_apply():
    creator_id = get_creator_id_from_session()
    if not creator_id:
        return jsonify({"success": False, "error": "Not authenticated"}), 401

    def go(conn):
        _row, error = up.apply(conn, creator_id, request.get_json(silent=True) or {})
        if error:
            return jsonify({"success": False, "error": error}), 400
        return jsonify({"success": True, **up.dashboard(conn, creator_id)})
    return _with_conn(go)


@ugc_program_bp.route("/api/promote/payout", methods=["POST"])
def promote_payout():
    creator_id = get_creator_id_from_session()
    if not creator_id:
        return jsonify({"success": False, "error": "Not authenticated"}), 401

    def go(conn):
        error = up.update_payout(conn, creator_id, request.get_json(silent=True) or {})
        if error:
            return jsonify({"success": False, "error": error}), 400
        return jsonify({"success": True, **up.dashboard(conn, creator_id)})
    return _with_conn(go)


@ugc_program_bp.route("/api/promote/videos", methods=["POST"])
def promote_submit():
    creator_id = get_creator_id_from_session()
    if not creator_id:
        return jsonify({"success": False, "error": "Not authenticated"}), 401

    def go(conn):
        _row, error = up.submit_video(conn, creator_id, (request.get_json(silent=True) or {}).get("url"))
        if error:
            return jsonify({"success": False, "error": error}), 400
        return jsonify({"success": True, **up.dashboard(conn, creator_id)})
    return _with_conn(go)


@ugc_program_bp.route("/api/admin/promote", methods=["GET"])
@admin_required
def admin_promote():
    return _with_conn(lambda conn: jsonify({"success": True, **up.admin_overview(conn)}))


@ugc_program_bp.route("/api/admin/promote/members/<int:member_id>", methods=["POST"])
@admin_required
def admin_promote_member(member_id):
    body = request.get_json(silent=True) or {}

    def go(conn):
        row, error = up.decide_member(conn, member_id, str(body.get("action") or ""), body.get("note") or "")
        if error:
            return jsonify({"success": False, "error": error}), 400
        emailed = False
        if body.get("action") == "approve" and body.get("email", True):
            emailed = up.email_approved(conn, row)
        return jsonify({"success": True, "emailed": emailed, **up.admin_overview(conn)})
    return _with_conn(go)


@ugc_program_bp.route("/api/admin/promote/videos/<int:video_id>", methods=["POST"])
@admin_required
def admin_promote_video(video_id):
    body = request.get_json(silent=True) or {}

    def go(conn):
        error = up.review_video(conn, video_id, str(body.get("action") or ""), body.get("note") or "",
                                body.get("payout_ref") or "")
        if error:
            return jsonify({"success": False, "error": error}), 400
        return jsonify({"success": True, **up.admin_overview(conn)})
    return _with_conn(go)


@ugc_program_bp.route("/api/admin/promote/refresh", methods=["POST"])
@admin_required
def admin_promote_refresh():
    return _with_conn(lambda conn: jsonify({"success": True, "refresh": up.refresh(conn), **up.admin_overview(conn)}))
