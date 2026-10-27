"""Cancel flow with real numbers: what Pro did in the last 30 days, and the save offer that fits.

The cancel screen shows the creator's own counts (pitches, follow-ups, kit opens, replies) and
offers a fix when Polly didn't deliver: connect Gmail, a person reviews the outreach, turn
Autopilot back on, the $12 x 3 price hold, or a one-month pause. Never a credit or free days.

Pause (one month, no free days): it starts when the paid period ends, so nothing paid is lost.
Stripe voids the invoice that falls inside the pause; Pro and Autopilot are off for that month
and come back on the day the next invoice is charged.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from psycopg2.extras import Json, RealDictCursor

WINDOW = timedelta(days=30)
FAIR_TEST_PITCHES = 30
MIN_DAYS_TO_JUDGE = 3
SENT_DAYS_SHARE = 0.6

SITUATIONS = ("no_gmail", "missed_days", "early", "no_opens", "opens_no_replies", "replies")


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _aware(dt: Optional[datetime]) -> Optional[datetime]:
    if not dt:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _cursor(conn):
    return conn.cursor(cursor_factory=RealDictCursor)


def weekdays_between(start: date, end: date) -> int:
    """Weekdays in [start, end)."""
    n, d = 0, start
    while d < end:
        if d.weekday() < 5:
            n += 1
        d += timedelta(days=1)
    return n


def _sent_days(conn, creator_id: int, since: datetime) -> int:
    cur = _cursor(conn)
    try:
        cur.execute(
            "SELECT COUNT(DISTINCT (sent_at AT TIME ZONE 'UTC')::date) AS n FROM polly_autopilot_queue "
            "WHERE creator_id = %s AND sent_at >= %s",
            (creator_id, since),
        )
        return int((cur.fetchone() or {}).get("n") or 0)
    except Exception:
        conn.rollback()
        return 0


def load(conn, creator_id: int, now: Optional[datetime] = None) -> Dict[str, Any]:
    """The creator's last 30 days on Pro, from the send log and timeline."""
    from services import polly_autopilot as pa
    from services.pitch_facts import CONFIRMED_KEY
    from services.polly_memory import load_thread
    from services.pro_manager import next_brand_names, window_counts

    now = now or _now()
    cur = _cursor(conn)
    cur.execute("SELECT subscription_started_at FROM creators WHERE id = %s", (creator_id,))
    started = _aware((cur.fetchone() or {}).get("subscription_started_at"))
    since = max(now - WINDOW, started) if started else now - WINDOW
    try:
        pa.ensure_autopilot_tables(conn)
        connected_at = pa.gmail_connected_at(conn, creator_id)
        prefs = pa.settings(conn, creator_id)
        lined_up = sum(1 for i in pa.list_items(conn, creator_id) if i.get("status") in ("draft", "approved"))
    except Exception as err:
        print(f"[retention] autopilot facts skipped: {err}")
        conn.rollback()
        connected_at, prefs, lined_up = None, {"enabled": False}, 0
    counts = window_counts(conn, creator_id, since, now + timedelta(minutes=1))
    send_from = max(since, _aware(connected_at)) if connected_at else None
    expected = weekdays_between(send_from.date(), now.date()) if send_from else 0
    notes = load_thread(conn, creator_id).get("notes") or {}
    return {
        "gmail_connected": bool(connected_at),
        "pitches_sent_30d": counts["pitched"],
        "followups_30d": counts["followups"],
        "kit_opens_30d": counts["kit_views"],
        "replies_30d": counts["replies"],
        "weekdays_sent": _sent_days(conn, creator_id, since),
        "weekdays_expected": expected,
        "facts_confirmed": bool(notes.get(CONFIRMED_KEY)),
        "days_on_pro": (now - started).days if started else None,
        "autopilot_enabled": bool(prefs.get("enabled")),
        "lined_up": lined_up,
        "next_brands": next_brand_names(conn, creator_id, limit=3),
    }


def situation(ctx: Dict[str, Any]) -> str:
    if not ctx.get("gmail_connected"):
        return "no_gmail"
    expected, sent_days = int(ctx.get("weekdays_expected") or 0), int(ctx.get("weekdays_sent") or 0)
    if expected >= MIN_DAYS_TO_JUDGE and sent_days < expected * SENT_DAYS_SHARE:
        return "missed_days"
    if int(ctx.get("replies_30d") or 0):
        return "replies"
    if int(ctx.get("kit_opens_30d") or 0):
        return "opens_no_replies"
    if int(ctx.get("pitches_sent_30d") or 0) >= FAIR_TEST_PITCHES:
        return "no_opens"
    return "early"


def _plural(n: int, word: str, many: str = "") -> str:
    return f"{n} {word if n == 1 else (many or word + 's')}"


