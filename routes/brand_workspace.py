"""Brand workspace API: brand signup (email/password or Google), the signed-in brand's workspace data,
and the admin approval queue for brand accounts.

Session handling reuses app.py's _establish_auth_session/_auth_json_response, handed in by
init_brand_workspace() so this module never imports app (no circular import).
"""
from __future__ import annotations

import uuid

import bcrypt
from flask import Blueprint, jsonify, request, session
from psycopg2.extras import RealDictCursor

from routes.admin_email import admin_required
from services import brand_workspace as ws

brand_workspace_bp = Blueprint("brand_workspace", __name__)

_auth = {"establish": None, "respond": None}
GOOGLE_PROJECT = "auth-app-feed3"


def init_brand_workspace(establish, respond) -> None:
    _auth["establish"], _auth["respond"] = establish, respond


def _conn():
    from pr_crm_routes import get_db_connection  # tuple rows; ws asks for dict cursors where it needs them

    return get_db_connection()


def _body():
    return request.get_json(silent=True) or {}


def _signed_in(conn, user_id: int, brand_id: int, status: int = 200, **extra):
    _auth["establish"](user_id, "brand", brand_id=brand_id)
    payload = {"success": True, "user_role": "brand", "user_id": user_id, "brand_id": brand_id,
               "redirect_url": "/brand/workspace", **extra}
    return _auth["respond"](payload, status)


def viewer(conn):
    """The signed-in brand, or None. {brand, approved, pr_brand_id, subscribed, email}."""
    if session.get("user_role") != "brand" or not session.get("user_id"):
        return None
    user_id = int(session["user_id"])
    brand = ws.brand_for_user(conn, user_id)
    if not brand:
        return None
    cur = conn.cursor()
    cur.execute("SELECT email, first_name FROM users WHERE id = %s", (user_id,))
    row = cur.fetchone() or ("", "")
    pr_brand_id = ws.resolve_pr_brand(conn, brand, row[0])
    bill = ws.billing(conn, pr_brand_id)
    return {"brand": brand, "approved": ws.is_approved(brand.get("approval_status")), "pr_brand_id": pr_brand_id,
            "subscribed": bool(bill.get("subscribed")), "billing": bill, "email": row[0], "first_name": row[1],
            "user_id": user_id}


def _require_brand(conn):
    me = viewer(conn)
    if not me:
        return None, (jsonify({"success": False, "error": "Sign in with your brand account.",
                               "code": "SESSION_EXPIRED"}), 401)
    return me, None


# ----- signup ------------------------------------------------------------------------------

def _signup_fields(body):
    return {
        "brand_name": ws.clean_text(body.get("brand_name"), 120),
        "website": ws.clean_website(body.get("website")),
        "category": ws.clean_text(body.get("category"), 60),
        "niche": ws.clean_text(body.get("niche"), 40),
        "source": ws.clean_text(body.get("ref") or body.get("source") or "for-brands", 40),
        "first_name": ws.clean_text(body.get("first_name"), 60),
        "last_name": ws.clean_text(body.get("last_name"), 60),
    }


@brand_workspace_bp.route("/api/brand-workspace/signup", methods=["POST"])
def signup():
    body = _body()
    email = ws.clean_email(body.get("email"))
    password = str(body.get("password") or "")
    f = _signup_fields(body)
    if not email:
        return jsonify({"success": False, "error": "Enter a valid work email."}), 400
    if len(password) < 8:
        return jsonify({"success": False, "error": "Use a password of at least 8 characters."}), 400
    if not f["brand_name"]:
        return jsonify({"success": False, "error": "Enter your brand name."}), 400
    conn = _conn()
    try:
        existing = ws.find_user(conn, email)
        if existing:
            if existing.get("role") == "brand":
                return jsonify({"success": False, "code": "EXISTS",
                                "error": "This email already has a brand account. Log in instead."}), 409
            return jsonify({"success": False, "code": "CREATOR_ACCOUNT",
                            "error": "This email is a creator account. Use your brand's work email."}), 409
        hashed = bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode()
        ids = ws.create_brand_account(conn, email=email, password_hash=hashed, **f)
        ws.notify_team_signup(email, f["brand_name"], f["website"], f["source"])
        return _signed_in(conn, ids["user_id"], ids["brand_id"], 201, approval_status="pending")
    except Exception as err:
        conn.rollback()
        print(f"[brand-workspace] signup failed: {type(err).__name__}: {err!r}")
        return jsonify({"success": False, "error": "Could not create your account. Try again."}), 500
    finally:
        conn.close()


