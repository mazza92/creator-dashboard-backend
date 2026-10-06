"""Polly Autopilot API (Pro): connect Gmail, plan the week's brands, draft, approve, send."""

import os

from flask import Blueprint, jsonify, redirect, request

from services.polly_autopilot import (
    add_draft,
    approve,
    batch_size,
    list_items,
    month_counts,
    queued_brand_ids,
    set_enabled,
    set_monthly_target,
    settings,
    skip_item,
    update_draft,
)
from services.polly_gmail import (
    GmailError,
    auth_url,
    disconnect,
    exchange_code,
    gmail_configured,
    load_account,
    read_state,
    save_account,
)

autopilot_bp = Blueprint("polly_autopilot", __name__, url_prefix="/api/polly/autopilot")

POLLY_PATH = "/creator/dashboard/for-you"


def _frontend(path: str) -> str:
    return (os.getenv("FRONTEND_URL") or "https://app.newcollab.co").rstrip("/") + path


def _auth():
    from routes.polly import _creator_auth
    return _creator_auth()


def _is_pro(creator_id, conn) -> bool:
    from routes.polly import _unlock_balance
    return bool(_unlock_balance(creator_id, conn=conn).get("is_unlimited"))


def _status_payload(conn, creator_id, is_pro):
    account = load_account(conn, creator_id) if gmail_configured() else None
    prefs = settings(conn, creator_id)
    counts = month_counts(conn, creator_id)
    return {
        "success": True,
        "available": gmail_configured(),
        "app_verified": os.getenv("POLLY_GMAIL_APP_VERIFIED", "").strip().lower() in ("1", "true", "yes"),
        "is_pro": is_pro,
        "gmail": {
            "connected": bool(account and not account.get("revoked_at")),
            "email": (account or {}).get("email"),
            "needs_reconnect": bool(account and account.get("revoked_at")),
        },
        "enabled": prefs["enabled"],
        "monthly_target": prefs["monthly_target"],
        "month": counts,
        "next_batch": batch_size(counts, prefs["monthly_target"]),
        "items": list_items(conn, creator_id),
    }


def _guard(require_gmail=True):
    """(creator_id, conn, creator, error_response)."""
    creator_id, conn, creator = _auth()
    if not creator_id:
        return None, None, None, (jsonify({"success": False, "error": "Not authenticated"}), 401)
    if not _is_pro(creator_id, conn):
        conn.close()
        return None, None, None, (jsonify({"success": False, "error": "pro_required", "paywall": True}), 402)
    if require_gmail:
        account = load_account(conn, creator_id) if gmail_configured() else None
        if not account or account.get("revoked_at"):
            conn.close()
            return None, None, None, (jsonify({"success": False, "error": "gmail_not_connected"}), 409)
    return creator_id, conn, creator, None


@autopilot_bp.route("/status", methods=["GET"])
def status():
    creator_id, conn, _creator = _auth()
    if not creator_id:
        return jsonify({"success": False, "error": "Not authenticated"}), 401
    try:
        return jsonify(_status_payload(conn, creator_id, _is_pro(creator_id, conn)))
    finally:
        conn.close()


@autopilot_bp.route("/gmail/connect", methods=["GET"])
def gmail_connect():
    if not gmail_configured():
        return jsonify({"success": False, "error": "gmail_unavailable"}), 503
    creator_id, conn, creator, err = _guard(require_gmail=False)
    if err:
        return err
    try:
        return jsonify({"success": True, "url": auth_url(creator_id)})
    finally:
        conn.close()


@autopilot_bp.route("/gmail/callback", methods=["GET"])
def gmail_callback():
    from pr_crm_routes import get_db_connection

    creator_id = read_state(request.args.get("state") or "")
    if not creator_id or request.args.get("error") or not request.args.get("code"):
        return redirect(_frontend(f"{POLLY_PATH}?autopilot=gmail_denied"))
    conn = get_db_connection()
    try:
        save_account(conn, creator_id, exchange_code(request.args["code"]))
        set_enabled(conn, creator_id, True)
        return redirect(_frontend(f"{POLLY_PATH}?autopilot=gmail_connected"))
    except GmailError as gerr:
        print(f"[Polly autopilot] gmail connect failed creator={creator_id}: {gerr}")
        reason = "gmail_scope" if "gmail.send" in str(gerr) else "gmail_failed"
        return redirect(_frontend(f"{POLLY_PATH}?autopilot={reason}"))
    finally:
        conn.close()


@autopilot_bp.route("/gmail/disconnect", methods=["POST"])
def gmail_disconnect():
    creator_id, conn, _creator = _auth()
    if not creator_id:
        return jsonify({"success": False, "error": "Not authenticated"}), 401
    try:
        disconnect(conn, creator_id)
        set_enabled(conn, creator_id, False)
        return jsonify(_status_payload(conn, creator_id, _is_pro(creator_id, conn)))
    finally:
        conn.close()


@autopilot_bp.route("/toggle", methods=["POST"])
def toggle():
    creator_id, conn, _creator, err = _guard(require_gmail=False)
    if err:
        return err
    try:
        set_enabled(conn, creator_id, bool((request.get_json(silent=True) or {}).get("enabled")))
        return jsonify(_status_payload(conn, creator_id, True))
    finally:
        conn.close()


@autopilot_bp.route("/settings", methods=["POST"])
def update_settings():
    creator_id, conn, _creator, err = _guard(require_gmail=False)
    if err:
        return err
    try:
        body = request.get_json(silent=True) or {}
        if "enabled" in body:
            set_enabled(conn, creator_id, bool(body.get("enabled")))
        if "monthly_target" in body:
            try:
                set_monthly_target(conn, creator_id, int(body.get("monthly_target")))
            except (TypeError, ValueError):
                return jsonify({"success": False, "error": "monthly_target must be 12 or 24"}), 400
        return jsonify(_status_payload(conn, creator_id, True))
    finally:
        conn.close()


