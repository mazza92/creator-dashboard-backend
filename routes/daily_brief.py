"""Daily brief API.

GET  /api/brief/preferences      {daily_brief: daily|weekly|off, is_default, default, timezone, options}
POST /api/brief/preferences      {daily_brief?: daily|weekly|off, timezone?: IANA name (the browser's)}
GET  /api/brief/preview          the signed-in creator's brief for right now (what it would say, or why it skips)
GET|POST /api/brief/cron/send    hourly cron: sends the briefs that are due (?dry_run=1, ?limit=)
"""

from flask import Blueprint, Response, jsonify, request

from services.daily_brief import get_preferences, send_due_briefs, set_preferences

brief_bp = Blueprint("daily_brief", __name__, url_prefix="/api/brief")


def _auth():
    from routes.polly import _creator_auth
    return _creator_auth()


def _is_pro(conn, creator_id):
    from services.creator_tracker import creator_profile
    return bool(creator_profile(conn, creator_id).get("is_pro"))


@brief_bp.route("/preferences", methods=["GET"])
def preferences_get():
    creator_id, conn, _creator = _auth()
    if not creator_id:
        return jsonify({"success": False, "error": "unauthorized"}), 401
    try:
        return jsonify({"success": True, **get_preferences(conn, creator_id, _is_pro(conn, creator_id))})
    finally:
        conn.close()


@brief_bp.route("/preferences", methods=["POST"])
def preferences_post():
    creator_id, conn, _creator = _auth()
    if not creator_id:
        return jsonify({"success": False, "error": "unauthorized"}), 401
    body = request.get_json(silent=True) or {}
    try:
        set_preferences(conn, creator_id, mode=body.get("daily_brief"), tz_name=body.get("timezone"))
        return jsonify({"success": True, **get_preferences(conn, creator_id, _is_pro(conn, creator_id))})
    except ValueError as err:
        conn.rollback()
        return jsonify({"success": False, "error": str(err)}), 400
    finally:
        conn.close()


@brief_bp.route("/preview", methods=["GET"])
def preview():
    """Dry run for the signed-in creator, ignoring the 7am clock: HTML when it would send, JSON when it skips."""
    from datetime import datetime, timezone

    from services.daily_brief import ensure_tables, send_brief

    creator_id, conn, _creator = _auth()
    if not creator_id:
        return jsonify({"success": False, "error": "unauthorized"}), 401
    try:
        ensure_tables(conn)
        res = send_brief(conn, creator_id, now=datetime.now(timezone.utc), dry_run=True, force=True)
        if res.get("html") and request.args.get("format") != "json":
            return Response(res["html"], mimetype="text/html")
        res.pop("html", None)
        res.pop("to", None)
        return jsonify({"success": True, **res})
    finally:
        conn.close()


@brief_bp.route("/cron/send", methods=["GET", "POST"])
def cron_send():
    from pr_crm_routes import get_db_connection
    from services.admin_auth import cron_request_ok
    from services.resend_mail import resend_configured

    if not cron_request_ok():
        return jsonify({"success": False, "error": "unauthorized"}), 401
    dry_run = str(request.args.get("dry_run") or "").lower() in ("1", "true", "yes") or not resend_configured()
    try:
        limit = max(1, min(1000, int(request.args.get("limit") or 300)))
    except (TypeError, ValueError):
        limit = 300
    conn = get_db_connection()
    try:
        out = send_due_briefs(conn, limit=limit, dry_run=dry_run)
        return jsonify({"success": True, "dry_run": dry_run, **out})
    except Exception as err:
        conn.rollback()
        print(f"[daily brief] cron error: {err}")
        return jsonify({"success": False, "error": "brief run failed"}), 500
    finally:
        conn.close()
