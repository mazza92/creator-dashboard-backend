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
    out = {"status": state, "checks": checks, "message": MESSAGE, "preview_used": False}
    if state != "verified":
        out["preview_used"] = len(preview_brands(conn, creator_id)) >= PREVIEW_PITCHES
    return out


# ---- Back after verification -------------------------------------------------------------------------------
# A creator who explored while being verified has already seen (and closed) the welcome and has pitches drafted,
# so the normal first-run rules would never show it again. Once the team verifies them, the welcome comes back
# once, opening on "You're verified" and then the two ways to start, and the pitches saved locked get their inbox.

EXPLORED = "explored_unverified"
RETURN_DONE = "verified_welcome_done"


def needs_verified_welcome(conn, creator_id: Optional[int]) -> bool:
    """Verified by the team (not paid to skip), explored before, hasn't seen the 'You're verified' welcome yet."""
    if not creator_id:
        return False
    try:
        row = _row(conn, creator_id)
        if str(row.get("status") or "").lower() != "approved" or str(row.get("tier") or "free") == "pro":
            return False
        from services.creator_tracker import milestones
        done = milestones(conn, creator_id)
        return EXPLORED in done and RETURN_DONE not in done
    except Exception as err:  # noqa: BLE001
        print(f"[verification] return check skipped creator={creator_id}: {err}")
        try:
            conn.rollback()
        except Exception:
            pass
        return False


def _locked_cards(messages: List[Any]) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for msg in messages or []:
        if not isinstance(msg, dict):
            continue
        pitch = msg.get("pitch")
        if isinstance(pitch, dict) and pitch.get("locked") == "verification":
            out.append(pitch)
        batch = msg.get("pitch_batch")
        items = batch.get("items") if isinstance(batch, dict) else batch if isinstance(batch, list) else None
        for item in items or []:
            if isinstance(item, dict) and item.get("locked") == "verification":
                out.append(item)
    return out


def unlock_saved_pitches(conn, creator_id: int, messages: List[Any], notes: Optional[Dict[str, Any]] = None):
    """(messages, changed). Pitches saved while locked get the brand's inbox back once outreach is open: from the
    server-side outbox when Polly kept it, else the brand's contact on file. Edited in place."""
    cards = _locked_cards(messages)
    if not cards or is_unverified(conn, creator_id):
        return messages, False
    from services.polly import build_mailto
    outbox = (notes or {}).get("pitch_outbox") or {}
    ids = sorted({int(c["brand_id"]) for c in cards if str(c.get("brand_id") or "").isdigit()})
    found: Dict[int, str] = {}
    if ids:
        try:
            cur = conn.cursor()
            cur.execute("SELECT id, TRIM(contact_email) FROM pr_brands WHERE id = ANY(%s)", (ids,))
            for row in cur.fetchall():
                bid, email = (row.get("id"), row.get("btrim")) if isinstance(row, dict) else (row[0], row[1])
                if email:
                    found[int(bid)] = email
        except Exception as err:  # noqa: BLE001
            print(f"[verification] inbox lookup skipped creator={creator_id}: {err}")
            try:
                conn.rollback()
            except Exception:
                pass
    for card in cards:
        bid = str(card.get("brand_id") or "")
        kept = outbox.get(bid) if isinstance(outbox.get(bid), dict) else {}
        email = kept.get("email") or (found.get(int(bid)) if bid.isdigit() else None) or ""
        card.pop("locked", None)
        card["email"] = email
        card["mailto"] = build_mailto(email, card.get("subject") or "", card.get("body") or "") if email else None
    return messages, True


# ---- One pitch while being verified ------------------------------------------------------------------------
# Opening the app before review must not let unverified signups spend Gemini on pitches they can't send. They get
# one: the first pitch (the welcome preview, or whichever brand they try first) is the tease; every further pitch
# waits for verification or Pro. Editing that one pitch stays open.

PREVIEW_PITCHES = 1
PREVIEW_LOCK_MESSAGE = ("Your first pitch is ready. More pitches unlock once your profile is verified, or skip the "
                        "wait with Pro.")
PREVIEW_TOOLS = ("draft_pitch", "draft_pitch_batch", "draft_brand_reply")


def preview_brands(conn, creator_id: int) -> set:
    """Brands Polly has already written a pitch for (polly_drafts). Empty on any error."""
    try:
        cur = conn.cursor()
        cur.execute("SELECT to_regclass('polly_drafts') IS NOT NULL")
        row = cur.fetchone()
        if not (row and (row[0] if not isinstance(row, dict) else list(row.values())[0])):
            return set()
        cur.execute("SELECT DISTINCT brand_id FROM polly_drafts WHERE creator_id = %s", (int(creator_id),))
        return {int(r[0] if not isinstance(r, dict) else r.get("brand_id")) for r in cur.fetchall()}
    except Exception as err:  # noqa: BLE001
        print(f"[verification] preview count skipped creator={creator_id}: {err}")
        try:
            conn.rollback()
        except Exception:
            pass
        return set()


def preview_gate(conn, creator_id: int, tool: str, args: Dict[str, Any]):
    """(locked_result or None, args). Only for unverified creators and pitch-writing tools; never raises."""
    if tool not in PREVIEW_TOOLS or not is_unverified(conn, creator_id):
        return None, args
    locked = {"ok": False, "status": "preview_used", "outreach_locked": True, "verification_required": True,
              "explain_lock": PREVIEW_LOCK_MESSAGE + " Say this in one short line; don't write or promise a pitch."}
    if tool == "draft_brand_reply":
        return locked, args
    drafted = preview_brands(conn, creator_id)

    def _id(value):
        try:
            return int(value)
        except (TypeError, ValueError):
            return None

    if tool == "draft_pitch":
        bid = _id(args.get("brand_id"))
        if len(drafted) >= PREVIEW_PITCHES and (bid is None or bid not in drafted):
            return locked, args
        return None, args
    # draft_pitch_batch: one pitch at most, and none once the preview is used.
    if len(drafted) >= PREVIEW_PITCHES:
        return locked, args
    ids = [i for i in (_id(x) for x in (args.get("brand_ids") or [])) if i]
    return None, {**args, "brand_ids": ids[:PREVIEW_PITCHES], "replace": None}


CHAT_LOCK_MESSAGE = ("Polly gets to work on your outreach once your profile is verified. Your first pitch is saved; "
                     "skip the wait with Pro to start today.")


def chat_locked(conn, creator_id: Optional[int]) -> bool:
    """Being verified and the one preview pitch is used: Polly's chat waits too (each turn is a Gemini call)."""
    if not creator_id or not is_unverified(conn, creator_id):
        return False
    return len(preview_brands(conn, creator_id)) >= PREVIEW_PITCHES
