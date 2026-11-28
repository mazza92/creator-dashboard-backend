"""Email verification by 6-digit code at signup (Nov 2026).

The app is open to creators before review (services.verification), so every email signup proves its inbox before
onboarding: a 6-digit code, typed on the next screen (no link, no leaving the app). Google sign-ups are already
verified by Google and never get a code.

- issue(): new code, sent through Resend (not Gmail SMTP, whose daily quota paused link verification). 10 minutes,
  5 tries; a new one at most every 30 seconds and 5 an hour.
- check(): the code they typed. Right: users.is_verified, the gate lifts.
- pending(): this signup still owes a code. Only accounts that were issued one are gated: older accounts and
  Google sign-ups are never affected. Never raises (a broken check never locks anyone out).
- If the code email can't be sent at signup, the account is let through (verified), as before: signups are never
  blocked by our own email outage.
"""

from __future__ import annotations

import hashlib
import hmac
import os
import secrets
from datetime import datetime, timedelta, timezone
from html import escape
from typing import Any, Dict, Optional

TTL = timedelta(minutes=10)
MAX_TRIES = 5
COOLDOWN = timedelta(seconds=30)
MAX_PER_HOUR = 5

SCHEMA_SQL = """
    CREATE TABLE IF NOT EXISTS email_codes (
        user_id INTEGER PRIMARY KEY,
        email TEXT NOT NULL,
        code_hash TEXT NOT NULL,
        expires_at TIMESTAMPTZ NOT NULL,
        tries INTEGER NOT NULL DEFAULT 0,
        sent_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        window_start TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        sends_in_window INTEGER NOT NULL DEFAULT 0,
        verified_at TIMESTAMPTZ
    )
"""


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _aware(value):
    if value is not None and getattr(value, "tzinfo", None) is None:
        return value.replace(tzinfo=timezone.utc)
    return value


def _hash(user_id: int, code: str) -> str:
    secret = (os.getenv("FLASK_SECRET_KEY") or os.getenv("SECRET_KEY") or "newcollab").encode()
    return hmac.new(secret, f"{int(user_id)}:{code}".encode(), hashlib.sha256).hexdigest()


def new_code() -> str:
    return f"{secrets.randbelow(10 ** 6):06d}"


def ensure_schema(conn) -> None:
    cur = conn.cursor()
    cur.execute(SCHEMA_SQL)
    conn.commit()


def _row(conn, user_id: int) -> Optional[Dict[str, Any]]:
    cur = conn.cursor()
    cur.execute("""SELECT email, code_hash, expires_at, tries, sent_at, window_start, sends_in_window, verified_at
                   FROM email_codes WHERE user_id = %s""", (int(user_id),))
    row = cur.fetchone()
    if not row:
        return None
    keys = ("email", "code_hash", "expires_at", "tries", "sent_at", "window_start", "sends_in_window", "verified_at")
    data = dict(row) if isinstance(row, dict) else dict(zip(keys, row))
    for k in ("expires_at", "sent_at", "window_start", "verified_at"):
        data[k] = _aware(data.get(k))
    return data


def pending(conn, user_id: Optional[int]) -> bool:
    if not user_id:
        return False
    try:
        ensure_schema(conn)
        row = _row(conn, user_id)
        return bool(row) and row.get("verified_at") is None
    except Exception as err:  # noqa: BLE001
        print(f"[email_code] pending check skipped user={user_id}: {err}")
        try:
            conn.rollback()
        except Exception:
            pass
        return False


def email_html(code: str, first_name: str = "") -> str:
    hi = f"Hi {escape(first_name)}," if first_name else "Hi,"
    return f"""<!doctype html><html><body style="margin:0;background:#f6f5f2;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Arial,sans-serif;color:#12141a">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0"><tr><td align="center" style="padding:32px 16px">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="max-width:440px;background:#ffffff;border-radius:16px;padding:32px">
<tr><td style="font-size:15px;line-height:1.5">{hi}</td></tr>
<tr><td style="font-size:15px;line-height:1.5;padding-top:8px">Here's your code to finish signing up to Newcollab:</td></tr>
<tr><td align="center" style="padding:24px 0"><div style="display:inline-block;font-size:34px;letter-spacing:10px;font-weight:800;background:#eef6f3;color:#095a46;border-radius:12px;padding:14px 22px">{escape(code)}</div></td></tr>
<tr><td style="font-size:13px;line-height:1.5;color:#5c6470">It expires in 10 minutes. If you didn't sign up to Newcollab, you can ignore this email.</td></tr>
</table></td></tr></table></body></html>"""


