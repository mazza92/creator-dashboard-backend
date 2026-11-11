"""Hot brands API: the daily cron (Meta-ads signals -> quality gate -> one hot brand per creator) and the creator's
own reads.

GET|POST /api/hotlist/cron/daily   cron auth (services.admin_auth.cron_request_ok). Steps, each skippable:
    1. seed ad samples from notes for meta brands with no signal yet   (?seed=0 to skip)
    2. AI-classify inconclusive UGC samples, cached                    (?classify=40 max calls, 0 to skip)
    3. re-run the quality gate on every published meta brand          (?gate=0 to skip)
    4. assign today's hot brand per creator + queue Pro pitches        (?assign=0, ?draft=0, ?budget=200 seconds)
    Idempotent per day: calling again only fills what's missing (e.g. Pro pitches left over by the time budget).
GET /api/hotlist          creator session: hotlist_for_creator (?limit=30)
GET /api/hotlist/today    creator session: hot_brand_today
"""

from flask import Blueprint, jsonify, request

hotlist_bp = Blueprint("hotlist", __name__, url_prefix="/api/hotlist")


def _int_arg(name, default, lo=0, hi=10_000):
    try:
        return max(lo, min(hi, int(request.args.get(name, default))))
    except (TypeError, ValueError):
        return default


def _off(name):
    return str(request.args.get(name, "1")).lower() in ("0", "false", "no", "off")


def run_daily(conn, *, seed=True, classify=40, gate=True, assign=True, draft=True, budget=200.0, day=None):
    """The whole daily job. Each step is isolated: one failing step never stops the next."""
    from services import brand_quality_gate, hot_brands, meta_ads_signal

    out = {}
    steps = []
    if seed:
        steps.append(("seeded", lambda: meta_ads_signal.seed_from_notes(conn)))
    if classify:
        steps.append(("classified", lambda: meta_ads_signal.classify_pending(conn, limit=classify)))
    if gate:
        steps.append(("gate", lambda: brand_quality_gate.run_gate(conn)))
    if assign:
        steps.append(("hot", lambda: hot_brands.assign_hot_brands(conn, day=day, draft=draft, time_budget=budget)))
    for name, step in steps:
        try:
            out[name] = step()
        except Exception as err:
            import traceback
            print(f"[hotlist] {name} failed: {err}\n{traceback.format_exc()[-1500:]}")
            try:
                conn.rollback()
            except Exception:
                pass
            out[name] = {"error": f"{type(err).__name__}: {err}"[:300]}
    return out


@hotlist_bp.route("/cron/daily", methods=["GET", "POST"])
def cron_daily():
    from services.admin_auth import cron_request_ok

    if not cron_request_ok():
        return jsonify({"success": False, "error": "unauthorized"}), 401
    from pr_crm_routes import get_db_connection
    from routes.polly import json_safe

    conn = get_db_connection()
    try:
        out = run_daily(conn, seed=not _off("seed"), classify=_int_arg("classify", 40, 0, 500),
                        gate=not _off("gate"), assign=not _off("assign"), draft=not _off("draft"),
                        budget=float(_int_arg("budget", 200, 0, 800)))
        return jsonify({"success": True, **json_safe(out)})
    finally:
        conn.close()


def _creator():
    from routes.polly import _creator_auth
    return _creator_auth()


@hotlist_bp.route("", methods=["GET"])
def my_hotlist():
    from routes.polly import json_safe
    from services.hot_brands import hotlist_for_creator

    creator_id, conn, _creator_row = _creator()
    if not creator_id:
        return jsonify({"success": False, "error": "Authentication required"}), 401
    try:
        brands = hotlist_for_creator(conn, creator_id, limit=_int_arg("limit", 30, 1, 100))
        return jsonify({"success": True, "brands": json_safe(brands)})
    finally:
        conn.close()


@hotlist_bp.route("/today", methods=["GET"])
def my_hot_brand_today():
    from routes.polly import json_safe
    from services.hot_brands import hot_brand_today

    creator_id, conn, _creator_row = _creator()
    if not creator_id:
        return jsonify({"success": False, "error": "Authentication required"}), 401
    try:
        return jsonify({"success": True, "hot_brand": json_safe(hot_brand_today(conn, creator_id))})
    finally:
        conn.close()
