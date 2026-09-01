"""
Media proxy for social CDN thumbnails (Instagram / imginn / TikTok).

Browsers block direct <img> loads from these hosts via Cross-Origin-Resource-Policy.
Fetching server-side and re-serving avoids broken thumbnails in the AI pitch modal.
"""

from __future__ import annotations

import os
import re
import hashlib
from io import BytesIO
from typing import Iterable, List, Optional, Tuple
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

_PLACEHOLDER_SVG = (
    b'<svg xmlns="http://www.w3.org/2000/svg" width="240" height="240" viewBox="0 0 240 240">'
    b'<rect fill="#F3F4F6" width="240" height="240"/>'
    b'<circle cx="120" cy="120" r="28" fill="#E5E7EB"/>'
    b'<polygon points="112,106 140,120 112,134" fill="#9CA3AF"/>'
    b'</svg>'
)


def _cdn_headers(host: str) -> dict:
    host = (host or "").lower()
    referer = "https://www.instagram.com/"
    origin = "https://www.instagram.com"
    if "imginn.com" in host:
        referer = "https://imginn.com/"
        origin = "https://imginn.com"
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
        origin = "https://www.tiktok.com"
    return {
        "User-Agent": _UA,
        "Accept": "image/avif,image/webp,image/apng,image/*,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "Referer": referer,
        "Origin": origin,
        "Sec-Fetch-Dest": "image",
        "Sec-Fetch-Mode": "no-cors",
        "Sec-Fetch-Site": "cross-site",
    }


def _fetch_cdn_bytes(url: str, timeout: int = 10) -> Optional[Tuple[bytes, str]]:
    """Download a social CDN image. Signed TikTok/IG URLs only work while fresh."""
    parsed = urlparse(url)
    headers = _cdn_headers(parsed.netloc)
    resp = requests.get(url, headers=headers, timeout=timeout)
    if resp.status_code == 200 and resp.content:
        content_type = resp.headers.get("Content-Type") or "image/jpeg"
        return resp.content, content_type
    print(f"[media-cdn] upstream {resp.status_code} for {url[:120]}")
    return None


def _already_hosted(url: str) -> bool:
    raw = (url or "").lower()
    return "supabase" in raw or "/storage/v1/object/public/" in raw


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


def rehost_social_image(image_url: str, dest_prefix: str = "avatars") -> Optional[str]:
    """
    Download a social CDN image (with Instagram/TikTok Referer) and upload to Supabase.
    Profile <img> tags cannot hotlink scontent URLs (CORP + signed query expiry).
    """
    if not image_url or not str(image_url).startswith("http"):
        return None
    raw = image_url.strip().rstrip("\\")
    if _already_hosted(raw):
        return raw
    if not is_social_cdn_url(raw):
        return None

    supabase_url = (os.getenv("SUPABASE_URL") or "").rstrip("/")
    supabase_key = os.getenv("SUPABASE_KEY") or ""
    bucket = os.getenv("SUPABASE_BUCKET", "creators")
    if not supabase_url or not supabase_key:
        return None

    try:
        fetched = _fetch_cdn_bytes(raw, timeout=10)
        if not fetched:
            return None
        content, content_type = fetched
        if "png" in content_type:
            ext = "png"
        elif "webp" in content_type:
            ext = "webp"
        else:
            ext = "jpg"
            content_type = "image/jpeg"

        prefix = (dest_prefix or "avatars").strip("/")
        stem = hashlib.sha1(urlparse(raw).path.encode("utf-8")).hexdigest()[:16]
        filename = f"{prefix}/{stem}.{ext}"
        upload = requests.post(
            f"{supabase_url}/storage/v1/object/{bucket}/{filename}",
            headers={
                "Authorization": f"Bearer {supabase_key}",
                "Content-Type": content_type,
                "x-upsert": "true",
            },
            data=content,
            timeout=20,
        )
        if upload.status_code not in (200, 201):
            print(f"[avatar-rehost] upload {upload.status_code}: {upload.text[:160]}")
            return None
        return f"{supabase_url}/storage/v1/object/public/{bucket}/{filename}"
    except Exception as e:
        print(f"[avatar-rehost] failed: {e}")
        return None


def persist_social_thumbnails(urls: Optional[Iterable[str]], dest_prefix: str = "thumbs") -> List[str]:
    """Copy social CDN thumbs to Supabase so signed TikTok/IG URLs can expire."""
    out: List[str] = []
    for url in urls or []:
        raw = str(url or "").strip()
        if not raw:
            continue
        if _already_hosted(raw) or not is_social_cdn_url(raw):
            out.append(raw)
            continue
        out.append(rehost_social_image(raw, dest_prefix=dest_prefix) or raw)
    return out


def persist_profile_media(profile: Optional[dict]) -> Optional[dict]:
    """Rehost scrape thumbnails in-place. Safe to call on every scrape."""
    if not profile or not isinstance(profile, dict):
        return profile
    handle = str(profile.get("handle") or "unknown").lstrip("@")[:40] or "unknown"
    prefix = f"thumbs/{handle}"
    mapping = {}

    def persist_one(url):
        raw = str(url or "").strip()
        if not raw:
            return raw
        if raw in mapping:
            return mapping[raw]
        hosted = persist_social_thumbnails([raw], dest_prefix=prefix)
        mapping[raw] = hosted[0] if hosted else raw
        return mapping[raw]

    thumbs = profile.get("recent_post_thumbnails")
    if isinstance(thumbs, list) and thumbs:
        profile["recent_post_thumbnails"] = [persist_one(u) for u in thumbs if u]

    for post in profile.get("recent_posts") or []:
        if not isinstance(post, dict):
            continue
        thumb = post.get("thumbnail_url") or post.get("displayUrl")
        if thumb:
            post["thumbnail_url"] = persist_one(thumb)
    return profile


def persist_social_avatar(image_url: str, dest_prefix: str = "avatars") -> str:
    """Durable avatar URL: rehost to storage, else media-proxy, else original."""
    if not image_url or not isinstance(image_url, str):
        return ""
    raw = image_url.strip().rstrip("\\")
    if not raw:
        return ""
    hosted = rehost_social_image(raw, dest_prefix=dest_prefix)
    if hosted:
        return hosted
    if is_social_cdn_url(raw):
        return to_proxied_media_url(raw)
    return raw


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
        fetched = _fetch_cdn_bytes(url, timeout=12)
        if not fetched:
            # Expired/signed CDN URLs cannot be recovered here — show a tile, not a broken <img>
            img_io = BytesIO(_PLACEHOLDER_SVG)
            img_io.seek(0)
            response = send_file(img_io, mimetype="image/svg+xml", max_age=120)
            response.headers["Cross-Origin-Resource-Policy"] = "cross-origin"
            response.headers["Cache-Control"] = "public, max-age=120"
            return response

        content, content_type = fetched
        if not content_type.startswith("image/") and "octet-stream" not in content_type:
            if re.search(r"\.(png)(?:\?|$)", url, re.I):
                content_type = "image/png"
            elif re.search(r"\.(webp)(?:\?|$)", url, re.I):
                content_type = "image/webp"
            else:
                content_type = "image/jpeg"

        if len(content) > 5 * 1024 * 1024:
            abort(413, description="Media too large")

        img_io = BytesIO(content)
        img_io.seek(0)
        response = send_file(img_io, mimetype=content_type, max_age=86400)
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
