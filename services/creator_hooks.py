"""Hooks that work, for creators (Polly): the brand workspace's niche hooks answer, from creators their size.

Same engine as the brand side (services.brand_insights kind "creator_hooks"): planned TikTok searches in the
creator's niche, posts from creators near their follower band, and an answer with effective opening lines,
top-performing video formats and the brands gifting or paying creators that size (linked to the directory
so Polly can pitch them).

Allowance: 1 scan for free creators (ever), 4 a calendar month on Pro. Re-opening a scan is free.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
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
    # viewed_at: the creator saw the finished playbook in chat. notified_at: we emailed it (or decided not to).
    cur.execute("""SELECT 1 FROM information_schema.columns
                   WHERE table_name = 'polly_hook_scans' AND column_name = 'notified_at'""")
    if not cur.fetchone():
        cur.execute("ALTER TABLE polly_hook_scans ADD COLUMN IF NOT EXISTS viewed_at TIMESTAMPTZ")
        cur.execute("ALTER TABLE polly_hook_scans ADD COLUMN IF NOT EXISTS notified_at TIMESTAMPTZ")
        # Scans from before this existed were already seen (or abandoned long ago): never email them.
        cur.execute("UPDATE polly_hook_scans SET notified_at = NOW() WHERE notified_at IS NULL")
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


# ---------------------------------------------------------------------------
# "Your playbook is ready" email, for scans that finish after the creator left the chat
# ---------------------------------------------------------------------------

EMAIL_KIND = "hooks_ready"
NOTIFY_AFTER = timedelta(minutes=2)    # quick scans are seen in chat; only slow ones get an email
NOTIFY_WITHIN = timedelta(days=2)      # don't email about a scan from last week
NOTIFY_PER_PASS = 2                    # finishing a scan writes the answer (an LLM call): keep cron passes short


def mark_viewed(conn, creator_id: int, run_id: int) -> None:
    ensure_table(conn)
    cur = conn.cursor()
    cur.execute("UPDATE polly_hook_scans SET viewed_at = NOW() WHERE creator_id = %s AND run_id = %s AND viewed_at IS NULL",
                (creator_id, run_id))
    conn.commit()


def playbook_url(run_id: int) -> str:
    from urllib.parse import urlencode

    from services.creator_directory_search import frontend_base

    q = urlencode({"hooks": run_id, "utm_source": "email", "utm_medium": "polly_alert", "utm_campaign": EMAIL_KIND})
    return f"{frontend_base().rstrip('/')}/creator/dashboard/for-you?{q}"


def build_ready_email(first_name: str, scan: Dict[str, Any], niche: str, band: str, run_id: int,
                      unsubscribe_url: str = "") -> Dict[str, str]:
    from html import escape

    from services.brand_insights import niche_label
    from services.polly_alerts import render_email

    stats = scan.get("stats") or {}
    label = escape(niche_label(niche))
    subject = "Your hooks playbook is ready"
    read = ""
    if stats.get("videos"):
        read = (f" I read <strong>{int(stats['videos']):,} posts</strong> from "
                f"<strong>{int(stats.get('creators') or 0):,} creators</strong> your size.")
    lines = [
        f"Your scan of <strong>{label}</strong> creators with {escape(band or 'your')} followers is done.{read}",
        "Inside: the opening lines working right now, the formats getting views, and the brands gifting creators "
        "your size, ready to pitch.",
    ]
    buttons = [{"label": "Open my playbook", "href": playbook_url(run_id)}]
    return {"subject": subject, "html": render_email(first_name, subject, lines, buttons, unsubscribe_url)}


def notify_ready(conn, *, poll_fn=None, send_fn=None, now: Optional[datetime] = None) -> Dict[str, int]:
    """Cron: finish hook scans the creator walked away from, and email them once each when ready."""
    from psycopg2.extras import RealDictCursor

    from services.polly_alerts import _default_send, _log_email, _recipient, _unsubscribe_url, ensure_alert_tables

    ensure_table(conn)
    now = now or _now()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    cur.execute("""SELECT * FROM polly_hook_scans
                   WHERE run_id IS NOT NULL AND viewed_at IS NULL AND notified_at IS NULL
                     AND created_at < %s AND created_at > %s
                   ORDER BY created_at LIMIT %s""",
                (now - NOTIFY_AFTER, now - NOTIFY_WITHIN, NOTIFY_PER_PASS))
    rows = [dict(r) for r in cur.fetchall()]
    if not rows:
        return {"checked": 0, "emailed": 0}
    if poll_fn is None:
        from routes.brand_scan import _redis
        from services import brand_insights as bi

        def poll_fn(c, run_id):
            return bi.poll(c, run_id, "creator_hooks", redis_client=_redis())
    ensure_alert_tables(conn)
    send = send_fn or _default_send
    emailed = 0
    for row in rows:
        scan = poll_fn(conn, row["run_id"]) or {}
        if scan.get("status") == "searching":
            continue  # still running: next pass
        mark = conn.cursor()
        mark.execute("UPDATE polly_hook_scans SET notified_at = NOW() WHERE id = %s", (row["id"],))
        conn.commit()
        if scan.get("status") != "ready" or not (scan.get("sections") or scan.get("hooks")):
            continue  # nothing worth an email; the chat explains it when they come back
        who = _recipient(conn, row["creator_id"])
        if not who:
            continue
        unsub = _unsubscribe_url(who["user_id"])
        mail = build_ready_email(who.get("first") or "", scan, row.get("niche") or "", row.get("band") or "",
                                 row["run_id"], unsub)
        result = send(who["email"], mail["subject"], mail["html"], unsub, EMAIL_KIND)
        _log_email(conn, row["creator_id"], EMAIL_KIND, None, row["run_id"], mail["subject"], result)
        conn.commit()
        emailed += int(bool(result.get("success")))
    return {"checked": len(rows), "emailed": emailed}
