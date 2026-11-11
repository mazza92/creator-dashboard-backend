"""Admin API: Meta-ads brand quality gate review (API only; the admin UI comes later).

Auth: same as routes/admin_brands.py (X-Admin-Token header, or a session user listed in ADMIN_USER_IDS).

GET  /api/admin/brand-quality                     latest gate batch: ?run=<run_id>|latest|all  ?status=pass|fail|all
                                                  ?check=<failing check>  ?q=<name>  ?limit=100  ?offset=0
GET  /api/admin/brand-quality/summary             counts for the latest batch: passed, failed, per failing check
GET  /api/admin/brand-quality/<brand_id>          one brand: gate result, checks, ad signal, override
POST /api/admin/brand-quality/<brand_id>/approve  {"note": "..."}  manual approve (wins over the checks)
POST /api/admin/brand-quality/<brand_id>/reject   {"note": "..."}  manual reject (wins over the checks)
POST /api/admin/brand-quality/<brand_id>/clear    remove the manual decision, back to the checks
POST /api/admin/brand-quality/<brand_id>/rerun    re-run the gate for one brand
POST /api/admin/brand-quality/rerun               {"brand_ids": [..]} optional; default every published meta brand
"""

from flask import Blueprint, jsonify, request, session

from routes.admin_brands import admin_required

admin_brand_quality_bp = Blueprint("admin_brand_quality", __name__, url_prefix="/api/admin/brand-quality")


def _conn():
    from pr_crm_routes import get_db_connection
    return get_db_connection()


def _safe(value):
    from routes.polly import json_safe
    return json_safe(value)


def _who():
    return str(session.get("user_id") or "admin-token")


def _latest_run(cur):
    cur.execute("""SELECT run_id FROM brand_quality_checks WHERE run_id IS NOT NULL AND run_id NOT LIKE 'single-%'
                   ORDER BY checked_at DESC LIMIT 1""")
    row = cur.fetchone()
    return row["run_id"] if row else None


_LIST_SQL = """
    SELECT b.id AS brand_id, b.brand_name, b.website, b.category, b.contact_email, b.email_status, b.status,
           b.source, b.logo_url, q.passed, q.auto_passed, q.reasons, q.checks, q.run_id, q.checked_at, q.override,
           q.override_note, q.override_by, q.override_at, s.active_ad_count, s.ads_first_seen, s.ads_last_seen,
           s.ad_library_url, s.ugc_style, s.ugc_style_reason, s.ugc_style_source, s.ad_samples, s.signal_source,
           s.ad_checked_at
    FROM brand_quality_checks q
    JOIN pr_brands b ON b.id = q.brand_id
    LEFT JOIN brand_ad_signals s ON s.brand_id = q.brand_id
"""


@admin_brand_quality_bp.route("", methods=["GET"])
@admin_required
def list_checks():
    from psycopg2.extras import RealDictCursor
    from services.brand_quality_gate import _ready

    conn = _conn()
    try:
        _ready(conn)
        cur = conn.cursor(cursor_factory=RealDictCursor)
        run = (request.args.get("run") or "latest").strip()
        status = (request.args.get("status") or "all").strip().lower()
        check = (request.args.get("check") or "").strip()
        q = (request.args.get("q") or "").strip()
        try:
            limit = max(1, min(500, int(request.args.get("limit") or 100)))
            offset = max(0, int(request.args.get("offset") or 0))
        except ValueError:
            return jsonify({"success": False, "error": "limit/offset must be numbers"}), 400
        where, params = [], []
        if run == "latest":
            run = _latest_run(cur)
            # Brands re-gated one by one since the batch (or overridden) still belong in the review list.
            if run:
                where.append("(q.run_id = %s OR q.run_id LIKE 'single-%%' OR q.override IS NOT NULL)")
                params.append(run)
        elif run != "all":
            where.append("q.run_id = %s")
            params.append(run)
        if status in ("pass", "passed"):
            where.append("q.passed")
        elif status in ("fail", "failed"):
            where.append("NOT q.passed")
        if check:
            where.append("(q.checks -> %s ->> 'ok') = 'false'")
            params.append(check)
        if q:
            where.append("b.brand_name ILIKE %s")
            params.append(f"%{q}%")
        clause = (" WHERE " + " AND ".join(where)) if where else ""
        cur.execute(f"SELECT COUNT(*) AS n FROM brand_quality_checks q JOIN pr_brands b ON b.id = q.brand_id{clause}",
                    params)
        total = cur.fetchone()["n"]
        cur.execute(_LIST_SQL + clause + " ORDER BY q.passed DESC, s.active_ad_count DESC NULLS LAST, b.id LIMIT %s OFFSET %s",
                    params + [limit, offset])
        return jsonify({"success": True, "run_id": run, "total": total, "items": _safe([dict(r) for r in cur.fetchall()])})
    finally:
        conn.close()


