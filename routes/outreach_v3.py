"""Scan-led outreach (pipeline v3) for the Hermes brand agent and the admin QA panel. Admin token only.

Sends default to the team inbox (test mode). See services/outreach_v3.py for the live switches.
"""
from flask import Blueprint, jsonify, request
from psycopg2.extras import RealDictCursor

from routes.admin_email import admin_required
from services import outreach_v3 as v3

outreach_v3_bp = Blueprint("outreach_v3", __name__)
MAX_PREPARE = 10


def _conn():
    from pr_crm_routes import get_db_connection  # tuple rows: the Brand Scan code expects them

    return get_db_connection()


def _redis():
    from routes.brand_scan import _redis as r

    return r()


def _body():
    return request.get_json(silent=True) or {}


def _guard(fn):
    conn = _conn()
    try:
        return fn(conn)
    except Exception as err:
        conn.rollback()
        print(f"[outreach-v3] failed: {type(err).__name__}: {err!r}")
        return jsonify({"success": False, "error": str(err)[:300]}), 500
    finally:
        conn.close()


@outreach_v3_bp.route("/api/admin/outreach-v3/prepare", methods=["POST"])
@admin_required
def prepare():
    """{brand_ids: [..] (max 10), force?: bool}. Call again for 'scanning' ones until ready / no_evidence."""
    body = _body()
    ids = [int(i) for i in (body.get("brand_ids") or ([body["brand_id"]] if body.get("brand_id") else []))][:MAX_PREPARE]
    if not ids:
        return jsonify({"success": False, "error": "brand_ids is required"}), 400

    def go(conn):
        out = []
        for bid in ids:
            try:
                out.append(v3.prepare(conn, bid, force=bool(body.get("force")), redis_client=_redis()))
            except Exception as err:
                conn.rollback()
                out.append({"brand_id": bid, "status": "error", "reason": f"{type(err).__name__}: {err}"[:300]})
        return jsonify({"success": True, "results": out})
    return _guard(go)


@outreach_v3_bp.route("/api/admin/outreach-v3/preview/<int:brand_id>", methods=["GET"])
@admin_required
def preview(brand_id):
    step = request.args.get("step", 0, type=int)

    def go(conn):
        email = v3.render(conn, brand_id, step)
        state = v3.public_state(v3._state(conn.cursor(cursor_factory=RealDictCursor), brand_id))
        return jsonify({"success": bool(email.get("ok")), "email": email, "state": state})
    return _guard(go)


@outreach_v3_bp.route("/api/admin/outreach-v3/send", methods=["POST"])
@admin_required
def send():
    """{brand_id, step (0-3), test?: bool}. Test mode unless OUTREACH_V3_LIVE=1 and test is false."""
    body = _body()
    if not body.get("brand_id"):
        return jsonify({"success": False, "error": "brand_id is required"}), 400

    def go(conn):
        out = v3.send(conn, int(body["brand_id"]), int(body.get("step") or 0),
                      test=body.get("test") if "test" in body else None)
        return jsonify({"success": bool(out.get("ok")), **out}), (200 if out.get("ok") else 409)
    return _guard(go)


@outreach_v3_bp.route("/api/admin/outreach-v3/queue/ready", methods=["GET"])
@admin_required
def queue_ready():
    return _guard(lambda conn: jsonify({"success": True, "brands": v3.queue_ready(conn, request.args.get("limit", 40, type=int))}))


@outreach_v3_bp.route("/api/admin/outreach-v3/queue/followups", methods=["GET"])
@admin_required
def queue_followups():
    return _guard(lambda conn: jsonify({"success": True, "items": v3.queue_followups(conn, request.args.get("limit", 100, type=int))}))


@outreach_v3_bp.route("/api/admin/outreach-v3/report", methods=["GET"])
@admin_required
def report():
    return _guard(lambda conn: jsonify({"success": True, **v3.report(conn, request.args.get("days", 30, type=int))}))
