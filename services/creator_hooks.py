"""Hooks that work, for creators (Polly): the brand workspace's niche hooks answer, from creators their size.

Same engine as the brand side (services.brand_insights kind "creator_hooks"): planned TikTok searches in the
creator's niche, posts from creators near their follower band, and an answer with effective opening lines,
top-performing video formats and the brands gifting or paying creators that size (linked to the directory
so Polly can pitch them).

Allowance: 1 scan for free creators (ever), 4 a calendar month on Pro. Re-opening a scan is free.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, Optional

from psycopg2.extras import RealDictCursor

FREE_SCANS = 1
PRO_SCANS_PER_MONTH = 4
CHIP = {"id": "creator_hooks", "label": "🎣 Hooks that work in my niche", "action": "creator_hooks"}

_READY = False


def _now() -> datetime:
    return datetime.now(timezone.utc)


def ensure_table(conn) -> None:
    global _READY
    if _READY:
        return
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS polly_hook_scans (
            id SERIAL PRIMARY KEY,
            creator_id INTEGER NOT NULL,
            run_id INTEGER,
            niche TEXT,
            band TEXT,
            pro BOOLEAN NOT NULL DEFAULT FALSE,
            created_at TIMESTAMPTZ DEFAULT NOW()
        )
    """)
    cur.execute("CREATE INDEX IF NOT EXISTS idx_polly_hook_scans_creator ON polly_hook_scans (creator_id, created_at)")
    conn.commit()
    _READY = True


def allowance(conn, creator_id: int, pro: bool, now: Optional[datetime] = None) -> Dict[str, Any]:
    ensure_table(conn)
    now = now or _now()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    if pro:
        start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        cur.execute("SELECT COUNT(*) AS n FROM polly_hook_scans WHERE creator_id = %s AND pro AND created_at >= %s",
                    (creator_id, start))  # the free scan doesn't eat into Pro's 4
        limit = PRO_SCANS_PER_MONTH
    else:
        from services.unlock_quota import free_limit

        cur.execute("SELECT COUNT(*) AS n FROM polly_hook_scans WHERE creator_id = %s", (creator_id,))
        used_row = cur.fetchone()
        limit = min(FREE_SCANS, free_limit(cur, creator_id))  # an account with 0 free credits gets no free scan
        cur.execute("SELECT %s::int AS n", (int((used_row or {}).get("n") or 0),))
    used = int((cur.fetchone() or {}).get("n") or 0)
    return {"used": used, "limit": limit, "left": max(0, limit - used), "allowed": used < limit, "pro": pro,
            "period": "month" if pro else "ever"}


def record(conn, creator_id: int, run_id: Optional[int], niche: str, band: str, pro: bool = False) -> None:
    ensure_table(conn)
    cur = conn.cursor()
    cur.execute("INSERT INTO polly_hook_scans (creator_id, run_id, niche, band, pro) VALUES (%s, %s, %s, %s, %s)",
                (creator_id, run_id, niche, band, bool(pro)))
    conn.commit()


def owns(conn, creator_id: int, run_id: int) -> bool:
    ensure_table(conn)
    cur = conn.cursor()
    cur.execute("SELECT 1 FROM polly_hook_scans WHERE creator_id = %s AND run_id = %s LIMIT 1", (creator_id, run_id))
    return cur.fetchone() is not None


def creator_niche(creator: Dict[str, Any]) -> str:
    """The creator's own recorded niches (up to 3), as one scan key: 'travel+fashion+wellness'.

    Never the brand-category mapping (that sends travel and lifestyle to home): the scan targets exactly the
    niches the creator picked, and the planned searches cover each of them.
    """
    from services.creator_niches import recorded_niches

    picked = []
    for n in recorded_niches(creator) or []:
        key = "_".join(str(n or "").strip().lower().replace("&", " ").split())
        if key and key not in picked:
            picked.append(key)
    return "+".join(picked[:3])


def gate_message(allow: Dict[str, Any]) -> str:
    if allow["pro"]:
        return (f"You've used your {allow['limit']} hook scans this month. They reset on the 1st, and every scan "
                "you ran stays in this chat.")
    return ("Your free hook scan is used. On Pro you get 4 a month, plus Polly sends your pitches every weekday "
            "from your Gmail.")
