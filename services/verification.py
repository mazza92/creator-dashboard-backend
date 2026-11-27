"""Verification is separate from activation (Nov 2026).

Newcollab used to gate the whole app behind approval (the PR marketplace protected brands from unsuitable
applicants). As outreach software, 60% of new signups sat on a waitlist and only ~3% paid to skip it. Now:

- Every creator explores right away: their matched brands, a written pitch, their plan (the welcome flow, Polly,
  Brands).
- Outreach waits for verification: an unverified creator (approval_status pending or rejected, not on Pro) never
  gets a brand's email, and every send / copy / "I sent it" is refused with VERIFICATION_REQUIRED. Pitches Polly
  writes for them come back without the inbox (redact_pitch).
- Pro (or the free week) unlocks outreach immediately (the checkout webhook sets pro_approved). Those creators
  send from their own Gmail only (no Newcollab relay) and their pitches don't train Thanos's reply signals until
  the team approves them (services.thanos.signals).
- Not verified yet means "not ready yet": readiness() lists what the review checks, so the creator can fix it.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

UNVERIFIED = ("pending", "rejected")
MESSAGE = ("Sending unlocks once your profile is verified. Explore your brands and pitches meanwhile, or skip the "
           "wait with Pro.")
VERIFICATION_REQUIRED = {"success": False, "verification_required": True, "error": "verification_required",
                         "message": MESSAGE}


def _row(conn, creator_id: int) -> Dict[str, Any]:
    cur = conn.cursor()
    cur.execute("SELECT approval_status, COALESCE(subscription_tier, 'free') FROM creators WHERE id = %s",
                (int(creator_id),))
    row = cur.fetchone()
    if not row:
        return {}
    if isinstance(row, dict):
        return {"status": row.get("approval_status"), "tier": row.get("coalesce") or row.get("subscription_tier")}
    return {"status": row[0], "tier": row[1]}


def is_unverified(conn, creator_id: Optional[int]) -> bool:
    """True when outreach is locked. Never raises: on any error outreach stays open (fail-open, as before)."""
    if not creator_id:
        return False
    try:
        row = _row(conn, creator_id)
    except Exception as err:  # noqa: BLE001
        print(f"[verification] check skipped creator={creator_id}: {err}")
        try:
            conn.rollback()
        except Exception:
            pass
        return False
    return str(row.get("status") or "").lower() in UNVERIFIED and str(row.get("tier") or "free") != "pro"


def skipped_review(conn, creator_id: Optional[int]) -> bool:
    """Paid to skip verification (pro_approved): Gmail-only sending, pitches kept out of Thanos signals."""
    if not creator_id:
        return False
    try:
        return str(_row(conn, creator_id).get("status") or "").lower() == "pro_approved"
    except Exception:  # noqa: BLE001
        try:
            conn.rollback()
        except Exception:
            pass
        return False


def refuse_unverified(creator_id: Optional[int]):
    """For routes that hand out a brand's inbox: (body, 403) when outreach is locked, else None. Opens its own
    connection so it can sit at the top of any route; fail-open like is_unverified."""
    try:
        from pr_crm_routes import get_db_connection
        conn = get_db_connection()
    except Exception as err:  # noqa: BLE001
        print(f"[verification] contact gate skipped: {err}")
        return None
    try:
        return (VERIFICATION_REQUIRED, 403) if is_unverified(conn, creator_id) else None
    finally:
        try:
            conn.close()
        except Exception:
            pass


def redact_pitch(pitch: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """The pitch without the brand's inbox, marked locked: they can read and edit it, not send it."""
    if not isinstance(pitch, dict):
        return pitch
    return {**pitch, "email": "", "mailto": None, "locked": "verification"}


def readiness(conn, creator_id: int) -> Dict[str, Any]:
    """What the review checks, from their profile: 12+ public posts and a post in the last 30 days."""
    from services.profile_quality import MAX_LAST_POST_DAYS, MIN_POSTS
    status = ""
    posts: Optional[int] = None
    days: Optional[int] = None
    try:
        status = str(_row(conn, creator_id).get("status") or "").lower()
        cur = conn.cursor()
        cur.execute("""SELECT d.post_count, d.latest_post_days_ago
                       FROM creators c JOIN creator_profile_data d ON d.user_id = c.user_id
                       WHERE c.id = %s LIMIT 1""", (int(creator_id),))
        row = cur.fetchone()
        if row:
            posts = row[0] if not isinstance(row, dict) else row.get("post_count")
            days = row[1] if not isinstance(row, dict) else row.get("latest_post_days_ago")
    except Exception as err:  # noqa: BLE001
        print(f"[verification] readiness partial creator={creator_id}: {err}")
        try:
            conn.rollback()
        except Exception:
            pass
    checks: List[Dict[str, Any]] = [
        {"id": "posts", "label": f"{MIN_POSTS}+ public posts",
         "ok": None if posts is None else int(posts) >= MIN_POSTS,
         "detail": None if posts is None else f"You have {int(posts)}"},
        {"id": "recent", "label": f"A post in the last {MAX_LAST_POST_DAYS} days",
         "ok": None if days is None or int(days) >= 999 else int(days) <= MAX_LAST_POST_DAYS,
         "detail": None if days is None or int(days) >= 999 else f"Last post {int(days)} day{'s' if int(days) != 1 else ''} ago"},
        {"id": "quality", "label": "Clear, natural videos with your face or hands on camera", "ok": None,
         "detail": "Checked by our team"},
    ]
    state = "verified" if status not in UNVERIFIED else ("not_ready" if status == "rejected" else "checking")
    return {"status": state, "checks": checks, "message": MESSAGE}
