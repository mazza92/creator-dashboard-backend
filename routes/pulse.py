"""UGC Pulse API.

GET  /api/pulse?niche=&view=today|week|saved&offset=   creator session
POST /api/pulse/save   {item_id, saved: true|false}     creator session
GET|POST /api/pulse/cron/build   hourly at :50; builds each niche once a day from our Meta ad samples (cron secret; ?niche=, ?day=)
POST /api/pulse/ingest           Hermes social agent items (cron secret): {items: [...], day?: YYYY-MM-DD}
"""
from datetime import date

from flask import Blueprint, jsonify, request

pulse_bp = Blueprint("pulse", __name__, url_prefix="/api/pulse")


def _json_safe(x):
    from routes.polly import json_safe
    return json_safe(x)


def _is_pro(conn, creator_id) -> bool:
    cur = conn.cursor()
    cur.execute("""SELECT (LOWER(COALESCE(subscription_tier, '')) = 'pro' OR COALESCE(unlocks_tier, '') = 'pro')
                   FROM creators WHERE id = %s""", (creator_id,))
    row = cur.fetchone()
    return bool(row and (row[0] if not isinstance(row, dict) else list(row.values())[0]))


def _available_niches(conn):
    cur = conn.cursor()
    cur.execute("""SELECT niche, COUNT(*) FROM ugc_pulse_items WHERE kind = 'hook' AND niche <> 'all'
                   AND day > CURRENT_DATE - 14 GROUP BY niche ORDER BY 2 DESC""")
    return [r[0] for r in cur.fetchall()]


@pulse_bp.route("", methods=["GET"])
def get_pulse():
    from routes.polly import _creator_auth
    from services import ugc_pulse
    from services.creator_niches import recorded_niches

    creator_id, conn, creator = _creator_auth()
    if not creator_id:
        return jsonify({"success": False, "error": "Authentication required"}), 401
    try:
        ugc_pulse.ensure_tables(conn)
        is_pro = _is_pro(conn, creator_id)
        mine = recorded_niches(creator) or []
        available = _available_niches(conn)
        niche = (request.args.get("niche") or "").strip().lower()
        if not niche:
            niche = next((n for n in mine if n in available), None) or (mine[0] if mine else None) \
                or (available[0] if available else "skincare")
        view = (request.args.get("view") or "today").lower()
        base = {"success": True, "is_pro": is_pro, "niche": niche, "my_niches": mine, "available_niches": available,
                "view": view}
        if view == "saved":
            return jsonify(_json_safe({**base, **ugc_pulse.saved_view(conn, creator_id)}))
        if view == "week":
            try:
                offset = max(0, min(83, int(request.args.get("offset") or 0)))
            except ValueError:
                offset = 0
            return jsonify(_json_safe({**base, **ugc_pulse.week_view(conn, niche, creator_id, is_pro,
                                                                      offset_days=offset)}))
        return jsonify(_json_safe({**base, **ugc_pulse.day_view(conn, niche, creator_id, is_pro)}))
    finally:
        conn.close()


@pulse_bp.route("/save", methods=["POST"])
def save():
    from routes.polly import _creator_auth
    from services import ugc_pulse

    creator_id, conn, _creator = _creator_auth()
    if not creator_id:
        return jsonify({"success": False, "error": "Authentication required"}), 401
    try:
        body = request.get_json(silent=True) or {}
        try:
            item_id = int(body.get("item_id"))
        except (TypeError, ValueError):
            return jsonify({"success": False, "error": "item_id required"}), 400
        ugc_pulse.set_saved(conn, creator_id, item_id, body.get("saved", True) is not False)
        return jsonify({"success": True, "item_id": item_id, "saved": body.get("saved", True) is not False})
    finally:
        conn.close()


def _day_arg():
    raw = (request.args.get("day") or (request.get_json(silent=True) or {}).get("day") or "").strip()
    try:
        return date.fromisoformat(raw) if raw else None
    except ValueError:
        return None


@pulse_bp.route("/cron/build", methods=["GET", "POST"])
def cron_build():
    from pr_crm_routes import get_db_connection
    from services.admin_auth import cron_request_ok
    from services import ugc_pulse

    if not cron_request_ok():
        return jsonify({"success": False, "error": "unauthorized"}), 401
    conn = get_db_connection()
    try:
        niche = (request.args.get("niche") or "").strip().lower()
        out = ugc_pulse.build_day(conn, day=_day_arg(), niches=[niche] if niche else None)
        return jsonify({"success": True, **out})
    finally:
        conn.close()


@pulse_bp.route("/ingest", methods=["POST"])
def ingest():
    from pr_crm_routes import get_db_connection
    from services.admin_auth import cron_request_ok
    from services import ugc_pulse

    if not cron_request_ok():
        return jsonify({"success": False, "error": "unauthorized"}), 401
    body = request.get_json(silent=True) or {}
    items = body.get("items")
    if not isinstance(items, list) or not items:
        return jsonify({"success": False, "error": "items required"}), 400
    conn = get_db_connection()
    try:
        return jsonify({"success": True, **ugc_pulse.ingest(conn, items[:200], day=_day_arg())})
    finally:
        conn.close()