def _names(names: List[str]) -> str:
    names = [n for n in names if n]
    return names[0] if len(names) == 1 else ", ".join(names[:-1]) + " and " + names[-1] if names else ""


def plan(reason: str, ctx: Dict[str, Any], price_hold_ok: bool, pause_ok: bool) -> Dict[str, Any]:
    """What the cancel screen says after a reason, and which offers it shows (first = main button)."""
    where = situation(ctx)
    pitched, follows = int(ctx.get("pitches_sent_30d") or 0), int(ctx.get("followups_30d") or 0)
    opens, replies = int(ctx.get("kit_opens_30d") or 0), int(ctx.get("replies_30d") or 0)
    offers: List[str] = []
    if where == "no_gmail":
        headline = "Polly couldn't send your daily outreach: Gmail isn't connected."
        body = "Connect it now and your first pitches go out within the hour, then every weekday."
        offers.append("connect_gmail")
    elif where == "missed_days":
        headline = (f"Polly sent on only {ctx.get('weekdays_sent', 0)} of {ctx.get('weekdays_expected', 0)} "
                    "weekdays. That's on us.")
        body = ("A person on our team checks why this week, fixes your brand list and pitch facts, "
                "and gets Polly back to sending every weekday.")
        offers.append("outreach_review")
    elif reason == "too_expensive":
        headline = "Here's what Pro did in the last 30 days."
        body = "Keep it for less, or pause for a month and come back."
        if price_hold_ok:
            offers.append("price_hold")
    elif reason == "unused":
        nxt = ctx.get("next_brands") or []
        headline = "Polly can run on her own. You don't need to open the app."
        body = (f"This week she'd pitch {_names(nxt)} from your Gmail, and follow up on her own."
                if nxt else "She picks your brands, sends from your Gmail and follows up on her own.")
        if not ctx.get("autopilot_enabled"):
            offers.append("autopilot_on")
    else:
        if where == "early":
            headline = f"It's early: Polly has sent {_plural(pitched, 'pitch', 'pitches')} so far."
            body = "Brands usually reply 2 to 4 weeks after a pitch, and Polly keeps following up."
        elif where == "opens_no_replies":
            headline = f"{_plural(opens, 'brand')} opened your kit."
            body = "Brands are looking. A person reviews your kit and pitch this week to turn those looks into replies."
        elif where == "no_opens":
            headline = f"Polly sent {_plural(pitched, 'pitch', 'pitches')}. No kit opens yet."
            body = ("That usually means the brand mix or the pitch needs a change, not more of the same. "
                    "A person reviews both this week.")
        else:
            headline = f"{_plural(replies, 'brand')} replied in the last 30 days."
            body = "Open Polly and she'll write your answers."
        offers.append("outreach_review")
    if pause_ok:
        offers.append("pause")
    return {"situation": where, "headline": headline, "body": body, "offers": offers}


# ---------------------------------------------------------------------------
# Pause for a month
# ---------------------------------------------------------------------------

_PAUSE_READY = False