def _send(email: str, code: str, first_name: str = "") -> bool:
    from services.resend_mail import send_resend_email
    out = send_resend_email(email, f"{code} is your Newcollab code", email_html(code, first_name),
                            tags=[{"name": "type", "value": "email_code"}])
    if not out.get("success"):
        print(f"[email_code] send failed: {out.get('error')}")
    return bool(out.get("success"))


def issue(conn, user_id: int, email: str, first_name: str = "", force: bool = False) -> Dict[str, Any]:
    """{'sent': bool, 'retry_in': seconds, 'reason': str}. force skips the cooldown (signup's first code)."""
    ensure_schema(conn)
    now = _now()
    row = _row(conn, user_id)
    if row and row.get("verified_at"):
        return {"sent": False, "retry_in": 0, "reason": "verified"}
    window_start, sends = now, 0
    if row:
        if not force and row["sent_at"] and now - row["sent_at"] < COOLDOWN:
            return {"sent": False, "retry_in": int((COOLDOWN - (now - row["sent_at"])).total_seconds()) + 1,
                    "reason": "cooldown"}
        window_start, sends = row["window_start"] or now, int(row.get("sends_in_window") or 0)
        if now - window_start >= timedelta(hours=1):
            window_start, sends = now, 0
        if sends >= MAX_PER_HOUR:
            return {"sent": False, "retry_in": int((window_start + timedelta(hours=1) - now).total_seconds()) + 1,
                    "reason": "hourly_limit"}
    code = new_code()
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO email_codes (user_id, email, code_hash, expires_at, tries, sent_at, window_start, sends_in_window)
        VALUES (%s, %s, %s, %s, 0, %s, %s, %s)
        ON CONFLICT (user_id) DO UPDATE SET email = EXCLUDED.email, code_hash = EXCLUDED.code_hash,
            expires_at = EXCLUDED.expires_at, tries = 0, sent_at = EXCLUDED.sent_at,
            window_start = EXCLUDED.window_start, sends_in_window = EXCLUDED.sends_in_window
    """, (int(user_id), email, _hash(user_id, code), now + TTL, now, window_start, sends + 1))
    conn.commit()
    sent = _send(email, code, first_name)
    return {"sent": sent, "retry_in": int(COOLDOWN.total_seconds()), "reason": "sent" if sent else "send_failed"}


def check(conn, user_id: int, code: str) -> str:
    """'ok' | 'wrong' | 'expired' | 'too_many' | 'none'."""
    ensure_schema(conn)
    row = _row(conn, user_id)
    if not row:
        return "none"
    if row.get("verified_at"):
        return "ok"
    if int(row.get("tries") or 0) >= MAX_TRIES:
        return "too_many"
    if row["expires_at"] and _now() > row["expires_at"]:
        return "expired"
    typed = "".join(ch for ch in str(code or "") if ch.isdigit())
    cur = conn.cursor()
    if len(typed) != 6 or not hmac.compare_digest(_hash(user_id, typed), row["code_hash"]):
        cur.execute("UPDATE email_codes SET tries = tries + 1 WHERE user_id = %s", (int(user_id),))
        conn.commit()
        return "too_many" if int(row.get("tries") or 0) + 1 >= MAX_TRIES else "wrong"
    cur.execute("UPDATE email_codes SET verified_at = NOW() WHERE user_id = %s", (int(user_id),))
    cur.execute("UPDATE users SET is_verified = TRUE WHERE id = %s", (int(user_id),))
    conn.commit()
    return "ok"


MESSAGES = {
    "wrong": "That code isn't right. Check the email and try again.",
    "expired": "That code has expired. We can send you a new one.",
    "too_many": "Too many tries. Send yourself a new code.",
    "none": "Send yourself a new code to continue.",
    "cooldown": "Wait a few seconds before sending another code.",
    "hourly_limit": "You've asked for a lot of codes. Try again in a little while.",
    "send_failed": "We couldn't send the email. Try again in a moment.",
}
