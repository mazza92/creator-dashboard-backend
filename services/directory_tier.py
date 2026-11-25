"""Directory access: creators Newcollab hasn't approved (pending or rejected) can still use the brand directory.

Nov 2026 decision: ~837 pending creators won't be approved for quality reasons, and rosters are stopped. Instead
of a waitlist they get the directory only: browse brands, unlock a brand's contact email, and a generated pitch
they send from their own email. Free: 10 unlocks in total. Pro ($19/mo, no trial): 500 unlocks a month.

Their pitches never mention Newcollab, so brands never connect the platform with creators it didn't approve:
no kit link (their social profile is the proof), no tracked links, no "via Newcollab" sending, no Autopilot.
Paying for Pro no longer approves anyone: approval stays a quality decision.
"""

from __future__ import annotations

import re
import time
from typing import Any, Dict, Optional

APPROVED = ("approved", "pro_approved")
FREE_TOTAL = 10
PRO_MONTHLY = 500

_STATUS_SQL = "SELECT approval_status FROM creators WHERE id = %s"


def _get(row, key, idx=0):
    if row is None:
        return None
    return row.get(key) if isinstance(row, dict) else row[idx]


def is_directory_status(status: Any) -> bool:
    return str(status or "").strip().lower() not in APPROVED


def is_directory(cursor, creator_id) -> bool:
    """True for creators who aren't approved. Unknown creators are not directory (callers 404 them anyway)."""
    if not creator_id:
        return False
    cursor.execute(_STATUS_SQL, (creator_id,))
    row = cursor.fetchone()
    if row is None:
        return False
    return is_directory_status(_get(row, "approval_status"))


_CACHE: Dict[int, tuple] = {}
CACHE_SECONDS = 60


def is_directory_id(creator_id) -> bool:
    """is_directory with its own connection, for request guards. Cached a minute per creator (approval changes
    are rare and an admin approval shows within a minute). False on any error."""
    if not creator_id:
        return False
    key = int(creator_id)
    hit = _CACHE.get(key)
    now = time.time()
    if hit and now - hit[1] < CACHE_SECONDS:
        return hit[0]
    try:
        from pr_crm_routes import get_db_connection
        conn = get_db_connection()
    except Exception:  # noqa: BLE001
        return False
    try:
        value = is_directory(conn.cursor(), key)
    except Exception:  # noqa: BLE001
        return False
    finally:
        conn.close()
    if len(_CACHE) > 5000:
        _CACHE.clear()
    _CACHE[key] = (value, now)
    return value


def _count(cursor, sql, params) -> int:
    cursor.execute(sql, params)
    row = cursor.fetchone()
    value = _get(row, "n")
    return int(value or 0)


def used(cursor, creator_id, is_pro: bool) -> int:
    """Brands unlocked: this calendar month on Pro, ever on free."""
    if is_pro:
        return _count(cursor, """SELECT COUNT(DISTINCT brand_id) AS n FROM brand_unlocks
                                  WHERE creator_id = %s AND unlocked_at >= date_trunc('month', NOW())""",
                      (creator_id,))
    return _count(cursor, "SELECT COUNT(DISTINCT brand_id) AS n FROM brand_unlocks WHERE creator_id = %s",
                  (creator_id,))


def balance(cursor, creator_id, is_pro: bool) -> Dict[str, Any]:
    limit = PRO_MONTHLY if is_pro else FREE_TOTAL
    n = used(cursor, creator_id, is_pro)
    return {
        "tier": "pro" if is_pro else "free",
        "access": "directory",
        "remaining": max(0, limit - n),
        "used": n,
        "limit": limit,
        "period": "month" if is_pro else "total",
        "reset_at": None,
        "is_unlimited": False,
        "trial_days_left": None,
    }


def at_limit(cursor, creator_id, is_pro: bool) -> bool:
    return used(cursor, creator_id, is_pro) >= (PRO_MONTHLY if is_pro else FREE_TOTAL)


# Lines and links that would tell a brand the pitch came through Newcollab.
_NEWCOLLAB_URL = re.compile(r"https?://(?:www\.)?(?:app\.)?newcollab\.co\S*", re.I)
_NEWCOLLAB_WORD = re.compile(r"newcollab", re.I)


def scrub(text: Optional[str]) -> Optional[str]:
    """Drop every line that mentions Newcollab (kit links, 'via Newcollab', signatures). Keeps the rest as is."""
    if not isinstance(text, str) or not _NEWCOLLAB_WORD.search(text):
        return text
    kept = []
    for line in text.split("\n"):
        if _NEWCOLLAB_WORD.search(line):
            continue
        kept.append(line)
    out = "\n".join(kept)
    out = re.sub(r"\n{3,}", "\n\n", out).strip("\n")
    return out


def scrub_html(text: Optional[str]) -> Optional[str]:
    """HTML pitch bodies: drop paragraphs / line breaks that mention Newcollab."""
    if not isinstance(text, str) or not _NEWCOLLAB_WORD.search(text):
        return text
    out = re.sub(r"<p>(?:(?!</p>).)*newcollab(?:(?!</p>).)*</p>", "", text, flags=re.I | re.S)
    out = re.sub(r"[^<>\n]*newcollab[^<>\n]*(<br\s*/?>)?", "", out, flags=re.I)
    return _NEWCOLLAB_URL.sub("", out)


def scrub_payload(value: Any) -> Any:
    """Every string in a JSON payload, scrubbed (HTML-looking strings with scrub_html)."""
    if isinstance(value, dict):
        return {k: scrub_payload(v) for k, v in value.items()}
    if isinstance(value, list):
        return [scrub_payload(v) for v in value]
    if isinstance(value, str):
        return scrub_html(value) if "<" in value and ">" in value else scrub(value)
    return value
