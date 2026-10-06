"""Gmail connection for Polly Autopilot: OAuth (send-only scope), token storage, sending."""

from __future__ import annotations

import base64
import os
import time
import uuid
from email.message import EmailMessage
from email.utils import formataddr, make_msgid
from typing import Any, Dict, Optional
from urllib.parse import urlencode

import requests
from psycopg2.extras import RealDictCursor

AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
REVOKE_URL = "https://oauth2.googleapis.com/revoke"
USERINFO_URL = "https://openidconnect.googleapis.com/v1/userinfo"
SEND_URL = "https://gmail.googleapis.com/gmail/v1/users/me/messages/send"
SCOPES = ("openid", "email", "https://www.googleapis.com/auth/gmail.send")
STATE_TTL_SEC = 15 * 60

_TABLE_READY = False
_ACCESS_CACHE: Dict[int, tuple] = {}


class GmailError(Exception):
    """Sending failed. ``revoked`` means the creator must reconnect."""

    def __init__(self, message: str, revoked: bool = False):
        super().__init__(message)
        self.revoked = revoked


def _config() -> Dict[str, str]:
    return {
        "client_id": (os.getenv("GOOGLE_OAUTH_CLIENT_ID") or "").strip(),
        "client_secret": (os.getenv("GOOGLE_OAUTH_CLIENT_SECRET") or "").strip(),
        "redirect_uri": (os.getenv("GOOGLE_OAUTH_REDIRECT_URI") or "").strip(),
    }


def _fernet():
    key = (os.getenv("POLLY_GMAIL_TOKEN_KEY") or os.getenv("SOCIAL_TOKEN_ENCRYPTION_KEY") or "").strip()
    if not key:
        return None
    try:
        from cryptography.fernet import Fernet
    except ImportError:
        return None
    return Fernet(key.encode())


def gmail_configured() -> bool:
    cfg = _config()
    return bool(cfg["client_id"] and cfg["client_secret"] and cfg["redirect_uri"] and _fernet())


def _cursor(conn):
    return conn.cursor(cursor_factory=RealDictCursor)