def ensure_pause_table(conn) -> None:
    global _PAUSE_READY
    if _PAUSE_READY:
        return
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS creator_pauses (
            id SERIAL PRIMARY KEY,
            creator_id INTEGER NOT NULL,
            subscription_id TEXT,
            starts_at TIMESTAMPTZ NOT NULL,
            resumes_at TIMESTAMPTZ NOT NULL,
            state TEXT NOT NULL DEFAULT 'scheduled',
            created_at TIMESTAMPTZ DEFAULT NOW()
        )
    """)
    cur.execute("CREATE INDEX IF NOT EXISTS idx_creator_pauses_state ON creator_pauses (state, starts_at)")
    conn.commit()
    _PAUSE_READY = True


def pause_window(period_end: datetime) -> Dict[str, datetime]:
    """Off from the end of the paid period until the same day next month (when billing restarts)."""
    from services.pro_manager import add_months

    resumes = add_months(period_end, 1)
    return {"starts_at": period_end, "resumes_at": resumes,
            "stripe_resumes_at": resumes - timedelta(hours=1)}  # the next cycle invoice is charged


def open_pause(conn, creator_id: int) -> Optional[Dict[str, Any]]:
    ensure_pause_table(conn)
    cur = _cursor(conn)
    cur.execute("SELECT * FROM creator_pauses WHERE creator_id = %s AND state IN ('scheduled', 'active') "
                "ORDER BY id DESC LIMIT 1", (creator_id,))
    row = dict(cur.fetchone() or {})
    return row if row.get("state") in ("scheduled", "active") and row.get("resumes_at") else None


def schedule_pause(conn, stripe_client, creator: Dict[str, Any], period_end: datetime) -> Dict[str, Any]:
    window = pause_window(period_end)
    stripe_client.Subscription.modify(
        creator["stripe_subscription_id"],
        cancel_at_period_end=False,
        pause_collection={"behavior": "void", "resumes_at": int(window["stripe_resumes_at"].timestamp())},
    )
    ensure_pause_table(conn)
    cur = conn.cursor()
    cur.execute("UPDATE creator_pauses SET state = 'replaced' WHERE creator_id = %s AND state = 'scheduled'",
                (creator["id"],))
    cur.execute("INSERT INTO creator_pauses (creator_id, subscription_id, starts_at, resumes_at) VALUES (%s, %s, %s, %s)",
                (creator["id"], creator["stripe_subscription_id"], window["starts_at"], window["resumes_at"]))
    cur.execute("UPDATE creators SET subscription_ends_at = NULL WHERE id = %s", (creator["id"],))
    conn.commit()
    return window


def _set_access(conn, creator_id: int, pro: bool) -> None:
    cur = conn.cursor()
    if pro:
        cur.execute("UPDATE creators SET subscription_tier = 'pro', unlocks_tier = 'pro', subscription_status = 'active' "
                    "WHERE id = %s", (creator_id,))
    else:
        cur.execute("UPDATE creators SET subscription_tier = 'free', unlocks_tier = 'free', subscription_status = 'paused' "
                    "WHERE id = %s", (creator_id,))
    conn.commit()
    try:
        cur.execute("UPDATE polly_autopilot SET enabled = %s WHERE creator_id = %s", (pro, creator_id))
        conn.commit()
    except Exception:
        conn.rollback()


def run_pauses(conn, now: Optional[datetime] = None) -> Dict[str, int]:
    """Cron: switch Pro off when a pause starts and back on when billing restarts."""
    now = now or _now()
    ensure_pause_table(conn)
    cur = _cursor(conn)
    out = {"started": 0, "resumed": 0}
    cur.execute("SELECT id, creator_id FROM creator_pauses WHERE state = 'scheduled' AND starts_at <= %s", (now,))
    for row in cur.fetchall():
        _set_access(conn, row["creator_id"], pro=False)
        conn.cursor().execute("UPDATE creator_pauses SET state = 'active' WHERE id = %s", (row["id"],))
        conn.commit()
        out["started"] += 1
    cur = _cursor(conn)
    cur.execute("SELECT id, creator_id FROM creator_pauses WHERE state = 'active' AND resumes_at <= %s", (now,))
    for row in cur.fetchall():
        _set_access(conn, row["creator_id"], pro=True)
        conn.cursor().execute("UPDATE creator_pauses SET state = 'resumed' WHERE id = %s", (row["id"],))
        conn.commit()
        out["resumed"] += 1
    return out


def resume_now(conn, stripe_client, creator_id: int) -> Optional[Dict[str, Any]]:
    """Undo a pause. During the pause month it restarts billing today (a fresh paid month)."""
    pause = open_pause(conn, creator_id)
    if not pause:
        return None
    modify = {"pause_collection": ""}
    if pause["state"] == "active":
        modify.update(billing_cycle_anchor="now", proration_behavior="none")
    stripe_client.Subscription.modify(pause["subscription_id"], **modify)
    if pause["state"] == "active":
        _set_access(conn, creator_id, pro=True)
    conn.cursor().execute("UPDATE creator_pauses SET state = 'cancelled' WHERE id = %s", (pause["id"],))
    conn.commit()
    return pause


# ---------------------------------------------------------------------------
# Admin: real churn vs never activated
# ---------------------------------------------------------------------------

def churn_report(conn, days: int = 90) -> Dict[str, Any]:
    cur = _cursor(conn)
    cur.execute(
        """
        SELECT reason, outcome, offer, context
        FROM creator_retention_events
        WHERE created_at >= NOW() - (%s || ' days')::interval AND outcome IN ('canceled', 'accepted')
        """,
        (int(days),),
    )
    rows = [dict(r) for r in cur.fetchall()]
    buckets = {"never_sent": 0, "sent_no_replies": 0, "had_replies": 0, "unknown": 0}
    saves: Dict[str, int] = {}
    reasons: Dict[str, Dict[str, int]] = {}
    for r in rows:
        if r["outcome"] == "accepted":
            saves[r["offer"] or "unknown"] = saves.get(r["offer"] or "unknown", 0) + 1
            continue
        ctx = r.get("context") or {}
        if not ctx:
            key = "unknown"
        elif not ctx.get("gmail_connected") or not ctx.get("pitches_sent_30d"):
            key = "never_sent"
        elif ctx.get("replies_30d"):
            key = "had_replies"
        else:
            key = "sent_no_replies"
        buckets[key] += 1
        by_reason = reasons.setdefault(r.get("reason") or "other", {})
        by_reason[key] = by_reason.get(key, 0) + 1
    return {"days": days, "cancels": buckets, "by_reason": reasons, "saves": saves}
