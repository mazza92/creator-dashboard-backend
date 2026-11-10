"""Keep creator pitches landing: verify a brand's inbox before we pitch it, and swap in a better one when it bounces.

Two entry points:
- ``ensure_deliverable`` runs before Polly drafts a pitch or autopilot sends one. Addresses not checked in the last
  90 days go through Hunter's email verifier. An invalid one is replaced by the best verified alternative from
  Hunter's domain search, and the brand directory is updated so every creator gets the fix.
- ``repair_after_bounce`` runs when a creator reports a bounce. The address is marked bad for everyone and we look
  for a verified alternative to resend to.

Hunter calls are counted per day (``HUNTER_DAILY_CAP``, default 150). With no key, over the cap or on any Hunter
error we fail open: the pitch goes ahead with the address it had.
"""

from __future__ import annotations
from services.db_ddl import ensure_index

import os
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Dict, List, Optional

import requests
from psycopg2.extras import RealDictCursor

from services.radar_contact import HUNTER_URL as DOMAIN_SEARCH_URL
from services.radar_contact import _service_inbox, domain_of, email_rank, hunter_rank

VERIFY_URL = "https://api.hunter.io/v2/email-verifier"
ACCOUNT_URL = "https://api.hunter.io/v2/account"
TIMEOUT = 8
FRESH = timedelta(days=90)
MIN_CONFIDENCE = 30
MAX_CANDIDATES = 2  # alternatives we verify per repair

SENDABLE = ("valid", "catch-all", "unknown")
BAD = ("invalid", "bounced")


# Swappable in tests so nothing reaches the real Hunter API.
HTTP: Callable = requests.get


def _get(http: Optional[Callable]):
    return http or HTTP


def _now() -> datetime:
    return datetime.now(timezone.utc)


def daily_cap() -> int:
    try:
        return max(0, int(os.getenv("HUNTER_DAILY_CAP", "150")))
    except ValueError:
        return 150


def _cur(conn):
    return conn.cursor(cursor_factory=RealDictCursor)


def ensure_tables(conn) -> None:
    cur = conn.cursor()
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS hunter_usage (
            day DATE NOT NULL,
            kind TEXT NOT NULL,
            calls INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (day, kind)
        )
        """
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS brand_contact_repairs (
            id SERIAL PRIMARY KEY,
            brand_id INTEGER NOT NULL,
            old_email TEXT,
            new_email TEXT,
            reason TEXT NOT NULL,
            creator_id INTEGER,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """
    )
    ensure_index(cur, "CREATE INDEX IF NOT EXISTS idx_brand_contact_repairs_brand ON brand_contact_repairs (brand_id)")
    conn.commit()


# ---------------------------------------------------------------------------
# Hunter calls (counted, capped, fail open)
# ---------------------------------------------------------------------------

def _calls_today(conn) -> int:
    cur = _cur(conn)
    cur.execute("SELECT COALESCE(SUM(calls), 0) AS n FROM hunter_usage WHERE day = %s", (_now().date(),))
    return int(cur.fetchone()["n"])


def _spend(conn, kind: str) -> bool:
    """Reserve one Hunter call. False when there's no key or today's cap is reached."""
    if not os.getenv("HUNTER_API_KEY") or _calls_today(conn) >= daily_cap():
        return False
    cur = conn.cursor()
    cur.execute(
        """INSERT INTO hunter_usage (day, kind, calls) VALUES (%s, %s, 1)
           ON CONFLICT (day, kind) DO UPDATE SET calls = hunter_usage.calls + 1""",
        (_now().date(), kind),
    )
    conn.commit()
    return True


def status_from_hunter(data: Dict[str, Any]) -> str:
    """Hunter verifier payload -> our email_status vocabulary."""
    status = str(data.get("status") or "").lower()
    result = str(data.get("result") or "").lower()
    if status in ("invalid", "disposable") or result == "undeliverable":
        return "invalid"
    if status == "accept_all":
        return "catch-all"
    if status == "valid" or result == "deliverable":
        return "valid"
    return "unknown"


