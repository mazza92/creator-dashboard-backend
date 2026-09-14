"""Official TikTok Login Kit (Display API) for signed-in creators.

Replaces the in-house HTML scraper for users who connected TikTok.
Lead-gen crawls of *other* public profiles still use the in-house scraper.
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

import requests

TIKTOK_USER_INFO_URL = "https://open.tiktokapis.com/v2/user/info/"
TIKTOK_VIDEO_LIST_URL = "https://open.tiktokapis.com/v2/video/list/"
TIKTOK_TOKEN_URL = "https://open.tiktokapis.com/v2/oauth/token/"


def load_tiktok_credentials() -> Tuple[str, str]:
    """Client key/secret from this repo's .env first, then process env."""
    file_vals = {}
    try:
        from dotenv import dotenv_values
        env_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".env")
        file_vals = dotenv_values(env_path) or {}
    except Exception:
        file_vals = {}
    key = (file_vals.get("TIKTOK_CLIENT_KEY") or os.getenv("TIKTOK_CLIENT_KEY") or "").strip()
    secret = (file_vals.get("TIKTOK_CLIENT_SECRET") or os.getenv("TIKTOK_CLIENT_SECRET") or "").strip()
    return key, secret

USER_FIELDS = (
    "open_id,union_id,avatar_url,display_name,"
    "username,bio_description,profile_deep_link,is_verified,"
    "follower_count,following_count,likes_count,video_count"
)
VIDEO_FIELDS = (
    "id,title,cover_image_url,create_time,share_url,"
    "like_count,comment_count,share_count,view_count"
)

_fernet = None
_fernet_loaded = False


def _get_fernet():
    """Same key as social_verification_routes — load from env or repo .env."""
    global _fernet, _fernet_loaded
    if _fernet_loaded:
        return _fernet
    _fernet_loaded = True
    key = (os.getenv("SOCIAL_TOKEN_ENCRYPTION_KEY") or "").strip()
    if not key:
        try:
            from dotenv import dotenv_values
            env_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".env")
            key = ((dotenv_values(env_path) or {}).get("SOCIAL_TOKEN_ENCRYPTION_KEY") or "").strip()
        except Exception:
            key = ""
    if key:
        try:
            from cryptography.fernet import Fernet
            _fernet = Fernet(key.encode())
        except Exception:
            _fernet = None
    return _fernet


class TikTokLoginKitError(ValueError):
    """Login Kit request failed or returned no usable profile."""


def _log(msg: str) -> None:
    try:
        print(str(msg).encode("ascii", "replace").decode("ascii"))
    except Exception:
        pass


def decrypt_token(encrypted_token: Optional[str]) -> Optional[str]:
    if not encrypted_token:
        return None
    fernet = _get_fernet()
    if fernet:
        try:
            return fernet.decrypt(encrypted_token.encode()).decode()
        except Exception:
            return encrypted_token
    return encrypted_token


def encrypt_token(token: Optional[str]) -> Optional[str]:
    if not token:
        return None
    fernet = _get_fernet()
    if fernet:
        return fernet.encrypt(token.encode()).decode()
    return token


def _api_error(payload: dict) -> Optional[str]:
    err = payload.get("error")
    if isinstance(err, dict):
        code = err.get("code")
        if code and code != "ok":
            return str(err.get("message") or code)
        return None
    if err in (None, "", "ok"):
        return None
    return str(err)


def fetch_user_info(access_token: str) -> Dict[str, Any]:
    resp = requests.get(
        TIKTOK_USER_INFO_URL,
        headers={"Authorization": f"Bearer {access_token}"},
        params={"fields": USER_FIELDS},
        timeout=15,
    )
    payload = resp.json() if resp.content else {}
    err = _api_error(payload)
    if err:
        raise TikTokLoginKitError(f"user.info failed: {err}")
    user = (payload.get("data") or {}).get("user") or {}
    if not isinstance(user, dict) or not user:
        raise TikTokLoginKitError("user.info returned no user")
    return user


