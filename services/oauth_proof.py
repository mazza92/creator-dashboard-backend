"""Onboarding social proof that survives a browser switch (Nov 2026).

Connecting TikTok/Instagram at onboarding step 1 used to leave its proof (verified handle, follower count) only in
the browser session of whatever browser the platform returned to. On phones that is often another browser (TikTok's
in-app browser, the default browser): the proof landed there, the creator's original tab could never finish step 1,
and qualified creators were stuck (e.g. a 5.4k-follower TikTok verified 9 times in 30 seconds, never saved).

The callback now also saves the verified proof here, keyed by user: step 1 falls back to it when the session has
none, and onboarding asks for it on load to continue straight to step 2. Only counts and the handle, never tokens.
Proofs older than a day are ignored.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

MAX_AGE_HOURS = 24

SCHEMA_SQL = """
    CREATE TABLE IF NOT EXISTS onboarding_oauth_proof (
        user_id INTEGER PRIMARY KEY,
        platform TEXT NOT NULL,
        handle TEXT NOT NULL,
        followers INTEGER NOT NULL DEFAULT 0,
        posts INTEGER NOT NULL DEFAULT 0,
        avatar_url TEXT,
        verified BOOLEAN NOT NULL DEFAULT FALSE,
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
    )
"""


def ensure_schema(conn) -> None:
    cur = conn.cursor()
    cur.execute(SCHEMA_SQL)
    conn.commit()


def save(conn, user_id: int, platform: str, profile_data: Dict[str, Any], passed: bool) -> None:
    handle = str(profile_data.get("username") or "").strip().lstrip("@")
    if not user_id or not handle:
        return
    ensure_schema(conn)
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO onboarding_oauth_proof (user_id, platform, handle, followers, posts, avatar_url, verified, created_at)
        VALUES (%s, %s, %s, %s, %s, %s, %s, NOW())
        ON CONFLICT (user_id) DO UPDATE SET platform = EXCLUDED.platform, handle = EXCLUDED.handle,
            followers = EXCLUDED.followers, posts = EXCLUDED.posts, avatar_url = EXCLUDED.avatar_url,
            verified = EXCLUDED.verified, created_at = NOW()
    """, (int(user_id), platform, handle, int(profile_data.get("follower_count") or 0),
          int(profile_data.get("media_count") or 0), (profile_data.get("avatar_url") or "")[:1000] or None,
          bool(passed)))
    conn.commit()


def load(conn, user_id: Optional[int]) -> Optional[Dict[str, Any]]:
    """The verified proof from the last day, or None. Never raises."""
    if not user_id:
        return None
    try:
        ensure_schema(conn)
        cur = conn.cursor()
        cur.execute("""
            SELECT platform, handle, followers, posts, avatar_url FROM onboarding_oauth_proof
            WHERE user_id = %s AND verified AND created_at > NOW() - make_interval(hours => %s)
        """, (int(user_id), MAX_AGE_HOURS))
        row = cur.fetchone()
    except Exception as err:  # noqa: BLE001
        print(f"[oauth_proof] load skipped user={user_id}: {err}")
        try:
            conn.rollback()
        except Exception:
            pass
        return None
    if not row:
        return None
    keys = ("platform", "handle", "followers", "posts", "avatar_url")
    return dict(row) if isinstance(row, dict) else dict(zip(keys, row))


def as_session(proof: Dict[str, Any]) -> Dict[str, Any]:
    """The same shapes step 1 reads from the session (social_verification_result, onboarding_quality)."""
    return {
        "social_verification_result": {
            "verified": True,
            "platform": proof["platform"],
            "profile": {"username": proof["handle"], "follower_count": int(proof.get("followers") or 0),
                        "media_count": int(proof.get("posts") or 0)},
        },
        "onboarding_quality": {"handle": str(proof["handle"]).lower(), "platform": proof["platform"],
                               "followers": int(proof.get("followers") or 0)},
        "scraped_avatar_url": proof.get("avatar_url") or "",
    }