def hunter_verify(conn, email: str, http: Optional[Callable] = None) -> Optional[str]:
    """valid / catch-all / unknown / invalid, or None when Hunter can't be asked."""
    if not email or not _spend(conn, "verify"):
        return None
    try:
        resp = _get(http)(VERIFY_URL, params={"email": email, "api_key": os.getenv("HUNTER_API_KEY")}, timeout=TIMEOUT)
        if resp.status_code == 202:  # still verifying on Hunter's side
            return "unknown"
        if resp.status_code >= 400:
            return None
        return status_from_hunter(resp.json().get("data") or {})
    except (requests.RequestException, ValueError):
        return None


def hunter_alternatives(conn, domain: str, exclude: List[str], http: Optional[Callable] = None) -> List[Dict[str, Any]]:
    """Best inboxes Hunter knows on the brand's domain, best first. Marketing / PR / partnerships people lead."""
    if not domain or not _spend(conn, "domain_search"):
        return []
    try:
        resp = _get(http)(DOMAIN_SEARCH_URL, params={"domain": domain, "limit": 10, "api_key": os.getenv("HUNTER_API_KEY")},
                    timeout=TIMEOUT)
        if resp.status_code >= 400:
            return []
        rows = (resp.json().get("data") or {}).get("emails") or []
    except (requests.RequestException, ValueError):
        return []
    skip = {e.lower() for e in exclude if e}
    out = []
    for row in rows:
        email = str(row.get("value") or "").strip().lower()
        if (not email or email in skip or "@" not in email or domain_of(email.split("@")[1]) != domain
                or int(row.get("confidence") or 0) < MIN_CONFIDENCE or email_rank(email) >= 999
                or _service_inbox(email)):
            continue
        out.append({"email": email, "rank": hunter_rank(row), "confidence": int(row.get("confidence") or 0),
                    "position": row.get("position") or ""})
    out.sort(key=lambda e: (e["rank"], -e["confidence"]))
    return out


# ---------------------------------------------------------------------------
# Brand directory
# ---------------------------------------------------------------------------

def _brand(conn, brand_id: int) -> Optional[Dict[str, Any]]:
    cur = _cur(conn)
    cur.execute(
        """SELECT id, brand_name, website, NULLIF(TRIM(contact_email), '') AS contact_email, email_status,
                  email_verified_at, COALESCE(email_bounce_count, 0) AS email_bounce_count
           FROM pr_brands WHERE id = %s""",
        (brand_id,),
    )
    row = cur.fetchone()
    return dict(row) if row else None


def known_bad(conn, brand_id: int) -> List[str]:
    cur = _cur(conn)
    cur.execute("SELECT DISTINCT LOWER(old_email) AS e FROM brand_contact_repairs WHERE brand_id = %s AND old_email IS NOT NULL",
                (brand_id,))
    return [r["e"] for r in cur.fetchall()]


# What each adjustment means, for the note admins see on the brand.
ADJUSTMENT_TEXT = {
    "verify_invalid": ("old address was invalid, replaced with a verified one",
                       "address is invalid and no working replacement was found yet"),
    "bounce_repair": ("a creator's pitch bounced, replaced with a verified address",
                      "a creator's pitch bounced and no working replacement was found yet"),
    "creator_bounce": ("a creator's pitch bounced, replaced with a verified address",
                       "a creator reported a bounce on this address"),
    "dns_dead_domain": ("domain receives no email, replaced", "domain receives no email (no MX/A record)"),
}
_ADJ_READY = False


def _ensure_adjustment_columns(cur) -> None:
    global _ADJ_READY
    if _ADJ_READY:
        return
    from services.db_ddl import ensure_columns
    ensure_columns(cur, """ALTER TABLE pr_brands
        ADD COLUMN IF NOT EXISTS email_adjusted_at TIMESTAMPTZ,
        ADD COLUMN IF NOT EXISTS email_adjustment TEXT""")
    _ADJ_READY = True


def adjustment_note(old: Optional[str], new: Optional[str], reason: str, day: Optional[str] = None) -> str:
    fixed, unfixed = ADJUSTMENT_TEXT.get(reason, ("email updated", "email flagged"))
    day = day or _now().date().isoformat()
    if new:
        return f"[Email check {day}] {old or '(none)'} -> {new}: {fixed}"
    return f"[Email check {day}] {old or '(none)'}: {unfixed}"