def ensure_gmail_table(conn) -> None:
    global _TABLE_READY
    if _TABLE_READY:
        return
    cur = _cursor(conn)
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS polly_gmail_accounts (
            creator_id INTEGER PRIMARY KEY,
            email TEXT NOT NULL,
            refresh_token_enc TEXT NOT NULL,
            scopes TEXT,
            connected_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            revoked_at TIMESTAMPTZ,
            last_error TEXT,
            last_sent_at TIMESTAMPTZ
        )
        """
    )
    conn.commit()
    _TABLE_READY = True


# ---------------------------------------------------------------------------
# OAuth
# ---------------------------------------------------------------------------

def _state_secret() -> str:
    return os.getenv("JWT_SECRET_KEY") or os.getenv("SECRET_KEY") or ""


def make_state(creator_id: int) -> str:
    import jwt

    secret = _state_secret()
    if not secret:
        raise GmailError("JWT_SECRET_KEY is not set")
    payload = {"cid": int(creator_id), "purpose": "polly_gmail", "exp": int(time.time()) + STATE_TTL_SEC,
               "n": uuid.uuid4().hex}
    return jwt.encode(payload, secret, algorithm="HS256")


def read_state(state: str) -> Optional[int]:
    import jwt

    try:
        data = jwt.decode(state or "", _state_secret(), algorithms=["HS256"])
    except Exception:
        return None
    if data.get("purpose") != "polly_gmail":
        return None
    try:
        return int(data.get("cid"))
    except (TypeError, ValueError):
        return None


def auth_url(creator_id: int, login_hint: str = "") -> str:
    cfg = _config()
    params = {
        "client_id": cfg["client_id"],
        "redirect_uri": cfg["redirect_uri"],
        "response_type": "code",
        "scope": " ".join(SCOPES),
        "access_type": "offline",
        "prompt": "consent",
        "include_granted_scopes": "true",
        "state": make_state(creator_id),
    }
    if login_hint:
        params["login_hint"] = login_hint
    return f"{AUTH_URL}?{urlencode(params)}"


def exchange_code(code: str) -> Dict[str, Any]:
    cfg = _config()
    resp = requests.post(TOKEN_URL, data={
        "code": code,
        "client_id": cfg["client_id"],
        "client_secret": cfg["client_secret"],
        "redirect_uri": cfg["redirect_uri"],
        "grant_type": "authorization_code",
    }, timeout=15)
    data = resp.json() if resp.content else {}
    if resp.status_code != 200 or not data.get("access_token"):
        raise GmailError(f"token exchange failed: {data.get('error') or resp.status_code}")
    granted = set((data.get("scope") or "").split())
    if "https://www.googleapis.com/auth/gmail.send" not in granted:
        raise GmailError("gmail.send not granted")
    if not data.get("refresh_token"):
        raise GmailError("no refresh token returned")
    info = requests.get(USERINFO_URL, headers={"Authorization": f"Bearer {data['access_token']}"}, timeout=10)
    email = ((info.json() if info.content else {}) or {}).get("email") or ""
    if not email:
        raise GmailError("could not read Gmail address")
    return {"email": email, "refresh_token": data["refresh_token"], "scope": data.get("scope") or "",
            "access_token": data["access_token"], "expires_in": int(data.get("expires_in") or 3000)}


def save_account(conn, creator_id: int, tokens: Dict[str, Any]) -> None:
    f = _fernet()
    if not f:
        raise GmailError("token encryption key missing")
    ensure_gmail_table(conn)
    cur = _cursor(conn)
    cur.execute(
        """
        INSERT INTO polly_gmail_accounts (creator_id, email, refresh_token_enc, scopes, connected_at)
        VALUES (%s, %s, %s, %s, NOW())
        ON CONFLICT (creator_id) DO UPDATE
          SET email = EXCLUDED.email, refresh_token_enc = EXCLUDED.refresh_token_enc,
              scopes = EXCLUDED.scopes, connected_at = NOW(), revoked_at = NULL, last_error = NULL
        """,
        (creator_id, tokens["email"], f.encrypt(tokens["refresh_token"].encode()).decode(), tokens.get("scope")),
    )
    conn.commit()
    _ACCESS_CACHE[int(creator_id)] = (time.time() + tokens.get("expires_in", 3000) - 120, tokens["access_token"])


def load_account(conn, creator_id: int) -> Optional[Dict[str, Any]]:
    ensure_gmail_table(conn)
    cur = _cursor(conn)
    cur.execute(
        "SELECT creator_id, email, connected_at, revoked_at, last_error, last_sent_at "
        "FROM polly_gmail_accounts WHERE creator_id = %s",
        (creator_id,),
    )
    row = cur.fetchone()
    return dict(row) if row else None


def _mark_revoked(conn, creator_id: int, error: str) -> None:
    cur = _cursor(conn)
    cur.execute(
        "UPDATE polly_gmail_accounts SET revoked_at = NOW(), last_error = %s WHERE creator_id = %s",
        (error[:300], creator_id),
    )
    conn.commit()
    _ACCESS_CACHE.pop(int(creator_id), None)


def disconnect(conn, creator_id: int) -> None:
    ensure_gmail_table(conn)
    cur = _cursor(conn)
    cur.execute("SELECT refresh_token_enc FROM polly_gmail_accounts WHERE creator_id = %s", (creator_id,))
    row = cur.fetchone()
    f = _fernet()
    if row and f:
        try:
            token = f.decrypt(row["refresh_token_enc"].encode()).decode()
            requests.post(REVOKE_URL, data={"token": token}, timeout=10)
        except Exception as err:
            print(f"[Polly gmail] revoke skipped: {err}")
    cur.execute("DELETE FROM polly_gmail_accounts WHERE creator_id = %s", (creator_id,))
    conn.commit()
    _ACCESS_CACHE.pop(int(creator_id), None)


def _access_token(conn, creator_id: int) -> str:
    hit = _ACCESS_CACHE.get(int(creator_id))
    if hit and hit[0] > time.time():
        return hit[1]
    cur = _cursor(conn)
    cur.execute(
        "SELECT refresh_token_enc FROM polly_gmail_accounts WHERE creator_id = %s AND revoked_at IS NULL",
        (creator_id,),
    )
    row = cur.fetchone()
    f = _fernet()
    if not row or not f:
        raise GmailError("Gmail not connected", revoked=True)
    cfg = _config()
    resp = requests.post(TOKEN_URL, data={
        "client_id": cfg["client_id"],
        "client_secret": cfg["client_secret"],
        "refresh_token": f.decrypt(row["refresh_token_enc"].encode()).decode(),
        "grant_type": "refresh_token",
    }, timeout=15)
    data = resp.json() if resp.content else {}
    if resp.status_code != 200 or not data.get("access_token"):
        err = data.get("error") or f"http {resp.status_code}"
        if err in ("invalid_grant", "unauthorized_client"):
            _mark_revoked(conn, creator_id, err)
            raise GmailError("Gmail access was revoked", revoked=True)
        raise GmailError(f"token refresh failed: {err}")
    _ACCESS_CACHE[int(creator_id)] = (time.time() + int(data.get("expires_in") or 3000) - 120, data["access_token"])
    return data["access_token"]


# ---------------------------------------------------------------------------
# Sending
# ---------------------------------------------------------------------------

def build_raw(from_email: str, from_name: str, to_email: str, subject: str, body: str,
              in_reply_to: str = "", message_id: str = "") -> Dict[str, str]:
    msg = EmailMessage()
    msg["From"] = formataddr((from_name, from_email)) if from_name else from_email
    msg["To"] = to_email
    msg["Subject"] = subject
    msg["Message-ID"] = message_id or make_msgid(domain=(from_email.split("@")[-1] or "gmail.com"))
    if in_reply_to:
        msg["In-Reply-To"] = in_reply_to
        msg["References"] = in_reply_to
    msg.set_content(body)
    raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()
    return {"raw": raw, "message_id": msg["Message-ID"]}


def send_email(conn, creator_id: int, to_email: str, subject: str, body: str, from_name: str = "",
               thread_id: str = "", in_reply_to: str = "") -> Dict[str, str]:
    """Send from the creator's Gmail. Returns Gmail ids plus the RFC Message-ID for threading."""
    account = load_account(conn, creator_id)
    if not account or account.get("revoked_at"):
        raise GmailError("Gmail not connected", revoked=True)
    token = _access_token(conn, creator_id)
    built = build_raw(account["email"], from_name, to_email, subject, body, in_reply_to=in_reply_to)
    payload: Dict[str, Any] = {"raw": built["raw"]}
    if thread_id:
        payload["threadId"] = thread_id
    resp = requests.post(SEND_URL, json=payload, headers={"Authorization": f"Bearer {token}"}, timeout=20)
    data = resp.json() if resp.content else {}
    if resp.status_code in (401, 403):
        _mark_revoked(conn, creator_id, f"send http {resp.status_code}")
        raise GmailError("Gmail access was revoked", revoked=True)
    if resp.status_code != 200 or not data.get("id"):
        raise GmailError(f"send failed: {(data.get('error') or {}).get('message') or resp.status_code}")
    cur = _cursor(conn)
    cur.execute("UPDATE polly_gmail_accounts SET last_sent_at = NOW(), last_error = NULL WHERE creator_id = %s",
                (creator_id,))
    conn.commit()
    return {"gmail_id": data["id"], "thread_id": data.get("threadId") or "", "message_id": built["message_id"]}
