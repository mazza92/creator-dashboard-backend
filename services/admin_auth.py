"""Admin authentication shared by every X-Admin-Token gate.

Two kinds of token are accepted:
- the static ADMIN_TOKEN env var (Hermes agents, scripts, internal crons)
- a short-lived signed token minted by POST /api/admin/login (the admin pages)

No token value lives in code. If ADMIN_TOKEN is unset, only signed logins work.
"""

from __future__ import annotations

import hmac
import os
import time
from typing import Optional

import jwt as pyjwt
from flask import Blueprint, current_app, jsonify, request

ADMIN_EMAIL = "team@newcollab.co"
LOGIN_TTL_SECONDS = 12 * 3600
MAX_FAILED_LOGINS = 10
FAILED_LOGIN_WINDOW = 15 * 60
_PURPOSE = "admin"

admin_auth_bp = Blueprint("admin_auth", __name__)


def _secret() -> str:
    return os.getenv("JWT_SECRET_KEY") or ""


def static_admin_token() -> str:
    return (os.getenv("ADMIN_TOKEN") or "").strip()


def _same(a: str, b: str) -> bool:
    return bool(a) and bool(b) and hmac.compare_digest(a.encode("utf-8"), b.encode("utf-8"))


def issue_admin_token(now: Optional[int] = None) -> str:
    now = int(now or time.time())
    token = pyjwt.encode(
        {"sub": ADMIN_EMAIL, "purpose": _PURPOSE, "iat": now, "exp": now + LOGIN_TTL_SECONDS},
        _secret(),
        algorithm="HS256",
    )
    return token.decode("utf-8") if isinstance(token, bytes) else token


def _signed_token_valid(token: str) -> bool:
    secret = _secret()
    if not secret or token.count(".") != 2:
        return False
    try:
        payload = pyjwt.decode(token, secret, algorithms=["HS256"])
    except pyjwt.InvalidTokenError:
        return False
    return payload.get("purpose") == _PURPOSE and payload.get("sub") == ADMIN_EMAIL


def admin_token_valid(token: Optional[str]) -> bool:
    token = (token or "").strip()
    if not token:
        return False
    if _same(token, static_admin_token()):
        return True
    return _signed_token_valid(token)


def request_admin_token() -> str:
    return (request.headers.get("X-Admin-Token") or "").strip()


def request_has_admin_token() -> bool:
    return admin_token_valid(request_admin_token())


def _redis():
    try:
        return current_app.config.get("SESSION_REDIS")
    except Exception:
        return None


def _client_ip() -> str:
    fwd = request.headers.get("X-Forwarded-For") or ""
    return (fwd.split(",")[0].strip() or request.remote_addr or "unknown")[:64]


def _failed_key() -> str:
    return f"admin_login_failed:{_client_ip()}"


def _too_many_failures() -> bool:
    r = _redis()
    if not r:
        return False
    try:
        return int(r.get(_failed_key()) or 0) >= MAX_FAILED_LOGINS
    except Exception:
        return False


def _record_failure() -> None:
    r = _redis()
    if not r:
        return
    try:
        key = _failed_key()
        r.incr(key)
        r.expire(key, FAILED_LOGIN_WINDOW)
    except Exception:
        pass


def check_admin_login(email: str, password: str) -> bool:
    expected = os.getenv("ADMIN_PASSWORD") or ""
    return (email or "").strip().lower() == ADMIN_EMAIL and _same(password or "", expected)


@admin_auth_bp.route("/api/admin/login", methods=["POST"])
def admin_login():
    if _too_many_failures():
        return jsonify({"success": False, "error": "Too many attempts. Try again in 15 minutes."}), 429
    if not os.getenv("ADMIN_PASSWORD") or not _secret():
        print("[admin-auth] login unavailable: ADMIN_PASSWORD or JWT_SECRET_KEY not set")
        return jsonify({"success": False, "error": "Admin login is not configured."}), 503
    data = request.get_json(silent=True) or {}
    if not check_admin_login(str(data.get("email") or ""), str(data.get("password") or "")):
        _record_failure()
        return jsonify({"success": False, "error": "Invalid credentials"}), 403
    return jsonify({"success": True, "token": issue_admin_token(), "expires_in": LOGIN_TTL_SECONDS})