def _verify_google(token: str) -> dict:
    from google.auth.transport.requests import Request
    from google.oauth2 import id_token

    info = id_token.verify_firebase_token(token, Request(), GOOGLE_PROJECT)
    if not info or not info.get("email"):
        raise ValueError("token has no email")
    return info


@brand_workspace_bp.route("/api/brand-workspace/google", methods=["POST"])
def google():
    """Sign in or sign up a brand with Google. New accounts need the brand fields from the signup form."""
    body = _body()
    token = str(body.get("idToken") or "")
    if not token:
        return jsonify({"success": False, "error": "Missing Google token."}), 400
    try:
        info = _verify_google(token)
    except Exception as err:
        print(f"[brand-workspace] google verify failed: {type(err).__name__}: {err}")
        return jsonify({"success": False, "error": "Google sign-in failed. Try again."}), 401
    email = ws.clean_email(info.get("email"))
    conn = _conn()
    try:
        existing = ws.find_user(conn, email)
        if existing:
            if existing.get("role") != "brand":
                return jsonify({"success": False, "code": "CREATOR_ACCOUNT",
                                "error": "This Google account is a creator account. Use your brand's work email."}), 409
            brand = ws.brand_for_user(conn, existing["id"])
            if not brand:
                return jsonify({"success": False, "error": "No brand found for this account."}), 409
            return _signed_in(conn, existing["id"], brand["id"], approval_status=brand.get("approval_status"))
        f = _signup_fields(body)
        if not f["brand_name"]:
            return jsonify({"success": False, "code": "NEEDS_BRAND",
                            "error": "Tell us your brand name to finish signing up."}), 400
        name = str(info.get("name") or "").split()
        f["first_name"] = f["first_name"] or (name[0] if name else "")
        f["last_name"] = f["last_name"] or " ".join(name[1:])
        # users.password is NOT NULL: Google-only accounts get the same unusable placeholder as /google-signup.
        placeholder = f"GOOGLE_AUTH_{uuid.uuid4().hex}"
        ids = ws.create_brand_account(conn, email=email, password_hash=placeholder, **f)
        ws.notify_team_signup(email, f["brand_name"], f["website"], f["source"] + " (google)")
        return _signed_in(conn, ids["user_id"], ids["brand_id"], 201, approval_status="pending")
    except Exception as err:
        conn.rollback()
        print(f"[brand-workspace] google signup failed: {type(err).__name__}: {err!r}")
        return jsonify({"success": False, "error": "Could not sign you in. Try again."}), 500
    finally:
        conn.close()


@brand_workspace_bp.route("/api/brand-workspace/link-opportunity", methods=["POST"])
def link_opportunity():
    conn = _conn()
    try:
        me, err = _require_brand(conn)
        if err:
            return err
        try:
            opp_id = int(_body().get("opportunity_id"))
        except (TypeError, ValueError):
            return jsonify({"success": False, "error": "opportunity_id is required"}), 400
        ok = ws.link_opportunity(conn, me["brand"]["id"], opp_id, me["email"])
        return jsonify({"success": ok})
    finally:
        conn.close()


# ----- workspace -----------------------------------------------------------------------------

