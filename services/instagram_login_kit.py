"""Official Instagram Login (Meta) for signed-in creators.

Same job as TikTok Login Kit: profile + public posts from OAuth, not HTML scrape.
Requires a Professional Instagram account (Creator or Business).
"""

from __future__ import annotations

import os
import re
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

import requests

GRAPH_VERSION = "v23.0"
TOKEN_URL = "https://api.instagram.com/oauth/access_token"
LONG_LIVED_URL = "https://graph.instagram.com/access_token"
REFRESH_URL = "https://graph.instagram.com/refresh_access_token"
ME_URLS = (
    f"https://graph.instagram.com/{GRAPH_VERSION}/me",
    "https://graph.instagram.com/me",
)
MEDIA_URLS = (
    f"https://graph.instagram.com/{GRAPH_VERSION}/me/media",
    "https://graph.instagram.com/me/media",
)
ME_FIELDS_BASE = "user_id,username,name,account_type,profile_picture_url,media_count,biography"
ME_FIELDS_INSIGHTS = "followers_count,follows_count"

MEDIA_FIELDS = (
    "id,caption,media_type,media_url,permalink,thumbnail_url,"
    "timestamp,like_count,comments_count"
)


class InstagramLoginKitError(ValueError):
    """Instagram Login request failed or returned no usable profile."""


def _log(msg: str) -> None:
    try:
        print(str(msg).encode("ascii", "replace").decode("ascii"))
    except Exception:
        pass


def load_instagram_credentials() -> Tuple[str, str]:
    file_vals = {}
    try:
        from dotenv import dotenv_values
        env_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".env")
        file_vals = dotenv_values(env_path) or {}
    except Exception:
        file_vals = {}
    app_id = (file_vals.get("INSTAGRAM_APP_ID") or os.getenv("INSTAGRAM_APP_ID") or "").strip()
    secret = (file_vals.get("INSTAGRAM_APP_SECRET") or os.getenv("INSTAGRAM_APP_SECRET") or "").strip()
    return app_id, secret


def _graph_error(payload: dict) -> Optional[str]:
    err = payload.get("error")
    if isinstance(err, dict):
        return str(err.get("message") or err.get("type") or "graph_error")
    if payload.get("error_type") or payload.get("error_message"):
        return str(payload.get("error_message") or payload.get("error_type"))
    return None


def is_unsupported_graph_method_error(message: str) -> bool:
    low = (message or "").lower()
    return "method type" in low or "unsupported get request" in low


def is_professional_account_error(message: str) -> bool:
    """True only for Instagram account-type failures — not Graph method errors."""
    low = (message or "").lower()
    if is_unsupported_graph_method_error(low):
        return False
    return any(
        needle in low
        for needle in (
            "professional account",
            "business account",
            "creator account",
            "not a professional",
            "switch to professional",
        )
    )


def _unwrap_token_payload(payload: Dict[str, Any]) -> Dict[str, Any]:
    if payload.get("access_token"):
        return payload
    data = payload.get("data")
    if isinstance(data, list) and data and isinstance(data[0], dict) and data[0].get("access_token"):
        return data[0]
    if isinstance(data, dict) and data.get("access_token"):
        return data
    return payload


