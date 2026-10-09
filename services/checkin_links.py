"""One-tap outcome links for Polly emails: the tap itself records what happened.

Creators don't come back to update their pipeline, and we can't read their inbox, so
every check-in email carries signed links that log the outcome without a login.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import time
from typing import Any, Dict, Optional
from urllib.parse import urlencode

TOKEN_TTL_SECONDS = 30 * 24 * 3600

# Outcome chip -> the Polly chip to open afterwards (must exist in Polly.js DEEP_LINK_CHIPS).
ONE_TAP_NEXT = {
    "checkin_interested": "help_reply",
    "checkin_question": "help_reply",
    "checkin_passed": "line_up",
    "checkin_quiet": "draft_followup",
    "checkin_not_sent": "send_draft",
    "checkin_bounced": "line_up",
    "pr_arrived": "need_idea",
    "pr_not_yet": "",
}

CHECKIN_BUTTONS = (
    ("checkin_interested", "They're interested"),
    ("checkin_passed", "They said no"),
    ("checkin_quiet", "No reply yet"),
)
CHECKIN_LINKS = (
    ("checkin_question", "They asked a question"),
    ("checkin_bounced", "Email bounced"),
    ("checkin_not_sent", "I never sent it"),
)
PR_BUTTONS = (
    ("pr_arrived", "Yes, it arrived"),
    ("pr_not_yet", "Not yet"),
)


def _secret() -> str:
    return os.getenv("JWT_SECRET_KEY") or os.getenv("SECRET_KEY") or ""


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def _sign(body: str, secret: str) -> str:
    return hmac.new(secret.encode(), f"checkin:{body}".encode(), hashlib.sha256).hexdigest()[:32]


def make_token(creator_id: int, chip_id: str, task_id: Any = None, brand_id: Any = None,
               brand_name: str = "", now: Optional[float] = None) -> str:
    secret = _secret()
    if not secret or chip_id not in ONE_TAP_NEXT or not creator_id:
        return ""
    payload = [int(creator_id), chip_id, int(task_id or 0), int(brand_id or 0),
               (brand_name or "")[:80], int((now or time.time()) + TOKEN_TTL_SECONDS)]
    body = _b64(json.dumps(payload, separators=(",", ":")).encode())
    return f"{body}.{_sign(body, secret)}"


def read_token(token: str, now: Optional[float] = None) -> Optional[Dict[str, Any]]:
    secret = _secret()
    if not secret or not token or "." not in token:
        return None
    body, sig = token.rsplit(".", 1)
    if not hmac.compare_digest(_sign(body, secret), sig):
        return None
    try:
        cid, chip, task_id, brand_id, name, exp = json.loads(_unb64(body))
    except (ValueError, TypeError):
        return None
    if exp < (now or time.time()) or chip not in ONE_TAP_NEXT:
        return None
    return {
        "creator_id": cid, "chip_id": chip, "task_id": task_id or None,
        "brand_id": brand_id or None, "brand_name": name, "token_id": sig,
    }


def backend_url() -> str:
    return (os.getenv("BACKEND_URL") or "https://api.newcollab.co").rstrip("/")


def one_tap_url(creator_id: int, chip_id: str, task_id: Any = None, brand_id: Any = None,
                brand_name: str = "", kind: str = "") -> str:
    token = make_token(creator_id, chip_id, task_id, brand_id, brand_name)
    if not token:
        return ""
    params = {"t": token}
    if kind:
        params["src"] = kind[:24]
    return f"{backend_url()}/api/polly/checkin?{urlencode(params)}"


def next_polly_url(frontend: str, info: Dict[str, Any], kind: str = "") -> str:
    params = {"utm_source": "email", "utm_medium": "polly_alert", "utm_campaign": kind or "checkin",
              "logged": info["chip_id"]}
    nxt = ONE_TAP_NEXT.get(info["chip_id"])
    if nxt:
        params["chip"] = nxt
        if info.get("brand_id"):
            params["brand_id"] = info["brand_id"]
        if info.get("brand_name"):
            params["brand_name"] = info["brand_name"]
    return f"{frontend.rstrip('/')}/creator/dashboard/for-you?{urlencode(params)}"
