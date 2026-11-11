"""Tracker API: the creator's monthly $ goal and every brand on one board / calendar / list.

GET  /api/tracker                     board, calendar, goal progress, funnel, your-move count, free balance
GET  /api/tracker/goal/suggest        starter / suggested / stretch goals + pitch math for each
POST /api/tracker/goal                {amount (dollars) | amount_cents, source}
POST /api/tracker/deal                {brand_id, action, amount?, due_date?, payment_due?, deliverables?, reason?}
GET  /api/tracker/brand/<brand_id>    one brand's history + deal
"""

from flask import Blueprint, jsonify, request

from services.creator_tracker import (
    ACTIONS,
    brand_detail,
    build_tracker,
    creator_profile,
    get_goal,
    mark_milestone,
    needs_plan,
    plan_week,
    post_goal_plan,
    own_rates,
    pitch_math,
    record_deal,
    set_goal,
    suggest_goal,
)

tracker_bp = Blueprint("creator_tracker", __name__, url_prefix="/api/tracker")


def _auth():
    from routes.polly import _creator_auth
    return _creator_auth()


PLAN_POOL = 250  # deep enough for several weeks of brands, not just Autopilot's next batch


def _plan(conn, creator_id, creator):
    """Line up this week's brands from the creator's matches (Autopilot's picker). Never fails the request."""
    if not creator:
        return None
    try:
        from routes.polly_autopilot import _creator_context, _pick_brands
        notes, scrape = _creator_context(conn, creator)
        brands, _err = _pick_brands(conn, creator_id, creator, notes, scrape, limit=PLAN_POOL)
        return plan_week(conn, creator_id, brands)
    except Exception as err:
        print(f"[tracker] plan skipped creator={creator_id}: {err}")
        try:
            conn.rollback()
        except Exception:
            pass
        return None


def _unauthorized():
    return jsonify({"success": False, "error": "unauthorized"}), 401


@tracker_bp.route("", methods=["GET"])
@tracker_bp.route("/", methods=["GET"])
def tracker():
    creator_id, conn, creator = _auth()
    if not creator_id:
        return _unauthorized()
    try:
        if needs_plan(conn, creator_id):
            _plan(conn, creator_id, creator)
        if request.args.get("seen"):  # the Tracker page itself (not Polly's goal bar): checklist step 3
            mark_milestone(conn, creator_id, "calendar_seen")
        return jsonify({"success": True, **build_tracker(conn, creator_id)})
    finally:
        conn.close()


@tracker_bp.route("/goal/suggest", methods=["GET"])
def goal_suggest():
    creator_id, conn, _creator = _auth()
    if not creator_id:
        return _unauthorized()
    try:
        profile = creator_profile(conn, creator_id)
        out = suggest_goal(profile)
        rates = own_rates(conn, creator_id)
        for opt in out["options"]:
            opt["math"] = pitch_math(opt["amount_cents"], profile["followers"], rates)
        return jsonify({"success": True, **out, "current": get_goal(conn, creator_id)})
    finally:
        conn.close()


@tracker_bp.route("/goal", methods=["POST"])
def goal_save():
    creator_id, conn, creator = _auth()
    if not creator_id:
        return _unauthorized()
    body = request.get_json(silent=True) or {}
    try:
        if body.get("amount_cents") is not None:
            cents = int(body["amount_cents"])
        else:
            cents = int(round(float(str(body.get("amount", "")).replace("$", "").replace(",", "")) * 100))
    except (TypeError, ValueError):
        conn.close()
        return jsonify({"success": False, "error": "amount_required"}), 400
    try:
        goal = set_goal(conn, creator_id, cents, body.get("source") or "custom")
        profile = creator_profile(conn, creator_id)
        goal["math"] = pitch_math(goal["amount_cents"], profile["followers"], own_rates(conn, creator_id))
        goal["plan"] = _plan(conn, creator_id, creator)  # the goal becomes this week's brands, with dates
        goal["posted_plan"] = False
        if goal["plan"] and goal["plan"].get("planned"):
            try:
                goal["posted_plan"] = post_goal_plan(conn, creator_id)  # Polly lays the plan out in chat
            except Exception as err:
                print(f"[tracker] plan message skipped creator={creator_id}: {err}")
                conn.rollback()
        return jsonify({"success": True, "goal": goal})
    finally:
        conn.close()


@tracker_bp.route("/deal", methods=["POST"])
def deal_action():
    creator_id, conn, _creator = _auth()
    if not creator_id:
        return _unauthorized()
    body = request.get_json(silent=True) or {}
    action = body.get("action")
    if action not in ACTIONS:
        conn.close()
        return jsonify({"success": False, "error": "unknown_action", "actions": sorted(ACTIONS)}), 400
    try:
        brand_id = int(body["brand_id"]) if body.get("brand_id") not in (None, "") else None
    except (TypeError, ValueError):
        brand_id = None
    if not brand_id and not (body.get("brand_name") or "").strip():
        conn.close()
        return jsonify({"success": False, "error": "brand_required"}), 400
    try:
        deal = record_deal(
            conn, creator_id, brand_id, action,
            brand_name=body.get("brand_name") or "", amount=body.get("amount"),
            due_date=body.get("due_date"), payment_due=body.get("payment_due"),
            deliverables=body.get("deliverables"), reason=body.get("reason"),
        )
        return jsonify({"success": True, "deal": _jsonable(deal)})
    finally:
        conn.close()


@tracker_bp.route("/brand/<int:brand_id>", methods=["GET"])
def brand(brand_id):
    creator_id, conn, _creator = _auth()
    if not creator_id:
        return _unauthorized()
    try:
        out = brand_detail(conn, creator_id, brand_id)
        if out.get("deal"):
            out["deal"] = _jsonable(out["deal"])
        return jsonify({"success": True, **out})
    finally:
        conn.close()


def _jsonable(row):
    out = {}
    for k, v in (row or {}).items():
        out[k] = v.isoformat() if hasattr(v, "isoformat") else v
    return out


@tracker_bp.route("/checklist/hide", methods=["POST"])
def checklist_hide():
    creator_id, conn, _creator = _auth()
    if not creator_id:
        return _unauthorized()
    try:
        mark_milestone(conn, creator_id, "checklist_hidden")
        return jsonify({"success": True})
    finally:
        conn.close()
