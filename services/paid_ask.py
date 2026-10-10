"""The reply that turns a gifted yes into paid usage: shipping details plus a usage-rights rate.

Small creators can charge for brands running their video as an ad even when the product is
gifted. 30 days of paid usage on a 15-30s video is the standard first ask.
"""

from __future__ import annotations

from typing import Dict, List, Optional
from urllib.parse import quote

USAGE_DAYS = 30
WHITELIST_EXTRA = 100
ADDRESS_LINE = "[Street address]"


def usage_rate(followers: Optional[int] = None) -> int:
    try:
        n = int(followers or 0)
    except (TypeError, ValueError):
        n = 0
    if n >= 25000:
        return 300
    if n >= 10000:
        return 250
    if n >= 2000:
        return 200
    return 150


def _usage_offer(rate: int) -> str:
    return (
        f"I offer {USAGE_DAYS} days of paid usage for ${rate}. "
        f"Whitelisting (running it from my handle) is +${WHITELIST_EXTRA}."
    )


def address_lines(address: Optional[Dict] = None, name: str = "", ship_to: str = "") -> List[str]:
    """The saved shipping address, or name + a street placeholder + city/country."""
    addr = address if isinstance(address, dict) else {}
    who = str(addr.get("full_name") or name or "").strip()
    line1 = str(addr.get("address_line1") or "").strip()
    if not line1:
        return [l for l in (who, ADDRESS_LINE, (ship_to or "").strip()) if l]
    town = " ".join(
        str(addr.get(k) or "").strip() for k in ("city", "state", "postal_code") if str(addr.get(k) or "").strip()
    )
    rest = [str(addr.get("address_line2") or "").strip(), town, str(addr.get("country") or "").strip()]
    return [l for l in [who, line1] + rest if l]


def interested_reply(brand_name: str, first: str = "", full_name: str = "", ship_to: str = "",
                     followers: Optional[int] = None, subject: str = "",
                     address: Optional[Dict] = None) -> Dict[str, str]:
    """They said yes to the gifted pitch: say thanks, give the address, put the usage rate on the table."""
    brand = (brand_name or "").strip() or "there"
    sign = (first or "").strip() or ((full_name or "").strip().split(" ")[0] if (full_name or "").strip() else "")
    lines = address_lines(address, (full_name or first or "").strip(), ship_to)
    body = "\n\n".join([
        f"Hi {brand} team,",
        "Thank you, I'd love to! Here's where to send it:\n" + "\n".join(lines),
        "I'll post within 14 days of it arriving and send you the link and the raw file.",
        "If you'd like to run the video as an ad, " + _usage_offer(usage_rate(followers))
        + " Happy to sort that now or once you've seen the video.",
        "Thanks again," + (f"\n{sign}" if sign else ""),
    ])
    return {"subject": _re(subject, brand), "body": body}


def ad_usage_ask(brand_name: str, first: str = "", followers: Optional[int] = None,
                 subject: str = "") -> Dict[str, str]:
    """After they post: ask whether the brand wants to run the video as an ad."""
    brand = (brand_name or "").strip() or "there"
    sign = (first or "").strip()
    body = "\n\n".join([
        f"Hi {brand} team,",
        "The video's live. Thanks again for sending the product, it was a fun one to make.",
        "Would you like to run it as an ad? " + _usage_offer(usage_rate(followers))
        + " I'll send the raw file without captions so it drops straight into Ads Manager.",
        "Want me to send over an invoice?" + (f"\n\n{sign}" if sign else ""),
    ])
    return {"subject": _re(subject, brand), "body": body}


def _re(subject: str, brand: str) -> str:
    subject = (subject or "").strip()
    if subject:
        return subject if subject.lower().startswith("re:") else f"Re: {subject}"
    return f"Re: {brand} collab"


def mailto(email: str, subject: str, body: str) -> str:
    if not email:
        return ""
    return f"mailto:{email}?subject={quote(subject)}&body={quote(body)}"


def free_teaser(brand_name: str, followers: Optional[int] = None, posted: bool = False) -> str:
    """Free plan: the playbook in one line, with the written reply on Pro."""
    name = (brand_name or "").strip() or "them"
    rate = usage_rate(followers)
    if posted:
        how = (
            f"Reply to **{name}** with the link and one line: \"Would you like to run it as an ad? "
            f"{USAGE_DAYS} days of usage is ${rate}.\""
        )
    else:
        how = (
            f"Reply to **{name}** with a thank-you, your shipping address, and one line: \"After I post, "
            f"would you like to run it as an ad? {USAGE_DAYS} days of usage is ${rate}.\""
        )
    return (
        f"{how} That's how a gifted yes turns into paid.\n\n"
        "On Pro I write that reply for you, with your rate filled in, every time a brand says yes."
    )
