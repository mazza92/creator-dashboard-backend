"""Public social identity for pitch sign-off.

Legal first names (e.g. Matio) are not what brands search. Prefer the
handle / username the creator publishes on Instagram or TikTok.
"""

from __future__ import annotations

import json
import re
from typing import Any, Dict, Optional


_GENERIC_NAMES = {"creator", "user", "admin", "test", "hey", ""}


def _clean_handle(value: Any) -> str:
    if not value:
        return ""
    text = str(value).strip().lstrip("@")
    text = re.sub(r"^https?://[^/]+/", "", text)
    text = text.split("/")[0].strip()
    return text.strip("@ \t")


def _handle_from_social_links(creator: Dict) -> str:
    links = creator.get("social_links") or []
    if isinstance(links, str):
        try:
            links = json.loads(links)
        except Exception:
            links = []
    if not isinstance(links, list):
        return ""
    for pref in ("instagram", "tiktok", "youtube"):
        for link in links:
            if not isinstance(link, dict):
                continue
            if (link.get("platform") or "").lower() != pref:
                continue
            handle = _clean_handle(link.get("handle") or link.get("username"))
            if not handle and link.get("url"):
                url = str(link.get("url"))
                match = re.search(r"/@([A-Za-z0-9._]+)", url)
                if not match:
                    match = re.search(r"/([A-Za-z0-9._]+)/?$", url)
                if match:
                    handle = match.group(1)
            if handle:
                return handle
    for link in links:
        if not isinstance(link, dict):
            continue
        handle = _clean_handle(link.get("handle") or link.get("username"))
        if handle:
            return handle
    return ""


def resolve_pitch_identity(creator: Optional[Dict] = None) -> Dict[str, str]:
    """Return sign-off name + handle for a pitch.

    Priority: social_handle, then username, then first_name.
    Never invent a first name from a concatenated handle.
    """
    creator = creator or {}
    handle = _clean_handle(creator.get("social_handle")) or _handle_from_social_links(creator)
    username = _clean_handle(creator.get("username"))
    first_name = str(creator.get("first_name") or "").strip()
    if first_name.lower() in _GENERIC_NAMES:
        first_name = ""

    public = handle or username
    signoff = public or first_name or "there"

    return {
        "signoff_name": signoff,
        "handle": handle or username,
        "username": username,
    }