@brand_workspace_bp.route("/api/brand-workspace/me", methods=["GET"])
def me():
    conn = _conn()
    try:
        me, err = _require_brand(conn)
        if err:
            return err
        brand = me["brand"]
        today = ws.scans_today(conn, brand["id"])
        roster = ws.live_roster(conn, me["pr_brand_id"])
        opp = None
        if brand.get("opportunity_id"):
            cur = conn.cursor(cursor_factory=RealDictCursor)
            cur.execute("SELECT id, product_name, status FROM opportunities WHERE id = %s", (brand["opportunity_id"],))
            row = cur.fetchone()
            opp = dict(row) if row else None
        return jsonify({"success": True, "workspace": {
            "brand": {"id": brand["id"], "name": brand.get("name"), "website": brand.get("website"),
                      "logo": brand.get("logo"), "niche": brand.get("signup_niche") or brand.get("category")},
            "user": {"email": me["email"], "first_name": me["first_name"]},
            "approval_status": brand.get("approval_status") or "approved",
            "approved": me["approved"],
            "scans": {"used_today": len(today), "limit": None if me["subscribed"] else ws.FREE_SCANS_PER_DAY,
                      "subscribed": me["subscribed"]},
            "roster": {"token": roster["token"], "status": roster["status"], "title": roster.get("title")}
            if roster else None,
            "campaign": opp,
            "billing": {k: me["billing"].get(k) for k in ("status", "subscribed", "needs_subscribe", "can_mint_next")},
        }})
    finally:
        conn.close()


@brand_workspace_bp.route("/api/brand-workspace/scans", methods=["GET"])
def scans():
    conn = _conn()
    try:
        me, err = _require_brand(conn)
        if err:
            return err
        return jsonify({"success": True, "scans": ws.scan_history(conn, me["brand"]["id"])})
    finally:
        conn.close()


@brand_workspace_bp.route("/api/brand-workspace/saved", methods=["GET", "POST"])
def saved():
    conn = _conn()
    try:
        me, err = _require_brand(conn)
        if err:
            return err
        if request.method == "POST":
            body = _body()
            card = body.get("creator") if isinstance(body.get("creator"), dict) else {}
            if not ws.save_creator(conn, me["brand"]["id"], card, str(body.get("from_scan") or "")):
                return jsonify({"success": False, "error": "This creator can't be saved."}), 400
        return jsonify({"success": True, "creators": ws.saved_creators(conn, me["brand"]["id"])})
    finally:
        conn.close()


@brand_workspace_bp.route("/api/brand-workspace/saved/remove", methods=["POST"])
def saved_remove():
    conn = _conn()
    try:
        me, err = _require_brand(conn)
        if err:
            return err
        ws.unsave_creator(conn, me["brand"]["id"], str(_body().get("ref") or ""))
        return jsonify({"success": True, "creators": ws.saved_creators(conn, me["brand"]["id"])})
    finally:
        conn.close()


# ----- admin ----------------------------------------------------------------------------------

@brand_workspace_bp.route("/api/admin/brand-accounts", methods=["GET"])
@admin_required
def admin_list():
    conn = _conn()
    try:
        status = str(request.args.get("status") or ws.PENDING)
        return jsonify({"success": True, "accounts": ws.list_accounts(conn, status)})
    finally:
        conn.close()


@brand_workspace_bp.route("/api/admin/brand-accounts/<int:brand_id>/<action>", methods=["POST"])
@admin_required
def admin_decide(brand_id, action):
    if action not in ("approve", "reject"):
        return jsonify({"success": False, "error": "Unknown action"}), 404
    body = _body()
    conn = _conn()
    try:
        info = ws.decide(conn, brand_id, action == "approve", str(body.get("reason") or ""))
        if not info:
            return jsonify({"success": False, "error": "Brand account not found"}), 404
        if body.get("send_email", True):
            ws.send_decision(info, action == "approve")
        return jsonify({"success": True, "accounts": ws.list_accounts(conn, ws.PENDING)})
    finally:
        conn.close()
