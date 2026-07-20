"""
Media proxy for social CDN thumbnails (Instagram / imginn / TikTok).

Browsers block direct <img> loads from these hosts via Cross-Origin-Resource-Policy.
Fetching server-side and re-serving avoids broken thumbnails in the AI pitch modal.
"""

from __future__ import annotations

import os
import re
from io import BytesIO
from typing import Iterable, List, Optional
from urllib.parse import quote, unquote, urlparse

import requests
from flask import Blueprint, abort, has_request_context, request, send_file
from werkzeug.exceptions import HTTPException

media_proxy = Blueprint("media_proxy", __name__)

# Host suffix allowlist (host == suffix or host.endswith("." + suffix))
_ALLOWED_HOST_SUFFIXES = (
    "cdninstagram.com",
    "fbcdn.net",
    "fbsbx.com",
    "imginn.com",
    "tiktokcdn.com",
    "tiktokcdn-us.com",
    "tiktokcdn-eu.com",
    "tiktokcdn-i18n.com",
    "ttlivecdn.com",
    "ibyteimg.com",
    "muscdn.com",
    "byteoversea.com",
    "ibytedtos.com",
)

_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)


def _host_allowed(host: str) -> bool:
    host = (host or "").lower().split(":")[0]
    if not host:
        return False
    return any(host == suffix or host.endswith("." + suffix) for suffix in _ALLOWED_HOST_SUFFIXES)


def is_social_cdn_url(url: str) -> bool:
    try:
        parsed = urlparse(url)
    except Exception:
        return False
    if parsed.scheme not in ("http", "https"):
        return False
    return _host_allowed(parsed.netloc)


def get_public_api_base() -> str:
    """Absolute API origin for proxied <img src> URLs (prod cross-subdomain safe)."""
    env = (os.getenv("PUBLIC_API_URL") or os.getenv("REACT_APP_API_URL") or "").strip().rstrip("/")
    if env and "localhost" not in env:
        return env
    if has_request_context():
        root = (request.url_root or "").rstrip("/")
        # Prefer api.* host when request somehow comes via app frontend proxy
        if root and "app.newcollab.co" in root:
            return "https://api.newcollab.co"
        if root:
            return root
    # Production default — never emit relative /api/media-proxy for cross-origin <img>
    if (os.getenv("FLASK_ENV") or "").lower() == "production" or os.getenv("RENDER") or os.getenv("RAILWAY_ENVIRONMENT"):
        return "https://api.newcollab.co"
    return env or "https://api.newcollab.co"


def to_proxied_media_url(url: Optional[str], api_base: Optional[str] = None) -> str:
    """Rewrite a social CDN URL to absolute /api/media-proxy?url=..."""
    if not url or not isinstance(url, str):
        return url or ""
    raw = url.strip()
    if not raw:
        return raw
    base = (api_base if api_base is not None else get_public_api_base()).rstrip("/")
    if "/api/media-proxy" in raw:
        # Re-pin relative or wrong-host proxy URLs onto the public API origin
        try:
            if raw.startswith("/"):
                return f"{base}{raw}"
            parsed = urlparse(raw)
            if "media-proxy" in (parsed.path or ""):
                return f"{base}{parsed.path}?{parsed.query}" if parsed.query else f"{base}{parsed.path}"
        except Exception:
            pass
        return raw
    if not is_social_cdn_url(raw):
        return raw
    path = f"/api/media-proxy?url={quote(raw, safe='')}"
    return f"{base}{path}" if base else path


def proxy_media_urls(urls: Optional[Iterable[str]], api_base: Optional[str] = None) -> List[str]:
    out: List[str] = []
    for url in urls or []:
        if not url:
            continue
        out.append(to_proxied_media_url(str(url), api_base=api_base))
    return out


def proxy_profile_snapshot_thumbnails(snapshot: Optional[dict]) -> Optional[dict]:
    """In-place rewrite of profile_snapshot.recent_thumbnails for API responses."""
    if not snapshot or not isinstance(snapshot, dict):
        return snapshot
    thumbs = snapshot.get("recent_thumbnails")
    if isinstance(thumbs, list) and thumbs:
        snapshot["recent_thumbnails"] = proxy_media_urls(thumbs)
    return snapshot


@media_proxy.route("/api/media-proxy", methods=["GET"])
def proxy_media():
    """
    GET /api/media-proxy?url=<encoded https URL>
    """
    url = (request.args.get("url") or "").strip()
    if not url:
        abort(400, description="url is required")
    # Support accidental double-encoding
    if "%" in url and "://" not in url:
        url = unquote(url)

    if not is_social_cdn_url(url):
        abort(403, description="Domain not allowed")

    try:
        parsed = urlparse(url)
        host = (parsed.netloc or "").lower()
        referer = "https://www.instagram.com/"
        if "imginn.com" in host:
            referer = "https://imginn.com/"
        elif any(
            s in host
            for s in (
                "tiktok",
                "byteoversea",
                "muscdn",
                "ibyteimg",
                "ttlivecdn",
                "ibytedtos",
            )
        ):
            referer = "https://www.tiktok.com/"

        resp = requests.get(
            url,
            headers={
                "User-Agent": _UA,
                "Accept": "image/avif,image/webp,image/apng,image/*,*/*;q=0.8",
                "Referer": referer,
            },
            timeout=12,
            stream=True,
        )
        if resp.status_code != 200:
            # Expired/signed CDN URLs are common — quiet 404, not a server fault
            print(f"[media-proxy] upstream {resp.status_code} for {url[:120]}")
            abort(404, description="Media not found")

        content_type = resp.headers.get("Content-Type") or "image/jpeg"
        if not content_type.startswith("image/") and "octet-stream" not in content_type:
            # Some CDNs omit type; sniff from URL
            if re.search(r"\.(png)(?:\?|$)", url, re.I):
                content_type = "image/png"
            elif re.search(r"\.(webp)(?:\?|$)", url, re.I):
                content_type = "image/webp"
            else:
                content_type = "image/jpeg"

        # Cap size (~5MB) to avoid abuse
        content = resp.content
        if len(content) > 5 * 1024 * 1024:
            abort(413, description="Media too large")

        img_io = BytesIO(content)
        img_io.seek(0)
        response = send_file(img_io, mimetype=content_type, max_age=86400)
        # Allow embedding from app.newcollab.co / localhost
        response.headers["Cross-Origin-Resource-Policy"] = "cross-origin"
        response.headers["Cache-Control"] = "public, max-age=86400"
        return response
    except HTTPException:
        raise
    except requests.RequestException as e:
        print(f"[media-proxy] fetch failed: {e}")
        abort(404, description="Failed to fetch media")
    except Exception as e:
        print(f"[media-proxy] error: {e}")
        abort(502, description="Failed to proxy media")
