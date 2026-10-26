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
    if brand.get("approval_status") == "pending" and ws.campaign_published(conn, brand, row[0]):
        # Their campaign was published from the Opportunities tab before publishing also approved accounts.
        ws.approve_for_opportunity(conn, brand.get("opportunity_id") or 0, row[0])
        brand = ws.brand_for_user(conn, user_id) or brand
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
        ids = ws.create_brand_account(conn, email=email, password_hash=hashed, method="email", **f)
        carried = _carry_free_scan(conn, ids["brand_id"], body)
        return _signed_in(conn, ids["user_id"], ids["brand_id"], 201, approval_status="pending",
                          needs_profile=True, free_scan=carried)
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
            return _signed_in(conn, existing["id"], brand["id"], approval_status=brand.get("approval_status"),
                              needs_profile=not brand.get("profile_completed_at"))
        f = _signup_fields(body)
        name = str(info.get("name") or "").split()
        f["first_name"] = f["first_name"] or (name[0] if name else "")
        f["last_name"] = f["last_name"] or " ".join(name[1:])
        # users.password is NOT NULL: Google-only accounts get the same unusable placeholder as /google-signup.
        placeholder = f"GOOGLE_AUTH_{uuid.uuid4().hex}"
        ids = ws.create_brand_account(conn, email=email, password_hash=placeholder, method="google", **f)
        carried = _carry_free_scan(conn, ids["brand_id"], body)
        return _signed_in(conn, ids["user_id"], ids["brand_id"], 201, approval_status="pending",
                          needs_profile=True, free_scan=carried)
    except Exception as err:
        conn.rollback()
        print(f"[brand-workspace] google signup failed: {type(err).__name__}: {err!r}")
        return jsonify({"success": False, "error": "Could not sign you in. Try again."}), 500
    finally:
        conn.close()


def _carry_free_scan(conn, brand_id: int, body) -> str:
    """Import the landing-page free scan: the brand named in the signup body (from the shared cookie), else
    the one this IP scanned. Returns the scanned brand's name, or ''."""
    from routes.brand_scan import free_scan_for_ip
    from services import brand_scan

    try:
        target, scan_id = None, None
        named = ws.clean_text(body.get("free_scan"), 80)
        if named:
            target = brand_scan.resolve(conn, named)
        if not target:
            used = free_scan_for_ip()
            if used:
                target = brand_scan.resolve(conn, used.get("name") or used.get("key"))
                scan_id = used.get("scan_id")
        return target["name"] if ws.import_free_scan(conn, brand_id, target, scan_id) else ""
    except Exception as err:
        conn.rollback()
        print(f"[brand-workspace] free scan carry-over skipped: {type(err).__name__}: {err}")
        return ""


@brand_workspace_bp.route("/api/brand-workspace/profile", methods=["POST"])
def profile():
    """Signup step 2: tell us about your brand. Sends the team the new-account email once it's complete."""
    body = _body()
    name = ws.clean_text(body.get("brand_name"), 120)
    website = ws.clean_website(body.get("website")) if body.get("website") else ""
    app_url = ws.clean_app_url(body.get("app_url")) if body.get("app_url") else ""
    category = ws.clean_text(body.get("category"), 60)
    if not name:
        return jsonify({"success": False, "error": "Enter your brand name."}), 400
    if not website and not app_url:
        return jsonify({"success": False, "error": "Add your website or an App Store / Google Play link."}), 400
    if not category:
        return jsonify({"success": False, "error": "Pick your category."}), 400
    conn = _conn()
    try:
        me, err = _require_brand(conn)
        if err:
            return err
        first_time = not me["brand"].get("profile_completed_at")
        ws.update_profile(conn, me["brand"]["id"], name=name, website=website, app_url=app_url, category=category)
        if first_time:
            ws.notify_team_signup(me["email"], name, website or app_url, me["brand"].get("signup_source") or "")
        return jsonify({"success": True})
    finally:
        conn.close()