def fetch_videos(access_token: str, max_videos: int = 40) -> List[Dict[str, Any]]:
    """Paginate video.list into scrape-shaped latestVideos rows."""
    out: List[Dict[str, Any]] = []
    cursor = 0
    pages = 0
    target = max(1, min(int(max_videos or 40), 60))
    while len(out) < target and pages < 4:
        pages += 1
        batch = min(20, target - len(out))
        try:
            resp = requests.post(
                TIKTOK_VIDEO_LIST_URL,
                headers={
                    "Authorization": f"Bearer {access_token}",
                    "Content-Type": "application/json",
                },
                params={"fields": VIDEO_FIELDS},
                json={"max_count": batch, "cursor": cursor},
                timeout=15,
            )
            payload = resp.json() if resp.content else {}
        except Exception as exc:
            _log(f"[login-kit] video.list exception: {exc}")
            break
        err = _api_error(payload)
        if err:
            _log(f"[login-kit] video.list error: {err}")
            break
        data = payload.get("data") or {}
        videos = data.get("videos") or []
        for item in videos:
            mapped = _video_to_scrape_item(item)
            if mapped:
                out.append(mapped)
        if not data.get("has_more"):
            break
        next_cursor = data.get("cursor")
        if next_cursor in (None, "", cursor):
            break
        cursor = next_cursor
    _log(f"[login-kit] video.list returned {len(out)} videos")
    return out[:target]


def _video_to_scrape_item(item: Any) -> Optional[Dict[str, Any]]:
    if not isinstance(item, dict) or not item.get("id"):
        return None
    cover = item.get("cover_image_url") or ""
    video_id = str(item.get("id"))
    create_time = item.get("create_time")
    try:
        create_time = int(create_time) if create_time not in (None, "") else None
    except (TypeError, ValueError):
        create_time = None
    return {
        "id": video_id,
        "text": item.get("title") or "",
        "diggCount": int(item.get("like_count") or 0),
        "commentCount": int(item.get("comment_count") or 0),
        "shareCount": int(item.get("share_count") or 0),
        "playCount": int(item.get("view_count") or 0),
        "createTime": create_time,
        "isPinnedItem": False,
        "videoMeta": {
            "coverUrl": cover,
            "originalCoverUrl": cover,
        },
        "covers": [cover] if cover else [],
        "share_url": item.get("share_url") or "",
        "_source": "login_kit",
    }


