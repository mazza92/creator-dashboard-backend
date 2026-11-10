"""Creator self-serve account deletion."""

import os
import logging

import psycopg2
from flask import Blueprint, jsonify, request, session
from flask_jwt_extended import get_jwt_identity, unset_jwt_cookies
from psycopg2.extras import RealDictCursor

from services.account_deletion import (
    cancel_stripe_billing,
    deletion_gate,
    purge_creator_account,
    validate_deletion_request,
)

logger = logging.getLogger(__name__)

account_bp = Blueprint("account", __name__, url_prefix="/api/account")


def get_db_connection():
    database_url = os.getenv("DATABASE_URL")
    if database_url:
        return psycopg2.connect(database_url)
    return psycopg2.connect(
        host=os.getenv("DB_HOST"),
        port=os.getenv("DB_PORT", 5432),
        database=os.getenv("DB_NAME"),
        user=os.getenv("DB_USER"),
        password=os.getenv("DB_PASSWORD"),
    )


def _session_user_id():
    user_id = session.get("user_id")
    if user_id:
        return user_id
    try:
        return get_jwt_identity()
    except Exception:
        return None


@account_bp.route("/delete/preflight", methods=["GET"])
def delete_preflight():
    """Step 1 of deletion: which plan they're on, whether Pro must be cancelled first, the reasons to pick from,
    and what they'd lose."""
    user_id = _session_user_id()
    if not user_id:
        return jsonify({"error": "Authentication required"}), 401
    conn = get_db_connection()
    try:
        cursor = conn.cursor(cursor_factory=RealDictCursor)
        cursor.execute("SELECT c.id FROM creators c WHERE c.user_id = %s", (user_id,))
        row = cursor.fetchone()
        creator_id = row["id"] if row else None
        gate = deletion_gate(cursor, creator_id)
        gate["loses"] = _what_they_lose(cursor, creator_id)
        return jsonify({"success": True, **gate})
    except Exception as exc:
        logger.exception("Deletion preflight failed for user_id=%s: %s", user_id, exc)
        return jsonify({"error": "Could not load your account"}), 500
    finally:
        conn.close()


def _what_they_lose(cursor, creator_id):
    """Real counts only; a missing table just leaves the number out."""
    if not creator_id:
        return {}
    queries = {
        "pitches_sent": "SELECT COUNT(*) AS n FROM polly_autopilot_queue WHERE creator_id = %s AND sent_at IS NOT NULL",
        "brands_tracked": "SELECT COUNT(DISTINCT brand_id) AS n FROM polly_tasks WHERE creator_id = %s",
        "kit_views": "SELECT COUNT(*) AS n FROM polly_timeline_events WHERE creator_id = %s AND event_type = 'portfolio_viewed'",
        "brand_replies": "SELECT COUNT(*) AS n FROM polly_timeline_events WHERE creator_id = %s AND event_type LIKE 'brand_replied%%'",
    }
    out = {}
    for key, q in queries.items():
        try:
            cursor.execute("SAVEPOINT lose")
            cursor.execute(q, (creator_id,))
            out[key] = int((cursor.fetchone() or {}).get("n") or 0)
            cursor.execute("RELEASE SAVEPOINT lose")
        except Exception:
            cursor.execute("ROLLBACK TO SAVEPOINT lose")
    return out


@account_bp.route("/delete", methods=["POST", "OPTIONS"])
def delete_account():
    if request.method == "OPTIONS":
        return jsonify({"success": True}), 200

    user_id = _session_user_id()
    if not user_id:
        return jsonify({"error": "Authentication required"}), 401

    body = request.get_json(silent=True) or {}
    conn = None
    cursor = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor(cursor_factory=RealDictCursor)
        try:
            cursor.execute(
                """
                SELECT u.id, u.email, u.role, c.id AS creator_id,
                       c.stripe_customer_id, c.stripe_subscription_id
                FROM users u
                LEFT JOIN creators c ON c.user_id = u.id
                WHERE u.id = %s
                """,
                (user_id,),
            )
        except Exception:
            conn.rollback()
            cursor.execute(
                """
                SELECT u.id, u.email, u.role, c.id AS creator_id,
                       NULL AS stripe_customer_id, NULL AS stripe_subscription_id
                FROM users u
                LEFT JOIN creators c ON c.user_id = u.id
                WHERE u.id = %s
                """,
                (user_id,),
            )
        account = cursor.fetchone()
        if not account:
            return jsonify({"error": "Account not found"}), 404

        if (account.get("role") or "").lower() != "creator":
            return jsonify({"error": "Account deletion is only available for creator accounts."}), 403

        error = validate_deletion_request(body, account.get("email"))
        if error:
            return jsonify({"error": error}), 400

        creator_id = account.get("creator_id")
        gate = deletion_gate(cursor, creator_id)
        if gate["must_cancel_first"]:
            return jsonify({
                "error": "Cancel your Pro plan first. It takes a minute and you keep Pro until the end of what you paid for.",
                "code": "cancel_first",
            }), 409
        email = account.get("email")
        stripe_customer_id = account.get("stripe_customer_id")
        stripe_subscription_id = account.get("stripe_subscription_id")

        cancel_stripe_billing(stripe_customer_id, stripe_subscription_id)
        purge_creator_account(cursor, user_id, creator_id, email, role="creator",
                              reason=str(body.get("reason") or "").strip(), feedback=body.get("feedback"),
                              plan=gate["plan"] if gate["plan"] == "free" else "pro_cancelled")
        conn.commit()
    except Exception as exc:
        logger.exception("Account deletion failed for user_id=%s: %s", user_id, exc)
        if conn:
            conn.rollback()
        return jsonify({"error": "Could not delete your account. Please contact team@newcollab.co."}), 500
    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()

    session.clear()
    response = jsonify({
        "success": True,
        "message": "Your account and personal data have been permanently deleted.",
    })
    try:
        unset_jwt_cookies(response)
    except Exception:
        pass
    cookie_kwargs = {"max_age": 0, "expires": 0, "httponly": True, "path": "/"}
    response.set_cookie("session", "", **cookie_kwargs)
    response.set_cookie("access_token_cookie", "", **cookie_kwargs)
    if os.getenv("FLASK_ENV") == "production" or os.getenv("VERCEL_ENV") == "production":
        cookie_kwargs.update({"domain": ".newcollab.co", "secure": True, "samesite": "None"})
        response.set_cookie("session", "", **cookie_kwargs)
        response.set_cookie("access_token_cookie", "", **cookie_kwargs)
    return response, 200
