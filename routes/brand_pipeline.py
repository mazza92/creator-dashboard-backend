"""Brand acquisition pipeline API for the Hermes brand agent (admin token) + brand-facing prefill."""
from flask import Blueprint, jsonify, request

from routes.admin_email import admin_required, get_db_connection
from services import brand_pipeline as bp

brand_pipeline_bp = Blueprint("brand_pipeline", __name__)

MAX_INBOUND_BATCH = 8


def _body():
    return request.get_json(silent=True) or {}


def _run(fn):
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        out = fn(cursor)
        conn.commit()
        return out
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


@brand_pipeline_bp.route("/api/admin/brand-pipeline/queue/new", methods=["GET"])
@admin_required
def queue_new():
    limit = request.args.get("limit", 40, type=int)
    rows = _run(lambda c: bp.queue_new(c, limit))
    return jsonify({"success": True, "total": len(rows), "brands": rows})


@brand_pipeline_bp.route("/api/admin/brand-pipeline/queue/followups", methods=["GET"])
@admin_required
def queue_followups():
    limit = request.args.get("limit", 100, type=int)
    rows = _run(lambda c: bp.queue_followups(c, limit))
    return jsonify({"success": True, "total": len(rows), "items": rows})


@brand_pipeline_bp.route("/api/admin/brand-pipeline/contact", methods=["POST"])
@admin_required
def record_contact():
    data = _body()
    if not data.get("brand_id") or not data.get("email"):
        return jsonify({"error": "brand_id and email are required"}), 400
    out = _run(lambda c: bp.record_contact(
        c, int(data["brand_id"]), data["email"], data.get("name") or "", data.get("role") or "",
        data.get("source") or "hunter", data.get("meta") or {},
    ))
    return jsonify(out), (200 if out.get("ok") else 400)


@brand_pipeline_bp.route("/api/admin/brand-pipeline/skip", methods=["POST"])
@admin_required
def skip_brand():
    data = _body()
    if not data.get("brand_id"):
        return jsonify({"error": "brand_id is required"}), 400

    def go(c):
        bp.skip_brand(c, int(data["brand_id"]), data.get("reason") or "",
                      already_contacted=bool(data.get("already_contacted")))
        return {"ok": True}

    return jsonify(_run(go))


@brand_pipeline_bp.route("/api/admin/brand-pipeline/send", methods=["POST"])
@admin_required
def send():
    data = _body()
    if not data.get("brand_id"):
        return jsonify({"error": "brand_id is required"}), 400
    out = _run(lambda c: bp.send_step(c, int(data["brand_id"]), data.get("kind") or "initial",
                                      dry_run=bool(data.get("dry_run")), test=bool(data.get("test")),
                                      test_to=str(data.get("test_to") or "")))
    return jsonify(out), (200 if out.get("ok") else 409)


@brand_pipeline_bp.route("/api/admin/brand-pipeline/inbound", methods=["POST"])
@admin_required
def inbound():
    data = _body()
    messages = data.get("messages") or []
    if not isinstance(messages, list):
        return jsonify({"error": "messages must be a list"}), 400
    if len(messages) > MAX_INBOUND_BATCH:
        return jsonify({"error": f"max {MAX_INBOUND_BATCH} messages per call"}), 400
    dry_run = bool(data.get("dry_run"))
    results = []
    for msg in messages:
        try:
            results.append(_run(lambda c: bp.ingest_message(c, msg or {}, dry_run=dry_run)))
        except Exception as exc:
            results.append({"action": "error", "message_id": (msg or {}).get("message_id"), "error": str(exc)[:300]})
    return jsonify({"success": True, "results": results})


@brand_pipeline_bp.route("/api/admin/brand-pipeline/report", methods=["GET"])
@admin_required
def report():
    hours = request.args.get("hours", 24, type=int)
    rep = _run(lambda c: bp.report(c, hours))
    rep["daily_new_limit"] = bp.daily_new_limit(rep)
    rep["live"] = bp.outreach_live()
    rep["test_email"] = bp.TEST_EMAIL
    return jsonify({"success": True, **rep})


@brand_pipeline_bp.route("/api/brand-pipeline/public/brief/<ref>", methods=["GET"])
def brief_prefill(ref):
    out = _run(lambda c: bp.brief_prefill(c, ref))
    if not out:
        return jsonify({"success": False}), 404
    return jsonify({"success": True, **out})