def videos_to_oauth_snapshot(videos: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    snapshot = []
    for v in videos or []:
        if not isinstance(v, dict) or not v.get("id"):
            continue
        cover = (v.get("videoMeta") or {}).get("coverUrl") or ""
        snapshot.append({
            "id": str(v.get("id")),
            "title": v.get("text") or "",
            "cover_image_url": cover,
            "create_time": v.get("createTime"),
            "share_url": v.get("share_url") or "",
            "url": v.get("share_url") or f"https://www.tiktok.com/@/video/{v.get('id')}",
            "likes": int(v.get("diggCount") or 0),
            "comments": int(v.get("commentCount") or 0),
            "shares": int(v.get("shareCount") or 0),
            "views": int(v.get("playCount") or 0),
        })
    return snapshot


def oauth_snapshot_to_videos(snapshot: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    videos = []
    for item in snapshot or []:
        if not isinstance(item, dict):
            continue
        mapped = _video_to_scrape_item({
            "id": item.get("id"),
            "title": item.get("title"),
            "cover_image_url": item.get("cover_image_url"),
            "create_time": item.get("create_time"),
            "share_url": item.get("share_url") or item.get("url"),
            "like_count": item.get("likes") or item.get("like_count"),
            "comment_count": item.get("comments") or item.get("comment_count"),
            "share_count": item.get("shares") or item.get("share_count"),
            "view_count": item.get("views") or item.get("view_count"),
        })
        if mapped:
            videos.append(mapped)
    return videos


def user_and_videos_to_raw_scrape(
    user_info: Dict[str, Any],
    videos: List[Dict[str, Any]],
    handle_hint: str = "",
) -> Dict[str, Any]:
    handle = (
        (user_info.get("username") or handle_hint or "")
        .strip()
        .lstrip("@")
    )
    video_count = int(user_info.get("video_count") or 0) or len(videos)
    return {
        "uniqueId": handle,
        "nickname": user_info.get("display_name") or "",
        "signature": user_info.get("bio_description") or "",
        "followerCount": int(user_info.get("follower_count") or 0),
        "followingCount": int(user_info.get("following_count") or 0),
        "videoCount": video_count,
        "heartCount": int(user_info.get("likes_count") or 0),
        "verified": bool(user_info.get("is_verified")),
        "privateAccount": False,
        "avatarUrl": user_info.get("avatar_url") or "",
        "bioLink": user_info.get("profile_deep_link") or "",
        "open_id": user_info.get("open_id") or "",
        "latestVideos": videos,
        "_source": "login_kit",
    }


def oauth_profile_to_raw_scrape(profile_data: Dict[str, Any]) -> Dict[str, Any]:
    """Map the OAuth callback snapshot (session/DB) into process_scrape shape."""
    profile_data = profile_data or {}
    videos = profile_data.get("latestVideos")
    if not videos:
        videos = oauth_snapshot_to_videos(profile_data.get("oauth_videos") or [])
    handle = (profile_data.get("username") or profile_data.get("uniqueId") or "").strip().lstrip("@")
    return user_and_videos_to_raw_scrape(
        {
            "username": handle,
            "display_name": profile_data.get("display_name") or profile_data.get("nickname") or "",
            "bio_description": profile_data.get("bio_description") or profile_data.get("signature") or "",
            "follower_count": profile_data.get("follower_count") or profile_data.get("followerCount") or 0,
            "following_count": profile_data.get("following_count") or profile_data.get("followingCount") or 0,
            "video_count": profile_data.get("media_count") or profile_data.get("videoCount") or len(videos),
            "likes_count": profile_data.get("likes_count") or profile_data.get("heartCount") or 0,
            "is_verified": profile_data.get("is_verified") or profile_data.get("verified") or False,
            "avatar_url": profile_data.get("avatar_url") or profile_data.get("avatarUrl") or "",
            "profile_deep_link": profile_data.get("profile_deep_link") or profile_data.get("bioLink") or "",
            "open_id": profile_data.get("open_id") or "",
        },
        videos,
        handle_hint=handle,
    )


def fetch_raw_scrape(access_token: str, handle_hint: str = "") -> Dict[str, Any]:
    user = fetch_user_info(access_token)
    videos = fetch_videos(access_token)
    raw = user_and_videos_to_raw_scrape(user, videos, handle_hint=handle_hint)
    if not raw.get("uniqueId"):
        raise TikTokLoginKitError("Login Kit profile is missing a username")
    return raw


def refresh_access_token(refresh_token: str, client_key: str, client_secret: str) -> Dict[str, Any]:
    resp = requests.post(
        TIKTOK_TOKEN_URL,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        data={
            "client_key": client_key,
            "client_secret": client_secret,
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
        },
        timeout=15,
    )
    payload = resp.json() if resp.content else {}
    err = _api_error(payload)
    if err or not payload.get("access_token"):
        raise TikTokLoginKitError(f"token refresh failed: {err or payload}")
    expires_in = int(payload.get("expires_in") or 86400)
    return {
        "access_token": payload.get("access_token"),
        "refresh_token": payload.get("refresh_token") or refresh_token,
        "expires_at": datetime.utcnow() + timedelta(seconds=expires_in),
    }


def load_creator_tiktok_tokens(
    conn,
    *,
    user_id=None,
    creator_id=None,
    handle: str = "",
) -> Optional[Dict[str, Any]]:
    """Return decrypted TikTok tokens for a connected creator, or None."""
    if conn is None:
        return None
    clauses = ["social_platform = 'tiktok'", "social_oauth_token IS NOT NULL"]
    params: List[Any] = []
    if creator_id:
        clauses.append("id = %s")
        params.append(creator_id)
    elif user_id:
        clauses.append("user_id = %s")
        params.append(user_id)
    elif handle:
        clauses.append("LOWER(TRIM(BOTH '@' FROM social_handle)) = LOWER(%s)")
        params.append(handle.lstrip("@").strip())
    else:
        return None

    try:
        from psycopg2.extras import RealDictCursor
        cursor = conn.cursor(cursor_factory=RealDictCursor)
    except Exception:
        cursor = conn.cursor()
    try:
        cursor.execute(
            f"""
            SELECT id, user_id, social_handle, social_oauth_token,
                   social_oauth_refresh_token, social_token_expires_at
            FROM creators
            WHERE {' AND '.join(clauses)}
            ORDER BY social_connected_at DESC NULLS LAST
            LIMIT 1
            """,
            params,
        )
        row = cursor.fetchone()
    finally:
        cursor.close()

    if not row:
        return None
    if isinstance(row, dict):
        token = decrypt_token(row.get("social_oauth_token"))
        refresh = decrypt_token(row.get("social_oauth_refresh_token"))
        return {
            "creator_id": row.get("id"),
            "user_id": row.get("user_id"),
            "handle": row.get("social_handle"),
            "access_token": token,
            "refresh_token": refresh,
            "expires_at": row.get("social_token_expires_at"),
        }
    token = decrypt_token(row[3])
    refresh = decrypt_token(row[4])
    return {
        "creator_id": row[0],
        "user_id": row[1],
        "handle": row[2],
        "access_token": token,
        "refresh_token": refresh,
        "expires_at": row[5] if len(row) > 5 else None,
    }


def persist_refreshed_tokens(conn, creator_id, tokens: Dict[str, Any]) -> None:
    if not conn or not creator_id or not tokens:
        return
    cursor = conn.cursor()
    try:
        cursor.execute(
            """
            UPDATE creators
            SET social_oauth_token = %s,
                social_oauth_refresh_token = COALESCE(%s, social_oauth_refresh_token),
                social_token_expires_at = %s,
                social_last_checked_at = NOW()
            WHERE id = %s
            """,
            (
                encrypt_token(tokens.get("access_token")),
                encrypt_token(tokens.get("refresh_token")),
                tokens.get("expires_at"),
                creator_id,
            ),
        )
        conn.commit()
    finally:
        cursor.close()


def resolve_access_token(
    conn=None,
    *,
    user_id=None,
    creator_id=None,
    handle: str = "",
    access_token: Optional[str] = None,
    refresh_token: Optional[str] = None,
    client_key: str = "",
    client_secret: str = "",
) -> Tuple[Optional[str], Optional[Dict[str, Any]]]:
    """Return a live access token, refreshing from DB when needed."""
    if access_token:
        return access_token, None
    stored = load_creator_tiktok_tokens(
        conn, user_id=user_id, creator_id=creator_id, handle=handle
    )
    if not stored or not stored.get("access_token"):
        return None, stored
    token = stored["access_token"]
    expires_at = stored.get("expires_at")
    refresh = refresh_token or stored.get("refresh_token")
    expired = False
    if expires_at:
        try:
            if getattr(expires_at, "tzinfo", None):
                expired = expires_at.replace(tzinfo=None) <= datetime.utcnow()
            else:
                expired = expires_at <= datetime.utcnow()
        except Exception:
            expired = False
    if expired and refresh and client_key and client_secret:
        try:
            refreshed = refresh_access_token(refresh, client_key, client_secret)
            persist_refreshed_tokens(conn, stored.get("creator_id"), refreshed)
            return refreshed.get("access_token"), stored
        except TikTokLoginKitError as exc:
            _log(f"[login-kit] refresh failed: {exc}")
            return None, stored
    return token, stored
