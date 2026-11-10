"""Send a creator's pitches without Gmail: from "Name via Newcollab", replies straight to the creator's inbox.

Why (Nov 2026 audit): 10 of 18 Pro members who cancelled never connected Gmail, so Autopilot sent nothing for
them, even after 5 reminders each. Some use Outlook/iCloud/Yahoo, some won't grant Gmail access. Like Calendly or
Upwork, Polly can send on their behalf from our own sending domain, with Reply-To set to the creator's email, so
brands answer the creator directly. It only runs when the creator opted in (polly_autopilot.send_via =
'newcollab') and the relay domain is configured.

The relay domain must be a dedicated subdomain verified in Resend (e.g. send.newcollab.co) with click/open
tracking off, so cold pitches never touch the reputation of the domain that sends account and billing emails.
"""

from __future__ import annotations

import os
import re
from email.utils import formataddr, make_msgid
from typing import Any, Dict, Optional

import requests

RESEND_API_URL = "https://api.resend.com/emails"
SEND_VIA_NEWCOLLAB = "newcollab"
SEND_VIA_GMAIL = "gmail"


def relay_domain() -> str:
    return (os.getenv("PITCH_RELAY_DOMAIN") or "").strip().lower()


def relay_available() -> bool:
    return bool(relay_domain() and (os.getenv("RESEND_API_KEY") or "").strip())


def _local_part(name: str, creator_id: int) -> str:
    slug = re.sub(r"[^a-z0-9]+", ".", (name or "").lower()).strip(".")[:30] or "creator"
    return f"{slug}.{creator_id}"


def creator_reply_to(conn, creator_id: int) -> Optional[str]:
    cur = conn.cursor()
    cur.execute("""SELECT u.email FROM creators c JOIN users u ON u.id = c.user_id WHERE c.id = %s""", (creator_id,))
    row = cur.fetchone()
    email = (row[0] if row and not isinstance(row, dict) else (row or {}).get("email")) or ""
    return email.strip() or None


def send_email(conn, creator_id: int, to_email: str, subject: str, body: str, from_name: str = "",
               thread_id: str = "", in_reply_to: str = "", http=None) -> Dict[str, str]:
    """Same contract as polly_gmail.send_email, so Autopilot can use either."""
    from services.polly_gmail import GmailError

    if not relay_available():
        raise GmailError("Sending without Gmail isn't switched on", revoked=True)
    reply_to = creator_reply_to(conn, creator_id)
    if not reply_to:
        raise GmailError("No reply-to address on this account", revoked=True)
    domain = relay_domain()
    name = (from_name or "").strip() or "A creator"
    from_addr = f"{_local_part(name, creator_id)}@{domain}"
    message_id = make_msgid(domain=domain)
    headers: Dict[str, Any] = {"Message-ID": message_id}
    if in_reply_to:
        headers["In-Reply-To"] = in_reply_to
        headers["References"] = in_reply_to
    payload = {
        "from": formataddr((f"{name} via Newcollab", from_addr)),
        "to": [to_email],
        "reply_to": [reply_to],
        "subject": subject,
        "text": body,
        "headers": headers,
        "tags": [{"name": "kind", "value": "pitch_relay"}, {"name": "creator", "value": str(creator_id)}],
    }
    post = http or requests.post
    try:
        resp = post(RESEND_API_URL, json=payload, timeout=20,
                    headers={"Authorization": f"Bearer {os.getenv('RESEND_API_KEY', '').strip()}"})
    except requests.RequestException as err:
        raise GmailError(f"send failed: {err}")
    data = {}
    try:
        data = resp.json() or {}
    except ValueError:
        pass
    if resp.status_code not in (200, 201) or not data.get("id"):
        raise GmailError(f"send failed: {data.get('message') or resp.status_code}")
    return {"gmail_id": f"relay:{data['id']}", "thread_id": "", "message_id": message_id}


def send_via(conn, creator_id: int) -> str:
    cur = conn.cursor()
    try:
        cur.execute("SELECT send_via FROM polly_autopilot WHERE creator_id = %s", (creator_id,))
        row = cur.fetchone()
    except Exception:
        conn.rollback()
        return SEND_VIA_GMAIL
    value = row[0] if row and not isinstance(row, dict) else (row or {}).get("send_via")
    return value or SEND_VIA_GMAIL


def send_for_creator(conn, creator_id: int, to_email: str, subject: str, body: str, from_name: str = "",
                     thread_id: str = "", in_reply_to: str = "") -> Dict[str, str]:
    """Gmail when it's connected (the pitch comes from their own inbox, best for replies), otherwise Newcollab
    sending if they chose it. A follow-up to a relay pitch goes through the relay too, so it threads."""
    from services.polly_gmail import GmailError, load_account
    from services.polly_gmail import send_email as gmail_send

    relay_thread = bool(in_reply_to) and not thread_id
    account = load_account(conn, creator_id)
    gmail_ok = bool(account and not account.get("revoked_at"))
    if gmail_ok and not (relay_thread and send_via(conn, creator_id) == SEND_VIA_NEWCOLLAB):
        return gmail_send(conn, creator_id, to_email, subject, body, from_name=from_name,
                          thread_id=thread_id, in_reply_to=in_reply_to)
    if send_via(conn, creator_id) == SEND_VIA_NEWCOLLAB:
        return send_email(conn, creator_id, to_email, subject, body, from_name=from_name,
                          thread_id=thread_id, in_reply_to=in_reply_to)
    raise GmailError("Gmail not connected", revoked=True)