def _brands_with_email(conn, ids):
    from psycopg2.extras import RealDictCursor

    if not ids:
        return {}
    cur = conn.cursor(cursor_factory=RealDictCursor)
    cur.execute(
        """
        SELECT id, brand_name, NULLIF(TRIM(contact_email), '') AS email
        FROM pr_brands
        WHERE id = ANY(%s)
          AND COALESCE(LOWER(email_status), '') NOT IN ('bounced', 'invalid', 'undeliverable', 'risky')
          AND COALESCE(email_bounce_count, 0) = 0
        """,
        (list(ids),),
    )
    return {int(r["id"]): r for r in cur.fetchall() if r.get("email")}


@autopilot_bp.route("/plan", methods=["POST"])
def plan():
    """This week's picks: matched, emailable, not pitched or queued already."""
    from routes.polly import _load_scrape, _suggest_payload, json_safe
    from services.polly_memory import load_thread

    creator_id, conn, creator, err = _guard()
    if err:
        return err
    try:
        prefs = settings(conn, creator_id)
        size = batch_size(month_counts(conn, creator_id), prefs["monthly_target"])
        if size <= 0:
            return jsonify({"success": True, "brands": [], "reason": "target_reached"})
        notes = load_thread(conn, creator_id).get("notes") or {}
        scrape = _load_scrape(conn, (creator or {}).get("user_id"))
        _status, brands, error = _suggest_payload(scrape, creator, notes, creator_id)
        skip = set(queued_brand_ids(conn, creator_id))
        pool = [b for b in brands or [] if b.get("id") and int(b["id"]) not in skip]
        emailable = _brands_with_email(conn, [int(b["id"]) for b in pool])
        picks = [b for b in pool if int(b["id"]) in emailable][:size]
        return jsonify({"success": True, "brands": json_safe(picks), "error": None if picks else error})
    finally:
        conn.close()


@autopilot_bp.route("/draft", methods=["POST"])
def draft():
    """Write one pitch for the batch. The frontend calls this per brand so progress shows."""
    from routes.polly import (
        _invoke_generate_pr_package,
        _load_scrape,
        apply_gifted_ask_to_pitch,
        apply_location_to_pitch,
        pitch_from_package_response,
        pitch_has_placeholder,
        resolve_pitch_location,
    )
    from services.polly_memory import load_thread

    creator_id, conn, creator, err = _guard()
    if err:
        return err
    try:
        try:
            brand_id = int((request.get_json(silent=True) or {}).get("brand_id"))
        except (TypeError, ValueError):
            return jsonify({"success": False, "error": "brand_id required"}), 400
        brand = _brands_with_email(conn, [brand_id]).get(brand_id)
        if not brand:
            return jsonify({"success": False, "error": "no_contact_email"}), 422
        notes = load_thread(conn, creator_id).get("notes") or {}
        scrape = _load_scrape(conn, (creator or {}).get("user_id"))
        shipping = resolve_pitch_location(creator, scrape, notes)
        if shipping.get("needs_location"):
            return jsonify({"success": False, "error": "needs_location"}), 409
        status, pkg = _invoke_generate_pr_package(
            brand_id, city=shipping.get("city") or "", country=shipping.get("country") or "",
        )
        if status != 200 or not pkg.get("success"):
            return jsonify({"success": False, "error": pkg.get("error") or "draft_failed"}), 502
        pitch = pitch_from_package_response(pkg) or {}
        loc_display = str(shipping.get("display") or "").strip()
        if notes.get("deal_intent") != "paid":
            pitch = apply_gifted_ask_to_pitch(pitch, location_display=loc_display or None)
        if loc_display:
            pitch = apply_location_to_pitch(pitch, shipping.get("city") or "", shipping.get("country") or "", loc_display)
        body = pitch.get("body") or ""
        if not body or pitch_has_placeholder(body):
            return jsonify({"success": False, "error": "needs_location"}), 409
        item = add_draft(
            conn, creator_id, brand_id, brand["brand_name"], pitch.get("email") or brand["email"],
            pitch.get("subject") or f"{brand['brand_name']} collab", body,
        )
        if not item:
            return jsonify({"success": False, "error": "already_queued"}), 409
        return jsonify({"success": True, "item": item})
    finally:
        conn.close()


@autopilot_bp.route("/items/<int:item_id>", methods=["PATCH"])
def edit_item(item_id):
    creator_id, conn, _creator, err = _guard(require_gmail=False)
    if err:
        return err
    try:
        body = request.get_json(silent=True) or {}
        ok = update_draft(conn, creator_id, item_id, body.get("subject") or "", body.get("body") or "")
        return jsonify({"success": ok})
    finally:
        conn.close()


@autopilot_bp.route("/items/<int:item_id>/skip", methods=["POST"])
def skip(item_id):
    creator_id, conn, _creator, err = _guard(require_gmail=False)
    if err:
        return err
    try:
        skip_item(conn, creator_id, item_id)
        return jsonify(_status_payload(conn, creator_id, True))
    finally:
        conn.close()


@autopilot_bp.route("/approve", methods=["POST"])
def approve_batch():
    creator_id, conn, _creator, err = _guard()
    if err:
        return err
    try:
        ids = (request.get_json(silent=True) or {}).get("ids") or []
        scheduled = approve(conn, creator_id, ids)
        payload = _status_payload(conn, creator_id, True)
        payload["scheduled"] = scheduled
        return jsonify(payload)
    finally:
        conn.close()