def _log(conn, brand_id: int, old: Optional[str], new: Optional[str], reason: str, creator_id: Optional[int]) -> None:
    """Record every inbox adjustment: the repair log, plus a dated note and flag on the brand for the admin."""
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO brand_contact_repairs (brand_id, old_email, new_email, reason, creator_id) VALUES (%s, %s, %s, %s, %s)",
        (brand_id, (old or "").lower() or None, (new or "").lower() or None, reason, creator_id),
    )
    try:
        cur.execute("SAVEPOINT email_adjust_note")
        _ensure_adjustment_columns(cur)
        note = adjustment_note((old or "").lower() or None, (new or "").lower() or None, reason)
        cur.execute(
            """UPDATE pr_brands
               SET notes = CASE WHEN COALESCE(notes, '') = '' THEN %s ELSE notes || E'\n' || %s END,
                   email_adjusted_at = NOW(), email_adjustment = %s
               WHERE id = %s
                 AND POSITION(%s IN COALESCE(notes, '')) = 0""",
            (note, note, ("fixed" if new else "flagged") + ":" + reason, brand_id, note),
        )
        cur.execute("RELEASE SAVEPOINT email_adjust_note")
    except Exception as err:  # the note never blocks the repair itself
        print(f"[contact_repair] adjustment note skipped: {err}")
        try:
            cur.execute("ROLLBACK TO SAVEPOINT email_adjust_note")
        except Exception:
            pass


def _save_status(conn, brand_id: int, status: str) -> None:
    conn.cursor().execute(
        """UPDATE pr_brands SET email_status = %s, email_verification_source = 'hunter', email_verified_at = NOW()
           WHERE id = %s""",
        (status, brand_id),
    )
    conn.commit()


def _swap(conn, brand_id: int, old: Optional[str], new: str, status: str, reason: str,
          creator_id: Optional[int]) -> None:
    """Point the brand at a verified inbox. The bounce count belonged to the old address."""
    conn.cursor().execute(
        """UPDATE pr_brands SET contact_email = %s, email_status = %s, email_verification_source = 'hunter_repair',
                  email_verified_at = NOW(), email_bounce_count = 0
           WHERE id = %s""",
        (new, status, brand_id),
    )
    _log(conn, brand_id, old, new, reason, creator_id)
    conn.commit()


def find_replacement(conn, brand: Dict[str, Any], exclude: List[str], http: Optional[Callable] = None) -> Optional[Dict[str, str]]:
    """First alternative that Hunter verifies as sendable."""
    domain = domain_of(brand.get("website")) or domain_of((brand.get("contact_email") or "@").split("@")[1])
    for cand in hunter_alternatives(conn, domain, exclude, http=http)[:MAX_CANDIDATES]:
        status = hunter_verify(conn, cand["email"], http=http)
        if status in ("valid", "catch-all"):
            return {"email": cand["email"], "status": status}
        if status is None:  # out of budget or Hunter down: stop spending
            break
    return None


REPAIR_RETRY = timedelta(days=14)


def _recent_failed_repair(conn, brand_id: int, email: str) -> bool:
    cur = _cur(conn)
    cur.execute("""SELECT 1 FROM brand_contact_repairs
                   WHERE brand_id = %s AND LOWER(old_email) = %s AND new_email IS NULL
                     AND reason IN ('verify_invalid', 'dns_dead_domain') AND created_at > %s LIMIT 1""",
                (brand_id, email.lower(), _now() - REPAIR_RETRY))
    return cur.fetchone() is not None