@brand_workspace_bp.route("/api/brand-workspace/campaigns", methods=["GET", "POST"])
def campaigns():
    """GET: the brand's campaigns + whether it can start another. POST: submit one (first free, then Pro)."""
    conn = _conn()
    try:
        me, err = _require_brand(conn)
        if err:
            return err
        items = ws.campaigns(conn, me["brand"], me["email"])
        gate = ws.campaign_gate(me["approved"], me["subscribed"], items)
        if request.method == "GET":
            return jsonify({"success": True, "campaigns": items, **gate})
        # Signup step 3 creates the first campaign while the account is still pending review.
        first_at_signup = not items and not me["approved"]
        if not gate["can_create"] and not first_at_signup:
            return jsonify({"success": False, "code": "PAYWALL" if gate["reason"] == "paywall" else gate["reason"],
                            "error": "Your free campaign is used. Subscribe to run more campaigns.", **gate}), 402
        brand = me["brand"]
        try:
            payload = request.get_json()  # the exact cached object brand_submit() reads below
        except Exception:
            payload = None
        if not isinstance(payload, dict):
            return jsonify({"success": False, "error": "Send the campaign as JSON."}), 400
        payload.update({
            "brand_name": brand.get("name"),
            "brand_email": me["email"],
            "brand_website": brand.get("website") or brand.get("app_url") or "",
            "brand_category": payload.get("brand_category") or brand.get("category"),
            "ref": payload.get("ref") or brand.get("signup_source") or "workspace",
        })
        if not payload["brand_website"]:
            return jsonify({"success": False, "error": "Add your website first."}), 400
        from opportunities_routes import brand_submit

        resp = brand_submit()  # same validation, insert, pipeline close-out and emails as /for-brands always had
        resp, status = (resp if isinstance(resp, tuple) else (resp, resp.status_code))
        data = resp.get_json() or {}
        if status >= 300 or not data.get("id"):
            return resp, status
        if not brand.get("opportunity_id"):
            ws.link_opportunity(conn, brand["id"], int(data["id"]), me["email"])
        if me["pr_brand_id"]:  # a subscribed brand's billing row: publishing reuses it instead of making another
            cur = conn.cursor()
            cur.execute("UPDATE opportunities SET pr_brand_id = %s WHERE id = %s AND pr_brand_id IS NULL",
                        (me["pr_brand_id"], int(data["id"])))
            conn.commit()
        return jsonify({"success": True, "id": data["id"], "campaigns": ws.campaigns(conn, ws.brand_for_user(conn, me["user_id"]), me["email"])}), 201
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


# ----- billing ---------------------------------------------------------------------------------

def _stripe():
    import os

    import stripe

    stripe.api_key = stripe.api_key or os.getenv("STRIPE_SECRET_KEY")
    return stripe


def _billing_url(state: str) -> str:
    from services.creator_directory_search import frontend_base

    return f"{frontend_base()}/brand/workspace/billing?billing={state}"


@brand_workspace_bp.route("/api/brand-workspace/billing/checkout", methods=["POST"])
def billing_checkout():
    """Subscribe from the workspace at any time: before, during or after the first campaign."""
    import os

    from services.brand_billing import PLAN_GIFTED_UGC_299, PRODUCT_META, get_or_create_brand_billing

    conn = _conn()
    try:
        me, err = _require_brand(conn)
        if err:
            return err
        if me["subscribed"]:
            return jsonify({"success": False, "code": "already_subscribed", "error": "You're already on Pro."}), 409
        stripe = _stripe()
        price_id = os.getenv("STRIPE_PRICE_ID_BRAND_GIFTED_MONTHLY")
        if not price_id or not stripe.api_key:
            return jsonify({"success": False, "code": "price_not_configured",
                            "error": "Billing isn't set up yet. Email us and we'll sort it."}), 500
        pr_brand_id = ws.ensure_pr_brand(conn, me["brand"], me["email"])
        cur = conn.cursor(cursor_factory=RealDictCursor)
        bill = get_or_create_brand_billing(cur, pr_brand_id, email=me["email"])
        cur.execute("""INSERT INTO brand_pr_events (brand_id, event, source, meta)
                       VALUES (%s, 'workspace_checkout_started', 'brand_workspace', '{}'::jsonb)""", (pr_brand_id,))
        conn.commit()
        meta = {"product": PRODUCT_META, "plan": PLAN_GIFTED_UGC_299, "brand_id": str(pr_brand_id)}
        kwargs = {
            "payment_method_types": ["card"],
            "line_items": [{"price": price_id, "quantity": 1}],
            "mode": "subscription",
            "success_url": _billing_url("success") + "&session_id={CHECKOUT_SESSION_ID}",
            "cancel_url": _billing_url("cancel"),
            "allow_promotion_codes": True,
            "metadata": {**meta, "brand_name": me["brand"].get("name") or "", "workspace_brand_id": str(me["brand"]["id"])},
            "subscription_data": {"metadata": meta},
        }
        if bill.get("stripe_customer_id"):
            kwargs["customer"] = bill["stripe_customer_id"]
        elif me["email"]:
            kwargs["customer_email"] = me["email"]
        checkout = stripe.checkout.Session.create(**kwargs)
        return jsonify({"success": True, "checkout_url": checkout.url, "session_id": checkout.id})
    except Exception as e:  # stripe errors included: show the brand a clean message, log the detail
        print(f"[brand-workspace] checkout failed: {type(e).__name__}: {e}")
        conn.rollback()
        return jsonify({"success": False, "error": "Couldn't open checkout. Try again in a minute."}), 500
    finally:
        conn.close()


