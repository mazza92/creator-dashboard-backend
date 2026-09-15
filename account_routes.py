"""Creator self-serve account deletion."""

import os
import logging

import psycopg2
from flask import Blueprint, jsonify, request, session
from flask_jwt_extended import get_jwt_identity, unset_jwt_cookies
from psycopg2.extras import RealDictCursor

from services.account_deletion import (
    cancel_stripe_billing,
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
        email = account.get("email")
        stripe_customer_id = account.get("stripe_customer_id")
        stripe_subscription_id = account.get("stripe_subscription_id")

        cancel_stripe_billing(stripe_customer_id, stripe_subscription_id)
        purge_creator_account(cursor, user_id, creator_id, email, role="creator")
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