def ensure_deliverable(conn, brand_id: Optional[int], email: Optional[str], *, creator_id: Optional[int] = None,
                       http: Optional[Callable] = None) -> Dict[str, Any]:
    """Before a pitch: the address to use and whether we changed it.

    Returns {"email": str|None, "status": str, "changed": bool, "previous": str|None}. ``email`` is None only when
    the address is known bad and no alternative was found.
    """
    email = (email or "").strip().lower()
    keep = {"email": email or None, "status": "unchecked", "changed": False, "previous": None}
    if not brand_id or not email:
        return keep
    try:
        ensure_tables(conn)
        brand = _brand(conn, int(brand_id))
        if not brand:
            return keep
        is_directory = email == (brand.get("contact_email") or "").lower()
        verified_at = brand.get("email_verified_at")
        if verified_at is not None and verified_at.tzinfo is None:
            verified_at = verified_at.replace(tzinfo=timezone.utc)
        fresh = bool(verified_at and verified_at > _now() - FRESH)
        if is_directory and fresh and (brand.get("email_status") or "") in SENDABLE:
            return {**keep, "status": brand["email_status"]}
        status = None
        if is_directory and (brand.get("email_status") or "") in BAD:
            status = "invalid"
        elif email in known_bad(conn, int(brand_id)):
            status = "invalid"
        else:
            status = hunter_verify(conn, email, http=http)
        if status is None:
            return keep
        if status in SENDABLE:
            if is_directory:
                _save_status(conn, int(brand_id), status)
            return {**keep, "status": status}
        # invalid: try to repair, everyone benefits. A search that already came back empty for this inbox in the
        # last REPAIR_RETRY isn't repeated (belif's dead inbox was searched 7 times in 5 days, 1 credit each).
        if _recent_failed_repair(conn, int(brand_id), email):
            return {"email": None, "status": "invalid", "changed": False, "previous": email}
        exclude = known_bad(conn, int(brand_id)) + [email]
        rep = find_replacement(conn, brand, exclude, http=http)
        if rep:
            _swap(conn, int(brand_id), email, rep["email"], rep["status"], "verify_invalid", creator_id)
            return {"email": rep["email"], "status": rep["status"], "changed": True, "previous": email}
        if is_directory:
            _save_status(conn, int(brand_id), "invalid")
        _log(conn, int(brand_id), email, None, "verify_invalid", creator_id)
        conn.commit()
        return {"email": None, "status": "invalid", "changed": False, "previous": email}
    except Exception as err:  # never block a pitch on this
        print(f"[contact_repair] ensure_deliverable skipped: {err}")
        try:
            conn.rollback()
        except Exception:
            pass
        return keep


def repair_after_bounce(conn, brand_id: Optional[int], bounced_email: Optional[str], *,
                        creator_id: Optional[int] = None, http: Optional[Callable] = None) -> Dict[str, Any]:
    """A creator's pitch bounced: mark the inbox bad for everyone, then look for a verified alternative.

    Returns {"bounced": str|None, "new_email": str|None, "status": str|None}.
    """
    out = {"bounced": None, "new_email": None, "status": None}
    if not brand_id:
        return out
    try:
        ensure_tables(conn)
        brand = _brand(conn, int(brand_id))
        if not brand:
            return out
        bounced = (bounced_email or brand.get("contact_email") or "").strip().lower() or None
        out["bounced"] = bounced
        if bounced and bounced == (brand.get("contact_email") or "").lower():
            conn.cursor().execute(
                """UPDATE pr_brands SET email_status = 'bounced', email_bounce_count = COALESCE(email_bounce_count, 0) + 1,
                          email_last_bounced_at = NOW() WHERE id = %s""",
                (int(brand_id),),
            )
        _log(conn, int(brand_id), bounced, None, "creator_bounce", creator_id)
        conn.commit()
        # Another creator may already have fixed this brand.
        current = (brand.get("contact_email") or "").lower()
        if current and current != bounced and (brand.get("email_status") or "") in ("valid", "catch-all"):
            return {**out, "new_email": current, "status": brand["email_status"]}
        exclude = known_bad(conn, int(brand_id)) + [bounced or ""]
        rep = find_replacement(conn, brand, exclude, http=http)
        if rep:
            _swap(conn, int(brand_id), bounced, rep["email"], rep["status"], "bounce_repair", creator_id)
            return {**out, "new_email": rep["email"], "status": rep["status"]}
        return out
    except Exception as err:
        print(f"[contact_repair] repair_after_bounce skipped: {err}")
        try:
            conn.rollback()
        except Exception:
            pass
        return out


def account_status(http: Optional[Callable] = None) -> Dict[str, Any]:
    """Hunter plan and remaining credits, for the admin view."""
    key = os.getenv("HUNTER_API_KEY")
    if not key:
        return {"configured": False}
    try:
        resp = _get(http)(ACCOUNT_URL, params={"api_key": key}, timeout=TIMEOUT)
        data = resp.json().get("data") or {}
    except (requests.RequestException, ValueError) as err:
        return {"configured": True, "error": str(err)[:200]}
    req = data.get("requests") or {}
    return {
        "configured": True,
        "plan": data.get("plan_name"),
        "reset_date": data.get("reset_date"),
        "searches": req.get("searches") or {},
        "verifications": req.get("verifications") or {},
        "credits": req.get("credits") or {},
    }


