"""Brand emails for a gifted campaign: received (in review) and live. Same branded template as the roster emails.

Brands with a workspace account are sent to /brand/workspace (scan competitors, manage their roster there).
Brands without one (the MCP /brands/launch flow) keep the private no-login roster link.
"""

from __future__ import annotations

from html import escape
from typing import Any, Dict, Optional
from urllib.parse import urlencode

from services.roster_delivery import _p, _steps, _wrap
from services.roster_waiting_email import BRAND_FOOTER_NOTE, frontend_base, safe_brand_name

WORKSPACE_PATH = "/brand/workspace"


def _link(path: str, content: str) -> str:
    query = {"utm_source": "email", "utm_medium": "brand_campaign", "utm_campaign": content}
    return f"{frontend_base().rstrip('/')}{path}?{urlencode(query)}"


def _hi(first_name: Optional[str], brand: str) -> str:
    first = str(first_name or "").strip().split(" ")[0]
    if first and first.lower() not in ("none", "null"):
        return f"Hi {escape(first)},"
    return f"Hi {escape(brand)} team,"


def received_email(*, brand_name: str, product_name: str, first_name: Optional[str] = None,
                   has_workspace: bool = True) -> Dict[str, str]:
    """Right after a brand submits a campaign. It's in review until an admin publishes it."""
    brand = safe_brand_name(brand_name)
    product = escape(str(product_name or "your product").strip()[:80])
    subject = f"We got your {str(product_name or 'gifted').strip()[:60]} campaign"
    body = [
        _p(_hi(first_name, brand)),
        _p(f"Thanks for starting your gifted campaign for <b>{product}</b>. We review new campaigns within 24 hours, "
           "then it goes live to vetted creators in your niche."),
    ]
    if has_workspace:
        body += [
            _p("While you wait, your workspace is ready:"),
            _steps([
                "<b>Scan a competitor</b> to see the TikTok creators posting for them and the hooks they reuse.",
                "<b>Pick your creators</b> as applications come in, then lock your list.",
                "<b>Ship and collect</b> ad-ready videos you can run for 6 months.",
            ]),
        ]
        url, action = _link(WORKSPACE_PATH, "campaign_received"), "Open my workspace"
        preheader = "Scan your competitors while we review it."
    else:
        body.append(_p("We'll email your private creator roster link as soon as it's live. You pick up to 5 creators, "
                       "then export a shipping CSV. No platform fee on your first campaign."))
        url, action = "https://newcollab.co/brands/scan?utm_source=email&utm_campaign=campaign_received", "Scan a competitor"
        preheader = "Your campaign is in review."
    return {"subject": subject, "html": _wrap(subject, preheader, "".join(body), url, action, BRAND_FOOTER_NOTE)}


def live_email(*, brand_name: str, product_name: str, roster_url: Optional[str], first_name: Optional[str] = None,
               has_workspace: bool = True) -> Dict[str, str]:
    """When an admin publishes the campaign and its roster goes live."""
    brand = safe_brand_name(brand_name)
    product = escape(str(product_name or "your product").strip()[:80])
    subject = f"Your {str(product_name or 'gifted').strip()[:60]} campaign is live"
    body = [
        _p(_hi(first_name, brand)),
        _p(f"Your gifted campaign for <b>{product}</b> is live. Vetted creators in your niche start applying "
           "within 24 hours."),
        _steps([
            "Review who applied and pick up to 5 creators.",
            "Lock your list to unlock shipping details.",
            "Ship your product. Each creator posts and delivers a UGC video you can run as ads for 6 months.",
        ]),
    ]
    if has_workspace or not roster_url:
        url = _link(f"{WORKSPACE_PATH}/roster", "campaign_live")
    else:
        url = f"{roster_url}?{urlencode({'utm_source': 'email', 'utm_campaign': 'campaign_live'})}"
    return {"subject": subject,
            "html": _wrap(subject, "Creators can apply now.", "".join(body), url, "Pick my creators", BRAND_FOOTER_NOTE)}


def workspace_account(cursor, email: str) -> Dict[str, Any]:
    """{exists, first_name} for the brand account behind this campaign email."""
    try:
        cursor.execute("SELECT first_name FROM users WHERE LOWER(email) = LOWER(%s) AND role = 'brand' LIMIT 1",
                       (email,))
        row = cursor.fetchone()
    except Exception:
        return {"exists": False, "first_name": None}
    if not row:
        return {"exists": False, "first_name": None}
    first = row["first_name"] if isinstance(row, dict) else row[0]
    return {"exists": True, "first_name": first}


def send(to_email: str, mail: Dict[str, str], kind: str) -> bool:
    from services.resend_mail import send_resend_email

    try:
        result = send_resend_email(to_email, mail["subject"], mail["html"], tags=[{"name": "type", "value": kind}])
    except Exception as err:
        print(f"[brand campaign email] {kind} skipped for {to_email}: {err}")
        return False
    return bool((result or {}).get("success"))