@admin_brand_quality_bp.route("/summary", methods=["GET"])
@admin_required
def summary():
    from psycopg2.extras import RealDictCursor
    from services.brand_quality_gate import CHECK_ORDER, _ready

    conn = _conn()
    try:
        _ready(conn)
        cur = conn.cursor(cursor_factory=RealDictCursor)
        run = request.args.get("run") or _latest_run(cur)
        cur.execute("""SELECT COUNT(*) AS checked, COUNT(*) FILTER (WHERE passed) AS passed,
                              COUNT(*) FILTER (WHERE override = 'approved') AS approved,
                              COUNT(*) FILTER (WHERE override = 'rejected') AS rejected,
                              MAX(checked_at) AS checked_at
                       FROM brand_quality_checks WHERE run_id = %s""", (run,))
        out = dict(cur.fetchone() or {})
        failing = {}
        for key in CHECK_ORDER:
            cur.execute("SELECT COUNT(*) AS n FROM brand_quality_checks WHERE run_id = %s AND (checks -> %s ->> 'ok') = 'false'",
                        (run, key))
            failing[key] = cur.fetchone()["n"]
        return jsonify({"success": True, "run_id": run, **_safe(out), "failing_checks": failing})
    finally:
        conn.close()


@admin_brand_quality_bp.route("/<int:brand_id>", methods=["GET"])
@admin_required
def one(brand_id):
    from psycopg2.extras import RealDictCursor
    from services.brand_quality_gate import _ready

    conn = _conn()
    try:
        _ready(conn)
        cur = conn.cursor(cursor_factory=RealDictCursor)
        cur.execute(_LIST_SQL + " WHERE q.brand_id = %s", (brand_id,))
        row = cur.fetchone()
        if not row:
            return jsonify({"success": False, "error": "not gated yet; POST /rerun first"}), 404
        return jsonify({"success": True, "item": _safe(dict(row))})
    finally:
        conn.close()


def _decide(brand_id, decision):
    from services.brand_quality_gate import set_override

    note = ((request.get_json(silent=True) or {}).get("note") or "").strip()[:500] or None
    conn = _conn()
    try:
        result = set_override(conn, brand_id, decision, note=note, by=_who())
        return jsonify({"success": True, "brand_id": brand_id, "override": decision, **_safe(result)})
    finally:
        conn.close()


@admin_brand_quality_bp.route("/<int:brand_id>/approve", methods=["POST"])
@admin_required
def approve(brand_id):
    return _decide(brand_id, "approved")


@admin_brand_quality_bp.route("/<int:brand_id>/reject", methods=["POST"])
@admin_required
def reject(brand_id):
    return _decide(brand_id, "rejected")


@admin_brand_quality_bp.route("/<int:brand_id>/clear", methods=["POST"])
@admin_required
def clear(brand_id):
    return _decide(brand_id, None)


@admin_brand_quality_bp.route("/<int:brand_id>/rerun", methods=["POST"])
@admin_required
def rerun_one(brand_id):
    from services.brand_quality_gate import evaluate

    conn = _conn()
    try:
        return jsonify({"success": True, "brand_id": brand_id, **_safe(evaluate(conn, brand_id))})
    finally:
        conn.close()


@admin_brand_quality_bp.route("/rerun", methods=["POST"])
@admin_required
def rerun_all():
    from services.brand_quality_gate import run_gate

    body = request.get_json(silent=True) or {}
    ids = body.get("brand_ids")
    try:
        ids = [int(i) for i in ids] if ids else None
    except (TypeError, ValueError):
        return jsonify({"success": False, "error": "brand_ids must be numbers"}), 400
    conn = _conn()
    try:
        return jsonify({"success": True, **_safe(run_gate(conn, brand_ids=ids))})
    finally:
        conn.close()