def admin_summary(conn) -> Dict[str, Any]:
    ensure_tables(conn)
    cur = _cur(conn)
    cur.execute("SELECT day, kind, calls FROM hunter_usage WHERE day >= %s ORDER BY day DESC, kind",
                (_now().date() - timedelta(days=13),))
    usage = [{"day": str(r["day"]), "kind": r["kind"], "calls": r["calls"]} for r in cur.fetchall()]
    cur.execute(
        """SELECT r.id, r.brand_id, b.brand_name, r.old_email, r.new_email, r.reason, r.creator_id, r.created_at
           FROM brand_contact_repairs r LEFT JOIN pr_brands b ON b.id = r.brand_id
           ORDER BY r.created_at DESC LIMIT 50"""
    )
    repairs = [{**dict(r), "created_at": r["created_at"].isoformat() if r.get("created_at") else None}
               for r in cur.fetchall()]
    cur.execute(
        """SELECT reason, COUNT(*) AS n, COUNT(new_email) AS fixed FROM brand_contact_repairs
           WHERE created_at >= NOW() - INTERVAL '30 days' GROUP BY reason"""
    )
    last30 = {r["reason"]: {"events": r["n"], "fixed": r["fixed"]} for r in cur.fetchall()}
    return {"daily_cap": daily_cap(), "calls_today": _calls_today(conn), "usage": usage, "last_30_days": last30,
            "recent": repairs}


def set_manual_email(conn, brand_id: int, old: Optional[str], new: Optional[str],
                     http: Optional[Callable] = None) -> Dict[str, Any]:
    """An admin typed a new contact email (e.g. working through "Email dead" brands).

    Clears the old verdict (invalid / bounced / dead flag) so Autopilot and the send gate pick the brand up again,
    checks the new address right away (live domain + Hunter) so the admin sees whether it works, and notes it on the
    brand. Returns {"status": valid|catch-all|unknown|invalid|unchecked, "message": str}.
    """
    new = (new or "").strip().lower()
    old = (old or "").strip().lower() or None
    if not new or new == old:
        return {"status": "unchanged", "message": ""}
    from services.deliverability import EMAIL_RE, domain_receives_mail

    if not EMAIL_RE.match(new):
        status = "invalid"
    elif domain_receives_mail(new.rsplit("@", 1)[1], http=http) is False:
        status = "invalid"
    elif new in known_bad(conn, int(brand_id)):
        status = "invalid"
    else:
        status = hunter_verify(conn, new, http=http) or "unchecked"
    cur = conn.cursor()
    _ensure_adjustment_columns(cur)
    verified = status in ("valid", "catch-all", "unknown", "invalid")
    cur.execute(
        """UPDATE pr_brands SET email_status = %s, email_bounce_count = 0, email_last_bounced_at = NULL,
                  email_verification_source = 'manual', email_verified_at = CASE WHEN %s THEN NOW() ELSE NULL END,
                  email_adjusted_at = NOW(), email_adjustment = %s
           WHERE id = %s""",
        ("unverified" if status == "unchecked" else status, verified,
         "manual" if status in ("valid", "catch-all", "unchecked", "unknown") else "flagged:manual_invalid", brand_id),
    )
    label = {"valid": "Hunter: valid", "catch-all": "Hunter: accept all", "unknown": "Hunter: unknown",
             "invalid": "invalid, won't be used", "unchecked": "not verified yet, checked before the next pitch"}[status]
    note = f"[Email check {_now().date().isoformat()}] {old or '(none)'} -> {new}: entered manually by admin ({label})"
    cur.execute("""UPDATE pr_brands SET notes = CASE WHEN COALESCE(notes, '') = '' THEN %s ELSE notes || E'\\n' || %s END
                   WHERE id = %s""", (note, note, brand_id))
    cur.execute("INSERT INTO brand_contact_repairs (brand_id, old_email, new_email, reason) VALUES (%s, %s, %s, 'manual')",
                (brand_id, old, new))
    conn.commit()
    messages = {
        "valid": "Verified: this inbox works. Autopilot will use it.",
        "catch-all": "Accepted: the domain accepts all mail (can't confirm the person). Autopilot will use it.",
        "unknown": "Saved, but Hunter couldn't confirm it. Manual sends work; Autopilot will retry the check.",
        "unchecked": "Saved. It will be verified before the next pitch goes out.",
        "invalid": "This address doesn't work (invalid or dead domain). Try another one.",
    }
    return {"status": status, "message": messages[status]}