@brand_workspace_bp.route("/api/brand-workspace/billing/portal", methods=["POST"])
def billing_portal():
    from services.brand_billing import get_or_create_brand_billing

    conn = _conn()
    try:
        me, err = _require_brand(conn)
        if err:
            return err
        bill = (get_or_create_brand_billing(conn.cursor(cursor_factory=RealDictCursor), me["pr_brand_id"])
                if me["pr_brand_id"] else {})
        conn.commit()
        if not bill.get("stripe_customer_id"):
            return jsonify({"success": False, "code": "no_customer", "error": "Subscribe first."}), 404
        portal = _stripe().billing_portal.Session.create(customer=bill["stripe_customer_id"],
                                                         return_url=_billing_url("portal"))
        return jsonify({"success": True, "portal_url": portal.url})
    except Exception as e:
        print(f"[brand-workspace] portal failed: {type(e).__name__}: {e}")
        conn.rollback()
        return jsonify({"success": False, "error": "Couldn't open billing. Try again in a minute."}), 500
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
                      "app_url": brand.get("app_url"), "category": brand.get("category"),
                      "logo": brand.get("logo"), "niche": brand.get("signup_niche") or brand.get("category")},
            "profile_complete": bool(brand.get("profile_completed_at")),
            "free_scan": brand.get("free_scan_brand"),
            "user": {"email": me["email"], "first_name": me["first_name"]},
            "approval_status": brand.get("approval_status") or "approved",
            "approved": me["approved"],
            "scans": {"used_today": len(today), "limit": None if me["subscribed"] else ws.FREE_SCANS_PER_DAY,
                      "subscribed": me["subscribed"],
                      "pending_left": 0 if (not me["approved"] and [k for k in ws.scanned_keys(conn, brand["id"])
                                                                   if not k.startswith("insight:")]) else 1},
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


# ----- prompt library insights ------------------------------------------------------------------

def _insight_reply(out):
    out = dict(out or {})
    out.pop("started", None)
    if out.get("status") == "invalid":
        return jsonify({"success": False, "error": "Pick your brand category first (Plan & settings)."}), 400
    if out.get("status") == "not_found":
        return jsonify({"success": False, "error": "Not found."}), 404
    return jsonify({"success": True, "scan": out})


@brand_workspace_bp.route("/api/brand-workspace/insight", methods=["POST"])
def insight():
    """Prompt library: niche insights (gifting brands, hooks, creators) for the brand's category."""
    from routes.brand_scan import _can_start, _count_start, _gate, _ip, _redis
    from services import brand_insights as bi

    body = _body()
    kind = str(body.get("kind") or "")
    conn = _conn()
    try:
        me, err = _require_brand(conn)
        if err:
            return err
        brand = me["brand"]
        niche = bi.niche_for(body.get("niche") or brand.get("category") or brand.get("signup_niche"))
        question = bi.question_for(kind, niche, brand.get("name") or "", str(body.get("question") or ""))
        if kind == "ask" and len(question) < 6:
            return jsonify({"success": False, "error": "Ask a question about creators or content."}), 400
        target = {"key": bi.insight_key(kind, niche, question),
                  "name": question[:80] if kind == "ask" else bi.TITLES.get(kind, "Insight").format(niche=niche)}
        if not me["approved"]:
            return jsonify({"success": True, "scan": _gate("pending", target)})
        if kind not in bi.KINDS or not niche:
            return _insight_reply({"status": "invalid"})
        allow = ws.scan_allowance(conn, brand["id"], target["key"], me["subscribed"])
        if not allow["allowed"]:
            return jsonify({"success": True, "scan": _gate("daily_limit", target, limit=allow["limit"], used=allow["used"])})
        out = bi.start(conn, kind, niche, brand_name=brand.get("name") or "", redis_client=_redis(),
                       can_start=me["subscribed"] or _can_start(_ip()), question=question)
        if out.get("status") in ("ready", "empty", "searching"):
            ws.record_scan(conn, brand["id"], target["key"], target["name"], out.get("scan_id"))
        if out.get("started"):
            _count_start(_ip())
        return _insight_reply(out)
    except Exception as err:
        conn.rollback()
        print(f"[brand-insight] failed: {type(err).__name__}: {err!r}")
        return jsonify({"success": False, "error": "Could not run this prompt. Try again."}), 500
    finally:
        conn.close()


@brand_workspace_bp.route("/api/brand-workspace/insight/<int:run_id>", methods=["GET"])
def insight_poll(run_id):
    from routes.brand_scan import _redis
    from services import brand_insights as bi

    conn = _conn()
    try:
        me, err = _require_brand(conn)
        if err:
            return err
        return _insight_reply(bi.poll(conn, run_id, str(request.args.get("kind") or ""),
                                      brand_name=me["brand"].get("name") or "", redis_client=_redis()))
    except Exception as err:
        conn.rollback()
        print(f"[brand-insight] poll failed: {type(err).__name__}: {err!r}")
        return jsonify({"success": False, "error": "Could not load this prompt."}), 500
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
