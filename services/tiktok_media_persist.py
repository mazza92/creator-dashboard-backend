"""Copy TikTok Login Kit avatars to our storage.

TikTok CDN links are signed and stop loading after a few days, which left public
kits without a photo. Video stills stay on the CDN and recover via /api/media-proxy.
"""

from __future__ import annotations

import threading

from psycopg2.extras import RealDictCursor


def persist_creator_tiktok_avatar(conn, creator_id: int, avatar_url: str = "") -> bool:
    """Rehost the creator's avatar and save it. Caller owns conn."""
    from media_proxy_routes import _already_hosted, rehost_social_image

    cur = conn.cursor(cursor_factory=RealDictCursor)
    cur.execute("SELECT image_profile FROM creators WHERE id = %s", (creator_id,))
    row = cur.fetchone() or {}
    current = (row.get("image_profile") or "").strip()
    source = (avatar_url or current).strip()
    if not source or _already_hosted(current):
        cur.close()
        return False
    hosted = rehost_social_image(source, dest_prefix=f"avatars/{creator_id}") or ""
    if hosted:
        cur.execute("UPDATE creators SET image_profile = %s WHERE id = %s", (hosted, creator_id))
        conn.commit()
    cur.close()
    return bool(hosted)


def persist_creator_tiktok_avatar_async(creator_id: int, avatar_url: str = "") -> None:
    """Fire-and-forget version for the OAuth callback."""

    def run():
        from pr_crm_routes import get_db_connection

        conn = None
        try:
            conn = get_db_connection()
            saved = persist_creator_tiktok_avatar(conn, creator_id, avatar_url=avatar_url)
            print(f"[tiktok-avatar] creator {creator_id}: saved={saved}")
        except Exception as exc:
            print(f"[tiktok-avatar] creator {creator_id} failed: {exc}")
            if conn:
                conn.rollback()
        finally:
            if conn:
                conn.close()

    threading.Thread(target=run, daemon=True).start()