def _graph_get(url: str, access_token: str, extra_params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Query-param token only — Bearer + query together makes Instagram reject GET."""
    params = {"access_token": access_token}
    if extra_params:
        params.update(extra_params)
    resp = requests.get(url, params=params, timeout=15)
    return resp.json() if resp.content else {}


def exchange_code_for_token(code: str, redirect_uri: str) -> Dict[str, Any]:
    app_id, secret = load_instagram_credentials()
    if not app_id or not secret:
        raise InstagramLoginKitError("INSTAGRAM_APP_ID / INSTAGRAM_APP_SECRET missing")
    clean_code = (code or "").strip().rstrip("#_")
    resp = requests.post(
        TOKEN_URL,
        data={
            "client_id": app_id,
            "client_secret": secret,
            "grant_type": "authorization_code",
            "redirect_uri": redirect_uri,
            "code": clean_code,
        },
        timeout=15,
    )
    raw = resp.json() if resp.content else {}
    payload = _unwrap_token_payload(raw if isinstance(raw, dict) else {})
    err = _graph_error(payload) or _graph_error(raw if isinstance(raw, dict) else {})
    token = str(payload.get("access_token") or "").strip()
    user_id = payload.get("user_id") or payload.get("user") or ""
    _log(
        f"[ig-login] token http={resp.status_code} keys={sorted((raw or {}).keys()) if isinstance(raw, dict) else []} "
        f"prefix={token[:4] or 'none'} user_id={user_id or 'none'}"
    )
    if err:
        raise InstagramLoginKitError(err)
    if not token:
        raise InstagramLoginKitError("token exchange returned no access_token")
    payload["access_token"] = token
    if user_id:
        payload["user_id"] = str(user_id)
    return payload


def exchange_long_lived_token(short_token: str) -> Dict[str, Any]:
    """60-day token. Falls back to the short-lived token if exchange fails."""
    _app_id, secret = load_instagram_credentials()
    body = {
        "grant_type": "ig_exchange_token",
        "client_secret": secret,
        "access_token": short_token,
    }
    last_err = None
    for method in ("get", "post"):
        try:
            req = requests.get if method == "get" else requests.post
            kwargs = {"timeout": 15}
            if method == "get":
                kwargs["params"] = body
            else:
                kwargs["data"] = body
            resp = req(LONG_LIVED_URL, **kwargs)
            payload = _unwrap_token_payload(resp.json() if resp.content else {})
        except Exception as exc:
            last_err = str(exc)
            _log(f"[ig-login] long-lived {method} failed: {exc}")
            continue
        err = _graph_error(payload)
        if err or not payload.get("access_token"):
            last_err = err or "no token"
            _log(f"[ig-login] long-lived {method} skipped: {last_err}")
            continue
        return payload
    _log(f"[ig-login] long-lived exchange using short-lived token: {last_err}")
    return {"access_token": short_token, "expires_in": 3600}


def refresh_long_lived_token(access_token: str) -> Dict[str, Any]:
    last_err = None
    for method in ("get", "post"):
        try:
            req = requests.get if method == "get" else requests.post
            kwargs = {"timeout": 15}
            body = {
                "grant_type": "ig_refresh_token",
                "access_token": access_token,
            }
            if method == "get":
                kwargs["params"] = body
            else:
                kwargs["data"] = body
            resp = req(REFRESH_URL, **kwargs)
            payload = _unwrap_token_payload(resp.json() if resp.content else {})
        except Exception as exc:
            last_err = str(exc)
            continue
        err = _graph_error(payload)
        if err:
            last_err = err
            continue
        if payload.get("access_token"):
            return payload
        last_err = "no token"
    raise InstagramLoginKitError(last_err or "refresh returned no access_token")


def _profile_urls(user_id: str = "") -> List[str]:
    uid = str(user_id or "").strip()
    urls: List[str] = []
    if uid:
        urls.append(f"https://graph.instagram.com/{GRAPH_VERSION}/{uid}")
        urls.append(f"https://graph.instagram.com/{uid}")
    urls.extend(ME_URLS)
    return urls


def _media_urls(user_id: str = "") -> List[str]:
    uid = str(user_id or "").strip()
    urls: List[str] = []
    if uid:
        urls.append(f"https://graph.instagram.com/{GRAPH_VERSION}/{uid}/media")
        urls.append(f"https://graph.instagram.com/{uid}/media")
    urls.extend(MEDIA_URLS)
    return urls


def fetch_user_info(access_token: str, user_id: str = "") -> Dict[str, Any]:
    last_err = None
    field_sets = (
        f"{ME_FIELDS_BASE},{ME_FIELDS_INSIGHTS}",
        ME_FIELDS_BASE,
        "user_id,username",
    )
    for url in _profile_urls(user_id):
        for fields in field_sets:
            try:
                payload = _graph_get(url, access_token, {"fields": fields})
            except Exception as exc:
                last_err = str(exc)
                continue
            err = _graph_error(payload)
            if err:
                last_err = err
                _log(f"[ig-login] me {url} fields={fields}: {err}")
                continue
            if payload.get("username"):
                if user_id and not payload.get("user_id"):
                    payload["user_id"] = str(user_id)
                return payload
            last_err = "me returned no username"
    raise InstagramLoginKitError(last_err or "me returned no username")


def fetch_media(access_token: str, max_items: int = 40, user_id: str = "") -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    extra = {
        "fields": MEDIA_FIELDS,
        "limit": min(25, max(1, int(max_items or 40))),
    }
    target = max(1, min(int(max_items or 40), 60))
    for start_url in _media_urls(user_id):
        url = start_url
        page_params: Optional[Dict[str, Any]] = extra
        pages = 0
        while url and len(out) < target and pages < 4:
            pages += 1
            try:
                if page_params is None:
                    payload = requests.get(url, timeout=15).json()
                else:
                    payload = _graph_get(url, access_token, page_params)
            except Exception as exc:
                _log(f"[ig-login] media exception: {exc}")
                break
            err = _graph_error(payload)
            if err:
                _log(f"[ig-login] media error: {err}")
                break
            for item in payload.get("data") or []:
                mapped = _media_to_scrape_item(item)
                if mapped:
                    out.append(mapped)
                if len(out) >= target:
                    break
            url = ((payload.get("paging") or {}).get("next") or "").strip()
            page_params = None
        if out:
            break
    _log(f"[ig-login] media returned {len(out)} posts")
    return out[:target]


def _iso_to_unix(value: Any) -> Optional[int]:
    if value in (None, ""):
        return None
    try:
        if isinstance(value, (int, float)) or str(value).isdigit():
            return int(value)
        text = str(value).strip().replace("Z", "+00:00")
        if len(text) >= 5 and text[-5] in "+-" and text[-3] != ":":
            text = text[:-2] + ":" + text[-2:]
        dt = datetime.fromisoformat(text)
        return int(dt.timestamp())
    except Exception:
        return None


def _media_to_scrape_item(item: Any) -> Optional[Dict[str, Any]]:
    if not isinstance(item, dict) or not item.get("id"):
        return None
    permalink = str(item.get("permalink") or item.get("url") or item.get("share_url") or "").strip()
    cover = str(item.get("thumbnail_url") or item.get("media_url") or "").strip()
    caption = str(item.get("caption") or "")
    create_time = _iso_to_unix(item.get("timestamp") if item.get("timestamp") not in (None, "") else item.get("create_time"))
    shortcode = ""
    if permalink:
        match = re.search(r"instagram\.com/(?:p|reel|tv)/([^/?#]+)", permalink, re.I)
        if match:
            shortcode = match.group(1)
    return {
        "id": str(item.get("id")),
        "shortCode": shortcode or str(item.get("id")),
        "caption": caption,
        "url": permalink,
        "displayUrl": cover,
        "likesCount": int(item.get("like_count") or 0),
        "commentsCount": int(item.get("comments_count") or 0),
        "timestamp": item.get("timestamp") or "",
        "createTime": create_time,
        "type": item.get("media_type") or "",
        "share_url": permalink,
        "_source": "instagram_login",
    }


def media_to_oauth_snapshot(media: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    snapshot = []
    for item in media or []:
        if not isinstance(item, dict) or not item.get("id"):
            continue
        permalink = item.get("url") or item.get("share_url") or item.get("permalink") or ""
        snapshot.append({
            "id": str(item.get("id")),
            "title": (item.get("caption") or item.get("title") or "")[:80],
            "cover_image_url": item.get("displayUrl") or item.get("cover_image_url") or "",
            "create_time": item.get("createTime") or item.get("create_time"),
            "share_url": permalink,
            "url": permalink,
            "likes": int(item.get("likesCount") or item.get("likes") or 0),
            "comments": int(item.get("commentsCount") or item.get("comments") or 0),
            "views": 0,
        })
    return snapshot


def user_and_media_to_raw_scrape(
    user_info: Dict[str, Any],
    media: List[Dict[str, Any]],
    handle_hint: str = "",
) -> Dict[str, Any]:
    handle = (user_info.get("username") or handle_hint or "").strip().lstrip("@")
    return {
        "username": handle,
        "fullName": user_info.get("name") or user_info.get("display_name") or "",
        "biography": user_info.get("biography") or user_info.get("bio_description") or "",
        "followersCount": int(user_info.get("followers_count") or user_info.get("follower_count") or 0),
        "followsCount": int(user_info.get("follows_count") or user_info.get("following_count") or 0),
        "postsCount": int(user_info.get("media_count") or 0) or len(media),
        "isPrivate": False,
        "profilePicUrl": user_info.get("profile_picture_url") or user_info.get("avatar_url") or "",
        "latestPosts": media,
        "open_id": str(user_info.get("user_id") or user_info.get("id") or ""),
        "account_type": str(user_info.get("account_type") or "BUSINESS").upper(),
        "isBusinessAccount": True,
        "_source": "instagram_login",
    }


def oauth_profile_to_raw_scrape(profile_data: Dict[str, Any]) -> Dict[str, Any]:
    profile_data = profile_data or {}
    media = profile_data.get("latestPosts")
    if not media:
        media = []
        for item in profile_data.get("oauth_videos") or []:
            mapped = _media_to_scrape_item({
                "id": item.get("id"),
                "caption": item.get("title") or item.get("caption"),
                "permalink": item.get("url") or item.get("share_url"),
                "thumbnail_url": item.get("cover_image_url"),
                "like_count": item.get("likes"),
                "comments_count": item.get("comments"),
                "timestamp": item.get("timestamp") or item.get("create_time"),
                "create_time": item.get("create_time"),
            })
            if mapped:
                if item.get("create_time") and not mapped.get("createTime"):
                    mapped["createTime"] = item.get("create_time")
                media.append(mapped)
    handle = (profile_data.get("username") or "").strip().lstrip("@")
    return user_and_media_to_raw_scrape(
        {
            "username": handle,
            "name": profile_data.get("display_name") or profile_data.get("fullName") or "",
            "biography": profile_data.get("bio_description") or profile_data.get("biography") or "",
            "followers_count": profile_data.get("follower_count") or profile_data.get("followersCount") or 0,
            "follows_count": profile_data.get("following_count") or profile_data.get("followsCount") or 0,
            "media_count": profile_data.get("media_count") or profile_data.get("postsCount") or len(media),
            "profile_picture_url": profile_data.get("avatar_url") or profile_data.get("profilePicUrl") or "",
            "user_id": profile_data.get("open_id") or "",
            "account_type": profile_data.get("account_type") or "BUSINESS",
        },
        media,
        handle_hint=handle,
    )


def fetch_raw_scrape(access_token: str, handle_hint: str = "") -> Dict[str, Any]:
    user = fetch_user_info(access_token)
    media = fetch_media(access_token)
    raw = user_and_media_to_raw_scrape(user, media, handle_hint=handle_hint)
    if not raw.get("username"):
        raise InstagramLoginKitError("Instagram Login profile is missing a username")
    return raw


def expires_at_from_payload(payload: Optional[Dict[str, Any]] = None) -> datetime:
    seconds = int((payload or {}).get("expires_in") or 3600)
    return datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(seconds=max(60, seconds))
